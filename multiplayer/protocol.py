"""
Multiplayer Protocol: Schemas, Message Models, and Strict Validation.

This module provides versioned, strictly validated Pydantic models for communication
between browser clients and the authoritative multiplayer Python backend.

Guarantees:
- Rejection of oversized payloads (> 16 KB).
- Rejection of unknown message types and unsupported protocol versions.
- Strict numeric validation (forbids NaN, +Infinity, -Infinity).
- Strict control bounds (throttle and steering bounded to [-1.0, 1.0]).
- Rejection of extra unexpected fields ('extra=forbid').
- Clear distinction between individual player stop/E-stop and room-wide E-stop.
- Complete isolation from legacy TCP teleoperation protocol (common/protocol.py).
"""

from enum import Enum
import json
import re
import time
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 16384  # 16 KB maximum message size
ID_PATTERN = r"^[a-zA-Z0-9_\-]{1,64}$"
NAME_PATTERN = r"^[a-zA-Z0-9 _\-]{1,32}$"

ID_REGEX = re.compile(ID_PATTERN)


class ErrorCode(str, Enum):
    """Standardized protocol rejection error codes."""

    ERR_PAYLOAD_TOO_LARGE = "ERR_PAYLOAD_TOO_LARGE"
    ERR_MALFORMED_JSON = "ERR_MALFORMED_JSON"
    ERR_MISSING_HEADER = "ERR_MISSING_HEADER"
    ERR_UNSUPPORTED_VERSION = "ERR_UNSUPPORTED_VERSION"
    ERR_UNKNOWN_TYPE = "ERR_UNKNOWN_TYPE"
    ERR_VALIDATION_FAILED = "ERR_VALIDATION_FAILED"
    ERR_INVALID_NUMERIC = "ERR_INVALID_NUMERIC"
    ERR_OUT_OF_BOUNDS = "ERR_OUT_OF_BOUNDS"
    ERR_UNAUTHORIZED = "ERR_UNAUTHORIZED"
    ERR_ESTOP_ACTIVE = "ERR_ESTOP_ACTIVE"
    ERR_COMM_LOST = "ERR_COMM_LOST"
    ERR_ROOM_NOT_FOUND = "ERR_ROOM_NOT_FOUND"
    ERR_ROOM_FULL = "ERR_ROOM_FULL"
    ERR_ALREADY_IN_ROOM = "ERR_ALREADY_IN_ROOM"
    ERR_NOT_IN_ROOM = "ERR_NOT_IN_ROOM"
    ERR_NO_ROBOTS_AVAILABLE = "ERR_NO_ROBOTS_AVAILABLE"
    ERR_ROBOT_UNAVAILABLE = "ERR_ROBOT_UNAVAILABLE"


class ControlState(str, Enum):
    """Input activity state from client browser."""

    ACTIVE = "active"        # Key held down or touch control active
    NEUTRAL = "neutral"      # No keys pressed (coasting)
    RELEASED = "released"    # Key released or browser lost focus


class RobotOperatingState(str, Enum):
    """4-state safety logic states conforming to common.config.ALL_STATES."""

    IDLE = "IDLE"
    MOVING = "MOVING"
    COMM_LOST = "COMM_LOST"
    ESTOP_LATCHED = "ESTOP_LATCHED"


class SafetyScope(str, Enum):
    """Safety event scoping: individual robot vs room-wide."""

    INDIVIDUAL = "individual"
    ROOM = "room"


class MultiplayerProtocolError(Exception):
    """Exception raised when multiplayer message fails decoding or validation."""

    def __init__(self, code: ErrorCode | str, message: str, details: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code if isinstance(code, str) else code.value
        self.message = message
        self.details = details


def now_ts() -> float:
    """Return current timestamp in seconds with high precision."""
    return round(time.time(), 4)


# ============================================================================
# BASE SCHEMA ENVELOPE
# ============================================================================


class BaseMultiplayerEnvelope(BaseModel):
    """
    Standard mandatory header for every message in the multiplayer protocol.
    Enforces protocol versioning, non-negative sequence numbers, and valid timestamps.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: int = Field(
        default=PROTOCOL_VERSION,
        ge=1,
        le=1,
        description="Protocol version (must equal 1)",
    )
    type: str = Field(description="Message discriminator type")
    seq: int = Field(ge=0, le=2147483647, description="Monotonically increasing sequence number")
    ts: float = Field(gt=0.0, allow_inf_nan=False, description="POSIX timestamp (seconds)")


# ============================================================================
# CLIENT -> SERVER MESSAGES
# ============================================================================


class PlayerInputMessage(BaseMultiplayerEnvelope):
    """Real-time throttle and steering inputs sent from the active player's browser."""

    type: Literal["player_input"] = "player_input"
    session_id: str = Field(pattern=ID_PATTERN, description="Server-issued session identifier")
    throttle: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
        description="Normalized forward/reverse throttle [-1.0, 1.0]",
    )
    steering: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
        description="Normalized left/right steering [-1.0, 1.0]",
    )
    control_state: ControlState = Field(
        default=ControlState.ACTIVE,
        description="Input state (active, neutral, or released)",
    )


class ClientHeartbeatMessage(BaseMultiplayerEnvelope):
    """Periodic heartbeat from browser client to reset session watchdog."""

    type: Literal["client_heartbeat"] = "client_heartbeat"
    session_id: str = Field(pattern=ID_PATTERN, description="Active session identifier")


class IndividualStopMessage(BaseMultiplayerEnvelope):
    """Player requests to halt only their assigned robot."""

    type: Literal["individual_stop"] = "individual_stop"
    session_id: str = Field(pattern=ID_PATTERN, description="Player session ID")
    reason: str = Field(default="Normal stop", max_length=128)


class IndividualEstopMessage(BaseMultiplayerEnvelope):
    """
    Player requests an emergency stop on their assigned robot.
    Latches ESTOP_LATCHED on this player's robot only.
    """

    type: Literal["individual_estop"] = "individual_estop"
    session_id: str = Field(pattern=ID_PATTERN, description="Player session ID")
    reason: str = Field(default="Player E-Stop", max_length=128)


class RoomEstopMessage(BaseMultiplayerEnvelope):
    """
    Authorized host or admin requests a room-wide emergency stop.
    Halts and latches all robots in the room simultaneously.
    Distinct from IndividualEstopMessage.
    """

    type: Literal["room_estop"] = "room_estop"
    session_id: str = Field(pattern=ID_PATTERN, description="Host session ID")
    room_id: str = Field(pattern=ID_PATTERN, description="Target room ID")
    reason: str = Field(default="Room-wide Global E-Stop", max_length=128)


class RoomResetMessage(BaseMultiplayerEnvelope):
    """Authorized reset request to unlatch emergency stops in a room."""

    type: Literal["room_reset"] = "room_reset"
    session_id: str = Field(pattern=ID_PATTERN, description="Host session ID")
    room_id: str = Field(pattern=ID_PATTERN, description="Target room ID")


class JoinRoomMessage(BaseMultiplayerEnvelope):
    """Client request to join a room as a driver or spectator."""

    type: Literal["join_room"] = "join_room"
    session_id: str = Field(pattern=ID_PATTERN, description="Client session ID")
    room_id: str = Field(pattern=ID_PATTERN, description="Room code / ID")
    display_name: str = Field(
        min_length=1,
        max_length=32,
        pattern=NAME_PATTERN,
        description="Player display name",
    )
    role: Literal["driver", "spectator"] = Field(default="driver")


class LeaveRoomMessage(BaseMultiplayerEnvelope):
    """Client request to cleanly leave a room."""

    type: Literal["leave_room"] = "leave_room"
    session_id: str = Field(pattern=ID_PATTERN, description="Client session ID")
    room_id: str = Field(pattern=ID_PATTERN, description="Room code / ID")


class CreateRoomMessage(BaseMultiplayerEnvelope):
    """Client request to create a new room."""

    type: Literal["create_room"] = "create_room"
    session_id: str = Field(pattern=ID_PATTERN, description="Creator session ID")
    name: Optional[str] = Field(default=None, max_length=64, description="Optional room name")
    capacity: Optional[int] = Field(default=None, ge=1, le=16, description="Optional room capacity override")
    role: Literal["driver", "spectator"] = Field(default="driver", description="Creator role")
    display_name: Optional[str] = Field(default=None, max_length=32, description="Optional display name")


class ListRoomsMessage(BaseMultiplayerEnvelope):
    """Client request to list joinable rooms."""

    type: Literal["list_rooms"] = "list_rooms"
    session_id: str = Field(pattern=ID_PATTERN, description="Client session ID")


class SetReadyMessage(BaseMultiplayerEnvelope):
    """Client request to update player ready state in their room."""

    type: Literal["set_ready"] = "set_ready"
    session_id: str = Field(pattern=ID_PATTERN, description="Client session ID")
    room_id: str = Field(pattern=ID_PATTERN, description="Room ID or Room Code")
    is_ready: bool = Field(default=True, description="Player ready state")


# ============================================================================
# SERVER -> CLIENT MESSAGES
# ============================================================================


class WelcomeMessage(BaseMultiplayerEnvelope):
    """Server handshake message sent immediately upon WebSocket connection."""

    type: Literal["welcome"] = "welcome"
    session_id: str = Field(pattern=ID_PATTERN, description="Server-assigned session identifier")
    heartbeat_interval_sec: float = Field(gt=0.0, allow_inf_nan=False)
    timeout_sec: float = Field(gt=0.0, allow_inf_nan=False)
    server_time: float = Field(gt=0.0, allow_inf_nan=False)
    message: str = Field(default="Connected to AISR Multiplayer Server", max_length=128)


class CommandAckMessage(BaseMultiplayerEnvelope):
    """Server acknowledgement or rejection of a client-sent command."""

    type: Literal["command_ack"] = "command_ack"
    ref_seq: int = Field(ge=0, description="Sequence number of the acknowledged message")
    ok: bool = Field(description="True if command was accepted and processed")
    code: Optional[str] = Field(default=None, max_length=64, description="Error code if ok=False")
    message: str = Field(default="", max_length=256, description="Human-readable result or error message")
    state: Optional[RobotOperatingState] = Field(
        default=None,
        description="Current robot operating state after command execution",
    )


class RobotStatePayload(BaseModel):
    """Authoritative snapshot of a single robot in the simulation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    robot_id: str = Field(pattern=ID_PATTERN, description="Unique robot identifier in the scene")
    player_id: Optional[str] = Field(default=None, max_length=64, description="Assigned player session ID")
    x: float = Field(allow_inf_nan=False, description="X coordinate in world frame (meters)")
    y: float = Field(allow_inf_nan=False, description="Y coordinate in world frame (meters)")
    theta: float = Field(allow_inf_nan=False, description="Yaw orientation in radians")
    linear_vel: float = Field(allow_inf_nan=False, description="Linear forward velocity (m/s)")
    angular_vel: float = Field(allow_inf_nan=False, description="Angular yaw velocity (rad/s)")
    state: RobotOperatingState = Field(description="Safety operating state")
    lap: int = Field(ge=0, default=0, description="Completed laps")
    checkpoint: int = Field(ge=0, default=0, description="Last validated checkpoint index")


class RobotStateBroadcastMessage(BaseMultiplayerEnvelope):
    """Authoritative broadcast of an individual robot's state."""

    type: Literal["robot_state"] = "robot_state"
    robot: RobotStatePayload


class RoomBroadcastMessage(BaseMultiplayerEnvelope):
    """Aggregate periodic snapshot of all robots and race state in a room."""

    type: Literal["room_broadcast"] = "room_broadcast"
    room_id: str = Field(pattern=ID_PATTERN)
    race_state: str = Field(max_length=32, description="WAITING, COUNTDOWN, RUNNING, PAUSED, FINISHED")
    robots: List[RobotStatePayload] = Field(default_factory=list)


class SafetyEventMessage(BaseMultiplayerEnvelope):
    """Broadcast notification of a safety-critical state transition."""

    type: Literal["safety_event"] = "safety_event"
    scope: SafetyScope = Field(description="individual or room")
    event: str = Field(max_length=64, description="E.g. WATCHDOG_TIMEOUT, ESTOP_ACTIVATED, ESTOP_RESET")
    target_id: str = Field(pattern=ID_PATTERN, description="Target robot_id or room_id")
    reason: str = Field(max_length=256, description="Trigger reason")
    state: RobotOperatingState = Field(description="Resulting operating state")


class ErrorMessage(BaseMultiplayerEnvelope):
    """Top-level error notification for malformed requests or server faults."""

    type: Literal["error"] = "error"
    code: str = Field(max_length=64)
    message: str = Field(max_length=256)
    ref_seq: Optional[int] = Field(default=None, ge=0)


class RaceStateMessage(BaseMultiplayerEnvelope):
    """Authoritative race lifecycle update contract."""

    type: Literal["race_state"] = "race_state"
    room_id: str = Field(pattern=ID_PATTERN)
    race_status: str = Field(max_length=32, description="WAITING, COUNTDOWN, RUNNING, PAUSED, FINISHED, ABORTED")
    countdown_sec: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)
    elapsed_sec: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)


class RoomSummaryPayload(BaseModel):
    """Safe public metadata for listing rooms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    room_id: str = Field(pattern=ID_PATTERN)
    room_code: str = Field(max_length=16)
    name: str = Field(max_length=64)
    owner_id: str = Field(max_length=64)
    player_count: int = Field(ge=0)
    capacity: int = Field(ge=1)
    status: str = Field(max_length=32)


class RoomListMessage(BaseMultiplayerEnvelope):
    """Server response listing available rooms."""

    type: Literal["room_list"] = "room_list"
    rooms: List[RoomSummaryPayload] = Field(default_factory=list)


class RoomMemberPayload(BaseModel):
    """Authoritative snapshot of a single room member."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=ID_PATTERN)
    display_name: str = Field(max_length=32)
    role: str = Field(max_length=16)
    robot_id: Optional[str] = Field(default=None, max_length=64)
    is_ready: bool = Field(default=False)
    is_owner: bool = Field(default=False)
    joined_at: float = Field(ge=0.0, allow_inf_nan=False)


class RoomSnapshotMessage(BaseMultiplayerEnvelope):
    """Authoritative snapshot of room state sent to room members."""

    type: Literal["room_snapshot"] = "room_snapshot"
    room_id: str = Field(pattern=ID_PATTERN)
    room_code: str = Field(max_length=16)
    name: str = Field(max_length=64)
    owner_id: str = Field(max_length=64)
    capacity: int = Field(ge=1)
    status: str = Field(max_length=32)
    members: List[RoomMemberPayload] = Field(default_factory=list)


class PlayerJoinedMessage(BaseMultiplayerEnvelope):
    """Notification broadcast when a player joins a room."""

    type: Literal["player_joined"] = "player_joined"
    room_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    display_name: str = Field(max_length=32)
    role: str = Field(max_length=16)
    robot_id: Optional[str] = Field(default=None, max_length=64)


class PlayerLeftMessage(BaseMultiplayerEnvelope):
    """Notification broadcast when a player leaves or disconnects from a room."""

    type: Literal["player_left"] = "player_left"
    room_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    reason: str = Field(default="", max_length=128)
    new_owner_id: Optional[str] = Field(default=None, max_length=64)


class PlayerReadyMessage(BaseMultiplayerEnvelope):
    """Notification broadcast when a player toggles their ready status."""

    type: Literal["player_ready"] = "player_ready"
    room_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    is_ready: bool = Field(description="New ready status")


class RobotAssignedMessage(BaseMultiplayerEnvelope):
    """Notification when a robot is assigned to a player."""

    type: Literal["robot_assigned"] = "robot_assigned"
    room_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    robot_id: str = Field(pattern=ID_PATTERN)


class RobotUnavailableMessage(BaseMultiplayerEnvelope):
    """Notification when an assigned robot becomes unavailable without silent reassignment."""

    type: Literal["robot_unavailable"] = "robot_unavailable"
    room_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    robot_id: str = Field(pattern=ID_PATTERN)
    reason: str = Field(default="", max_length=128)


# ============================================================================
# DISCRIMINATED UNIONS & TYPE ADAPTERS
# ============================================================================

MultiplayerClientMessage = Annotated[
    Union[
        PlayerInputMessage,
        ClientHeartbeatMessage,
        IndividualStopMessage,
        IndividualEstopMessage,
        RoomEstopMessage,
        RoomResetMessage,
        JoinRoomMessage,
        LeaveRoomMessage,
        CreateRoomMessage,
        ListRoomsMessage,
        SetReadyMessage,
    ],
    Field(discriminator="type"),
]

MultiplayerServerMessage = Annotated[
    Union[
        WelcomeMessage,
        CommandAckMessage,
        RobotStateBroadcastMessage,
        RoomBroadcastMessage,
        SafetyEventMessage,
        ErrorMessage,
        RaceStateMessage,
        RoomListMessage,
        RoomSnapshotMessage,
        PlayerJoinedMessage,
        PlayerLeftMessage,
        PlayerReadyMessage,
        RobotAssignedMessage,
        RobotUnavailableMessage,
    ],
    Field(discriminator="type"),
]

MultiplayerMessage = Annotated[
    Union[
        PlayerInputMessage,
        ClientHeartbeatMessage,
        IndividualStopMessage,
        IndividualEstopMessage,
        RoomEstopMessage,
        RoomResetMessage,
        JoinRoomMessage,
        LeaveRoomMessage,
        CreateRoomMessage,
        ListRoomsMessage,
        SetReadyMessage,
        WelcomeMessage,
        CommandAckMessage,
        RobotStateBroadcastMessage,
        RoomBroadcastMessage,
        SafetyEventMessage,
        ErrorMessage,
        RaceStateMessage,
        RoomListMessage,
        RoomSnapshotMessage,
        PlayerJoinedMessage,
        PlayerLeftMessage,
        PlayerReadyMessage,
        RobotAssignedMessage,
        RobotUnavailableMessage,
    ],
    Field(discriminator="type"),
]


_client_adapter: TypeAdapter[MultiplayerClientMessage] = TypeAdapter(MultiplayerClientMessage)
_server_adapter: TypeAdapter[MultiplayerServerMessage] = TypeAdapter(MultiplayerServerMessage)
_any_adapter: TypeAdapter[MultiplayerMessage] = TypeAdapter(MultiplayerMessage)


# ============================================================================
# PARSER & SERIALIZATION FUNCTIONS
# ============================================================================


def parse_multiplayer_message(raw_data: str | bytes) -> MultiplayerMessage:
    """
    Safely parse and strictly validate an incoming multiplayer message.

    Enforces:
    1. Maximum payload size (MAX_PAYLOAD_BYTES = 16 KB).
    2. Valid JSON structure.
    3. Mandatory envelope fields ('v', 'type', 'seq', 'ts').
    4. Supported protocol version (v=1).
    5. Strict model field validation (types, ranges, no NaN/inf, no extra fields).

    Raises:
        MultiplayerProtocolError on any decoding or validation failure.
    """
    # 1. Payload size check
    if isinstance(raw_data, str):
        raw_bytes = raw_data.encode("utf-8")
    elif isinstance(raw_data, bytes):
        raw_bytes = raw_data
    else:
        raise MultiplayerProtocolError(
            ErrorCode.ERR_MALFORMED_JSON,
            f"Expected string or bytes, got {type(raw_data).__name__}",
        )

    if len(raw_bytes) > MAX_PAYLOAD_BYTES:
        raise MultiplayerProtocolError(
            ErrorCode.ERR_PAYLOAD_TOO_LARGE,
            f"Payload size ({len(raw_bytes)} bytes) exceeds maximum permitted {MAX_PAYLOAD_BYTES} bytes",
        )

    if not raw_bytes.strip():
        raise MultiplayerProtocolError(
            ErrorCode.ERR_MALFORMED_JSON,
            "Cannot parse empty message payload",
        )

    # 2. JSON parsing
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise MultiplayerProtocolError(
            ErrorCode.ERR_MALFORMED_JSON,
            f"Invalid UTF-8 encoding: {e}",
        ) from e
    except json.JSONDecodeError as e:
        raise MultiplayerProtocolError(
            ErrorCode.ERR_MALFORMED_JSON,
            f"Invalid JSON payload: {e.msg} at line {e.lineno} col {e.colno}",
        ) from e

    if not isinstance(data, dict):
        raise MultiplayerProtocolError(
            ErrorCode.ERR_MALFORMED_JSON,
            f"Message payload must be a JSON object, got {type(data).__name__}",
        )

    # 3. Fast pre-check for required envelope fields
    for field in ("v", "type", "seq", "ts"):
        if field not in data:
            raise MultiplayerProtocolError(
                ErrorCode.ERR_MISSING_HEADER,
                f"Missing required envelope field '{field}'",
            )

    # 4. Version check
    if data.get("v") != PROTOCOL_VERSION:
        raise MultiplayerProtocolError(
            ErrorCode.ERR_UNSUPPORTED_VERSION,
            f"Unsupported protocol version {data.get('v')}. Supported version: {PROTOCOL_VERSION}",
        )

    # 5. Schema validation via discriminated TypeAdapter
    try:
        return _any_adapter.validate_python(data)
    except ValidationError as err:
        errors = err.errors()
        first_err = errors[0] if errors else {}
        err_type = first_err.get("type", "")
        err_loc = ".".join(str(loc) for loc in first_err.get("loc", []))
        err_msg = first_err.get("msg", "Validation error")

        if "finite_number" in err_type:
            code = ErrorCode.ERR_INVALID_NUMERIC
        elif "greater_than" in err_type or "less_than" in err_type:
            code = ErrorCode.ERR_OUT_OF_BOUNDS
        elif "discriminator" in err_type or "literal_error" in err_type:
            code = ErrorCode.ERR_UNKNOWN_TYPE
        else:
            code = ErrorCode.ERR_VALIDATION_FAILED

        raise MultiplayerProtocolError(
            code=code,
            message=f"Validation failed at '{err_loc}': {err_msg}",
            details=errors,
        ) from err


def parse_client_message(raw_data: str | bytes) -> MultiplayerClientMessage:
    """Convenience parser that specifically validates client-to-server messages."""
    msg = parse_multiplayer_message(raw_data)
    if not isinstance(
        msg,
        (
            PlayerInputMessage,
            ClientHeartbeatMessage,
            IndividualStopMessage,
            IndividualEstopMessage,
            RoomEstopMessage,
            RoomResetMessage,
            JoinRoomMessage,
            LeaveRoomMessage,
            CreateRoomMessage,
            ListRoomsMessage,
            SetReadyMessage,
        ),
    ):
        raise MultiplayerProtocolError(
            ErrorCode.ERR_UNAUTHORIZED,
            f"Expected client message type, got server message type '{msg.type}'",
        )
    return msg


def encode_multiplayer_message(msg: MultiplayerMessage | BaseModel | dict) -> str:
    """Serialize a message model or dictionary to a JSON string."""
    if isinstance(msg, BaseModel):
        return msg.model_dump_json()
    elif isinstance(msg, dict):
        return json.dumps(msg, separators=(",", ":"))
    raise TypeError(f"Cannot encode object of type {type(msg).__name__}")
