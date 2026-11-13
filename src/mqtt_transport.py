"""
mqtt_transport.py – MQTT teleoperation transport for AISR robot.

Implements MQTT-based communication for the AISR robot system, providing:
- Configurable broker endpoint, keepalive, and QoS settings.
- Explicit command, heartbeat, response, and status topic hierarchy.
- Retention rejection and timestamp staleness checks to prevent queued/stale motion.
- Application-level heartbeat monitoring integrated with SafetyController.
- Transport ownership policy to prevent TCP and MQTT collision.
- Clean session and Last Will and Testament (LWT) support.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

try:
    import paho.mqtt.client as paho_mqtt
    from paho.mqtt.enums import CallbackAPIVersion
    PAHO_AVAILABLE = True
except (ImportError, AttributeError):
    try:
        import paho.mqtt.client as paho_mqtt  # fallback for v1
        CallbackAPIVersion = None  # type: ignore[assignment,misc]
        PAHO_AVAILABLE = True
    except ImportError:
        paho_mqtt = None  # type: ignore[assignment]
        CallbackAPIVersion = None  # type: ignore[assignment,misc]
        PAHO_AVAILABLE = False

from src.safety import RobotState, SafetyController

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration and Topic Hierarchy
# ---------------------------------------------------------------------------

@dataclass
class MQTTConfig:
    """Configuration parameters for MQTT teleoperation transport."""
    broker_host: str = "localhost"
    broker_port: int = 1883
    keepalive: int = 60
    topic_prefix: str = "aisr/robot"
    client_id: str | None = None
    username: str | None = None
    password: str | None = None
    qos: int = 1                         # Default QoS for commands/telemetry
    clean_session: bool = True           # Discard stale broker queues on connect
    max_command_age_sec: float = 2.0     # Reject commands delayed beyond this age
    heartbeat_interval: float = 0.5      # Frequency of application heartbeats (sec)
    status_publish_interval: float = 0.0 # Periodic status interval (0 = event-only)


class MQTTTopics:
    """Resolves and validates topic names for AISR teleoperation."""

    def __init__(self, prefix: str = "aisr/robot") -> None:
        self.prefix = prefix.strip().rstrip("/")
        if not self.prefix:
            self.prefix = "aisr/robot"

        self.command = f"{self.prefix}/command"
        self.heartbeat = f"{self.prefix}/heartbeat"
        self.response = f"{self.prefix}/response"
        self.status = f"{self.prefix}/status"
        self.connection = f"{self.prefix}/connection"

    def __repr__(self) -> str:
        return (
            f"MQTTTopics(command='{self.command}', heartbeat='{self.heartbeat}', "
            f"response='{self.response}', status='{self.status}', connection='{self.connection}')"
        )


def create_paho_client(
    client_id: str | None = None,
    clean_session: bool = True,
    userdata: Any = None,
) -> Any:
    """Instantiate a Paho MQTT Client compatible with v1 and v2 API."""
    if not PAHO_AVAILABLE:
        raise ImportError(
            "paho-mqtt package is required for MQTT transport. "
            "Install with: pip install paho-mqtt>=2.0.0"
        )

    if CallbackAPIVersion is not None:
        return paho_mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=client_id or "",
            clean_session=clean_session,
            userdata=userdata,
        )
    return paho_mqtt.Client(
        client_id=client_id or "",
        clean_session=clean_session,
        userdata=userdata,
    )


# ---------------------------------------------------------------------------
# MQTTRobotServer: Connects robot backend / SafetyController to MQTT Broker
# ---------------------------------------------------------------------------

class MQTTRobotServer:
    """Robot-side MQTT transport server.

    Subscribes to command and heartbeat topics, dispatches commands through
    the SafetyController with transport ownership checking, and publishes
    structured JSON responses, status updates, and connection telemetry.
    """

    def __init__(
        self,
        controller: SafetyController,
        config: MQTTConfig | None = None,
        mqtt_client: Any | None = None,
    ) -> None:
        self.controller = controller
        self.config = config or MQTTConfig()
        self.topics = MQTTTopics(self.config.topic_prefix)

        self._client = mqtt_client
        self._owns_client = mqtt_client is None
        self._connected = threading.Event()
        self._stop_event = threading.Event()
        self._status_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        """Connect to broker, configure LWT, and start message loop."""
        if self._client is None:
            self._client = create_paho_client(
                client_id=self.config.client_id,
                clean_session=self.config.clean_session,
            )

        if self.config.username:
            self._client.username_pw_set(self.config.username, self.config.password)

        # Configure Last Will and Testament (LWT) for unexpected disconnects
        lwt_payload = json.dumps({
            "status": "offline",
            "reason": "unexpected_disconnect",
            "timestamp": time.time(),
        })
        self._client.will_set(self.topics.connection, lwt_payload, qos=1, retain=True)

        # Wire callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._stop_event.clear()

        # Connect
        self._client.connect(
            self.config.broker_host,
            self.config.broker_port,
            self.config.keepalive,
        )

        # Start background network loop if paho client supports loop_start
        if hasattr(self._client, "loop_start"):
            self._client.loop_start()

        # Optional periodic status publishing
        if self.config.status_publish_interval > 0:
            self._status_thread = threading.Thread(
                target=self._periodic_status_loop,
                daemon=True,
                name="mqtt-server-status",
            )
            self._status_thread.start()

    def stop(self) -> None:
        """Publish graceful offline state, disconnect, and stop network loop."""
        self._stop_event.set()

        # Publish graceful offline status
        if self._client and self.is_connected:
            try:
                offline_msg = json.dumps({
                    "status": "offline",
                    "reason": "clean_shutdown",
                    "timestamp": time.time(),
                })
                self._client.publish(self.topics.connection, offline_msg, qos=1, retain=True)
            except Exception:
                pass

        # Disconnect client
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            if hasattr(self._client, "loop_stop"):
                self._client.loop_stop()

        self._connected.clear()

        # Halt robot and release transport ownership
        self.controller.on_client_disconnect(transport="MQTT")

        if self._status_thread and self._status_thread.is_alive():
            self._status_thread.join(timeout=2.0)

    # -- Internal MQTT Callbacks --

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
        """Invoked when connection to broker is established."""
        rc_code = rc.value if hasattr(rc, "value") else rc
        if rc_code == 0:
            self._connected.set()
            # Subscribe to command and heartbeat topics
            client.subscribe(self.topics.command, qos=self.config.qos)
            client.subscribe(self.topics.heartbeat, qos=self.config.qos)

            # Publish online status (retained)
            online_msg = json.dumps({
                "status": "online",
                "timestamp": time.time(),
                "node": "robot_server",
            })
            client.publish(self.topics.connection, online_msg, qos=1, retain=True)

            # Publish current robot state
            self.publish_status()
            logger.info("MQTTRobotServer connected and subscribed to %s and %s",
                        self.topics.command, self.topics.heartbeat)
        else:
            logger.error("MQTTRobotServer failed to connect to broker, return code: %s", rc_code)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any, **kwargs: Any) -> None:
        """Invoked when connection to broker drops."""
        self._connected.clear()
        # Immediately halt robot, cancel timers, and set COMM_LOST
        self.controller.on_client_disconnect(transport="MQTT")
        logger.warning("MQTTRobotServer disconnected from broker. Robot stopped and transport released.")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Handle incoming message on command or heartbeat topic."""
        topic = getattr(msg, "topic", "")
        payload_bytes = getattr(msg, "payload", b"")
        is_retained = bool(getattr(msg, "retain", False))

        # 1. Retention Safety Check: NEVER execute retained movement or commands!
        if is_retained and (topic == self.topics.command or topic == self.topics.heartbeat):
            logger.warning("Discarding retained message on %s for safety.", topic)
            rejection = {
                "type": "ERROR",
                "status": "retained_rejected",
                "message": "Retained messages are rejected for teleoperation safety.",
            }
            self._publish_response(rejection)
            return

        # 2. Deserialization & validation
        try:
            payload_str = payload_bytes.decode("utf-8")
            data = json.loads(payload_str)
        except Exception as exc:
            rejection = {
                "type": "ERROR",
                "status": "invalid_json",
                "message": f"Malformed JSON payload: {exc}",
            }
            self._publish_response(rejection)
            return

        if not isinstance(data, dict) or "type" not in data:
            rejection = {
                "type": "ERROR",
                "status": "invalid",
                "message": "Payload must be a JSON object containing a 'type' field.",
            }
            self._publish_response(rejection)
            return

        msg_type = str(data["type"]).strip().upper()

        # 3. Staleness / Timestamp Expiry Check (Safety Requirement 6)
        if "timestamp" in data and msg_type != "ESTOP":
            try:
                sent_time = float(data["timestamp"])
                age = time.time() - sent_time
                if age > self.config.max_command_age_sec:
                    logger.warning("Rejecting stale command '%s' (age: %.3fs > limit %.3fs)",
                                   msg_type, age, self.config.max_command_age_sec)
                    stale_err = {
                        "type": "ERROR",
                        "status": "stale_command",
                        "message": (
                            f"Command '{msg_type}' discarded: transit age {age:.2f}s "
                            f"exceeds limit {self.config.max_command_age_sec:.2f}s."
                        ),
                    }
                    self._publish_response(stale_err)
                    return
            except (ValueError, TypeError):
                pass

        # 4. Route command
        if topic == self.topics.heartbeat or msg_type == "HEARTBEAT":
            resp = self.controller.record_heartbeat(transport="MQTT")
            self._publish_response(resp)
            return

        # Regular command on command topic
        resp = self.controller.dispatch(data, transport="MQTT")
        self._publish_response(resp)

        # Publish updated status snapshot on successful movement or status query
        if msg_type == "STATUS" or resp.get("status") == "ok":
            self.publish_status()

    # -- Response & Telemetry Publishing --

    def _publish_response(self, resp: dict) -> None:
        """Publish command ACK or error response to response topic."""
        if not self._client:
            return
        payload = json.dumps(resp)
        try:
            # Responses are NEVER retained
            self._client.publish(self.topics.response, payload, qos=self.config.qos, retain=False)
        except Exception as exc:
            logger.error("Failed to publish response: %s", exc)

    def publish_status(self) -> None:
        """Query current robot status and publish to status topic."""
        if not self._client:
            return
        status = self.controller.handle_status()
        status["timestamp"] = time.time()
        payload = json.dumps(status)
        try:
            # Status telemetry is not retained by default
            self._client.publish(self.topics.status, payload, qos=self.config.qos, retain=False)
        except Exception as exc:
            logger.error("Failed to publish status: %s", exc)

    def _periodic_status_loop(self) -> None:
        """Background loop for periodic status telemetry broadcasting."""
        interval = self.config.status_publish_interval
        while not self._stop_event.wait(timeout=interval):
            if self.is_connected:
                self.publish_status()


# ---------------------------------------------------------------------------
# MQTTRobotClient: Operator Teleoperation Client over MQTT
# ---------------------------------------------------------------------------

class MQTTRobotClient:
    """Operator-side MQTT client.

    Maintains automatic background heartbeats on `<prefix>/heartbeat`,
    subscribes to `<prefix>/response` and `<prefix>/status`, and exposes
    thread-safe synchronous `send_command()` and status inspection.
    """

    def __init__(
        self,
        config: MQTTConfig | None = None,
        mqtt_client: Any | None = None,
        on_status: Callable[[dict], None] | None = None,
        on_response: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config or MQTTConfig()
        self.topics = MQTTTopics(self.config.topic_prefix)
        self.on_status = on_status
        self.on_response = on_response

        self._client = mqtt_client
        self._owns_client = mqtt_client is None
        self._connected = threading.Event()
        self._stop_event = threading.Event()
        self._resp_queue: queue.Queue[dict] = queue.Queue()
        self._latest_status: dict = {}
        self._status_lock = threading.Lock()
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def latest_status(self) -> dict:
        with self._status_lock:
            return dict(self._latest_status)

    def connect(self, timeout: float = 5.0) -> None:
        """Connect to broker, subscribe to response/status topics, and launch heartbeats."""
        if self._client is None:
            self._client = create_paho_client(
                client_id=self.config.client_id,
                clean_session=self.config.clean_session,
            )

        if self.config.username:
            self._client.username_pw_set(self.config.username, self.config.password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._stop_event.clear()
        self._client.connect(
            self.config.broker_host,
            self.config.broker_port,
            self.config.keepalive,
        )

        if hasattr(self._client, "loop_start"):
            self._client.loop_start()

        # Wait for connection event
        if not self._connected.wait(timeout=timeout):
            self.disconnect()
            raise TimeoutError(f"Could not connect to MQTT broker at {self.config.broker_host}:{self.config.broker_port}")

        # Start application heartbeat daemon thread
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="mqtt-client-heartbeat",
        )
        self._heartbeat_thread.start()

    def disconnect(self) -> None:
        """Halt heartbeat sender, unsubscribe, and close MQTT connection."""
        self._stop_event.set()

        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            if hasattr(self._client, "loop_stop"):
                self._client.loop_stop()

        self._connected.clear()

        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)

    # -- Callbacks --

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
        rc_code = rc.value if hasattr(rc, "value") else rc
        if rc_code == 0:
            self._connected.set()
            client.subscribe(self.topics.response, qos=self.config.qos)
            client.subscribe(self.topics.status, qos=self.config.qos)
            logger.info("MQTTRobotClient connected and subscribed to response and status topics.")

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any, **kwargs: Any) -> None:
        self._connected.clear()
        logger.warning("MQTTRobotClient disconnected from broker.")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        topic = getattr(msg, "topic", "")
        payload_bytes = getattr(msg, "payload", b"")

        try:
            data = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return

        if not isinstance(data, dict):
            return

        msg_type = data.get("type", "")

        if topic == self.topics.status:
            with self._status_lock:
                self._latest_status = data
            if self.on_status:
                try:
                    self.on_status(data)
                except Exception:
                    pass
        elif topic == self.topics.response:
            # Heartbeat ACKs are processed silently without clogging command response queue
            if msg_type != "HEARTBEAT_ACK":
                self._resp_queue.put(data)
            if self.on_response:
                try:
                    self.on_response(data)
                except Exception:
                    pass

    # -- Commands & Heartbeat --

    def _heartbeat_loop(self) -> None:
        """Publish heartbeat every heartbeat_interval seconds."""
        interval = self.config.heartbeat_interval
        while not self._stop_event.wait(timeout=interval):
            if not self.is_connected or not self._client:
                continue
            hb = {
                "type": "HEARTBEAT",
                "timestamp": time.time(),
            }
            try:
                # Heartbeats are never retained
                self._client.publish(
                    self.topics.heartbeat,
                    json.dumps(hb),
                    qos=self.config.qos,
                    retain=False,
                )
            except Exception:
                pass

    def send_command(self, command: dict, timeout: float = 5.0) -> dict:
        """Publish a command to the robot and block until response is received.

        Automatically attaches an issuance timestamp for freshness checking.
        """
        if not self.is_connected or not self._client:
            raise ConnectionError("MQTT client is not connected to broker.")

        # Ensure timestamp is set for transit staleness protection
        cmd = dict(command)
        if "timestamp" not in cmd:
            cmd["timestamp"] = time.time()

        payload = json.dumps(cmd)

        # Clear any old unread responses
        while not self._resp_queue.empty():
            try:
                self._resp_queue.get_nowait()
            except queue.Empty:
                break

        # Publish command (NEVER retained!)
        self._client.publish(self.topics.command, payload, qos=self.config.qos, retain=False)

        try:
            resp = self._resp_queue.get(timeout=timeout)
            return resp
        except queue.Empty:
            raise TimeoutError(f"No response received for command '{cmd.get('type')}' within {timeout}s.")

