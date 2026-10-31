"""
WebSocket Connection and Session Manager.
Maintains independent player sessions, handles message validation,
provides targeted and broadcast communications, and runs per-session timeout sweeps.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Union
from fastapi import WebSocket, WebSocketDisconnect

from multiplayer.config import MultiplayerSettings, get_settings
from multiplayer.protocol import (
    PROTOCOL_VERSION,
    CommandAckMessage,
    ErrorCode,
    ErrorMessage,
    MultiplayerProtocolError,
    MultiplayerServerMessage,
    WelcomeMessage,
    encode_multiplayer_message,
    now_ts,
    parse_client_message,
)
from multiplayer.session import PlayerSession, SessionStatus

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Thread/Async-safe connection manager for WebSocket clients.
    Guarantees session isolation, per-session monotonic sequence tracking,
    and non-blocking I/O across concurrent players.
    """

    def __init__(self, settings: Optional[MultiplayerSettings] = None) -> None:
        self.settings = settings or get_settings()
        self._sessions: Dict[str, PlayerSession] = {}
        self._lock = asyncio.Lock()
        self._server_seq = 0
        self._sweep_task: Optional[asyncio.Task] = None

    def _next_server_seq(self) -> int:
        self._server_seq += 1
        return self._server_seq

    async def register(
        self, websocket: WebSocket, display_name: Optional[str] = None
    ) -> Optional[PlayerSession]:
        """
        Accept a WebSocket connection, enforce connection limits,
        issue a server-generated session ID, and send a WelcomeMessage.
        """
        async with self._lock:
            if len(self._sessions) >= self.settings.max_concurrent_connections:
                logger.warning(
                    f"Max concurrent connections ({self.settings.max_concurrent_connections}) reached. "
                    "Rejecting incoming client."
                )
                await websocket.close(code=1013, reason="Server busy: connection limit reached")
                return None

            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            session = PlayerSession(
                session_id=session_id,
                websocket=websocket,
                display_name=display_name,
                protocol_version=PROTOCOL_VERSION,
            )
            self._sessions[session_id] = session

        await websocket.accept()
        logger.info(f"Registered new player session: {session_id}")

        # Send server welcome handshake
        welcome = WelcomeMessage(
            seq=self._next_server_seq(),
            ts=now_ts(),
            session_id=session_id,
            heartbeat_interval_sec=self.settings.ws_heartbeat_interval_sec,
            timeout_sec=self.settings.session_timeout_sec,
            server_time=now_ts(),
            message="Connected to AISR Multiplayer Server",
        )
        await self.send_message(session_id, welcome)
        return session

    async def unregister(
        self, session_id: str, close_code: int = 1000, reason: str = ""
    ) -> None:
        """
        Cleanly remove a session, mark it disconnected, and close its socket if open.
        """
        session: Optional[PlayerSession] = None
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session is not None:
            session.mark_disconnected()
            logger.info(f"Unregistered session: {session_id} (Reason: '{reason}')")
            try:
                await session.websocket.close(code=close_code, reason=reason)
            except Exception:
                pass  # Socket may already be closed by client

    def get_session(self, session_id: str) -> Optional[PlayerSession]:
        """Lookup session by ID."""
        return self._sessions.get(session_id)

    def get_active_session_count(self) -> int:
        """Return number of currently active sessions."""
        return len(self._sessions)

    def get_all_session_ids(self) -> List[str]:
        """Return defensive copy of all active session IDs."""
        return list(self._sessions.keys())

    async def send_message(
        self,
        session_id: str,
        message: Union[MultiplayerServerMessage, Any, Dict[str, Any], str],
    ) -> bool:
        """
        Send a message to a specific session through its isolated send lock.
        Does not block other clients.
        """
        session = self.get_session(session_id)
        if session is None or session.status != SessionStatus.CONNECTED:
            return False

        if isinstance(message, str):
            json_str = message
        else:
            json_str = encode_multiplayer_message(message)

        try:
            async with session.send_lock:
                await session.websocket.send_text(json_str)
            return True
        except (WebSocketDisconnect, RuntimeError, ConnectionResetError) as e:
            logger.info(f"Send failed for session {session_id}: {e}. Disconnecting session.")
            await self.unregister(session_id, reason="Send failure")
            return False

    async def broadcast(
        self,
        message: Union[MultiplayerServerMessage, Any, Dict[str, Any], str],
        exclude_session_id: Optional[str] = None,
    ) -> int:
        """
        Broadcast a message to all connected sessions concurrently.
        Slow or failing clients are isolated using asyncio.gather with return_exceptions=True.
        """
        session_ids = [
            sid for sid in self.get_all_session_ids() if sid != exclude_session_id
        ]
        if not session_ids:
            return 0

        tasks = [self.send_message(sid, message) for sid in session_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful = sum(1 for res in results if res is True)
        return successful

    async def process_client_message(
        self, session: PlayerSession, raw_data: str | bytes
    ) -> Optional[MultiplayerServerMessage]:
        """
        Validate and process an incoming raw client frame.

        Enforces:
        1. Maximum payload size.
        2. Module 2 protocol parsing (rejects malformed, extra fields, NaN/inf).
        3. Monotonic sequence numbers (seq > last_seq).
        4. Session ID authentication (client session_id must match connection session_id).
        5. Updates session monotonic activity timestamp.
        """
        ref_seq: Optional[int] = None

        # 1. Size pre-flight
        raw_bytes = raw_data.encode("utf-8") if isinstance(raw_data, str) else raw_data
        if len(raw_bytes) > self.settings.max_message_bytes:
            return ErrorMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                code=ErrorCode.ERR_PAYLOAD_TOO_LARGE.value,
                message=f"Payload size ({len(raw_bytes)} bytes) exceeds limit {self.settings.max_message_bytes}",
                ref_seq=None,
            )

        # 2. Extract seq for error attribution if available
        try:
            peek_data = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(peek_data, dict):
                ref_seq = peek_data.get("seq")
        except Exception:
            pass

        # 3. Parse via Module 2 strictly
        try:
            msg = parse_client_message(raw_data)
        except MultiplayerProtocolError as err:
            return ErrorMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                code=err.code,
                message=err.message,
                ref_seq=ref_seq,
            )

        # 4. Monotonic sequence validation
        if msg.seq <= session.last_seq:
            return ErrorMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                code=ErrorCode.ERR_OUT_OF_BOUNDS.value,
                message=(
                    f"Out-of-order or duplicate sequence number: received {msg.seq}, "
                    f"must be strictly greater than {session.last_seq}"
                ),
                ref_seq=msg.seq,
            )
        session.last_seq = msg.seq

        # 5. Session authentication / spoofing prevention
        if msg.session_id != session.session_id:
            return ErrorMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                code=ErrorCode.ERR_UNAUTHORIZED.value,
                message=(
                    f"Session ID mismatch: message session '{msg.session_id}' does not match "
                    f"authenticated connection '{session.session_id}'"
                ),
                ref_seq=msg.seq,
            )

        # 6. Update session activity
        session.update_activity()

        # 7. Formulate acknowledgement
        if msg.type == "client_heartbeat":
            return CommandAckMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                ref_seq=msg.seq,
                ok=True,
                message="Heartbeat acknowledged",
            )
        else:
            # Control / Room messages: validated successfully; dispatching is added in later modules
            return CommandAckMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                ref_seq=msg.seq,
                ok=True,
                message=f"Message '{msg.type}' validated and accepted (dispatch pending Module 5)",
            )

    async def check_timeouts(self, timeout_sec: Optional[float] = None) -> List[str]:
        """
        Scan all active sessions using monotonic clock.
        Disconnects and unregisters sessions whose idle time exceeds timeout_sec.
        Returns list of timed-out session IDs.
        """
        effective_timeout = (
            timeout_sec if timeout_sec is not None else self.settings.session_timeout_sec
        )
        now_mono = time.monotonic()
        timed_out: List[str] = []

        # Snapshot session list
        async with self._lock:
            sessions = list(self._sessions.values())

        for session in sessions:
            if session.is_timed_out(effective_timeout, now_mono=now_mono):
                timed_out.append(session.session_id)
                logger.warning(
                    f"Session {session.session_id} timed out after {now_mono - session.last_activity_mono:.2f}s "
                    f"(limit: {effective_timeout}s). Disconnecting."
                )
                await self.unregister(
                    session.session_id, close_code=1000, reason="Session heartbeat timeout"
                )

        return timed_out

    async def start_timeout_sweeper(self, interval_sec: float = 1.0) -> None:
        """Start background timeout sweeper loop."""
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweeper_loop(interval_sec))

    async def stop_timeout_sweeper(self) -> None:
        """Cancel background timeout sweeper loop."""
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
        self._sweep_task = None

    async def _sweeper_loop(self, interval_sec: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval_sec)
                await self.check_timeouts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in timeout sweeper loop: {e}")


# Singleton instance accessor
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager(settings: Optional[MultiplayerSettings] = None) -> ConnectionManager:
    """Retrieve or initialize the global ConnectionManager singleton."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager(settings)
    return _connection_manager


def set_connection_manager(manager: ConnectionManager) -> None:
    """Explicitly override global ConnectionManager (useful in tests)."""
    global _connection_manager
    _connection_manager = manager
