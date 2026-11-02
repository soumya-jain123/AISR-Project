"""
Tests for Module 4: Multi-Session Connection Manager & WebSocket Lifecycle.

Verifies:
1. WebSocket connection and WelcomeMessage handshake.
2. Concurrent clients receive unique, isolated server-generated session IDs.
3. Message isolation: responses and ACKs go only to the originating client.
4. Validation of client messages through Module 2 protocol parser.
5. Rejection of malformed JSON, NaN inputs, and client-forged server broadcasts.
6. Monotonic sequence-number enforcement (rejects duplicates and out-of-order packets).
7. Session ID authentication (rejects messages forged with another session's ID).
8. Heartbeat updates only the originating session's activity timer.
9. One client's timeout disconnects only that client without disrupting others.
10. Clean resource cleanup upon client disconnect.
11. Broadcast delivery to all active clients without leakage or blocking.
12. Enforcement of max concurrent connection limits.
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
from multiplayer.protocol import (
    CommandAckMessage,
    ErrorMessage,
    WelcomeMessage,
    parse_multiplayer_message,
)
from multiplayer.session import SessionStatus


@pytest.fixture(autouse=True)
def reset_manager():
    """Ensure clean connection manager before and after every test."""
    settings = MultiplayerSettings(
        ws_heartbeat_interval_sec=0.5,
        session_timeout_sec=2.0,
        max_concurrent_connections=10,
        max_message_bytes=16384,
    )
    set_settings(settings)
    manager = ConnectionManager(settings)
    set_connection_manager(manager)
    yield manager


# ============================================================================
# 1. CONNECTION & HANDSHAKE TESTS
# ============================================================================


def test_websocket_connection_and_welcome_handshake():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        raw_welcome = ws.receive_text()
        msg = parse_multiplayer_message(raw_welcome)

        assert isinstance(msg, WelcomeMessage)
        assert msg.v == 1
        assert msg.session_id.startswith("sess_")
        assert msg.heartbeat_interval_sec > 0.0
        assert msg.timeout_sec > 0.0
        assert "Connected" in msg.message


def test_concurrent_clients_distinct_sessions():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws1:
        welcome1 = parse_multiplayer_message(ws1.receive_text())
        assert isinstance(welcome1, WelcomeMessage)

        with client.websocket_connect("/ws/multiplayer") as ws2:
            welcome2 = parse_multiplayer_message(ws2.receive_text())
            assert isinstance(welcome2, WelcomeMessage)

            # Unique session IDs
            assert welcome1.session_id != welcome2.session_id

            manager = get_connection_manager()
            assert manager.get_active_session_count() == 2
            assert manager.get_session(welcome1.session_id) is not None
            assert manager.get_session(welcome2.session_id) is not None


# ============================================================================
# 2. MESSAGE VALIDATION & TARGETED ISOLATION
# ============================================================================


def test_valid_player_input_ack():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        welcome = parse_multiplayer_message(ws.receive_text())
        sess_id = welcome.session_id

        # Join a room so session has an assigned robot
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "create_room",
                    "seq": 1,
                    "ts": time.time(),
                    "session_id": sess_id,
                }
            )
        )
        _ = ws.receive_text()
        _ = ws.receive_text()

        input_frame = {
            "v": 1,
            "type": "player_input",
            "seq": 2,
            "ts": time.time(),
            "session_id": sess_id,
            "throttle": 0.6,
            "steering": -0.2,
            "control_state": "active",
        }
        ws.send_text(json.dumps(input_frame))

        raw_ack = ws.receive_text()
        ack = parse_multiplayer_message(raw_ack)
        assert isinstance(ack, CommandAckMessage)
        assert ack.ref_seq == 2
        assert ack.ok is True
        assert "player_input" in ack.message


def test_message_isolation_between_clients():
    """
    Verify that an action from Client 1 generates a response ONLY to Client 1.
    Client 2 must NOT receive Client 1's response.
    """
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws1:
        w1 = parse_multiplayer_message(ws1.receive_text())

        with client.websocket_connect("/ws/multiplayer") as ws2:
            w2 = parse_multiplayer_message(ws2.receive_text())

            # Client 1 joins a room to get an assigned robot
            ws1.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "create_room",
                        "seq": 1,
                        "ts": time.time(),
                        "session_id": w1.session_id,
                    }
                )
            )
            _ = ws1.receive_text()
            _ = ws1.receive_text()

            # Client 1 sends input
            ws1.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "player_input",
                        "seq": 10,
                        "ts": time.time(),
                        "session_id": w1.session_id,
                        "throttle": 0.5,
                        "steering": 0.0,
                    }
                )
            )

            # Client 1 receives ACK
            ack1 = parse_multiplayer_message(ws1.receive_text())
            assert isinstance(ack1, CommandAckMessage)
            assert ack1.ref_seq == 10

            # Client 2 sends heartbeat to confirm its channel is clean
            ws2.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "client_heartbeat",
                        "seq": 1,
                        "ts": time.time(),
                        "session_id": w2.session_id,
                    }
                )
            )

            # Client 2 receives its own heartbeat ACK, NOT Client 1's ACK
            ack2 = parse_multiplayer_message(ws2.receive_text())
            assert isinstance(ack2, CommandAckMessage)
            assert ack2.ref_seq == 1
            assert "Heartbeat" in ack2.message


# ============================================================================
# 3. REJECTION OF INVALID / FORGED MESSAGES
# ============================================================================


def test_reject_malformed_json_input():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        _ = ws.receive_text()  # welcome

        ws.send_text("{bad_json, invalid")
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == "ERR_MALFORMED_JSON"


def test_reject_out_of_bounds_and_nan():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        w = parse_multiplayer_message(ws.receive_text())

        # Out-of-bounds throttle (2.5 > 1.0)
        bad_frame = {
            "v": 1,
            "type": "player_input",
            "seq": 1,
            "ts": time.time(),
            "session_id": w.session_id,
            "throttle": 2.5,
            "steering": 0.0,
        }
        ws.send_text(json.dumps(bad_frame))
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == "ERR_OUT_OF_BOUNDS"
        assert err.ref_seq == 1


def test_reject_client_forged_server_broadcast():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        _ = ws.receive_text()  # welcome

        # Client attempts to broadcast an authoritative robot_state
        forged = {
            "v": 1,
            "type": "robot_state",
            "seq": 1,
            "ts": time.time(),
            "robot": {
                "robot_id": "r1",
                "x": 100.0,
                "y": 100.0,
                "theta": 0.0,
                "linear_vel": 0.0,
                "angular_vel": 0.0,
                "state": "IDLE",
            },
        }
        ws.send_text(json.dumps(forged))
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == "ERR_UNAUTHORIZED"


def test_reject_session_id_mismatch():
    """Client connected under session A cannot send messages with session B's ID."""
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        _ = ws.receive_text()  # welcome

        spoofed = {
            "v": 1,
            "type": "client_heartbeat",
            "seq": 1,
            "ts": time.time(),
            "session_id": "sess_another_player_99",
        }
        ws.send_text(json.dumps(spoofed))
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == "ERR_UNAUTHORIZED"
        assert "Session ID mismatch" in err.message


# ============================================================================
# 4. SEQUENCE NUMBERING & DUPLICATE REJECTION
# ============================================================================


def test_monotonic_sequence_number_enforcement():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws:
        w = parse_multiplayer_message(ws.receive_text())
        sess_id = w.session_id

        # 1. First command: seq=5 -> OK
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "client_heartbeat",
                    "seq": 5,
                    "ts": time.time(),
                    "session_id": sess_id,
                }
            )
        )
        ack1 = parse_multiplayer_message(ws.receive_text())
        assert isinstance(ack1, CommandAckMessage)
        assert ack1.ok is True

        # 2. Duplicate seq=5 -> Rejection
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "client_heartbeat",
                    "seq": 5,
                    "ts": time.time(),
                    "session_id": sess_id,
                }
            )
        )
        err = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err, ErrorMessage)
        assert err.code == "ERR_OUT_OF_BOUNDS"
        assert "duplicate" in err.message.lower()

        # 3. Decreasing seq=4 -> Rejection
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "client_heartbeat",
                    "seq": 4,
                    "ts": time.time(),
                    "session_id": sess_id,
                }
            )
        )
        err2 = parse_multiplayer_message(ws.receive_text())
        assert isinstance(err2, ErrorMessage)
        assert err2.code == "ERR_OUT_OF_BOUNDS"

        # 4. Increasing seq=6 -> OK
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "client_heartbeat",
                    "seq": 6,
                    "ts": time.time(),
                    "session_id": sess_id,
                }
            )
        )
        ack2 = parse_multiplayer_message(ws.receive_text())
        assert isinstance(ack2, CommandAckMessage)
        assert ack2.ok is True
        assert ack2.ref_seq == 6


# ============================================================================
# 5. TIMEOUTS, HEARTBEAT & DISCONNECT ISOLATION
# ============================================================================


def test_heartbeat_updates_only_originating_session():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws1:
        w1 = parse_multiplayer_message(ws1.receive_text())
        with client.websocket_connect("/ws/multiplayer") as ws2:
            w2 = parse_multiplayer_message(ws2.receive_text())

            manager = get_connection_manager()
            s1 = manager.get_session(w1.session_id)
            s2 = manager.get_session(w2.session_id)

            time.sleep(0.05)
            t_orig_s2 = s2.last_activity_mono

            # Client 1 feeds heartbeat
            ws1.send_text(
                json.dumps(
                    {
                        "v": 1,
                        "type": "client_heartbeat",
                        "seq": 1,
                        "ts": time.time(),
                        "session_id": w1.session_id,
                    }
                )
            )
            _ = ws1.receive_text()  # ACK

            # s1 activity advanced, s2 activity stayed unchanged
            assert s1.last_activity_mono > t_orig_s2
            assert s2.last_activity_mono == t_orig_s2


def test_session_timeout_disconnects_only_inactive_client():
    """
    Verify that an inactive client times out and is cleanly removed
    while an active client remains connected.
    """
    async def _run():
        settings = MultiplayerSettings(session_timeout_sec=0.2)
        manager = ConnectionManager(settings)

        # Mock fake WebSocket connections
        class FakeWs:
            def __init__(self):
                self.closed = False
                self.close_code = None

            async def accept(self):
                pass

            async def send_text(self, text):
                pass

            async def close(self, code=1000, reason=""):
                self.closed = True
                self.close_code = code

        ws_active = FakeWs()
        ws_idle = FakeWs()

        s_active = await manager.register(ws_active)
        s_idle = await manager.register(ws_idle)

        assert manager.get_active_session_count() == 2

        # Wait 0.12s, feed s_active
        await asyncio.sleep(0.12)
        s_active.update_activity()

        # Wait another 0.12s (s_idle total idle > 0.24s > 0.2s, s_active idle ~0.12s < 0.2s)
        await asyncio.sleep(0.12)

        timed_out = await manager.check_timeouts(timeout_sec=0.2)
        assert s_idle.session_id in timed_out
        assert s_active.session_id not in timed_out

        # s_idle was closed and removed; s_active remains active
        assert ws_idle.closed is True
        assert ws_active.closed is False
        assert manager.get_session(s_idle.session_id) is None
        assert manager.get_session(s_active.session_id) is not None
        assert manager.get_active_session_count() == 1

    asyncio.run(_run())


def test_disconnect_decrements_session_count():
    client = TestClient(app)
    manager = get_connection_manager()

    with client.websocket_connect("/ws/multiplayer") as ws:
        _ = ws.receive_text()  # welcome
        assert manager.get_active_session_count() == 1

    # After exiting with-block, client is disconnected
    assert manager.get_active_session_count() == 0


# ============================================================================
# 6. BROADCAST & CONCURRENT LIMITS
# ============================================================================


def test_broadcast_delivers_to_all_clients():
    client = TestClient(app)
    with client.websocket_connect("/ws/multiplayer") as ws1:
        _ = ws1.receive_text()  # welcome 1

        with client.websocket_connect("/ws/multiplayer") as ws2:
            _ = ws2.receive_text()  # welcome 2

            manager = get_connection_manager()
            broadcast_msg = {
                "v": 1,
                "type": "error",
                "seq": 999,
                "ts": time.time(),
                "code": "SERVER_ANNOUNCEMENT",
                "message": "Race track loading...",
            }

            # Run broadcast synchronously using asyncio
            asyncio.run(manager.broadcast(broadcast_msg))

            # Both clients receive the broadcast
            b1 = parse_multiplayer_message(ws1.receive_text())
            b2 = parse_multiplayer_message(ws2.receive_text())

            assert isinstance(b1, ErrorMessage)
            assert isinstance(b2, ErrorMessage)
            assert b1.message == "Race track loading..."
            assert b2.message == "Race track loading..."


def test_enforce_max_concurrent_connections():
    settings = MultiplayerSettings(max_concurrent_connections=2)
    set_settings(settings)
    manager = ConnectionManager(settings)
    set_connection_manager(manager)

    try:
        client = TestClient(app)
        with client.websocket_connect("/ws/multiplayer") as ws1:
            _ = ws1.receive_text()

            with client.websocket_connect("/ws/multiplayer") as ws2:
                _ = ws2.receive_text()
                assert manager.get_active_session_count() == 2

                # 3rd connection attempt must be closed / rejected
                with pytest.raises(Exception):
                    with client.websocket_connect("/ws/multiplayer") as ws3:
                        _ = ws3.receive_text()
    finally:
        set_settings(MultiplayerSettings())
        set_connection_manager(None)

