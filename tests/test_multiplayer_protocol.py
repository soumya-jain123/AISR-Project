"""
Tests for Module 2: Multiplayer Protocol and Input Validation.

Validates:
1. Valid messages across all defined client and server categories.
2. Rejection of missing required envelope and payload fields.
3. Rejection of unexpected extra fields (strict schema extra='forbid').
4. Rejection of NaN, +Infinity, and -Infinity values.
5. Strict range bounds for throttle and steering [-1.0, 1.0].
6. Rejection of malformed JSON, empty payloads, and oversized payloads (> 16 KB).
7. Rejection of unsupported message types and versions.
8. Rejection of negative/invalid sequence numbers and non-positive timestamps.
9. Rejection of malformed or unsafe identifiers.
10. Safety semantics: strict distinction between IndividualEstopMessage and RoomEstopMessage.
"""

import json
import pytest

from multiplayer.protocol import (
    PROTOCOL_VERSION,
    MAX_PAYLOAD_BYTES,
    ControlState,
    ErrorCode,
    MultiplayerProtocolError,
    PlayerInputMessage,
    ClientHeartbeatMessage,
    IndividualStopMessage,
    IndividualEstopMessage,
    RoomEstopMessage,
    RoomResetMessage,
    JoinRoomMessage,
    LeaveRoomMessage,
    CommandAckMessage,
    RobotStateBroadcastMessage,
    RobotStatePayload,
    RoomBroadcastMessage,
    SafetyEventMessage,
    SafetyScope,
    ErrorMessage,
    RaceStateMessage,
    RobotOperatingState,
    parse_multiplayer_message,
    parse_client_message,
    encode_multiplayer_message,
    now_ts,
)


# ============================================================================
# 1. VALID MESSAGE TESTS ACROSS ALL CATEGORIES
# ============================================================================


def test_valid_player_input():
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 101,
        "ts": 1730000000.5,
        "session_id": "sess_user_alpha_123",
        "throttle": 0.75,
        "steering": -0.5,
        "control_state": "active",
    }
    msg = parse_multiplayer_message(json.dumps(payload))
    assert isinstance(msg, PlayerInputMessage)
    assert msg.throttle == 0.75
    assert msg.steering == -0.5
    assert msg.control_state == ControlState.ACTIVE
    assert msg.seq == 101


def test_valid_client_heartbeat():
    payload = {
        "v": 1,
        "type": "client_heartbeat",
        "seq": 50,
        "ts": 1730000001.0,
        "session_id": "sess_driver_01",
    }
    msg = parse_multiplayer_message(json.dumps(payload))
    assert isinstance(msg, ClientHeartbeatMessage)
    assert msg.session_id == "sess_driver_01"


def test_valid_individual_stop_and_estop():
    stop_payload = {
        "v": 1,
        "type": "individual_stop",
        "seq": 51,
        "ts": 1730000002.0,
        "session_id": "sess_driver_01",
        "reason": "Braking for obstacle",
    }
    msg = parse_multiplayer_message(json.dumps(stop_payload))
    assert isinstance(msg, IndividualStopMessage)
    assert msg.reason == "Braking for obstacle"

    estop_payload = {
        "v": 1,
        "type": "individual_estop",
        "seq": 52,
        "ts": 1730000003.0,
        "session_id": "sess_driver_01",
        "reason": "Emergency stop button pressed",
    }
    estop_msg = parse_multiplayer_message(json.dumps(estop_payload))
    assert isinstance(estop_msg, IndividualEstopMessage)
    assert estop_msg.reason == "Emergency stop button pressed"


def test_valid_room_estop_and_reset():
    room_estop = {
        "v": 1,
        "type": "room_estop",
        "seq": 10,
        "ts": 1730000004.0,
        "session_id": "sess_host_01",
        "room_id": "room_grand_prix",
        "reason": "Red flag on track",
    }
    msg = parse_multiplayer_message(json.dumps(room_estop))
    assert isinstance(msg, RoomEstopMessage)
    assert msg.room_id == "room_grand_prix"

    room_reset = {
        "v": 1,
        "type": "room_reset",
        "seq": 11,
        "ts": 1730000005.0,
        "session_id": "sess_host_01",
        "room_id": "room_grand_prix",
    }
    reset_msg = parse_multiplayer_message(json.dumps(room_reset))
    assert isinstance(reset_msg, RoomResetMessage)


def test_valid_room_join_and_leave():
    join_payload = {
        "v": 1,
        "type": "join_room",
        "seq": 1,
        "ts": 1730000000.1,
        "session_id": "sess_new_player",
        "room_id": "room_circuit_1",
        "display_name": "Racer X",
        "role": "driver",
    }
    join_msg = parse_multiplayer_message(json.dumps(join_payload))
    assert isinstance(join_msg, JoinRoomMessage)
    assert join_msg.display_name == "Racer X"

    leave_payload = {
        "v": 1,
        "type": "leave_room",
        "seq": 99,
        "ts": 1730000050.0,
        "session_id": "sess_new_player",
        "room_id": "room_circuit_1",
    }
    leave_msg = parse_multiplayer_message(json.dumps(leave_payload))
    assert isinstance(leave_msg, LeaveRoomMessage)


def test_valid_server_broadcast_messages():
    # Command ACK
    ack_payload = {
        "v": 1,
        "type": "command_ack",
        "seq": 200,
        "ts": 1730000010.0,
        "ref_seq": 101,
        "ok": True,
        "message": "Input applied",
        "state": "MOVING",
    }
    ack = parse_multiplayer_message(json.dumps(ack_payload))
    assert isinstance(ack, CommandAckMessage)
    assert ack.ref_seq == 101
    assert ack.ok is True

    # Robot State Broadcast
    robot_payload = {
        "v": 1,
        "type": "robot_state",
        "seq": 201,
        "ts": 1730000010.05,
        "robot": {
            "robot_id": "robot_pioneer_01",
            "player_id": "sess_driver_01",
            "x": 1.25,
            "y": -0.85,
            "theta": 1.57,
            "linear_vel": 0.4,
            "angular_vel": 0.0,
            "state": "MOVING",
            "lap": 1,
            "checkpoint": 3,
        },
    }
    state_msg = parse_multiplayer_message(json.dumps(robot_payload))
    assert isinstance(state_msg, RobotStateBroadcastMessage)
    assert state_msg.robot.robot_id == "robot_pioneer_01"
    assert state_msg.robot.linear_vel == 0.4

    # Room Broadcast
    room_broadcast = {
        "v": 1,
        "type": "room_broadcast",
        "seq": 202,
        "ts": 1730000010.1,
        "room_id": "room_grand_prix",
        "race_state": "RUNNING",
        "robots": [robot_payload["robot"]],
    }
    room_msg = parse_multiplayer_message(json.dumps(room_broadcast))
    assert isinstance(room_msg, RoomBroadcastMessage)
    assert len(room_msg.robots) == 1

    # Safety Event
    safety_event = {
        "v": 1,
        "type": "safety_event",
        "seq": 203,
        "ts": 1730000010.2,
        "scope": "room",
        "event": "ESTOP_ACTIVATED",
        "target_id": "room_grand_prix",
        "reason": "Manual operator intervention",
        "state": "ESTOP_LATCHED",
    }
    safety_msg = parse_multiplayer_message(json.dumps(safety_event))
    assert isinstance(safety_msg, SafetyEventMessage)
    assert safety_msg.scope == SafetyScope.ROOM

    # Error Message
    err_payload = {
        "v": 1,
        "type": "error",
        "seq": 204,
        "ts": 1730000010.3,
        "code": "ERR_RATE_LIMITED",
        "message": "Too many requests per second",
        "ref_seq": 105,
    }
    err_msg = parse_multiplayer_message(json.dumps(err_payload))
    assert isinstance(err_msg, ErrorMessage)
    assert err_msg.code == "ERR_RATE_LIMITED"


# ============================================================================
# 2. NUMERIC VALIDATION: REJECTION OF NAN AND INFINITY
# ============================================================================


@pytest.mark.parametrize("bad_val", ["NaN", "Infinity", "-Infinity"])
def test_reject_nan_and_infinity_in_player_input(bad_val):
    # In JSON, NaN/Infinity can be written directly or as string/literal
    raw_json = f"""{{
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": {bad_val},
        "steering": 0.0
    }}"""
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(raw_json)
    assert exc_info.value.code in (
        ErrorCode.ERR_INVALID_NUMERIC,
        ErrorCode.ERR_MALFORMED_JSON,
        ErrorCode.ERR_VALIDATION_FAILED,
    )


def test_reject_nan_float_via_python_dict():
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": float("nan"),
        "steering": 0.0,
    }
    # json.dumps without allow_nan raises or encodes NaN
    # We test with encode_multiplayer_message or direct pydantic validation
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload, allow_nan=True))
    assert exc_info.value.code == ErrorCode.ERR_INVALID_NUMERIC


@pytest.mark.parametrize("inf_val", [float("inf"), float("-inf")])
def test_reject_inf_float_in_telemetry(inf_val):
    payload = {
        "v": 1,
        "type": "robot_state",
        "seq": 1,
        "ts": 100.0,
        "robot": {
            "robot_id": "r1",
            "x": inf_val,
            "y": 0.0,
            "theta": 0.0,
            "linear_vel": 0.0,
            "angular_vel": 0.0,
            "state": "IDLE",
        },
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload, allow_nan=True))
    assert exc_info.value.code in (ErrorCode.ERR_INVALID_NUMERIC, ErrorCode.ERR_VALIDATION_FAILED)


# ============================================================================
# 3. RANGE BOUNDS: OUT-OF-RANGE THROTTLE AND STEERING REJECTION
# ============================================================================


@pytest.mark.parametrize("bad_throttle", [1.0001, 2.5, 100.0, -1.0001, -3.0])
def test_reject_out_of_bounds_throttle(bad_throttle):
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": bad_throttle,
        "steering": 0.0,
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_OUT_OF_BOUNDS


@pytest.mark.parametrize("bad_steering", [1.0001, 5.0, -1.0001, -10.0])
def test_reject_out_of_bounds_steering(bad_steering):
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": 0.0,
        "steering": bad_steering,
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_OUT_OF_BOUNDS


# ============================================================================
# 4. STRICT SCHEMA: REJECTION OF EXTRA UNEXPECTED FIELDS (extra='forbid')
# ============================================================================


def test_reject_extra_fields():
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": 0.5,
        "steering": 0.0,
        "unauthorized_admin_flag": True,  # Extra unexpected field
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_VALIDATION_FAILED
    assert "extra_forbidden" in str(exc_info.value) or "unauthorized_admin_flag" in str(exc_info.value)


# ============================================================================
# 5. REJECTION OF MISSING REQUIRED FIELDS
# ============================================================================


@pytest.mark.parametrize("missing_field", ["v", "type", "seq", "ts"])
def test_reject_missing_envelope_headers(missing_field):
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": 0.5,
        "steering": 0.0,
    }
    payload.pop(missing_field)
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_MISSING_HEADER


def test_reject_missing_payload_fields():
    # Missing session_id
    payload = {
        "v": 1,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "throttle": 0.5,
        "steering": 0.0,
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_VALIDATION_FAILED


# ============================================================================
# 6. VERSION & TYPE VALIDATION
# ============================================================================


@pytest.mark.parametrize("bad_version", [0, 2, 99, -1])
def test_reject_unsupported_protocol_version(bad_version):
    payload = {
        "v": bad_version,
        "type": "player_input",
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
        "throttle": 0.5,
        "steering": 0.0,
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_UNSUPPORTED_VERSION


@pytest.mark.parametrize("unknown_type", ["teleport_robot", "override_state", "hack_points", ""])
def test_reject_unknown_message_type(unknown_type):
    payload = {
        "v": 1,
        "type": unknown_type,
        "seq": 1,
        "ts": 100.0,
        "session_id": "sess_1",
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code in (ErrorCode.ERR_UNKNOWN_TYPE, ErrorCode.ERR_VALIDATION_FAILED)


# ============================================================================
# 7. MALFORMED JSON AND OVERSIZED PAYLOADS
# ============================================================================


def test_reject_empty_payload():
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message("")
    assert exc_info.value.code == ErrorCode.ERR_MALFORMED_JSON


def test_reject_malformed_json():
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message("{not_valid_json: 123,")
    assert exc_info.value.code == ErrorCode.ERR_MALFORMED_JSON


def test_reject_oversized_payload():
    # Construct a payload larger than 16 KB (16384 bytes)
    huge_string = "a" * (MAX_PAYLOAD_BYTES + 100)
    raw = f'{{"v": 1, "type": "player_input", "seq": 1, "ts": 1.0, "huge": "{huge_string}"}}'
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(raw)
    assert exc_info.value.code == ErrorCode.ERR_PAYLOAD_TOO_LARGE


# ============================================================================
# 8. IDENTIFIER VALIDATION (MALFORMED OR PATH-TRAVERSAL ATTEMPTS)
# ============================================================================


@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",
        "session with spaces",
        "session$!@#%",
        "a" * 65,  # Exceeds max 64 chars
        "",        # Empty
        "id/slash",
    ],
)
def test_reject_malformed_session_identifiers(bad_id):
    payload = {
        "v": 1,
        "type": "client_heartbeat",
        "seq": 1,
        "ts": 100.0,
        "session_id": bad_id,
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.ERR_VALIDATION_FAILED


# ============================================================================
# 9. SEQUENCE NUMBERS & TIMESTAMP VALIDATION
# ============================================================================


@pytest.mark.parametrize("bad_seq", [-1, -100, 2147483648])
def test_reject_negative_or_overflow_sequence_number(bad_seq):
    payload = {
        "v": 1,
        "type": "client_heartbeat",
        "seq": bad_seq,
        "ts": 100.0,
        "session_id": "sess_1",
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code in (ErrorCode.ERR_OUT_OF_BOUNDS, ErrorCode.ERR_VALIDATION_FAILED)


@pytest.mark.parametrize("bad_ts", [0.0, -10.5])
def test_reject_non_positive_timestamp(bad_ts):
    payload = {
        "v": 1,
        "type": "client_heartbeat",
        "seq": 1,
        "ts": bad_ts,
        "session_id": "sess_1",
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_multiplayer_message(json.dumps(payload))
    assert exc_info.value.code in (ErrorCode.ERR_OUT_OF_BOUNDS, ErrorCode.ERR_VALIDATION_FAILED)


# ============================================================================
# 10. SAFETY SEMANTICS: INDIVIDUAL VS ROOM-WIDE E-STOP DISTINCTION
# ============================================================================


def test_individual_estop_cannot_be_confused_with_room_estop():
    """
    Verify that an individual player's E-stop message:
    1. Parses strictly as IndividualEstopMessage.
    2. Does NOT have a room_id field (cannot target other rooms).
    3. RoomEstopMessage strictly requires room_id and cannot masquerade as an individual stop.
    """
    individual_data = {
        "v": 1,
        "type": "individual_estop",
        "seq": 10,
        "ts": 100.0,
        "session_id": "player_sess_42",
        "reason": "Player lost control",
    }
    parsed_ind = parse_multiplayer_message(json.dumps(individual_data))
    assert isinstance(parsed_ind, IndividualEstopMessage)
    assert not hasattr(parsed_ind, "room_id")  # Scope is restricted to assigned robot

    # An individual E-stop that attempts to sneak in a room_id is rejected by extra='forbid'
    individual_with_room = individual_data.copy()
    individual_with_room["room_id"] = "room_global"
    with pytest.raises(MultiplayerProtocolError):
        parse_multiplayer_message(json.dumps(individual_with_room))

    # RoomEstopMessage requires room_id
    room_estop_missing_room = {
        "v": 1,
        "type": "room_estop",
        "seq": 11,
        "ts": 100.0,
        "session_id": "host_sess_01",
        "reason": "Global stop",
    }
    with pytest.raises(MultiplayerProtocolError):
        parse_multiplayer_message(json.dumps(room_estop_missing_room))


def test_client_cannot_send_server_broadcast_message():
    """
    Verify that parse_client_message explicitly rejects server-originated messages
    (e.g., a client attempting to forge authoritative robot_state coordinates).
    """
    forged_state = {
        "v": 1,
        "type": "robot_state",
        "seq": 1,
        "ts": 100.0,
        "robot": {
            "robot_id": "r1",
            "x": 1000.0,  # Teleport attempt
            "y": 1000.0,
            "theta": 0.0,
            "linear_vel": 0.0,
            "angular_vel": 0.0,
            "state": "IDLE",
        },
    }
    with pytest.raises(MultiplayerProtocolError) as exc_info:
        parse_client_message(json.dumps(forged_state))
    assert exc_info.value.code == ErrorCode.ERR_UNAUTHORIZED


def test_message_serialization():
    """Verify that encode_multiplayer_message correctly formats JSON."""
    msg = ClientHeartbeatMessage(seq=5, ts=12345.67, session_id="sess_abc")
    serialized = encode_multiplayer_message(msg)
    parsed_back = parse_multiplayer_message(serialized)
    assert parsed_back == msg
