"""Automated experiments: a scripted 'human operator' drives the robot from the
start to a goal through the obstacle field while the network is degraded.
Compares transports (socket vs MQTT), delay, loss and the safety layer.

  python experiments/run_experiments.py --robot fake --grid quick
  python experiments/run_experiments.py --robot coppelia --grid full --repeats 3

Results: results/results.csv and results/traj/*.csv  (then run plot_results.py)
"""
import argparse
import csv
import math
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p11.bridge import Bridge                                    # noqa: E402
from p11.client import build_client                              # noqa: E402
from p11.robots import (CoppeliaRobot, FakeRobot, LEFT_IDX,      # noqa: E402
                        RIGHT_IDX, FRONT_IDX, wrap)
from p11.transports import MqttServer, SocketServer              # noqa: E402

GOAL = (4.0, 0.0)
GOAL_TOL = 0.30
MAX_TIME = 60.0
V_CRUISE = 0.5
AVOID_DIST = 0.45        # operator starts turning away at this front distance
LOOP_HZ = 5
PASS_TIME = 1.6          # s of straight driving after an avoidance turn


def operator_mission(client, bridge, goal=GOAL, max_time=MAX_TIME):
    """Simple rule-based operator with three states.
    GOAL  : steer towards the goal.
    AVOID : an obstacle is close ahead -> turn on the spot, always the same way.
    PASS  : obstacle no longer ahead -> drive straight for a moment to clear it.
    Decisions use the telemetry the client has received (so they are delayed by
    the network); every drive command is sent blocking with retries, so a bad
    network also slows the operator down, as it would a real person."""
    t0 = time.time()
    state, direction, pass_until = "GOAL", 1.0, 0.0
    reached = False
    while time.time() - t0 < max_time:
        tel = client.latest
        if tel is not None:
            x, y, th = tel["x"], tel["y"], math.radians(tel["theta_deg"])
            if math.hypot(goal[0] - x, goal[1] - y) < GOAL_TOL:
                reached = True
                break
            s = tel["sensors"]
            front = min(s[i] for i in FRONT_IDX)
            now = time.time()
            if state == "GOAL" and front < AVOID_DIST:
                left = sum(s[i] for i in LEFT_IDX) / 3
                right = sum(s[i] for i in RIGHT_IDX) / 3
                direction = 1.0 if left >= right else -1.0
                state = "AVOID"
            elif state == "AVOID" and front >= AVOID_DIST + 0.3:
                state, pass_until = "PASS", now + PASS_TIME
            elif state == "PASS" and now >= pass_until:
                state = "GOAL"
            if state == "AVOID":
                v, w = 0.05, direction * 1.0
            elif state == "PASS":
                v, w = V_CRUISE * 0.7, 0.0
            else:
                err = wrap(math.atan2(goal[1] - y, goal[0] - x) - th)
                w = max(-1.0, min(1.0, 1.5 * err))
                v = V_CRUISE if abs(err) < 0.5 else 0.15
            client.send_command("drive", [v, w])
        time.sleep(1.0 / LOOP_HZ)
    client.send_command("stop")
    return reached, time.time() - t0


def one_run(cfg, args, run_no):
    factory = (lambda: FakeRobot()) if args.robot == "fake" else \
              (lambda: CoppeliaRobot(args.coppelia_host))
    bridge = Bridge(factory, safety=cfg["safety"], adaptive=cfg["adaptive"])
    bridge.start()
    if cfg["transport"] == "socket":
        server = SocketServer(bridge, "127.0.0.1", 0)
        server.start()
        client = build_client("socket", port=server.port, delay_ms=cfg["delay"],
                              loss=cfg["loss"], seed=run_no)
    else:
        server = MqttServer(bridge, args.mqtt_host, args.mqtt_port)
        server.start()
        client = build_client("mqtt", mqtt_host=args.mqtt_host, mqtt_port=args.mqtt_port,
                              delay_ms=cfg["delay"], loss=cfg["loss"], seed=run_no)
    traj, stop_flag = [], threading.Event()

    def sampler():
        t_start = time.time()
        while not stop_flag.is_set():
            s = bridge.snapshot()
            if s:
                traj.append((time.time() - t_start, s["x"], s["y"]))
            time.sleep(0.1)

    try:
        client.connect()
        time.sleep(0.5)                       # let heartbeats / first telemetry arrive
        threading.Thread(target=sampler, daemon=True).start()
        reached, t_mission = operator_mission(client, bridge)
        time.sleep(0.3)
    finally:
        stop_flag.set()
        snap = bridge.snapshot()
        stats = client.summary()
        client.close()
        server.stop()
        bridge.stop()

    path = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(traj, traj[1:]))
    straight = math.hypot(GOAL[0], GOAL[1])
    xs = [p[1] for p in traj]
    ys = [p[2] for p in traj]
    row = {"run": run_no, **cfg, "success": int(reached),
           "time_s": round(t_mission, 2), "collisions": snap.get("collisions", 0),
           "obstacle_stops": snap.get("obstacle_stops", 0),
           "watchdog_trips": snap.get("watchdog_trips", 0),
           "path_len_m": round(path, 2), "path_ratio": round(path / straight, 2),
           "mean_abs_y_m": round(statistics.mean(abs(y) for y in ys), 3) if ys else "",
           "cmd_success": round(stats["cmd_success"], 3),
           "mean_attempts": round(stats["mean_attempts"], 2),
           "rtt_mean_ms": round(stats["rtt_mean"], 1), "rtt_p95_ms": round(stats["rtt_p95"], 1),
           "rtt_max_ms": round(stats["rtt_max"], 1)}
    return row, traj


def build_grid(name, transports, repeats):
    if name == "quick":
        delays, losses = [0, 500], [0.0]
    else:
        delays, losses = [0, 100, 300, 500, 1000], [0.0]
    conds = [(d, l) for d in delays for l in losses]
    if name == "full":
        conds += [(0, 0.1), (0, 0.2)]
    grid = []
    for t in transports:
        for d, l in conds:
            for safety in (True, False):
                for r in range(repeats):
                    grid.append({"transport": t, "delay": d, "loss": l,
                                 "safety": safety, "adaptive": True, "rep": r})
    return grid


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--robot", choices=["fake", "coppelia"], default="fake")
    ap.add_argument("--grid", choices=["quick", "full"], default="quick")
    ap.add_argument("--transports", nargs="+", default=["socket", "mqtt"])
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", default="results")
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--coppelia-host", default="localhost")
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, "traj"), exist_ok=True)
    grid = build_grid(args.grid, args.transports, args.repeats)
    print(f"{len(grid)} runs (about {len(grid) * 25 // 60} min), robot = {args.robot}")
    csv_path = os.path.join(args.out, "results.csv")
    writer = f = None
    for i, cfg in enumerate(grid, 1):
        label = (f"{cfg['transport']}_d{cfg['delay']}_l{int(cfg['loss']*100)}_"
                 f"{'safe' if cfg['safety'] else 'nosafe'}_r{cfg['rep']}")
        try:
            row, traj = one_run(cfg, args, i)
        except (ConnectionError, OSError) as e:
            print(f"[{i}/{len(grid)}] {label}: SKIPPED ({e})")
            continue
        row["label"] = label
        if writer is None:
            f = open(csv_path, "w", newline="")
            writer = csv.DictWriter(f, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        with open(os.path.join(args.out, "traj", label + ".csv"), "w", newline="") as tf:
            w = csv.writer(tf)
            w.writerow(["t", "x", "y"])
            w.writerows(traj)
        print(f"[{i}/{len(grid)}] {label}: success={row['success']} t={row['time_s']}s "
              f"collisions={row['collisions']} rtt={row['rtt_mean_ms']}ms")
    if f:
        f.close()
        print(f"Saved {csv_path}")


if __name__ == "__main__":
    main()
