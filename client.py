"""Operator Client: type commands, they go to the Robot Server over TCP.

Run:  python client.py [--host 127.0.0.1] [--port 5555] [--delay 0]
--delay (ms) holds every outgoing and every incoming message that long (one-way delay).
"""
import argparse
import collections
import json
import readline  # noqa: F401  (arrow keys + command history in input())
import math
import shutil
import socket
import sys
import threading
import time

HEARTBEAT_DT = 0.5

# Commands handled here in the client. The robot's own commands come from the server, because they
# depend on which robot is being controlled.
LOCAL_HELP = [
    "pose             last position received here, and how late it arrived",
    "delay 300        add 300 ms of artificial network delay (0 to turn off)",
    "clear            clear the window",
    "help, quit",
]


class DelayLine:
    """Delivers items to fn() `delay` seconds after put(), in order."""

    def __init__(self, fn):
        self.fn, self.delay = fn, 0.0
        self.q = collections.deque()
        self.cv = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def put(self, item):
        with self.cv:
            self.q.append((time.time() + self.delay, item))
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                due, item = self.q[0]
            if (wait := due - time.time()) > 0:
                time.sleep(wait)
            with self.cv:
                self.q.popleft()
            self.fn(item)


class Link:
    """TCP connection to the server with heartbeat and artificial delay.
    on_event(msg) is called for server events; latest feedback is kept in self.fb."""

    def __init__(self, host, port, delay_ms=0, on_event=print):
        self.sock = socket.create_connection((host, port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.on_event = on_event
        self.fb, self.fb_rx = None, 0.0   # latest feedback and when it was used here
        self.latencies = collections.deque(maxlen=50)   # one-way latency of feedback (s)
        self.alive = True
        self.out = DelayLine(self._send_now)
        self.inn = DelayLine(self._use)
        self.set_delay(delay_ms)
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def set_delay(self, ms):
        self.out.delay = self.inn.delay = ms / 1000

    def send(self, msg):
        msg["ts"] = time.time()   # stamped when the operator issued it
        self.out.put(msg)

    def cmd(self, text):
        self.send({"type": "cmd", "cmd": text})

    def _send_now(self, msg):
        try:
            self.sock.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            self.alive = False

    def _heartbeat(self):
        while self.alive:
            self.send({"type": "hb"})
            time.sleep(HEARTBEAT_DT)

    def _reader(self):
        try:
            for line in self.sock.makefile("r"):
                self.inn.put(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
        self.alive = False
        self.on_event({"type": "msg", "text": "Lost connection to the robot server. The robot stops itself "
                       "automatically. Start client.py again once the server is running.", "code": "disconnected",
                       "ts": time.time()})

    def _use(self, msg):
        now = time.time()
        self.latencies.append(now - msg["ts"])
        if msg["type"] == "fb":
            self.fb, self.fb_rx = msg, now
        else:
            self.on_event(msg)

    def close(self):
        self.alive = False
        self.sock.close()


BOLD, DIM, INVERT, RESET = "\033[1m", "\033[2m", "\033[7m", "\033[0m"
HEADER_ROWS = 3


def draw_header(title, info):
    """Pinned header: rows 1-3 stay put, everything below scrolls (ANSI scroll region)."""
    cols, rows = shutil.get_terminal_size()
    sys.stdout.write("\0337"                                    # save cursor
                     f"\033[1;1H{INVERT}{BOLD}{title.center(cols)}{RESET}"
                     f"\033[2;1H\033[2K {info}"
                     f"\033[3;1H{DIM}{'─' * cols}{RESET}"
                     f"\033[{HEADER_ROWS + 1};{rows}r"               # scroll region below the header
                     "\0338")                                   # restore cursor
    sys.stdout.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("--delay", type=float, default=0, help="one-way delay in ms")
    a = p.parse_args()

    ui = {"robot": "robot", "help": [], "delay": a.delay}

    def print_help():
        print(f"{BOLD}Commands for the {ui['robot']}{RESET}")
        for line in ui["help"] + LOCAL_HELP:
            print("  " + line)

    def show(msg):
        if msg["type"] == "help":   # robot connected or switched: new header and command list
            ui["robot"], ui["help"] = msg["robot"], msg["lines"]
            header(ui["delay"])
            print("\r\033[2K", end="")
            print_help()
            print(">> ", end="", flush=True)
            return
        late = (time.time() - msg["ts"]) * 1000
        note = f"  {DIM}[arrived {late:.0f} ms late]{RESET}" if late >= 50 else ""
        print(f"\r\033[2K{BOLD}<<{RESET} {msg['text']}{note}\n>> ", end="", flush=True)

    def header(delay_ms):
        d = f"network delay {delay_ms:.0f} ms" if delay_ms else "no added delay"
        draw_header("Robot Simulation Control Window",
                    f"{ui['robot']}  ·  server {a.host}:{a.port}  ·  {d}  ·  'help' for commands, 'quit' to exit")

    # Own screen like a full-screen app: the shell's scrollback comes back untouched on exit.
    # ponytail: header isn't redrawn on terminal resize; type 'clear' to redraw it.
    sys.stdout.write(f"\033[?1049h\033[2J\033[{HEADER_ROWS + 1};1H")
    header(a.delay)
    try:
        try:
            link = Link(a.host, a.port, a.delay, show)
        except OSError:
            print(f"Can't reach the robot server at {a.host}:{a.port}. Is server.py running?\n"
                  "Press Enter to close.")
            input()
            return
        while link.alive:
            try:
                line = input(">> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            word = line.split()[0].lower()
            if word in ("quit", "exit"):
                break
            if word == "help":
                print_help()
            elif word == "clear":
                sys.stdout.write(f"\033[r\033[2J\033[{HEADER_ROWS + 1};1H")
                header(ui["delay"])
            elif word == "pose":
                fb = link.fb
                if fb:
                    lat = sum(link.latencies) / len(link.latencies) * 1000
                    print(f"Robot at ({fb['x']:.2f}, {fb['y']:.2f}), facing {math.degrees(fb['yaw']):.0f}°, "
                          f"speed {fb['speed']:.2f} m/s. This information is {lat:.0f} ms old on average.")
                else:
                    print("No position received yet.")
            elif word == "delay":
                try:
                    ms = float(line.split()[1])
                    link.set_delay(ms)
                    ui["delay"] = ms
                    header(ms)
                    print(f"Network delay set to {ms:.0f} ms each way." if ms else "Network delay turned off.")
                except (IndexError, ValueError):
                    print("Usage: delay 300")
            else:
                link.cmd(line)
        if not link.alive:
            input("Press Enter to close.")
        link.close()
    finally:
        sys.stdout.write("\033[r\033[?1049l")   # reset scroll region, back to the normal screen
        sys.stdout.flush()


if __name__ == "__main__":
    main()
