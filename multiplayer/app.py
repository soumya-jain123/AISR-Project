"""
FastAPI application for AISR Multiplayer Robot Racing Simulator.
Provides health monitoring, simulation readiness checks, and sets up
the application foundation for multiplayer session management.
"""

from contextlib import asynccontextmanager
import logging
import time
from typing import Any, Dict
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from multiplayer.config import get_settings
from multiplayer.sim_check import check_simulation_readiness

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context for startup and shutdown logging."""
    settings = get_settings()
    logger.info(
        f"Starting AISR Multiplayer API on {settings.host}:{settings.port} "
        f"[Mode: {settings.sim_mode.upper()}]"
    )
    yield
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
