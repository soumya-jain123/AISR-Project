"""
CLI entry point to launch the AISR Multiplayer backend.
Supports both mock simulation mode and live CoppeliaSim connection.
"""

import argparse
import logging
import os
import sys

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import uvicorn
from multiplayer.config import MultiplayerSettings, get_settings, set_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MultiplayerRunner")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start AISR Multiplayer Backend Server")
    parser.add_argument("--host", default=None, help="Host to bind server to (default: MP_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on (default: MP_PORT or 8000)")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in headless mock simulation mode (no CoppeliaSim required)",
    )
    parser.add_argument(
        "--coppelia",
        action="store_true",
        help="Connect to live CoppeliaSim instance via ZeroMQ Remote API",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code change (development mode)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    settings = get_settings()

    # Apply CLI argument overrides
    host = args.host or settings.host
    port = args.port or settings.port

    sim_mode = settings.sim_mode
    if args.mock:
        sim_mode = "mock"
    elif args.coppelia:
        sim_mode = "coppelia"

    # Update active configuration
    updated_settings = MultiplayerSettings(
        host=host,
        port=port,
        sim_mode=sim_mode,
        cors_origins=settings.cors_origins,
        log_level=settings.log_level,
        coppelia_host=settings.coppelia_host,
        coppelia_port=settings.coppelia_port,
        max_players_per_room=settings.max_players_per_room,
    )
    set_settings(updated_settings)

    print("=" * 70)
    print("  AISR NETWORKED MULTIPLAYER ROBOT RACING SIMULATOR - BACKEND")
    print(f"  Mode:           {sim_mode.upper()} SIMULATION")
    print(f"  Listening on:   http://{host}:{port}")
    print(f"  Health Check:   http://{host}:{port}/api/health")
    print(f"  Readiness:      http://{host}:{port}/api/ready")
    print(f"  API Docs:       http://{host}:{port}/docs")
    print("=" * 70)

    uvicorn.run(
        "multiplayer.app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=updated_settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
