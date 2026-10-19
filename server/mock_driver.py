"""
Mock Robot Driver for headless testing and development.
Simulates basic 2D differential drive kinematics without requiring CoppeliaSim.
"""

import math
import threading
import time
from typing import Tuple
from server.driver_interface import RobotDriverInterface
from common.config import PIONEER_TRACK_WIDTH


class MockRobotDriver(RobotDriverInterface):
    """
    Simulates a Pioneer P3-DX robot in a 2D plane.
    Runs a background kinematics integration loop.
    """

    def __init__(self, dt: float = 0.05) -> None:
        self._dt = dt
        self._x = 0.0
        self._y = 0.0
        self._theta = 0.0  # radians
        self._linear_vel = 0.0  # m/s
        self._angular_vel = 0.0  # rad/s

        self._target_x: float | None = None
        self._target_y: float | None = None
        self._target_angle: float | None = None

        self._lock = threading.Lock()
        self._connected = False
        self._running = False
        self._thread: threading.Thread | None = None

    def connect(self) -> bool:
        with self._lock:
            self._connected = True
            self._running = True
            self._thread = threading.Thread(target=self._update_loop, daemon=True, name="MockSimLoop")
            self._thread.start()
            return True

    def disconnect(self) -> None:
        with self._lock:
            self._running = False
            self._connected = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)

    def set_wheel_speeds(self, left_speed: float, right_speed: float) -> None:
        with self._lock:
            # Differential kinematics: v = (vr + vl)/2, w = (vr - vl)/L
            self._linear_vel = (right_speed + left_speed) / 2.0
            self._angular_vel = (right_speed - left_speed) / PIONEER_TRACK_WIDTH

    def drive(self, speed: float) -> None:
        with self._lock:
            self._linear_vel = float(speed)
            self._angular_vel = 0.0
            self._target_x = None
            self._target_y = None
            self._target_angle = None

    def turn(self, angle_deg: float, speed: float = 0.5) -> None:
        with self._lock:
            target_rad = self._theta + math.radians(angle_deg)
            # Normalize to -pi..pi
            self._target_angle = (target_rad + math.pi) % (2 * math.pi) - math.pi
            self._linear_vel = 0.0
            self._angular_vel = speed if angle_deg >= 0 else -speed

    def goto(self, target_x: float, target_y: float) -> bool:
        with self._lock:
            self._target_x = float(target_x)
            self._target_y = float(target_y)
            self._target_angle = None
            return True

    def stop(self) -> None:
        with self._lock:
            self._linear_vel = 0.0
            self._angular_vel = 0.0
            self._target_x = None
            self._target_y = None
            self._target_angle = None

    def get_pose(self) -> Tuple[float, float, float]:
        with self._lock:
            return (self._x, self._y, self._theta)

    def get_velocities(self) -> Tuple[float, float]:
        with self._lock:
            return (self._linear_vel, self._angular_vel)

    def is_action_in_progress(self) -> bool:
        with self._lock:
            return (
                self._target_x is not None
                or self._target_angle is not None
                or abs(self._linear_vel) > 1e-4
                or abs(self._angular_vel) > 1e-4
            )

    def cancel_current_action(self) -> None:
        self.stop()

    def _update_loop(self) -> None:
        while True:
            time.sleep(self._dt)
            with self._lock:
                if not self._running:
                    break

                # Target angle control
                if self._target_angle is not None:
                    angle_err = (self._target_angle - self._theta + math.pi) % (2 * math.pi) - math.pi
                    if abs(angle_err) < 0.05:
                        self._angular_vel = 0.0
                        self._theta = self._target_angle
                        self._target_angle = None
                    else:
                        self._angular_vel = 0.5 if angle_err > 0 else -0.5

                # Target coordinates goto control
                elif self._target_x is not None and self._target_y is not None:
                    dx = self._target_x - self._x
                    dy = self._target_y - self._y
                    dist = math.hypot(dx, dy)
                    if dist < 0.05:
                        self._linear_vel = 0.0
                        self._angular_vel = 0.0
                        self._target_x = None
                        self._target_y = None
                    else:
                        desired_angle = math.atan2(dy, dx)
                        angle_err = (desired_angle - self._theta + math.pi) % (2 * math.pi) - math.pi
                        if abs(angle_err) > 0.2:
                            self._linear_vel = 0.0
                            self._angular_vel = 0.6 if angle_err > 0 else -0.6
                        else:
                            self._linear_vel = min(0.4, dist)
                            self._angular_vel = 1.0 * angle_err

                # Update 2D unicycle kinematics
                self._theta = (self._theta + self._angular_vel * self._dt + math.pi) % (2 * math.pi) - math.pi
                self._x += self._linear_vel * math.cos(self._theta) * self._dt
                self._y += self._linear_vel * math.sin(self._theta) * self._dt
