"""Pure-Python differential-drive (unicycle) robot model.

Used as the default backend so the whole project runs WITHOUT CoppeliaSim.
Swap in coppelia_backend.CoppeliaRobot when the simulator is available.
Both expose the same interface: set_velocity(v, w), step(dt), get_state(), reset().
"""
import math
import threading

MAX_V = 1.0   # m/s
MAX_W = 2.0   # rad/s


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


class RobotSim:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.x = self.y = self.theta = 0.0
            self.v = self.w = 0.0

    def set_velocity(self, v, w):
        with self._lock:
            self.v = clamp(v, -MAX_V, MAX_V)
            self.w = clamp(w, -MAX_W, MAX_W)

    def step(self, dt):
        with self._lock:
            self.x += self.v * math.cos(self.theta) * dt
            self.y += self.v * math.sin(self.theta) * dt
            self.theta = (self.theta + self.w * dt + math.pi) % (2 * math.pi) - math.pi

    def get_state(self):
        with self._lock:
            return {"x": self.x, "y": self.y, "theta": self.theta,
                    "v": self.v, "w": self.w}
