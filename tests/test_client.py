"""
Unit and integration tests for Operator Client and TelemetryLogger.
"""

import os
import tempfile
import time
import pytest

from client.logger import TelemetryLogger
from client.operator_client import OperatorClient
from common.config import STATE_ESTOP_LATCHED, STATE_IDLE, STATE_MOVING
from server.mock_driver import MockRobotDriver
from server.robot_server import RobotServer


def test_telemetry_logger():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "test_log.csv")
        logger = TelemetryLogger(filepath=csv_path)

        logger.log_event(
            msg_type="fb",
            seq=1,
            x=1.2,
            y=0.5,
            theta=0.2,
            state=STATE_MOVING,
            linear_vel=0.3,
            angular_vel=0.1,
            latency_ms=15.2,
        )
        logger.close()

        assert os.path.exists(csv_path)
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 2  # header + 1 row
            assert "1.2" in lines[1]
            assert "MOVING" in lines[1]


def test_operator_client_server_integration():
    test_port = 55234
    driver = MockRobotDriver()
    server = RobotServer(
        driver=driver,
        host="127.0.0.1",
        port=test_port,
        watchdog_timeout=0.6,
        feedback_interval=0.05,
    )
    server.start()
    time.sleep(0.1)

    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "client_run.csv")
        client = OperatorClient(host="127.0.0.1", port=test_port, delay_ms=0.0, log_file=log_file)

        assert client.connect() is True
        time.sleep(0.2)  # allow feedback stream to start

        # Check telemetry received
        tel = client.get_telemetry()
        assert tel["state"] == STATE_IDLE

        # Send forward command
        ok, msg, _ = client.send_command("forward", [0.4])
        assert ok is True
        time.sleep(0.1)
        assert client.get_telemetry()["state"] == STATE_MOVING

        # Send estop command
        ok, msg, _ = client.send_command("estop")
        assert ok is True
        assert client.get_telemetry()["state"] == STATE_ESTOP_LATCHED

        # Attempt move during estop -> should be rejected
        ok, msg, _ = client.send_command("forward", [0.4])
        assert ok is False
        assert "Rejected" in msg

        # Reset
        ok, msg, _ = client.send_command("reset")
        assert ok is True
        assert client.get_telemetry()["state"] == STATE_IDLE

        client.disconnect()

    server.stop()
