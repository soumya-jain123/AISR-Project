"""
Data Analysis and Plotting for Network Delay Study.
Reads experiment CSV results and generates:
1. Delay vs. Position Error & Overshoot curve (PNG)
2. Delay vs. E-Stop Reaction & Task Completion Time (PNG)
3. 2D Path Trajectories Overlay (PNG)
4. Formatted Markdown Results Table for Project Report
"""

import glob
import os
import sys
import matplotlib.pyplot as plt
import pandas as pd

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def generate_plots_and_report(results_dir: str = "results"):
    summary_path = os.path.join(results_dir, "delay_experiment_summary.csv")
    if not os.path.exists(summary_path):
        print(f"Error: {summary_path} not found. Run experiments first.")
        return

    df = pd.read_csv(summary_path)

    # 1. Plot: Delay vs Position Error & Overshoot
    plt.figure(figsize=(8, 5))
    plt.plot(df["delay_ms"], df["avg_error_m"], "o-", color="#1f77b4", linewidth=2.5, markersize=8, label="Final Position Error (m)")
    plt.plot(df["delay_ms"], df["avg_overshoot_m"], "s--", color="#d62728", linewidth=2.5, markersize=8, label="Overshoot (m)")
    plt.title("Effect of Network Delay on Closed-Loop Position Accuracy", fontsize=13, fontweight="bold")
    plt.xlabel("Simulated Network Delay (ms)", fontsize=11)
    plt.ylabel("Error / Distance (meters)", fontsize=11)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend(fontsize=10)
    plt.tight_layout()
    plot1_path = os.path.join(results_dir, "plot_delay_vs_error.png")
    plt.savefig(plot1_path, dpi=300)
    plt.close()
    print(f"Saved: {plot1_path}")

    # 2. Plot: Delay vs E-Stop Reaction Time & Task Completion Time
    fig, ax1 = plt.subplots(figsize=(8, 5))

    color1 = "#2ca02c"
    ax1.set_xlabel("Simulated Network Delay (ms)", fontsize=11)
    ax1.set_ylabel("E-Stop Reaction Time (ms)", color=color1, fontsize=11)
    line1 = ax1.plot(df["delay_ms"], df["avg_estop_reaction_ms"], "o-", color=color1, linewidth=2.5, markersize=8, label="E-Stop Reaction Time (ms)")
    ax1.tick_params(axis="y", labelcolor=color1)
    ax1.grid(True, linestyle="--", alpha=0.5)

    ax2 = ax1.twinx()
    color2 = "#9467bd"
    ax2.set_ylabel("Task Completion Time (s)", color=color2, fontsize=11)
    line2 = ax2.plot(df["delay_ms"], df["avg_completion_time_s"], "^--", color=color2, linewidth=2.5, markersize=8, label="Task Completion Time (s)")
    ax2.tick_params(axis="y", labelcolor=color2)

    lines = line1 + line2
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc="upper left", fontsize=10)

    plt.title("Effect of Delay on E-Stop Latency & Completion Time", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plot2_path = os.path.join(results_dir, "plot_delay_vs_timing.png")
    plt.savefig(plot2_path, dpi=300)
    plt.close()
    print(f"Saved: {plot2_path}")

    # 3. Plot: 2D Trajectory Overlay
    plt.figure(figsize=(7, 7))
    traj_files = glob.glob(os.path.join(results_dir, "trajectory_delay_*ms.csv"))
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    for i, tf in enumerate(sorted(traj_files)):
        tdf = pd.read_csv(tf)
        delay_label = os.path.basename(tf).replace("trajectory_delay_", "").replace(".csv", "")
        plt.plot(tdf["x"], tdf["y"], linewidth=2, label=f"Delay {delay_label}", color=colors[i % len(colors)])

    # Draw nominal square path
    square_x = [0, 1.5, 1.5, 0, 0]
    square_y = [0, 0, 1.5, 1.5, 0]
    plt.plot(square_x, square_y, "k--", linewidth=1.5, alpha=0.7, label="Nominal Path")

    plt.title("Robot Trajectories Under Varying Network Latency", fontsize=13, fontweight="bold")
    plt.xlabel("X Position (m)", fontsize=11)
    plt.ylabel("Y Position (m)", fontsize=11)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend(fontsize=9, loc="best")
    plt.axis("equal")
    plt.tight_layout()
    plot3_path = os.path.join(results_dir, "plot_trajectories.png")
    plt.savefig(plot3_path, dpi=300)
    plt.close()
    print(f"Saved: {plot3_path}")

    # 4. Generate Markdown Table for Report
    table_path = os.path.join(results_dir, "results_table.md")
    with open(table_path, "w", encoding="utf-8") as f:
        f.write("# Quantitative Experimental Results\n\n")
        f.write("| Network Delay (ms) | Final Position Error (m) | Overshoot (m) | Completion Time (s) | E-Stop Reaction Time (ms) |\n")
        f.write("|:------------------:|:------------------------:|:-------------:|:-------------------:|:-------------------------:|\n")
        for _, row in df.iterrows():
            f.write(
                f"| {int(row['delay_ms'])} ms "
                f"| {row['avg_error_m']:.4f} "
                f"| {row['avg_overshoot_m']:.4f} "
                f"| {row['avg_completion_time_s']:.2f} "
                f"| {row['avg_estop_reaction_ms']:.1f} |\n"
            )
    print(f"Saved results table: {table_path}")


if __name__ == "__main__":
    generate_plots_and_report()
