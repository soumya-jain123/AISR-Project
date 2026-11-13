"""
mqtt_server.py – AISR robot teleoperation server over MQTT.

Connects the robot backend and SafetyController to an MQTT broker,
subscribes to command and heartbeat topics, and publishes responses
and status updates.

Run from project root:
    python -m src.mqtt_server
    python -m src.mqtt_server --broker-host localhost --broker-port 1883 --backend coppeliasim
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

from src.mqtt_transport import MQTTConfig, MQTTRobotServer
from src.robot_interface import create_robot_backend
from src.safety import SafetyController


def main() -> None:
    parser = argparse.ArgumentParser(description="AISR Robot Teleoperation MQTT Server")
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
        "--backend",
        "-b",
        default=os.environ.get("AISR_ROBOT_BACKEND", "mock"),
        help="Robot backend: 'mock' or 'coppeliasim' (default: mock)",
    )
    parser.add_argument(
        "--qos",
        type=int,
        default=1,
        choices=[0, 1],
        help="MQTT QoS level for teleoperation messages (default: 1)",
    )
    parser.add_argument(
        "--max-command-age",
        type=float,
        default=2.0,
        help="Maximum allowed transit age in seconds for movement commands (default: 2.0)",
    )

    args = parser.parse_args()

    print("=" * 65)
    print("AISR Robot Teleoperation MQTT Server")
    print("=" * 65)
    print(f"Broker Endpoint : {args.broker_host}:{args.broker_port}")
    print(f"Topic Prefix    : {args.topic_prefix}")
    print(f"Robot Backend   : {args.backend}")
    print(f"QoS Level       : {args.qos}")
    print(f"Max Command Age : {args.max_command_age}s")
    print("=" * 65)

    print(f"Initializing robot backend: '{args.backend}'...")
    try:
        robot = create_robot_backend(args.backend)
    except Exception as exc:
        print(f"Fatal error initializing robot backend '{args.backend}': {exc}", file=sys.stderr)
        sys.exit(1)

    controller = SafetyController(robot)
    controller.start_watchdog()

    config = MQTTConfig(
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        topic_prefix=args.topic_prefix,
        qos=args.qos,
        max_command_age_sec=args.max_command_age,
        client_id=f"aisr_robot_server_{int(time.time())}",
    )

    server = MQTTRobotServer(controller, config)

    shutdown_event = threading.Event()

    def _sig_handler(signum: int, frame: Any) -> None:
        print("\nShutdown signal received. Stopping server...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        print(f"Connecting to MQTT broker at {config.broker_host}:{config.broker_port}...")
        server.start()
        print("Server is online and listening for teleoperation commands.")
        print(f"  Command topic   : {server.topics.command}")
        print(f"  Heartbeat topic : {server.topics.heartbeat}")
        print(f"  Response topic  : {server.topics.response}")
        print(f"  Status topic    : {server.topics.status}")
        print("Press Ctrl+C to terminate.")

        while not shutdown_event.is_set():
            time.sleep(0.5)

    except Exception as exc:
        print(f"Error running MQTT server: {exc}", file=sys.stderr)
    finally:
        print("Cleaning up server and stopping robot...")
        server.stop()
        controller.stop_watchdog()
        print("Server exited.")


if __name__ == "__main__":
    main()

