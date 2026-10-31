"""
Thread-safe Robot Registry for managing multi-robot simulation instances.
Tracks individual robot configuration, health/availability, and handles.
"""

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, List, Optional, Tuple

from multiplayer.protocol import ID_REGEX, RobotOperatingState, RobotStatePayload
from multiplayer.simulation.base import (
    RobotConfig,
    RobotNotFoundError,
    MultiplayerSimulationError,
)


@dataclass
class RobotInstance:
    """Runtime representation of an individual simulated robot."""

    config: RobotConfig
    pose: Tuple[float, float, float]
    velocities: Tuple[float, float] = (0.0, 0.0)
    operating_state: RobotOperatingState = RobotOperatingState.IDLE
    is_available: bool = True
    assigned_player_id: Optional[str] = None
    lap: int = 0
    checkpoint: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def robot_id(self) -> str:
        return self.config.robot_id

    def to_payload(self) -> RobotStatePayload:
        """Convert runtime instance to a validated RobotStatePayload."""
        return RobotStatePayload(
            robot_id=self.robot_id,
            player_id=self.assigned_player_id,
            x=round(self.pose[0], 4),
            y=round(self.pose[1], 4),
            theta=round(self.pose[2], 4),
            linear_vel=round(self.velocities[0], 4),
            angular_vel=round(self.velocities[1], 4),
            state=self.operating_state,
            lap=self.lap,
            checkpoint=self.checkpoint,
        )


class RobotRegistry:
    """
    Registry managing known robot instances in a simulation environment.
    Guarantees thread-safety, non-exposure of internal mutable state, and duplicate rejection.
    """

    def __init__(self) -> None:
        self._robots: Dict[str, RobotInstance] = {}
        self._lock = threading.RLock()

    def register(self, config: RobotConfig) -> RobotInstance:
        """
        Register a new robot with the given configuration.
        Raises ValueError if robot_id is invalid or already registered.
        """
        if not ID_REGEX.match(config.robot_id):
            raise ValueError(f"Invalid robot_id '{config.robot_id}'. Must match pattern {ID_REGEX.pattern}")

        with self._lock:
            if config.robot_id in self._robots:
                raise ValueError(f"Robot with ID '{config.robot_id}' is already registered.")

            instance = RobotInstance(
                config=config,
                pose=config.initial_pose,
                velocities=(0.0, 0.0),
                operating_state=RobotOperatingState.IDLE,
                is_available=True,
            )
            self._robots[config.robot_id] = instance
            return instance

    def unregister(self, robot_id: str) -> bool:
        """
        Remove a robot from the registry.
        Returns True if removed, False if not found.
        """
        with self._lock:
            return self._robots.pop(robot_id, None) is not None

    def get(self, robot_id: str) -> Optional[RobotInstance]:
        """Lookup robot by ID. Returns None if not found."""
        with self._lock:
            return self._robots.get(robot_id)

    def require(self, robot_id: str) -> RobotInstance:
        """
        Lookup robot by ID or raise RobotNotFoundError.
        Guarantees operations fail closed on unknown robots.
        """
        with self._lock:
            robot = self._robots.get(robot_id)
            if robot is None:
                raise RobotNotFoundError(robot_id)
            return robot

    def contains(self, robot_id: str) -> bool:
        """Check if robot_id exists in registry."""
        with self._lock:
            return robot_id in self._robots

    def get_all_ids(self) -> List[str]:
        """Return list of all registered robot IDs (copy)."""
        with self._lock:
            return list(self._robots.keys())

    def get_all_instances(self) -> List[RobotInstance]:
        """Return shallow copy of all registered robot instances."""
        with self._lock:
            return list(self._robots.values())

    def count(self) -> int:
        """Return total number of registered robots."""
        with self._lock:
            return len(self._robots)

    def set_availability(self, robot_id: str, is_available: bool, reason: str = "") -> None:
        """Update a robot's availability status."""
        with self._lock:
            robot = self.require(robot_id)
            robot.is_available = is_available
            if reason:
                robot.metadata["unavailable_reason"] = reason

    def clear(self) -> None:
        """Clear all registered robots."""
        with self._lock:
            self._robots.clear()
