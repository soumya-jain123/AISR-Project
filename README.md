# AISR-Project
Networked Teleoperation of a Robot through Shell Commands

## Networked robot teleoperation (CoppeliaSim)

Drive a Pioneer P3-DX in CoppeliaSim with terminal commands sent over TCP, with heartbeat and emergency stop.

1. `pip install -r requirements.txt`
2. Open CoppeliaSim and load `Pioneerp3dx.ttt`
3. `python server.py` (robot side)
4. `python client.py` (operator window, type `help`)

`python study.py` runs the network delay study and writes plots and a table to `results/`.
