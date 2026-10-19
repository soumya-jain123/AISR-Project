"""
CoppeliaSim Robot Driver for Pioneer P3-DX via ZeroMQ Remote API.
Provides closed-loop kinematic control (drive, turn, goto, stop) and pose telemetry.
"""

import logging
import math
import threading
import time
from typing import Optional, Tuple

from common.config import (
    DEFAULT_LINEAR_SPEED,
    MAX_LINEAR_SPEED,
    PIONEER_TRACK_WIDTH,
    PIONEER_WHEEL_RADIUS,
)
from server.driver_interface import RobotDriverInterface

logger = logging.getLogger(__name__)


class CoppeliaSimDriver(RobotDriverInterface):
    """
    Direct interface to Pioneer P3-DX robot inside CoppeliaSim using the ZMQ Remote API.
    """

    def __init__(
        self,
        robot_path: str = "/PioneerP3DX",
        left_motor_path: str = "/PioneerP3DX/leftMotor",
        right_motor_path: str = "/PioneerP3DX/rightMotor",
        wheel_radius: float = PIONEER_WHEEL_RADIUS,
        track_width: float = PIONEER_TRACK_WIDTH,
    ) -> None:
        self.robot_path = robot_path
        self.left_motor_path = left_motor_path
        self.right_motor_path = right_motor_path
        self.wheel_radius = wheel_radius
        self.track_width = track_width

        self._client = None
        self._sim = None
        self._robot_handle = None
        self._left_motor = None
        self._right_motor = None

        self._lock = threading.Lock()
        self._is_connected = False
        self._action_thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()

        self._current_linear_vel = 0.0
        self._current_angular_vel = 0.0
        self._last_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def connect(self) -> bool:
        """Connect to CoppeliaSim ZMQ remote API and retrieve object handles."""
        with self._lock:
            if self._is_connected and self._sim:
                return True
        try:
            from coppeliasim_zmqremoteapi_client import RemoteAPIClient

            self._client = RemoteAPIClient()
            self._sim = self._client.require("sim")

            # Verify simulation communication
            sim_time = self._sim.getSimulationTime()
            logger.info(f"Connected to CoppeliaSim ZMQ API (sim time: {sim_time:.2f}s)")

            # Resolve object handles
            self._robot_handle = self._sim.getObject(self.robot_path)
            self._left_motor = self._sim.getObject(self.left_motor_path)
            self._right_motor = self._sim.getObject(self.right_motor_path)

            # Check and disable conflicting demo child script if present
            try:
                script = self._sim.getScript(self._sim.scripttype_childscript, self._robot_handle)
                script_text = self._sim.getScriptStringParam(script, self._sim.scriptstringparam_text)
                if "braitenberg" in script_text.lower() or "sysCall_actuation" in script_text:
                    logger.info("Disabling default demo Braitenberg child script on Pioneer P3-DX...")
                    self._sim.setScriptStringParam(
                        script,
                        self._sim.scriptstringparam_text,
                        "-- Teleoperation controlled via external Python API\nfunction sysCall_init()\nend\nfunction sysCall_actuation()\nend\nfunction sysCall_cleanup()\nend\n",
                    )
            except Exception as e:
                logger.debug(f"No child script or unable to modify script: {e}")

            # Ensure simulation is actively running
            sim_state = self._sim.getSimulationState()
            if sim_state == 0:  # simulation stopped
                logger.info("CoppeliaSim was stopped. Automatically starting simulation...")
                self._sim.startSimulation()

            self._is_connected = True
            logger.info(
                f"Successfully bound robot handles: robot={self._robot_handle}, "
                f"motors=({self._left_motor}, {self._right_motor})"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Could not connect to CoppeliaSim ZMQ API ({e}). "
                "Ensure CoppeliaSim is running with the ZMQ remote API add-on enabled."
            )
            self._is_connected = False
            return False

    def disconnect(self) -> None:
        """Cleanly disconnect and halt motors."""
        self.stop()
        self._is_connected = False
        self._client = None
        self._sim = None

    def set_wheel_speeds(self, left_rad_s: float, right_rad_s: float) -> None:
        """Directly command left and right wheel motor speeds (in rad/s)."""
        with self._lock:
            self._set_motor_speeds_internal(left_rad_s, right_rad_s)

    def _set_motor_speeds_internal(self, left_rad_s: float, right_rad_s: float) -> None:
        if self._is_connected and self._sim:
            try:
                # If simulation was stopped, automatically resume/start it
                if self._sim.getSimulationState() == 0 and (abs(left_rad_s) > 1e-3 or abs(right_rad_s) > 1e-3):
                    self._sim.startSimulation()
                self._sim.setJointTargetVelocity(self._left_motor, float(left_rad_s))
                self._sim.setJointTargetVelocity(self._right_motor, float(right_rad_s))
            except Exception as e:
                logger.error(f"Failed to set joint target velocities: {e}")

        # Compute equivalent forward & angular speeds
        vl = left_rad_s * self.wheel_radius
        vr = right_rad_s * self.wheel_radius
        self._current_linear_vel = (vr + vl) / 2.0
        self._current_angular_vel = (vr - vl) / self.track_width

    def _speeds_to_wheel_rates(self, linear_v: float, angular_w: float) -> Tuple[float, float]:
        """Differential drive inverse kinematics: (v, w) -> (left_rad/s, right_rad/s)."""
        vl = linear_v - (angular_w * self.track_width / 2.0)
        vr = linear_v + (angular_w * self.track_width / 2.0)
        left_rad_s = vl / self.wheel_radius
        right_rad_s = vr / self.wheel_radius
        return left_rad_s, right_rad_s

    def drive(self, speed: float) -> None:
        """Drive straight at speed m/s. Positive = forward, negative = backward."""
        self.cancel_current_action()
        speed = max(-MAX_LINEAR_SPEED, min(float(speed), MAX_LINEAR_SPEED))
        w_left, w_right = self._speeds_to_wheel_rates(speed, 0.0)
        self.set_wheel_speeds(w_left, w_right)

    def turn(self, angle_deg: float, speed: float = 0.5) -> None:
        """Turn in-place by angle_deg (positive left, negative right)."""
        self.cancel_current_action()
        self._cancel_event.clear()
        self._action_thread = threading.Thread(
            target=self._turn_loop, args=(float(angle_deg), float(speed)), daemon=True
        )
        self._action_thread.start()

    def _turn_loop(self, angle_deg: float, speed: float) -> None:
        _, _, start_theta = self.get_pose()
        target_theta = (start_theta + math.radians(angle_deg) + math.pi) % (2 * math.pi) - math.pi
        w_dir = 1.0 if angle_deg >= 0 else -1.0
        w_rate = abs(speed) * w_dir

        left_rate, right_rate = self._speeds_to_wheel_rates(0.0, w_rate)
        self.set_wheel_speeds(left_rate, right_rate)

        while not self._cancel_event.is_set():
            time.sleep(0.02)
            _, _, cur_theta = self.get_pose()
            err = (target_theta - cur_theta + math.pi) % (2 * math.pi) - math.pi
            if abs(err) < 0.05:  # within ~2.8 degrees
                break

        self.set_wheel_speeds(0.0, 0.0)

    def goto(self, target_x: float, target_y: float) -> bool:
        """Drive to target coordinates (x, y) using closed-loop P controller."""
        self.cancel_current_action()
        self._cancel_event.clear()
        self._action_thread = threading.Thread(
            target=self._goto_loop, args=(float(target_x), float(target_y)), daemon=True
        )
        self._action_thread.start()
        return True

    def _goto_loop(self, target_x: float, target_y: float) -> None:
        """Closed-loop proportional distance and heading controller."""
        kp_linear = 0.8
        kp_angular = 2.0
        tolerance = 0.05  # 5 cm tolerance

        while not self._cancel_event.is_set():
            cur_x, cur_y, cur_theta = self.get_pose()
            dx = target_x - cur_x
            dy = target_y - cur_y
            dist = math.hypot(dx, dy)

            if dist < tolerance:
                logger.info(f"Reached target ({target_x:.2f}, {target_y:.2f})")
                break

            target_heading = math.atan2(dy, dx)
            heading_err = (target_heading - cur_theta + math.pi) % (2 * math.pi) - math.pi

            # If heading error is large, turn in place first
            if abs(heading_err) > 0.4:
                v = 0.0
                w = max(-1.0, min(kp_angular * heading_err, 1.0))
            else:
                v = max(0.05, min(kp_linear * dist, DEFAULT_LINEAR_SPEED))
                w = max(-1.2, min(kp_angular * heading_err, 1.2))

            wl, wr = self._speeds_to_wheel_rates(v, w)
            self.set_wheel_speeds(wl, wr)
            time.sleep(0.02)

        self.set_wheel_speeds(0.0, 0.0)

    def stop(self) -> None:
        """Immediately halt all robot motion."""
        self.cancel_current_action()
        self.set_wheel_speeds(0.0, 0.0)
        self._current_linear_vel = 0.0
        self._current_angular_vel = 0.0

    def cancel_current_action(self) -> None:
        """Signal cancellation to any running background controller thread."""
        self._cancel_event.set()
        if self._action_thread and self._action_thread.is_alive():
            self._action_thread.join(timeout=0.2)
        self._action_thread = None

    def is_action_in_progress(self) -> bool:
        with self._lock:
            thread_active = self._action_thread is not None and self._action_thread.is_alive()
            has_velocity = abs(self._current_linear_vel) > 1e-4 or abs(self._current_angular_vel) > 1e-4
            return thread_active or has_velocity

    def get_pose(self) -> Tuple[float, float, float]:
        """Read 2D pose (x, y, theta_rad) from simulator."""
        if self._is_connected and self._sim:
            try:
                # -1 specifies world reference frame
                pos = self._sim.getObjectPosition(self._robot_handle, -1)
                ori = self._sim.getObjectOrientation(self._robot_handle, -1)
                # ori[2] is yaw angle (theta) around Z axis
                self._last_pose = (pos[0], pos[1], ori[2])
                return self._last_pose
            except Exception:
                # Return last known pose if simulator is transitioning
                return self._last_pose

        return self._last_pose

    def get_velocities(self) -> Tuple[float, float]:
        with self._lock:
            return (self._current_linear_vel, self._current_angular_vel)
