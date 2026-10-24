# AISR Multiplayer Development & Architecture Guide

This document defines the project structure, development commands, and endpoint specifications for the **Networked Multiplayer Robot Racing Simulator** (Module 1).

---

## 1. Project Architecture & Directory Layout

The codebase maintains a clean separation of concerns between existing single-operator teleoperation and the new multiplayer stack:

```text
AISR-Project/
├── client/                     # Existing single-operator CLI teleoperation client
│   ├── heartbeat.py            # Heartbeat sender thread (0.5s interval)
│   ├── logger.py               # Telemetry CSV logging
│   └── operator_client.py      # Terminal command shell interface
├── common/                     # Shared protocol, config, and utilities
│   ├── config.py               # Constants, port defaults, robot speed limits
│   ├── protocol.py             # Line-delimited JSON framing (StreamFramer) & schemas
│   └── netdelay.py             # Artificial network delay queue (DelayedQueue)
├── server/                     # Existing single-robot TCP server & drivers
│   ├── driver_interface.py     # RobotDriverInterface ABC
│   ├── mock_driver.py          # Kinematic 2D differential-drive simulation (headless)
│   ├── sim_driver.py           # CoppeliaSim Pioneer P3-DX ZeroMQ driver
│   ├── state_machine.py        # 4-state safety logic (IDLE, MOVING, COMM_LOST, ESTOP)
│   ├── watchdog.py             # Heartbeat watchdog thread (1.5s timeout)
│   ├── robot_server.py         # Authoritative TCP server with priority E-Stop
│   └── run_server.py           # Existing server CLI entrypoint
├── multiplayer/                # NEW: Multiplayer backend application (Module 1+)
│   ├── __init__.py             # Package marker & version
│   ├── app.py                  # FastAPI application with CORS and health/readiness endpoints
│   ├── config.py               # Typed Pydantic configuration loaded from environment
│   ├── sim_check.py            # Fast non-blocking simulation readiness probe
│   └── run_multiplayer.py      # CLI runner for the multiplayer API
├── frontend/                   # Browser user interface (Three.js 3D arena + React UI)
├── experiments/                # Automated parameter sweep test harness
│   ├── run_experiments.py      # Sweeps delays [0, 100, 300, 500, 1000] ms
│   └── analyze.py              # Matplotlib plots and results table generator
├── tests/                      # Automated test suite
│   ├── test_client.py          # Existing client teleoperation tests
│   ├── test_netdelay.py        # Existing delay queue tests
│   ├── test_protocol.py        # Existing JSON framing tests
│   ├── test_server_core.py     # Existing state machine & TCP server tests
│   └── test_multiplayer_module1.py # NEW: Health, readiness, and configuration tests
├── docs/                       # Project documentation and developer guides
├── results/                    # CSV telemetry exports and experiment figures
├── .env.example                # Environment variables template
├── requirements.txt            # Python dependencies (UTF-8)
└── README.md                   # Core project documentation
```

---

## 2. Environment Configuration

Copy `.env.example` to `.env` or set environment variables:

| Variable | Default | Description |
|---|---|---|
| `MP_HOST` | `127.0.0.1` | IP address to bind the multiplayer API server |
| `MP_PORT` | `8000` | Port for the multiplayer HTTP / WebSocket server |
| `MP_SIM_MODE` | `mock` | Simulation mode: `mock` (headless) or `coppelia` |
| `MP_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MP_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |
| `COPPELIA_HOST` | `127.0.0.1` | CoppeliaSim host for ZeroMQ Remote API |
| `COPPELIA_PORT` | `23004` | CoppeliaSim port for ZeroMQ Remote API |
| `MP_MAX_PLAYERS_PER_ROOM` | `4` | Maximum active drivers per race room |

---

## 3. How to Run the Applications

### 3.1 Existing CLI Teleoperation (Unchanged & Preserved)

#### Mode A: Headless Mock Driver
```bash
# Terminal 1 - Start Robot Server:
python server/run_server.py --mock

# Terminal 2 - Start Operator Shell:
python client/operator_client.py
```

#### Mode B: With CoppeliaSim
```bash
# 1. Launch CoppeliaSim and load Pioneer P3-DX
# 2. Terminal 1:
python server/run_server.py

# 3. Terminal 2:
python client/operator_client.py
```

---

### 3.2 New Multiplayer Development Environment

#### Mode A: Headless Mock Mode (Recommended for development without simulator)
```bash
python multiplayer/run_multiplayer.py --mock
```
Output:
```text
======================================================================
  AISR NETWORKED MULTIPLAYER ROBOT RACING SIMULATOR - BACKEND
  Mode:           MOCK SIMULATION
  Listening on:   http://127.0.0.1:8000
  Health Check:   http://127.0.0.1:8000/api/health
  Readiness:      http://127.0.0.1:8000/api/ready
  API Docs:       http://127.0.0.1:8000/docs
======================================================================
```

#### Mode B: Live CoppeliaSim Connection
```bash
python multiplayer/run_multiplayer.py --coppelia
```

#### Development Auto-Reload Mode
```bash
python multiplayer/run_multiplayer.py --mock --reload
```

---

## 4. Health and Readiness Endpoints

### 4.1 Liveness Probe: `GET /api/health`
- **Purpose:** Verifies that the API web process is alive and responsive.
- **Status Code:** `200 OK`
- **Response Format:**
  ```json
  {
    "status": "ok",
    "service": "aisr-multiplayer-backend",
    "version": "1.0.0",
    "timestamp": 1730000000.123,
    "sim_mode": "mock"
  }
  ```
- **Safety Guarantee:** A successful health check **does not** imply that the simulation is ready.

### 4.2 Readiness Probe: `GET /api/ready`
- **Purpose:** Verifies whether the simulation backend is connected, reachable, and ready to simulate robots.
- **When Ready (HTTP 200 OK):**
  ```json
  {
    "ready": true,
    "api": "ok",
    "sim_mode": "mock",
    "simulation": {
      "ready": true,
      "mode": "mock",
      "details": "Mock differential-drive simulation engine is ready",
      "error": null
    },
    "timestamp": 1730000000.123
  }
  ```
- **When Simulation is Unavailable (HTTP 503 Service Unavailable):**
  ```json
  {
    "ready": false,
    "api": "ok",
    "sim_mode": "coppelia",
    "simulation": {
      "ready": false,
      "mode": "coppelia",
      "details": "CoppeliaSim ZMQ API is not reachable at 127.0.0.1:23004",
      "error": "Connection failed: [WinError 10061] No connection could be made because the target machine actively refused it"
    },
    "timestamp": 1730000000.123
  }
  ```

---

## 5. Running Automated Tests

Run the full pytest suite (both baseline teleoperation tests and multiplayer tests):

```bash
pytest -v
```

To run only the new multiplayer module tests:
```bash
pytest tests/test_multiplayer_module1.py -v
```
