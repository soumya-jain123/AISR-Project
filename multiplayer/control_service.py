"""
Control Service for AISR Multiplayer Robot Racing Simulator.

Provides a dedicated, authoritative server-side control dispatch layer
between validated client messages, per-robot watchdogs, safety logic,
and the simulation interface (Mock or CoppeliaSim).

Guarantees:
- Server-authoritative resolution of session -> room -> assigned robot.
- Strict isolation: a player can only actuate their assigned robot.
- Rejecting client-supplied overrides (extra fields, forged IDs).
- Per-robot watchdog tracking and independent timeout halting.
- Latched emergency stop (E-stop) enforcement: client control input cannot clear E-stop.
- Distinct individual E-stop vs room-wide E-stop.
- Bounded control update rates to prevent client flooding.
- Safe disconnect and leave-room cleanup.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from multiplayer.config import MultiplayerSettings, get_settings
from multiplayer.protocol import (
    CommandAckMessage,
    ControlState,
    ErrorCode,
    ErrorMessage,
    IndividualEstopMessage,
    IndividualStopMessage,
    MultiplayerServerMessage,
    PlayerInputMessage,
    RobotOperatingState,
    RoomEstopMessage,
    RoomResetMessage,
    SafetyEventMessage,
    SafetyScope,
    now_ts,
)
from multiplayer.rooms import Room, RoomManager, RoomStatus, get_room_manager
from multiplayer.simulation.base import (
    RobotUnavailableError,
    SimulationInterface,
    SimulationNotReadyError,
)
from multiplayer.simulation.robot_registry import RobotInstance, RobotRegistry, get_robot_registry

logger = logging.getLogger(__name__)


class ControlService:
    """
    Dedicated server-side control dispatch and safety manager.
    Coordinates between RoomManager, RobotRegistry, and SimulationInterface.
    """

    def __init__(
        self,
        settings: Optional[MultiplayerSettings] = None,
        room_manager: Optional[RoomManager] = None,
        registry: Optional[RobotRegistry] = None,
        simulation: Optional[SimulationInterface] = None,
    ) -> None:
        self.settings: MultiplayerSettings = settings or get_settings()
        self.room_manager: RoomManager = room_manager or get_room_manager(settings=self.settings)
        self.registry: RobotRegistry = registry or get_robot_registry()
        self._simulation = simulation  # Lazy-loaded via get_simulation if None

        # Per-robot watchdog timestamp: robot_id -> monotonic float
        self._last_control_mono: Dict[str, float] = {}
        # Per-session rate-limit timestamp: session_id -> monotonic float
        self._last_cmd_rate_mono: Dict[str, float] = {}

    @property
    def simulation(self) -> SimulationInterface:
        """Retrieve simulation backend instance."""
        if self._simulation is None:
            from multiplayer.simulation import get_simulation

            self._simulation = get_simulation(settings=self.settings, registry=self.registry)
        if not self._simulation.is_ready():
            try:
                self._simulation.connect()
            except Exception:
                pass
        return self._simulation

    def set_simulation(self, sim: SimulationInterface) -> None:
        """Inject simulation instance (useful in tests)."""
        self._simulation = sim

    def _resolve_session_robot(
        self, session_id: str, ref_seq: int
    ) -> Tuple[Optional[Room], Optional[RobotInstance], Optional[ErrorMessage]]:
        """
        Authoritatively resolve a session's current room and assigned robot.
        Fails closed if the session is not in a room, has no assignment, or robot is unavailable.
        """
        room = self.room_manager.get_session_room(session_id)
        if room is None or room.status == RoomStatus.CLOSED:
            return (
                None,
                None,
                ErrorMessage(
                    seq=0,
                    ts=now_ts(),
                    code=ErrorCode.ERR_NOT_IN_ROOM.value,
                    message=f"Session '{session_id}' is not in an active room.",
                    ref_seq=ref_seq,
                ),
            )

        member = room.get_member(session_id)
        if member is None or member.robot_id is None:
            return (
                room,
                None,
                ErrorMessage(
                    seq=0,
                    ts=now_ts(),
                    code=ErrorCode.ERR_UNAUTHORIZED.value,
                    message=f"Session '{session_id}' has no assigned robot in room '{room.room_id}'.",
                    ref_seq=ref_seq,
                ),
            )

        robot = self.registry.get(member.robot_id)
        if robot is None:
            return (
                room,
                None,
                ErrorMessage(
                    seq=0,
                    ts=now_ts(),
                    code=ErrorCode.ERR_ROBOT_UNAVAILABLE.value,
                    message=f"Assigned robot '{member.robot_id}' does not exist in registry.",
                    ref_seq=ref_seq,
                ),
            )

        if not robot.is_available:
            return (
                room,
                robot,
                ErrorMessage(
                    seq=0,
                    ts=now_ts(),
                    code=ErrorCode.ERR_ROBOT_UNAVAILABLE.value,
                    message=f"Assigned robot '{robot.robot_id}' is currently unavailable.",
                    ref_seq=ref_seq,
                ),
            )

        return room, robot, None

    async def handle_player_input(
        self, session_id: str, msg: PlayerInputMessage, server_seq: int = 0
    ) -> MultiplayerServerMessage:
        """
        Validate and dispatch driving controls (throttle, steering) to the player's assigned robot.
        """
        # 1. Authoritative resolution of session -> room -> robot
        room, robot, error = self._resolve_session_robot(session_id, msg.seq)
        if error is not None:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=error.code,
                message=error.message,
                ref_seq=msg.seq,
            )
        assert robot is not None

        # 2. Check for latched emergency stop
        if robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_ESTOP_ACTIVE.value,
                message=(
                    f"Robot '{robot.robot_id}' is in latched E-stop state ({robot.operating_state.value}). "
                    "Client control commands cannot clear a latched E-stop."
                ),
                ref_seq=msg.seq,
            )

        # 3. Bounded control update rate limiting
        now_mono = time.monotonic()
        last_mono = self._last_cmd_rate_mono.get(session_id, 0.0)
        min_interval = 1.0 / self.settings.control_rate_limit_hz
        is_neutral = (
            msg.control_state in (ControlState.NEUTRAL, ControlState.RELEASED)
            or (abs(msg.throttle) < 1e-4 and abs(msg.steering) < 1e-4)
        )

        if not is_neutral and (now_mono - last_mono) < min_interval:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_OUT_OF_BOUNDS.value,
                message=(
                    f"Control update rate exceeded limit ({self.settings.control_rate_limit_hz} Hz). "
                    f"Interval was {round(now_mono - last_mono, 4)}s, minimum is {round(min_interval, 4)}s."
                ),
                ref_seq=msg.seq,
            )
        self._last_cmd_rate_mono[session_id] = now_mono

        # 4. Resolve effective throttle and steering
        if msg.control_state in (ControlState.NEUTRAL, ControlState.RELEASED):
            target_throttle = 0.0
            target_steering = 0.0
        else:
            target_throttle = msg.throttle
            target_steering = msg.steering

        # 5. Apply control to simulation
        try:
            ok = self.simulation.apply_control(
                robot.robot_id, throttle=target_throttle, steering=target_steering
            )
            if not ok:
                return ErrorMessage(
                    seq=server_seq,
                    ts=now_ts(),
                    code=ErrorCode.ERR_ESTOP_ACTIVE.value,
                    message=f"Simulation rejected control for robot '{robot.robot_id}'.",
                    ref_seq=msg.seq,
                )
        except (RobotUnavailableError, SimulationNotReadyError) as e:
            logger.error(f"Failed applying control to robot '{robot.robot_id}': {e}")
            robot.is_available = False
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_ROBOT_UNAVAILABLE.value,
                message=f"Simulation error: {e}",
                ref_seq=msg.seq,
            )

        # 6. Update per-robot watchdog timestamp
        self._last_control_mono[robot.robot_id] = now_mono

        # 7. Update operating state if recovering from COMM_LOST
        if robot.operating_state == RobotOperatingState.COMM_LOST:
            if abs(target_throttle) > 1e-4 or abs(target_steering) > 1e-4:
                robot.operating_state = RobotOperatingState.MOVING
            else:
                robot.operating_state = RobotOperatingState.IDLE

        return CommandAckMessage(
            seq=server_seq,
            ts=now_ts(),
            ref_seq=msg.seq,
            ok=True,
            message=f"Control applied to {robot.robot_id} (player_input)",
            state=robot.operating_state,
        )

    async def handle_individual_stop(
        self, session_id: str, msg: IndividualStopMessage, server_seq: int = 0
    ) -> MultiplayerServerMessage:
        """
        Immediately halt motion of the calling session's assigned robot and clear control inputs.
        Does not affect any other player's robot.
        """
        room, robot, error = self._resolve_session_robot(session_id, msg.seq)
        if error is not None:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=error.code,
                message=error.message,
                ref_seq=msg.seq,
            )
        assert robot is not None

        try:
            self.simulation.stop_robot(robot.robot_id, reason=msg.reason)
        except Exception as e:
            logger.warning(f"Error stopping robot '{robot.robot_id}': {e}")

        # Update per-robot watchdog
        self._last_control_mono[robot.robot_id] = time.monotonic()

        return CommandAckMessage(
            seq=server_seq,
            ts=now_ts(),
            ref_seq=msg.seq,
            ok=True,
            message=f"Robot '{robot.robot_id}' stopped",
            state=robot.operating_state,
        )

    async def handle_individual_estop(
        self, session_id: str, msg: IndividualEstopMessage, server_seq: int = 0
    ) -> MultiplayerServerMessage:
        """
        Latch emergency stop on the calling session's assigned robot only.
        Clears stored control state and prevents further actuation until authorized reset.
        """
        room, robot, error = self._resolve_session_robot(session_id, msg.seq)
        if error is not None:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=error.code,
                message=error.message,
                ref_seq=msg.seq,
            )
        assert robot is not None

        # Halt simulation motion immediately
        try:
            self.simulation.stop_robot(robot.robot_id, reason=msg.reason)
        except Exception as e:
            logger.warning(f"Error stopping robot during E-stop '{robot.robot_id}': {e}")

        # Latch individual E-stop state
        robot.operating_state = RobotOperatingState.ESTOP_LATCHED
        self._last_control_mono[robot.robot_id] = time.monotonic()

        logger.warning(
            f"Individual E-stop latched for robot '{robot.robot_id}' by session '{session_id}' (Reason: {msg.reason})"
        )

        return CommandAckMessage(
            seq=server_seq,
            ts=now_ts(),
            ref_seq=msg.seq,
            ok=True,
            message=f"Individual E-stop latched for robot '{robot.robot_id}'",
            state=RobotOperatingState.ESTOP_LATCHED,
        )

    async def handle_room_estop(
        self, session_id: str, msg: RoomEstopMessage, server_seq: int = 0
    ) -> MultiplayerServerMessage:
        """
        Authorized room-wide emergency stop.
        Halts and latches all robots currently in the room.
        Distinct concept from individual E-stop.
        """
        room = self.room_manager.get_room(msg.room_id)
        if room is None or room.status == RoomStatus.CLOSED:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_ROOM_NOT_FOUND.value,
                message=f"Room '{msg.room_id}' not found.",
                ref_seq=msg.seq,
            )

        # Verify sender is in room
        if session_id not in room.member_session_ids:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_UNAUTHORIZED.value,
                message=f"Session '{session_id}' is not a member of room '{room.room_id}'.",
                ref_seq=msg.seq,
            )

        latched_count = 0
        now_mono = time.monotonic()
        for sid in room.member_session_ids:
            member = room.get_member(sid)
            if member and member.robot_id:
                robot = self.registry.get(member.robot_id)
                if robot:
                    try:
                        self.simulation.stop_robot(robot.robot_id, reason=msg.reason)
                    except Exception:
                        pass
                    robot.operating_state = RobotOperatingState.ESTOP_LATCHED
                    self._last_control_mono[robot.robot_id] = now_mono
                    latched_count += 1

        logger.warning(
            f"Room-wide E-stop executed in room '{room.room_id}' by session '{session_id}'. "
            f"Latched {latched_count} robots."
        )

        return CommandAckMessage(
            seq=server_seq,
            ts=now_ts(),
            ref_seq=msg.seq,
            ok=True,
            message=f"Room-wide E-stop latched for {latched_count} robots in room '{room.room_id}'",
            state=RobotOperatingState.ESTOP_LATCHED,
        )

    async def handle_room_reset(
        self, session_id: str, msg: RoomResetMessage, server_seq: int = 0
    ) -> MultiplayerServerMessage:
        """
        Authorized reset to unlatch emergency stops in a room.
        Enforces that only the room owner/host can reset room-wide E-stop.
        """
        room = self.room_manager.get_room(msg.room_id)
        if room is None or room.status == RoomStatus.CLOSED:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_ROOM_NOT_FOUND.value,
                message=f"Room '{msg.room_id}' not found.",
                ref_seq=msg.seq,
            )

        # Enforce host authority
        if room.owner_session_id != session_id:
            return ErrorMessage(
                seq=server_seq,
                ts=now_ts(),
                code=ErrorCode.ERR_UNAUTHORIZED.value,
                message="Only the room host can reset the emergency stop state.",
                ref_seq=msg.seq,
            )

        reset_count = 0
        now_mono = time.monotonic()
        for sid in room.member_session_ids:
            member = room.get_member(sid)
            if member and member.robot_id:
                robot = self.registry.get(member.robot_id)
                if robot and robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
                    robot.operating_state = RobotOperatingState.IDLE
                    self._last_control_mono[robot.robot_id] = now_mono
                    reset_count += 1

        logger.info(
            f"Room E-stop reset executed in room '{room.room_id}' by host '{session_id}'. "
            f"Reset {reset_count} robots to IDLE."
        )

        return CommandAckMessage(
            seq=server_seq,
            ts=now_ts(),
            ref_seq=msg.seq,
            ok=True,
            message=f"Room E-stop reset for {reset_count} robots in room '{room.room_id}'",
            state=RobotOperatingState.IDLE,
        )

    def reset_robot_estop(self, robot_id: str) -> bool:
        """
        Authorized server-side individual E-stop reset procedure.
        Transitions an individual robot from ESTOP_LATCHED back to IDLE.
        """
        robot = self.registry.get(robot_id)
        if robot is not None and robot.operating_state == RobotOperatingState.ESTOP_LATCHED:
            robot.operating_state = RobotOperatingState.IDLE
            self._last_control_mono[robot_id] = time.monotonic()
            logger.info(f"Authorized server-side reset unlatched E-stop for robot '{robot_id}'.")
            return True
        return False

    def handle_session_disconnect(self, session_id: str, robot_id: Optional[str] = None) -> None:
        """
        Handle session disconnect or room departure:
        Immediately halts the robot with neutral input, clears stored control state,
        and removes its watchdog tracking so reconnection cannot resume prior throttle.
        """
        self._last_cmd_rate_mono.pop(session_id, None)

        target_robot_id = robot_id
        if target_robot_id is None:
            # Check if any robot was assigned to this session
            for r in self.registry.get_all_instances():
                if r.assigned_player_id == session_id:
                    target_robot_id = r.robot_id
                    break

        if target_robot_id:
            self._last_control_mono.pop(target_robot_id, None)
            try:
                self.simulation.stop_robot(
                    target_robot_id, reason="Controlling session disconnected"
                )
            except Exception as e:
                logger.warning(f"Error stopping robot '{target_robot_id}' on disconnect: {e}")

            robot = self.registry.get(target_robot_id)
            if robot and robot.operating_state != RobotOperatingState.ESTOP_LATCHED:
                robot.operating_state = RobotOperatingState.IDLE

            logger.info(
                f"Cleaned up control state and halted robot '{target_robot_id}' for disconnected session '{session_id}'"
            )

    def check_watchdogs(self, now_mono: Optional[float] = None) -> List[Tuple[str, str]]:
        """
        Per-robot watchdog evaluation loop.
        If a robot with an active controlling session receives no valid control updates
        beyond control_watchdog_timeout_sec, halt ONLY that robot and set state to COMM_LOST.
        Other robots continue moving unaffected.
        Returns list of (robot_id, reason) that timed out on this tick.
        """
        current_mono = now_mono if now_mono is not None else time.monotonic()
        timeout_sec = self.settings.control_watchdog_timeout_sec
        timed_out: List[Tuple[str, str]] = []

        for robot in self.registry.get_all_instances():
            if robot.assigned_player_id is None:
                continue

            last_mono = self._last_control_mono.get(robot.robot_id)
            if last_mono is None:
                continue

            elapsed = current_mono - last_mono
            if elapsed > timeout_sec:
                # Watchdog expired for this robot
                if (
                    robot.operating_state == RobotOperatingState.MOVING
                    or robot.operating_state == RobotOperatingState.IDLE
                ):
                    try:
                        self.simulation.stop_robot(
                            robot.robot_id, reason="Per-robot watchdog timeout"
                        )
                    except Exception as e:
                        logger.warning(f"Error stopping robot '{robot.robot_id}': {e}")

                    robot.operating_state = RobotOperatingState.COMM_LOST
                    timed_out.append(
                        (robot.robot_id, f"Watchdog timeout ({round(elapsed, 2)}s > {timeout_sec}s)")
                    )
                    logger.warning(
                        f"Watchdog timeout on robot '{robot.robot_id}' (elapsed {round(elapsed, 2)}s). "
                        "Halted robot and transitioned to COMM_LOST."
                    )

        return timed_out


# Singleton accessor
_control_service: Optional[ControlService] = None


def get_control_service(
    settings: Optional[MultiplayerSettings] = None,
    room_manager: Optional[RoomManager] = None,
    registry: Optional[RobotRegistry] = None,
    simulation: Optional[SimulationInterface] = None,
) -> ControlService:
    """Retrieve or initialize the global ControlService singleton."""
    global _control_service
    if _control_service is None:
        _control_service = ControlService(
            settings=settings,
            room_manager=room_manager,
            registry=registry,
            simulation=simulation,
        )
    return _control_service


def set_control_service(service: Optional[ControlService]) -> None:
    """Explicitly override global ControlService (useful in tests)."""
    global _control_service
    _control_service = service
