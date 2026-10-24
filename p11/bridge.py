"""Robot bridge: receives commands from the network, runs the control loop and
the LOCAL safety layer, talks to the robot back-end.

Safety layer (runs on the robot side, so it still works when the network is bad):
  1. Watchdog      - no message for `watchdog_s` while moving -> stop.
  2. Obstacle stop - forward speed -> 0 when front distance <= stop_dist,
                     scaled down between stop_dist and slow_dist.
  3. E-stop        - latched until 'reset'.
  4. Adaptive speed (novelty) - max speed shrinks when the operator-reported
                     round-trip time is high.
"""
import collections
import csv
import math
import threading
import time

from .protocol import MAX_V, MAX_W, validate
from .robots import FRONT_IDX, wrap

COLLISION_GAP = 0.05   # m: sensor gap below this counts as a collision event


def clip(x, lo, hi):
    return max(lo, min(hi, x))


class Bridge:
    def __init__(self, robot_factory, safety=True, adaptive=True, log_path=None,
                 watchdog_s=0.5, stop_dist=0.30, slow_dist=0.70, rate_hz=20):
        self._factory = robot_factory
        self.safety, self.adaptive = safety, adaptive
        self.watchdog_s, self.stop_dist, self.slow_dist = watchdog_s, stop_dist, slow_dist
        self.dt_nom = 1.0 / rate_hz
        self.lock = threading.RLock()
        self.mode = "idle"              # idle | velocity | turn | goto
        self.cmd_v = self.cmd_w = 0.0
        self.default_speed = 0.3
        self.estop = False
        self.target = None
        self.turn_rel = 0.0
        self.mode_t0 = 0.0
        self.turn_started_at = None
        self.turn_progress_at = None
        self.turn_best_error = None
        self._out_v = self._out_w = 0.0
        self.note = ""
        self.last_heard = None
        self.rtt_ms = None
        self.collisions = 0
        self.obstacle_stops = 0
        self.watchdog_trips = 0
        self._in_collision = False
        self._blocked = False
        self.tel = {}
        self._dedupe = collections.OrderedDict()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._log_f = self._log = None
        if log_path:
            self._log_f = open(log_path, "w", newline="")
            self._log = csv.writer(self._log_f)
            self._log.writerow(["id", "client", "cmd", "args", "t_sent",
                                "t_recv", "t_exec", "ok", "msg"])

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(60)
        if self._error:
            raise RuntimeError(f"robot back-end failed to start: {self._error}")

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        if self._log_f:
            self._log_f.close()

    def snapshot(self):
        with self.lock:
            return dict(self.tel)

    # ------------------------------------------------------------- commands
    def handle(self, msg):
        """Called by the network layer for every incoming message."""
        t_recv = time.time()
        mid, cmd = msg.get("id"), str(msg.get("cmd", "")).lower()
        with self.lock:
            self.last_heard = time.monotonic()
            try:
                if msg.get("rtt_ms") is not None:
                    self.rtt_ms = float(msg["rtt_ms"])
            except (TypeError, ValueError):
                pass
            key = (msg.get("client"), mid)
            if cmd != "ping" and key in self._dedupe:   # retry of a command we already ran
                cached = dict(self._dedupe[key])
                cached["telemetry"] = dict(self.tel)
                cached["dup"] = True
                return cached
            try:
                args = validate(cmd, msg.get("args", []))
                ok, text = self._apply(cmd, args)
            except ValueError as e:
                args, ok, text = msg.get("args", []), False, str(e)
            reply = {"id": mid, "ok": ok, "msg": text, "t_recv": t_recv,
                     "t_exec": time.time(), "telemetry": dict(self.tel)}
            if cmd != "ping":
                self._dedupe[key] = reply
                while len(self._dedupe) > 500:
                    self._dedupe.popitem(last=False)
                if self._log:
                    self._log.writerow([mid, msg.get("client"), cmd, args,
                                        msg.get("t_sent"), reply["t_recv"],
                                        reply["t_exec"], ok, text])
                    self._log_f.flush()
        return reply

    def _to_idle(self):
        self.mode, self.cmd_v, self.cmd_w, self.target = "idle", 0.0, 0.0, None
        self.turn_started_at = self.turn_progress_at = self.turn_best_error = None

    def _apply(self, cmd, a):
        if cmd == "ping":
            return True, "pong"
        if cmd == "estop":
            self.estop = True
            self._to_idle()
            return True, "EMERGENCY STOP latched (send 'reset' to clear)"
        if cmd == "reset":
            self.estop = False
            return True, "emergency stop cleared"
        if cmd == "safety":
            self.safety = (a[0] == "on")
            return True, f"safety layer {a[0]}"
        if cmd == "speed":
            self.default_speed = a[0]
            return True, f"default speed = {a[0]} m/s"
        if cmd in ("status", "sensors"):
            return True, cmd
        if cmd == "stop":
            self._to_idle()
            return True, "stopped"
        if self.estop:
            return False, "rejected: emergency stop is latched, send 'reset'"
        if cmd in ("forward", "back"):
            v = a[0] if a else self.default_speed
            return self._velocity(v if cmd == "forward" else -v, 0.0)
        if cmd == "drive":
            return self._velocity(a[0], a[1])
        if cmd in ("left", "right"):
            rad = math.radians(a[0]) * (1 if cmd == "left" else -1)
            self.mode, self.turn_rel, self.target = "turn", rad, None
            self.turn_started_at = self.turn_progress_at = time.monotonic()
            self.turn_best_error = None
            self.cmd_v = self.cmd_w = 0.0
            return True, f"turning {cmd} {a[0]:g} deg"
        if cmd == "goto":
            self.mode, self.target, self.mode_t0 = "goto", (a[0], a[1]), time.monotonic()
            return True, f"going to ({a[0]:g}, {a[1]:g})"
        return False, "unhandled command"

    def _velocity(self, v, w):
        self.mode, self.cmd_v, self.cmd_w, self.target = "velocity", v, w, None
        return True, f"drive v={v:g} m/s w={w:g} rad/s"

    # --------------------------------------------------------- control loop
    def _run(self):
        try:
            robot = self._factory()
        except Exception as e:                       # report to start()
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        last = time.monotonic()
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                dt, last = min(t0 - last, 0.2), t0
                self._tick(robot, dt)
                rest = self.dt_nom - (time.monotonic() - t0)
                if rest > 0:
                    time.sleep(rest)
        finally:
            try:
                robot.set_velocity(0.0, 0.0)
            finally:
                robot.close()

    def _controller(self, x, y, th):
        m = self.mode
        if m == "idle":
            return 0.0, 0.0
        if m == "velocity":
            return self.cmd_v, self.cmd_w
        if m == "turn":
            if self.target is None:
                self.target = wrap(th + self.turn_rel)
            err = wrap(self.target - th)
            if abs(err) < 0.03:
                self._to_idle()
                self.note = "turn done"
                return 0.0, 0.0
            now = time.monotonic()
            if self.turn_started_at is None:
                self.turn_started_at = now
            if self.turn_progress_at is None or self.turn_best_error is None:
                self.turn_progress_at = now
                self.turn_best_error = abs(err)
            if now - self.turn_started_at > 20.0:
                self._to_idle()
                self.note = "turn failed: heading not converging"
                return 0.0, 0.0
            if abs(err) <= self.turn_best_error - 0.05:
                self.turn_best_error = abs(err)
                self.turn_progress_at = now
            elif now - self.turn_progress_at >= 3.0:
                self._to_idle()
                self.note = "turn failed: heading not converging"
                return 0.0, 0.0
            w = clip(1.2 * err, -0.6, 0.6)
            return 0.0, math.copysign(max(abs(w), 0.15), w)
        if m == "goto":
            gx, gy = self.target
            dx, dy = gx - x, gy - y
            d = math.hypot(dx, dy)
            if d < 0.08:
                self._to_idle()
                self.note = "goto done"
                return 0.0, 0.0
            if time.monotonic() - self.mode_t0 > 60:
                self._to_idle()
                self.note = "goto timeout"
                return 0.0, 0.0
            err = wrap(math.atan2(dy, dx) - th)
            w = clip(2.0 * err, -1.2, 1.2)
            if abs(err) > 0.6:
                return 0.0, w
            v = max(min(self.default_speed, 0.8 * d) * max(0.2, math.cos(err)), 0.06)
            return v, w
        return 0.0, 0.0

    def _tick(self, robot, dt):
        x, y, th = robot.get_pose()
        sens = robot.get_sensors()
        front = min(sens[i] for i in FRONT_IDX)
        gap = min(sens)
        with self.lock:
            v, w = self._controller(x, y, th)
            now = time.monotonic()
            intent = self.mode in ("turn", "goto") or (
                self.mode == "velocity" and (self.cmd_v or self.cmd_w))
            if self.last_heard is not None and intent and \
                    now - self.last_heard > self.watchdog_s:
                self._to_idle()
                self.watchdog_trips += 1
                self.note = "watchdog stop"
                v = w = 0.0
            vmax = MAX_V
            if self.adaptive and self.rtt_ms is not None:
                vmax = MAX_V * clip(1.0 - (self.rtt_ms - 150.0) / 800.0, 0.3, 1.0)
            v = clip(v, -vmax, vmax)
            w = clip(w, -MAX_W, MAX_W)
            blocked = False
            if self.safety and v > 0:
                if front <= self.stop_dist:
                    v = 0.0
                elif front < self.slow_dist:
                    v *= (front - self.stop_dist) / (self.slow_dist - self.stop_dist)
                blocked = v < 0.02          # safety layer has effectively stopped the robot
            if blocked and not self._blocked:
                self.obstacle_stops += 1
                self.note = "obstacle stop"
            self._blocked = blocked
            if gap < COLLISION_GAP and not self._in_collision:
                self.collisions += 1
                self._in_collision = True
            elif gap > COLLISION_GAP + 0.03:
                self._in_collision = False
            vmax_eff = vmax
            if self.estop:
                v = w = self._out_v = self._out_w = 0.0
            else:
                v = self._slew(self._out_v, v, 0.6, 1.5, dt)
                w = self._slew(self._out_w, w, 3.0, 6.0, dt)
                self._out_v, self._out_w = v, w
        robot.set_velocity(v, w)
        robot.step(dt)
        with self.lock:
            self.tel = {
                "t": round(time.time(), 3), "x": round(x, 3), "y": round(y, 3),
                "theta_deg": round(math.degrees(th), 1), "v": round(v, 3),
                "w": round(w, 3), "mode": self.mode, "safety": self.safety,
                "adaptive": self.adaptive, "estop": self.estop,
                "front_min": round(front, 3), "min_gap": round(gap, 3),
                "sensors": [round(s, 3) for s in sens],
                "collisions": self.collisions, "obstacle_stops": self.obstacle_stops,
                "watchdog_trips": self.watchdog_trips,
                "rtt_ms": None if self.rtt_ms is None else round(self.rtt_ms, 1),
                "vmax_eff": round(vmax_eff, 3), "note": self.note}

    @staticmethod
    def _slew(current, target, acceleration, deceleration, dt):
        if dt <= 0:
            return current
        if current * target < 0:
            target = 0.0
        rate = deceleration if abs(target) < abs(current) else acceleration
        max_change = rate * dt
        return current + clip(target - current, -max_change, max_change)
