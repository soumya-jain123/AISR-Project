"""
Unit and integration tests for M2: Robot Server core, state machine, and watchdog.
Uses MockRobotDriver to verify state transitions and safety mechanisms over TCP.
"""

import socket
import time
import pytest

from common.config import (
    CMD_ESTOP,
    CMD_FORWARD,
    CMD_RESET,
    CMD_STATUS,
    CMD_STOP,
    STATE_COMM_LOST,
    STATE_ESTOP_LATCHED,
    STATE_IDLE,
    STATE_MOVING,
)
from common.protocol import (
    StreamFramer,
    create_cmd,
    create_hb,
    decode_message,
    encode_message,
)
from server.mock_driver import MockRobotDriver
from server.robot_server import RobotServer
from server.state_machine import RobotStateMachine
from server.watchdog import HeartbeatWatchdog


def test_state_machine_transitions():
    sm = RobotStateMachine()
    assert sm.state == STATE_IDLE
    assert sm.can_accept_movement() is True

    # Start motion
    sm.on_movement_started()
    assert sm.state == STATE_MOVING

    # Stop motion
    sm.on_movement_finished()
    assert sm.state == STATE_IDLE

    # Trigger E-Stop
    sm.trigger_estop("Test E-stop")
    assert sm.state == STATE_ESTOP_LATCHED
    assert sm.can_accept_movement() is False

    # Movement command rejected during E-stop
    allowed, reason = sm.validate_command(CMD_FORWARD)
    assert allowed is False
    assert "ESTOP_LATCHED" in reason

    # Reset
    ok, _ = sm.trigger_reset()
    assert ok is True
    assert sm.state == STATE_IDLE
    assert sm.can_accept_movement() is True

    # Trigger Comm Lost
    sm.trigger_comm_lost("Test Comm Lost")
    assert sm.state == STATE_COMM_LOST
    assert sm.can_accept_movement() is False

    # Restore Comm
    sm.trigger_comm_restored()
    assert sm.state == STATE_IDLE
    assert sm.can_accept_movement() is True


def test_watchdog_timeout_and_recovery():
    timeout_triggered = []
    recovery_triggered = []

    wd = HeartbeatWatchdog(
        timeout_sec=0.15,
        check_interval_sec=0.03,
        on_timeout=lambda: timeout_triggered.append(True),
        on_recovered=lambda: recovery_triggered.append(True),
    )
    wd.start()

    try:
        # Feed initially
        for _ in range(3):
            wd.feed()
            time.sleep(0.04)

        assert len(timeout_triggered) == 0

        # Stop feeding and wait for timeout
        time.sleep(0.25)
        assert len(timeout_triggered) >= 1

        # Feed again to recover
        wd.feed()
        time.sleep(0.05)
        assert len(recovery_triggered) >= 1

    finally:
        wd.stop()


def test_server_tcp_communication_and_safety():
    test_port = 55123
    driver = MockRobotDriver()
    server = RobotServer(
        driver=driver,
        host="127.0.0.1",
        port=test_port,
        watchdog_timeout=0.3,  # fast timeout for test
        feedback_interval=0.05,
    )
    server.start()
    time.sleep(0.1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", test_port))
    client.settimeout(1.0)
    framer = StreamFramer()

    def send_and_recv_ack(cmd_dict):
        client.sendall(encode_message(cmd_dict))
        while True:
            chunk = client.recv(2048)
            for msg in framer.feed(chunk):
                if msg.get("type") == "ack" and msg.get("ref_seq") == cmd_dict["seq"]:
                    return msg

    try:
        # 1. Send status command
        cmd1 = create_cmd(seq=1, name=CMD_STATUS)
        ack1 = send_and_recv_ack(cmd1)
        assert ack1["ok"] is True
        assert ack1["state"] == STATE_IDLE

        # 2. Send forward command
        cmd2 = create_cmd(seq=2, name=CMD_FORWARD, args=[0.4])
        ack2 = send_and_recv_ack(cmd2)
        assert ack2["ok"] is True
        assert ack2["state"] == STATE_MOVING

        # 3. Send E-stop
        cmd3 = create_cmd(seq=3, name=CMD_ESTOP)
        ack3 = send_and_recv_ack(cmd3)
        assert ack3["ok"] is True
        assert ack3["state"] == STATE_ESTOP_LATCHED

        # 4. Attempt movement during E-stop -> Must be rejected!
        cmd4 = create_cmd(seq=4, name=CMD_FORWARD, args=[0.3])
        ack4 = send_and_recv_ack(cmd4)
        assert ack4["ok"] is False
        assert ack4["state"] == STATE_ESTOP_LATCHED

        # 5. Send reset
        cmd5 = create_cmd(seq=5, name=CMD_RESET)
        ack5 = send_and_recv_ack(cmd5)
        assert ack5["ok"] is True
        assert ack5["state"] == STATE_IDLE

    finally:
        client.close()
        server.stop()
