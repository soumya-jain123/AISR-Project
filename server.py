"""Robot-side TCP server.

Text protocol, one command per line (newline terminated):
  PING                -> PONG
  HB                  -> OK        (heartbeat; resets the watchdog)
  MOVE <v> <w>        -> OK        (linear m/s, angular rad/s)
  STOP                -> OK        (normal stop, can move again)
  ESTOP               -> ESTOP     (latched emergency stop, needs RESET)
  RESET               -> OK        (clear e-stop, reset robot pose)
  STATUS              -> STATUS x=.. y=.. theta=.. v=.. w=.. mode=..

Safety:
  * Heartbeat watchdog: if no HB/command arrives within --hb-timeout seconds
    the robot is stopped automatically (fail-safe) and mode becomes WATCHDOG.
  * E-stop is latched: MOVE is rejected until RESET.

Run:  python server.py                      (built-in simulator)
      python server.py --backend coppelia   (CoppeliaSim)
"""
import argparse
import socket
import threading
import time

DT = 0.02  # physics / control tick: 50 Hz


class RobotServer:
    def __init__(self, robot, host, port, hb_timeout):
        self.robot = robot
        self.host, self.port = host, port
        self.hb_timeout = hb_timeout
        self.mode = "IDLE"            # IDLE | RUNNING | ESTOP | WATCHDOG
        self.last_hb = time.monotonic()
        self.lock = threading.Lock()
        self.running = True

    # ---------- safety / control loop ----------
    def control_loop(self):
        while self.running:
            now = time.monotonic()
            with self.lock:
                if self.mode == "RUNNING" and now - self.last_hb > self.hb_timeout:
                    self.robot.set_velocity(0, 0)
                    self.mode = "WATCHDOG"
                    print(f"[SAFETY] heartbeat lost > {self.hb_timeout}s -> robot stopped")
            self.robot.step(DT)
            time.sleep(DT)

    # ---------- command handling ----------
    def handle(self, line):
        parts = line.strip().split()
        if not parts:
            return None
        cmd, args = parts[0].upper(), parts[1:]
        with self.lock:
            if cmd == "PING":
                return "PONG"
            if cmd == "HB":
                self.last_hb = time.monotonic()
                if self.mode == "WATCHDOG":
                    self.mode = "IDLE"   # link recovered, robot stays stopped
                return "OK"
            if cmd == "ESTOP":
                self.robot.set_velocity(0, 0)
                self.mode = "ESTOP"
                print("[SAFETY] EMERGENCY STOP")
                return "ESTOP"
            if cmd == "RESET":
                self.robot.reset()
                self.mode = "IDLE"
                self.last_hb = time.monotonic()
                return "OK"
            if cmd == "STOP":
                self.robot.set_velocity(0, 0)
                if self.mode == "RUNNING":
                    self.mode = "IDLE"
                return "OK"
            if cmd == "MOVE":
                if self.mode == "ESTOP":
                    return "ERR estop_latched (send RESET)"
                try:
                    v, w = float(args[0]), float(args[1])
                except (IndexError, ValueError):
                    return "ERR usage: MOVE <v> <w>"
                self.robot.set_velocity(v, w)
                self.mode = "RUNNING"
                self.last_hb = time.monotonic()
                return "OK"
            if cmd == "STATUS":
                s = self.robot.get_state()
                return ("STATUS x={x:.3f} y={y:.3f} theta={theta:.3f} "
                        "v={v:.2f} w={w:.2f} ").format(**s) + f"mode={self.mode}"
            return "ERR unknown_command"

    def client_thread(self, conn, addr):
        print(f"[NET] client connected {addr}")
        f = conn.makefile("rw", newline="\n")
        try:
            for line in f:
                reply = self.handle(line)
                if reply is not None:
                    f.write(reply + "\n")
                    f.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            with self.lock:   # client gone -> fail safe
                if self.mode == "RUNNING":
                    self.robot.set_velocity(0, 0)
                    self.mode = "WATCHDOG"
            print(f"[NET] client disconnected {addr}")
            conn.close()

    def serve(self):
        threading.Thread(target=self.control_loop, daemon=True).start()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(5)
        print(f"[NET] listening on {self.host}:{self.port}  (hb timeout {self.hb_timeout}s)")
        try:
            while True:
                conn, addr = srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(target=self.client_thread, args=(conn, addr),
                                 daemon=True).start()
        except KeyboardInterrupt:
            print("\nshutting down")
        finally:
            self.running = False
            srv.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--hb-timeout", type=float, default=2.0)
    ap.add_argument("--backend", choices=["sim", "coppelia"], default="sim")
    a = ap.parse_args()
    if a.backend == "coppelia":
        from coppelia_backend import CoppeliaRobot
        robot = CoppeliaRobot()
    else:
        from robot_sim import RobotSim
        robot = RobotSim()
    RobotServer(robot, a.host, a.port, a.hb_timeout).serve()
