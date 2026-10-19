"""
Main entry point for starting the Robot Server.
Supports both live CoppeliaSim connection and headless mock mode for testing.
"""

import argparse
import logging
import os
import signal
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.config import DEFAULT_HOST, DEFAULT_PORT
from server.mock_driver import MockRobotDriver
from server.robot_server import RobotServer
from server.sim_driver import CoppeliaSimDriver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RobotServerMain")


def main():
    parser = argparse.ArgumentParser(description="Start Networked Robot Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host/IP to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use internal kinematic mock driver instead of CoppeliaSim",
    )
    args = parser.parse_args()

    if args.mock:
        logger.info("Starting server with MOCK robot driver (headless mode)...")
        driver = MockRobotDriver()
    else:
        logger.info("Connecting to CoppeliaSim ZMQ API...")
        driver = CoppeliaSimDriver()
        if not driver.connect():
            logger.warning(
                "CoppeliaSim is not reachable! Falling back to MOCK driver so system can still run. "
                "To connect to simulator, start CoppeliaSim and re-run without --mock."
            )
            driver = MockRobotDriver()

    server = RobotServer(driver=driver, host=args.host, port=args.port)

    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received. Stopping server...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    server.start()
    print("=" * 60)
    print(f"  Robot Server is active on {args.host}:{args.port}")
    print("  Press Ctrl+C to terminate.")
    print("=" * 60)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        shutdown_handler(None, None)


if __name__ == "__main__":
    main()
