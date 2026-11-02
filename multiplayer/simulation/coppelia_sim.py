"""
CoppeliaSim Multi-Robot Simulation Adapter.
Controls multiple Pioneer P3-DX robots in a shared CoppeliaSim scene
using a single shared ZeroMQ Remote API connection with bounded timeouts.
"""

import logging
import math
import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

from common.config import (
    MAX_LINEAR_SPEED,
    PIONEER_TRACK_WIDTH,
    PIONEER_WHEEL_RADIUS,
)
from multiplayer.protocol import RobotOperatingState, RobotStatePayload
from multiplayer.simulation.base import (
    RobotConfig,
    RobotNotFoundError,
    RobotUnavailableError,
    SimulationInterface,
    SimulationNotReadyError,
)
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry, get_robot_registry

logger = logging.getLogger(__name__)

MAX_ANGULAR_SPEED = 1.5  # rad/s


class CoppeliaMultiplayerSimulation(SimulationInterface):
    """
    Adapter interfacing multiple robots inside a shared CoppeliaSim scene.
    Resolves independent handles for each robot's base and motor joints.
    """

    def __init__(
        self,
        robots: Optional[List[RobotConfig]] = None,
        host: str = "127.0.0.1",
        port: int = 23004,
        timeout_sec: float = 1.0,
        wheel_radius: float = PIONEER_WHEEL_RADIUS,
        track_width: float = PIONEER_TRACK_WIDTH,
        client: Optional[Any] = None,  # Supports dependency injection for tests
        registry: Optional[RobotRegistry] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self.wheel_radius = wheel_radius
        self.track_width = track_width
        self._lock = threading.RLock()

        if registry is not None:
            self.registry = registry
        elif robots is not None:
            self.registry = RobotRegistry()
        else:
            self.registry = get_robot_registry()

        self._injected_client = client
        self._client: Optional[Any] = None
        self._sim: Optional[Any] = None
        self._connected = False

        if robots:
            for cfg in robots:
                if not self.registry.contains(cfg.robot_id):
                    self.registry.register(cfg)

    def connect(self) -> bool:
        """
        Connect to CoppeliaSim ZMQ API and resolve handles for all registered robots.
        Fails clearly if simulation is unreachable or robot handles are missing.
        """
        if self._connected and self._sim:
            return True

        # 1. Pre-flight check (if not using an injected test client)
        if self._injected_client is None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(min(self.timeout_sec, 0.5))
                    s.connect((self.host, self.port))
            except Exception as e:
                logger.warning(f"CoppeliaSim host {self.host}:{self.port} unreachable: {e}")
                self._connected = False
                return False

        # 2. Establish ZMQ Remote API Client
        try:
            if self._injected_client is not None:
                self._client = self._injected_client
            else:
                import zmq
                from coppeliasim_zmqremoteapi_client import RemoteAPIClient

                self._client = RemoteAPIClient(host=self.host, port=self.port)
                self._client.socket.setsockopt(zmq.RCVTIMEO, int(self.timeout_sec * 1000))
                self._client.socket.setsockopt(zmq.SNDTIMEO, int(self.timeout_sec * 1000))

            self._sim = self._client.require("sim")

            # Check responsiveness
            _ = self._sim.getSimulationTime()

            # Ensure simulation is actively running
            if self._sim.getSimulationState() == 0:
                logger.info("Starting simulation in CoppeliaSim...")
                self._sim.startSimulation()

        except Exception as e:
            logger.error(f"Failed to initialize CoppeliaSim Remote API: {e}")
            self._connected = False
            self._sim = None
            return False

        # 3. Resolve per-robot handles
        resolved_any = False
        for robot in self.registry.get_all_instances():
            base_path, left_path, right_path = robot.config.resolve_coppelia_paths()
            try:
                base_h = self._sim.getObject(base_path)
                left_h = self._sim.getObject(left_path)
                right_h = self._sim.getObject(right_path)

                robot.metadata["handles"] = {
                    "base": base_h,
                    "left_motor": left_h,
                    "right_motor": right_h,
                    "base_path": base_path,
                    "left_path": left_path,
                    "right_path": right_path,
                }
                robot.is_available = True
                resolved_any = True
                logger.info(
                    f"Resolved robot '{robot.robot_id}' -> base={base_h}, "
                    f"left={left_h}, right={right_h}"
                )
            except Exception as err:
                robot.is_available = False
                robot.metadata["unavailable_reason"] = f"Failed resolving paths: {err}"
                logger.warning(
                    f"Failed resolving handles for robot '{robot.robot_id}' "
                    f"at ({base_path}, {left_path}, {right_path}): {err}"
                )

        # Simulation backend is connected; individual robot availability is tracked per robot
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Halt motors and close simulation connection."""
        if self._connected:
            try:
                self.stop_all(reason="Simulation disconnecting")
            except Exception:
                pass
        self._connected = False
        self._sim = None
        self._client = None

    def is_ready(self) -> bool:
        if not self._connected or not self._sim:
            return False
        try:
            return self._sim.getSimulationState() != 0
        except Exception:
            return False

    def get_registered_robot_ids(self) -> List[str]:
        return self.registry.get_all_ids()

    def _speeds_to_wheel_rates(self, linear_v: float, angular_w: float) -> Tuple[float, float]:
        """Differential drive inverse kinematics: (v, w) -> (left_rad/s, right_rad/s)."""
        vl = linear_v - (angular_w * self.track_width / 2.0)
        vr = linear_v + (angular_w * self.track_width / 2.0)
        left_rad_s = vl / self.wheel_radius
        right_rad_s = vr / self.wheel_radius
        return left_rad_s, right_rad_s

    def apply_control(self, robot_id: str, throttle: float, steering: float) -> bool:
        """
        Send wheel speed commands directly to the targeted robot's motors in CoppeliaSim.
        Guarantees isolation: command for Robot A never targets Robot B.
        """
        with self._lock:
            if not self._connected or not self._sim:
                raise SimulationNotReadyError("coppelia", "Simulation not connected")

            robot = self.registry.require(robot_id)
            if not robot.is_available:
                raise RobotUnavailableError(robot_id, robot.metadata.get("unavailable_reason", "Not available"))

            if robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
                return False

            handles = robot.metadata.get("handles", {})
            left_h = handles.get("left_motor")
            right_h = handles.get("right_motor")
            if left_h is None or right_h is None:
                raise RobotUnavailableError(robot_id, "Missing motor handles")

            clamped_throttle = max(-1.0, min(float(throttle), 1.0))
            clamped_steering = max(-1.0, min(float(steering), 1.0))

            target_v = clamped_throttle * MAX_LINEAR_SPEED
            target_w = clamped_steering * MAX_ANGULAR_SPEED
            wl, wr = self._speeds_to_wheel_rates(target_v, target_w)

            try:
                self._sim.setJointTargetVelocity(left_h, float(wl))
                self._sim.setJointTargetVelocity(right_h, float(wr))

                robot.velocities = (target_v, target_w)
                if abs(target_v) > 1e-4 or abs(target_w) > 1e-4:
                    robot.operating_state = RobotOperatingState.MOVING
                else:
                    robot.operating_state = RobotOperatingState.IDLE
                return True

            except Exception as e:
                logger.error(f"Error commanding robot '{robot_id}': {e}")
                robot.is_available = False
                robot.metadata["unavailable_reason"] = str(e)
                raise RobotUnavailableError(robot_id, str(e)) from e

    def stop_robot(self, robot_id: str, reason: str = "") -> bool:
        with self._lock:
            if not self._connected or not self._sim:
                return False

            robot = self.registry.require(robot_id)
            handles = robot.metadata.get("handles", {})
            left_h = handles.get("left_motor")
            right_h = handles.get("right_motor")

            if left_h is not None and right_h is not None:
                try:
                    self._sim.setJointTargetVelocity(left_h, 0.0)
                    self._sim.setJointTargetVelocity(right_h, 0.0)
                except Exception as e:
                    logger.warning(f"Error stopping robot '{robot_id}': {e}")

            robot.velocities = (0.0, 0.0)
            if robot.operating_state != RobotOperatingState.ESTOP_LATCHED:
                robot.operating_state = RobotOperatingState.IDLE
            return True

    def stop_all(self, reason: str = "") -> None:
        with self._lock:
            for robot_id in self.registry.get_all_ids():
                self.stop_robot(robot_id, reason=reason)

    def reset_robot(self, robot_id: str, pose: Optional[Tuple[float, float, float]] = None) -> bool:
        """
        Teleport robot back to starting position in CoppeliaSim scene and halt motion.
        """
        with self._lock:
            if not self._connected or not self._sim:
                raise SimulationNotReadyError("coppelia", "Simulation not connected")

            robot = self.registry.require(robot_id)
            handles = robot.metadata.get("handles", {})
            base_h = handles.get("base")
            if base_h is None:
                raise RobotUnavailableError(robot_id, "Missing base handle")

            target_pose = pose if pose is not None else robot.config.initial_pose
            self.stop_robot(robot_id, reason="Resetting pose")

            try:
                # CoppeliaSim coordinates: [x, y, z] and [alpha, beta, gamma] (radians)
                self._sim.setObjectPosition(base_h, -1, [target_pose[0], target_pose[1], 0.1])
                self._sim.setObjectOrientation(base_h, -1, [0.0, 0.0, target_pose[2]])
                robot.pose = target_pose
                robot.velocities = (0.0, 0.0)
                robot.operating_state = RobotOperatingState.IDLE
                return True
            except Exception as e:
                logger.error(f"Failed to reset pose for robot '{robot_id}': {e}")
                raise RobotUnavailableError(robot_id, f"Pose reset failed: {e}") from e

    def get_robot_state(self, robot_id: str) -> RobotStatePayload:
        with self._lock:
            if not self._connected or not self._sim:
                raise SimulationNotReadyError("coppelia", "Simulation not connected")

            robot = self.registry.require(robot_id)
            if not robot.is_available:
                raise RobotUnavailableError(robot_id, robot.metadata.get("unavailable_reason", "Not available"))

            handles = robot.metadata.get("handles", {})
            base_h = handles.get("base")
            if base_h is None:
                raise RobotUnavailableError(robot_id, "Missing base handle")

            try:
                pos = self._sim.getObjectPosition(base_h, -1)
                ori = self._sim.getObjectOrientation(base_h, -1)
                robot.pose = (pos[0], pos[1], ori[2])
                return robot.to_payload()
            except Exception as e:
                logger.warning(f"Failed reading pose for robot '{robot_id}': {e}")
                robot.is_available = False
                robot.metadata["unavailable_reason"] = str(e)
                raise RobotUnavailableError(robot_id, f"Pose read error: {e}") from e
