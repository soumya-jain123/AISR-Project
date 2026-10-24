"""Operator terminal (the 'shell' of the project).

  python teleop.py --transport socket
  python teleop.py --transport mqtt
  python teleop.py --transport socket --delay-ms 300 --loss 0.1   # bad network
  python teleop.py --script scripts/demo.txt
  python teleop.py -c "forward 0.3; wait 2; stop"
"""
import argparse
import sys
import time

from p11.client import build_client
from p11.protocol import HELP, parse_command


def show(cmd, reply):
    if reply is None:
        print("[TIMEOUT] no reply from robot (network down? bridge running?)")
        return
    t = reply.get("telemetry") or {}
    tag = "ok " if reply["ok"] else "ERR"
    if cmd == "sensors" and t:
        print(f"[{tag}] sensors L->R (m): {t.get('sensors')}  front_min={t.get('front_min')}")
    elif cmd == "status" and t:
        print(f"[{tag}] x={t['x']} y={t['y']} theta={t['theta_deg']}deg  mode={t['mode']}  "
              f"v={t['v']} w={t['w']}")
        print(f"       safety={'ON' if t['safety'] else 'OFF'} estop={t['estop']} "
              f"front_min={t['front_min']}  collisions={t['collisions']} "
              f"obstacle_stops={t['obstacle_stops']} watchdog_trips={t['watchdog_trips']}")
        print(f"       rtt_ms={t['rtt_ms']} vmax_eff={t['vmax_eff']} note='{t['note']}'")
    else:
        extra = f"  (pose {t['x']}, {t['y']}, {t['theta_deg']}deg)" if t else ""
        print(f"[{tag}] {reply['msg']}{extra}")


def execute(client, line, depth=0):
    """Run one input line. Returns False when the user wants to quit."""
    for part in line.split(";"):
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        word = part.split()[0].lower()
        if word in ("quit", "exit"):
            return False
        if word == "help":
            print(HELP)
            continue
        if word == "wait":
            try:
                time.sleep(float(part.split()[1]))
            except (IndexError, ValueError):
                print("usage: wait <seconds>")
            continue
        if word == "script":
            try:
                path = part.split(None, 1)[1]
                lines = open(path).read().splitlines()
            except (IndexError, OSError) as e:
                print(f"cannot read script: {e}")
                continue
            if depth > 3:
                print("scripts nested too deeply")
                continue
            for ln in lines:
                print(f"> {ln}")
                if not execute(client, ln, depth + 1):
                    return False
            continue
        try:
            cmd, args = parse_command(part)       # client-side validation
        except ValueError as e:
            print(f"[INVALID] {e}")
            continue
        show(cmd, client.send_command(cmd, args))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transport", choices=["socket", "mqtt"], default="socket")
    ap.add_argument("--host", default="127.0.0.1", help="bridge address (socket)")
    ap.add_argument("--port", type=int, default=9009)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--qos", type=int, default=0, choices=[0, 1])
    ap.add_argument("--delay-ms", type=float, default=0.0, help="extra round-trip delay")
    ap.add_argument("--loss", type=float, default=0.0, help="message loss probability 0..1")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--log", default="teleop_log.csv", help="client command log (CSV)")
    ap.add_argument("--script", help="text file with commands")
    ap.add_argument("-c", dest="cmdline", help="commands separated by ';'")
    a = ap.parse_args()

    client = build_client(a.transport, a.host, a.port, a.mqtt_host, a.mqtt_port, a.qos,
                          a.delay_ms, a.loss, a.seed, a.timeout, a.retries, a.log)
    try:
        client.connect()
    except (OSError, ConnectionError) as e:
        print(f"Cannot connect: {e}\nIs run_bridge.py running"
              f"{' and Mosquitto started' if a.transport == 'mqtt' else ''}?")
        sys.exit(1)
    print(f"Connected via {a.transport.upper()}  (delay {a.delay_ms} ms, loss {a.loss}).  "
          f"Type 'help'.")
    try:
        if a.script:
            execute(client, f"script {a.script}")
        elif a.cmdline:
            execute(client, a.cmdline)
        else:
            while True:
                try:
                    line = input("teleop> ")
                except EOFError:
                    break
                if not execute(client, line):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        client.send_command("stop")
        s = client.summary()
        print(f"Session: {s['n_cmds']} commands, success {s['cmd_success']*100:.0f}%, "
              f"RTT mean {s['rtt_mean']:.1f} ms, p95 {s['rtt_p95']:.1f} ms")
        client.close()


if __name__ == "__main__":
    main()
