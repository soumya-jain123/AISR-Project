"""
Global configuration and constants for Networked Robot Teleoperation.
"""

# Networking
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 55000

# Heartbeat & Safety Timers (in seconds)
HEARTBEAT_INTERVAL = 0.5   # Client sends heartbeat every 0.5 s
HEARTBEAT_TIMEOUT = 1.5    # Server assumes connection lost if no heartbeat for 1.5 s
FEEDBACK_INTERVAL = 0.1    # Server sends status feedback at 10 Hz (every 100 ms)

# Robot States
STATE_IDLE = "IDLE"
STATE_MOVING = "MOVING"
STATE_COMM_LOST = "COMM_LOST"
STATE_ESTOP_LATCHED = "ESTOP_LATCHED"

ALL_STATES = {STATE_IDLE, STATE_MOVING, STATE_COMM_LOST, STATE_ESTOP_LATCHED}

# Command Names
CMD_FORWARD = "forward"
CMD_BACK = "back"
CMD_LEFT = "left"
CMD_RIGHT = "right"
CMD_STOP = "stop"
CMD_GOTO = "goto"
CMD_STATUS = "status"
CMD_ESTOP = "estop"
CMD_RESET = "reset"

VALID_COMMANDS = {
    CMD_FORWARD,
    CMD_BACK,
    CMD_LEFT,
    CMD_RIGHT,
    CMD_STOP,
    CMD_GOTO,
    CMD_STATUS,
    CMD_ESTOP,
    CMD_RESET,
}

# Physical & Control Limits (Pioneer P3-DX)
DEFAULT_LINEAR_SPEED = 0.3    # m/s (safe realistic operating speed)
MAX_LINEAR_SPEED = 1.2        # m/s (hardware top speed)
MIN_LINEAR_SPEED = 0.05       # m/s
DEFAULT_ANGULAR_SPEED = 0.5   # rad/s (~28 deg/s)
PIONEER_TRACK_WIDTH = 0.381   # Wheelbase distance L in meters (Pioneer P3-DX)
PIONEER_WHEEL_RADIUS = 0.0975 # Wheel radius R in meters

# Benchmark Delay Values for Experiments (in milliseconds)
EXPERIMENT_DELAYS_MS = [0, 100, 300, 500, 1000]
