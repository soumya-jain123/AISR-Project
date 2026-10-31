"""
Player Session Model for Multiplayer WebSocket Connections.
Maintains isolated per-connection state, sequence tracking, and monotonic activity timing.
"""

import asyncio
from enum import Enum
import time
from typing import Any, Dict, Optional
from fastapi import WebSocket

from multiplayer.protocol import PROTOCOL_VERSION, ID_REGEX


class SessionStatus(str, Enum):
    """Lifecycle status of a player connection."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class PlayerSession:
    """
    Server-side session representing an active or recent WebSocket connection.
    Guarantees thread/async safety and isolation per connection.
    """

    def __init__(
        self,
        session_id: str,
        websocket: WebSocket,
        display_name: Optional[str] = None,
        protocol_version: int = PROTOCOL_VERSION,
    ) -> None:
        if not ID_REGEX.match(session_id):
            raise ValueError(f"Invalid session_id '{session_id}'")

        self.session_id: str = session_id
        self.websocket: WebSocket = websocket
        self.display_name: Optional[str] = display_name
        self.protocol_version: int = protocol_version

        self.status: SessionStatus = SessionStatus.CONNECTED
        self.connected_at: float = time.time()
        self.last_activity_mono: float = time.monotonic()
        self.last_seq: int = -1  # Monotonically increasing sequence tracker

        # Per-session send lock to prevent interleaved WebSocket frames without holding global lock
        self.send_lock: asyncio.Lock = asyncio.Lock()
        self.metadata: Dict[str, Any] = {}

    def update_activity(self) -> None:
        """Record fresh client activity using monotonic clock."""
        self.last_activity_mono = time.monotonic()

    def is_timed_out(self, timeout_sec: float, now_mono: Optional[float] = None) -> bool:
        """Check if elapsed time since last activity exceeds timeout_sec."""
        current_mono = now_mono if now_mono is not None else time.monotonic()
        return (current_mono - self.last_activity_mono) > timeout_sec

    def mark_disconnected(self) -> None:
        """Mark session as disconnected."""
        self.status = SessionStatus.DISCONNECTED

    def to_dict(self) -> Dict[str, Any]:
        """Return safe, non-sensitive session metadata snapshot."""
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "display_name": self.display_name,
            "connected_at": round(self.connected_at, 3),
            "idle_sec": round(time.monotonic() - self.last_activity_mono, 3),
            "last_seq": self.last_seq,
            "protocol_version": self.protocol_version,
        }
