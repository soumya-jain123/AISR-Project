"""
WebSocket Connection and Session Manager.
Maintains independent player sessions, handles message validation,
provides targeted, room-scoped, and broadcast communications, and runs
per-session timeout sweeps and room lifecycle integration.
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
    PlayerJoinedMessage,
    PlayerLeftMessage,
    PlayerReadyMessage,
    RobotAssignedMessage,
    RobotUnavailableMessage,
    RoomListMessage,
    RoomSnapshotMessage,
    WelcomeMessage,
    encode_multiplayer_message,
    now_ts,
    parse_client_message,
)
from multiplayer.rooms import RoomManager, RoomStatus, get_room_manager
from multiplayer.session import PlayerSession, SessionStatus

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Thread/Async-safe connection manager for WebSocket clients.
    Guarantees session isolation, per-session monotonic sequence tracking,
    room isolation, and non-blocking I/O across concurrent players.
    """

    def __init__(
        self,
        settings: Optional[MultiplayerSettings] = None,
        room_manager: Optional[RoomManager] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.room_manager = room_manager or get_room_manager(settings=self.settings)
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
        Cleanly remove a session, mark it disconnected, handle room departure/cleanup,
        and close its socket if open.
        """
        session: Optional[PlayerSession] = None
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        # 1. Clean up room membership if session was in a room
        try:
            leave_info = await self.room_manager.handle_disconnect(session_id)
            if leave_info is not None:
                room, released_robot, new_owner = leave_info
                # Broadcast departure to remaining room members
                left_msg = PlayerLeftMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    room_id=room.room_id,
                    session_id=session_id,
                    reason=reason or "Player disconnected",
                    new_owner_id=new_owner,
                )
                await self.send_to_room(room.room_id, left_msg)
                if room.status != RoomStatus.CLOSED:
                    snap_msg = room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
                    await self.send_to_room(room.room_id, snap_msg)
        except Exception as e:
            logger.error(f"Error handling room disconnect for {session_id}: {e}")

        # 2. Close socket cleanly
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

    async def send_to_room(
        self,
        room_id: str,
        message: Union[MultiplayerServerMessage, Any, Dict[str, Any], str],
        exclude_session_id: Optional[str] = None,
    ) -> int:
        """
        Send a message to all active sessions in a room concurrently.
        Prevents cross-room message leakage and isolates slow clients.
        """
        member_ids = self.room_manager.get_room_member_session_ids(room_id)
        target_ids = [sid for sid in member_ids if sid != exclude_session_id]
        if not target_ids:
            return 0

        tasks = [self.send_message(sid, message) for sid in target_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful = sum(1 for res in results if res is True)
        return successful

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
        2. Module 2 protocol parsing (rejects malformed, extra fields, NaN/inf, forged server types).
        3. Monotonic sequence numbers (seq > last_seq).
        4. Session ID authentication (client session_id must match connection session_id).
        5. Updates session monotonic activity timestamp.
        6. Dispatches room lifecycle actions (create, join, leave, ready, list).
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

        # 3. Parse via Module 2 strictly (rejects forged server messages, extra fields, etc.)
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

        # 7. Route messages according to type
        if msg.type == "client_heartbeat":
            return CommandAckMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                ref_seq=msg.seq,
                ok=True,
                message="Heartbeat acknowledged",
            )

        elif msg.type == "create_room":
            try:
                display_name = getattr(msg, "display_name", None) or session.display_name
                room, robot_id = await self.room_manager.create_room(
                    session_id=session.session_id,
                    display_name=display_name,
                    name=msg.name,
                    capacity=msg.capacity,
                    role=msg.role,
                )
                if robot_id:
                    assign_msg = RobotAssignedMessage(
                        seq=self._next_server_seq(),
                        ts=now_ts(),
                        room_id=room.room_id,
                        session_id=session.session_id,
                        robot_id=robot_id,
                    )
                    await self.send_message(session.session_id, assign_msg)

                return room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
            except MultiplayerProtocolError as err:
                return ErrorMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    code=err.code,
                    message=err.message,
                    ref_seq=msg.seq,
                )

        elif msg.type == "join_room":
            try:
                room, robot_id = await self.room_manager.join_room(
                    session_id=session.session_id,
                    room_identifier=msg.room_id,
                    display_name=msg.display_name,
                    role=msg.role,
                )
                # Notify existing room members of new player
                joined_notif = PlayerJoinedMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    room_id=room.room_id,
                    session_id=session.session_id,
                    display_name=msg.display_name,
                    role=msg.role,
                    robot_id=robot_id,
                )
                await self.send_to_room(
                    room.room_id, joined_notif, exclude_session_id=session.session_id
                )

                if robot_id:
                    assign_msg = RobotAssignedMessage(
                        seq=self._next_server_seq(),
                        ts=now_ts(),
                        room_id=room.room_id,
                        session_id=session.session_id,
                        robot_id=robot_id,
                    )
                    await self.send_message(session.session_id, assign_msg)

                # Broadcast updated snapshot to other members
                updated_snap = room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
                await self.send_to_room(
                    room.room_id, updated_snap, exclude_session_id=session.session_id
                )

                # Return room snapshot to joining player
                return room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
            except MultiplayerProtocolError as err:
                return ErrorMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    code=err.code,
                    message=err.message,
                    ref_seq=msg.seq,
                )

        elif msg.type == "leave_room":
            try:
                leave_info = await self.room_manager.leave_room(session.session_id)
                if leave_info is None:
                    return ErrorMessage(
                        seq=self._next_server_seq(),
                        ts=now_ts(),
                        code=ErrorCode.ERR_NOT_IN_ROOM.value,
                        message=f"Session '{session.session_id}' is not in any room.",
                        ref_seq=msg.seq,
                    )
                room, released_robot, new_owner = leave_info
                left_msg = PlayerLeftMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    room_id=room.room_id,
                    session_id=session.session_id,
                    reason="Player left room",
                    new_owner_id=new_owner,
                )
                await self.send_to_room(room.room_id, left_msg)
                if room.status != RoomStatus.CLOSED:
                    snap_msg = room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
                    await self.send_to_room(room.room_id, snap_msg)

                return CommandAckMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    ref_seq=msg.seq,
                    ok=True,
                    message=f"Left room {room.room_id} successfully",
                )
            except MultiplayerProtocolError as err:
                return ErrorMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    code=err.code,
                    message=err.message,
                    ref_seq=msg.seq,
                )

        elif msg.type == "set_ready":
            try:
                room, is_ready = await self.room_manager.set_ready(session.session_id, msg.is_ready)
                ready_notif = PlayerReadyMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    room_id=room.room_id,
                    session_id=session.session_id,
                    is_ready=is_ready,
                )
                await self.send_to_room(room.room_id, ready_notif)
                snap_msg = room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
                await self.send_to_room(room.room_id, snap_msg)

                return CommandAckMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    ref_seq=msg.seq,
                    ok=True,
                    message=f"Ready state set to {is_ready}",
                )
            except MultiplayerProtocolError as err:
                return ErrorMessage(
                    seq=self._next_server_seq(),
                    ts=now_ts(),
                    code=err.code,
                    message=err.message,
                    ref_seq=msg.seq,
                )

        elif msg.type == "list_rooms":
            summaries = self.room_manager.list_rooms()
            return RoomListMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                rooms=summaries,
            )

        else:
            # Control / E-stop messages: validated successfully; control dispatching is added in later modules
            return CommandAckMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                ref_seq=msg.seq,
                ok=True,
                message=f"Message '{msg.type}' validated and accepted (control dispatch pending Module 7)",
            )

    async def notify_robot_unavailable(self, robot_id: str, reason: str = "") -> None:
        """
        Notify affected room when an assigned robot becomes unavailable externally.
        Does not silently assign another robot.
        """
        res = await self.room_manager.mark_robot_unavailable(robot_id, reason=reason)
        if res is not None:
            room, session_id = res
            notif = RobotUnavailableMessage(
                seq=self._next_server_seq(),
                ts=now_ts(),
                room_id=room.room_id,
                session_id=session_id,
                robot_id=robot_id,
                reason=reason or "Robot became unavailable",
            )
            await self.send_to_room(room.room_id, notif)
            snap = room.to_snapshot(seq=self._next_server_seq(), ts=now_ts())
            await self.send_to_room(room.room_id, snap)

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


def get_connection_manager(
    settings: Optional[MultiplayerSettings] = None,
    room_manager: Optional[RoomManager] = None,
) -> ConnectionManager:
    """Retrieve or initialize the global ConnectionManager singleton."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager(settings=settings, room_manager=room_manager)
    return _connection_manager


def set_connection_manager(manager: Optional[ConnectionManager]) -> None:
    """Explicitly override global ConnectionManager (useful in tests)."""
    global _connection_manager
    _connection_manager = manager
