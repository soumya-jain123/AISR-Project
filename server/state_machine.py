"""
State machine for the Robot Server.
Enforces the 4 core states: IDLE, MOVING, COMM_LOST, ESTOP_LATCHED.
Guarantees safety properties:
- E-Stop has highest priority and latches until explicit reset.
- Communication loss stops robot and rejects queued motion.
- Recovering communication requires a fresh command before moving.
"""

import logging
import threading
from typing import Callable, Optional
from common.config import (
    STATE_COMM_LOST,
    STATE_ESTOP_LATCHED,
    STATE_IDLE,
    STATE_MOVING,
    CMD_ESTOP,
    CMD_RESET,
    CMD_STATUS,
    CMD_STOP,
    VALID_COMMANDS,
)

logger = logging.getLogger(__name__)


class RobotStateMachine:
    def __init__(self, on_state_change: Optional[Callable[[str, str], None]] = None) -> None:
        self._state = STATE_IDLE
        self._lock = threading.Lock()
        self._on_state_change = on_state_change

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _set_state(self, new_state: str, reason: str = "") -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        logger.info(f"[StateMachine] {old_state} -> {new_state} (Reason: {reason})")
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception as e:
                logger.error(f"Error in state change callback: {e}")

    def can_accept_movement(self) -> bool:
        """Return True if movement commands are currently accepted."""
        with self._lock:
            return self._state in (STATE_IDLE, STATE_MOVING)

    def trigger_estop(self, reason: str = "Manual E-Stop") -> None:
        """
        Emergency stop has the highest priority.
        Immediately transitions to ESTOP_LATCHED regardless of current state.
        """
        with self._lock:
            self._set_state(STATE_ESTOP_LATCHED, reason)

    def trigger_reset(self) -> tuple[bool, str]:
        """
        Unlatches E-stop and returns robot to IDLE.
        Only valid if currently in ESTOP_LATCHED.
        """
        with self._lock:
            if self._state == STATE_ESTOP_LATCHED:
                self._set_state(STATE_IDLE, "Operator Reset")
                return True, "E-Stop unlatched. Robot is now IDLE."
            return False, f"Cannot reset when in state {self._state}."

    def trigger_comm_lost(self, reason: str = "Heartbeat timeout") -> None:
        """
        Watchdog detected communication loss.
        If already in ESTOP_LATCHED, stays in ESTOP_LATCHED (highest priority).
        Otherwise transitions to COMM_LOST.
        """
        with self._lock:
            if self._state == STATE_ESTOP_LATCHED:
                # E-stop is higher priority
                return
            self._set_state(STATE_COMM_LOST, reason)

    def trigger_comm_restored(self) -> None:
        """
        Heartbeats have resumed after a timeout.
        Returns to IDLE only if currently in COMM_LOST.
        Never auto-resumes any prior movement!
        """
        with self._lock:
            if self._state == STATE_COMM_LOST:
                self._set_state(STATE_IDLE, "Heartbeat restored")

    def validate_command(self, cmd_name: str) -> tuple[bool, str]:
        """
        Check if a given command is permitted in the current state.
        Returns (is_allowed, rejection_reason).
        """
        if cmd_name not in VALID_COMMANDS:
            return False, f"Unknown command '{cmd_name}'"

        with self._lock:
            # Estop and Status are always permitted
            if cmd_name in (CMD_ESTOP, CMD_STATUS):
                return True, "Allowed"

            if self._state == STATE_ESTOP_LATCHED:
                if cmd_name == CMD_RESET:
                    return True, "Allowed"
                return False, "Robot is in ESTOP_LATCHED. Issue 'reset' to unlock."

            if self._state == STATE_COMM_LOST:
                return False, "Communication is lost (heartbeat timeout). Cannot execute movement."

            # In IDLE or MOVING
            return True, "Allowed"

    def on_movement_started(self) -> None:
        """Called when a physical movement begins."""
        with self._lock:
            if self._state == STATE_IDLE:
                self._set_state(STATE_MOVING, "Movement started")

    def on_movement_finished(self) -> None:
        """Called when physical movement stops or completes."""
        with self._lock:
            if self._state == STATE_MOVING:
                self._set_state(STATE_IDLE, "Movement finished")
