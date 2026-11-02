# Module 7: Real-Time Input and State Synchronization

This document specifies the real-time control input dispatch, safety state machine, watchdog timeout, emergency stop system, and authoritative state synchronization implemented for the AISR Multiplayer Robot Racing Simulator.

---

## 1. Overview & Architecture

Module 7 establishes an authoritative, closed-loop telemetry and control pipeline between connected browser pilot sessions, the FastAPI WebSocket server, and the Module 3 multi-robot simulation backend (`MockSimulationBackend` or `CoppeliaSimMultiRobotBackend`).

```
 +-------------------------------------------------------------------------+
 |                          BROWSER CLIENTS                                |
 |  [Pilot A - WASD]                      [Pilot B - Arrow Keys]           |
 +---------+-----------------------------------------+---------------------+
           | `player_input` (30 Hz rate-limited)     | `player_input`
           v                                         v
 +-------------------------------------------------------------------------+
 |                 FASTAPI WEBSOCKET / CONNECTION MANAGER                  |
 |  - Per-connection sequence validation & monotonic tracking              |
 |  - Session authentication & room membership verification                |
 +-----------------------------------+-------------------------------------+
                                     |
                                     v
 +-------------------------------------------------------------------------+
 |                         CONTROL SERVICE                                 |
 |  - Resolves session -> Room -> Assigned robot                           |
 |  - Enforces single-pilot control authority                              |
 |  - Evaluates per-robot safety state & watchdog timers                   |
 |  - Dispatches non-blocking velocity commands to Simulation Backend      |
 +-----------------------------------+-------------------------------------+
                                     |
                                     v
 +-------------------------------------------------------------------------+
 |                       SIMULATION BACKEND                                |
 |  - Advances multi-robot kinematics (30 Hz physics loop)                 |
 |  - RLock concurrency protection across threads                          |
 +-----------------------------------+-------------------------------------+
                                     |
                                     v
 +-------------------------------------------------------------------------+
 |                       STATE BROADCASTER                                 |
 |  - Periodic 20 Hz state synchronization                                 |
 |  - Per-robot watchdog expiration checks (1.0s timeout -> COMM_LOST)     |
 |  - Publishes room-scoped `room_broadcast` frames to room members        |
 +-----------------------------------+-------------------------------------+
                                     |
                                     v (Broadcast 20 Hz)
 +-------------------------------------------------------------------------+
 |                   BROWSER 3D ARENA & COCKPIT HUD                        |
 |  - Three.js lerp position & shortest-path yaw interpolation             |
 |  - Throttle / steering gauges, velocity readout, safety banners         |
 +-------------------------------------------------------------------------+
```

---

## 2. Control Pipeline & Safety State Machine

Each robot registered in the simulation operates under a strict server-authoritative safety state machine:

| Operating State | Description | Actuation Allowed? | Transitions |
|---|---|:---:|---|
| `IDLE` | Robot is powered on, initialized at zero velocity. | Yes | Commands -> `MOVING`, Watchdog -> `COMM_LOST`, E-stop -> `ESTOP_LATCHED` |
| `MOVING` | Robot is receiving valid steering/throttle inputs. | Yes | Velocity=0 -> `IDLE`, Watchdog -> `COMM_LOST`, E-stop -> `ESTOP_LATCHED` |
| `COMM_LOST` | Robot watchdog timer expired (> 1.0s without input). Zero velocity forced. | Auto-recovers on valid input | Valid input -> `MOVING`/`IDLE`, E-stop -> `ESTOP_LATCHED` |
| `ESTOP_LATCHED` | Emergency stop triggered (individual or room-wide). Halted immediately. | **NO (Latched)** | Server or host reset -> `IDLE` |

### Key Guarantees:
1. **Single-Pilot Exclusivity:** Only the session assigned to a robot can drive that robot. Spectators or players assigned to other robots are rejected with `ERR_UNAUTHORIZED`.
2. **Room Boundary Isolation:** Input for an unassigned session or a session not currently in a room is rejected with `ERR_NOT_IN_ROOM`.
3. **Forged Session Prevention:** If the envelope `session_id` does not match the authenticated WebSocket connection, the message is dropped immediately.

---

## 3. Rate Limiting & Input Validation

Control inputs are bounded to prevent denial-of-service and network congestion:

- **Client-Side Throttling:** `MultiplayerClient` enforces a maximum transmission rate of 30 Hz (33.3ms minimum interval) for active driving updates.
- **Server-Side Enforcement:** `ControlService` monitors monotonic timestamps and drops continuous non-neutral inputs exceeding `control_rate_limit_hz` with `ERR_OUT_OF_BOUNDS`.
- **Safety Neutral Release:** Commands setting throttle and steering to neutral/zero (`control_state: "neutral"` or `"released"`) bypass the rate limiter, ensuring immediate halting even under rapid key transitions.
- **Normalized Bounds:** Throttle and steering inputs outside `[-1.0, 1.0]` are clamped by the client and validated by the backend.

---

## 4. Emergency Stop System

The system provides dual-tier emergency stop capabilities:

### Individual E-Stop (`individual_estop`)
- Issued by a driver via the cockpit HUD or the `E` key shortcut.
- Latches **only** the sender's assigned robot into `ESTOP_LATCHED`.
- Halts motors immediately ($v = 0, \omega = 0$).
- Broadcasts a `SafetyEventMessage` (`scope: "individual"`, `state: "ESTOP_LATCHED"`) to the room.
- Further driving inputs are rejected with `ERR_ESTOP_ACTIVE`.
- **Client control commands cannot clear a latched E-stop.**

### Room-Wide E-Stop (`room_estop`) & Reset (`room_reset`)
- Issued by any driver or room owner.
- Halts and latches **all** robots participating in the room.
- Broadcasts `SafetyEventMessage` (`scope: "room"`, `state: "ESTOP_LATCHED"`).
- **Authorized Room Reset:** Only the room host (`owner_id`) can issue `room_reset`. Requests from non-hosts are rejected with `ERR_UNAUTHORIZED`.
- Upon host reset, all robots in the room return to `IDLE` and motors are unlatched.

---

## 5. Authoritative State Synchronization

State broadcasting is completely decoupled from incoming client inputs:

- **Physics Simulation Loop:** Runs at 30 Hz (`sim_tick_rate_hz`), updating position and kinematics based on active velocities and elapsed time ($\Delta t$).
- **State Broadcaster Loop:** Runs at 20 Hz (`state_broadcast_rate_hz`), assembling `RoomBroadcastMessage` frames containing the authoritative $(x, y, \theta, v, \omega, \text{state})$ of all robots in that specific room.
- **Zero Leakage:** Telemetry frames are delivered strictly to active members of the room.
- **Watchdog Supervisor:** Every broadcast cycle checks elapsed time since each moving robot's last control update. If $> 1.0\text{s}$, the robot is transitioned to `COMM_LOST`, brought to a dead stop, and a safety event is emitted.

---

## 6. Frontend Driving Cockpit & 3D Visualization

The browser interface embeds an interactive pilot cockpit within `RoomView.tsx`:

- **Controls:**
  - `W` / `Up Arrow`: Forward throttle (+1.0)
  - `S` / `Down Arrow`: Reverse throttle (-1.0)
  - `A` / `Left Arrow`: Steer left (+1.0)
  - `D` / `Right Arrow`: Steer right (-1.0)
  - `Space`: Brake pedal (instant stop)
  - `E`: Emergency Stop latch
- **Key Safety Features:**
  - Window blur and component unmount events automatically dispatch a neutral release command (`throttle: 0, steering: 0, control_state: "released"`).
  - Arrow key and spacebar scrolling is suppressed when focused on the cockpit.
- **Cockpit HUD:**
  - Dual vertical bar meters for Throttle and Steering.
  - Linear velocity and heading readout.
  - Operating state badge (`IDLE`, `MOVING`, `COMM_LOST`, `ESTOP_LATCHED`).
  - Emergency banner and interactive Brake / E-Stop buttons.
- **Smooth 3D Interpolation:**
  - `Arena3D.tsx` maintains authoritative pose targets and interpolates robot meshes at 60 FPS using linear interpolation for position and shortest-path angular difference for yaw:
    $$\Delta\theta = (\theta_{\text{target}} - \theta_{\text{current}} + \pi) \pmod{2\pi} - \pi$$

---

## 7. Verification & Test Suite

### Python Backend Tests
Run the test suite in the project virtual environment:
```powershell
.\.venv\Scripts\pytest -v tests/test_multiplayer_control.py
```
Covers:
- `test_assigned_robot_control_actuates_only_designated_robot`: Verified driver acts only on assigned robot.
- `test_reject_control_from_unassigned_or_spectator_session`: Spectators and unassigned sessions blocked.
- `test_forged_session_cannot_actuate_robot`: Cross-session forgery attempts rejected.
- `test_neutral_and_release_inputs_halt_robot`: Neutral release halts moving robot.
- `test_per_robot_watchdog_timeout_halts_only_affected_robot`: Watchdog timer expirations halt only affected robot without disturbing other drivers.
- `test_disconnect_and_leave_room_halt_assigned_robot`: Disconnections safely halt and reset robot state.
- `test_individual_estop_latching_and_server_reset`: E-stop latching and server-side reset behavior.
- `test_room_wide_estop_and_host_only_reset`: Global room E-stop and host-only reset authority.
- `test_room_state_broadcast_scoped_to_room_members`: Zero-leakage room broadcast verification.
- `test_multi_robot_independent_movement_in_mock`: Multi-robot concurrent kinematic independence.

### Frontend Unit Tests & Build
```powershell
cd frontend
npm test -- --run
npm run build
```
- 21/21 Vitest tests passing across protocol validation, driving rate limiting, safety events, and UI components.
- Clean TypeScript production build (`tsc -b && vite build`) with zero errors.
