# AISR-Project

## Networked Teleoperation of a Robot through Shell Commands

Control a simulated **Pioneer P3-DX robot** in CoppeliaSim using terminal commands sent over TCP or MQTT. The system includes heartbeat monitoring, an emergency stop, transport mutual exclusion, and network-delay experiments.

## Architecture

- **Operator layer:** `src/tcp_client.py` (TCP) and `src/mqtt_client.py` (MQTT) — send commands from the terminal.
- **Network layer:** Dual transport support:
  - Stream-based JSON-over-newline over TCP.
  - Pub/Sub JSON telemetry and command topics over MQTT.
- **Safety layer:** `src/safety.py` (`SafetyController`) — command validation, heartbeat watchdog, transport ownership mutual exclusion, and emergency-stop handling.
- **Execution layer:** CoppeliaSim backend (`src/robot_interface.py`) for controlling the simulated Pioneer P3-DX robot, or `MockRobot` for testing.
- **Experiment layer:** Scripts for measuring network delay and robot movement.

## Run

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Open CoppeliaSim and load the project scene (optional if using `mock` backend):

`scenes/aisr_teleoperation.ttt`

### Option A: Run via TCP

Start the TCP server in Terminal 1:

```powershell
python -m src.tcp_server --backend coppeliasim
```

Start the TCP client in Terminal 2:

```powershell
python -m src.tcp_client
```

### Option B: Run via MQTT

Ensure an MQTT broker is running (e.g. `mosquitto -v` or via Docker).

Start the MQTT robot server in Terminal 1:

```powershell
python -m src.mqtt_server --backend coppeliasim --broker-host localhost --broker-port 1883
```

Start the MQTT operator client in Terminal 2:

```powershell
python -m src.mqtt_client --broker-host localhost --broker-port 1883
```

See `docs/mqtt_teleoperation.md` for full MQTT topic hierarchy, schemas, and safety mechanisms.

Use the available shell commands to control the robot and check its status.

## Safety Features

- Heartbeat monitoring and communication timeout detection.
- Automatic stopping when communication is lost.
- Emergency stop (E-stop) with a latched safety state.
- Reset handling that does not automatically resume movement.
- Transport ownership policy preventing conflicting simultaneous control between TCP and MQTT.
- Stale and retained command rejection over MQTT for teleoperation safety.

## Testing

Run the automated test suite:

```powershell
python -m unittest discover -s tests -v
```

## Network Delay Experiments

Run the experiment runner:

```powershell
python scripts/run_experiments.py --trials 3
```

Generate plots from recorded experiment data:

```powershell
python scripts/plot_results.py
```

See `docs/experiments_and_benchmarks.md` for detailed experiment instructions.

## Documentation

- `docs/commands_and_safety.md`: Command syntax, state machine, and safety watchdog design.
- `docs/coppeliasim_integration.md`: CoppeliaSim Pioneer P3-DX setup and remote API interface.
- `docs/mqtt_teleoperation.md`: MQTT architecture, broker setup, topic hierarchy, and safety policies.
- `docs/experiments_and_benchmarks.md`: Automated benchmarking methodology and latency analysis.

## Project Repository

GitHub: https://github.com/soumya-jain123/AISR-Project
