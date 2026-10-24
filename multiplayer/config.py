"""
Configuration settings for the Multiplayer backend.
Supports environment variables and command-line overrides without hardcoding.
"""

import os
from typing import List
from pydantic import BaseModel, Field


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
    max_players_per_room: int = Field(
        default_factory=lambda: int(os.getenv("MP_MAX_PLAYERS_PER_ROOM", "4"))
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
