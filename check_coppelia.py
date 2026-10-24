
"""Diagnostic test for the CoppeliaSim Pioneer P3DX."""

import math

from p11.robots import CoppeliaRobot


def advance_for(robot, seconds):
    """Advance the stepping-mode simulation for a given duration."""
    start = robot.sim.getSimulationTime()

    while robot.sim.getSimulationTime() - start < seconds:
        robot.client.step()


def main():
    print("Connecting to CoppeliaSim ...")
    robot = CoppeliaRobot(stepping=True)

    try:
        print("Robot, motors, and 8 sensors found.")

        x, y, theta = robot.get_pose()
        print(
            f"Start: x={x:.2f}, y={y:.2f}, "
            f"heading={math.degrees(theta):.1f} deg"
        )

        # Test forward movement.
        print("\n1. Forward test")
        robot.set_velocity(0.3, 0.0)
        advance_for(robot, 2.0)
        robot.set_velocity(0.0, 0.0)
        advance_for(robot, 0.5)

        x, y, theta = robot.get_pose()
        print(
            f"After forward: x={x:.2f}, y={y:.2f}, "
            f"heading={math.degrees(theta):.1f} deg"
        )

        # Test in-place left turn through the normal velocity function.
        print("\n2. Left-turn test")
        _, _, before = robot.get_pose()
        print("Wheel motor max torque (N*m):",
              "left=", robot.sim.getJointTargetForce(robot.lm),
              "right=", robot.sim.getJointTargetForce(robot.rm))

        turn_rate, turn_seconds = 0.5, 2.0
        robot.set_velocity(0.0, turn_rate)
        left_start = robot.sim.getJointPosition(robot.lm)
        right_start = robot.sim.getJointPosition(robot.rm)
        left_command = -turn_rate * robot.AXLE / (2 * robot.WHEEL_RADIUS)
        right_command = turn_rate * robot.AXLE / (2 * robot.WHEEL_RADIUS)
        print(
            "Wheel targets:",
            robot.sim.getJointTargetVelocity(robot.lm),
            robot.sim.getJointTargetVelocity(robot.rm),
        )

        advance_for(robot, turn_seconds)
        left_delta = math.atan2(
            math.sin(robot.sim.getJointPosition(robot.lm) - left_start),
            math.cos(robot.sim.getJointPosition(robot.lm) - left_start),
        )
        right_delta = math.atan2(
            math.sin(robot.sim.getJointPosition(robot.rm) - right_start),
            math.cos(robot.sim.getJointPosition(robot.rm) - right_start),
        )
        print(f"Wheel rotation commanded/achieved (rad): left={left_command * turn_seconds:.3f}/"
              f"{left_delta:.3f}, right={right_command * turn_seconds:.3f}/{right_delta:.3f}")

        _, _, after = robot.get_pose()
        print(f"Heading before: {math.degrees(before):.1f} deg")
        print(f"Heading after:  {math.degrees(after):.1f} deg")
        print(
            "Heading change:",
            f"{math.degrees((after - before + math.pi) % (2 * math.pi) - math.pi):.1f} deg",
        )

        robot.set_velocity(0.0, 0.0)
        advance_for(robot, 0.5)

        # Read sensor values.
        print("\n3. Ultrasonic sensor readings")
        print([round(v, 3) for v in robot.get_sensors()])

    finally:
        robot.close()
        print("\nDiagnostic test finished.")


if __name__ == "__main__":
    main()
