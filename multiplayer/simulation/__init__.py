"""
Multiplayer Simulation Package.
Provides simulation abstraction, robot registry, deterministic mock simulation,
and multi-robot CoppeliaSim adapter.
"""

from multiplayer.simulation.base import (
    MultiplayerSimulationError,
    RobotNotFoundError,
    RobotUnavailableError,
    SimulationNotReadyError,
    SimulationInterface,
    RobotConfig,
)
from multiplayer.simulation.robot_registry import (
    RobotInstance,
    RobotRegistry,
    get_robot_registry,
    set_robot_registry,
)
from typing import Optional
from multiplayer.config import MultiplayerSettings
from multiplayer.simulation.mock_sim import MockMultiplayerSimulation
from multiplayer.simulation.coppelia_sim import CoppeliaMultiplayerSimulation

_global_simulation: Optional[SimulationInterface] = None


def get_simulation(
    settings: Optional[MultiplayerSettings] = None,
    registry: Optional[RobotRegistry] = None,
) -> SimulationInterface:
    """Retrieve or initialize the global simulation backend singleton."""
    global _global_simulation
    if _global_simulation is None:
        from multiplayer.config import get_settings
        cfg = settings or get_settings()
        reg = registry or get_robot_registry()
        if cfg.sim_mode == "coppelia":
            _global_simulation = CoppeliaMultiplayerSimulation(
                host=cfg.coppelia_host,
                port=cfg.coppelia_port,
                timeout_sec=cfg.sim_timeout_sec,
                registry=reg,
            )
        else:
            _global_simulation = MockMultiplayerSimulation(
                dt=1.0 / cfg.sim_tick_rate_hz,
                auto_step=False,
                registry=reg,
            )
    return _global_simulation


def set_simulation(sim: Optional[SimulationInterface]) -> None:
    """Explicitly override global simulation singleton (useful in tests)."""
    global _global_simulation
    _global_simulation = sim


__all__ = [
    "MultiplayerSimulationError",
    "RobotNotFoundError",
    "RobotUnavailableError",
    "SimulationNotReadyError",
    "SimulationInterface",
    "RobotConfig",
    "RobotInstance",
    "RobotRegistry",
    "get_robot_registry",
    "set_robot_registry",
    "MockMultiplayerSimulation",
    "CoppeliaMultiplayerSimulation",
    "get_simulation",
    "set_simulation",
]
