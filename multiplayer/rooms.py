"""
Lobby, Rooms, and Player Assignment for AISR Multiplayer Robot Racing Simulator.

Maintains in-memory room state, handles room creation, code generation,
player capacity enforcement, deterministic owner transfer, and integrates
with the Simulation RobotRegistry for 1:1 atomic robot reservations.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import secrets
import string
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from multiplayer.config import MultiplayerSettings, get_settings
from multiplayer.protocol import (
    ErrorCode,
    MultiplayerProtocolError,
    PlayerJoinedMessage,
    PlayerLeftMessage,
    PlayerReadyMessage,
    RobotAssignedMessage,
    RobotUnavailableMessage,
    RoomMemberPayload,
    RoomSnapshotMessage,
    RoomSummaryPayload,
    now_ts,
)
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry, get_robot_registry

logger = logging.getLogger(__name__)

# Character set for human-friendly room codes (excluding ambiguous chars 0, O, 1, I)
ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ROOM_CODE_LENGTH = 5


class RoomStatus(str, Enum):
    """Lifecycle status of a game room."""

    WAITING = "WAITING"
    STARTING = "STARTING"
    RACING = "RACING"
    FINISHED = "FINISHED"
    CLOSED = "CLOSED"


@dataclass
class RoomMember:
    """Internal representation of a member in a room."""

    session_id: str
    display_name: str
    role: str = "driver"  # "driver" or "spectator"
    robot_id: Optional[str] = None
    is_ready: bool = False
    joined_at: float = field(default_factory=time.time)

    def to_payload(self, is_owner: bool) -> RoomMemberPayload:
        return RoomMemberPayload(
            session_id=self.session_id,
            display_name=self.display_name,
            role=self.role,
            robot_id=self.robot_id,
            is_ready=self.is_ready,
            is_owner=is_owner,
            joined_at=round(self.joined_at, 3),
        )


class Room:
    """
    Server-authoritative room state holding members, robot assignments,
    and readiness flags.
    """

    def __init__(
        self,
        room_id: str,
        room_code: str,
        owner_session_id: str,
        name: Optional[str] = None,
        capacity: int = 4,
    ) -> None:
        self.room_id: str = room_id
        self.room_code: str = room_code.upper()
        self.owner_session_id: str = owner_session_id
        self.name: str = (name or f"Room {self.room_code}").strip()[:64]
        self.capacity: int = capacity
        self.status: RoomStatus = RoomStatus.WAITING
        self.created_at: float = time.time()
        self.updated_at: float = self.created_at

        # Ordered dict of members: session_id -> RoomMember
        self._members: Dict[str, RoomMember] = {}

    @property
    def member_count(self) -> int:
        return len(self._members)

    @property
    def driver_count(self) -> int:
        return sum(1 for m in self._members.values() if m.role == "driver")

    @property
    def is_full(self) -> bool:
        return self.member_count >= self.capacity

    @property
    def member_session_ids(self) -> List[str]:
        return list(self._members.keys())

    def get_member(self, session_id: str) -> Optional[RoomMember]:
        return self._members.get(session_id)

    def add_member(
        self,
        session_id: str,
        display_name: str,
        role: str = "driver",
        robot_id: Optional[str] = None,
    ) -> RoomMember:
        member = RoomMember(
            session_id=session_id,
            display_name=display_name,
            role=role,
            robot_id=robot_id,
            is_ready=False,
            joined_at=time.time(),
        )
        self._members[session_id] = member
        self.updated_at = time.time()
        return member

    def remove_member(self, session_id: str) -> Optional[RoomMember]:
        member = self._members.pop(session_id, None)
        if member:
            self.updated_at = time.time()
        return member

    def set_ready(self, session_id: str, is_ready: bool) -> bool:
        member = self._members.get(session_id)
        if member:
            member.is_ready = is_ready
            self.updated_at = time.time()
            return True
        return False

    def to_snapshot(self, seq: int, ts: Optional[float] = None) -> RoomSnapshotMessage:
        """Create an authoritative snapshot message for members."""
        current_ts = ts if ts is not None else now_ts()
        member_payloads = [
            m.to_payload(is_owner=(m.session_id == self.owner_session_id))
            for m in self._members.values()
        ]
        return RoomSnapshotMessage(
            seq=seq,
            ts=current_ts,
            room_id=self.room_id,
            room_code=self.room_code,
            name=self.name,
            owner_id=self.owner_session_id,
            capacity=self.capacity,
            status=self.status.value,
            members=member_payloads,
        )

    def to_summary(self) -> RoomSummaryPayload:
        """Create public summary metadata for room listing."""
        return RoomSummaryPayload(
            room_id=self.room_id,
            room_code=self.room_code,
            name=self.name,
            owner_id=self.owner_session_id,
            player_count=self.member_count,
            capacity=self.capacity,
            status=self.status.value,
        )


class RoomManager:
    """
    Thread- and async-safe room management layer.
    Coordinates room creation, joining, robot reservation, and disconnect cleanup.
    """

    def __init__(
        self,
        settings: Optional[MultiplayerSettings] = None,
        registry: Optional[RobotRegistry] = None,
    ) -> None:
        self.settings: MultiplayerSettings = settings or get_settings()
        self.registry: RobotRegistry = registry or get_robot_registry()

        self._rooms: Dict[str, Room] = {}
        self._code_to_room_id: Dict[str, str] = {}
        self._session_to_room_id: Dict[str, str] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def _generate_room_code(self) -> str:
        """Generate a random, non-colliding human-friendly room code."""
        for _ in range(200):
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in self._code_to_room_id:
                return code
        raise RuntimeError("Failed to generate a unique room code after 200 attempts.")

    def _sanitize_display_name(self, raw_name: Optional[str], session_id: str) -> str:
        """Sanitize display names for safe in-memory representation."""
        if not raw_name or not raw_name.strip():
            return f"Player_{session_id[-4:]}"
        # Strip control characters, collapse whitespaces, clamp to 32 chars
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", raw_name).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)[:32]
        return cleaned or f"Player_{session_id[-4:]}"

    def _find_and_reserve_available_robot(self, session_id: str) -> Optional[str]:
        """
        Find an available, unassigned robot from the registry and atomically reserve it.
        Must be called while holding self._lock.
        """
        instances = self.registry.get_all_instances()
        for inst in instances:
            if inst.is_available and inst.assigned_player_id is None:
                inst.assigned_player_id = session_id
                logger.info(f"Reserved robot '{inst.robot_id}' for session '{session_id}'")
                return inst.robot_id
        return None

    def _release_robot_reservation(self, robot_id: str, session_id: str) -> None:
        """
        Release a reserved robot back to the pool.
        Must be called while holding self._lock.
        """
        robot = self.registry.get(robot_id)
        if robot is not None:
            if robot.assigned_player_id == session_id:
                robot.assigned_player_id = None
                logger.info(f"Released robot '{robot_id}' reservation for session '{session_id}'")

    async def create_room(
        self,
        session_id: str,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        capacity: Optional[int] = None,
        role: str = "driver",
    ) -> Tuple[Room, Optional[str]]:
        """
        Create a new room with the calling session as owner.
        Assigns an available robot if role == "driver".
        Returns (Room, assigned_robot_id).
        """
        clean_name = self._sanitize_display_name(display_name, session_id)
        effective_capacity = capacity or self.settings.max_players_per_room
        if effective_capacity < 1 or effective_capacity > 16:
            raise MultiplayerProtocolError(
                ErrorCode.ERR_OUT_OF_BOUNDS,
                f"Room capacity must be between 1 and 16, got {effective_capacity}",
            )

        async with self._lock:
            # 1. Enforce: a session can belong to at most one room
            if session_id in self._session_to_room_id:
                curr_room_id = self._session_to_room_id[session_id]
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_ALREADY_IN_ROOM,
                    f"Session '{session_id}' is already in room '{curr_room_id}'. Must leave first.",
                )

            # 2. Reserve robot if driver
            assigned_robot: Optional[str] = None
            if role == "driver":
                assigned_robot = self._find_and_reserve_available_robot(session_id)
                if assigned_robot is None:
                    raise MultiplayerProtocolError(
                        ErrorCode.ERR_NO_ROBOTS_AVAILABLE,
                        "No available robots in the simulation registry to assign to driver.",
                    )

            # 3. Create room instance
            room_id = f"room_{secrets.token_hex(6)}"
            room_code = self._generate_room_code()
            room = Room(
                room_id=room_id,
                room_code=room_code,
                owner_session_id=session_id,
                name=name,
                capacity=effective_capacity,
            )
            room.add_member(
                session_id=session_id,
                display_name=clean_name,
                role=role,
                robot_id=assigned_robot,
            )

            # 4. Update index mappings
            self._rooms[room_id] = room
            self._code_to_room_id[room_code] = room_id
            self._session_to_room_id[session_id] = room_id

            logger.info(
                f"Created room {room_id} (Code: {room_code}) by session {session_id} "
                f"[Capacity: {effective_capacity}, Robot: {assigned_robot}]"
            )
            return room, assigned_robot

    async def join_room(
        self,
        session_id: str,
        room_identifier: str,
        display_name: Optional[str] = None,
        role: str = "driver",
    ) -> Tuple[Room, Optional[str]]:
        """
        Join an existing room by room code or room ID.
        Assigns an available robot if role == "driver".
        Returns (Room, assigned_robot_id).
        """
        clean_name = self._sanitize_display_name(display_name, session_id)
        ident_clean = room_identifier.strip().upper()

        async with self._lock:
            # 1. Enforce: a session can belong to at most one room
            if session_id in self._session_to_room_id:
                curr_room_id = self._session_to_room_id[session_id]
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_ALREADY_IN_ROOM,
                    f"Session '{session_id}' is already in room '{curr_room_id}'. Must leave first.",
                )

            # 2. Resolve room by code or room_id
            room_id = self._code_to_room_id.get(ident_clean)
            if not room_id:
                # Try lower/exact room_id
                room_id = room_identifier.strip()

            room = self._rooms.get(room_id)
            if room is None or room.status == RoomStatus.CLOSED:
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_ROOM_NOT_FOUND,
                    f"Room '{room_identifier}' not found or is closed.",
                )

            # 3. Capacity check
            if room.is_full:
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_ROOM_FULL,
                    f"Room '{room.room_code}' is full (capacity {room.capacity}).",
                )

            # 4. Status check
            if room.status not in (RoomStatus.WAITING, RoomStatus.FINISHED):
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_VALIDATION_FAILED,
                    f"Cannot join room in state '{room.status.value}'.",
                )

            # 5. Reserve robot if driver
            assigned_robot: Optional[str] = None
            if role == "driver":
                assigned_robot = self._find_and_reserve_available_robot(session_id)
                if assigned_robot is None:
                    raise MultiplayerProtocolError(
                        ErrorCode.ERR_NO_ROBOTS_AVAILABLE,
                        "No available robots in simulation registry to assign to driver.",
                    )

            # 6. Add member to room
            room.add_member(
                session_id=session_id,
                display_name=clean_name,
                role=role,
                robot_id=assigned_robot,
            )
            self._session_to_room_id[session_id] = room.room_id

            logger.info(
                f"Session {session_id} joined room {room.room_id} (Code: {room.room_code}) "
                f"[Role: {role}, Robot: {assigned_robot}]"
            )
            return room, assigned_robot

    async def leave_room(
        self, session_id: str
    ) -> Optional[Tuple[Room, Optional[str], Optional[str]]]:
        """
        Leave the room currently joined by session_id.
        Releases any assigned robot.
        Applies deterministic owner transfer if owner leaves:
        - If other members remain: transfers ownership to the next joined member.
        - If no members remain: marks room CLOSED and removes it.
        Returns (Room, released_robot_id, new_owner_id) or None if session not in a room.
        """
        async with self._lock:
            room_id = self._session_to_room_id.pop(session_id, None)
            if not room_id:
                return None

            room = self._rooms.get(room_id)
            if not room:
                return None

            member = room.remove_member(session_id)
            released_robot: Optional[str] = None
            if member and member.robot_id:
                released_robot = member.robot_id
                self._release_robot_reservation(released_robot, session_id)

            new_owner_id: Optional[str] = None

            # Handle owner departure policy
            if room.owner_session_id == session_id:
                if room.member_count > 0:
                    # Transfer ownership to next joined member
                    new_owner_id = room.member_session_ids[0]
                    room.owner_session_id = new_owner_id
                    logger.info(
                        f"Transferred ownership of room {room.room_id} to session {new_owner_id}"
                    )
                else:
                    # No members remain -> close and delete room
                    room.status = RoomStatus.CLOSED
                    self._rooms.pop(room.room_id, None)
                    self._code_to_room_id.pop(room.room_code, None)
                    logger.info(f"Room {room.room_id} (Code: {room.room_code}) closed and cleaned up.")

            return room, released_robot, new_owner_id

    async def set_ready(self, session_id: str, is_ready: bool) -> Tuple[Room, bool]:
        """Update ready status for a member in their current room."""
        async with self._lock:
            room_id = self._session_to_room_id.get(session_id)
            if not room_id:
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_NOT_IN_ROOM,
                    f"Session '{session_id}' is not currently in any room.",
                )

            room = self._rooms.get(room_id)
            if not room or room.status == RoomStatus.CLOSED:
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_ROOM_NOT_FOUND,
                    "Current room is closed or no longer exists.",
                )

            ok = room.set_ready(session_id, is_ready)
            if not ok:
                raise MultiplayerProtocolError(
                    ErrorCode.ERR_NOT_IN_ROOM,
                    f"Session '{session_id}' is not registered as a member in room {room_id}.",
                )

            return room, is_ready

    async def handle_disconnect(
        self, session_id: str
    ) -> Optional[Tuple[Room, Optional[str], Optional[str]]]:
        """
        Idempotent handler called when a session disconnects or unregisters.
        Ensures complete cleanup of room membership and robot reservation.
        """
        return await self.leave_room(session_id)

    async def mark_robot_unavailable(
        self, robot_id: str, reason: str = ""
    ) -> Optional[Tuple[Room, str]]:
        """
        Mark an assigned robot as unavailable without silently reassigning another robot.
        Finds the affected room and member, clears assignment, and returns (Room, session_id).
        """
        async with self._lock:
            for room in self._rooms.values():
                for member in room._members.values():
                    if member.robot_id == robot_id:
                        member.robot_id = None
                        logger.warning(
                            f"Marked robot '{robot_id}' unavailable for session '{member.session_id}' "
                            f"in room '{room.room_id}' (Reason: {reason})"
                        )
                        return room, member.session_id
            return None

    def get_session_room(self, session_id: str) -> Optional[Room]:
        """Lookup room for session without acquiring async lock (safe read)."""
        room_id = self._session_to_room_id.get(session_id)
        if room_id:
            return self._rooms.get(room_id)
        return None

    def get_room_member_session_ids(self, room_id: str) -> List[str]:
        """Get list of session IDs in a room (defensive copy)."""
        room = self._rooms.get(room_id)
        if room:
            return room.member_session_ids
        return []

    def get_room(self, room_identifier: str) -> Optional[Room]:
        """Lookup room by code or ID."""
        ident_clean = room_identifier.strip().upper()
        room_id = self._code_to_room_id.get(ident_clean, room_identifier.strip())
        return self._rooms.get(room_id)

    def list_rooms(self) -> List[RoomSummaryPayload]:
        """Return public summaries of all active, non-closed rooms."""
        summaries = []
        for room in self._rooms.values():
            if room.status != RoomStatus.CLOSED:
                summaries.append(room.to_summary())
        return summaries

    def clear(self) -> None:
        """Clear all active rooms and reservations (useful in tests)."""
        for sid, room_id in list(self._session_to_room_id.items()):
            room = self._rooms.get(room_id)
            if room:
                for member in room._members.values():
                    if member.robot_id:
                        self._release_robot_reservation(member.robot_id, member.session_id)
        self._rooms.clear()
        self._code_to_room_id.clear()
        self._session_to_room_id.clear()


# Singleton accessor
_room_manager: Optional[RoomManager] = None


def get_room_manager(
    settings: Optional[MultiplayerSettings] = None,
    registry: Optional[RobotRegistry] = None,
) -> RoomManager:
    """Retrieve or initialize global RoomManager singleton."""
    global _room_manager
    if _room_manager is None:
        _room_manager = RoomManager(settings=settings, registry=registry)
    return _room_manager


def set_room_manager(manager: Optional[RoomManager]) -> None:
    """Explicitly override global RoomManager singleton (useful in tests)."""
    global _room_manager
    _room_manager = manager
