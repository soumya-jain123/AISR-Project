"""Make report figures and tables from results/results.csv.

  python experiments/plot_results.py            # reads results/results.csv
Outputs (in results/): fig_rtt_vs_delay.png, fig_collisions.png,
fig_success.png, fig_paths.png, summary_table.csv, summary_table.tex
"""
import argparse
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import pandas as pd                      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p11.robots import default_obstacles  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default="results")
a = ap.parse_args()
df = pd.read_csv(os.path.join(a.dir, "results.csv"))
df["safety_lbl"] = df["safety"].map({True: "safety ON", False: "safety OFF"})
COL = {"socket": "tab:blue", "mqtt": "tab:orange"}

# 1. RTT vs injected delay (no loss, safety on)
base = df[(df.loss == 0) & (df.safety == True)]            # noqa: E712
g = base.groupby(["transport", "delay"])[["rtt_mean_ms", "rtt_p95_ms"]].mean().reset_index()
plt.figure(figsize=(5.5, 3.8))
for t, d in g.groupby("transport"):
    plt.plot(d.delay, d.rtt_mean_ms, "o-", color=COL.get(t), label=f"{t} mean")
    plt.plot(d.delay, d.rtt_p95_ms, "x--", color=COL.get(t), alpha=.6, label=f"{t} p95")
plt.xlabel("Injected round-trip delay (ms)")
plt.ylabel("Measured RTT (ms)")
plt.grid(alpha=.3)
plt.legend()
plt.tight_layout()
plt.savefig(os.path.join(a.dir, "fig_rtt_vs_delay.png"), dpi=200)
plt.close()

# 2. Collisions with / without the safety layer
d0 = df[df.loss == 0]
piv = d0.groupby(["delay", "safety_lbl"])["collisions"].mean().unstack()
ax = piv.plot(kind="bar", figsize=(5.5, 3.8), color=["tab:red", "tab:green"][:len(piv.columns)])
ax.set_xlabel("Injected round-trip delay (ms)")
ax.set_ylabel("Mean collisions per run")
ax.grid(axis="y", alpha=.3)
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig(os.path.join(a.dir, "fig_collisions.png"), dpi=200)
plt.close()

# 3. Task success rate and command delivery vs loss (delay 0), if loss runs exist
ls = df[(df.delay == 0)]
if ls.loss.nunique() > 1:
    gg = ls.groupby(["transport", "loss"])[["success", "cmd_success"]].mean().reset_index()
    plt.figure(figsize=(5.5, 3.8))
    for t, d in gg.groupby("transport"):
        plt.plot(d.loss * 100, d.cmd_success * 100, "o-", color=COL.get(t), label=f"{t} commands delivered")
        plt.plot(d.loss * 100, d.success * 100, "s--", color=COL.get(t), alpha=.6, label=f"{t} mission success")
    plt.xlabel("Message loss (%)")
    plt.ylabel("Percent")
    plt.grid(alpha=.3)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(a.dir, "fig_success.png"), dpi=200)
    plt.close()

# 4. Paths: all runs of the first run-label per transport with delay 0 and max delay
fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), sharey=True)
for ax, (title, dsel) in zip(axes, [("Delay 0 ms", df.delay.min()), (f"Delay {df.delay.max()} ms", df.delay.max())]):
    for cx, cy, cr in default_obstacles():
        ax.add_patch(plt.Circle((cx, cy), cr, color="grey", alpha=.5))
    for _, r in df[(df.delay == dsel) & (df.loss == 0)].iterrows():
        fn = os.path.join(a.dir, "traj", r["label"] + ".csv")
        if not os.path.exists(fn):
            continue
        t = pd.read_csv(fn)
        ax.plot(t.x, t.y, color=COL.get(r["transport"]), ls="-" if r["safety"] else ":",
                label=f"{r['transport']}, {'safe' if r['safety'] else 'no safety'}")
    ax.plot(0, 0, "k^")
    ax.plot(4, 0, "g*", ms=12)
    ax.set_aspect("equal")
    ax.set_title(title)
    ax.set_xlabel("x (m)")
    ax.grid(alpha=.3)
axes[0].set_ylabel("y (m)")
h, l = axes[1].get_legend_handles_labels()
by = dict(zip(l, h))
axes[1].legend(by.values(), by.keys(), fontsize=7)
plt.tight_layout()
plt.savefig(os.path.join(a.dir, "fig_paths.png"), dpi=200)
plt.close()

# 5. Summary table (CSV + LaTeX)
tab = df.groupby(["transport", "delay", "loss", "safety_lbl"]).agg(
    runs=("success", "size"), success_pct=("success", lambda s: 100 * s.mean()),
    time_s=("time_s", "mean"), collisions=("collisions", "mean"),
    rtt_mean_ms=("rtt_mean_ms", "mean"), rtt_p95_ms=("rtt_p95_ms", "mean"),
    cmd_delivered_pct=("cmd_success", lambda s: 100 * s.mean())).round(1).reset_index()
tab.to_csv(os.path.join(a.dir, "summary_table.csv"), index=False)
with open(os.path.join(a.dir, "summary_table.tex"), "w") as f:
    f.write(tab.to_latex(index=False, escape=True))
print(tab.to_string(index=False))
print("Figures and tables saved in", a.dir)
