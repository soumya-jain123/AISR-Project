# P11 - Networked Teleoperation of a Robot through Shell Commands
CSAI4014 AISR | Group G09 | Batch B2 | CoppeliaSim + Python socket / MQTT (paho)

You type commands (`forward 0.3`, `left 90`, `goto 2 1`) in a terminal. They travel over the
network (TCP socket **or** MQTT) to a **bridge** that moves a robot in CoppeliaSim, with a local
**safety layer** on the robot side. The same CLI works over both transports, so they can be compared.

```
 teleop.py (CLI) --[socket | MQTT via Mosquitto]--> run_bridge.py --ZeroMQ--> CoppeliaSim (P3DX)
   heartbeat 10 Hz, retries, RTT log               validation, watchdog, obstacle stop,
   optional delay / loss injection                 e-stop, adaptive speed, CSV log
```

## 1. Install (Windows)
1. **Python 3.10+** from python.org (tick "Add Python to PATH").
2. **CoppeliaSim Edu** (free) from coppeliarobotics.com (version 4.6 or newer).
3. **Mosquitto** (MQTT broker) from mosquitto.org/download (Windows installer).
4. In a terminal (PowerShell or CMD) inside this folder:
```
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
Allow Python through the Windows Firewall if asked.

## 2. Test WITHOUT the simulator first (built-in fake robot)
Terminal 1: `python run_bridge.py --robot fake --transport socket`
Terminal 2: `python teleop.py --transport socket` then type `status`, `forward 0.3`, `stop`, `help`.
Unit tests: `python -m unittest discover -s tests -v` (12 tests, no simulator needed).

## 3. Start Mosquitto (only for the MQTT method)
Open CMD **as Administrator**: `net start mosquitto`
(or run `"C:\Program Files\mosquitto\mosquitto.exe" -v` in a separate window).
If connections are refused, add `listener 1883` and `allow_anonymous true` to `mosquitto.conf`.
Then use `--transport mqtt` (or `both` for the bridge).

## 4. Build the CoppeliaSim scene (once)
1. File > New scene. Drag **robots > mobile > Pioneer p3dx** from the model browser into the scene.
2. The bridge removes the child script associated with `/PioneerP3DX` before starting the
  simulation, so it cannot compete with remote motor commands.
3. Place the robot at x=0, y=0 facing +x. Add 3 cylinders (Add > Primitive shape > Cylinder,
   size 0.5 x 0.5 x 0.5 m) at world positions (x, y): **(1.4, 0.0), (2.5, 0.45), (3.3, -0.45)**.
   Make sure they are *Detectable* by ultrasonic sensors (object properties > Common).
4. Save the scene. Keep CoppeliaSim open (its ZeroMQ remote API starts by itself on port 23000).
5. Run `python check_coppelia.py`. It confirms object names and the driving direction. If the robot
   drives sideways or backwards, change `FORWARD_YAW_OFFSET` in `p11/robots.py` as the script says.
   If an object name is not found, fix the paths at the top of `CoppeliaRobot` in `p11/robots.py`.
  To set a wheel torque limit, set `P11_MOTOR_TORQUE` in N*m before starting the bridge;
  start conservatively at `2` and increase only if the wheels cannot track the commanded speed.

## 5. Run the real system
```
python run_bridge.py --transport both                       # terminal 1 (Coppelia is the default)
python teleop.py --transport socket                         # terminal 2
python teleop.py --transport mqtt                           # (or this one)
python teleop.py --transport socket --delay-ms 300 --loss 0.1    # bad network demo
python teleop.py --script scripts/demo.txt                  # scripted demo
```
Use `--robot fake` only when intentionally testing without CoppeliaSim.
`forward`, `back`, and `drive` keep their commanded velocity until `stop`; the client heartbeat
keeps the watchdog satisfied while the terminal is open. For a timed move, use for example
`python teleop.py --transport socket -c "forward 0.3; wait 2; stop"`.
Commands: `forward [v]  back [v]  left <deg>  right <deg>  drive <v> <w>  stop  speed <v>
goto <x> <y>  status  sensors  safety on|off  estop  reset  wait <s>  script <file>  help  quit`.
Logs: `teleop_log.csv` (client: send/reply time, RTT, attempts) and `bridge_log.csv`
(sent / received / executed timestamps for every command).

## 6. Experiments, plots, tables for the report
```
python experiments/run_experiments.py --robot coppelia --grid quick                 # ~4-5 min
python experiments/run_experiments.py --robot coppelia --grid full --repeats 3      # long
python experiments/plot_results.py
```
`--robot fake` runs the same experiment without the simulator (for testing the scripts ONLY;
never put fake-robot numbers in the report). Output in `results/`: `results.csv`, `traj/`,
`fig_rtt_vs_delay.png`, `fig_collisions.png`, `fig_success.png`, `fig_paths.png`,
`summary_table.csv`, `summary_table.tex` (paste into Overleaf).
Metrics: RTT mean/p95/max, command delivery %, mission success, time, collisions, path length
ratio, safety interventions, watchdog trips. Compare socket vs MQTT, delay, loss, safety on/off.

## Design notes
* **Loss/delay injection** is applied on the client in the application layer (half of `--delay-ms`
  each way). TCP/MQTT do not see it, so both transports face the same conditions. Retries
  (`--retries`, `--timeout`) recover lost commands; the bridge ignores duplicate command ids.
* **Watchdog**: the client sends a ping 10x per second; if the bridge hears nothing for 0.5 s while
  the robot is moving, it stops. **Obstacle stop**: forward speed is scaled to 0 between 0.7 m and
  0.3 m front distance. **E-stop** is latched. **Adaptive speed**: max speed shrinks when the
  client-reported RTT is above 150 ms (this is the novelty to evaluate: adaptive on vs off).
* "Collision" = a sensor gap below 0.05 m (rising edge). Same rule for fake and CoppeliaSim.
* Round-trip timestamps use one clock, valid on one machine. Across two PCs, clocks differ; use RTT.
* Two PCs: run the bridge with `--host 0.0.0.0`, the client with `--host <bridge-ip>`,
  and point both MQTT sides to the broker's IP.

## Files and suggested ownership (each member must commit their own files from their own account)
| Member | Files |
|---|---|
| M1 | `p11/robots.py` (CoppeliaRobot), scene, `check_coppelia.py` |
| M2 | `p11/protocol.py`, `p11/transports.py` (socket part), `teleop.py` |
| M3 | `p11/transports.py` (MQTT part), `run_bridge.py`, Mosquitto setup |
| M4 | `p11/bridge.py` (safety layer, goto/turn controllers, adaptive speed) |
| M5 | `p11/client.py`, `p11/netsim.py`, `experiments/`, `tests/` |
