"""Robot Server: TCP commands in, a robot in CoppeliaSim moves, feedback out.

Protocol: one JSON object per line, every message has "ts" (time.time() when sent).
Run:  python server.py [--port 5555] [--robot pioneer]
"""
import argparse
import json
import os
import math
import socket
import threading
import time
import traceback

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

TURN_W = 0.8            # rad/s used by left/right
MAX_W = 2.0             # rad/s; much faster spins tip the robot over
HEARTBEAT_TIMEOUT = 1.5 # s
LOOP_DT = 0.05          # control loop period (s)
FEEDBACK_DT = 0.1       # feedback period (s)
GOTO_TOL = 0.05         # m
BRAKE_T = 0.6           # s; robot coasts ~0.5 m from 1.2 m/s, so check where it will be this far ahead
SIM_TIMEOUT = 3.0       # s without a reply from CoppeliaSim = assume it crashed
CAMERA_FILL = 0.4       # after a robot switch, zoom so the robot fills this share of the view
EDGE_GAP = 0.15         # extra gap (m) kept between the robot's body and the floor edge

# Robots the operator can switch between. All are two-wheeled (differential drive), so they share one
# command set. radius/axle were measured by driving each model in CoppeliaSim 4.10.
# max_v is a sensible top speed for each robot; tune freely.
# ponytail: only differential-drive robots; an arm would need its own command set and controller.
ROBOTS = {
    "pioneer":  dict(name="Pioneer P3-DX", model="pioneer p3dx.ttm", base="/PioneerP3DX",
                     left="/PioneerP3DX/leftMotor", right="/PioneerP3DX/rightMotor",
                     radius=0.0952, axle=0.364, max_v=1.2),
    "epuck":    dict(name="e-puck", model="e-puck.ttm", base="/ePuck",
                     left="/ePuck/leftJoint", right="/ePuck/rightJoint",
                     radius=0.0212, axle=0.060, max_v=0.13),
    "dr12":     dict(name="dr12", model="dr12.ttm", base="/dr12",
                     left="/dr12/leftJoint_", right="/dr12/rightJoint_",
                     radius=0.0430, axle=0.172, max_v=0.4),
    "dr20":     dict(name="dr20", model="dr20.ttm", base="/dr20",
                     left="/dr20/leftWheelJoint_", right="/dr20/rightWheelJoint_",
                     radius=0.0417, axle=0.252, max_v=0.5),
    "linebot":  dict(name="Line follower", model="line follower.ttm", base="/LineTracer",
                     left="/LineTracer/DynamicLeftJoint", right="/LineTracer/DynamicRightJoint",
                     radius=0.0270, axle=0.119, max_v=0.3),
}

IDLE, MOVING, COMM_LOST, ESTOP = "IDLE", "MOVING", "COMM_LOST", "ESTOP_LATCHED"


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


class Robot:
    """Thin wrapper over the sim for one two-wheeled robot at a time.
    Every sim call goes through one lock (the ZMQ client is not thread-safe)."""

    def __init__(self, key=None):
        self.lock = threading.Lock()
        self.sim = RemoteAPIClient().require("sim")
        self.models = self.sim.getStringParam(self.sim.stringparam_resourcesdir) + "/models/robots/mobile/"
        self.stop_sim()
        # Use a robot already in the scene if there is one (and none was asked for), else load one.
        found = [k for k, r in ROBOTS.items() if self.find(r["base"]) is not None]
        if key is None and found:
            self.attach(found[0])
        else:
            self.load(key or "pioneer")
        self.start_sim()

    def find(self, path):
        try:
            return self.sim.getObject(path)
        except Exception:
            return None

    def stop_sim(self):
        self.sim.stopSimulation()
        while self.sim.getSimulationState() != self.sim.simulation_stopped:
            time.sleep(0.05)

    def start_sim(self):
        # Real-time mode, otherwise CoppeliaSim runs several times faster than the wall clock our timings use.
        self.sim.setBoolParam(self.sim.boolparam_realtime_simulation, True)
        self.sim.startSimulation()

    def attach(self, key):
        """Take control of robot `key`, which must already be in the scene (simulation stopped)."""
        self.key, self.spec = key, ROBOTS[key]
        self.body = self.sim.getObject(self.spec["base"])
        self.left = self.sim.getObject(self.spec["left"])
        self.right = self.sim.getObject(self.spec["right"])
        # Some model files are saved with the wheels already spinning (the e-puck at ~0.05 m/s), so zero them.
        self.sim.setJointTargetVelocity(self.left, 0)
        self.sim.setJointTargetVelocity(self.right, 0)
        # Models ship with their own behaviour scripts (obstacle avoidance etc.) that would fight our commands.
        for o in self.sim.getObjectsInTree(self.body):
            if self.sim.getObjectType(o) == self.sim.sceneobject_script:
                self.sim.removeObjects([o])
        # Some models' base frame isn't z-up, so remember which local axis points up for the tip-over check.
        m = self.sim.getObjectMatrix(self.body, -1)
        self.up = max(range(3), key=lambda i: abs(m[8 + i]))
        self.up_sign = 1 if m[8 + self.up] > 0 else -1
        # Safe area = floor minus the robot's size and a gap. Falls back to the default 5x5 m floor.
        size = max(abs(self.sim.getObjectFloatParam(self.body, p)) for p in (
            self.sim.objfloatparam_modelbbox_min_x, self.sim.objfloatparam_modelbbox_max_x,
            self.sim.objfloatparam_modelbbox_min_y, self.sim.objfloatparam_modelbbox_max_y,
            self.sim.objfloatparam_modelbbox_min_z, self.sim.objfloatparam_modelbbox_max_z))
        margin = size + EDGE_GAP
        try:
            box = self.sim.getObject("/Floor/box")
            cx, cy, _ = self.sim.getObjectPosition(box, -1)
            hx = self.sim.getObjectFloatParam(box, self.sim.objfloatparam_objbbox_max_x)
            hy = self.sim.getObjectFloatParam(box, self.sim.objfloatparam_objbbox_max_y)
        except Exception:
            cx, cy, hx, hy = 0, 0, 2.5, 2.5
        self.area = (cx - hx + margin, cx + hx - margin, cy - hy + margin, cy + hy - margin)

    def load(self, key):
        """Replace the current robot with a fresh `key` from CoppeliaSim's model library (simulation stopped)."""
        if getattr(self, "body", None) is not None:
            self.sim.removeModel(self.body)
        self.sim.loadModel(self.models + ROBOTS[key]["model"])
        self.attach(key)

    def switch(self, key):
        with self.lock:
            self.stop_sim()
            self.load(key)
            self.start_sim()
            # Zoom the main view onto the new robot; small ones like the e-puck are invisible from afar.
            # CAMERA_FILL = share of the view the robot takes up.
            try:
                self.sim.cameraFitToView(0, [self.body], 0, CAMERA_FILL)
            except Exception:
                pass   # headless CoppeliaSim has no view; driving still works

    def inside(self, x, y):
        x0, x1, y0, y1 = self.area
        return x0 <= x <= x1 and y0 <= y <= y1

    def heading_out(self, x, y, vx, vy):
        """True if the robot is past the safe area and still moving further out."""
        x0, x1, y0, y1 = self.area
        return (x < x0 and vx < -0.01) or (x > x1 and vx > 0.01) or (y < y0 and vy < -0.01) or (y > y1 and vy > 0.01)

    def restart(self):
        """Stop and start again: the robot goes back to where it was when the simulation started."""
        with self.lock:
            self.stop_sim()
            self.start_sim()

    def drive(self, v, w):
        """v: forward m/s, w: turn rad/s (positive = left)."""
        r, axle, max_v = self.spec["radius"], self.spec["axle"], self.spec["max_v"]
        v = max(-max_v, min(max_v, v))
        w = max(-MAX_W, min(MAX_W, w))
        with self.lock:
            self.sim.setJointTargetVelocity(self.left, (v - w * axle / 2) / r)
            self.sim.setJointTargetVelocity(self.right, (v + w * axle / 2) / r)

    def pose(self):
        """(x, y, z, yaw, vx, vy, tipped, running) in world frame.
        Position is the point between the wheels; heading is perpendicular to the wheel axle,
        which works for every model whatever its base frame looks like."""
        with self.lock:
            a = self.sim.getObjectPosition(self.left, -1)
            b = self.sim.getObjectPosition(self.right, -1)
            m = self.sim.getObjectMatrix(self.body, -1)
            lin, _ = self.sim.getObjectVelocity(self.body)
            running = self.sim.getSimulationState() != self.sim.simulation_stopped
        x, y, z = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2
        yaw = math.atan2(b[0] - a[0], a[1] - b[1])
        return x, y, z, yaw, lin[0], lin[1], self.up_sign * m[8 + self.up] < 0.5, running


def at(x, y):
    return f"({x:.2f}, {y:.2f})"


def facing(yaw):
    return f"facing {round(math.degrees(yaw)) + 0}°"   # + 0 turns -0 into 0


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


class Server:
    """States: IDLE, MOVING, COMM_LOST, ESTOP_LATCHED. They are logged here; the operator gets plain sentences."""

    def __init__(self, robot):
        self.robot = robot
        self.lock = threading.Lock()   # guards everything below
        self.send_lock = threading.Lock()
        self.state = IDLE
        self.motion = None             # ("vel", v, w) | ("turn", v, w, target_yaw) | ("goto", x, y)
        self.pose = (0, 0, 0)          # latest (x, y, yaw)
        self.fell = None               # None, or why the robot can't move ("fell off the floor", "tipped over")
        self.sim_stopped = False
        self.last_hb = float("inf")    # no heartbeat watchdog until an operator first connects
        self.last_tick = time.time()   # last time the control loop got an answer from CoppeliaSim
        self.conn = None
        self.estop_ts = None           # when estop was handled
        self.estop_stopped_ts = None   # when robot actually came to rest after estop
        self.run = 0                   # bumped on every sim restart, so clients can drop stale feedback

    # ---- outgoing ----
    def send(self, msg):
        msg["ts"] = time.time()
        conn = self.conn
        if conn:
            try:
                with self.send_lock:
                    conn.sendall((json.dumps(msg) + "\n").encode())
            except OSError:
                pass

    def say(self, text, code=None):
        """Tell the operator something. `code` is a machine tag for scripts like study.py."""
        log("-> operator: " + text)
        self.send({"type": "msg", "text": text, "code": code})

    def here(self):
        x, y, yaw = self.pose
        return f"{at(x, y)}, {facing(yaw)}"

    def set_state(self, s):
        if s != self.state:
            log(f"state {self.state} -> {s}")
            self.state = s

    def halt(self, new_state):
        self.motion = None
        self.robot.drive(0, 0)
        self.set_state(new_state)

    def start_motion(self, motion, text):
        self.motion = motion
        self.set_state(MOVING)
        self.say(text)

    # ---- incoming ----
    def handle(self, msg):
        t = msg.get("type")
        with self.lock:
            if t == "hb":
                self.last_hb = time.time()
                if self.state == COMM_LOST:
                    self.set_state(IDLE)   # stays still until a new command
                    self.say("Connection is back. The robot is still stopped; send a new command to move it.")
                return
            if t == "cmd":
                log(f"<- operator: {msg.get('cmd')}")
                self.command(msg.get("cmd", "").split())

    def command(self, parts):
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        if name == "robot":   # takes a name, not numbers
            return self.robot_command(args)
        try:
            nums = [float(a) for a in args]
        except ValueError:
            return self.say(f"'{' '.join(parts)}' needs numbers, e.g. 'forward 0.3'.")

        # Safety commands first: valid in every state.
        if name == "estop":
            if self.state == ESTOP:
                return self.say("Emergency stop is already on. Type 'reset' to release it.")
            self.estop_ts, self.estop_stopped_ts = time.time(), None
            self.halt(ESTOP)
            return self.say(f"EMERGENCY STOP. The robot is stopped and locked at {self.here()}. "
                            "Movement commands will be ignored until you type 'reset'.", "estop")
        if name == "reset":
            if self.state != ESTOP:
                return self.say("Nothing to reset: the emergency stop is not on.")
            self.halt(IDLE)
            return self.say("Emergency stop released. The robot is unlocked but stays still until you send a new command.")
        if name == "stop":
            if self.state == ESTOP:
                return self.say("The robot is already stopped and locked by the emergency stop.")
            was_moving = self.state == MOVING
            self.halt(IDLE)
            return self.say(f"Robot stopped at {self.here()}." if was_moving else "The robot is already stopped.")
        if name == "status":
            return self.say(self.status())
        if name == "restart":
            self.motion = None
            self.robot.restart()
            self.last_tick = time.time()
            self.estop_ts = self.estop_stopped_ts = None
            self.fell, self.sim_stopped = None, False
            self.run += 1
            self.set_state(IDLE)
            x, y, _, yaw, *_ = self.robot.pose()
            self.pose = (x, y, yaw)
            return self.say(f"Simulation restarted. The robot is back at its start position {self.here()}.")

        # Movement commands.
        usage = {"forward": "forward 0.3", "back": "back 0.3", "left": "left 90", "right": "right 90",
                 "goto": "goto 1 0.5", "drive": "drive 0.3 0.5"}
        if name not in usage:
            return self.say(f"Unknown command '{name}'. Type 'help' to see the commands.")
        if len(nums) != len(usage[name].split()) - 1:
            return self.say(f"Usage: {usage[name]}")
        if self.state == ESTOP:
            return self.say(f"Ignored '{name}': the robot is locked by the emergency stop. Type 'reset' first.")
        if self.state == COMM_LOST:
            return self.say(f"Ignored '{name}': the connection just dropped. Try again.")
        if self.fell:
            return self.say(f"The robot {self.fell} and can't move. Type 'restart' to put it back at the start.")
        if self.sim_stopped:
            return self.say("The simulation is stopped in CoppeliaSim. Type 'restart' to start it again.")

        max_v = self.robot.spec["max_v"]
        cur_v = self.motion[1] if self.motion and self.motion[0] in ("vel", "turn") else 0.0
        if name in ("forward", "back"):
            v = min(abs(nums[0]), max_v)
            clip = f" (you asked for {abs(nums[0]):g} m/s; {max_v} m/s is the {self.robot.spec['name']}'s top speed)" \
                if abs(nums[0]) > max_v else ""
            if name == "forward":
                self.start_motion(("vel", v, 0.0), f"Moving forward at {v:.2f} m/s{clip}.")
            else:
                self.start_motion(("vel", -v, 0.0), f"Reversing at {v:.2f} m/s{clip}.")
        elif name in ("left", "right"):
            # Keep the current speed: turning while driving makes an arc, then carries on straight.
            sign = 1 if name == "left" else -1
            target = wrap(self.pose[2] + sign * math.radians(abs(nums[0])))
            how = f"while moving at {abs(cur_v):.2f} m/s" if cur_v else "on the spot"
            self.start_motion(("turn", cur_v, sign * TURN_W, target), f"Turning {name} {abs(nums[0]):g}° {how}.")
        elif name == "goto":
            tx, ty = nums
            if not self.robot.inside(tx, ty):
                x0, x1, y0, y1 = self.robot.area
                return self.say(f"Can't go to {at(tx, ty)}: it is outside the safe area "
                                f"(x {x0:.1f} to {x1:.1f}, y {y0:.1f} to {y1:.1f}).")
            d = math.hypot(tx - self.pose[0], ty - self.pose[1])
            self.start_motion(("goto", tx, ty), f"Driving to {at(tx, ty)}, {d:.2f} m away.")
        elif name == "drive":   # raw velocity, also used by study.py
            v, w = nums
            vc, wc = max(-max_v, min(max_v, v)), max(-MAX_W, min(MAX_W, w))
            clip = f" (limited from {v:g} m/s, {w:g} rad/s: the robot tops out at {max_v} m/s and {MAX_W} rad/s)" \
                if (vc, wc) != (v, w) else ""
            turn = "straight" if wc == 0 else f"turning {'left' if wc > 0 else 'right'} at {abs(wc):.2f} rad/s"
            self.start_motion(("vel", vc, wc), f"Driving at {vc:.2f} m/s, {turn}{clip}.")

    def robot_command(self, args):
        if not args:
            names = ", ".join(f"{k} ({r['name']})" for k, r in ROBOTS.items())
            return self.say(f"You are controlling the {self.robot.spec['name']}. Available robots: {names}. "
                            "Type 'robot <name>' to switch, e.g. 'robot epuck'.")
        key = args[0].lower()
        if key not in ROBOTS:
            return self.say(f"There is no robot called '{key}'. Type 'robot' to see the list.")
        if key == self.robot.key:
            return self.say(f"You are already controlling the {ROBOTS[key]['name']}.")
        if self.state == ESTOP:
            return self.say("The emergency stop is on. Type 'reset' before switching robots.")
        self.halt(IDLE)
        self.robot.switch(key)
        self.last_tick = time.time()
        self.estop_ts = self.estop_stopped_ts = None
        self.fell, self.sim_stopped = None, False
        self.run += 1
        x, y, _, yaw, *_ = self.robot.pose()
        self.pose = (x, y, yaw)
        self.say(f"Switched to the {self.robot.spec['name']}. It is stopped at {self.here()}.")
        self.send_help()

    def send_help(self):
        """The command list depends on the robot, so the server tells the client what to show."""
        r = self.robot.spec
        lines = [
            f"forward 0.1      drive forward at 0.1 m/s (top speed {r['max_v']})",
            "back 0.1         reverse at 0.1 m/s",
            "left 90          turn left 90 degrees (keeps driving if already moving)",
            "right 90         turn right 90 degrees",
            "goto 1 0.5       drive to position x=1, y=0.5 (metres)",
            f"drive 0.1 0.5    drive at 0.1 m/s while turning at 0.5 rad/s (+ left, - right; max {MAX_W})",
            "stop             stop the robot",
            "estop            emergency stop: stops and locks the robot",
            "reset            release the emergency stop",
            "status           where the robot is and what it is doing",
            "robot            list robots; 'robot epuck' switches to another one",
            "restart          restart the simulation, robot back at its start",
        ]
        self.send({"type": "help", "robot": r["name"], "lines": lines})

    def status(self):
        x, y, _, yaw, vx, vy, *_ = self.robot.pose()
        x0, x1, y0, y1 = self.robot.area
        what = {IDLE: "stopped", MOVING: "moving", COMM_LOST: "stopped because the connection dropped",
                ESTOP: "stopped and locked by the emergency stop (type 'reset' to release)"}[self.state]
        if self.fell:
            what = f"{self.fell} (type 'restart')"
        return (f"The robot is {what}, at {at(x, y)}, {facing(yaw)}, speed {math.hypot(vx, vy):.2f} m/s. "
                f"Safe area: x {x0:.1f} to {x1:.1f}, y {y0:.1f} to {y1:.1f}.")

    # ---- control loop: watchdog, motion execution, feedback ----
    def loop(self):
        next_fb = 0
        try:
            while True:
                now = time.time()
                with self.lock:
                    fb = self.tick(now)
                if now >= next_fb:
                    self.send(fb)
                    next_fb = now + FEEDBACK_DT
                time.sleep(LOOP_DT)
        except Exception:
            self.die("The robot server hit an internal error and is shutting down:\n" + traceback.format_exc())

    def tick(self, now):
        if self.state not in (COMM_LOST, ESTOP) and now - self.last_hb > HEARTBEAT_TIMEOUT:
            self.halt(COMM_LOST)
            self.say(f"No signal from the operator for {HEARTBEAT_TIMEOUT} s, so the robot stopped itself at {self.here()}.",
                     "comm_lost")
        x, y, z, yaw, vx, vy, tipped, running = self.robot.pose()
        self.last_tick = time.time()
        speed = math.hypot(vx, vy)
        self.pose = (x, y, yaw)
        if not running and not self.sim_stopped:
            self.sim_stopped = True
            self.motion = None
            self.set_state(IDLE)
            self.say("The simulation was stopped in CoppeliaSim. Type 'restart' to start it again.")
        elif (z < -0.3 or tipped) and not self.fell:
            self.fell = "fell off the floor" if z < -0.3 else "tipped over"
            self.halt(IDLE)
            self.say(f"The robot {self.fell} at {at(x, y)}. Type 'restart' to put it back at the start.")
        # Look ahead by the braking time so fast driving stops in time too.
        elif self.state == MOVING and self.robot.heading_out(x + vx * BRAKE_T, y + vy * BRAKE_T, vx, vy):
            self.halt(IDLE)
            self.say(f"Stopped at {at(x, y)}: the robot reached the edge of the floor. Back up or turn around.")
        if self.state == ESTOP and self.estop_stopped_ts is None and speed < 0.05:
            self.estop_stopped_ts = now
        if self.state == MOVING:
            self.step(x, y, yaw)
        return {"type": "fb", "x": x, "y": y, "yaw": yaw, "speed": speed, "state": self.state,
                "run": self.run, "estop_ts": self.estop_ts, "estop_stopped_ts": self.estop_stopped_ts}

    def step(self, x, y, yaw):
        kind = self.motion[0]
        if kind == "vel":
            self.robot.drive(self.motion[1], self.motion[2])
        elif kind == "turn":
            _, v, w, target = self.motion
            err = wrap(target - yaw)
            if abs(err) < math.radians(1):
                if v:
                    self.motion = ("vel", v, 0.0)
                    self.say(f"Turn complete, {facing(yaw)}. Continuing straight at {abs(v):.2f} m/s.")
                else:
                    self.halt(IDLE)
                    self.say(f"Turn complete. Stopped at {self.here()}.")
            else:  # slow down near the target so it doesn't overshoot
                self.robot.drive(v, math.copysign(min(abs(w), 2 * abs(err) + 0.1), err))
        elif kind == "goto":
            _, tx, ty = self.motion
            v, w, done = goto_control(x, y, yaw, tx, ty, min(0.4, self.robot.spec["max_v"]))
            if done:
                self.halt(IDLE)
                self.say(f"Arrived at {at(tx, ty)} ({math.hypot(tx - x, ty - y) * 100:.0f} cm off target).")
            else:
                self.robot.drive(v, w)

    # ---- simulator watchdog ----
    def sim_watchdog(self):
        """The ZMQ client waits forever if CoppeliaSim dies, which freezes the control loop silently.
        Notice the freeze from this separate thread and shut down loudly instead."""
        while True:
            time.sleep(0.5)
            if time.time() - self.last_tick > SIM_TIMEOUT:
                self.die(f"No answer from CoppeliaSim for {SIM_TIMEOUT:.0f} s. It probably crashed or was closed.")

    def die(self, reason):
        log("FATAL: " + reason)
        self.say("Lost contact with the simulator, so the robot server is shutting down. "
                 "Reopen CoppeliaSim, then start server.py again."
                 if "CoppeliaSim" in reason else "The robot server hit an internal error and is shutting down. "
                 "See the server window for details.", "fatal")
        time.sleep(0.2)   # let the message go out
        os._exit(1)

    # ---- networking ----
    def serve(self, srv):
        log(f"listening on port {srv.getsockname()[1]}")
        while True:
            conn, addr = srv.accept()
            with self.lock:
                if self.conn:   # one operator at a time: newest wins
                    self.conn.close()
                self.conn = conn
                self.last_hb = time.time()
                if self.state == COMM_LOST:
                    self.set_state(IDLE)
                log(f"operator connected from {addr[0]}")
                self.send_help()
                self.say(f"Connected to the {self.robot.spec['name']}. {self.status()}")
            threading.Thread(target=self.reader, args=(conn,), daemon=True).start()

    def reader(self, conn):
        try:
            for line in conn.makefile("r"):
                try:
                    self.handle(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
        with self.lock:
            if self.conn is conn:
                self.conn = None
                log("operator disconnected")
                # The heartbeat watchdog will stop the robot.


def goto_control(x, y, yaw, tx, ty, v_max=0.4):
    """Point-to-point P controller. Returns (v, w, done). Shared with study.py."""
    dx, dy = tx - x, ty - y
    dist = math.hypot(dx, dy)
    if dist < GOTO_TOL:
        return 0.0, 0.0, True
    herr = wrap(math.atan2(dy, dx) - yaw)
    w = max(-1.5, min(1.5, 2.0 * herr))
    v = 0.0 if abs(herr) > 0.6 else min(v_max, 0.8 * dist) * math.cos(herr)
    return v, w, False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5555)
    p.add_argument("--robot", choices=ROBOTS, help="robot to load (default: the one in the scene, else pioneer)")
    a = p.parse_args()
    try:   # RemoteAPIClient would hang forever if CoppeliaSim isn't running, so check first
        socket.create_connection(("127.0.0.1", 23000), timeout=2).close()
    except OSError:
        raise SystemExit("Can't reach CoppeliaSim on port 23000. Open CoppeliaSim first.")
    try:   # grab the port before touching the sim, so a second server can't restart the running one
        srv = socket.create_server(("0.0.0.0", a.port))
    except OSError:
        raise SystemExit(f"Port {a.port} is already in use: another server.py is probably still running. "
                         f"Close it (Ctrl-C in its window, or: pkill -f server.py) and try again.")
    server = Server(Robot(a.robot))
    threading.Thread(target=server.loop, daemon=True).start()
    threading.Thread(target=server.sim_watchdog, daemon=True).start()
    try:
        server.serve(srv)
    except KeyboardInterrupt:
        server.robot.drive(0, 0)
