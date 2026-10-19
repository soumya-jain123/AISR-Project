# Implementation Plan — Networked Robot Teleoperation via Shell Commands

Source: `DOCS/Networked Robot Teleoperation — Project Overview.pdf` and `DOCS/AISR_Project_Instructions_B1-B2.pdf`.
**Deadline: 29 Oct 2026** (late = −10%/day). Today: 19 Oct → ~10 days.

Each module below has a clear **owner-able scope**, **inputs/outputs (interface)**, **tasks**, and **Definition of Done (DoD)**. Work in order M0 → M1 → … ; modules M2, M3, M4 can run in parallel once M1 (protocol) is frozen.

---

## Target Repo Structure

```
AISR-Project/
├── common/
│   ├── protocol.py        # M1  message schema, encode/decode, framing
│   ├── netdelay.py        # M1  artificial delay (hold message N ms)
│   └── config.py          # M0  ports, timeouts, delays
├── server/
│   ├── robot_server.py    # M2  TCP server, dispatcher, main loop
│   ├── state_machine.py   # M2  IDLE/MOVING/COMM_LOST/ESTOP_LATCHED
│   ├── watchdog.py        # M2  heartbeat watchdog
│   └── sim_driver.py      # M3  CoppeliaSim ZMQ wrapper (P3-DX)
├── client/
│   ├── operator_client.py # M4  terminal UI, command parser
│   ├── heartbeat.py       # M4  0.5 s heartbeat thread
│   └── logger.py          # M4  timing/CSV logging
├── experiments/
│   ├── run_experiments.py # M5  automated delay sweeps
│   ├── analyze.py         # M5  plots + tables
│   └── results/           # CSVs, PNGs
├── scenes/teleop.ttt      # M3  CoppeliaSim scene
├── tests/                 # per-module tests
├── report/ slides/ video/ # M6
├── requirements.txt
└── README.md
```

## Fixed Design Constants (from the overview — do not change)

| Item | Value |
|---|---|
| Robot / simulator | Pioneer P3-DX in CoppeliaSim, via ZMQ Remote API (Python) |
| Transport | TCP, localhost, two separate processes |
| Heartbeat | client → server every **0.5 s**; server timeout **1.5 s** |
| States | `IDLE`, `MOVING`, `COMM_LOST`, `ESTOP_LATCHED` (E-stop highest priority) |
| Commands | `forward <v>`, `back <v>`, `left <deg>`, `right <deg>`, `stop`, `goto <x> <y>`, `status`, `estop`, `reset` |
| Speeds | realistic 0.3–0.5 m/s (max ~1.2 m/s) |
| Delay values | 0, 100, 300, 500, 1000 ms (held at sender/receiver before use) |
| Tasks | drive to target point; follow square path |
| Metrics | final position error, overshoot, oscillation, completion time, E-stop reaction time, false heartbeat stops |
| Every message | carries a timestamp; receiver computes latency |

---

## M0 — Project Setup & Repo (Day 1, whole team)

**Tasks**
- Install CoppeliaSim (EDU), `pip install coppeliasim-zmqremoteapi-client`; verify with a hello-world that moves P3-DX.
- Create venv, `requirements.txt`, folder skeleton above, `.gitignore` (`__pycache__`, `.venv`).
- Each member commits from **own GitHub account** (graded: B1 traceability). Use feature branches + PRs.
- Agree team lead, module owners, start the **meeting log** (date, duration, attendees, decisions) — needed in Appendix.
- Create `common/config.py` with all constants above.

**DoD:** everyone can clone, run a script that makes the robot move in the sim.

---

## M1 — Protocol & Network Layer (`common/`)

**Goal:** a frozen message contract everybody codes against. *Do this first (Day 1–2).*

**Message format** — newline-delimited JSON over TCP (simple, debuggable):
```json
{"type":"cmd","seq":12,"ts":1729339200.123,"name":"forward","args":[0.5]}
{"type":"hb","seq":88,"ts":...}
{"type":"fb","seq":5,"ts":...,"x":1.2,"y":0.3,"theta":0.1,"state":"MOVING"}
{"type":"ack","seq":12,"ts":...,"ok":true,"msg":"..."}
```

**Tasks**
1. `protocol.py`: `encode(msg)->bytes`, `decode(line)->dict`, schema validation, `make_cmd/hb/fb/ack`.
2. Line-framed `recv` helper (handle partial TCP reads, `TCP_NODELAY`).
3. `netdelay.py`: `DelayedQueue(delay_ms)` — holds each message until `ts_recv + delay`, then releases (exact, OS-independent, as required). Delay applied in both directions.
4. Latency calc helper: `now() - msg["ts"]`.
5. Unit tests: round-trip, fragmented/merged packets, delay accuracy (±10 ms).

**DoD:** `pytest tests/test_protocol.py` green; interface documented in `docs/protocol.md`.

---

## M2 — Robot Server Core: State Machine, Heartbeat, E-stop (`server/`)

**Interface:** consumes protocol messages; calls an abstract `RobotDriver` (implemented in M3). Develop against a **`MockDriver`** so M2 doesn't block on CoppeliaSim.

**Tasks**
1. `robot_server.py`: TCP accept loop, receive thread → priority dispatcher, send feedback thread (e.g. 10 Hz `fb`).
2. **Priority handling:** `estop` is processed *ahead of* queued commands (use `PriorityQueue` / bypass queue).
3. `state_machine.py` transitions:
   - `IDLE ⇄ MOVING` on move commands / completion / `stop`
   - any → `COMM_LOST` on watchdog timeout (robot stopped)
   - `COMM_LOST` → `IDLE` when heartbeats return; **never auto-resume old command**; accepts movement only after a *new* command
   - any → `ESTOP_LATCHED` on `estop` (stop + lock, ignore all movement, server & connection stay alive, feedback continues)
   - `ESTOP_LATCHED` → `IDLE` only via `reset`; still needs new command to move
4. `watchdog.py`: thread tracking last heartbeat time; trigger `COMM_LOST` at 1.5 s.
5. Reject movement commands in `COMM_LOST`/`ESTOP_LATCHED` with an `ack ok=false`.
6. Log every command/state change with timestamps (for E-stop reaction time).
7. Tests: simulate missed heartbeats, estop-then-delayed-move (must be ignored), reset flow, resume-after-comm-loss.

**DoD:** all state-table rows from the overview pass automated tests with `MockDriver`.

---

## M3 — CoppeliaSim Scene & Robot Driver (`server/sim_driver.py`, `scenes/`)

**Tasks**
1. Build scene `teleop.ttt`: floor, Pioneer P3-DX, a target marker, 4 corner markers for the square path.
2. `sim_driver.py` implementing:
   ```python
   class RobotDriver:
       def set_wheel_speeds(l, r)
       def drive(v)                 # forward/back, m/s
       def turn(angle_deg)          # in-place turn, using heading feedback
       def stop()
       def get_pose() -> (x, y, theta)
       def goto(x, y)               # closed-loop P controller (distance + heading)
   ```
3. Kinematics: differential drive `vl = v − ωL/2`, `vr = v + ωL/2`; read wheel radius/axle from model.
4. `goto` controller: proportional on distance & heading error, speed clamp ≤0.5 m/s, stop tolerance (e.g. 0.05 m). Runs as a non-blocking step loop so `estop/stop` can interrupt instantly.
5. Run sim in **stepping mode** (or fixed-rate) to keep results repeatable; note time-step in docs.
6. Square-path helper (sequence of `goto`s / forward+turn).
7. `MockDriver` (shared with M2) for tests.

**DoD:** standalone script drives the robot forward, turns, goes to (x, y), stops on demand; pose reading accurate.

---

## M4 — Operator Client & Terminal UI (`client/`)

**Tasks**
1. `operator_client.py`: connect to server, interactive prompt (`>`), parse & validate commands (arg count, numeric ranges, speed ≤ limit), `help`.
2. `heartbeat.py`: background thread sends `hb` every 0.5 s, independent of typed input.
3. Receive thread: display feedback/status live (position, state, latency) without corrupting the input prompt.
4. Apply `netdelay` to outgoing & incoming messages (configurable via CLI flag `--delay 300`).
5. `logger.py`: CSV log of every sent/received message: `seq, type, t_sent, t_recv, latency, pose, state`. Needed by M5.
6. Scriptable mode: `--script commands.txt` / programmatic API so M5 can drive experiments without a human.
7. Graceful handling of server disconnect/reconnect.

**DoD:** operator can type the full command set against the M2 server (mock or real) and see feedback; logs written.

---

## M5 — Integration & Network-Delay Experiments (`experiments/`)

**Prereq:** M1–M4 integrated end-to-end.

**Tasks**
1. Integration test: full loop with real CoppeliaSim; fix issues; confirm heartbeat stop (kill client heartbeat) and E-stop work live.
2. `run_experiments.py`: for each delay ∈ {0, 100, 300, 500, 1000} ms, **repeat ≥3 runs** each of:
   - **Task A:** drive to target point (operator-in-the-loop emulated by a scripted "operator" that acts on *delayed* feedback)
   - **Task B:** follow square path
   - **E-stop test:** issue `estop` mid-motion, measure time until robot velocity = 0
   - **Heartbeat test:** count false `COMM_LOST` stops at each delay (esp. 1000 ms)
3. Record metrics: final position error, overshoot, oscillation (count of sign changes / std of error after arrival), completion time, E-stop reaction time, false heartbeat stops.
4. `analyze.py` outputs:
   - Graph: **delay vs position error** (also vs overshoot, completion, E-stop time)
   - **Path plots** (x–y) for each delay, overlaid
   - **Results table** (mean ± std)
5. Short discussion: expected trend — higher delay ⇒ more overshoot, wobble, slower, slower E-stop; note when heartbeat gives false stops (1000 ms vs 1.5 s timeout).

**DoD:** `results/` has CSVs, ≥3 figures, 1 table; scripts reproducible with one command.

---

## M6 — Report, Slides, Video, Submission (Days 7–10)

**Report (25–35 pages, Word/Overleaf sample format — border, TNR 12pt 1.5 spacing, A4, margin L3/R,T,B 2.5 cm)**
Order: Title, Declaration, Index, Lists of Figures/Tables, Writing Allocation, Abstract (150–200 w), then sections 1–13 (Intro, Problem & Objectives, Lit Review ≥5 sources, **Novelty** (mandatory), Syllabus Mapping, System Design, Methodology, Simulation Setup, Results, **Contribution** (mandatory), Co-learning reflection, Conclusion, References ≥8 IEEE), Appendix (GitHub link, YouTube link, meeting log, peer-assessment).

**Suggested novelty points (back with M5 data):** (a) TCP teleop with dual safety layer (heartbeat + latched E-stop); (b) exact repeatable delay injection; (c) quantified delay→control-quality relation incl. E-stop latency; (d) false heartbeat-stop analysis vs timeout choice.

**Slides:** ≤17 slides (suggested plan in the instructions PDF), fonts ≥20 pt, own screenshots, presenter name per slide.
**Video:** 20 min (18–22), whole online meeting, all cameras on, intro with name + SAP ID, ~14 min slides + ~6 min live sim demo; upload **Unlisted** on YouTube.
**Submit:** printed bound report (mandatory), report PDF, slides PDF, GitHub link, video link via Google Form (team lead only).

---

## Suggested Timeline

| Day | Date | Work |
|---|---|---|
| 1 | Oct 19–20 | M0 setup; **M1 protocol frozen** |
| 2–4 | Oct 21–23 | M2, M3, M4 in parallel (MockDriver decouples them) |
| 5 | Oct 24 | Integration; bug fixes |
| 6–7 | Oct 25–26 | M5 experiments & plots |
| 6–9 | Oct 25–28 | M6 report writing (split equally), slides |
| 9 | Oct 28 | Record video; print report |
| 10 | Oct 29 | Submit (buffer: submit by morning) |

## Suggested Team Split (adjust to 4 or 5 members)

| Member | Code module | Report sections (example) |
|---|---|---|
| M1 | M1 Protocol/Net + M0 | Abstract, 1, 2, 13 |
| M2 | M2 Server/State machine | 3, 4, 5 (state/heartbeat theory) |
| M3 | M3 CoppeliaSim/Controller | 6, 8 |
| M4 | M4 Client/Logger | 7, 12 |
| M5 | M5 Experiments/Analysis | 9 + help with slides |
| All | — | 10 (own row), 11, Declaration |

## Syllabus Mapping to Document (confirm against your AISR syllabus)
Differential-drive kinematics (M3), feedback/closed-loop control & P-controller (M3), delay effects/stability (M5), safety/fault-tolerance (M2), teleoperation architecture (design section).

## Risks
- **ZMQ API version mismatch** → pin versions in `requirements.txt`, test in M0.
- **Sim timing non-determinism** → use stepping mode / fixed step.
- **Console I/O mixing with feedback** → use separate threads + clean prompt redraw.
- **Time** → integration by Oct 24 is the critical gate; freeze features after.
