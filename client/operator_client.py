"""
Operator Client for Networked Robot Teleoperation.
Provides terminal shell interface, background heartbeat, artificial delay injection,
telemetry feedback monitoring, and structured CSV logging.
"""

import argparse
import logging
import os
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.config import (
    CMD_BACK,
    CMD_ESTOP,
    CMD_FORWARD,
    CMD_GOTO,
    CMD_LEFT,
    CMD_RESET,
    CMD_RIGHT,
    CMD_STATUS,
    CMD_STOP,
    DEFAULT_HOST,
    DEFAULT_PORT,
    STATE_IDLE,
)
from common.netdelay import DelayedQueue
from common.protocol import (
    MSG_TYPE_ACK,
    MSG_TYPE_FB,
    StreamFramer,
    calc_latency,
    create_cmd,
    encode_message,
)
from client.heartbeat import HeartbeatSender
from client.logger import TelemetryLogger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("OperatorClient")


class OperatorClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        delay_ms: float = 0.0,
        log_file: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.delay_ms = delay_ms

        self.tx_delay_queue = DelayedQueue(delay_ms=delay_ms)
        self.logger = TelemetryLogger(log_file)
        self.heartbeat = HeartbeatSender(send_callback=self._queue_raw_message)

        self._sock: Optional[socket.socket] = None
        self._cmd_seq = 0
        self._is_connected = False
        self._latest_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._latest_state: str = STATE_IDLE
        self._latest_latency: float = 0.0
        self._ack_events: Dict[int, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._lock = threading.Lock()

        self._tx_thread: Optional[threading.Thread] = None
        self._rx_thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        """Connect to the Robot Server."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.connect((self.host, self.port))
            self._is_connected = True
            logger.info(f"Connected to Robot Server at {self.host}:{self.port}")

            # Start delayed TX sender thread
            self._tx_thread = threading.Thread(target=self._tx_worker, daemon=True, name="ClientTxWorker")
            self._tx_thread.start()

            # Start RX listener thread
            self._rx_thread = threading.Thread(target=self._rx_worker, daemon=True, name="ClientRxWorker")
            self._rx_thread.start()

            # Start heartbeat thread
            self.heartbeat.start()
            return True

        except Exception as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self._is_connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect and stop all client threads."""
        self._is_connected = False
        self.heartbeat.stop()
        self.tx_delay_queue.close()

        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self._sock.close()
            self._sock = None

        self.logger.close()
        logger.info("Disconnected from Robot Server")

    def set_delay_ms(self, delay_ms: float) -> None:
        """Dynamically update artificial network delay."""
        self.delay_ms = max(0.0, float(delay_ms))
        self.tx_delay_queue.set_delay_ms(self.delay_ms)
        logger.info(f"Simulated network delay set to {self.delay_ms:.1f} ms")

    def _queue_raw_message(self, msg: Dict[str, Any]) -> None:
        """Enqueue message for transmission subject to artificial delay."""
        if not self._is_connected:
            return
        self.tx_delay_queue.put(msg)

    def _tx_worker(self) -> None:
        """Pulls messages from the delayed queue and sends them over TCP."""
        while self._is_connected:
            try:
                msg = self.tx_delay_queue.get(timeout=0.2)
                if msg is None:
                    continue
                if self._sock:
                    data = encode_message(msg)
                    self._sock.sendall(data)
            except TimeoutError:
                continue
            except (socket.error, OSError):
                break

    def _rx_worker(self) -> None:
        """Receives and parses incoming packets from server."""
        framer = StreamFramer()
        while self._is_connected and self._sock:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    logger.warning("Server closed connection")
                    break

                for msg in framer.feed(chunk):
                    self._handle_incoming_message(msg)

            except (socket.error, OSError):
                break

        self._is_connected = False

    def _handle_incoming_message(self, msg: Dict[str, Any]) -> None:
        msg_type = msg.get("type")
        latency = calc_latency(msg)

        if msg_type == MSG_TYPE_FB:
            with self._lock:
                self._latest_pose = (msg["x"], msg["y"], msg["theta"])
                self._latest_state = msg["state"]
                self._latest_latency = latency

            self.logger.log_event(
                msg_type="fb",
                seq=msg["seq"],
                x=msg["x"],
                y=msg["y"],
                theta=msg["theta"],
                state=msg["state"],
                linear_vel=msg.get("linear_vel", 0.0),
                angular_vel=msg.get("angular_vel", 0.0),
                latency_ms=latency,
            )

        elif msg_type == MSG_TYPE_ACK:
            ref_seq = msg.get("ref_seq", 0)
            with self._lock:
                self._latest_state = msg.get("state", self._latest_state)
                if ref_seq in self._ack_events:
                    event, holder = self._ack_events[ref_seq]
                    holder.update(msg)
                    event.set()

            self.logger.log_event(
                msg_type="ack",
                seq=msg["seq"],
                state=msg.get("state", ""),
                latency_ms=latency,
                details=f"ref={ref_seq} ok={msg.get('ok')} msg={msg.get('msg')}",
            )

    def send_command(
        self, name: str, args: Optional[List[Any]] = None, timeout: float = 3.0
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Send a command to the robot server and await its ACK.
        Returns (success, message, ack_payload).
        """
        if not self._is_connected:
            return False, "Not connected to server", {}

        with self._lock:
            self._cmd_seq += 1
            seq = self._cmd_seq
            ack_event = threading.Event()
            ack_holder: Dict[str, Any] = {}
            self._ack_events[seq] = (ack_event, ack_holder)

        cmd = create_cmd(seq=seq, name=name, args=args or [])
        self.logger.log_event(
            msg_type="cmd",
            seq=seq,
            name=name,
            details=f"args={args} delay={self.delay_ms}ms",
        )
        self._queue_raw_message(cmd)

        # Allow extra time if artificial network delay is high
        effective_timeout = timeout + (self.delay_ms / 500.0)
        got_ack = ack_event.wait(timeout=effective_timeout)

        with self._lock:
            self._ack_events.pop(seq, None)

        if not got_ack:
            return False, f"Timed out waiting for ACK (timeout={effective_timeout:.2f}s)", {}

        ok = bool(ack_holder.get("ok", False))
        msg_text = str(ack_holder.get("msg", ""))
        return ok, msg_text, ack_holder

    def get_telemetry(self) -> Dict[str, Any]:
        """Return snapshot of latest telemetry."""
        with self._lock:
            return {
                "x": self._latest_pose[0],
                "y": self._latest_pose[1],
                "theta": self._latest_pose[2],
                "state": self._latest_state,
                "latency_ms": self._latest_latency,
            }

    def run_interactive(self) -> None:
        """Start the interactive command-line shell."""
        print("=" * 60)
        print("  Networked Robot Teleoperation - Operator Shell")
        print("  Type 'help' for command list, 'exit' to quit.")
        print("=" * 60)

        while self._is_connected:
            try:
                line = input("operator> ").strip()
                if not line:
                    continue

                parts = line.split()
                cmd = parts[0].lower()
                args = parts[1:]

                if cmd in ("exit", "quit"):
                    break

                elif cmd == "help":
                    self._print_help()

                elif cmd == "delay":
                    if not args:
                        print(f"Current delay: {self.delay_ms} ms")
                    else:
                        self.set_delay_ms(float(args[0]))

                elif cmd == "pause_hb":
                    self.heartbeat.pause()
                    print("[WARNING] Heartbeat paused! Server will trigger COMM_LOST in 1.5s.")

                elif cmd == "resume_hb":
                    self.heartbeat.resume()
                    print("[INFO] Heartbeat resumed.")

                elif cmd == "telemetry":
                    tel = self.get_telemetry()
                    print(f"Telemetry: Pos=({tel['x']:.2f}, {tel['y']:.2f}, {tel['theta']:.2f} rad) State={tel['state']} Latency={tel['latency_ms']:.1f}ms")

                elif cmd in (
                    CMD_FORWARD,
                    CMD_BACK,
                    CMD_LEFT,
                    CMD_RIGHT,
                    CMD_STOP,
                    CMD_GOTO,
                    CMD_STATUS,
                    CMD_ESTOP,
                    CMD_RESET,
                ):
                    float_args = [float(a) for a in args]
                    ok, msg, _ = self.send_command(cmd, float_args)
                    status_prefix = "[OK]" if ok else "[REJECTED]"
                    print(f"{status_prefix} {msg}")

                else:
                    print(f"Unknown command '{cmd}'. Type 'help' for usage.")

            except (KeyboardInterrupt, EOFError):
                break
            except Exception as e:
                print(f"[Error] {e}")

        self.disconnect()

    def _print_help(self) -> None:
        print("""
Available Commands:
  forward <speed>     - Drive straight forward (e.g. forward 0.4)
  back <speed>        - Drive backward (e.g. back 0.3)
  left <angle_deg>    - Turn left in-place (e.g. left 90)
  right <angle_deg>   - Turn right in-place (e.g. right 90)
  stop                - Stop robot motion normally
  goto <x> <y>        - Navigate to coordinates (e.g. goto 1.5 0.5)
  status              - Query robot position and state
  estop               - EMERGENCY STOP (locks robot, overrides all commands)
  reset               - Unlocks robot from ESTOP_LATCHED
  delay <ms>          - Set artificial network delay in ms (0, 100, 300, 500, 1000)
  telemetry           - Print latest feedback received from server
  pause_hb            - Simulate network cut by stopping heartbeats
  resume_hb           - Resume heartbeats after test
  exit / quit         - Disconnect and exit
""")


def main():
    parser = argparse.ArgumentParser(description="Operator Client for Robot Teleoperation")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port")
    parser.add_argument("--delay", type=float, default=0.0, help="Initial network delay in ms")
    parser.add_argument("--log", type=str, default=None, help="Custom CSV log file path")
    args = parser.parse_args()

    client = OperatorClient(host=args.host, port=args.port, delay_ms=args.delay, log_file=args.log)
    if client.connect():
        client.run_interactive()


if __name__ == "__main__":
    main()
