"""
Simulation readiness probe.
Explicitly distinguishes between API liveness and simulation readiness.
Health status does NOT falsely imply that simulation is ready.
"""

import logging
import socket
from typing import Any, Dict
from multiplayer.config import MultiplayerSettings, get_settings

logger = logging.getLogger(__name__)


def check_simulation_readiness(settings: MultiplayerSettings | None = None) -> Dict[str, Any]:
    """
    Check if the configured simulation backend is operational.
    Returns:
        dict: {
            "ready": bool,
            "mode": "mock" | "coppelia",
            "details": str,
            "error": Optional[str]
        }
    """
    cfg = settings or get_settings()
    mode = cfg.sim_mode.lower()

    if mode == "mock":
        try:
            from server.mock_driver import MockRobotDriver

            driver = MockRobotDriver()
            if driver.connect():
                driver.disconnect()
                return {
                    "ready": True,
                    "mode": "mock",
                    "details": "Mock differential-drive simulation engine is ready",
                    "error": None,
                }
            return {
                "ready": False,
                "mode": "mock",
                "details": "Mock driver failed to start",
                "error": "Initialization failure",
            }
        except Exception as e:
            logger.error(f"Error checking mock simulation: {e}")
            return {
                "ready": False,
                "mode": "mock",
                "details": "Failed to initialize mock driver",
                "error": str(e),
            }

    elif mode == "coppelia":
        # 1. Rapid pre-flight TCP probe to avoid blocking on unresponsive ZMQ socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
                probe_sock.settimeout(0.3)
                probe_sock.connect((cfg.coppelia_host, cfg.coppelia_port))
        except Exception as e:
            logger.debug(
                f"CoppeliaSim socket check failed at {cfg.coppelia_host}:{cfg.coppelia_port}: {e}"
            )
            return {
                "ready": False,
                "mode": "coppelia",
                "details": f"CoppeliaSim ZMQ API is not reachable at {cfg.coppelia_host}:{cfg.coppelia_port}",
                "error": f"Connection failed: {e}",
            }

        # 2. Probe through ZMQ client with bounded timeouts
        try:
            import zmq
            from coppeliasim_zmqremoteapi_client import RemoteAPIClient

            client = RemoteAPIClient(host=cfg.coppelia_host, port=cfg.coppelia_port)
            # Enforce bounded socket timeouts
            client.socket.setsockopt(zmq.RCVTIMEO, 500)
            client.socket.setsockopt(zmq.SNDTIMEO, 500)

            sim = client.require("sim")
            sim_time = sim.getSimulationTime()
            sim_state = sim.getSimulationState()

            return {
                "ready": True,
                "mode": "coppelia",
                "details": f"Connected to CoppeliaSim ZMQ API (sim_time={sim_time:.2f}s, state={sim_state})",
                "error": None,
            }
        except Exception as e:
            logger.warning(
                f"CoppeliaSim probe failed at {cfg.coppelia_host}:{cfg.coppelia_port}: {e}"
            )
            return {
                "ready": False,
                "mode": "coppelia",
                "details": f"CoppeliaSim ZMQ API error at {cfg.coppelia_host}:{cfg.coppelia_port}",
                "error": str(e),
            }

    return {
        "ready": False,
        "mode": mode,
        "details": f"Unknown simulation mode '{mode}'",
        "error": "Unsupported simulation mode",
    }
