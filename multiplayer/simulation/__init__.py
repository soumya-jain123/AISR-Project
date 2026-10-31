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
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry
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
    "MockMultiplayerSimulation",
    "CoppeliaMultiplayerSimulation",
]
