"""Ablation study: turn one safety feature off at a time and count what goes wrong, in the three real-world scenes.
Start CoppeliaSim and server.py first, then:  python ablation.py   (about three hours)

Three tests, each run in every scene at 0, 300 and 1000 ms one-way delay:
  route    drive the scene's route.   Rows: full system (operator steers over the network),
           no auto-stop, and robot-side driving (the route is sent as 'goto' points and the robot steers itself).
  estop    a worker stands on the route; the operator hits E-stop when the worker looks 1.2 m away.
           The autopilot keeps sending drive commands until it sees the stop confirmed, as a joystick stream would.
           Rows: latch on, latch off.
  linkloss the network is cut 3 s into the route, for 20 s.  Rows: watchdog on, watchdog off.
The estop and linkloss tests run with the auto-stop off, so they measure the one feature alone.
Writes results/ablation.csv, results/ablation.png, results/ablation_paths.png.
If it stops part-way, run it again: finished runs in ablation.csv are kept and only the missing ones are run.
"""
import csv
import json
import math
import os
import threading
import time
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

import scenes
from client import Link

SCENE_KEYS = ["plant", "warehouse", "disaster"]
DELAYS_MS = [0, 300, 1000]
TRIALS = 3           # driving under delay varies run to run
SPEED = 0.4          # m/s, same as the robot-side goto
PASS = 0.3           # m; a route point counts as reached this close
STEER = 0.8          # operator's steering gain (rad/s per rad of heading error)
ROUTE_TIMEOUT = 150  # s
ESTOP_AT = 1.2       # m; operator hits E-stop when the worker looks this close
CUT_AFTER, CUT_FOR = 3, 20   # s
OUT = "results"


class Recorder:
    """Keeps every feedback message (the robot's path as the operator saw it) and every server message."""

    def __init__(self, link):
        self.link, self.fbs, self.events = link, [], []
        link.on_event = self.events.append
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        last = None
        while True:
            f = self.link.fb
            if f and f is not last:
                self.fbs.append(f)
                last = f
            time.sleep(0.02)

    def mark(self):
        return len(self.fbs), len(self.events)

    def since(self, m):
        return self.fbs[m[0]:], self.events[m[1]:]


def start(link, rec, key, still=False, safety=()):
    """Fresh run: every safety feature on except those in `safety`, scene rebuilt, robot at the start."""
    for f in ("watchdog", "latch", "autostop"):
        link.cmd(f"safety {f} {'off' if f in safety else 'on'}")
    run = link.fb["run"]
    link.cmd(f"scene {key}" + (" still" if still else ""))
    while link.fb["run"] == run:
        alive(link)
        time.sleep(0.05)
    # CoppeliaSim pauses for ~2 s a few seconds after a scene build; start once it has run smoothly for 3 s.
    steady, last = time.time(), link.fb
    while time.time() - steady < 3:
        alive(link)
        f = link.fb
        if f is not last:
            if f["ts"] - last["ts"] > 0.5:
                steady = time.time()
            last = f
        time.sleep(0.02)
    return rec.mark()


def alive(link):
    """The server shut down (e.g. CoppeliaSim froze): stop without writing a half-finished run. Run again to resume."""
    if not link.alive:
        print("lost the robot server; stopping. Restart it and run ablation.py again to resume.", flush=True)
        os._exit(1)


def drive_step(link, target):
    """One step of the operator's autopilot: steer toward target from (delayed) feedback. True when reached."""
    f = link.fb
    if math.hypot(target[0] - f["x"], target[1] - f["y"]) < PASS:
        return True
    # Steers like a person driving: keeps rolling and turns the wheel in proportion to the heading error.
    # (The robot-side goto stops and turns on the spot instead, which is too slow to feel natural by hand.)
    err = math.atan2(math.sin(math.atan2(target[1] - f["y"], target[0] - f["x"]) - f["yaw"]),
                     math.cos(math.atan2(target[1] - f["y"], target[0] - f["x"]) - f["yaw"]))
    v = SPEED * max(0.25, math.cos(err))
    w = max(-0.8, min(0.8, STEER * err))
    link.cmd(f"drive {v:.3f} {w:.3f}")
    return False


def finish(link, delay):
    link.cmd("stop")
    time.sleep(2 + 2 * delay)   # let the last feedback arrive


def outcome(fbs):
    last = fbs[-1]
    gaps = [f["person_gap"] for f in fbs if f["person_gap"] is not None]
    ts = [f["ts"] for f in fbs]
    gap = [b - a for a, b in zip(ts, ts[1:]) if b - a < 1]   # how often feedback really came (cuts excluded)
    return dict(hits=last["hits"], person_hits=last["person_hits"], autostops=last["autostops"],
                fell=bool(last["fell"]), min_person_gap_m=min(gaps) if gaps else None,
                fb_interval_s=mean(gap) if gap else None, stalls=last["stalls"] - fbs[0]["stalls"])


def route_test(link, rec, key, delay, config):
    route = scenes.SCENES[key]["route"]
    m = start(link, rec, key, safety=("autostop",) if config == "no auto-stop" else ())
    t0, ok, i = time.time(), False, 0
    while time.time() - t0 < ROUTE_TIMEOUT and not link.fb["fell"]:
        if config == "robot-side driving":
            codes = []
            link.cmd(f"goto {route[i][0]} {route[i][1]}")
            sent = len(rec.events)
            while time.time() - t0 < ROUTE_TIMEOUT and not link.fb["fell"]:
                codes = [e.get("code") for e in rec.events[sent:]]
                if "arrived" in codes or "autostop" in codes:
                    break
                time.sleep(0.05)
            if "arrived" in codes:
                i += 1
            else:
                time.sleep(1.5)   # blocked (e.g. the worker is in the way): wait, then send the goto again
        elif drive_step(link, route[i]):
            i += 1
        else:
            time.sleep(0.1)
        if i == len(route):
            ok = True
            break
    secs = time.time() - t0
    finish(link, delay)
    fbs, _ = rec.since(m)
    return dict(ok=ok, time_s=secs, **outcome(fbs)), [(f["x"], f["y"]) for f in fbs]


def estop_test(link, rec, key, delay, config):
    route = scenes.SCENES[key]["route"]
    m = start(link, rec, key, still=True, safety=("autostop",) + (("latch",) if config == "latch off" else ()))
    t0, sent_at, i = time.time(), None, 0
    while time.time() - t0 < 40 and i < len(route):
        f = link.fb
        if sent_at is None and f["person_gap"] is not None and f["person_gap"] < ESTOP_AT:
            link.cmd("estop")
            sent_at, seen = time.time(), len(rec.events)
        if sent_at and any(e.get("code") == "estop" for e in rec.events[seen:]):
            break   # the operator now sees the stop and lets go of the controls
        if drive_step(link, route[i]):
            i += 1
        time.sleep(0.1)
    finish(link, delay + 1)
    link.cmd("reset")
    fbs, _ = rec.since(m)
    after = [f for f in fbs if f["estop_ts"] and f["ts"] >= f["estop_ts"]]
    travel = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(after, after[1:]))
    gap_after = [f["person_gap"] for f in after if f["person_gap"] is not None]
    return dict(estop_sent=sent_at is not None, travel_after_estop_m=travel,
                gap_at_rest_m=gap_after[-1] if gap_after else None, **outcome(fbs)), [(f["x"], f["y"]) for f in fbs]


def linkloss_test(link, rec, key, delay, config):
    route = scenes.SCENES[key]["route"]
    m = start(link, rec, key, safety=("autostop",) + (("watchdog",) if config == "watchdog off" else ()))
    t0 = time.time()
    while time.time() - t0 < CUT_AFTER:
        drive_step(link, route[0])
        time.sleep(0.1)
    link.cut = True
    t_cut = time.time()
    time.sleep(CUT_FOR)
    link.cut = False
    finish(link, delay)
    fbs, _ = rec.since(m)
    before = [f for f in fbs if f["ts"] <= t_cut]
    a, b = before[-1], fbs[-1]
    return dict(travel_after_cut_m=math.hypot(b["x"] - a["x"], b["y"] - a["y"]), **outcome(fbs)), \
        [(f["x"], f["y"]) for f in fbs]


TESTS = {
    "route": (route_test, ["full system", "no auto-stop", "robot-side driving"]),
    "estop": (estop_test, ["latch on", "latch off"]),
    "linkloss": (linkloss_test, ["watchdog on", "watchdog off"]),
}
FIELDS = ["test", "scene", "delay_ms", "config", "trial", "ok", "time_s", "estop_sent", "travel_after_estop_m",
          "gap_at_rest_m", "travel_after_cut_m", "hits", "person_hits", "autostops", "fell", "min_person_gap_m", "fb_interval_s", "stalls"]


def main():
    os.makedirs(OUT, exist_ok=True)
    link = Link("127.0.0.1", 5555, 0, on_event=lambda m: None)
    rec = Recorder(link)
    while not link.fb:
        time.sleep(0.05)
    table, path_file = f"{OUT}/ablation.csv", f"{OUT}/ablation_paths.json"
    rows = []
    if os.path.exists(table):   # resume: keep the runs already done
        with open(table) as fh:
            rows = [{k: num(v) for k, v in r.items()} for r in csv.DictReader(fh)]
    paths = {}
    if os.path.exists(path_file):
        with open(path_file) as fh:
            paths = {tuple(json.loads(k)): v for k, v in json.load(fh).items()}
    done = {(r["test"], r["scene"], r["delay_ms"], r["config"], r["trial"]) for r in rows}

    def save_paths():
        with open(path_file, "w") as fh:
            json.dump({json.dumps(list(k)): v for k, v in paths.items()}, fh)

    with open(table, "a" if rows else "w", newline="") as fh:
        out = csv.DictWriter(fh, FIELDS)
        if not rows:
            out.writeheader()
        for test, (fn, configs) in TESTS.items():
            for key in SCENE_KEYS:
                for ms in DELAYS_MS:
                    link.set_delay(ms)
                    for trial in range(TRIALS):
                        for config in configs:
                            if (test, key, ms, config, trial) in done:
                                continue
                            best = None   # a run the simulator paused during isn't a fair measurement: redo it
                            for attempt in range(4):
                                row, path = fn(link, rec, key, ms / 1000, config)
                                alive(link)
                                if best is None or row["stalls"] < best[0]["stalls"]:
                                    best = row, path
                                if not row["stalls"]:
                                    break
                                print("simulator paused during the run; running it again", flush=True)
                            row, path = best
                            row = dict(test=test, scene=key, delay_ms=ms, config=config, trial=trial, **row)
                            print(row, flush=True)
                            rows.append(row)
                            out.writerow(row)
                            fh.flush()
                            if ms == 1000:
                                paths.setdefault((test, key, ms, config), path)
                                save_paths()
    # The path plot shows one run per setting at 1000 ms; rerun any whose path wasn't saved.
    link.set_delay(1000)
    for test, (fn, configs) in TESTS.items():
        for key in SCENE_KEYS:
            for config in configs:
                if (test, key, 1000, config) not in paths:
                    print("path run:", test, key, config, flush=True)
                    paths[(test, key, 1000, config)] = fn(link, rec, key, 1.0, config)[1]
                    alive(link)
                    save_paths()
    link.set_delay(0)
    for f in ("watchdog", "latch", "autostop"):
        link.cmd(f"safety {f} on")
    link.cmd("scene open")
    time.sleep(1)
    link.close()
    plot(rows, paths)
    print(f"wrote {OUT}/ablation.csv, ablation.png, ablation_paths.png")


def num(v):
    """CSV text back to a value."""
    if v in ("True", "False"):
        return v == "True"
    if v == "":
        return None
    for kind in (int, float):
        try:
            return kind(v)
        except ValueError:
            pass
    return v


def plot(rows, paths):
    def bars(ax, test, value, title):
        r = [x for x in rows if x["test"] == test]
        configs = TESTS[test][1]
        groups = [(k, ms) for k in SCENE_KEYS for ms in DELAYS_MS]
        width = 0.8 / len(configs)
        for j, c in enumerate(configs):
            ys = [mean([value(x) for x in r if (x["scene"], x["delay_ms"], x["config"]) == (k, ms, c)])
                  for k, ms in groups]
            ax.bar([g + j * width for g in range(len(groups))], ys, width, label=c)
        ax.set_xticks([g + 0.4 - width / 2 for g in range(len(groups))])
        ax.set_xticklabels([f"{k}\n{ms} ms" for k, ms in groups], fontsize=7)
        ax.set_title(title)
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=.3)

    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    fig.suptitle(f"Mean of {TRIALS} runs per bar")
    bars(ax[0][0], "route", lambda x: x["hits"], "Route: collisions per run (F = runs where it fell off)")
    groups = [(k, ms) for k in SCENE_KEYS for ms in DELAYS_MS]
    for j, c in enumerate(TESTS["route"][1]):
        for g, (k, ms) in enumerate(groups):
            n = sum(1 for x in rows if x["test"] == "route" and (x["scene"], x["delay_ms"], x["config"]) == (k, ms, c)
                    and x["fell"])
            if n:
                ax[0][0].text(g + j * 0.8 / 3, 0.1, f"F{n}", ha="center", fontsize=7, color="tab:red")
    bars(ax[0][1], "route", lambda x: 100.0 * x["ok"], "Route: runs finished (%)")
    bars(ax[1][0], "estop", lambda x: x["travel_after_estop_m"], "E-stop: distance driven after the E-stop (m)")
    bars(ax[1][1], "linkloss", lambda x: x["travel_after_cut_m"], "Link cut: distance driven after the cut (m)")
    fig.tight_layout()
    fig.savefig(f"{OUT}/ablation.png", dpi=120)

    # Paths at 1000 ms over each scene's layout.
    fig, ax = plt.subplots(len(SCENE_KEYS), 3, figsize=(15, 4.6 * len(SCENE_KEYS)))
    for r, key in enumerate(SCENE_KEYS):
        for c, test in enumerate(TESTS):
            a = ax[r][c]
            draw_scene(a, key, still=test == "estop")
            for config in TESTS[test][1]:
                p = paths.get((test, key, 1000, config))
                if p:
                    xs, ys = zip(*p)
                    a.plot(xs, ys, label=config)
            a.set_title(f"{scenes.SCENES[key]['name']}: {test} at 1000 ms")
            a.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(f"{OUT}/ablation_paths.png", dpi=110)


FOOTPRINT = {"wall section 200cm": (0.16, 2.0), "wall section 100cm": (0.16, 1.0), "rack": (0.3, 1.2),
             "deep cupboard": (0.6, 1.2), "pillar 50cm": (0.5, 0.5), "resizable concret block": (0.4, 0.4)}


def draw_scene(a, key, still):
    s = scenes.SCENES[key]
    x0, x1, y0, y1 = s["floor"]
    a.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, color="0.93"))
    for path, x, y, yaw, label in s["items"]:
        size = next((v for k, v in FOOTPRINT.items() if k in path), None)
        if size:   # footprint is (local x, local y); the model is turned by yaw
            a.add_patch(Rectangle((x - size[0] / 2, y - size[1] / 2), *size, angle=yaw, rotation_point="center",
                                  color="0.45"))
    people = [s["still"]] if still else ([s["worker"][0], s["worker"][1]] if s["worker"] else [])
    if s["worker"] and not still:
        a.plot(*zip(*s["worker"]), ":", color="tab:red", lw=1)
    for p in people + ([s["victim"]] if s.get("victim") else []):
        a.add_patch(Circle(p, 0.25, color="tab:red"))
    a.plot(*zip(s["start"][:2], *s["route"]), "--", color="0.6", lw=1)
    a.set_xlim(x0 - 0.3, x1 + 0.3)
    a.set_ylim(y0 - 0.3, y1 + 0.3)
    a.set_aspect("equal")


if __name__ == "__main__":
    main()
