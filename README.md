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
