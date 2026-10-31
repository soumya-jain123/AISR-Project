# AISR Multiplayer Protocol Specification (Version 1)

This document specifies the communication protocol, message schemas, validation rules, and safety semantics for the **Networked Multiplayer Robot Racing Simulator** (Module 2).

---

## 1. Protocol Architecture & Transports

The system maintains two distinct communication layers:

1. **Legacy TCP Robot Teleoperation (`common/protocol.py`):**
   - Transports newline-delimited JSON over raw TCP stream sockets (port 55000).
   - Used by the single-operator CLI terminal client (`client/operator_client.py`).
   - Supports commands: `cmd`, `hb`, `fb`, `ack`.
2. **Multiplayer WebSocket/HTTP Protocol (`multiplayer/protocol.py`):**
   - Transports structured, versioned JSON messages over WebSocket / HTTP.
   - Used by browser clients to join rooms, stream throttle/steering inputs, receive authoritative room and robot broadcasts, and trigger safety events.
   - Enforces strict Pydantic schemas, range clamping checks, and explicit error codes.

---

## 2. Common Message Envelope

Every multiplayer message strictly requires the following base envelope:

| Field | Type | Description | Constraints |
|---|---|---|---|
| `v` | `int` | Protocol version | Must equal `1` |
| `type` | `str` | Message discriminator | Must match a supported message type |
| `seq` | `int` | Sequence number | Non-negative integer `[0, 2147483647]` |
| `ts` | `float` | Timestamp (seconds) | Positive float (`ts > 0.0`), finite number |

*Note:* All message models enforce `extra="forbid"`. Any unexpected or extra fields will result in immediate message rejection.

---

## 3. Message Categories & Schemas

### 3.1 Client -> Server Messages

#### 1. `player_input`
Real-time control input from an assigned driver's browser.
- `session_id` (`str`): Server-issued alphanumeric session ID `[a-zA-Z0-9_-]{1,64}`.
- `throttle` (`float`): Normalized forward/reverse speed in range `[-1.0, 1.0]`. Positive forward, negative reverse. Finite number only (no NaN / Infinity).
- `steering` (`float`): Normalized steering rate in range `[-1.0, 1.0]`. Positive left, negative right. Finite number only.
- `control_state` (`str`): `"active"` (key/touch held), `"neutral"` (released/coasting), or `"released"` (browser lost focus). Default: `"active"`.

*Example:*
```json
{
  "v": 1,
  "type": "player_input",
  "seq": 101,
  "ts": 1730000000.5,
  "session_id": "sess_user_alpha_123",
  "throttle": 0.75,
  "steering": -0.5,
  "control_state": "active"
}
```

#### 2. `client_heartbeat`
Periodic heartbeat sent from the browser client to keep the session watchdog fed.
- `session_id` (`str`): Active session ID.

*Example:*
```json
{
  "v": 1,
  "type": "client_heartbeat",
  "seq": 50,
  "ts": 1730000001.0,
  "session_id": "sess_driver_01"
}
```

#### 3. `individual_stop`
Normal braking request to halt only the player's assigned robot.
- `session_id` (`str`): Player session ID.
- `reason` (`str`, optional): Explanation string (max 128 chars).

#### 4. `individual_estop`
Emergency stop command targeting **only** the player's assigned robot. Immediately halts the robot and latches `ESTOP_LATCHED` for that specific robot.
- `session_id` (`str`): Player session ID.
- `reason` (`str`, optional): Explanation string (max 128 chars).

*Example:*
```json
{
  "v": 1,
  "type": "individual_estop",
  "seq": 52,
  "ts": 1730000003.0,
  "session_id": "sess_driver_01",
  "reason": "Emergency stop button pressed"
}
```

#### 5. `room_estop`
Room-wide emergency stop issued by an authorized host or referee. Halts and latches all robots in the room simultaneously.
- `session_id` (`str`): Host session ID.
- `room_id` (`str`): Target room ID.
- `reason` (`str`, optional): Reason for global stoppage.

*Example:*
```json
{
  "v": 1,
  "type": "room_estop",
  "seq": 10,
  "ts": 1730000004.0,
  "session_id": "sess_host_01",
  "room_id": "room_grand_prix",
  "reason": "Red flag on track"
}
```

#### 6. `room_reset`
Authorized request to clear latched emergency stops in a room.
- `session_id` (`str`): Host session ID.
- `room_id` (`str`): Target room ID.

#### 7. `join_room` & `leave_room`
- `join_room`: Joins room with `room_id`, `display_name` (1-32 chars alphanumeric/spaces), and `role` (`"driver"` or `"spectator"`).
- `leave_room`: Gracefully leaves a room with `session_id` and `room_id`.

---

### 3.2 Server -> Client Messages

#### 1. `command_ack`
Server confirmation or rejection of a client action.
- `ref_seq` (`int`): Sequence number of the referenced client command.
- `ok` (`bool`): `true` if accepted and executed; `false` if rejected.
- `code` (`str`, optional): Standardized rejection error code if `ok=false`.
- `message` (`str`): Human-readable status description.
- `state` (`str`, optional): Robot operating state (`"IDLE"`, `"MOVING"`, `"COMM_LOST"`, `"ESTOP_LATCHED"`).

*Example:*
```json
{
  "v": 1,
  "type": "command_ack",
  "seq": 200,
  "ts": 1730000010.0,
  "ref_seq": 101,
  "ok": true,
  "message": "Input applied",
  "state": "MOVING"
}
```

#### 2. `robot_state`
Authoritative broadcast of an individual robot's position and dynamics.
- `robot`:
  - `robot_id` (`str`): Identifier of the robot in the scene.
  - `player_id` (`str`, optional): Assigned player session ID.
  - `x`, `y` (`float`): Cartesian coordinates in meters (world frame).
  - `theta` (`float`): Yaw heading in radians.
  - `linear_vel` (`float`): Linear velocity in m/s.
  - `angular_vel` (`float`): Angular velocity in rad/s.
  - `state` (`str`): Current operating state.
  - `lap` (`int`): Completed laps.
  - `checkpoint` (`int`): Last validated checkpoint index.

*Example:*
```json
{
  "v": 1,
  "type": "robot_state",
  "seq": 201,
  "ts": 1730000010.05,
  "robot": {
    "robot_id": "robot_pioneer_01",
    "player_id": "sess_driver_01",
    "x": 1.25,
    "y": -0.85,
    "theta": 1.57,
    "linear_vel": 0.4,
    "angular_vel": 0.0,
    "state": "MOVING",
    "lap": 1,
    "checkpoint": 3
  }
}
```

#### 3. `room_broadcast`
Periodic snapshot broadcasting all robot states and race status for a given room.
- `room_id` (`str`): Target room.
- `race_state` (`str`): State of the race (`"WAITING"`, `"COUNTDOWN"`, `"RUNNING"`, `"PAUSED"`, `"FINISHED"`).
- `robots` (`list[RobotStatePayload]`): Array of robot states.

#### 4. `safety_event`
Asynchronous notification of safety interventions.
- `scope` (`str`): `"individual"` or `"room"`.
- `event` (`str`): E.g., `"WATCHDOG_TIMEOUT"`, `"ESTOP_ACTIVATED"`, `"ESTOP_RESET"`.
- `target_id` (`str`): Targeted `robot_id` or `room_id`.
- `reason` (`str`): Explanatory reason.
- `state` (`str`): New operating state.

#### 5. `error`
Protocol-level error notification for malformed requests or server faults.
- `code` (`str`): Standardized error code.
- `message` (`str`): Error message.
- `ref_seq` (`int`, optional): Client sequence number that provoked the error.

---

## 4. Error Codes & Rejection Rules

When an invalid message is received, `parse_multiplayer_message()` raises a `MultiplayerProtocolError` with one of the following codes:

| Error Code | Trigger Condition |
|---|---|
| `ERR_PAYLOAD_TOO_LARGE` | Message exceeds `MAX_PAYLOAD_BYTES` (16,384 bytes). |
| `ERR_MALFORMED_JSON` | Payload is empty, has invalid UTF-8, or cannot be parsed as JSON. |
| `ERR_MISSING_HEADER` | Missing any required envelope field (`v`, `type`, `seq`, `ts`). |
| `ERR_UNSUPPORTED_VERSION`| Header `v != 1`. |
| `ERR_UNKNOWN_TYPE` | Field `type` is not recognized. |
| `ERR_INVALID_NUMERIC` | Numeric values are `NaN`, `+Infinity`, or `-Infinity`. |
| `ERR_OUT_OF_BOUNDS` | Value violates boundaries (e.g., `throttle > 1.0` or `seq < 0`). |
| `ERR_VALIDATION_FAILED` | Unexpected extra field, invalid regex pattern, or schema mismatch. |
| `ERR_UNAUTHORIZED` | Client attempts to send server-broadcast types (e.g., forging `robot_state`). |

---

## 5. Safety Semantics

1. **Individual E-Stop vs. Room E-Stop Distinction:**
   - An individual player's E-stop (`individual_estop`) affects **only** that player's assigned robot. It cannot take a `room_id` parameter and cannot halt opponents.
   - A room-wide E-stop (`room_estop`) requires `room_id` and authorized session credentials. It latches safety stops across all room participants.
2. **Strict Latching:**
   - Once a robot enters `ESTOP_LATCHED`, any subsequent `player_input` or `individual_stop` messages are rejected at the protocol and state-machine layers until an explicit authorized reset message is accepted.
3. **Recovery After Comm Loss:**
   - Resumed heartbeats return state to `IDLE` but **never** auto-resume previously queued or held motion inputs. A fresh valid input message is strictly required.
4. **Authoritative State:**
   - Client messages can **never** dictate robot coordinates, velocities, or lap counts. Only the server publishes `robot_state` and `room_broadcast`.
