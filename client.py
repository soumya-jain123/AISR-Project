"""Operator-side interactive shell.

Run:  python client.py --host 127.0.0.1 --port 9000

Shell commands:
  fwd [v]   back [v]   left [w]   right [w]   stop   estop   reset   status   ping
  move <v> <w>         raw command
  rtt                  measure round-trip time (5 pings)
  quit
A background thread sends a heartbeat (HB) every --hb-interval seconds.
"""
import argparse
import cmd
import socket
import threading
import time


class Link:
    """Thread-safe request/response link to the robot server."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.f = self.sock.makefile("rw", newline="\n")
        self.lock = threading.Lock()

    def send(self, line):
        with self.lock:
            self.f.write(line + "\n")
            self.f.flush()
            return self.f.readline().strip()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Shell(cmd.Cmd):
    intro = "Robot teleop shell. Type help or ? for commands."
    prompt = "robot> "

    def __init__(self, link, hb_interval):
        super().__init__()
        self.link = link
        self.alive = True
        threading.Thread(target=self._heartbeat, args=(hb_interval,), daemon=True).start()

    def _heartbeat(self, interval):
        while self.alive:
            try:
                self.link.send("HB")
            except Exception:
                print("\n[!] connection lost")
                self.alive = False
                return
            time.sleep(interval)

    def _tx(self, line):
        t = time.perf_counter()
        r = self.link.send(line)
        print(f"{r}   ({(time.perf_counter() - t) * 1000:.1f} ms)")

    # movement
    def do_fwd(self, a):   self._tx(f"MOVE {float(a or 0.5)} 0")
    def do_back(self, a):  self._tx(f"MOVE {-float(a or 0.5)} 0")
    def do_left(self, a):  self._tx(f"MOVE 0 {float(a or 1.0)}")
    def do_right(self, a): self._tx(f"MOVE 0 {-float(a or 1.0)}")
    def do_move(self, a):  self._tx(f"MOVE {a}")
    def do_stop(self, a):  self._tx("STOP")
    def do_estop(self, a): self._tx("ESTOP")
    def do_reset(self, a): self._tx("RESET")
    def do_status(self, a): self._tx("STATUS")
    def do_ping(self, a):  self._tx("PING")

    def do_rtt(self, a):
        ts = []
        for _ in range(5):
            t = time.perf_counter()
            self.link.send("PING")
            ts.append((time.perf_counter() - t) * 1000)
        print(f"RTT min/avg/max = {min(ts):.1f}/{sum(ts)/len(ts):.1f}/{max(ts):.1f} ms")

    def do_quit(self, a):
        self.alive = False
        self.link.close()
        return True
    do_EOF = do_quit

    def emptyline(self):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--hb-interval", type=float, default=0.5)
    a = ap.parse_args()
    Shell(Link(a.host, a.port), a.hb_interval).cmdloop()
