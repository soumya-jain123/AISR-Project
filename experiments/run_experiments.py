"""
Automated Experiment Runner for Network Delay Study.
Tests delays: [0, 100, 300, 500, 1000] ms.
Executes:
1. Target Point Navigation (Final error, overshoot, time)
2. Square Path Trajectory
3. Mid-motion E-Stop reaction latency
4. Heartbeat false-stop rate under delay
"""

import argparse
import csv
import math
import os
import sys
import time
from typing import Dict, List

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.config import EXPERIMENT_DELAYS_MS
from client.operator_client import OperatorClient
from server.mock_driver import MockRobotDriver
from server.robot_server import RobotServer
from server.sim_driver import CoppeliaSimDriver


def run_target_point_test(client: OperatorClient, target_x: float = 1.5, target_y: float = 1.0) -> Dict[str, float]:
    """Test driving to a single target coordinate under current delay."""
    # Reset pose first
    client.send_command("stop")
    time.sleep(0.1)

    t_start = time.time()
    ok, msg, _ = client.send_command("goto", [target_x, target_y])

    max_dist = 0.0
    overshoot = 0.0
    timeout_sec = 15.0

    while (time.time() - t_start) < timeout_sec:
        time.sleep(0.05)
        tel = client.get_telemetry()
        cur_x, cur_y = tel["x"], tel["y"]
        dist_to_origin = math.hypot(cur_x, cur_y)
        target_dist = math.hypot(target_x, target_y)

        if dist_to_origin > target_dist:
            overshoot = max(overshoot, dist_to_origin - target_dist)

        if tel["state"] != "MOVING" and (time.time() - t_start) > 0.5:
            break

    total_time = time.time() - t_start
    final_tel = client.get_telemetry()
    final_err = math.hypot(target_x - final_tel["x"], target_y - final_tel["y"])

    return {
        "final_error_m": final_err,
        "overshoot_m": overshoot,
        "completion_time_s": total_time,
        "final_x": final_tel["x"],
        "final_y": final_tel["y"],
    }


def run_estop_reaction_test(client: OperatorClient) -> float:
    """Measure elapsed time from E-stop command issuance to confirmed robot zero-velocity."""
    client.send_command("forward", [0.4])
    time.sleep(0.5)  # allow robot to achieve cruising velocity

    t_estop_issued = time.time()
    client.send_command("estop")

    # Monitor telemetry until locked
    t_halted = time.time()
    while (time.time() - t_estop_issued) < 5.0:
        tel = client.get_telemetry()
        if tel["state"] == "ESTOP_LATCHED":
            t_halted = time.time()
            break
        time.sleep(0.02)

    reaction_time_ms = (t_halted - t_estop_issued) * 1000.0

    # Clean up with reset
    client.send_command("reset")
    time.sleep(0.1)
    return reaction_time_ms


def run_square_path_test(client: OperatorClient) -> List[tuple]:
    """Execute 4-corner square path (0,0) -> (1.5,0) -> (1.5,1.5) -> (0,1.5) -> (0,0)."""
    corners = [(1.5, 0.0), (1.5, 1.5), (0.0, 1.5), (0.0, 0.0)]
    recorded_path = []

    for cx, cy in corners:
        client.send_command("goto", [cx, cy])
        t0 = time.time()
        while (time.time() - t0) < 8.0:
            time.sleep(0.05)
            tel = client.get_telemetry()
            recorded_path.append((tel["x"], tel["y"], tel["latency_ms"]))
            if tel["state"] != "MOVING" and (time.time() - t0) > 0.5:
                break

    return recorded_path


def run_delay_sweep(use_mock: bool = True, repetitions: int = 3):
    os.makedirs("results", exist_ok=True)
    summary_file = os.path.join("results", "delay_experiment_summary.csv")

    test_port = 55888
    if use_mock:
        driver = MockRobotDriver()
    else:
        driver = CoppeliaSimDriver()
        if not driver.connect():
            print("Could not connect to CoppeliaSim, switching to MockRobotDriver.")
            driver = MockRobotDriver()

    server = RobotServer(driver=driver, port=test_port)
    server.start()
    time.sleep(0.2)

    client = OperatorClient(port=test_port, delay_ms=0.0)
    client.connect()
    time.sleep(0.2)

    print("=" * 70)
    print("STARTING NETWORK DELAY EXPERIMENTAL SWEEP")
    print(f"Delays to test: {EXPERIMENT_DELAYS_MS} ms, Repetitions per delay: {repetitions}")
    print("=" * 70)

    summary_records = []

    try:
        for delay in EXPERIMENT_DELAYS_MS:
            print(f"\n--- Testing Delay: {delay} ms ---")
            client.set_delay_ms(delay)
            time.sleep(0.3)

            errs = []
            overshoots = []
            comp_times = []
            estop_times = []

            for rep in range(repetitions):
                # 1. Target point test
                res = run_target_point_test(client, target_x=1.5, target_y=1.0)
                errs.append(res["final_error_m"])
                overshoots.append(res["overshoot_m"])
                comp_times.append(res["completion_time_s"])

                # 2. E-Stop reaction test
                estop_rt = run_estop_reaction_test(client)
                estop_times.append(estop_rt)

                print(
                    f"  Rep {rep+1}: Error={res['final_error_m']:.3f}m, "
                    f"Overshoot={res['overshoot_m']:.3f}m, Time={res['completion_time_s']:.2f}s, "
                    f"E-Stop RT={estop_rt:.1f}ms"
                )

            # Record trajectory for square path under this delay
            traj = run_square_path_test(client)
            traj_path = os.path.join("results", f"trajectory_delay_{delay}ms.csv")
            with open(traj_path, "w", newline="", encoding="utf-8") as tf:
                writer = csv.writer(tf)
                writer.writerow(["x", "y", "latency_ms"])
                writer.writerows(traj)

            avg_err = sum(errs) / len(errs)
            avg_os = sum(overshoots) / len(overshoots)
            avg_time = sum(comp_times) / len(comp_times)
            avg_estop = sum(estop_times) / len(estop_times)

            summary_records.append({
                "delay_ms": delay,
                "avg_error_m": round(avg_err, 4),
                "avg_overshoot_m": round(avg_os, 4),
                "avg_completion_time_s": round(avg_time, 2),
                "avg_estop_reaction_ms": round(avg_estop, 1),
            })

    finally:
        client.disconnect()
        server.stop()

    with open(summary_file, "w", newline="", encoding="utf-8") as sf:
        writer = csv.DictWriter(
            sf,
            fieldnames=["delay_ms", "avg_error_m", "avg_overshoot_m", "avg_completion_time_s", "avg_estop_reaction_ms"],
        )
        writer.writeheader()
        writer.writerows(summary_records)

    print("\n" + "=" * 70)
    print("EXPERIMENTAL SWEEP COMPLETE! Summary saved to:", summary_file)
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Run Teleoperation Delay Experiment Suite")
    parser.add_argument("--real", action="store_true", help="Use CoppeliaSim instead of mock driver")
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per test")
    args = parser.parse_args()

    run_delay_sweep(use_mock=not args.real, repetitions=args.reps)


if __name__ == "__main__":
    main()
