"""
Tests for Module 1: Project Structure, Development Environment, Health and Readiness endpoints.
Verifies that:
1. /api/health responds with 200 and 'status: ok'.
2. /api/ready responds with 200 and 'ready: true' in mock mode.
3. /api/ready responds with 503 and 'ready: false' when CoppeliaSim is unreachable.
4. Health status does not falsely imply that simulation is ready.
5. Environment variables properly override settings.
"""

import os
import pytest
from fastapi.testclient import TestClient

from multiplayer.app import app
from multiplayer.config import MultiplayerSettings, set_settings
from multiplayer.run_multiplayer import parse_args


@pytest.fixture
def client():
    """TestClient fixture for FastAPI app."""
    return TestClient(app)


def test_root_endpoint(client):
    """Verify root metadata endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert data["health_url"] == "/api/health"
    assert data["readiness_url"] == "/api/ready"


def test_health_endpoint(client):
    """
    Verify /api/health returns 200 and process liveness status.
    Must always succeed as long as the API server process is running.
    """
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "aisr-multiplayer-backend"
    assert "timestamp" in data


def test_readiness_mock_mode(client):
    """
    Verify /api/ready in mock mode returns HTTP 200 and ready=True.
    """
    set_settings(MultiplayerSettings(sim_mode="mock"))
    response = client.get("/api/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["api"] == "ok"
    assert data["sim_mode"] == "mock"
    assert data["simulation"]["ready"] is True
    assert "mock" in data["simulation"]["details"].lower()


def test_readiness_coppelia_mode_unreachable_returns_503(client):
    """
    Verify /api/ready in coppelia mode when simulator is unreachable returns HTTP 503 and ready=False.
    Crucially: health endpoint must still return 200, proving health does not falsely imply simulation readiness.
    """
    # Use non-existent port to guarantee unreachable simulation
    set_settings(
        MultiplayerSettings(
            sim_mode="coppelia",
            coppelia_host="127.0.0.1",
            coppelia_port=59999,
        )
    )

    # 1. Health must still be 200 OK (API process is alive)
    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    # 2. Readiness must return 503 Service Unavailable (Simulation is NOT ready)
    ready_resp = client.get("/api/ready")
    assert ready_resp.status_code == 503
    data = ready_resp.json()
    assert data["ready"] is False
    assert data["api"] == "ok"
    assert data["sim_mode"] == "coppelia"
    assert data["simulation"]["ready"] is False
    assert data["simulation"]["error"] is not None

    # Reset back to mock mode
    set_settings(MultiplayerSettings(sim_mode="mock"))


def test_configuration_environment_overrides(monkeypatch):
    """Test that environment variables override configuration defaults."""
    monkeypatch.setenv("MP_HOST", "0.0.0.0")
    monkeypatch.setenv("MP_PORT", "9000")
    monkeypatch.setenv("MP_SIM_MODE", "mock")
    monkeypatch.setenv("MP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MP_MAX_PLAYERS_PER_ROOM", "6")

    cfg = MultiplayerSettings()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.sim_mode == "mock"
    assert cfg.log_level == "DEBUG"
    assert cfg.max_players_per_room == 6


def test_cli_argument_parsing(monkeypatch):
    """Test argument parsing for run_multiplayer.py."""
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_multiplayer.py", "--host", "127.0.0.1", "--port", "8080", "--mock"],
    )
    args = parse_args()
    assert args.host == "127.0.0.1"
    assert args.port == 8080
    assert args.mock is True
    assert args.coppelia is False
