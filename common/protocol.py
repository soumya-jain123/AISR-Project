"""
Protocol definitions, message schemas, and serialization for Networked Teleoperation.
Uses line-delimited JSON over TCP.
"""

import json
import time
from typing import Any, Dict, Generator, List, Optional
from common.config import ALL_STATES, VALID_COMMANDS

MSG_TYPE_CMD = "cmd"
MSG_TYPE_HB = "hb"
MSG_TYPE_FB = "fb"
MSG_TYPE_ACK = "ack"

VALID_MSG_TYPES = {MSG_TYPE_CMD, MSG_TYPE_HB, MSG_TYPE_FB, MSG_TYPE_ACK}


class ProtocolError(Exception):
    """Raised when message encoding, decoding, or schema validation fails."""
    pass


def now_ts() -> float:
    """Return the current system timestamp in seconds (high precision)."""
    return time.time()


def encode_message(msg: Dict[str, Any]) -> bytes:
    """
    Encode a message dictionary into newline-delimited UTF-8 JSON bytes.
    """
    try:
        json_str = json.dumps(msg, separators=(",", ":"))
        return (json_str + "\n").encode("utf-8")
    except (TypeError, ValueError) as e:
        raise ProtocolError(f"Failed to encode message: {e}") from e


def decode_message(line: str | bytes) -> Dict[str, Any]:
    """
    Decode a single line of UTF-8 JSON into a message dict and validate basic schema.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    line = line.strip()
    if not line:
        raise ProtocolError("Cannot decode empty line")

    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ProtocolError(f"Message payload must be a JSON object, got {type(data).__name__}")

    # Validate mandatory header fields
    for field in ("type", "seq", "ts"):
        if field not in data:
            raise ProtocolError(f"Missing mandatory field '{field}'")

    msg_type = data["type"]
    if msg_type not in VALID_MSG_TYPES:
        raise ProtocolError(f"Unknown message type '{msg_type}'")

    if not isinstance(data["seq"], int):
        raise ProtocolError("Field 'seq' must be an integer")

    if not isinstance(data["ts"], (int, float)):
        raise ProtocolError("Field 'ts' must be a numeric timestamp")

    # Type-specific validation
    if msg_type == MSG_TYPE_CMD:
        if "name" not in data or not isinstance(data["name"], str):
            raise ProtocolError("Command message requires 'name' (string)")
        if data["name"] not in VALID_COMMANDS:
            raise ProtocolError(f"Invalid command name '{data['name']}'")
        if "args" in data and not isinstance(data["args"], list):
            raise ProtocolError("Command 'args' must be a list")

    elif msg_type == MSG_TYPE_FB:
        for f in ("x", "y", "theta", "state"):
            if f not in data:
                raise ProtocolError(f"Feedback message missing field '{f}'")
        if data["state"] not in ALL_STATES:
            raise ProtocolError(f"Invalid state in feedback: '{data['state']}'")

    elif msg_type == MSG_TYPE_ACK:
        if "ok" not in data or not isinstance(data["ok"], bool):
            raise ProtocolError("Ack message requires 'ok' (bool)")

    return data


def create_cmd(seq: int, name: str, args: Optional[List[Any]] = None, ts: Optional[float] = None) -> Dict[str, Any]:
    """Factory for command message."""
    return {
        "type": MSG_TYPE_CMD,
        "seq": int(seq),
        "ts": ts if ts is not None else now_ts(),
        "name": str(name),
        "args": list(args) if args is not None else [],
    }


def create_hb(seq: int, ts: Optional[float] = None) -> Dict[str, Any]:
    """Factory for heartbeat message."""
    return {
        "type": MSG_TYPE_HB,
        "seq": int(seq),
        "ts": ts if ts is not None else now_ts(),
    }


def create_fb(
    seq: int,
    x: float,
    y: float,
    theta: float,
    state: str,
    cmd_seq: int = 0,
    linear_vel: float = 0.0,
    angular_vel: float = 0.0,
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Factory for robot feedback message."""
    return {
        "type": MSG_TYPE_FB,
        "seq": int(seq),
        "ts": ts if ts is not None else now_ts(),
        "x": round(float(x), 4),
        "y": round(float(y), 4),
        "theta": round(float(theta), 4),
        "state": str(state),
        "cmd_seq": int(cmd_seq),
        "linear_vel": round(float(linear_vel), 4),
        "angular_vel": round(float(angular_vel), 4),
    }


def create_ack(
    seq: int,
    ref_seq: int,
    ok: bool,
    msg: str = "",
    state: str = "",
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Factory for command acknowledgement message."""
    return {
        "type": MSG_TYPE_ACK,
        "seq": int(seq),
        "ts": ts if ts is not None else now_ts(),
        "ref_seq": int(ref_seq),
        "ok": bool(ok),
        "msg": str(msg),
        "state": str(state),
    }


def calc_latency(msg: Dict[str, Any], current_ts: Optional[float] = None) -> float:
    """Calculate one-way latency in milliseconds."""
    curr = current_ts if current_ts is not None else now_ts()
    return max(0.0, (curr - msg.get("ts", curr)) * 1000.0)


class StreamFramer:
    """
    Accumulates raw byte chunks from a TCP socket and parses complete
    newline-delimited messages. Handles partial reads and packet coalescing.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Generator[Dict[str, Any], None, None]:
        """Feed bytes into the buffer and yield all decoded messages."""
        self._buffer.extend(chunk)
        while True:
            nl_index = self._buffer.find(b"\n")
            if nl_index == -1:
                break
            line_bytes = bytes(self._buffer[:nl_index])
            del self._buffer[: nl_index + 1]
            if line_bytes.strip():
                yield decode_message(line_bytes)
