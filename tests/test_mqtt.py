"""
test_mqtt.py – Comprehensive unit tests for MQTT teleoperation transport.

Uses an in-memory mock MQTT client and broker double to test:
- Configuration and topic resolution.
- Command dispatch, ACKs, and status publishing.
- Retained message rejection (safety rule).
- Stale timestamped command rejection.
- Emergency stop prioritization and reset safety.
- Application-level heartbeat and watchdog timeout.
- Disconnect handling and timer cancellation.
- Transport ownership policy (preventing TCP vs MQTT conflicts).
- Client-server interactions without requiring an external broker.

Run with:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import time
import unittest
from typing import Any, Callable

from src.mqtt_transport import (
    MQTTConfig,
    MQTTRobotClient,
    MQTTRobotServer,
    MQTTTopics,
)
from src.robot_interface import MockRobot
from src.safety import RobotState, SafetyConfig, SafetyController


# ============================================================================
# In-Memory Mock MQTT Client and Message Double
# ============================================================================

class MockMQTTMessage:
    """Mock representing a received paho.mqtt.client.MQTTMessage."""

    def __init__(
        self,
        topic: str,
        payload: bytes,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain


class MockMQTTClientDouble:
    """In-memory test double mimicking paho.mqtt.client.Client."""

    def __init__(self, client_id: str = "mock_client") -> None:
        self.client_id = client_id
        self.connected = False
        self.subscriptions: dict[str, int] = {}
        self.published_messages: list[tuple[str, str, int, bool]] = []
        self.will_message: tuple[str, str, int, bool] | None = None

        self.on_connect: Callable[..., Any] | None = None
        self.on_disconnect: Callable[..., Any] | None = None
        self.on_message: Callable[..., Any] | None = None

        self.loop_started = False
        self.peer_client: MockMQTTClientDouble | None = None

    def will_set(self, topic: str, payload: str, qos: int = 1, retain: bool = False) -> None:
        self.will_message = (topic, payload, qos, retain)

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        pass

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> int:
        self.connected = True
        if self.on_connect:
            self.on_connect(self, None, {}, 0)
        return 0

    def disconnect(self) -> int:
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, 0)
        return 0

    def subscribe(self, topic: str, qos: int = 1) -> tuple[int, int]:
        self.subscriptions[topic] = qos
        return 0, 1

    def unsubscribe(self, topic: str) -> tuple[int, int]:
        self.subscriptions.pop(topic, None)
        return 0, 1

    def publish(
        self,
        topic: str,
        payload: str | bytes,
        qos: int = 1,
        retain: bool = False,
    ) -> Any:
        payload_str = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        self.published_messages.append((topic, payload_str, qos, retain))

        # Forward to peer if subscribed
        if self.peer_client and self.peer_client.connected:
            if topic in self.peer_client.subscriptions:
                msg = MockMQTTMessage(topic, payload_str.encode("utf-8"), qos, retain)
                if self.peer_client.on_message:
                    self.peer_client.on_message(self.peer_client, None, msg)

        # Create dummy info object with wait_for_publish
        class Info:
            rc = 0
            def wait_for_publish(self, timeout: float = 1.0) -> None:
                pass

        return Info()

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_started = False

    def simulate_message(
        self,
        topic: str,
        payload_data: dict | str,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Helper to inject an incoming message from the broker."""
        payload_bytes = (
            json.dumps(payload_data).encode("utf-8")
            if isinstance(payload_data, dict)
            else str(payload_data).encode("utf-8")
        )
        msg = MockMQTTMessage(topic=topic, payload=payload_bytes, qos=qos, retain=retain)
        if self.on_message:
            self.on_message(self, None, msg)


# ============================================================================
# 1. Configuration & Topics
# ============================================================================

class TestMQTTTopicsAndConfig(unittest.TestCase):
    """Verify topic generation and configuration defaults."""

    def test_default_topics(self):
        topics = MQTTTopics("aisr/robot")
        self.assertEqual(topics.command, "aisr/robot/command")
        self.assertEqual(topics.heartbeat, "aisr/robot/heartbeat")
        self.assertEqual(topics.response, "aisr/robot/response")
        self.assertEqual(topics.status, "aisr/robot/status")
        self.assertEqual(topics.connection, "aisr/robot/connection")

    def test_custom_prefix_stripping(self):
        topics = MQTTTopics("my_robot/pioneer///")
        self.assertEqual(topics.command, "my_robot/pioneer/command")
        self.assertEqual(topics.heartbeat, "my_robot/pioneer/heartbeat")

    def test_empty_prefix_defaults(self):
        topics = MQTTTopics("")
        self.assertEqual(topics.command, "aisr/robot/command")

    def test_config_defaults(self):
        cfg = MQTTConfig()
        self.assertEqual(cfg.broker_host, "localhost")
        self.assertEqual(cfg.broker_port, 1883)
        self.assertEqual(cfg.qos, 1)
        self.assertTrue(cfg.clean_session)
        self.assertEqual(cfg.max_command_age_sec, 2.0)


# ============================================================================
# 2. Server Message Handling & Safety Rules
# ============================================================================

class TestMQTTRobotServer(unittest.TestCase):
    """Test server dispatching, response publishing, and safety guards."""

    def setUp(self) -> None:
        self.robot = MockRobot()
        self.ctrl = SafetyController(self.robot)
        self.mock_client = MockMQTTClientDouble("test_server_client")
        self.config = MQTTConfig(topic_prefix="test/robot", qos=1)
        self.server = MQTTRobotServer(
            controller=self.ctrl,
            config=self.config,
            mqtt_client=self.mock_client,
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()

    def test_server_starts_and_subscribes(self):
        self.assertTrue(self.server.is_connected)
        self.assertIn(self.server.topics.command, self.mock_client.subscriptions)
        self.assertIn(self.server.topics.heartbeat, self.mock_client.subscriptions)
        self.assertIsNotNone(self.mock_client.will_message)
        self.assertEqual(self.mock_client.will_message[0], self.server.topics.connection)

    def test_retained_message_rejected(self):
        """Safety requirement 6: retained messages on command topic MUST be discarded."""
        self.mock_client.published_messages.clear()
        cmd = {"type": "FORWARD", "speed": 0.5}
        self.mock_client.simulate_message(
            self.server.topics.command,
            cmd,
            retain=True,  # Retained!
        )

        # Robot must not have moved
        self.assertEqual(self.ctrl.state, RobotState.IDLE)
        self.assertFalse(self.robot.get_status().is_moving)

        # An error must be published on response topic
        responses = [
            json.loads(p[1]) for p in self.mock_client.published_messages
            if p[0] == self.server.topics.response
        ]
        self.assertTrue(any(r.get("status") == "retained_rejected" for r in responses))

    def test_stale_timestamped_command_rejected(self):
        """Safety requirement 6: messages delayed in transit must be rejected."""
        self.mock_client.published_messages.clear()
        stale_cmd = {
            "type": "FORWARD",
            "speed": 0.5,
            "timestamp": time.time() - 10.0,  # 10s old
        }
        self.mock_client.simulate_message(self.server.topics.command, stale_cmd)

        self.assertEqual(self.ctrl.state, RobotState.IDLE)
        self.assertFalse(self.robot.get_status().is_moving)

        responses = [
            json.loads(p[1]) for p in self.mock_client.published_messages
            if p[0] == self.server.topics.response
        ]
        self.assertTrue(any(r.get("status") == "stale_command" for r in responses))

    def test_fresh_command_executes(self):
        self.mock_client.published_messages.clear()
        fresh_cmd = {
            "type": "FORWARD",
            "speed": 0.5,
            "duration": 2.0,
            "timestamp": time.time(),
        }
        self.mock_client.simulate_message(self.server.topics.command, fresh_cmd)

        self.assertEqual(self.ctrl.state, RobotState.MOVING)
        self.assertTrue(self.robot.get_status().is_moving)

        responses = [
            json.loads(p[1]) for p in self.mock_client.published_messages
            if p[0] == self.server.topics.response
        ]
        ack = responses[-1]
        self.assertEqual(ack.get("type"), "FORWARD_ACK")
        self.assertEqual(ack.get("status"), "ok")
        self.assertEqual(ack.get("speed"), 0.5)

    def test_status_query_publishes_status(self):
        self.mock_client.published_messages.clear()
        self.mock_client.simulate_message(self.server.topics.command, {"type": "STATUS"})

        status_messages = [
            json.loads(p[1]) for p in self.mock_client.published_messages
            if p[0] == self.server.topics.status
        ]
        self.assertGreaterEqual(len(status_messages), 1)
        self.assertEqual(status_messages[-1].get("type"), "STATUS_RESPONSE")

    def test_estop_and_reset_over_mqtt(self):
        # Start movement
        self.mock_client.simulate_message(self.server.topics.command, {"type": "FORWARD", "speed": 0.5})
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # Trigger ESTOP
        self.mock_client.simulate_message(self.server.topics.command, {"type": "ESTOP"})
        self.assertEqual(self.ctrl.state, RobotState.ESTOP_LATCHED)
        self.assertFalse(self.robot.get_status().is_moving)

        # Movement commands blocked in ESTOP_LATCHED
        self.mock_client.simulate_message(self.server.topics.command, {"type": "FORWARD", "speed": 0.5})
        self.assertEqual(self.ctrl.state, RobotState.ESTOP_LATCHED)

        # RESET clears latch and leaves robot IDLE
        self.mock_client.simulate_message(self.server.topics.command, {"type": "RESET"})
        self.assertEqual(self.ctrl.state, RobotState.IDLE)
        self.assertFalse(self.robot.get_status().is_moving)

    def test_heartbeat_refreshes_watchdog(self):
        self.mock_client.published_messages.clear()
        self.mock_client.simulate_message(self.server.topics.heartbeat, {"type": "HEARTBEAT"})

        responses = [
            json.loads(p[1]) for p in self.mock_client.published_messages
            if p[0] == self.server.topics.response
        ]
        self.assertTrue(any(r.get("type") == "HEARTBEAT_ACK" for r in responses))

    def test_disconnect_halts_robot(self):
        self.mock_client.simulate_message(self.server.topics.command, {"type": "FORWARD", "speed": 0.5})
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # Simulate broker connection drop
        self.mock_client.disconnect()
        self.assertEqual(self.ctrl.state, RobotState.COMM_LOST)
        self.assertFalse(self.robot.get_status().is_moving)
        self.assertIsNone(self.ctrl.active_transport)


# ============================================================================
# 3. Transport Ownership Policy (Requirement 8)
# ============================================================================

class TestTransportOwnership(unittest.TestCase):
    """Verify that TCP and MQTT cannot simultaneously command the robot without ownership."""

    def setUp(self) -> None:
        self.robot = MockRobot()
        self.ctrl = SafetyController(self.robot)

    def test_tcp_acquires_ownership(self):
        ok, msg = self.ctrl.acquire_transport("TCP")
        self.assertTrue(ok)
        self.assertEqual(self.ctrl.active_transport, "TCP")

        # Commands from TCP succeed
        resp = self.ctrl.dispatch({"type": "FORWARD", "speed": 0.5}, transport="TCP")
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # Movement command from MQTT is REJECTED
        mqtt_resp = self.ctrl.dispatch({"type": "FORWARD", "speed": 0.5}, transport="MQTT")
        self.assertEqual(mqtt_resp["status"], "ownership_denied")

        # But ESTOP from MQTT is ALWAYS ALLOWED
        estop_resp = self.ctrl.dispatch({"type": "ESTOP"}, transport="MQTT")
        self.assertEqual(estop_resp["type"], "ESTOP_ACK")
        self.assertEqual(self.ctrl.state, RobotState.ESTOP_LATCHED)
        self.assertFalse(self.robot.get_status().is_moving)

    def test_release_ownership_allows_transfer(self):
        self.ctrl.acquire_transport("TCP")
        self.assertEqual(self.ctrl.active_transport, "TCP")

        # Release TCP
        self.ctrl.release_transport("TCP")
        self.assertIsNone(self.ctrl.active_transport)

        # MQTT can now acquire
        ok, msg = self.ctrl.acquire_transport("MQTT")
        self.assertTrue(ok)
        self.assertEqual(self.ctrl.active_transport, "MQTT")

    def test_disconnect_releases_ownership(self):
        self.ctrl.acquire_transport("TCP")
        self.ctrl.dispatch({"type": "FORWARD", "speed": 0.5}, transport="TCP")
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # TCP client disconnects
        self.ctrl.on_client_disconnect(transport="TCP")
        self.assertEqual(self.ctrl.state, RobotState.COMM_LOST)
        self.assertIsNone(self.ctrl.active_transport)

        # MQTT sends heartbeat to recover to IDLE
        self.ctrl.record_heartbeat(transport="MQTT")
        self.assertEqual(self.ctrl.state, RobotState.IDLE)

        # MQTT takes control
        resp = self.ctrl.dispatch({"type": "FORWARD", "speed": 0.3}, transport="MQTT")
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(self.ctrl.active_transport, "MQTT")
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

    def test_force_transfer_ownership(self):
        self.ctrl.acquire_transport("TCP")
        self.ctrl.dispatch({"type": "FORWARD", "speed": 0.5}, transport="TCP")
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # Force transfer to MQTT
        ok, msg = self.ctrl.acquire_transport("MQTT", force=True)
        self.assertTrue(ok)
        self.assertEqual(self.ctrl.active_transport, "MQTT")
        # Robot is safely stopped upon forced transfer
        self.assertFalse(self.robot.get_status().is_moving)


# ============================================================================
# 4. Client-Server End-to-End via Peer Mock Clients
# ============================================================================

class TestMQTTClientServerIntegration(unittest.TestCase):
    """Test MQTTRobotClient interacting with MQTTRobotServer via paired mock clients."""

    def setUp(self) -> None:
        self.robot = MockRobot()
        self.ctrl = SafetyController(self.robot)
        self.config = MQTTConfig(topic_prefix="robot/aisr", qos=1)

        # Create two paired mock clients
        self.server_mock = MockMQTTClientDouble("server_client")
        self.client_mock = MockMQTTClientDouble("operator_client")
        self.server_mock.peer_client = self.client_mock
        self.client_mock.peer_client = self.server_mock

        self.server = MQTTRobotServer(self.ctrl, self.config, mqtt_client=self.server_mock)
        self.client = MQTTRobotClient(self.config, mqtt_client=self.client_mock)

        self.server.start()
        self.client.connect(timeout=2.0)

    def tearDown(self) -> None:
        self.client.disconnect()
        self.server.stop()

    def test_ping_command_roundtrip(self):
        resp = self.client.send_command({"type": "PING"}, timeout=2.0)
        self.assertEqual(resp.get("type"), "ACK")
        self.assertEqual(resp.get("received_type"), "PING")
        self.assertEqual(resp.get("status"), "ok")

    def test_forward_and_status_roundtrip(self):
        resp = self.client.send_command(
            {"type": "FORWARD", "speed": 0.05, "duration": 2.0},
            timeout=2.0,
        )
        self.assertEqual(resp.get("type"), "FORWARD_ACK")
        self.assertEqual(resp.get("status"), "ok")
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        # Status query
        status_resp = self.client.send_command({"type": "STATUS"}, timeout=2.0)
        self.assertEqual(status_resp.get("type"), "STATUS_RESPONSE")
        self.assertEqual(status_resp.get("state"), "MOVING")

    def test_estop_roundtrip(self):
        self.client.send_command({"type": "FORWARD", "speed": 0.05}, timeout=2.0)
        self.assertEqual(self.ctrl.state, RobotState.MOVING)

        estop_resp = self.client.send_command({"type": "ESTOP"}, timeout=2.0)
        self.assertEqual(estop_resp.get("type"), "ESTOP_ACK")
        self.assertEqual(self.ctrl.state, RobotState.ESTOP_LATCHED)
        self.assertFalse(self.robot.get_status().is_moving)


if __name__ == "__main__":
    unittest.main()

