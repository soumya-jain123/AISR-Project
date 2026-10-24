
"""
Robot backends for networked teleoperation.
Coordinate frame: x forward, y left, theta counter-clockwise.
"""

import math
import os
import time

SENSOR_ANGLES_DEG = [90, 50, 30, 10, -10, -30, -50, -90]
SENSOR_MAX = 1.0
FRONT_IDX = (2, 3, 4, 5)
LEFT_IDX = (0, 1, 2)
RIGHT_IDX = (5, 6, 7)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def default_obstacles():
    return [
        (1.4, 0.0, 0.25),
        (2.5, 0.45, 0.25),
        (3.3, -0.45, 0.25),
    ]


class RobotBase:
    def set_velocity(self, v, w):
        raise NotImplementedError

    def get_pose(self):
        raise NotImplementedError

    def get_sensors(self):
        raise NotImplementedError

    def step(self, dt):
        pass

    def close(self):
        pass


class FakeRobot(RobotBase):
    RADIUS = 0.18

    def __init__(self, obstacles=None):
        self.x = self.y = self.th = 0.0
        self.v = self.w = 0.0
        self.obstacles = (
            default_obstacles() if obstacles is None else obstacles
        )

    def set_velocity(self, v, w):
        self.v, self.w = v, w

    def get_pose(self):
        return self.x, self.y, self.th

    def step(self, dt):
        mid = self.th + 0.5 * self.w * dt
        nx = self.x + self.v * math.cos(mid) * dt
        ny = self.y + self.v * math.sin(mid) * dt

        for cx, cy, cr in self.obstacles:
            d_new = math.hypot(nx - cx, ny - cy)
            d_old = math.hypot(self.x - cx, self.y - cy)

            if d_new < self.RADIUS + cr and d_new < d_old:
                nx, ny = self.x, self.y
                break

        self.x, self.y = nx, ny
        self.th = wrap(self.th + self.w * dt)

    def get_sensors(self):
        readings = []

        for angle in SENSOR_ANGLES_DEG:
            ang = self.th + math.radians(angle)
            dx, dy = math.cos(ang), math.sin(ang)
            ox = self.x + self.RADIUS * dx
            oy = self.y + self.RADIUS * dy
            best = SENSOR_MAX

            for cx, cy, cr in self.obstacles:
                distance = _ray_circle(
                    ox, oy, dx, dy, cx, cy, cr
                )
                if distance is not None and distance < best:
                    best = distance

            readings.append(best)

        return readings


def _ray_circle(ox, oy, dx, dy, cx, cy, cr):
    fx, fy = ox - cx, oy - cy
    b = fx * dx + fy * dy
    c = fx * fx + fy * fy - cr * cr
    disc = b * b - c

    if disc < 0:
        return None

    sq = math.sqrt(disc)

    if -b + sq < 0:
        return None

    return max(-b - sq, 0.0)


class CoppeliaRobot(RobotBase):
    """Pioneer P3DX controlled through CoppeliaSim ZeroMQ Remote API."""

    ROBOT = "/PioneerP3DX"
    LEFT_MOTOR = "/PioneerP3DX/leftMotor"
    RIGHT_MOTOR = "/PioneerP3DX/rightMotor"
    SENSOR_FMT = "/PioneerP3DX/ultrasonicSensor[{}]"

    WHEEL_RADIUS = 0.0975
    AXLE = 0.381
    FORWARD_YAW_OFFSET = 0.0
    MOTOR_TORQUE = None

    def __init__(self, host="localhost", port=23000, stepping=False,
                 motor_torque=None):
        from coppeliasim_zmqremoteapi_client import RemoteAPIClient

        self.client = RemoteAPIClient(host, port)

        try:
            self.sim = self.client.require("sim")
        except Exception:
            self.sim = self.client.getObject("sim")

        # Configure stepping BEFORE starting the simulation.
        self.stepping = stepping
        self.client.setStepping(stepping)

        self.robot = self.sim.getObject(self.ROBOT)
        self.lm = self.sim.getObject(self.LEFT_MOTOR)
        self.rm = self.sim.getObject(self.RIGHT_MOTOR)

        self.sensors = [
            self.sim.getObject(self.SENSOR_FMT.format(i))
            for i in range(8)
        ]

        if self.sim.getSimulationState() != self.sim.simulation_stopped:
            self.sim.stopSimulation()
            while (
                self.sim.getSimulationState()
                != self.sim.simulation_stopped
            ):
                time.sleep(0.05)

        self._remove_child_script()
        self.motor_torque = self._configured_motor_torque(motor_torque)
        if self.motor_torque is not None:
            self._set_motor_torque(self.motor_torque)

        self.sim.startSimulation()

        if stepping:
            # Advance a few steps to let the simulation initialize.
            for _ in range(5):
                self.client.step()
        else:
            time.sleep(0.5)

        p = self.sim.getObjectPosition(
            self.robot, self.sim.handle_world
        )
        self.x0, self.y0 = p[0], p[1]
        self.yaw0 = self._yaw()

    def _remove_child_script(self):
        script = self.sim.getScriptAssociatedWithObject(self.robot)
        if script is None or script < 0:
            return
        try:
            self.sim.removeObject(script)
        except Exception:
            self.sim.removeScript(script)

    def _configured_motor_torque(self, motor_torque):
        if motor_torque is None:
            value = os.getenv("P11_MOTOR_TORQUE")
            motor_torque = self.MOTOR_TORQUE if value is None or not value.strip() else float(value)
        if motor_torque is None:
            return None
        torque = float(motor_torque)
        if not math.isfinite(torque) or torque <= 0:
            raise ValueError("motor torque must be a finite positive value in N*m")
        return torque

    def _set_motor_torque(self, torque):
        try:
            self.sim.setJointTargetForce(self.lm, torque)
            self.sim.setJointTargetForce(self.rm, torque)
        except Exception:
            self.sim.setJointMaxForce(self.lm, torque)
            self.sim.setJointMaxForce(self.rm, torque)

    def _yaw(self):
        orientation = self.sim.getObjectOrientation(
            self.robot, self.sim.handle_world
        )
        return orientation[2] + self.FORWARD_YAW_OFFSET

    def set_velocity(self, v, w):
        """Convert linear and angular velocity to wheel speeds."""
        left_velocity = (
            v - w * self.AXLE / 2
        ) / self.WHEEL_RADIUS

        right_velocity = (
            v + w * self.AXLE / 2
        ) / self.WHEEL_RADIUS

        self.sim.setJointTargetVelocity(self.lm, left_velocity)
        self.sim.setJointTargetVelocity(self.rm, right_velocity)

    def get_pose(self):
        p = self.sim.getObjectPosition(
            self.robot, self.sim.handle_world
        )
        dx, dy = p[0] - self.x0, p[1] - self.y0

        c = math.cos(self.yaw0)
        s = math.sin(self.yaw0)

        x = c * dx + s * dy
        y = -s * dx + c * dy
        theta = wrap(self._yaw() - self.yaw0)

        return x, y, theta

    def get_sensors(self):
        readings = []

        for handle in self.sensors:
            result = self.sim.readProximitySensor(handle)

            # Standard ZeroMQ API returns a detection flag and point.
            if result[0] > 0:
                point = result[1]
                if isinstance(point, (list, tuple)):
                    distance = math.sqrt(sum(v * v for v in point))
                else:
                    distance = float(point)
                readings.append(min(distance, SENSOR_MAX))
            else:
                readings.append(SENSOR_MAX)

        return readings

    def close(self):
        try:
            self.set_velocity(0.0, 0.0)
            if self.sim.getSimulationState() != self.sim.simulation_stopped:
                self.sim.stopSimulation()
                while (
                    self.sim.getSimulationState()
                    != self.sim.simulation_stopped
                ):
                    time.sleep(0.05)
        except Exception:
            pass
