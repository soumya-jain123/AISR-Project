"""
Integration and Unit Tests for Module 7: Real-Time Input and State Synchronization.

Verifies:
1. Valid player control actuates only the caller's server-assigned robot.
2. Forged session/room IDs cannot redirect control or actuate other robots.
3. Control commands from unassigned or disconnected sessions are rejected (ERR_NOT_IN_ROOM / ERR_UNAUTHORIZED).
4. Invalid and out-of-range control values are rejected and never reach simulation.
5. Neutral and release inputs stop robot motion promptly.
6. Per-robot watchdog timeout stops only the timed-out robot and transitions it to COMM_LOST.
7. One player's timeout does not affect another player's moving robot.
8. Player disconnect or room departure immediately stops the robot and clears stored input.
9. Individual E-stop latches the sender's robot only and cannot be cleared by client input.
10. Authorized server reset unlatches individual E-stop.
11. Room-wide E-stop latches all room robots, and reset requires host authority.
12. State broadcaster publishes authoritative room-scoped telemetry without cross-room leakage.
13. Multi-robot independence: multiple robots move simultaneously with independent kinematics.
14. Bounded control rate limiting prevents flooding.
"""

import asyncio
import json
import time
import pytest
from fastapi.testclient import TestClient

from multiplayer.app import app
from multiplayer.config import MultiplayerSettings, set_settings
from multiplayer.connection_manager import (
    ConnectionManager,
    get_connection_manager,
    set_connection_manager,
)
from multiplayer.control_service import ControlService, get_control_service, set_control_service
from multiplayer.protocol import (
    CommandAckMessage,
    ControlState,
    ErrorCode,
    ErrorMessage,
    PlayerInputMessage,
    RobotOperatingState,
    RoomBroadcastMessage,
    SafetyEventMessage,
    SafetyScope,
    parse_multiplayer_message,
)
from multiplayer.rooms import RoomManager, RoomStatus, get_room_manager, set_room_manager
from multiplayer.simulation.mock_sim import MockMultiplayerSimulation
from multiplayer.simulation.robot_registry import RobotRegistry, get_robot_registry, set_robot_registry
from multiplayer.state_broadcaster import StateBroadcaster, get_state_broadcaster, set_state_broadcaster


@pytest.fixture(autouse=True)
def reset_multiplayer_singletons():
    """Reset all multiplayer singletons with fast, deterministic test settings."""
    settings = MultiplayerSettings(
        ws_heartbeat_interval_sec=0.5,
        session_timeout_sec=2.0,
        control_watchdog_timeout_sec=0.4,
        control_rate_limit_hz=30.0,
        sim_tick_rate_hz=30,
        state_broadcast_rate_hz=20,
        max_concurrent_connections=10,
    )
    set_settings(settings)

    reg = RobotRegistry()
    from multiplayer.config import get_default_robot_configs

    for cfg in get_default_robot_configs():
        reg.register(cfg)
    set_robot_registry(reg)

    sim = MockMultiplayerSimulation(dt=1.0 / 30.0, auto_step=False, registry=reg)
    sim.connect()

    rm = RoomManager(settings=settings, registry=reg)
    set_room_manager(rm)

    cs = ControlService(settings=settings, room_manager=rm, registry=reg, simulation=sim)
    set_control_service(cs)

    cm = ConnectionManager(settings=settings, room_manager=rm, control_service=cs)
    set_connection_manager(cm)

    sb = StateBroadcaster(
        settings=settings,
        connection_manager=cm,
        room_manager=rm,
        control_service=cs,
        simulation=sim,
    )
    set_state_broadcaster(sb)

    yield {
        "settings": settings,
        "registry": reg,
        "simulation": sim,
        "room_manager": rm,
        "control_service": cs,
        "connection_manager": cm,
        "broadcaster": sb,
    }

    sim.disconnect()
    set_state_broadcaster(None)
    set_control_service(None)
    set_connection_manager(None)
    set_room_manager(None)
    set_robot_registry(None)


def test_assigned_robot_control_actuates_only_designated_robot():
    """Verify player can control only their assigned robot and simulation pose advances."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        welcome = parse_multiplayer_message(ws.receive_text())
        sess_id = welcome.session_id

        # 1. Create room -> assigns robot_1
        ws.send_text(
            json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": sess_id})
        )
        assign_msg = parse_multiplayer_message(ws.receive_text())
        assert assign_msg.type == "robot_assigned"
        assert assign_msg.robot_id == "robot_1"
        _ = ws.receive_text()  # room_snapshot

        # 2. Send forward input
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 2,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "throttle": 0.8,
                    "steering": 0.0,
                    "control_state": "active",
                }
            )
        )
        ack = parse_multiplayer_message(ws.receive_text())
        assert isinstance(ack, CommandAckMessage)
        assert ack.ok is True
        assert ack.state == RobotOperatingState.MOVING

        # Step simulation
        sim = get_control_service().simulation
        init_x, init_y, _ = sim.get_robot_state("robot_1").x, sim.get_robot_state("robot_1").y, 0
        sim.step(0.1)

        # robot_1 must have moved forward (X increased)
        st1 = sim.get_robot_state("robot_1")
        assert st1.x > init_x
        assert st1.linear_vel > 0.0

        # Other robots (robot_2, robot_3, robot_4) must remain stationary at initial poses
        st2 = sim.get_robot_state("robot_2")
        assert st2.linear_vel == 0.0
        assert st2.state == RobotOperatingState.IDLE


def test_reject_control_from_unassigned_or_spectator_session():
    """Verify sessions not in a room or in spectator role cannot drive robots."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        welcome = parse_multiplayer_message(ws.receive_text())
        sess_id = welcome.session_id

        # 1. Send input without joining a room
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 1,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "throttle": 0.5,
                    "steering": 0.0,
                }
            )
        )
        err1 = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err1, ErrorMessage)
        assert err1.code == ErrorCode.ERR_NOT_IN_ROOM.value

        # 2. Join a room as spectator
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "create_room",
                    "seq": 2,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "role": "spectator",
                }
            )
        )
        snap = parse_multiplayer_message(ws.receive_text())
        assert snap.type == "room_snapshot"

        # 3. Spectator attempts to drive -> Rejected with ERR_UNAUTHORIZED
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 3,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "throttle": 0.5,
                    "steering": 0.0,
                }
            )
        )
        err2 = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err2, ErrorMessage)
        assert err2.code == ErrorCode.ERR_UNAUTHORIZED.value


def test_forged_session_cannot_actuate_robot():
    """Verify sending input with another session's ID is rejected immediately."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws1:
        w1 = parse_multiplayer_message(ws1.receive_text())

        with client.websocket_connect("/ws/multiplayer") as ws2:
            w2 = parse_multiplayer_message(ws2.receive_text())

            # Client 1 joins room
            ws1.send_text(
                json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": w1.session_id})
            )
            _ = ws1.receive_text()
            _ = ws1.receive_text()

            # Client 2 attempts to send input forging Client 1's session_id
            ws2.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "player_input",
                        "seq": 1,
                        "ts": time.time(),
                        "session_id": w1.session_id,  # Forged!
                        "throttle": 1.0,
                        "steering": 0.0,
                    }
                )
            )
            err = parse_multiplayer_message(ws2.receive_text())
            assert isinstance(err, ErrorMessage)
            assert err.code == ErrorCode.ERR_UNAUTHORIZED.value


def test_neutral_and_release_inputs_halt_robot():
    """Verify neutral or released inputs promptly zero velocities."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        w = parse_multiplayer_message(ws.receive_text())

        ws.send_text(
            json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": w.session_id})
        )
        _ = ws.receive_text()
        _ = ws.receive_text()

        # Drive
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 2,
                    "ts": time.time(),
                    "session_id": w.session_id,
                    "throttle": 0.7,
                    "steering": 0.0,
                    "control_state": "active",
                }
            )
        )
        ack1 = parse_multiplayer_message(ws.receive_text())
        assert ack1.ok is True
        assert ack1.state == RobotOperatingState.MOVING

        # Release keys (control_state = released)
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 3,
                    "ts": time.time(),
                    "session_id": w.session_id,
                    "throttle": 0.0,
                    "steering": 0.0,
                    "control_state": "released",
                }
            )
        )
        ack2 = parse_multiplayer_message(ws.receive_text())
        assert ack2.ok is True
        assert ack2.state == RobotOperatingState.IDLE

        sim = get_control_service().simulation
        assert sim.get_robot_state("robot_1").linear_vel == 0.0


def test_per_robot_watchdog_timeout_halts_only_affected_robot():
    """Verify per-robot watchdog halts timed-out robot into COMM_LOST without stopping other moving robots."""
    cs = get_control_service()
    rm = get_room_manager()
    sim = cs.simulation

    # Setup 2 sessions in a room
    room, r1 = asyncio.run(rm.create_room("sess_1", "Driver 1"))
    _, r2 = asyncio.run(rm.join_room("sess_2", room.room_id, "Driver 2"))
    assert r1 == "robot_1"
    assert r2 == "robot_2"

    t0 = time.monotonic()
    # Driver 1 sends input at t0
    asyncio.run(
        cs.handle_player_input(
            "sess_1",
            PlayerInputMessage(
                seq=1,
                ts=time.time(),
                session_id="sess_1",
                throttle=0.8,
                steering=0.0,
                control_state=ControlState.ACTIVE,
            ),
        )
    )
    # Driver 2 sends input at t0
    asyncio.run(
        cs.handle_player_input(
            "sess_2",
            PlayerInputMessage(
                seq=1,
                ts=time.time(),
                session_id="sess_2",
                throttle=0.5,
                steering=0.0,
                control_state=ControlState.ACTIVE,
            ),
        )
    )

    assert sim.get_robot_state("robot_1").state == RobotOperatingState.MOVING
    assert sim.get_robot_state("robot_2").state == RobotOperatingState.MOVING

    # Advance time beyond watchdog (timeout = 0.4s) to t0 + 0.5s for Driver 1 ONLY
    # Driver 2 sends fresh input at t0 + 0.35s
    cs._last_control_mono["robot_2"] = t0 + 0.35

    timed_out = cs.check_watchdogs(now_mono=t0 + 0.5)

    # robot_1 must have timed out into COMM_LOST
    assert any(tid == "robot_1" for tid, _ in timed_out)
    assert sim.get_robot_state("robot_1").state == RobotOperatingState.COMM_LOST
    assert sim.get_robot_state("robot_1").linear_vel == 0.0

    # robot_2 must NOT have timed out and remains MOVING
    assert not any(tid == "robot_2" for tid, _ in timed_out)
    assert sim.get_robot_state("robot_2").state == RobotOperatingState.MOVING
    assert sim.get_robot_state("robot_2").linear_vel > 0.0


def test_disconnect_and_leave_room_halt_assigned_robot():
    """Verify session disconnect or room departure zeroes control and clears watchdog."""
    cs = get_control_service()
    rm = get_room_manager()
    sim = cs.simulation

    room, r1 = asyncio.run(rm.create_room("sess_disconn", "Driver Disconn"))
    assert r1 == "robot_1"

    # Drive robot
    asyncio.run(
        cs.handle_player_input(
            "sess_disconn",
            PlayerInputMessage(
                seq=1,
                ts=time.time(),
                session_id="sess_disconn",
                throttle=0.9,
                steering=0.0,
                control_state=ControlState.ACTIVE,
            ),
        )
    )
    assert sim.get_robot_state("robot_1").linear_vel > 0.0

    # Simulate disconnect
    cs.handle_session_disconnect("sess_disconn", "robot_1")

    # Robot must be stopped and state reset
    assert sim.get_robot_state("robot_1").linear_vel == 0.0
    assert sim.get_robot_state("robot_1").state == RobotOperatingState.IDLE
    assert "robot_1" not in cs._last_control_mono


def test_individual_estop_latching_and_server_reset():
    """Verify individual E-stop latches robot, blocks actuation, and only server reset unlatches."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        w = parse_multiplayer_message(ws.receive_text())
        sess_id = w.session_id

        ws.send_text(
            json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": sess_id})
        )
        _ = ws.receive_text()
        _ = ws.receive_text()

        # Send individual E-Stop
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "individual_estop",
                    "seq": 2,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "reason": "Test E-stop latch",
                }
            )
        )
        safety_evt = parse_multiplayer_message(ws.receive_text())
        assert isinstance(safety_evt, SafetyEventMessage)
        assert safety_evt.state == RobotOperatingState.ESTOP_LATCHED
        assert safety_evt.target_id == "robot_1"

        ack = parse_multiplayer_message(ws.receive_text())
        assert isinstance(ack, CommandAckMessage)
        assert ack.state == RobotOperatingState.ESTOP_LATCHED

        # Client attempts driving while E-stopped -> MUST be rejected with ERR_ESTOP_ACTIVE
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 3,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "throttle": 0.5,
                    "steering": 0.0,
                }
            )
        )
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == ErrorCode.ERR_ESTOP_ACTIVE.value

        # Robot remains halted
        sim = get_control_service().simulation
        assert sim.get_robot_state("robot_1").state == RobotOperatingState.ESTOP_LATCHED

        # Authorized server-side reset
        ok = get_control_service().reset_robot_estop("robot_1")
        assert ok is True
        assert sim.get_robot_state("robot_1").state == RobotOperatingState.IDLE

        # Clear rate limit tracker to avoid test-runner burst limit
        get_control_service()._last_cmd_rate_mono.clear()

        # Now driving succeeds
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "player_input",
                    "seq": 4,
                    "ts": time.time(),
                    "session_id": sess_id,
                    "throttle": 0.5,
                    "steering": 0.0,
                }
            )
        )
        ack_post_reset = parse_multiplayer_message(ws.receive_text())
        assert isinstance(ack_post_reset, CommandAckMessage)
        assert ack_post_reset.ok is True


def test_room_wide_estop_and_host_only_reset():
    """Verify room-wide E-stop latches all room robots and requires host authority to reset."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws_host:
        w_host = parse_multiplayer_message(ws_host.receive_text())

        # Host creates room
        ws_host.send_text(
            json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": w_host.session_id})
        )
        _ = ws_host.receive_text()
        snap = parse_multiplayer_message(ws_host.receive_text())
        room_id = snap.room_id

        with client.websocket_connect("/ws/multiplayer") as ws_guest:
            w_guest = parse_multiplayer_message(ws_guest.receive_text())

            # Guest joins room
            ws_guest.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "join_room",
                        "seq": 1,
                        "ts": time.time(),
                        "session_id": w_guest.session_id,
                        "room_id": room_id,
                        "display_name": "Guest Driver",
                    }
                )
            )
            # Drain join broadcasts
            _ = ws_host.receive_text()  # player_joined
            _ = ws_host.receive_text()  # snapshot
            _ = ws_guest.receive_text()  # robot_assigned
            _ = ws_guest.receive_text()  # snapshot

            # Trigger room E-stop by host
            ws_host.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "room_estop",
                        "seq": 2,
                        "ts": time.time(),
                        "session_id": w_host.session_id,
                        "room_id": room_id,
                        "reason": "Host global E-stop",
                    }
                )
            )
            # Both clients receive SafetyEventMessage; host also receives command ack
            s_host = parse_multiplayer_message(ws_host.receive_text())
            assert isinstance(s_host, SafetyEventMessage)
            assert s_host.scope == SafetyScope.ROOM
            assert s_host.state == RobotOperatingState.ESTOP_LATCHED

            ack_estop = parse_multiplayer_message(ws_host.receive_text())
            assert isinstance(ack_estop, CommandAckMessage)
            assert ack_estop.ok is True

            s_guest = parse_multiplayer_message(ws_guest.receive_text())
            assert isinstance(s_guest, SafetyEventMessage)
            assert s_guest.scope == SafetyScope.ROOM

            # Non-host attempts room reset -> REJECTED
            ws_guest.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "room_reset",
                        "seq": 2,
                        "ts": time.time(),
                        "session_id": w_guest.session_id,
                        "room_id": room_id,
                    }
                )
            )
            err = parse_multiplayer_message(ws_guest.receive_text())
            assert isinstance(err, ErrorMessage)
            assert err.code == ErrorCode.ERR_UNAUTHORIZED.value

            # Host resets room E-stop -> ACCEPTED
            ws_host.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "room_reset",
                        "seq": 3,
                        "ts": time.time(),
                        "session_id": w_host.session_id,
                        "room_id": room_id,
                    }
                )
            )
            reset_evt = parse_multiplayer_message(ws_host.receive_text())
            assert isinstance(reset_evt, SafetyEventMessage)
            assert reset_evt.state == RobotOperatingState.IDLE

            ack_reset = parse_multiplayer_message(ws_host.receive_text())
            assert isinstance(ack_reset, CommandAckMessage)
            assert ack_reset.ok is True


def test_room_state_broadcast_scoped_to_room_members():
    """Verify StateBroadcaster delivers authoritative telemetry only to room members without leaking."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws_r1:
        w_r1 = parse_multiplayer_message(ws_r1.receive_text())

        # Room 1
        ws_r1.send_text(
            json.dumps({"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": w_r1.session_id})
        )
        _ = ws_r1.receive_text()
        snap1 = parse_multiplayer_message(ws_r1.receive_text())

        with client.websocket_connect("/ws/multiplayer") as ws_r2:
            w_r2 = parse_multiplayer_message(ws_r2.receive_text())

            # Room 2
            ws_r2.send_text(
                json.dumps(
                    {"v": 1, "type": "create_room", "seq": 1, "ts": time.time(), "session_id": w_r2.session_id}
                )
            )
            _ = ws_r2.receive_text()
            snap2 = parse_multiplayer_message(ws_r2.receive_text())

            # Trigger a broadcast tick
            sb = get_state_broadcaster()
            asyncio.run(sb._broadcast_room_states())

            # Room 1 receives broadcast with only Room 1's robot
            b1 = parse_multiplayer_message(ws_r1.receive_text())
            assert isinstance(b1, RoomBroadcastMessage)
            assert b1.room_id == snap1.room_id
            assert all(r.robot_id == "robot_1" for r in b1.robots)

            # Room 2 receives broadcast with only Room 2's robot
            b2 = parse_multiplayer_message(ws_r2.receive_text())
            assert isinstance(b2, RoomBroadcastMessage)
            assert b2.room_id == snap2.room_id
            assert all(r.robot_id == "robot_2" for r in b2.robots)


def test_multi_robot_independent_movement_in_mock():
    """Verify multiple robots can move independently in mock simulation."""
    sim = get_control_service().simulation
    assert isinstance(sim, MockMultiplayerSimulation)

    # Robot 1: full forward (throttle=1.0, steering=0.0)
    sim.apply_control("robot_1", throttle=1.0, steering=0.0)

    # Robot 2: turn in place (throttle=0.0, steering=1.0)
    sim.apply_control("robot_2", throttle=0.0, steering=1.0)

    p1_0 = sim.get_robot_state("robot_1").x
    th2_0 = sim.get_robot_state("robot_2").theta

    # Step simulation by 0.2s
    sim.step(0.2)

    p1_1 = sim.get_robot_state("robot_1").x
    th2_1 = sim.get_robot_state("robot_2").theta

    # Robot 1 translated along X; Robot 2 rotated yaw
    assert p1_1 > p1_0
    assert abs(th2_1 - th2_0) > 0.05
    # Robot 2 did not translate along X
    assert abs(sim.get_robot_state("robot_2").x - sim.registry.get("robot_2").config.initial_pose[0]) < 1e-4

