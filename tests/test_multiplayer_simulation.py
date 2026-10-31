"""
Tests for Module 3: Simulation Abstraction, Robot Registry, and Multi-Robot Backends.

Verifies:
1. RobotRegistry: registration, duplicate rejection, lookup, enumeration, availability tracking.
2. Safe command routing: unknown and unavailable robots fail closed.
3. Mock multi-robot simulation: controlling Robot A does not alter Robot B.
4. Independent stop vs stop-all semantics.
5. Robot reset restores exact initial poses and halts velocity.
6. Connect / disconnect lifecycle and SimulationNotReadyError handling.
7. CoppeliaSim adapter path resolution and handle isolation with fake ZeroMQ client.
8. Missing CoppeliaSim objects fail safely without crashing.
"""

from typing import Dict, List
import pytest

from multiplayer.protocol import RobotOperatingState
from multiplayer.simulation.base import (
    RobotConfig,
    RobotNotFoundError,
    RobotUnavailableError,
    SimulationNotReadyError,
)
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry
from multiplayer.simulation.mock_sim import MockMultiplayerSimulation
from multiplayer.simulation.coppelia_sim import CoppeliaMultiplayerSimulation


# ============================================================================
# 1. ROBOT REGISTRY TESTS
# ============================================================================


def test_registry_registration_and_lookup():
    reg = RobotRegistry()
    cfg1 = RobotConfig(robot_id="r1", display_name="Racer 1", initial_pose=(0.0, 0.0, 0.0))
    cfg2 = RobotConfig(robot_id="r2", display_name="Racer 2", initial_pose=(1.0, 2.0, 3.14))

    inst1 = reg.register(cfg1)
    inst2 = reg.register(cfg2)

    assert inst1.robot_id == "r1"
    assert inst2.robot_id == "r2"
    assert reg.count() == 2
    assert reg.contains("r1") is True
    assert reg.contains("r2") is True
    assert reg.contains("unknown") is False

    # Lookup
    found = reg.get("r1")
    assert found is not None
    assert found.config.display_name == "Racer 1"
    assert reg.require("r2").pose == (1.0, 2.0, 3.14)

    # Missing lookup
    assert reg.get("missing") is None
    with pytest.raises(RobotNotFoundError):
        reg.require("missing")


def test_registry_duplicate_rejection():
    reg = RobotRegistry()
    cfg = RobotConfig(robot_id="robot_alpha", display_name="Alpha")
    reg.register(cfg)

    with pytest.raises(ValueError, match="already registered"):
        reg.register(cfg)


def test_registry_invalid_id_rejection():
    reg = RobotRegistry()
    # Bad pattern: spaces, slashes, or special symbols
    with pytest.raises(Exception):
        RobotConfig(robot_id="bad id with spaces")


def test_registry_safe_enumeration_and_removal():
    reg = RobotRegistry()
    reg.register(RobotConfig(robot_id="r1"))
    reg.register(RobotConfig(robot_id="r2"))

    all_ids = reg.get_all_ids()
    assert set(all_ids) == {"r1", "r2"}

    # Mutating returned list does not mutate internal registry
    all_ids.append("r3")
    assert reg.count() == 2

    # Unregister
    assert reg.unregister("r1") is True
    assert reg.unregister("r1") is False
    assert reg.count() == 1
    assert reg.get_all_ids() == ["r2"]

    reg.clear()
    assert reg.count() == 0


def test_registry_availability_tracking():
    reg = RobotRegistry()
    reg.register(RobotConfig(robot_id="r1"))
    assert reg.require("r1").is_available is True

    reg.set_availability("r1", False, reason="Hardware fault")
    inst = reg.require("r1")
    assert inst.is_available is False
    assert inst.metadata.get("unavailable_reason") == "Hardware fault"


# ============================================================================
# 2. MOCK MULTI-ROBOT SIMULATION TESTS
# ============================================================================


def test_mock_multi_robot_isolation():
    """
    Critical test: Commanding Robot A moves ONLY Robot A.
    Robot B's pose, velocity, and state must remain completely unchanged.
    """
    r1 = RobotConfig(robot_id="robot_A", initial_pose=(0.0, 0.0, 0.0))
    r2 = RobotConfig(robot_id="robot_B", initial_pose=(5.0, 5.0, 0.0))

    sim = MockMultiplayerSimulation(robots=[r1, r2], dt=0.1)
    sim.connect()

    # Initial states
    state_a0 = sim.get_robot_state("robot_A")
    state_b0 = sim.get_robot_state("robot_B")
    assert state_a0.x == 0.0 and state_a0.state == RobotOperatingState.IDLE
    assert state_b0.x == 5.0 and state_b0.state == RobotOperatingState.IDLE

    # Apply forward throttle to Robot A ONLY
    assert sim.apply_control("robot_A", throttle=1.0, steering=0.0) is True

    # Advance 10 steps (1.0 second of simulation time)
    for _ in range(10):
        sim.step(0.1)

    state_a1 = sim.get_robot_state("robot_A")
    state_b1 = sim.get_robot_state("robot_B")

    # Robot A has moved forward along X axis
    assert state_a1.x > 1.0
    assert state_a1.linear_vel > 0.0
    assert state_a1.state == RobotOperatingState.MOVING

    # Robot B has NOT moved at all
    assert state_b1.x == 5.0
    assert state_b1.y == 5.0
    assert state_b1.theta == 0.0
    assert state_b1.linear_vel == 0.0
    assert state_b1.state == RobotOperatingState.IDLE

    sim.disconnect()


def test_mock_robot_specific_stop_vs_stop_all():
    r1 = RobotConfig(robot_id="r1", initial_pose=(0.0, 0.0, 0.0))
    r2 = RobotConfig(robot_id="r2", initial_pose=(0.0, 1.0, 0.0))

    sim = MockMultiplayerSimulation(robots=[r1, r2], dt=0.05)
    sim.connect()

    # Move both robots
    sim.apply_control("r1", throttle=0.5, steering=0.0)
    sim.apply_control("r2", throttle=0.5, steering=0.0)
    sim.step(0.1)

    assert sim.get_robot_state("r1").state == RobotOperatingState.MOVING
    assert sim.get_robot_state("r2").state == RobotOperatingState.MOVING

    # Stop ONLY r1
    sim.stop_robot("r1")
    assert sim.get_robot_state("r1").linear_vel == 0.0
    assert sim.get_robot_state("r1").state == RobotOperatingState.IDLE
    # r2 is still moving
    assert sim.get_robot_state("r2").linear_vel > 0.0
    assert sim.get_robot_state("r2").state == RobotOperatingState.MOVING

    # Stop all
    sim.stop_all()
    assert sim.get_robot_state("r1").linear_vel == 0.0
    assert sim.get_robot_state("r2").linear_vel == 0.0
    assert sim.get_robot_state("r2").state == RobotOperatingState.IDLE

    sim.disconnect()


def test_mock_robot_reset_behavior():
    r1 = RobotConfig(robot_id="r1", initial_pose=(2.0, 3.0, 1.57))
    sim = MockMultiplayerSimulation(robots=[r1], dt=0.1)
    sim.connect()

    # Drive away
    sim.apply_control("r1", throttle=1.0, steering=0.5)
    for _ in range(5):
        sim.step(0.1)

    distorted_state = sim.get_robot_state("r1")
    assert (distorted_state.x, distorted_state.y) != (2.0, 3.0)

    # Reset to initial
    sim.reset_robot("r1")
    reset_state = sim.get_robot_state("r1")
    assert reset_state.x == 2.0
    assert reset_state.y == 3.0
    assert reset_state.theta == 1.57
    assert reset_state.linear_vel == 0.0
    assert reset_state.state == RobotOperatingState.IDLE

    # Reset with custom coordinate
    sim.reset_robot("r1", pose=(10.0, -5.0, 0.0))
    custom_reset = sim.get_robot_state("r1")
    assert custom_reset.x == 10.0
    assert custom_reset.y == -5.0
    assert custom_reset.theta == 0.0

    sim.disconnect()


def test_mock_unknown_robot_fails_closed():
    sim = MockMultiplayerSimulation(robots=[], dt=0.05)
    sim.connect()

    with pytest.raises(RobotNotFoundError):
        sim.get_robot_state("non_existent")

    with pytest.raises(RobotNotFoundError):
        sim.apply_control("non_existent", 0.5, 0.0)

    with pytest.raises(RobotNotFoundError):
        sim.stop_robot("non_existent")

    with pytest.raises(RobotNotFoundError):
        sim.reset_robot("non_existent")

    sim.disconnect()


def test_mock_simulation_not_ready_when_disconnected():
    r1 = RobotConfig(robot_id="r1")
    sim = MockMultiplayerSimulation(robots=[r1])
    assert sim.is_ready() is False

    with pytest.raises(SimulationNotReadyError):
        sim.get_robot_state("r1")

    with pytest.raises(SimulationNotReadyError):
        sim.apply_control("r1", 0.5, 0.0)


# ============================================================================
# 3. COPPELIASIM MULTI-ROBOT ADAPTER TESTS (USING FAKE ZMQ CLIENT)
# ============================================================================


class FakeSimApi:
    """Fake CoppeliaSim 'sim' remote API object for unit testing."""

    def __init__(self, existing_objects: Dict[str, int]):
        self.objects = existing_objects  # path -> handle
        self.sim_time = 12.34
        self.sim_state = 1  # running
        self.joint_velocities: Dict[int, float] = {}
        self.positions: Dict[int, List[float]] = {}
        self.orientations: Dict[int, List[float]] = {}

        # Default pose for handles
        for h in self.objects.values():
            self.positions[h] = [1.0, 2.0, 0.1]
            self.orientations[h] = [0.0, 0.0, 0.785]

    def getSimulationTime(self):
        return self.sim_time

    def getSimulationState(self):
        return self.sim_state

    def startSimulation(self):
        self.sim_state = 1

    def getObject(self, path: str):
        if path in self.objects:
            return self.objects[path]
        raise Exception(f"Object '{path}' not found in scene")

    def setJointTargetVelocity(self, handle: int, vel: float):
        self.joint_velocities[handle] = float(vel)

    def getObjectPosition(self, handle: int, relative_to: int):
        return self.positions.get(handle, [0.0, 0.0, 0.0])

    def getObjectOrientation(self, handle: int, relative_to: int):
        return self.orientations.get(handle, [0.0, 0.0, 0.0])

    def setObjectPosition(self, handle: int, relative_to: int, pos: List[float]):
        self.positions[handle] = list(pos)

    def setObjectOrientation(self, handle: int, relative_to: int, ori: List[float]):
        self.orientations[handle] = list(ori)


class FakeRemoteApiClient:
    def __init__(self, fake_sim: FakeSimApi):
        self.sim = fake_sim

    def require(self, name: str):
        if name == "sim":
            return self.sim
        raise ValueError(f"Unknown required item '{name}'")


def test_coppelia_adapter_multi_robot_resolution_and_control():
    # Setup fake scene with 2 robots: PioneerP3DX_1 and PioneerP3DX_2
    scene_objects = {
        "/PioneerP3DX_1": 101,
        "/PioneerP3DX_1/leftMotor": 102,
        "/PioneerP3DX_1/rightMotor": 103,
        "/PioneerP3DX_2": 201,
        "/PioneerP3DX_2/leftMotor": 202,
        "/PioneerP3DX_2/rightMotor": 203,
    }
    fake_sim = FakeSimApi(scene_objects)
    fake_client = FakeRemoteApiClient(fake_sim)

    cfg1 = RobotConfig(robot_id="r1", coppelia_base_path="/PioneerP3DX_1")
    cfg2 = RobotConfig(robot_id="r2", coppelia_base_path="/PioneerP3DX_2")

    adapter = CoppeliaMultiplayerSimulation(
        robots=[cfg1, cfg2],
        client=fake_client,
    )

    # 1. Connect and verify handle resolution
    assert adapter.connect() is True
    assert adapter.is_ready() is True

    inst1 = adapter.registry.require("r1")
    inst2 = adapter.registry.require("r2")
    assert inst1.is_available is True
    assert inst2.is_available is True
    assert inst1.metadata["handles"]["base"] == 101
    assert inst2.metadata["handles"]["base"] == 201

    # 2. Command Robot 1 ONLY
    assert adapter.apply_control("r1", throttle=0.8, steering=0.2) is True

    # Motor velocities for Robot 1 must be set; Robot 2 motors must NOT be touched
    assert 102 in fake_sim.joint_velocities
    assert 103 in fake_sim.joint_velocities
    assert 202 not in fake_sim.joint_velocities
    assert 203 not in fake_sim.joint_velocities

    # 3. Read state
    state1 = adapter.get_robot_state("r1")
    assert state1.robot_id == "r1"
    assert state1.x == 1.0 and state1.y == 2.0
    assert state1.theta == 0.785
    assert state1.state == RobotOperatingState.MOVING

    # 4. Stop Robot 1
    adapter.stop_robot("r1")
    assert fake_sim.joint_velocities[102] == 0.0
    assert fake_sim.joint_velocities[103] == 0.0
    assert adapter.get_robot_state("r1").state == RobotOperatingState.IDLE

    # 5. Reset Robot 1 to custom pose
    adapter.reset_robot("r1", pose=(0.0, 0.0, 0.0))
    assert fake_sim.positions[101] == [0.0, 0.0, 0.1]
    assert fake_sim.orientations[101] == [0.0, 0.0, 0.0]

    adapter.disconnect()
    assert adapter.is_ready() is False


def test_coppelia_adapter_missing_handles_fail_cleanly():
    """Verify that if a robot's motor handle is missing from the scene, it marks it unavailable."""
    scene_objects = {
        "/PioneerP3DX_1": 101,
        # Missing left and right motors!
    }
    fake_sim = FakeSimApi(scene_objects)
    fake_client = FakeRemoteApiClient(fake_sim)

    cfg1 = RobotConfig(robot_id="r1", coppelia_base_path="/PioneerP3DX_1")
    adapter = CoppeliaMultiplayerSimulation(robots=[cfg1], client=fake_client)
    adapter.connect()

    inst1 = adapter.registry.require("r1")
    assert inst1.is_available is False
    assert "Failed resolving paths" in inst1.metadata.get("unavailable_reason", "")

    # Commands to unavailable robot fail closed
    with pytest.raises(RobotUnavailableError):
        adapter.apply_control("r1", 0.5, 0.0)

    adapter.disconnect()
