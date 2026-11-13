"""
mqtt_client.py – Interactive AISR robot teleoperation client over MQTT.

Connects to the configured MQTT broker, sends automatic heartbeats every 0.5s,
subscribes to response and status topics, and provides an interactive operator REPL.

Run from project root:
    python -m src.mqtt_client
    python -m src.mqtt_client --broker-host localhost --broker-port 1883
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from src.mqtt_transport import MQTTConfig, MQTTRobotClient
from src.tcp_client import _parse_user_command


_HELP_TEXT = """
Available teleoperation commands (MQTT):
  FORWARD [speed] [duration]   – Move forward (default 0.5 m/s; duration in seconds, continuous if omitted)
  BACKWARD [speed] [duration]  – Move backward (duration in seconds, continuous if omitted)
  TURN_LEFT [speed] [duration] – Turn left (default 45 deg/s)
  TURN_RIGHT [speed] [duration]– Turn right
  MOVE <linear> <angular>      – Set velocities directly
  GOTO <x> <y>                 – Navigate to coordinates
  STOP                         – Stop the robot
  STATUS                       – Query robot state
  ESTOP                        – Emergency stop (latched)
  RESET                        – Clear emergency stop
  PING                         – Basic ping
  help                         – Show this help
  quit                         – Disconnect and exit
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="AISR Robot Teleoperation MQTT Operator Client")
    parser.add_argument(
        "--broker-host",
        default=os.environ.get("AISR_MQTT_BROKER_HOST", "localhost"),
        help="MQTT broker host address (default: localhost)",
    )
    parser.add_argument(
        "--broker-port",
        type=int,
        default=int(os.environ.get("AISR_MQTT_BROKER_PORT", "1883")),
        help="MQTT broker port (default: 1883)",
    )
    parser.add_argument(
        "--topic-prefix",
        default=os.environ.get("AISR_MQTT_TOPIC_PREFIX", "aisr/robot"),
        help="MQTT topic prefix (default: aisr/robot)",
    )
    parser.add_argument(
        "--qos",
        type=int,
        default=1,
        choices=[0, 1],
        help="MQTT QoS level (default: 1)",
    )

    args = parser.parse_args()

    config = MQTTConfig(
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        topic_prefix=args.topic_prefix,
        qos=args.qos,
        client_id=f"aisr_operator_{int(time.time())}",
    )

    print("=" * 65)
    print("AISR Robot Teleoperation MQTT Client")
    print("=" * 65)
    print(f"Connecting to broker at {config.broker_host}:{config.broker_port}...")
    print(f"Topic prefix: {config.topic_prefix}")

    client = MQTTRobotClient(config)

    try:
        client.connect(timeout=5.0)
    except Exception as exc:
        print(f"Could not connect to MQTT broker: {exc}", file=sys.stderr)
        print("Please ensure your MQTT broker (e.g. Mosquitto) is running.", file=sys.stderr)
        sys.exit(1)

    print("Connected to MQTT broker.")
    print("Automatic heartbeats started in background thread.")
    print("Type 'help' for available commands or 'quit' to exit.")

    try:
        while True:
            try:
                user_input = input("\nAISR [MQTT]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit"):
                break

            if user_input.lower() in ("help", "?"):
                print(_HELP_TEXT)
                continue

            msg = _parse_user_command(user_input)
            if msg is None:
                continue

            print(f"  → Sent (MQTT): {json.dumps(msg)}")
            try:
                resp = client.send_command(msg, timeout=4.0)
                print(f"  ← Response: {json.dumps(resp)}")
            except TimeoutError:
                print("  [ERROR] Request timed out. Is the robot MQTT server active?")
            except Exception as exc:
                print(f"  [ERROR] {exc}")

    finally:
        print("Disconnecting from MQTT broker...")
        client.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    main()

