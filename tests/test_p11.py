"""Run:  python -m unittest discover -s tests -v   (no simulator or broker needed)"""
import os
import math
import sys
import time
import unittest
from unittest.mock import Mock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from p11.bridge import Bridge                                   # noqa: E402
from p11.client import build_client                             # noqa: E402
from p11.netsim import ImpairedTransport                        # noqa: E402
from p11.protocol import decode, encode, parse_command, validate  # noqa: E402
from p11.robots import CoppeliaRobot, FakeRobot                 # noqa: E402
from p11.transports import SocketServer                         # noqa: E402


class TestProtocol(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_command("forward 0.3"), ("forward", [0.3]))
        self.assertEqual(parse_command("LEFT 90"), ("left", [90.0]))
        self.assertEqual(parse_command("goto 1 -2.5"), ("goto", [1.0, -2.5]))
        self.assertEqual(parse_command("safety OFF"), ("safety", ["off"]))
        self.assertEqual(parse_command("stop"), ("stop", []))

    def test_invalid(self):
        for bad in ["", "fly", "forward 9", "forward abc", "left 0", "left 400",
                    "goto 1", "goto 100 0", "drive 0.1", "stop now", "safety maybe",
                    "forward nan", "drive 0.1 5"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_command(bad)

    def test_wire(self):
        m = {"id": 1, "cmd": "stop", "args": []}
        self.assertEqual(decode(encode(m)), m)
        with self.assertRaises(ValueError):
            decode(b"not json")
        with self.assertRaises(ValueError):
            decode(b"[1,2]")


def make_bridge(**kw):
    b = Bridge(FakeRobot, **kw)
    b.start()
    return b


def msg(i, cmd, *args, client="t"):
    return {"id": i, "client": client, "cmd": cmd, "args": list(args)}


class TestBridge(unittest.TestCase):
    def test_slew_limits_acceleration_and_deceleration(self):
        bridge = Bridge(FakeRobot, safety=False)
        robot = FakeRobot()
        bridge._apply("drive", [0.5, 1.0])

        bridge._tick(robot, 0.1)

        self.assertAlmostEqual(robot.v, 0.06)
        self.assertAlmostEqual(robot.w, 0.3)

        bridge._apply("stop", [])
        bridge._out_v, bridge._out_w = 0.3, 1.0
        bridge._tick(robot, 0.1)

        self.assertAlmostEqual(robot.v, 0.15)
        self.assertAlmostEqual(robot.w, 0.4)

    def test_estop_bypasses_slew_ramp(self):
        bridge = Bridge(FakeRobot, safety=False)
        robot = FakeRobot()
        bridge._out_v, bridge._out_w = 0.4, 0.5
        bridge._apply("estop", [])

        bridge._tick(robot, 0.1)

        self.assertEqual((robot.v, robot.w), (0.0, 0.0))

    def test_mirrored_wheels_abort_nonconverging_turn(self):
        class MirroredWheelRobot(FakeRobot):
            def set_velocity(self, v, w):
                super().set_velocity(v, -w)

        bridge = Bridge(FakeRobot, safety=False)
        robot = MirroredWheelRobot()
        bridge._apply("left", [90.0])
        bridge._tick(robot, 0.1)
        bridge.turn_progress_at = time.monotonic() - 3.1

        bridge._tick(robot, 0.1)

        self.assertEqual(bridge.mode, "idle")
        self.assertEqual(bridge.note, "turn failed: heading not converging")

    def test_turn_rate_bounds_and_absolute_timeout(self):
        bridge = Bridge(FakeRobot, safety=False)
        bridge._apply("left", [90.0])
        _, turn_rate = bridge._controller(0.0, 0.0, 0.0)
        self.assertEqual(turn_rate, 0.6)

        slow_turn = Bridge(FakeRobot, safety=False)
        slow_turn._apply("left", [3.0])
        _, turn_rate = slow_turn._controller(0.0, 0.0, 0.0)
        self.assertEqual(turn_rate, 0.15)

        bridge.turn_started_at = time.monotonic() - 20.1
        _, turn_rate = bridge._controller(0.0, 0.0, 0.0)
        self.assertEqual(turn_rate, 0.0)
        self.assertEqual(bridge.mode, "idle")
        self.assertEqual(bridge.note, "turn failed: heading not converging")

    def test_fake_robot_turn_converges(self):
        bridge = Bridge(FakeRobot, safety=False)
        robot = FakeRobot()
        bridge._apply("left", [90.0])

        for _ in range(100):
            bridge._tick(robot, 0.05)
            if bridge.mode == "idle":
                break

        self.assertEqual(bridge.mode, "idle")
        self.assertEqual(bridge.note, "turn done")
        self.assertAlmostEqual(robot.th, math.pi / 2, delta=0.03)

    def test_forward_back_stop_and_pose_reporting(self):
        b = make_bridge(watchdog_s=5.0)
        try:
            self.assertTrue(b.handle(msg(1, "forward", 0.3))["ok"])
            time.sleep(0.8)
            forward_pose = b.snapshot()
            self.assertGreater(forward_pose["x"], 0.0)
            self.assertEqual(forward_pose["mode"], "velocity")

            self.assertTrue(b.handle(msg(2, "back", 0.3))["ok"])
            time.sleep(0.8)
            backward_pose = b.snapshot()
            self.assertLess(backward_pose["x"], forward_pose["x"])

            self.assertTrue(b.handle(msg(3, "stop"))["ok"])
            time.sleep(0.5)
            stopped_pose = b.snapshot()
            self.assertEqual(stopped_pose["mode"], "idle")
            x_stopped = stopped_pose["x"]
            time.sleep(0.15)
            self.assertAlmostEqual(b.snapshot()["x"], x_stopped, places=2)
        finally:
            b.stop()

    def test_server_side_validation(self):
        b = make_bridge()
        try:
            r = b.handle(msg(1, "forward", 99))
            self.assertFalse(r["ok"])
            r = b.handle(msg(2, "selfdestruct"))
            self.assertFalse(r["ok"])
        finally:
            b.stop()

    def test_watchdog_stops_robot(self):
        b = make_bridge(watchdog_s=0.3)
        try:
            b.handle(msg(1, "forward", 0.3))
            time.sleep(1.2)                       # no more messages
            s = b.snapshot()
            self.assertEqual(s["mode"], "idle")
            self.assertGreaterEqual(s["watchdog_trips"], 1)
            x1 = s["x"]
            time.sleep(0.3)
            self.assertAlmostEqual(b.snapshot()["x"], x1, places=3)
        finally:
            b.stop()

    def _drive_at_obstacle(self, safety):
        b = make_bridge(safety=safety)
        try:
            end = time.time() + 6
            while time.time() < end:              # keep link alive, drive at obstacle at x=1.4
                b.handle(msg(int(time.time() * 1000), "forward", 0.5))
                time.sleep(0.1)
            return b.snapshot()
        finally:
            b.stop()

    def test_obstacle_stop_prevents_collision(self):
        s = self._drive_at_obstacle(True)
        self.assertEqual(s["collisions"], 0)
        self.assertGreaterEqual(s["obstacle_stops"], 1)
        self.assertLess(s["x"], 1.4 - 0.25 - 0.18)    # stayed in front of obstacle

    def test_no_safety_collides(self):
        s = self._drive_at_obstacle(False)
        self.assertGreaterEqual(s["collisions"], 1)

    def test_estop_latches(self):
        b = make_bridge()
        try:
            b.handle(msg(1, "forward", 0.3))
            self.assertTrue(b.handle(msg(2, "estop"))["ok"])
            self.assertFalse(b.handle(msg(3, "forward", 0.3))["ok"])
            self.assertTrue(b.handle(msg(4, "reset"))["ok"])
            self.assertTrue(b.handle(msg(5, "forward", 0.3))["ok"])
        finally:
            b.stop()

    def test_duplicate_command_not_executed_twice(self):
        b = make_bridge()
        try:
            r1 = b.handle(msg(7, "speed", 0.2))
            b.handle(msg(8, "speed", 0.4))
            r2 = b.handle(msg(7, "speed", 0.2))       # retry of id 7
            self.assertTrue(r2.get("dup"))
            self.assertEqual(b.default_speed, 0.4)    # NOT reset to 0.2
        finally:
            b.stop()

    def test_adaptive_speed_limits_vmax(self):
        b = make_bridge()
        try:
            b.handle(dict(msg(1, "forward", 0.5), rtt_ms=900))
            time.sleep(0.3)
            self.assertLess(b.snapshot()["vmax_eff"], 0.3)
        finally:
            b.stop()


class TestNetwork(unittest.TestCase):
    def test_delay_line_adds_rtt(self):
        b = make_bridge()
        srv = SocketServer(b, "127.0.0.1", 0)
        srv.start()
        c = build_client("socket", port=srv.port, delay_ms=200)
        try:
            c.connect()
            t = time.time()
            c.send_command("status")
            self.assertGreater(time.time() - t, 0.19)
        finally:
            c.close(); srv.stop(); b.stop()

    def test_retries_recover_from_loss(self):
        b = make_bridge()
        srv = SocketServer(b, "127.0.0.1", 0)
        srv.start()
        c = build_client("socket", port=srv.port, loss=0.3, seed=1, timeout=0.15, retries=6)
        try:
            c.connect()
            ok = sum(1 for _ in range(15) if c.send_command("status") is not None)
            self.assertGreaterEqual(ok, 14)               # retries hide most loss
            self.assertGreater(c.summary()["mean_attempts"], 1.0)
        finally:
            c.close(); srv.stop(); b.stop()


class TestCoppeliaRobotMath(unittest.TestCase):
    def test_motor_torque_uses_modern_api_and_legacy_fallback(self):
        robot = CoppeliaRobot.__new__(CoppeliaRobot)
        robot.lm, robot.rm = "left", "right"
        robot.sim = Mock()

        robot._set_motor_torque(2.0)
        self.assertEqual(
            robot.sim.setJointTargetForce.call_args_list,
            [call("left", 2.0), call("right", 2.0)],
        )

        robot.sim.setJointTargetForce.side_effect = AttributeError("unsupported")
        robot._set_motor_torque(2.0)
        self.assertEqual(
            robot.sim.setJointMaxForce.call_args_list,
            [call("left", 2.0), call("right", 2.0)],
        )

    def test_velocity_conversion_and_zero_velocity_stop(self):
        robot = CoppeliaRobot.__new__(CoppeliaRobot)
        robot.lm, robot.rm = "left", "right"
        robot.sim = Mock()

        robot.set_velocity(0.3, 0.0)
        linear_targets = [call.args[1] for call in robot.sim.setJointTargetVelocity.call_args_list]
        self.assertAlmostEqual(linear_targets[0], 0.3 / robot.WHEEL_RADIUS)
        self.assertAlmostEqual(linear_targets[1], 0.3 / robot.WHEEL_RADIUS)

        robot.sim.setJointTargetVelocity.reset_mock()
        robot.set_velocity(0.0, 0.5)
        turn_targets = [call.args[1] for call in robot.sim.setJointTargetVelocity.call_args_list]
        self.assertLess(turn_targets[0], 0.0)
        self.assertGreater(turn_targets[1], 0.0)

        robot.sim.setJointTargetVelocity.reset_mock()
        robot.set_velocity(0.0, 0.0)
        self.assertEqual(
            [call.args[1] for call in robot.sim.setJointTargetVelocity.call_args_list],
            [0.0, 0.0],
        )

    def test_pose_is_relative_to_start_frame(self):
        robot = CoppeliaRobot.__new__(CoppeliaRobot)
        robot.robot = "robot"
        robot.sim = Mock()
        robot.sim.handle_world = -1
        robot.sim.getObjectPosition.return_value = [1.0, 3.0, 0.0]
        robot.sim.getObjectOrientation.return_value = [0.0, 0.0, math.pi / 2 + 0.5]
        robot.x0, robot.y0, robot.yaw0 = 1.0, 2.0, math.pi / 2

        x, y, theta = robot.get_pose()

        self.assertAlmostEqual(x, 1.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        self.assertAlmostEqual(theta, 0.5, places=6)

if __name__ == "__main__":
    unittest.main()
