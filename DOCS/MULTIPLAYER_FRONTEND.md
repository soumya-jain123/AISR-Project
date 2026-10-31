# Module 6: Browser Frontend and 3D Arena

This document specifies the browser frontend architecture, setup instructions, and component layout for the AISR Multiplayer Robot Racing Simulator.

---

## 1. Overview & Technology Stack

The frontend is built as a responsive, real-time single-page application (SPA) communicating over WebSockets with the authoritative FastAPI Python backend.

- **Framework:** React 19 + TypeScript + Vite 6
- **3D Graphics Engine:** Three.js (WebGL with PCF Soft Shadows)
- **Styling:** Vanilla CSS with custom design tokens, dark motorsport theme, and responsive flex/grid layouts.
- **Testing:** Vitest + React Testing Library + jest-dom + Real Browser Automation via Chromium.

---

## 2. Directory Structure

```
frontend/
├── .env.example               # Example environment configuration
├── .env                       # Local environment variables (VITE_WS_URL, etc.)
├── index.html                 # HTML5 entry with meta tags
├── package.json               # NPM dependencies and scripts
├── tsconfig.json              # TypeScript root configuration
├── tsconfig.app.json          # Application TypeScript configuration
├── vite.config.ts             # Vite server, proxy, and Vitest configuration
└── src/
    ├── main.tsx               # React application entry point
    ├── App.tsx                # App shell, routing state, and client lifecycle
    ├── App.css                # App shell container styling
    ├── index.css              # Global design tokens, dark racing theme, and utilities
    ├── setupTests.ts          # Vitest and testing-library setup
    ├── types/
    │   └── protocol.ts        # Versioned protocol interfaces matching backend schemas
    ├── services/
    │   └── MultiplayerClient.ts # Pure WebSocket service (heartbeat, sequence, events)
    ├── components/
    │   ├── Arena3D.tsx        # Three.js 3D race track, Pioneer P3-DX models, and staging grid
    │   ├── LobbyView.tsx      # Pilot profile, room creation, join by code, and active rooms
    │   └── RoomView.tsx       # Room roster, readiness toggle, robot badge, and arena embed
    └── __tests__/
        ├── protocol.test.ts   # Protocol message generation and schema validation tests
        ├── LobbyView.test.tsx # Lobby form validation and interaction tests
        └── RoomView.test.tsx  # Room roster, readiness, and error display tests
```

---

## 3. Installation and Development Workflow

### Prerequisites
- Node.js (v18+ recommended; verified on Node v22.16.0, npm 11.4.2)
- Python 3.11+ virtual environment with multiplayer dependencies

### Quickstart

1. **Start the Backend Server (Mock Simulation Mode):**
   ```powershell
   # From repository root
   .\.venv\Scripts\python.exe -m multiplayer.run_multiplayer --mock --port 8000
   ```

2. **Install Frontend Dependencies:**
   ```powershell
   cd frontend
   npm install
   ```

3. **Start the Frontend Vite Dev Server:**
   ```powershell
   npm run dev
   ```
   Open `http://127.0.0.1:3000` in a WebGL-compatible browser.

4. **Run Frontend Tests:**
   ```powershell
   npm test
   ```

5. **Build for Production:**
   ```powershell
   npm run build
   ```
   Outputs minified bundle to `frontend/dist/`.

---

## 4. Environment Variables

The frontend reads configuration from `.env`:

| Variable | Default Value | Description |
|---|---|---|
| `VITE_WS_URL` | `ws://127.0.0.1:8000/ws/multiplayer` | Authoritative backend WebSocket endpoint |
| `VITE_API_URL` | `http://127.0.0.1:8000` | Backend HTTP REST endpoint for readiness checks |

---

## 5. User Flows

### 5.1 Lobby Flow
1. **Pilot Identity:** The user enters a driver display name (1–32 characters, letters/numbers/spaces/hyphens). Persisted in `localStorage`.
2. **Create Room:** The user selects capacity (2–4 players) and clicks **Create & Host Room**. The server returns a 5-character room code (e.g. `K9X2P`) and assigns a robot (e.g. `robot_1`).
3. **Join Room:** A user enters a 5-character code from a host and clicks **Enter Race Room**. The server assigns the next available robot.
4. **Room Browser:** The user can browse active public rooms and copy their codes with one click.

### 5.2 Room Waiting Screen
1. **Room Code:** Large display with 1-click clipboard copy.
2. **Roster:** Displays each member's name, assigned Pioneer racer, and readiness state.
3. **Ready Up:** The user toggles their ready status (`✓ READY` or `⏳ WAITING`).
4. **Leave Room:** Cleanly releases the user's robot reservation and returns to the lobby.

### 5.3 3D Arena Visuals
- Realistic asphalt race track with inner island, curb strips, finish line, and cyan/orange stadium floodlighting.
- Pioneer P3-DX mobile robots positioned on their designated starting grid coordinates:
  - `robot_1`: `(0.0, 0.0, 0.0)` — Crimson Red
  - `robot_2`: `(0.0, 1.2, 0.0)` — Electric Cyan
  - `robot_3`: `(-1.8, 0.0, 0.0)` — Emerald Green
  - `robot_4`: `(-1.8, 1.2, 0.0)` — Amber Gold
- Includes dual differential drive wheels, front curved sonar sensor ring, and active status beacon.
- Supports Orbit (mouse-drag) and Top-Down camera modes with mouse wheel zoom.
- WebGL fallback banner displayed automatically if 3D acceleration is unavailable.

---

## 6. Current Limitations & Next Steps

- **No Movement Dispatch:** Browser keyboard / touch inputs do not yet actuate robots (scheduled for Module 7).
- **Static Staging Poses:** Robot meshes render at initial grid coordinates; live dynamic pose synchronization begins in Module 7.
- **No Race Lifecycle:** Checkpoint detection, lap counting, and countdown timers are scheduled for Module 8.
