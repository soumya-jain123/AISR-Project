"""
Heartbeat Watchdog for the Robot Server.
Monitors periodic heartbeat messages from the client.
Triggers safety stops when heartbeats are delayed beyond HEARTBEAT_TIMEOUT (1.5 s).
"""

import logging
import threading
import time
from typing import Callable
from common.config import HEARTBEAT_TIMEOUT

logger = logging.getLogger(__name__)


class HeartbeatWatchdog:
    """
    Watches incoming heartbeats from the client.
    Calls `on_timeout` when no heartbeat is received within `timeout_sec`.
    Calls `on_recovered` when heartbeats resume after a timeout.
    """

    def __init__(
        self,
        timeout_sec: float = HEARTBEAT_TIMEOUT,
        check_interval_sec: float = 0.05,
        on_timeout: Callable[[], None] = lambda: None,
        on_recovered: Callable[[], None] = lambda: None,
    ) -> None:
        self._timeout_sec = float(timeout_sec)
        self._check_interval_sec = float(check_interval_sec)
        self._on_timeout = on_timeout
        self._on_recovered = on_recovered

        self._lock = threading.Lock()
        self._last_heartbeat = time.time()
        self._is_timed_out = False
        self._is_active = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the watchdog monitor thread."""
        with self._lock:
            if self._is_active:
                return
            self._is_active = True
            self._last_heartbeat = time.time()
            self._is_timed_out = False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="WatchdogThread")
            self._thread.start()
            logger.info(f"Heartbeat watchdog started (timeout={self._timeout_sec}s)")

    def stop(self) -> None:
        """Stop the watchdog monitor thread."""
        self._stop_event.set()
        with self._lock:
            self._is_active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        logger.info("Heartbeat watchdog stopped")

    def feed(self) -> None:
        """
        Record receipt of a fresh heartbeat.
        If previously timed out, fires on_recovered callback.
        """
        was_timed_out = False
        with self._lock:
            self._last_heartbeat = time.time()
            if self._is_timed_out:
                self._is_timed_out = False
                was_timed_out = True

        if was_timed_out:
            logger.info("Heartbeat recovered from timeout")
            try:
                self._on_recovered()
            except Exception as e:
                logger.error(f"Error in watchdog on_recovered: {e}")

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._check_interval_sec)

            trigger_timeout = False
            with self._lock:
                if not self._is_active:
                    break
                elapsed = time.time() - self._last_heartbeat
                if elapsed > self._timeout_sec and not self._is_timed_out:
                    self._is_timed_out = True
                    trigger_timeout = True

            if trigger_timeout:
                logger.warning(
                    f"Watchdog timeout triggered! No heartbeat for {elapsed:.2f}s (> {self._timeout_sec}s)"
                )
                try:
                    self._on_timeout()
                except Exception as e:
                    logger.error(f"Error in watchdog on_timeout callback: {e}")
