"""CoppeliaSim backend (optional).

Setup:
  1. Install CoppeliaSim EDU (https://www.coppeliarobotics.com/)
  2. pip install coppeliasim-zmqremoteapi-client
  3. Open CoppeliaSim, add a robot, e.g. Models > robots > mobile > Pioneer p3dx
     (the default script names its joints /PioneerP3DX/leftMotor and /rightMotor).
  4. Press Play in CoppeliaSim, then run:  python server.py --backend coppelia

Interface matches RobotSim so server.py doesn't care which one it talks to.
"""
import math

MAX_V = 1.0
MAX_W = 2.0
WHEEL_RADIUS = 0.0975   # P3DX
AXLE = 0.331            # P3DX wheel separation


class CoppeliaRobot:
    def __init__(self, robot_path="/PioneerP3DX",
                 left="/PioneerP3DX/leftMotor", right="/PioneerP3DX/rightMotor"):
        from coppeliasim_zmqremoteapi_client import RemoteAPIClient
        self.client = RemoteAPIClient()
        self.sim = self.client.require("sim")
        self.robot = self.sim.getObject(robot_path)
        self.left = self.sim.getObject(left)
        self.right = self.sim.getObject(right)
        self._v = self._w = 0.0
        self._start_pose = self.sim.getObjectPose(self.robot, -1)

    def set_velocity(self, v, w):
        v = max(-MAX_V, min(MAX_V, v))
        w = max(-MAX_W, min(MAX_W, w))
        self._v, self._w = v, w
        wl = (v - w * AXLE / 2) / WHEEL_RADIUS
        wr = (v + w * AXLE / 2) / WHEEL_RADIUS
        self.sim.setJointTargetVelocity(self.left, wl)
        self.sim.setJointTargetVelocity(self.right, wr)

    def step(self, dt):
        pass  # CoppeliaSim runs its own physics while simulation is playing

    def get_state(self):
        p = self.sim.getObjectPosition(self.robot, -1)
        o = self.sim.getObjectOrientation(self.robot, -1)
        return {"x": p[0], "y": p[1], "theta": o[2], "v": self._v, "w": self._w}

    def reset(self):
        self.set_velocity(0, 0)
        self.sim.setObjectPose(self.robot, self._start_pose, -1)
