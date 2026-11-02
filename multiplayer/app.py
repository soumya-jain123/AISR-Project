"""
FastAPI application for AISR Multiplayer Robot Racing Simulator.
Provides health monitoring, simulation readiness checks, and the
multiplayer WebSocket connection endpoint with per-session management.
"""

from contextlib import asynccontextmanager
import logging
import time
from typing import Any, Dict
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from multiplayer.config import get_settings
from multiplayer.connection_manager import get_connection_manager
from multiplayer.sim_check import check_simulation_readiness
from multiplayer.simulation import get_simulation
from multiplayer.state_broadcaster import get_state_broadcaster

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context for startup and shutdown logging, simulation, and background tasks."""
    settings = get_settings()
    logger.info(
        f"Starting AISR Multiplayer API on {settings.host}:{settings.port} "
        f"[Mode: {settings.sim_mode.upper()}]"
    )

    # Initialize and connect simulation backend
    sim = get_simulation(settings)
    sim_ok = sim.connect()
    if not sim_ok:
        logger.warning(f"Simulation backend [{settings.sim_mode}] did not connect on startup.")

    # Start background session inactivity timeout sweeper
    manager = get_connection_manager(settings)
    await manager.start_timeout_sweeper(interval_sec=1.0)

    # Start background control & state synchronization broadcaster
    broadcaster = get_state_broadcaster(settings=settings)
    await broadcaster.start()

    yield

    # Shutdown sequence
    await broadcaster.stop()
    await manager.stop_timeout_sweeper()
    try:
        sim.disconnect()
    except Exception:
        pass
    logger.info("Shutting down AISR Multiplayer API")


app = FastAPI(
    title="AISR Multiplayer Robot Racing API",
    description="Backend API for Networked Multiplayer Robot Racing Simulator",
    version="1.0.0",
    lifespan=lifespan,
)

# Apply CORS middleware
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def get_root() -> Dict[str, Any]:
    """Root endpoint returning basic service metadata."""
    settings = get_settings()
    return {
        "service": "AISR Multiplayer Robot Racing Simulator",
        "version": "1.0.0",
        "sim_mode": settings.sim_mode,
        "health_url": "/api/health",
        "readiness_url": "/api/ready",
        "websocket_url": "/ws/multiplayer",
    }


@app.get("/api/health")
def get_health() -> Dict[str, Any]:
    """
    Liveness probe.
    Indicates that the HTTP API process is alive and responsive.
    NOTE: Does NOT imply the simulation engine is ready. Check /api/ready for simulation readiness.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "service": "aisr-multiplayer-backend",
        "version": "1.0.0",
        "timestamp": round(time.time(), 3),
        "sim_mode": settings.sim_mode,
    }


@app.get("/api/ready")
def get_readiness(response: Response) -> Dict[str, Any]:
    """
    Readiness probe.
    Explicitly checks whether the simulation backend (Mock or CoppeliaSim) is reachable.
    Returns HTTP 200 when ready, HTTP 503 when the simulation backend is unavailable.
    """
    settings = get_settings()
    sim_status = check_simulation_readiness(settings)

    if not sim_status["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "ready": sim_status["ready"],
        "api": "ok",
        "sim_mode": settings.sim_mode,
        "simulation": sim_status,
        "timestamp": round(time.time(), 3),
    }


@app.websocket("/ws/multiplayer")
async def websocket_multiplayer_endpoint(websocket: WebSocket):
    """
    Multiplayer WebSocket endpoint.
    Maintains independent session state, validates incoming protocol frames,
    enforces sequence numbers, and sends acknowledgements / error frames.
    """
    manager = get_connection_manager()
    session = await manager.register(websocket)
    if session is None:
        # Connection limit exceeded or registration failed
        return

    try:
        while True:
            raw_text = await websocket.receive_text()
            response = await manager.process_client_message(session, raw_text)
            if response is not None:
                await manager.send_message(session.session_id, response)

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {session.session_id}")
    except Exception as e:
        logger.warning(f"Error handling WebSocket session {session.session_id}: {e}")
    finally:
        await manager.unregister(session.session_id, reason="Client disconnected")
