"""
Unit tests for common/protocol.py.
Tests serialization, deserialization, schemas, stream framing, and latency calculation.
"""

import pytest
from common.protocol import (
    ProtocolError,
    StreamFramer,
    calc_latency,
    create_ack,
    create_cmd,
    create_fb,
    create_hb,
    decode_message,
    encode_message,
)
from common.config import (
    CMD_FORWARD,
    CMD_ESTOP,
    CMD_GOTO,
    STATE_IDLE,
    STATE_MOVING,
    STATE_COMM_LOST,
)


def test_encode_decode_cmd():
    cmd = create_cmd(seq=1, name=CMD_FORWARD, args=[0.5], ts=1000.0)
    encoded = encode_message(cmd)
    assert encoded.endswith(b"\n")

    decoded = decode_message(encoded)
    assert decoded["type"] == "cmd"
    assert decoded["seq"] == 1
    assert decoded["name"] == CMD_FORWARD
    assert decoded["args"] == [0.5]
    assert decoded["ts"] == 1000.0


def test_encode_decode_hb():
    hb = create_hb(seq=42, ts=2000.0)
    encoded = encode_message(hb)
    decoded = decode_message(encoded)

    assert decoded["type"] == "hb"
    assert decoded["seq"] == 42
    assert decoded["ts"] == 2000.0


def test_encode_decode_fb():
    fb = create_fb(seq=10, x=1.23456, y=2.34567, theta=0.7854, state=STATE_MOVING, cmd_seq=1, linear_vel=0.3)
    encoded = encode_message(fb)
    decoded = decode_message(encoded)

    assert decoded["type"] == "fb"
    assert decoded["seq"] == 10
    assert decoded["x"] == 1.2346  # rounded to 4 decimals
    assert decoded["y"] == 2.3457
    assert decoded["theta"] == 0.7854
    assert decoded["state"] == STATE_MOVING
    assert decoded["cmd_seq"] == 1
    assert decoded["linear_vel"] == 0.3


def test_encode_decode_ack():
    ack = create_ack(seq=15, ref_seq=1, ok=True, msg="Command accepted", state=STATE_IDLE)
    encoded = encode_message(ack)
    decoded = decode_message(encoded)

    assert decoded["type"] == "ack"
    assert decoded["seq"] == 15
    assert decoded["ref_seq"] == 1
    assert decoded["ok"] is True
    assert decoded["state"] == STATE_IDLE


def test_invalid_messages():
    # Empty message
    with pytest.raises(ProtocolError):
        decode_message("")

    # Invalid JSON
    with pytest.raises(ProtocolError):
        decode_message("not valid json\n")

    # Missing mandatory fields
    with pytest.raises(ProtocolError):
        decode_message('{"type": "hb"}')

    # Unknown command name
    with pytest.raises(ProtocolError):
        decode_message('{"type": "cmd", "seq": 1, "ts": 123.0, "name": "fly_to_moon"}')

    # Invalid state
    with pytest.raises(ProtocolError):
        decode_message('{"type": "fb", "seq": 1, "ts": 123.0, "x": 0, "y": 0, "theta": 0, "state": "SLEEPING"}')


def test_stream_framer_fragmentation_and_coalescing():
    framer = StreamFramer()
    msg1 = create_cmd(seq=1, name=CMD_ESTOP)
    msg2 = create_hb(seq=2)

    enc1 = encode_message(msg1)
    enc2 = encode_message(msg2)

    # 1. Feed partial packet (half of msg1)
    split_pt = len(enc1) // 2
    part1 = enc1[:split_pt]
    part2 = enc1[split_pt:]

    res1 = list(framer.feed(part1))
    assert len(res1) == 0  # incomplete

    # 2. Feed remaining of msg1 combined with entire msg2
    res2 = list(framer.feed(part2 + enc2))
    assert len(res2) == 2
    assert res2[0]["seq"] == 1
    assert res2[0]["type"] == "cmd"
    assert res2[1]["seq"] == 2
    assert res2[1]["type"] == "hb"


def test_latency_calculation():
    msg = {"type": "hb", "seq": 1, "ts": 100.0}
    lat = calc_latency(msg, current_ts=100.25)
    assert pytest.approx(lat, rel=1e-3) == 250.0  # 250 ms
