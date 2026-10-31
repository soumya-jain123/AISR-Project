"""
Base interfaces and models for Multiplayer Simulation Abstraction.
Defines explicit robot identity on every operation and clean error hierarchies.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from multiplayer.protocol import ID_PATTERN, RobotOperatingState, RobotStatePayload


class MultiplayerSimulationError(Exception):
    """Base exception for all simulation abstraction errors."""
    pass


class RobotNotFoundError(MultiplayerSimulationError):
    """Raised when an operation targets an unmapped or unregistered robot ID."""
    def __init__(self, robot_id: str):
        super().__init__(f"Robot '{robot_id}' is not registered in the simulation registry.")
        self.robot_id = robot_id


class RobotUnavailableError(MultiplayerSimulationError):
    """Raised when a robot is registered but its physical or simulation handle is disconnected/faulty."""
    def __init__(self, robot_id: str, reason: str = "Robot is unavailable"):
        super().__init__(f"Robot '{robot_id}' is unavailable: {reason}")
        self.robot_id = robot_id
        self.reason = reason


class SimulationNotReadyError(MultiplayerSimulationError):
    """Raised when commands are issued to a disconnected or stopped simulation backend."""
    def __init__(self, mode: str, details: str = "Simulation backend is not ready"):
        super().__init__(f"Simulation backend [{mode}] not ready: {details}")
        self.mode = mode
        self.details = details


class RobotConfig(BaseModel):
    """
    Configuration specification for a simulated mobile robot.
    Supports both headless mock and CoppeliaSim scene object mappings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    robot_id: str = Field(pattern=ID_PATTERN, description="Unique, stable robot identifier")
    display_name: str = Field(default="", max_length=64)
    initial_pose: Tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        description="(x, y, theta_rad) world starting pose",
    )
    # CoppeliaSim scene paths (optional in mock mode)
    coppelia_base_path: Optional[str] = Field(default=None, description="Path to base robot object, e.g. /PioneerP3DX_1")
    coppelia_left_motor_path: Optional[str] = Field(default=None, description="Path to left wheel motor joint")
    coppelia_right_motor_path: Optional[str] = Field(default=None, description="Path to right wheel motor joint")

    def resolve_coppelia_paths(self) -> Tuple[str, str, str]:
        """
        Derive full CoppeliaSim object paths if not explicitly overridden.
        Uses convention: base: /<robot_id> (or /PioneerP3DX_<idx>), motors: <base>/leftMotor, <base>/rightMotor.
        """
        base = self.coppelia_base_path or f"/{self.robot_id}"
        left = self.coppelia_left_motor_path or f"{base}/leftMotor"
        right = self.coppelia_right_motor_path or f"{base}/rightMotor"
        return base, left, right


class SimulationInterface(ABC):
    """
    Abstract interface for multiplayer robot simulation backends.
    All robot-targeting operations strictly require an explicit robot_id.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the simulation backend."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Cleanly terminate simulation connections and release resources."""
        pass

    @abstractmethod
    def is_ready(self) -> bool:
        """Return True if the backend is connected and ready to process commands."""
        pass

    @abstractmethod
    def get_robot_state(self, robot_id: str) -> RobotStatePayload:
        """
        Retrieve current authoritative state for a specific robot.
        Raises RobotNotFoundError if robot_id is unknown.
        Raises RobotUnavailableError if robot handles cannot be queried.
        """
        pass

    @abstractmethod
    def apply_control(self, robot_id: str, throttle: float, steering: float) -> bool:
        """
        Apply normalized throttle [-1.0, 1.0] and steering [-1.0, 1.0] to a specific robot.
        Commands must strictly target only the designated robot.
        """
        pass

    @abstractmethod
    def stop_robot(self, robot_id: str, reason: str = "") -> bool:
        """
        Immediately halt motion of a single robot without modifying other robots.
        """
        pass

    @abstractmethod
    def stop_all(self, reason: str = "") -> None:
        """
        Halt motion on all registered robots in this simulation environment.
        """
        pass

    @abstractmethod
    def reset_robot(self, robot_id: str, pose: Optional[Tuple[float, float, float]] = None) -> bool:
        """
        Reset a robot's pose and velocities to its initial or specified coordinates.
        """
        pass

    @abstractmethod
    def get_registered_robot_ids(self) -> List[str]:
        """Return list of all registered robot IDs."""
        pass
