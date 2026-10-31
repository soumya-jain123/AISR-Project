"""
Configuration settings for the Multiplayer backend.
Supports environment variables and command-line overrides without hardcoding.
"""

import os
from typing import List, Tuple
from pydantic import BaseModel, Field


def get_default_robot_configs():
    """Default robot grid for 4-player races with staggered starting positions."""
    from multiplayer.simulation.base import RobotConfig

    staggered_grid: List[Tuple[float, float, float]] = [
        (0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (-1.5, 0.0, 0.0),
        (-1.5, 1.0, 0.0),
    ]

    configs = []
    for i, pose in enumerate(staggered_grid, start=1):
        robot_id = f"robot_{i}"
        configs.append(
            RobotConfig(
                robot_id=robot_id,
                display_name=f"Pioneer Racer {i}",
                initial_pose=pose,
                coppelia_base_path=f"/PioneerP3DX_{i}",
                coppelia_left_motor_path=f"/PioneerP3DX_{i}/leftMotor",
                coppelia_right_motor_path=f"/PioneerP3DX_{i}/rightMotor",
            )
        )
    return configs


class MultiplayerSettings(BaseModel):
    """Configuration model for the multiplayer server."""

    host: str = Field(default_factory=lambda: os.getenv("MP_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.getenv("MP_PORT", "8000")))
    sim_mode: str = Field(default_factory=lambda: os.getenv("MP_SIM_MODE", "mock").lower())
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv("MP_CORS_ORIGINS", "*").split(",")
            if origin.strip()
        ]
    )
    log_level: str = Field(default_factory=lambda: os.getenv("MP_LOG_LEVEL", "INFO").upper())
    coppelia_host: str = Field(default_factory=lambda: os.getenv("COPPELIA_HOST", "127.0.0.1"))
    coppelia_port: int = Field(default_factory=lambda: int(os.getenv("COPPELIA_PORT", "23004")))
    sim_timeout_sec: float = Field(
        default_factory=lambda: float(os.getenv("MP_SIM_TIMEOUT_SEC", "1.0"))
    )
    max_players_per_room: int = Field(
        default_factory=lambda: int(os.getenv("MP_MAX_PLAYERS_PER_ROOM", "4"))
    )
    ws_heartbeat_interval_sec: float = Field(
        default_factory=lambda: float(os.getenv("MP_WS_HEARTBEAT_INTERVAL_SEC", "1.0"))
    )
    session_timeout_sec: float = Field(
        default_factory=lambda: float(os.getenv("MP_SESSION_TIMEOUT_SEC", "5.0"))
    )
    max_concurrent_connections: int = Field(
        default_factory=lambda: int(os.getenv("MP_MAX_CONCURRENT_CONNECTIONS", "100"))
    )
    max_message_bytes: int = Field(
        default_factory=lambda: int(os.getenv("MP_MAX_MESSAGE_BYTES", "16384"))
    )

    def validate_sim_mode(self) -> str:
        """Ensure sim_mode is either 'mock' or 'coppelia'."""
        mode = self.sim_mode.lower()
        if mode not in ("mock", "coppelia"):
            raise ValueError(f"Invalid sim_mode '{self.sim_mode}'. Must be 'mock' or 'coppelia'.")
        return mode


_settings: MultiplayerSettings | None = None


def get_settings() -> MultiplayerSettings:
    """Retrieve global multiplayer settings singleton."""
    global _settings
    if _settings is None:
        _settings = MultiplayerSettings()
    return _settings


def set_settings(settings: MultiplayerSettings) -> None:
    """Explicitly override global multiplayer settings (useful in tests)."""
    global _settings
    _settings = settings
