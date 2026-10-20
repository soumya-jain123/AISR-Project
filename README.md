# AISR-Project
Networked Teleoperation of a Robot through Shell Commands

Control a (simulated) robot with typed shell commands over TCP, with a heartbeat watchdog,
a latched emergency stop, and an experiment on how network delay affects closed-loop control.

## Architecture (Decision-Control Hierarchy)
- **Operator layer** `client.py` - human decision layer: typed commands + heartbeat thread
- **Network layer** - TCP, newline-delimited text protocol
- **Supervisory/safety layer** `server.py` - watchdog, e-stop latch, command validation
- **Execution layer** `robot_sim.py` / `coppelia_backend.py` - velocity control of the robot

## Run
    pip install -r requirements.txt
    python server.py            # terminal 1
    python client.py            # terminal 2   (type: fwd, left, status, estop, reset, rtt, quit)
    python test_system.py       # automated test
    python experiment_delay.py  # delay study -> delay_results.csv, delay_plot.png

CoppeliaSim: see header of `coppelia_backend.py`, then `python server.py --backend coppelia`.

## Protocol
PING, HB, MOVE v w, STOP, ESTOP, RESET, STATUS

## Branch: Tia
Initial implementation: TCP server/client, heartbeat, e-stop, delay experiment.
