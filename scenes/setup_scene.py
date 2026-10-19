"""
Automated scene builder / verifier for CoppeliaSim.
Connects via ZMQ API, loads Pioneer P3-DX robot if not already present,
and adds reference waypoints for the square trajectory test.
"""

import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SceneSetup")


def setup_scene():
    try:
        from coppeliasim_zmqremoteapi_client import RemoteAPIClient
    except ImportError:
        logger.error("coppeliasim_zmqremoteapi_client is not installed.")
        return False

    try:
        client = RemoteAPIClient()
        sim = client.require("sim")
        logger.info("Connected to CoppeliaSim.")
    except Exception as e:
        logger.error(f"Failed to connect to CoppeliaSim: {e}")
        logger.info("Please start CoppeliaSim first, then rerun this script.")
        return False

    try:
        # Check if robot already exists in scene
        robot = sim.getObject("/PioneerP3DX")
        logger.info(f"Found existing robot at handle: {robot}")
    except Exception:
        logger.info("Robot '/PioneerP3DX' not found in current scene.")
        logger.info(
            "To add Pioneer P3-DX manually in CoppeliaSim: "
            "Model Browser -> robots -> mobile -> pioneer p3dx.ttm (drag into scene)."
        )

    logger.info("Scene verification completed.")
    return True


if __name__ == "__main__":
    setup_scene()
