"""
Robot Server implementation.
Manages TCP connection to Operator Client, dispatches commands to the robot driver,
enforces safety watchdog and latched E-stop, and streams feedback telemetry.
"""

import logging
import socket
import threading
import time
from typing import Any, Dict, Optional

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
    FEEDBACK_INTERVAL,
    HEARTBEAT_TIMEOUT,
    MAX_LINEAR_SPEED,
    STATE_COMM_LOST,
    STATE_ESTOP_LATCHED,
    STATE_IDLE,
    STATE_MOVING,
)
from common.protocol import (
    MSG_TYPE_CMD,
    MSG_TYPE_HB,
    StreamFramer,
    calc_latency,
    create_ack,
    create_fb,
    encode_message,
)
from server.driver_interface import RobotDriverInterface
from server.state_machine import RobotStateMachine
from server.watchdog import HeartbeatWatchdog

logger = logging.getLogger(__name__)


class RobotServer:
    def __init__(
        self,
        driver: RobotDriverInterface,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        watchdog_timeout: float = HEARTBEAT_TIMEOUT,
        feedback_interval: float = FEEDBACK_INTERVAL,
    ) -> None:
        self.host = host
        self.port = port
        self.driver = driver
        self.feedback_interval = feedback_interval

        self.state_machine = RobotStateMachine(on_state_change=self._on_state_changed)
        self.watchdog = HeartbeatWatchdog(
            timeout_sec=watchdog_timeout,
            on_timeout=self._on_watchdog_timeout,
            on_recovered=self._on_watchdog_recovered,
        )

        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._client_lock = threading.Lock()

        self._is_running = False
        self._fb_seq = 0
        self._last_cmd_seq = 0

        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start server, listen for connections, and connect driver."""
        self.driver.connect()
        self._is_running = True

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(1)
        logger.info(f"Robot Server listening on {self.host}:{self.port}")

        accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="AcceptLoop")
        accept_thread.start()
        self._threads.append(accept_thread)

    def stop(self) -> None:
        """Shut down server cleanly."""
        self._is_running = False
        self.watchdog.stop()

        with self._client_lock:
            if self._client_sock:
                try:
                    self._client_sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self._client_sock.close()
                self._client_sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        self.driver.stop()
        self.driver.disconnect()
        logger.info("Robot Server stopped")

    def _on_state_changed(self, old_state: str, new_state: str) -> None:
        logger.info(f"State transition: {old_state} -> {new_state}")

    def _on_watchdog_timeout(self) -> None:
        logger.warning("Safety Watchdog Timeout: Stopping robot and setting COMM_LOST")
        self.driver.stop()
        self.state_machine.trigger_comm_lost("Heartbeat timeout")

    def _on_watchdog_recovered(self) -> None:
        logger.info("Safety Watchdog: Heartbeats restored. Robot returns to IDLE (awaits new command)")
        self.state_machine.trigger_comm_restored()

    def _accept_loop(self) -> None:
        while self._is_running and self._server_sock:
            try:
                sock, addr = self._server_sock.accept()
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                logger.info(f"Accepted connection from {addr}")

                with self._client_lock:
                    if self._client_sock:
                        try:
                            self._client_sock.close()
                        except Exception:
                            pass
                    self._client_sock = sock

                # Start watchdog and worker threads
                self.watchdog.start()

                rx_thread = threading.Thread(
                    target=self._client_rx_loop, args=(sock,), daemon=True, name="ClientRxLoop"
                )
                fb_thread = threading.Thread(
                    target=self._feedback_loop, args=(sock,), daemon=True, name="FeedbackLoop"
                )
                rx_thread.start()
                fb_thread.start()

            except (socket.error, OSError):
                break

    def _send_to_client(self, sock: socket.socket, msg: Dict[str, Any]) -> bool:
        try:
            data = encode_message(msg)
            sock.sendall(data)
            return True
        except (socket.error, OSError):
            return False

    def _feedback_loop(self, sock: socket.socket) -> None:
        """Streams feedback packets to client at fixed frequency."""
        while self._is_running:
            time.sleep(self.feedback_interval)
            with self._client_lock:
                if self._client_sock is not sock:
                    break

            # Update state machine if asynchronous movement completed
            if self.state_machine.state == STATE_MOVING and not self.driver.is_action_in_progress():
                self.state_machine.on_movement_finished()

            x, y, theta = self.driver.get_pose()
            lin_vel, ang_vel = self.driver.get_velocities()
            self._fb_seq += 1

            fb = create_fb(
                seq=self._fb_seq,
                x=x,
                y=y,
                theta=theta,
                state=self.state_machine.state,
                cmd_seq=self._last_cmd_seq,
                linear_vel=lin_vel,
                angular_vel=ang_vel,
            )

            if not self._send_to_client(sock, fb):
                break

    def _client_rx_loop(self, sock: socket.socket) -> None:
        """Receives and processes incoming lines from client."""
        framer = StreamFramer()
        while self._is_running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    logger.info("Client closed connection")
                    break

                for msg in framer.feed(chunk):
                    self._handle_message(sock, msg)

            except (socket.error, OSError):
                break

        # Client disconnected
        self.watchdog.stop()
        self.driver.stop()
        with self._client_lock:
            if self._client_sock is sock:
                self._client_sock = None

    def _handle_message(self, sock: socket.socket, msg: Dict[str, Any]) -> None:
        msg_type = msg.get("type")

        # 1. Heartbeat
        if msg_type == MSG_TYPE_HB:
            self.watchdog.feed()
            return

        # 2. Command
        if msg_type == MSG_TYPE_CMD:
            self._handle_command(sock, msg)

    def _handle_command(self, sock: socket.socket, msg: Dict[str, Any]) -> None:
        cmd_seq = msg.get("seq", 0)
        cmd_name = msg.get("name", "")
        args = msg.get("args", [])
        self._last_cmd_seq = cmd_seq

        # A. E-STOP has highest priority
        if cmd_name == CMD_ESTOP:
            logger.warning("[SAFETY] E-STOP command received! Latching emergency stop.")
            self.driver.cancel_current_action()
            self.driver.stop()
            self.state_machine.trigger_estop("Operator E-stop")
            ack = create_ack(
                seq=cmd_seq,
                ref_seq=cmd_seq,
                ok=True,
                msg="EMERGENCY STOP ACTIVATED: Robot locked",
                state=self.state_machine.state,
            )
            self._send_to_client(sock, ack)
            return

        # B. RESET unlocks E-stop
        if cmd_name == CMD_RESET:
            ok, text = self.state_machine.trigger_reset()
            ack = create_ack(
                seq=cmd_seq,
                ref_seq=cmd_seq,
                ok=ok,
                msg=text,
                state=self.state_machine.state,
            )
            self._send_to_client(sock, ack)
            return

        # C. Validate against state machine
        allowed, reason = self.state_machine.validate_command(cmd_name)
        if not allowed:
            logger.warning(f"Command '{cmd_name}' rejected: {reason}")
            ack = create_ack(
                seq=cmd_seq,
                ref_seq=cmd_seq,
                ok=False,
                msg=f"Rejected: {reason}",
                state=self.state_machine.state,
            )
            self._send_to_client(sock, ack)
            return

        # D. Execute command
        ack_ok = True
        ack_msg = "OK"

        try:
            if cmd_name == CMD_STATUS:
                x, y, theta = self.driver.get_pose()
                ack_msg = f"Pose: ({x:.2f}, {y:.2f}, {theta:.2f} rad), State: {self.state_machine.state}"

            elif cmd_name == CMD_STOP:
                self.driver.stop()
                self.state_machine.on_movement_finished()
                ack_msg = "Robot stopped"

            elif cmd_name == CMD_FORWARD:
                speed = float(args[0]) if args else 0.3
                speed = min(abs(speed), MAX_LINEAR_SPEED)
                self.state_machine.on_movement_started()
                self.driver.drive(speed)
                ack_msg = f"Driving forward at {speed:.2f} m/s"

            elif cmd_name == CMD_BACK:
                speed = float(args[0]) if args else 0.3
                speed = min(abs(speed), MAX_LINEAR_SPEED)
                self.state_machine.on_movement_started()
                self.driver.drive(-speed)
                ack_msg = f"Driving backward at {speed:.2f} m/s"

            elif cmd_name == CMD_LEFT:
                angle_deg = float(args[0]) if args else 90.0
                self.state_machine.on_movement_started()
                self.driver.turn(angle_deg)
                ack_msg = f"Turning left {angle_deg:.1f} degrees"

            elif cmd_name == CMD_RIGHT:
                angle_deg = float(args[0]) if args else 90.0
                self.state_machine.on_movement_started()
                self.driver.turn(-angle_deg)
                ack_msg = f"Turning right {angle_deg:.1f} degrees"

            elif cmd_name == CMD_GOTO:
                if len(args) < 2:
                    raise ValueError("goto command requires x and y arguments")
                target_x = float(args[0])
                target_y = float(args[1])
                self.state_machine.on_movement_started()
                self.driver.goto(target_x, target_y)
                ack_msg = f"Navigating to ({target_x:.2f}, {target_y:.2f})"

        except Exception as e:
            logger.error(f"Error executing command {cmd_name}: {e}")
            ack_ok = False
            ack_msg = f"Execution error: {e}"

        ack = create_ack(
            seq=cmd_seq,
            ref_seq=cmd_seq,
            ok=ack_ok,
            msg=ack_msg,
            state=self.state_machine.state,
        )
        self._send_to_client(sock, ack)
