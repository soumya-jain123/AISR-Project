# AISR MQTT Teleoperation Architecture & User Guide

This document describes the MQTT transport implementation for the AISR robot teleoperation project, detailing broker configuration, topic hierarchies, JSON schemas, safety policies, transport mutual exclusion, and operating instructions.

---

## 1. System Architecture

The MQTT transport introduces a decoupled pub/sub communication layer while reusing the core safety controller, robot abstractions, and JSON protocol.

```
+-------------------------------------------------------------+
|                   MQTT Client (Operator)                    |
|  - Interactive REPL CLI (`src.mqtt_client`)                 |
|  - Background heartbeat publisher (0.5s period)             |
|  - Message timestamping (`ts`) for staleness protection     |
|  - Subscribed to: `aisr/robot/response`, `aisr/robot/status`|
+------------------------------+------------------------------+
                               |
                       MQTT (TCP/1883)
                               |
+------------------------------v------------------------------+
|                        MQTT Broker                          |
|  - Standard MQTT 3.1.1 / 5.0 broker (e.g. Eclipse Mosquitto)|
|  - Ephemeral clean sessions (no retained queue buildup)     |
+------------------------------+------------------------------+
                               |
                       MQTT (TCP/1883)
                               |
+------------------------------v------------------------------+
|                    MQTTRobotServer                          |
|  - Subscribed to: `aisr/robot/command`, `aisr/robot/heartbeat`
|  - Retained message rejection filter                        |
|  - Command staleness timestamp check (< 2.0s)               |
|  - LWT: publishes `offline` on unexpected disconnect         |
+------------------------------+------------------------------+
                               |
+------------------------------v------------------------------+
|                     SafetyController                        |
|  - Transport ownership mutual exclusion (TCP vs MQTT)       |
|  - State machine: IDLE, MOVING, COMM_LOST, ESTOP_LATCHED   |
|  - 1.5s Heartbeat watchdog thread                           |
|  - Universal ESTOP acceptance from any transport            |
+------------------------------+------------------------------+
                               |
+------------------------------v------------------------------+
|                RobotInterface Backend Layer                 |
|  - `MockRobot`: In-memory simulated test double             |
|  - `CoppeliaSimRobot`: Real-time ZeroMQ Remote API Pioneer  |
+-------------------------------------------------------------+
```

---

## 2. Broker Requirements & Setup

The system works with any standard MQTT broker compliant with MQTT 3.1.1 or 5.0.

### Default Connection Parameters
- **Host:** `localhost`
- **Port:** `1883`
- **Keepalive:** `60` seconds
- **Clean Session:** `True` (prevents replay of stale messages queued during offline periods)

### Running Eclipse Mosquitto Locally

#### Using Docker (Recommended for cross-platform):
```bash
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:latest
```

#### Windows Native:
If Mosquitto is installed on Windows:
```powershell
mosquitto -v
```

---

## 3. Topic Hierarchy

All topics are prefixed by `<topic_prefix>` (default: `aisr/robot`):

| Topic | Publisher | Subscriber | QoS | Retained? | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `aisr/robot/command` | Operator Client | Robot Server | 1 or 0 | **False** | Operator movement and control commands |
| `aisr/robot/heartbeat` | Operator Client | Robot Server | 0 | **False** | Periodic keepalive heartbeats (0.5s interval) |
| `aisr/robot/response` | Robot Server | Operator Client | 1 or 0 | **False** | Acknowledgements, command results, error messages |
| `aisr/robot/status` | Robot Server | Operator Client / Telemetry | 0 | **False** | Periodic/on-demand robot state, pose, velocity |
| `aisr/robot/connection` | Server / Client | Watchdogs / Monitors | 1 | **True** (LWT) | Connection lifecycle events (`online`, `offline`) |

---

## 4. Message Payloads and JSON Schemas

All message bodies are UTF-8 encoded JSON objects formatted according to `src.protocol`.

### 4.1. Command Message (`aisr/robot/command`)

Published by operator client to request movement or state changes.

```json
{
  "type": "FORWARD",
  "speed": 0.2,
  "duration": 2.0,
  "ts": 1729501234.567
}
```

- `type` (string, required): Command type (`FORWARD`, `BACKWARD`, `TURN_LEFT`, `TURN_RIGHT`, `MOVE`, `GOTO`, `STOP`, `ESTOP`, `RESET`, `STATUS`, `PING`).
- `ts` (float, recommended): Unix timestamp when command was sent. Used by the server to reject stale or delayed messages.
- Command-specific fields: `speed`, `angular_speed`, `duration`, `x`, `y`, `heading`.

### 4.2. Heartbeat Message (`aisr/robot/heartbeat`)

Published periodically (every 0.5s) by the active operator client.

```json
{
  "type": "HEARTBEAT",
  "ts": 1729501234.567
}
```

### 4.3. Response Message (`aisr/robot/response`)

Published by the robot server in response to every received command.

#### Successful Execution:
```json
{
  "type": "ACK",
  "received_type": "FORWARD",
  "status": "success",
  "message": "Moving forward at 0.2 m/s for 2.0 s"
}
```

#### Rejection / Error:
```json
{
  "type": "ACK",
  "received_type": "FORWARD",
  "status": "stale_command",
  "error": "Command timestamp is 3.42s old (max allowed 2.0s); rejected for safety"
}
```

Common status codes:
- `success`: Command validated and executed.
- `error`: Invalid parameters, unhandled exception, or invalid state.
- `ownership_denied`: Another transport currently controls the robot.
- `stale_command`: Command exceeded `max_command_age_sec`.
- `retained_rejected`: Message arrived with MQTT `retain=True` flag.

### 4.4. Status Telemetry (`aisr/robot/status`)

Published after commands or queried via `STATUS`.

```json
{
  "type": "STATUS_RESPONSE",
  "status": "success",
  "state": "MOVING",
  "active_transport": "MQTT",
  "pose": {
    "x": 0.125,
    "y": -0.045,
    "heading": 0.012
  },
  "linear_velocity": 0.2,
  "angular_velocity": 0.0,
  "heartbeat_age_sec": 0.24,
  "watchdog_alive": true
}
```

### 4.5. Last Will and Testament (LWT) (`aisr/robot/connection`)

Configured when the server connects to the broker:
- **LWT Payload (unexpected disconnect):**
  ```json
  {"status": "offline", "transport": "MQTT", "reason": "unexpected_disconnect"}
  ```
- **Birth Payload (on connect):**
  ```json
  {"status": "online", "transport": "MQTT"}
  ```

---

## 5. Teleoperation Safety Mechanisms

### 5.1. Stale Queued Message Prevention
In lossy or delayed networks, MQTT brokers may queue messages that arrive long after conditions on the ground have changed. To guarantee safety:
1. **Clean Session Enforcement:** Clients connect with `clean_session=True` so offline queues are discarded upon reconnection.
2. **Retained Message Rejection:** The robot server inspects `msg.retain`. If `True`, the message is rejected with status `retained_rejected` and discarded.
3. **Command Staleness Check:** Commands include a `ts` timestamp. The server computes `age = now - ts`. If `age > max_command_age_sec` (default: 2.0 seconds), the command is rejected with `stale_command` and the robot is not moved. (Exception: `ESTOP` is always executed immediately regardless of age).

### 5.2. Transport Mutual Exclusion Policy
To prevent conflicting simultaneous control from TCP and MQTT operators:
- `SafetyController` tracks an `active_transport` identifier (`"TCP"`, `"MQTT"`, or `None`).
- **Acquiring Ownership:** The first transport to issue a movement command or explicitly connect acquires ownership.
- **Ownership Denial:** If Transport A owns the controller, movement commands from Transport B receive:
  ```json
  {"type": "ACK", "status": "ownership_denied", "error": "Robot controlled by TCP; MQTT command rejected"}
  ```
- **Universal ESTOP:** `ESTOP` is permitted from **any** transport at any time, immediately halting the robot and latching `ESTOP_LATCHED`.
- **Releasing Ownership:** When a transport disconnects cleanly, ownership is released (`active_transport = None`), setting state to `COMM_LOST` and stopping all motion.

### 5.3. Application-Level Heartbeat Watchdog
- MQTT broker-level TCP ping/pong only verifies broker reachability, not operator application health.
- `MQTTRobotClient` runs a background thread publishing `{"type": "HEARTBEAT"}` every 0.5s.
- `MQTTRobotServer` feeds received heartbeats to `SafetyController.record_heartbeat("MQTT")`.
- If no heartbeat is received for > 1.5s, the independent watchdog thread halts the robot (`stop()`) and transitions the state to `COMM_LOST`.
- **Heartbeat Recovery:** Receiving subsequent heartbeats restores `IDLE`, but **never** resumes movement automatically.

### 5.4. Emergency Stop & Reset Safety
- `ESTOP` forces the robot into `ESTOP_LATCHED` and immediately cuts motor velocities to `0.0`.
- All subsequent movement commands are rejected with `"state": "ESTOP_LATCHED"`.
- `RESET` unlatches the state to `IDLE` with motors stopped. A new, explicit movement command is required to move again.

---

## 6. How to Run

### 6.1. Start the Robot Server with MQTT

#### Terminal 1: Run with Mock Robot Backend (no simulator needed)
```powershell
python -m src.mqtt_server --backend mock --broker-host localhost --broker-port 1883
```

#### Terminal 1 (Alternative): Run with CoppeliaSim Pioneer P3-DX
Ensure CoppeliaSim has `scenes/aisr_teleoperation.ttt` loaded:
```powershell
python -m src.mqtt_server --backend coppeliasim --broker-host localhost --broker-port 1883
```

Server CLI Arguments:
- `--backend`: `mock` (default) or `coppeliasim`.
- `--broker-host`: MQTT broker address (default: `localhost`).
- `--broker-port`: MQTT broker port (default: `1883`).
- `--topic-prefix`: Root MQTT topic (default: `aisr/robot`).

### 6.2. Start the Interactive Operator Client

#### Terminal 2: Run Operator REPL
```powershell
python -m src.mqtt_client --broker-host localhost --broker-port 1883
```

Once connected, the operator prompt will appear:
```
Connected to MQTT broker at localhost:1883.
Topic prefix: aisr/robot
Type 'HELP' for available commands or 'QUIT' to exit.

AISR-MQTT> STATUS
[STATUS] State: IDLE | Pose: x=0.000, y=0.000, th=0.000 | Speed: lin=0.000, ang=0.000 | Active Transport: MQTT

AISR-MQTT> FORWARD 0.2 2
[RESPONSE] Status: success | Moving forward at 0.2 m/s for 2.0 s

AISR-MQTT> ESTOP
[RESPONSE] Status: success | Emergency stop engaged
```

Supported Commands:
- `FORWARD [speed] [duration]`
- `BACKWARD [speed] [duration]`
- `TURN_LEFT [angular_speed] [duration]`
- `TURN_RIGHT [angular_speed] [duration]`
- `MOVE <linear_speed> <angular_speed> [duration]`
- `GOTO <x> <y> [heading]`
- `STOP`
- `STATUS`
- `PING`
- `ESTOP`
- `RESET`
- `HELP`
- `QUIT`

---

## 7. Running Automated Tests

The MQTT implementation includes 19 comprehensive unit tests using a mock MQTT broker test double. No external broker or physical robot is required.

Run the entire test suite:
```powershell
python -m unittest discover -s tests -v
```

Run only the MQTT test suite:
```powershell
python -m unittest tests/test_mqtt.py -v
```

---

## 8. Known Limitations & Network Considerations

1. **Broker as Single Point of Failure:** In standard deployments, the MQTT broker represents a single failure domain. For production safety, high-availability clustering or dual-homed failover brokers should be used.
2. **Latency & Jitter on Open-Loop Timed Commands:** Network jitter can introduce delay between when an operator initiates a timed command and when the server executes it. To mitigate this:
   - Server-side execution timers are used so the robot stops after the requested duration relative to command receipt.
   - Command timestamps reject commands delayed longer than `max_command_age_sec`.
3. **QoS Trade-Offs:**
   - Command and response messages use QoS 1 (at least once delivery) to prevent dropped commands under intermittent network links.
   - Heartbeats and high-frequency status telemetry use QoS 0 to minimize latency and prevent head-of-line blocking.

