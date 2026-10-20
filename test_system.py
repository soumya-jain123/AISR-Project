"""Automated end-to-end test: starts the server, drives it, checks e-stop + watchdog.
Run:  python test_system.py
"""
import subprocess
import sys
import time

from client import Link

PORT = 9123


def main():
    srv = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--hb-timeout", "1.0"])
    time.sleep(1)
    try:
        a = Link("127.0.0.1", PORT)
        assert a.send("PING") == "PONG"
        assert a.send("MOVE 0.5 0") == "OK"
        a.send("HB")
        time.sleep(0.5)
        s = a.send("STATUS")
        print(s)
        assert "mode=RUNNING" in s and "x=0.0" not in s, "robot should be moving"

        # e-stop latches
        assert a.send("ESTOP") == "ESTOP"
        assert a.send("MOVE 0.5 0").startswith("ERR"), "move must be rejected after estop"
        assert a.send("RESET") == "OK"

        # watchdog: move, then stop sending heartbeats
        a.send("MOVE 0.5 0")
        time.sleep(1.6)
        s = a.send("STATUS")
        print(s)
        assert "mode=WATCHDOG" in s and "v=0.00" in s, "watchdog should have stopped robot"
        a.close()
        print("ALL TESTS PASSED")
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
