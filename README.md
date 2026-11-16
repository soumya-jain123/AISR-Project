# AISR-Project
Networked Teleoperation of a Robot through Shell Commands

## Networked robot teleoperation (CoppeliaSim)

Drive a Pioneer P3-DX in CoppeliaSim with terminal commands sent over TCP, with heartbeat and emergency stop.

1. `pip install -r requirements.txt`
2. Open CoppeliaSim (loading `Pioneerp3dx.ttt` is optional; the server loads a robot itself)
3. `python server.py` (robot side)
4. `python client.py` (operator window, type `help`)

Switch robots from the client with `robot` (list) and `robot epuck` (switch). Available: pioneer, epuck, dr12, dr20, linebot.
`python server.py --robot dr12` starts with a given robot.

`python study.py` runs the network delay study and writes plots and a table to `results/`.

### Scenes

`scene` lists them, `scene plant` switches (also `python server.py --scene plant`). Each is built from CoppeliaSim's model library:
chemical plant (narrow corridor, fire, a worker crossing the route), warehouse (1.2 m rack aisles, worker crossing the aisles)
and disaster site (rubble, broken walls, smoke, an open drop, a victim to reach).

### Safety features

- **Heartbeat watchdog**: the robot stops itself if it hears nothing from the operator for 1.5 s.
- **Latched E-stop**: after `estop`, every drive command is ignored until `reset`.
- **Auto-stop**: the robot brakes by itself before hitting a wall or person (within 30° of its direction of travel) or driving off the floor.

`safety` shows them; `safety latch off` turns one off (for the study only).

### Studies

`python study.py` (about 12 min): the operator steers over the network at 0–1000 ms one-way delay, one run per delay.
Writes `results/results.csv`, `error_vs_delay.png`, `paths.png`.

`python ablation.py` (about 3 h): turns one safety feature off at a time, in all three scenes at 0, 300 and 1000 ms, 3 runs each.
Writes `results/ablation.csv`, `ablation.png`, `ablation_paths.png`. Restartable: finished runs are kept.

Both studies redo a run if CoppeliaSim pauses during it (the `stalls` count in the feedback), so every saved run had feedback every 0.10–0.13 s.

Main results (speeds 0.4 m/s):

| Feature off | What happened | Mechanism |
|---|---|---|
| E-stop latch | Robot drove 0.26–0.32 m after the E-stop at 300 ms and 0.78–0.89 m at 1000 ms (and hit the worker), vs 0.02–0.05 m with the latch | Drive commands already in flight undo the stop: ≈ 2 × delay × 0.4 m/s |
| Watchdog | Robot drove 7.5–9.2 m after a link cut, into a wall or off the drop, vs 0.5–1.1 m with the watchdog | With it, the robot stops 1.5 s after the last heartbeat arrives |
| Auto-stop | 0 ms: worker hit in 4 of 9 runs without it, 0 of 9 with it | Brakes before the worker when it is ahead of the robot |

Steering over the network degrades with delay: in study.py the goto took 32 s instead of 7 s at 300 ms and timed out at
500 ms and above; in the ablation every operator-driven route failed at 1000 ms (the robot weaves, then times out).
Continuous steering from feedback that is 2 × delay old becomes unstable. Sending `goto` points and letting the robot steer itself finished every route at every delay.

Limits: the operator is a scripted autopilot, not a person (a person can move-and-wait); the auto-stop only looks ahead, so a
worker walking into the robot from the side (about 51° off its heading when both keep course) is not caught — this caused the
robot-side driving hits in the warehouse; study.py has one run per delay.
