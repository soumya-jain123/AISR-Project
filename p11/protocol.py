"""Command grammar, validation and wire format shared by client and bridge.

Wire format: one JSON object per message.
  request : {"id", "client", "cmd", "args", "t_sent", "rtt_ms"}
  reply   : {"id", "ok", "msg", "t_recv", "t_exec", "telemetry"}
Over TCP each JSON object is one line (newline terminated).
Over MQTT each JSON object is one MQTT payload.
"""
import json
import math

MAX_V = 0.5          # m/s   hard speed limit (enforced on the bridge too)
MAX_W = 1.5          # rad/s hard turn-rate limit
COORD_LIMIT = 20.0   # m     largest |x| or |y| accepted by goto

HELP = """Commands:
    forward [v]      drive forward continuously until 'stop' (default = 'speed' setting)
    back [v]         drive backward continuously until 'stop'
  left <deg>       turn left in place by <deg> degrees (closed loop)
  right <deg>      turn right in place by <deg> degrees
    drive <v> <w>    set linear v (m/s) and angular w (rad/s) until 'stop'
  stop             stop the robot
  speed <v>        set default speed (m/s)
  goto <x> <y>     go to point (x, y) in metres (robot start = origin)
  status           show pose, mode and safety state
  sensors          show the 8 distance sensors (m)
  safety on|off    enable / disable the local safety layer
  estop            emergency stop (latched)
  reset            clear emergency stop
  wait <s>         (client only) pause, useful in scripts
  script <file>    (client only) run commands from a text file
  help, quit"""

NO_ARG = {"stop", "status", "sensors", "estop", "reset", "ping"}
ALL_CMDS = NO_ARG | {"forward", "back", "left", "right", "drive", "speed",
                     "goto", "safety"}


def _num(x, name):
    try:
        val = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got '{x}'")
    if not math.isfinite(val):
        raise ValueError(f"{name} must be a finite number")
    return val


def validate(cmd, args):
    """Check a command and its arguments; return normalised args (list).
    Raises ValueError with a readable message. Used by BOTH sides."""
    cmd = str(cmd).lower()
    args = list(args) if args is not None else []
    if cmd not in ALL_CMDS:
        raise ValueError(f"unknown command '{cmd}' (type 'help')")
    if cmd in NO_ARG:
        if args:
            raise ValueError(f"'{cmd}' takes no arguments")
        return []
    if cmd in ("forward", "back"):
        if len(args) > 1:
            raise ValueError(f"usage: {cmd} [speed]")
        if not args:
            return []
        v = _num(args[0], "speed")
        if not 0 < v <= MAX_V:
            raise ValueError(f"speed must be in (0, {MAX_V}] m/s")
        return [v]
    if cmd in ("left", "right"):
        if len(args) != 1:
            raise ValueError(f"usage: {cmd} <degrees>")
        d = _num(args[0], "degrees")
        if not 0 < d <= 360:
            raise ValueError("degrees must be in (0, 360]")
        return [d]
    if cmd == "drive":
        if len(args) != 2:
            raise ValueError("usage: drive <v> <w>")
        v, w = _num(args[0], "v"), _num(args[1], "w")
        if abs(v) > MAX_V or abs(w) > MAX_W:
            raise ValueError(f"|v| <= {MAX_V} m/s and |w| <= {MAX_W} rad/s")
        return [v, w]
    if cmd == "speed":
        if len(args) != 1:
            raise ValueError("usage: speed <v>")
        v = _num(args[0], "speed")
        if not 0 < v <= MAX_V:
            raise ValueError(f"speed must be in (0, {MAX_V}] m/s")
        return [v]
    if cmd == "goto":
        if len(args) != 2:
            raise ValueError("usage: goto <x> <y>")
        x, y = _num(args[0], "x"), _num(args[1], "y")
        if abs(x) > COORD_LIMIT or abs(y) > COORD_LIMIT:
            raise ValueError(f"|x| and |y| must be <= {COORD_LIMIT} m")
        return [x, y]
    if cmd == "safety":
        if len(args) != 1 or str(args[0]).lower() not in ("on", "off"):
            raise ValueError("usage: safety on|off")
        return [str(args[0]).lower()]
    raise ValueError("unhandled command")  # pragma: no cover


def parse_command(line):
    """'forward 0.3' -> ('forward', [0.3]). Raises ValueError if invalid."""
    parts = line.strip().split()
    if not parts:
        raise ValueError("empty command")
    cmd = parts[0].lower()
    return cmd, validate(cmd, parts[1:])


def encode(msg):
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


def decode(data):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON: {e}")
    if not isinstance(obj, dict):
        raise ValueError("message must be a JSON object")
    return obj
