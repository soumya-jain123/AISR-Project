"""Network delay study. Start CoppeliaSim and server.py first, then:  python study.py

For each delay, the client closes the loop itself (it steers from delayed feedback and sends
`drive v w` over TCP), so delay hits both the commands and the feedback, like a real operator.
Writes results/results.csv, results/error_vs_delay.png, results/paths.png.
"""
import csv
import math
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from client import Link
from server import goto_control

DELAYS_MS = [0, 100, 300, 500, 1000]
TASK_TIMEOUT = 60   # s
OUT = "results"


def wait_fb(link, cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if link.fb and cond(link.fb):
            return link.fb
        time.sleep(0.02)
    return None


def fresh_start(link, delay):
    run = link.fb["run"]
    link.cmd("restart")
    f = wait_fb(link, lambda f: f["run"] > run, 10 + 2 * delay)   # ignore feedback from before the restart
    time.sleep(0.5)   # let the robot settle on the ground
    return link.fb


def follow(link, waypoints):
    """Drive through waypoints using only (delayed) feedback. Returns (path, w_history, seconds, ok)."""
    path, ws, t0 = [], [], time.time()
    i = 0
    while i < len(waypoints):
        if time.time() - t0 > TASK_TIMEOUT:
            link.cmd("stop")
            return path, ws, time.time() - t0, False
        f = link.fb
        path.append((f["x"], f["y"]))
        v, w, done = goto_control(f["x"], f["y"], f["yaw"], *waypoints[i])
        if done:
            i += 1
            continue
        link.cmd(f"drive {v:.3f} {w:.3f}")
        ws.append(w)
        time.sleep(0.1)
    link.cmd("stop")
    return path, ws, time.time() - t0, True


def settle(link, delay, path):
    """After stop, let the robot come to rest and keep recording its path."""
    end = time.time() + 1.5 + 2 * delay
    while time.time() < end:
        path.append((link.fb["x"], link.fb["y"]))
        time.sleep(0.1)
    return link.fb


def metrics(path, ws, start, target, final):
    ux, uy = target[0] - start[0], target[1] - start[1]
    n = math.hypot(ux, uy)
    ux, uy = ux / n, uy / n
    overshoot = max(0.0, max((x - target[0]) * ux + (y - target[1]) * uy for x, y in path))
    flips = sum(1 for a, b in zip(ws, ws[1:]) if a * b < 0 and abs(a) > 0.05 and abs(b) > 0.05)
    err = math.hypot(final["x"] - target[0], final["y"] - target[1])
    return err, overshoot, flips


def estop_test(link, delay):
    fresh_start(link, delay)
    link.cmd("forward 0.5")
    time.sleep(3)
    t0 = time.time()
    link.cmd("estop")
    f = wait_fb(link, lambda f: f["estop_stopped_ts"] and f["estop_ts"] and f["estop_ts"] > t0, 15)
    seen = time.time()
    link.cmd("reset")
    if not f:
        return float("nan"), float("nan")
    return f["estop_stopped_ts"] - t0, seen - t0   # actual stop, stop as seen by operator


def main():
    os.makedirs(OUT, exist_ok=True)
    comm_lost = []
    link = Link("127.0.0.1", 5555, 0,
                on_event=lambda m: comm_lost.append(1) if m.get("code") == "comm_lost" else None)
    wait_fb(link, lambda f: True)

    rows, paths = [], {}
    for ms in DELAYS_MS:
        d = ms / 1000
        link.set_delay(ms)
        comm_lost.clear()
        print(f"--- delay {ms} ms ---")

        s = fresh_start(link, d)
        start = (s["x"], s["y"])
        target = (start[0] + 2.0, start[1] + 1.0)
        path, ws, secs, ok = follow(link, [target])
        final = settle(link, d, path)
        err, over, osc = metrics(path, ws, start, target, final)
        paths[("goto", ms)] = path
        rows.append(dict(task="goto", delay_ms=ms, ok=ok, final_error_m=err, overshoot_m=over,
                         oscillations=osc, time_s=secs))
        print(rows[-1])

        s = fresh_start(link, d)
        x0, y0 = s["x"], s["y"]
        square = [(x0 + 1.5, y0), (x0 + 1.5, y0 + 1.5), (x0, y0 + 1.5), (x0, y0)]
        path, ws, secs, ok = follow(link, square)
        final = settle(link, d, path)
        err, over, osc = metrics(path, ws, square[2], square[3], final)
        paths[("square", ms)] = path
        rows.append(dict(task="square", delay_ms=ms, ok=ok, final_error_m=err, overshoot_m=over,
                         oscillations=osc, time_s=secs))
        print(rows[-1])

        actual, seen = estop_test(link, d)
        rows.append(dict(task="estop", delay_ms=ms, estop_stop_s=actual, estop_seen_s=seen,
                         false_hb_stops=len(comm_lost)))
        print(rows[-1])

    link.cmd("stop")
    link.close()

    fields = ["task", "delay_ms", "ok", "final_error_m", "overshoot_m", "oscillations", "time_s",
              "estop_stop_s", "estop_seen_s", "false_hb_stops"]
    with open(f"{OUT}/results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        w.writerows(rows)

    fig, ax = plt.subplots(1, 3, figsize=(14, 4))
    for task in ("goto", "square"):
        r = [x for x in rows if x["task"] == task]
        ax[0].plot([x["delay_ms"] for x in r], [x["final_error_m"] for x in r], "o-", label=task)
        ax[1].plot([x["delay_ms"] for x in r], [x["overshoot_m"] for x in r], "o-", label=task)
    r = [x for x in rows if x["task"] == "estop"]
    ax[2].plot([x["delay_ms"] for x in r], [x["estop_stop_s"] for x in r], "o-", label="robot stopped")
    ax[2].plot([x["delay_ms"] for x in r], [x["estop_seen_s"] for x in r], "o-", label="operator sees stop")
    for a, t in zip(ax, ["final position error (m)", "overshoot (m)", "E-stop reaction (s)"]):
        a.set_xlabel("one-way delay (ms)"); a.set_title(t); a.legend(); a.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(f"{OUT}/error_vs_delay.png", dpi=120)

    fig, ax = plt.subplots(1, 2, figsize=(12, 6))
    for a, task in zip(ax, ("goto", "square")):
        for ms in DELAYS_MS:
            xs, ys = zip(*paths[(task, ms)])
            a.plot(xs, ys, label=f"{ms} ms")
        a.set_title(f"{task} path"); a.axis("equal"); a.legend(); a.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(f"{OUT}/paths.png", dpi=120)
    print(f"wrote {OUT}/")


if __name__ == "__main__":
    main()
