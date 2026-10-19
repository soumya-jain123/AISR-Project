"""
Abstract Robot Driver Interface.
Enables decoupling the server state machine and networking from the CoppeliaSim simulator.
"""

from abc import ABC, abstractmethod
from typing import Tuple


class RobotDriverInterface(ABC):
    """Abstract base class for robot motion execution and telemetry."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to simulator or hardware."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Cleanly disconnect."""
        pass

    @abstractmethod
    def set_wheel_speeds(self, left_speed: float, right_speed: float) -> None:
        """Set direct left and right wheel angular/linear velocities."""
        pass

    @abstractmethod
    def drive(self, speed: float) -> None:
        """Drive straight at given linear velocity (m/s). Positive forward, negative backward."""
        pass

    @abstractmethod
    def turn(self, angle_deg: float, speed: float = 0.5) -> None:
        """Turn in-place by angle_deg (positive left, negative right)."""
        pass

    @abstractmethod
    def goto(self, target_x: float, target_y: float) -> bool:
        """Drive to target coordinates (x, y) using closed-loop control. Returns True if reached."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Instantly stop all wheel motion."""
        pass

    @abstractmethod
    def get_pose(self) -> Tuple[float, float, float]:
        """Return current pose (x, y, theta_radians)."""
        pass

    @abstractmethod
    def get_velocities(self) -> Tuple[float, float]:
        """Return current estimated (linear_velocity, angular_velocity)."""
        pass

    @abstractmethod
    def is_action_in_progress(self) -> bool:
        """Check if an asynchronous motion task (like goto or turn) is currently executing."""
        pass

    @abstractmethod
    def cancel_current_action(self) -> None:
        """Cancel any ongoing trajectory or controller action."""
        pass
