"""
Deterministic multi-robot mock simulation backend.
Supports headless unit testing and local development without CoppeliaSim.
"""

import math
import threading
import time
from typing import Dict, List, Optional, Tuple

from common.config import MAX_LINEAR_SPEED, PIONEER_TRACK_WIDTH
from multiplayer.protocol import RobotOperatingState, RobotStatePayload
from multiplayer.simulation.base import (
    RobotConfig,
    RobotNotFoundError,
    RobotUnavailableError,
    SimulationInterface,
    SimulationNotReadyError,
)
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry, get_robot_registry

MAX_ANGULAR_SPEED = 1.5  # rad/s for differential turning


class MockMultiplayerSimulation(SimulationInterface):
    """
    Deterministic multi-robot mock simulation.
    Integrates 2D differential-drive kinematics for all registered robots independently.
    Supports explicit step(dt) for reproducible unit tests, or a background worker loop for live execution.
    """

    def __init__(
        self,
        robots: Optional[List[RobotConfig]] = None,
        dt: float = 0.05,
        auto_step: bool = False,
        registry: Optional[RobotRegistry] = None,
    ) -> None:
        self.dt = dt
        self.auto_step = auto_step

        if registry is not None:
            self.registry = registry
        elif robots is not None:
            self.registry = RobotRegistry()
        else:
            self.registry = get_robot_registry()

        self._connected = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # Target control inputs per robot: robot_id -> (target_v, target_w)
        self._control_inputs: Dict[str, Tuple[float, float]] = {}

        if robots:
            for cfg in robots:
                if not self.registry.contains(cfg.robot_id):
                    self.registry.register(cfg)
                self._control_inputs[cfg.robot_id] = (0.0, 0.0)
        else:
            for r_id in self.registry.get_all_ids():
                self._control_inputs[r_id] = (0.0, 0.0)

    def connect(self) -> bool:
        with self._lock:
            if self._connected:
                return True
            self._connected = True
            if self.auto_step:
                self._running = True
                self._thread = threading.Thread(
                    target=self._update_loop,
                    daemon=True,
                    name="MockMultiSimLoop",
                )
                self._thread.start()
            return True

    def disconnect(self) -> None:
        with self._lock:
            self._running = False
            self._connected = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def is_ready(self) -> bool:
        with self._lock:
            return self._connected

    def get_registered_robot_ids(self) -> List[str]:
        return self.registry.get_all_ids()

    def get_robot_state(self, robot_id: str) -> RobotStatePayload:
        with self._lock:
            if not self._connected:
                raise SimulationNotReadyError("mock", "Simulation is not connected")
            robot = self.registry.require(robot_id)
            if not robot.is_available:
                raise RobotUnavailableError(robot_id, robot.metadata.get("unavailable_reason", "Not available"))
            return robot.to_payload()

    def apply_control(self, robot_id: str, throttle: float, steering: float) -> bool:
        """
        Apply normalized throttle [-1.0, 1.0] and steering [-1.0, 1.0] to a specific robot.
        Fails closed if the robot is unknown or unavailable.
        """
        with self._lock:
            if not self._connected:
                raise SimulationNotReadyError("mock", "Cannot apply control: simulation not connected")

            robot = self.registry.require(robot_id)
            if not robot.is_available:
                raise RobotUnavailableError(robot_id)

            if robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
                # E-stop is latched; motion commands are rejected
                return False

            clamped_throttle = max(-1.0, min(float(throttle), 1.0))
            clamped_steering = max(-1.0, min(float(steering), 1.0))

            target_v = clamped_throttle * MAX_LINEAR_SPEED
            target_w = clamped_steering * MAX_ANGULAR_SPEED

            self._control_inputs[robot_id] = (target_v, target_w)
            robot.velocities = (target_v, target_w)

            if abs(target_v) > 1e-4 or abs(target_w) > 1e-4:
                robot.operating_state = RobotOperatingState.MOVING
            else:
                robot.operating_state = RobotOperatingState.IDLE

            return True

    def stop_robot(self, robot_id: str, reason: str = "") -> bool:
        with self._lock:
            robot = self.registry.require(robot_id)
            self._control_inputs[robot_id] = (0.0, 0.0)
            robot.velocities = (0.0, 0.0)
            if robot.operating_state != RobotOperatingState.ESTOP_LATCHED:
                robot.operating_state = RobotOperatingState.IDLE
            return True

    def stop_all(self, reason: str = "") -> None:
        with self._lock:
            for robot_id in self.registry.get_all_ids():
                self.stop_robot(robot_id, reason=reason)

    def reset_robot(self, robot_id: str, pose: Optional[Tuple[float, float, float]] = None) -> bool:
        with self._lock:
            robot = self.registry.require(robot_id)
            target_pose = pose if pose is not None else robot.config.initial_pose
            robot.pose = target_pose
            robot.velocities = (0.0, 0.0)
            self._control_inputs[robot_id] = (0.0, 0.0)
            robot.operating_state = RobotOperatingState.IDLE
            return True

    def step(self, dt: Optional[float] = None) -> None:
        """
        Advance kinematics by dt seconds for all active robots.
        Guarantees deterministic, reproducible state progression.
        """
        step_dt = dt if dt is not None else self.dt
        with self._lock:
            for robot_id, (target_v, target_w) in self._control_inputs.items():
                robot = self.registry.get(robot_id)
                if not robot or not robot.is_available:
                    continue

                if robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
                    continue

                x, y, theta = robot.pose
                new_theta = (theta + target_w * step_dt + math.pi) % (2 * math.pi) - math.pi
                new_x = x + target_v * math.cos(new_theta) * step_dt
                new_y = y + target_v * math.sin(new_theta) * step_dt

                robot.pose = (new_x, new_y, new_theta)
                robot.velocities = (target_v, target_w)

    def _update_loop(self) -> None:
        while True:
            time.sleep(self.dt)
            with self._lock:
                if not self._running:
                    break
                self.step(self.dt)
