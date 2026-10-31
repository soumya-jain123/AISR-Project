"""
Comprehensive tests for Module 5: Lobby, Rooms, and Player Assignment.

Validates:
1. Room creation and room-code uniqueness.
2. Joining room by code or room ID.
3. Rejection of invalid or nonexistent room codes.
4. Enforcement of room capacity under high concurrency race conditions.
5. Prevention of a single session joining multiple rooms.
6. Unique, non-overlapping robot assignment across multiple rooms.
7. Rejection of driver joins when no robots are available in simulation registry.
8. Releasing robot reservations upon leave or disconnect.
9. Preserving other members' assignments when one player departs.
10. Deterministic owner transfer on owner departure, and room closure when empty.
11. Idempotent leave and disconnect cleanup.
12. Ready-state updates and room snapshots.
13. Room-scoped WebSocket notifications without cross-room message leakage.
14. Rejection of client-forged server room messages.
15. Handling unavailable assigned robots without silent reassignment.
"""

import asyncio
import json
import pytest
from fastapi.testclient import TestClient

from multiplayer.app import app
from multiplayer.config import MultiplayerSettings
from multiplayer.connection_manager import ConnectionManager
from multiplayer.protocol import (
    ErrorCode,
    MultiplayerProtocolError,
    PlayerJoinedMessage,
    PlayerLeftMessage,
    PlayerReadyMessage,
    RobotAssignedMessage,
    RobotUnavailableMessage,
    RoomListMessage,
    RoomSnapshotMessage,
)
from multiplayer.rooms import Room, RoomManager, RoomStatus
from multiplayer.simulation.base import RobotConfig
from multiplayer.simulation.robot_registry import RobotRegistry


def _make_test_registry(num_robots: int = 4) -> RobotRegistry:
    """Helper to create an isolated registry with specified number of robots."""
    registry = RobotRegistry()
    for i in range(1, num_robots + 1):
        registry.register(
            RobotConfig(
                robot_id=f"robot_{i}",
                display_name=f"Test Racer {i}",
                initial_pose=(float(i), 0.0, 0.0),
            )
        )
    return registry


@pytest.fixture(autouse=True)
def reset_singletons():
    from multiplayer.config import set_settings, MultiplayerSettings
    from multiplayer.connection_manager import set_connection_manager
    from multiplayer.rooms import set_room_manager
    from multiplayer.simulation.robot_registry import set_robot_registry

    set_settings(MultiplayerSettings())
    set_connection_manager(None)
    set_room_manager(None)
    set_robot_registry(None)
    yield
    set_settings(MultiplayerSettings())
    set_connection_manager(None)
    set_room_manager(None)
    set_robot_registry(None)



# ============================================================================
# 1. ROOM CREATION & CODE UNIQUENESS
# ============================================================================


def test_create_room_and_room_code_uniqueness():
    async def _run():
        registry = _make_test_registry(num_robots=60)
        manager = RoomManager(registry=registry)
        codes = set()
        room_ids = set()

        for i in range(50):
            sid = f"sess_creator_{i}"
            room, robot_id = await manager.create_room(
                session_id=sid,
                display_name=f"Creator {i}",
                name=f"Room {i}",
                capacity=4,
            )
            assert len(room.room_code) == 5
            assert room.room_code.isalnum()
            assert room.room_code not in codes
            assert room.room_id not in room_ids
            assert room.owner_session_id == sid
            assert robot_id == f"robot_{i+1}"
            codes.add(room.room_code)
            room_ids.add(room.room_id)

        assert len(codes) == 50
        assert len(room_ids) == 50

    asyncio.run(_run())


# ============================================================================
# 2. JOIN ROOM BY CODE AND ID
# ============================================================================


def test_join_room_by_code_and_by_id():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        # Creator makes room
        room, rob1 = await manager.create_room(
            session_id="sess_p1", display_name="Player 1", capacity=4
        )
        code = room.room_code
        room_id = room.room_id

        # Player 2 joins by room_code (case-insensitive)
        r2, rob2 = await manager.join_room(
            session_id="sess_p2", room_identifier=code.lower(), display_name="Player 2"
        )
        assert r2.room_id == room_id
        assert rob2 != rob1
        assert r2.member_count == 2

        # Player 3 joins by exact room_id
        r3, rob3 = await manager.join_room(
            session_id="sess_p3", room_identifier=room_id, display_name="Player 3"
        )
        assert r3.room_id == room_id
        assert rob3 not in (rob1, rob2)
        assert r3.member_count == 3

    asyncio.run(_run())


# ============================================================================
# 3. REJECT INVALID OR NONEXISTENT ROOM CODE
# ============================================================================


def test_reject_invalid_or_nonexistent_room_code():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        with pytest.raises(MultiplayerProtocolError) as exc_info:
            await manager.join_room(session_id="sess_x", room_identifier="ZZZZZ")

        assert exc_info.value.code == ErrorCode.ERR_ROOM_NOT_FOUND.value

    asyncio.run(_run())


# ============================================================================
# 4. PREVENT JOINING MULTIPLE ROOMS
# ============================================================================


def test_prevent_session_joining_multiple_rooms():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        # Player 1 creates Room 1
        r1, _ = await manager.create_room(session_id="sess_multi", name="Room 1")

        # Player 2 creates Room 2
        r2, _ = await manager.create_room(session_id="sess_other", name="Room 2")

        # Player 1 attempts to join Room 2 -> rejected
        with pytest.raises(MultiplayerProtocolError) as exc_info:
            await manager.join_room(session_id="sess_multi", room_identifier=r2.room_code)
        assert exc_info.value.code == ErrorCode.ERR_ALREADY_IN_ROOM.value

        # Player 1 attempts to create another room -> rejected
        with pytest.raises(MultiplayerProtocolError) as exc_info:
            await manager.create_room(session_id="sess_multi", name="Room 3")
        assert exc_info.value.code == ErrorCode.ERR_ALREADY_IN_ROOM.value

    asyncio.run(_run())


# ============================================================================
# 5. ATOMIC CAPACITY UNDER CONCURRENT JOINS (RACE CONDITION PROOF)
# ============================================================================


def test_enforce_room_capacity_under_concurrent_joins():
    async def _run():
        # Setup registry with 10 robots, but room capacity is only 3
        registry = _make_test_registry(num_robots=10)
        manager = RoomManager(registry=registry)

        # Host creates room (member 1)
        room, host_rob = await manager.create_room(
            session_id="sess_host", name="Grand Prix", capacity=3
        )
        code = room.room_code

        success_count = 0
        error_count = 0
        assigned_robots = [host_rob]

        # 10 concurrent players attempt to join simultaneously
        async def _try_join(idx: int):
            nonlocal success_count, error_count
            sid = f"sess_runner_{idx}"
            try:
                r, rob = await manager.join_room(
                    session_id=sid, room_identifier=code, display_name=f"Runner {idx}"
                )
                success_count += 1
                if rob:
                    assigned_robots.append(rob)
            except MultiplayerProtocolError as err:
                if err.code == ErrorCode.ERR_ROOM_FULL.value:
                    error_count += 1
                else:
                    raise

        tasks = [_try_join(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # Exactly 2 should succeed (1 host + 2 joiners = 3 max capacity)
        assert success_count == 2
        # Exactly 8 should be rejected with ERR_ROOM_FULL
        assert error_count == 8
        # Final room capacity must be exactly 3
        assert room.member_count == 3
        # All assigned robots must be unique
        assert len(assigned_robots) == 3
        assert len(set(assigned_robots)) == 3

    asyncio.run(_run())


# ============================================================================
# 6. REJECT JOINS WHEN NO ROBOTS ARE AVAILABLE
# ============================================================================


def test_reject_joins_when_no_robots_available():
    async def _run():
        # Registry has ONLY 2 robots, but room capacity is 4
        registry = _make_test_registry(num_robots=2)
        manager = RoomManager(registry=registry)

        room, rob1 = await manager.create_room(
            session_id="sess_p1", display_name="P1", capacity=4
        )
        assert rob1 == "robot_1"

        _, rob2 = await manager.join_room(
            session_id="sess_p2", room_identifier=room.room_code, display_name="P2", role="driver"
        )
        assert rob2 == "robot_2"

        # 3rd player attempts to join as driver -> rejected because 0 robots available
        with pytest.raises(MultiplayerProtocolError) as exc_info:
            await manager.join_room(
                session_id="sess_p3",
                room_identifier=room.room_code,
                display_name="P3",
                role="driver",
            )
        assert exc_info.value.code == ErrorCode.ERR_NO_ROBOTS_AVAILABLE.value

        # However, 3rd player joining as spectator succeeds without needing a robot
        r_spec, rob_spec = await manager.join_room(
            session_id="sess_p3",
            room_identifier=room.room_code,
            display_name="Spectator 3",
            role="spectator",
        )
        assert rob_spec is None
        assert r_spec.member_count == 3

    asyncio.run(_run())


# ============================================================================
# 7. UNIQUE ROBOT ASSIGNMENTS ACROSS MULTIPLE ROOMS
# ============================================================================


def test_unique_robot_assignment_across_multiple_rooms():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        # Room 1 with 2 drivers
        r1, r1_p1_rob = await manager.create_room(session_id="s1_1", name="R1", capacity=2)
        _, r1_p2_rob = await manager.join_room(session_id="s1_2", room_identifier=r1.room_code)

        # Room 2 with 2 drivers
        r2, r2_p1_rob = await manager.create_room(session_id="s2_1", name="R2", capacity=2)
        _, r2_p2_rob = await manager.join_room(session_id="s2_2", room_identifier=r2.room_code)

        all_assignments = [r1_p1_rob, r1_p2_rob, r2_p1_rob, r2_p2_rob]
        # All 4 assignments must be distinct across both rooms
        assert len(set(all_assignments)) == 4
        assert None not in all_assignments

    asyncio.run(_run())


# ============================================================================
# 8. RELEASE ASSIGNMENT ON LEAVE AND DISCONNECT
# ============================================================================


def test_release_assignment_on_leave_and_disconnect():
    async def _run():
        registry = _make_test_registry(num_robots=1)
        manager = RoomManager(registry=registry)

        # Player 1 creates room, takes only robot
        room, rob1 = await manager.create_room(session_id="sess_p1", capacity=2)
        assert rob1 == "robot_1"

        # Player 2 tries to join as driver -> fails (no robots)
        with pytest.raises(MultiplayerProtocolError):
            await manager.join_room(session_id="sess_p2", room_identifier=room.room_code)

        # Player 1 leaves room
        leave_res = await manager.leave_room("sess_p1")
        assert leave_res is not None
        _, released_robot, _ = leave_res
        assert released_robot == "robot_1"

        # Robot is now available again; Player 2 can create a room and get it
        new_room, rob2 = await manager.create_room(session_id="sess_p2", capacity=2)
        assert rob2 == "robot_1"

    asyncio.run(_run())


# ============================================================================
# 9. PRESERVE OTHER MEMBERS' ASSIGNMENTS WHEN ONE LEAVES
# ============================================================================


def test_preserve_other_members_assignments_on_leave():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        room, r1 = await manager.create_room(session_id="sess_1", capacity=4)
        _, r2 = await manager.join_room(session_id="sess_2", room_identifier=room.room_code)
        _, r3 = await manager.join_room(session_id="sess_3", room_identifier=room.room_code)

        assert room.get_member("sess_1").robot_id == r1
        assert room.get_member("sess_2").robot_id == r2
        assert room.get_member("sess_3").robot_id == r3

        # Player 2 leaves
        leave_res = await manager.leave_room("sess_2")
        assert leave_res[1] == r2

        # Verify Player 1 and Player 3 are unchanged
        assert room.get_member("sess_1").robot_id == r1
        assert room.get_member("sess_3").robot_id == r3
        assert room.get_member("sess_2") is None
        assert room.member_count == 2

    asyncio.run(_run())


# ============================================================================
# 10. OWNER DEPARTURE AND TRANSFER / CLOSURE
# ============================================================================


def test_owner_departure_and_transfer_behavior():
    async def _run():
        registry = _make_test_registry(num_robots=4)
        manager = RoomManager(registry=registry)

        room, _ = await manager.create_room(session_id="sess_owner", capacity=3)
        await manager.join_room(session_id="sess_second", room_identifier=room.room_code)
        await manager.join_room(session_id="sess_third", room_identifier=room.room_code)

        assert room.owner_session_id == "sess_owner"

        # Owner leaves -> transferred to sess_second
        _, _, new_owner = await manager.leave_room("sess_owner")
        assert new_owner == "sess_second"
        assert room.owner_session_id == "sess_second"
        assert room.status == RoomStatus.WAITING

        # Next owner leaves -> transferred to sess_third
        _, _, new_owner2 = await manager.leave_room("sess_second")
        assert new_owner2 == "sess_third"
        assert room.owner_session_id == "sess_third"

        # Last member leaves -> room closed and removed
        closed_room, _, final_owner = await manager.leave_room("sess_third")
        assert final_owner is None
        assert closed_room.status == RoomStatus.CLOSED
        assert manager.get_room(room.room_code) is None

    asyncio.run(_run())


# ============================================================================
# 11. IDEMPOTENT LEAVE AND DISCONNECT
# ============================================================================


def test_idempotent_leave_and_disconnect():
    async def _run():
        registry = _make_test_registry(num_robots=2)
        manager = RoomManager(registry=registry)

        await manager.create_room(session_id="sess_idem")

        # First leave succeeds
        res1 = await manager.leave_room("sess_idem")
        assert res1 is not None

        # Second leave is cleanly idempotent (returns None without raising)
        res2 = await manager.leave_room("sess_idem")
        assert res2 is None

        # Disconnect call on already-left session is also idempotent
        res3 = await manager.handle_disconnect("sess_idem")
        assert res3 is None

    asyncio.run(_run())


# ============================================================================
# 12. READY-STATE UPDATES AND ROOM SNAPSHOTS
# ============================================================================


def test_ready_state_updates_and_snapshot():
    async def _run():
        registry = _make_test_registry(num_robots=2)
        manager = RoomManager(registry=registry)

        room, _ = await manager.create_room(session_id="sess_ready_test")
        assert room.get_member("sess_ready_test").is_ready is False

        # Set ready to True
        r, state = await manager.set_ready("sess_ready_test", True)
        assert state is True
        assert r.get_member("sess_ready_test").is_ready is True

        snapshot = r.to_snapshot(seq=1)
        assert isinstance(snapshot, RoomSnapshotMessage)
        assert snapshot.members[0].is_ready is True
        assert snapshot.members[0].is_owner is True

        # Toggle back to False
        _, state2 = await manager.set_ready("sess_ready_test", False)
        assert state2 is False
        assert r.get_member("sess_ready_test").is_ready is False

    asyncio.run(_run())


# ============================================================================
# 13. UNAVAILABLE ROBOT HANDLING WITHOUT SILENT REASSIGNMENT
# ============================================================================


def test_handle_unavailable_assigned_robot_without_silent_reassignment():
    async def _run():
        registry = _make_test_registry(num_robots=3)
        manager = RoomManager(registry=registry)

        room, rob_id = await manager.create_room(session_id="sess_p1")
        assert rob_id == "robot_1"

        # Mark robot_1 as faulted/unavailable
        res = await manager.mark_robot_unavailable("robot_1", reason="Motor driver fault")
        assert res is not None
        affected_room, affected_session = res
        assert affected_room.room_id == room.room_id
        assert affected_session == "sess_p1"

        # Robot assignment in room member is now cleared, not silently replaced with robot_2
        member = affected_room.get_member("sess_p1")
        assert member.robot_id is None

    asyncio.run(_run())


# ============================================================================
# 14. REJECT FORGED SERVER-ORIGINATED ROOM MESSAGES
# ============================================================================


def test_reject_forged_server_originated_room_messages():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        # 1. Welcome handshake
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"
        sid = welcome["session_id"]

        # 2. Client attempts to forge a server 'room_snapshot' message
        forged_payload = {
            "v": 1,
            "type": "room_snapshot",
            "seq": 1,
            "ts": 1730000000.0,
            "room_id": "room_fake_123",
            "room_code": "FAKE1",
            "name": "Fake Room",
            "owner_id": sid,
            "capacity": 4,
            "status": "WAITING",
            "members": [],
        }
        ws.send_text(json.dumps(forged_payload))

        err_frame = ws.receive_json()
        assert err_frame["type"] == "error"
        assert err_frame["code"] == ErrorCode.ERR_UNAUTHORIZED.value


# ============================================================================
# 15. ROOM-SCOPED NOTIFICATIONS & ISOLATION VIA WEBSOCKET (MULTI-CLIENT)
# ============================================================================


def test_room_scoped_notifications_without_cross_room_leakage():
    """
    Test 3 simultaneous WebSocket clients:
    - Client 1 creates Room A
    - Client 2 joins Room A
    - Client 3 creates Room B
    Verify Room A messages (join, ready) only reach Client 1 and 2, NEVER Client 3.
    """
    client = TestClient(app)

    with client.websocket_connect("/ws/multiplayer") as ws1, \
         client.websocket_connect("/ws/multiplayer") as ws2, \
         client.websocket_connect("/ws/multiplayer") as ws3:

        # Receive welcomes
        w1 = ws1.receive_json()
        sid1 = w1["session_id"]

        w2 = ws2.receive_json()
        sid2 = w2["session_id"]

        w3 = ws3.receive_json()
        sid3 = w3["session_id"]

        # 1. Client 1 creates Room A
        ws1.send_text(
            json.dumps({
                "v": 1,
                "type": "create_room",
                "seq": 1,
                "ts": 1730000001.0,
                "session_id": sid1,
                "name": "Room A",
                "capacity": 4,
            })
        )
        # Client 1 receives robot_assigned then room_snapshot
        msg_rob1 = ws1.receive_json()
        assert msg_rob1["type"] == "robot_assigned"
        room_a_snap = ws1.receive_json()
        assert room_a_snap["type"] == "room_snapshot"
        code_a = room_a_snap["room_code"]

        # 2. Client 3 creates Room B
        ws3.send_text(
            json.dumps({
                "v": 1,
                "type": "create_room",
                "seq": 1,
                "ts": 1730000001.0,
                "session_id": sid3,
                "name": "Room B",
                "capacity": 4,
            })
        )
        msg_rob3 = ws3.receive_json()
        assert msg_rob3["type"] == "robot_assigned"
        room_b_snap = ws3.receive_json()
        assert room_b_snap["type"] == "room_snapshot"

        # 3. Client 2 joins Room A
        ws2.send_text(
            json.dumps({
                "v": 1,
                "type": "join_room",
                "seq": 1,
                "ts": 1730000002.0,
                "session_id": sid2,
                "room_id": code_a,
                "display_name": "Racer Two",
                "role": "driver",
            })
        )

        # Client 1 in Room A receives player_joined and updated snapshot
        notif_join = ws1.receive_json()
        assert notif_join["type"] == "player_joined"
        assert notif_join["session_id"] == sid2

        snap_a_updated = ws1.receive_json()
        assert snap_a_updated["type"] == "room_snapshot"
        assert len(snap_a_updated["members"]) == 2

        # Client 2 receives their robot_assigned and their room_snapshot
        c2_rob = ws2.receive_json()
        assert c2_rob["type"] == "robot_assigned"
        c2_snap = ws2.receive_json()
        assert c2_snap["type"] == "room_snapshot"
        assert len(c2_snap["members"]) == 2

        # 4. Client 2 sets ready in Room A
        ws2.send_text(
            json.dumps({
                "v": 1,
                "type": "set_ready",
                "seq": 2,
                "ts": 1730000003.0,
                "session_id": sid2,
                "room_id": code_a,
                "is_ready": True,
            })
        )

        # Client 1 receives player_ready and updated snapshot
        notif_ready = ws1.receive_json()
        assert notif_ready["type"] == "player_ready"
        assert notif_ready["session_id"] == sid2
        assert notif_ready["is_ready"] is True
        snap_a_ready = ws1.receive_json()
        assert snap_a_ready["type"] == "room_snapshot"

        # Client 2 receives command_ack for set_ready, then ready notif, then snapshot
        ack_ready = ws2.receive_json()
        assert ack_ready["type"] == "player_ready"
        snap_ready_c2 = ws2.receive_json()
        assert snap_ready_c2["type"] == "room_snapshot"
        ack_cmd = ws2.receive_json()
        assert ack_cmd["type"] == "command_ack"
        assert ack_cmd["ok"] is True

        # 5. VERIFY CLIENT 3 (IN ROOM B) RECEIVED ZERO MESSAGES FROM ROOM A
        # To verify without blocking on receive_json(), send a harmless heartbeat from Client 3
        # and verify Client 3 receives ONLY the heartbeat ack!
        ws3.send_text(
            json.dumps({
                "v": 1,
                "type": "client_heartbeat",
                "seq": 2,
                "ts": 1730000004.0,
                "session_id": sid3,
            })
        )
        c3_response = ws3.receive_json()
        # If any Room A message had leaked to Client 3, it would appear before this heartbeat ack
        assert c3_response["type"] == "command_ack"
        assert c3_response["ref_seq"] == 2
        assert c3_response["message"] == "Heartbeat acknowledged"
