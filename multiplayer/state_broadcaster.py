"""
Authoritative State Synchronization and Simulation Control Loop.

Maintains a bounded periodic loop that:
1. Steps the simulation physics (in mock mode).
2. Runs per-robot watchdog checks and halts timed-out robots.
3. Broadcasts authoritative room telemetry (RoomBroadcastMessage) to active room members only.
4. Handles simulation exceptions safely without crashing the server.
5. Emits safety events on watchdog timeout or robot unavailability.
"""

import asyncio
import logging
import time
from typing import Optional

from multiplayer.config import MultiplayerSettings, get_settings
from multiplayer.connection_manager import ConnectionManager, get_connection_manager
from multiplayer.control_service import ControlService, get_control_service
from multiplayer.protocol import (
    RobotOperatingState,
    RobotStatePayload,
    RoomBroadcastMessage,
    SafetyEventMessage,
    SafetyScope,
    now_ts,
)
from multiplayer.rooms import RoomManager, RoomStatus, get_room_manager
from multiplayer.simulation.base import RobotUnavailableError, SimulationInterface
from multiplayer.simulation.mock_sim import MockMultiplayerSimulation

logger = logging.getLogger(__name__)


class StateBroadcaster:
    """
    Background worker managing the simulation tick, watchdog sweep,
    and periodic room-scoped state broadcasts.
    """

    def __init__(
        self,
        settings: Optional[MultiplayerSettings] = None,
        connection_manager: Optional[ConnectionManager] = None,
        room_manager: Optional[RoomManager] = None,
        control_service: Optional[ControlService] = None,
        simulation: Optional[SimulationInterface] = None,
    ) -> None:
        self.settings: MultiplayerSettings = settings or get_settings()
        self.connection_manager: ConnectionManager = connection_manager or get_connection_manager(
            settings=self.settings
        )
        self.room_manager: RoomManager = room_manager or get_room_manager(settings=self.settings)
        self.control_service: ControlService = control_service or get_control_service(
            settings=self.settings,
            room_manager=self.room_manager,
            simulation=simulation,
        )

        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._last_broadcast_mono: float = 0.0

    @property
    def simulation(self) -> SimulationInterface:
        return self.control_service.simulation

    async def tick(self, dt: float) -> None:
        """
        Execute a single simulation and synchronization tick.
        Can be called directly in tests or run in the background loop.
        """
        now_mono = time.monotonic()

        # 1. Per-robot watchdog evaluation
        timed_out_robots = self.control_service.check_watchdogs(now_mono=now_mono)
        for robot_id, reason in timed_out_robots:
            # Emit safety event to the robot's room
            for room in list(self.room_manager._rooms.values()):
                for member in room._members.values():
                    if member.robot_id == robot_id:
                        safety_msg = SafetyEventMessage(
                            seq=self.connection_manager._next_server_seq(),
                            ts=now_ts(),
                            scope=SafetyScope.INDIVIDUAL,
                            event="WATCHDOG_TIMEOUT",
                            target_id=robot_id,
                            reason=reason,
                            state=RobotOperatingState.COMM_LOST,
                        )
                        await self.connection_manager.send_to_room(room.room_id, safety_msg)

        # 2. Advance simulation physics if in mock mode
        if isinstance(self.simulation, MockMultiplayerSimulation):
            try:
                self.simulation.step(dt)
            except Exception as e:
                logger.error(f"Error stepping mock simulation: {e}")

        # 3. Broadcast state updates if broadcast interval has elapsed
        broadcast_interval = 1.0 / self.settings.state_broadcast_rate_hz
        if (now_mono - self._last_broadcast_mono) >= broadcast_interval:
            self._last_broadcast_mono = now_mono
            await self._broadcast_room_states()

    async def _broadcast_room_states(self) -> None:
        """Query authoritative robot poses and send RoomBroadcastMessage to each active room."""
        active_rooms = [
            room
            for room in list(self.room_manager._rooms.values())
            if room.status != RoomStatus.CLOSED
        ]

        for room in active_rooms:
            if room.member_count == 0:
                continue

            robot_payloads = []
            for member in list(room._members.values()):
                if member.robot_id:
                    try:
                        state_payload = self.simulation.get_robot_state(member.robot_id)
                        robot_payloads.append(state_payload)
                    except RobotUnavailableError as err:
                        logger.warning(
                            f"Robot '{member.robot_id}' unavailable during state broadcast: {err}"
                        )
                        await self.connection_manager.notify_robot_unavailable(
                            member.robot_id, reason=str(err)
                        )
                    except Exception as e:
                        logger.error(
                            f"Unexpected error getting state for robot '{member.robot_id}': {e}"
                        )

            # Send room broadcast only to members of this room
            broadcast_msg = RoomBroadcastMessage(
                seq=self.connection_manager._next_server_seq(),
                ts=now_ts(),
                room_id=room.room_id,
                race_state=room.status.value,
                robots=robot_payloads,
            )
            await self.connection_manager.send_to_room(room.room_id, broadcast_msg)

    async def start(self) -> None:
        """Start the background state synchronization loop."""
        if self._running or self._loop_task is not None:
            return

        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(
            f"StateBroadcaster started: tick={self.settings.sim_tick_rate_hz} Hz, "
            f"broadcast={self.settings.state_broadcast_rate_hz} Hz"
        )

    async def stop(self) -> None:
        """Cleanly stop the background loop."""
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("StateBroadcaster stopped")

    async def _run_loop(self) -> None:
        tick_interval = 1.0 / self.settings.sim_tick_rate_hz
        while self._running:
            start_mono = time.monotonic()
            try:
                await self.tick(tick_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in StateBroadcaster tick: {e}", exc_info=True)

            elapsed = time.monotonic() - start_mono
            sleep_time = max(0.001, tick_interval - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break


# Singleton accessor
_state_broadcaster: Optional[StateBroadcaster] = None


def get_state_broadcaster(
    settings: Optional[MultiplayerSettings] = None,
    connection_manager: Optional[ConnectionManager] = None,
    room_manager: Optional[RoomManager] = None,
    control_service: Optional[ControlService] = None,
    simulation: Optional[SimulationInterface] = None,
) -> StateBroadcaster:
    """Retrieve or initialize global StateBroadcaster singleton."""
    global _state_broadcaster
    if _state_broadcaster is None:
        _state_broadcaster = StateBroadcaster(
            settings=settings,
            connection_manager=connection_manager,
            room_manager=room_manager,
            control_service=control_service,
            simulation=simulation,
        )
    return _state_broadcaster


def set_state_broadcaster(broadcaster: Optional[StateBroadcaster]) -> None:
    """Explicitly override global StateBroadcaster (useful in tests)."""
    global _state_broadcaster
    _state_broadcaster = broadcaster
