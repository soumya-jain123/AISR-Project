"""Study: how does network delay affect closed-loop control?

A proportional controller drives the robot to x = TARGET (closed loop).
Sensor feedback and control commands each suffer a one-way delay d, so the
controller acts on state that is 2d old on average. We sweep d and measure:
  overshoot (%), settling time (s), and whether the loop is stable.
Also runs an open-loop baseline (fixed-duration drive, no feedback).

Run:  python experiment_delay.py          -> prints table, writes delay_results.csv
                                             and delay_plot.png (if matplotlib is installed)
"""
import csv
from collections import deque

from robot_sim import RobotSim

DT = 0.02
TARGET = 2.0
KP = 1.5
T_END = 20.0


def run_closed_loop(delay_s, kp=KP):
    robot = RobotSim()
    n = int(round(delay_s / DT))
    sensor_buf = deque([0.0] * (n + 1), maxlen=n + 1)   # feedback path delay
    cmd_buf = deque([0.0] * (n + 1), maxlen=n + 1)      # command path delay
    xs = []
    for _ in range(int(T_END / DT)):
        sensor_buf.append(robot.get_state()["x"])
        x_seen = sensor_buf[0]                      # controller sees old position
        cmd_buf.append(kp * (TARGET - x_seen))       # P control law
        robot.set_velocity(cmd_buf[0], 0.0)          # robot applies old command
        robot.step(DT)
        xs.append(robot.get_state()["x"])
    return xs


def metrics(xs):
    peak = max(xs)
    overshoot = max(0.0, (peak - TARGET) / TARGET * 100)
    settle = None
    for i in range(len(xs)):
        if all(abs(x - TARGET) <= 0.02 * TARGET for x in xs[i:]):
            settle = i * DT
            break
    # unstable if oscillation is still large in the last 2 seconds
    tail = xs[-int(2 / DT):]
    stable = (max(tail) - min(tail)) < 0.1 and abs(xs[-1] - TARGET) < 0.2
    return overshoot, settle, stable


def run_open_loop(duration=TARGET / 0.5, v=0.5, delay_s=0.0):
    """Drive at v for a fixed time. Network delay shifts when STOP arrives."""
    robot = RobotSim()
    robot.set_velocity(v, 0)
    for _ in range(int((duration + delay_s) / DT)):
        robot.step(DT)
    robot.set_velocity(0, 0)
    return robot.get_state()["x"]


if __name__ == "__main__":
    delays = [0.0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5]
    rows = []
    print(f"Closed loop P-control, Kp={KP}, target={TARGET} m  "
          f"(theory: unstable when Kp * round-trip delay > pi/2, i.e. one-way delay > ~0.52 s)")
    print(f"{'one-way delay':>14} {'overshoot %':>12} {'settle (s)':>11} {'stable':>7} {'OL final x':>11}")
    for d in delays:
        xs = run_closed_loop(d)
        ov, st, ok = metrics(xs)
        ol = run_open_loop(delay_s=d)
        rows.append([d, round(ov, 2), st if st is not None else "never", ok, round(ol, 3)])
        print(f"{d:>14.2f} {ov:>12.1f} {str(st if st is None else round(st, 2)):>11} {str(ok):>7} {ol:>11.3f}")
    with open("delay_results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["one_way_delay_s", "overshoot_pct", "settling_time_s", "stable", "open_loop_final_x"])
        w.writerows(rows)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        t = [i * DT for i in range(int(T_END / DT))]
        plt.figure(figsize=(8, 5))
        for d in [0.0, 0.2, 0.6, 1.0]:
            plt.plot(t, run_closed_loop(d), label=f"delay {d}s")
        plt.axhline(TARGET, color="k", ls="--", lw=0.8)
        plt.xlabel("time (s)"); plt.ylabel("x position (m)")
        plt.title("Effect of network delay on closed-loop P control")
        plt.legend(); plt.grid(alpha=0.3)
        plt.savefig("delay_plot.png", dpi=120)
        print("saved delay_results.csv and delay_plot.png")
    except ImportError:
        print("saved delay_results.csv (install matplotlib for the plot)")
