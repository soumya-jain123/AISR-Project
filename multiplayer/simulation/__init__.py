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
from multiplayer.simulation.mock_sim import MockMultiplayerSimulation
from multiplayer.simulation.coppelia_sim import CoppeliaMultiplayerSimulation

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
]
