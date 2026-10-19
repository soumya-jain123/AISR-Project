"""
Client Heartbeat sender.
Runs in a background thread and sends heartbeat messages every 0.5 s to keep connection alive.
"""

import logging
import threading
import time
from typing import Callable
from common.config import HEARTBEAT_INTERVAL
from common.protocol import create_hb

logger = logging.getLogger(__name__)


class HeartbeatSender:
    """
    Background worker sending periodic heartbeats over the network link.
    """

    def __init__(
        self,
        send_callback: Callable[[dict], None],
        interval_sec: float = HEARTBEAT_INTERVAL,
    ) -> None:
        self._send_callback = send_callback
        self._interval_sec = float(interval_sec)
        self._hb_seq = 0
        self._is_running = False
        self._is_paused = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background heartbeat sender."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._is_paused = False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="HeartbeatSender")
            self._thread.start()
            logger.info(f"Heartbeat sender started (interval: {self._interval_sec}s)")

    def stop(self) -> None:
        """Stop sending heartbeats."""
        self._stop_event.set()
        with self._lock:
            self._is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        logger.info("Heartbeat sender stopped")

    def pause(self) -> None:
        """Temporarily pause heartbeats to simulate network disconnection."""
        with self._lock:
            self._is_paused = True
        logger.warning("Heartbeat paused (simulating link failure)")

    def resume(self) -> None:
        """Resume sending heartbeats."""
        with self._lock:
            self._is_paused = False
        logger.info("Heartbeat resumed")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(self._interval_sec)
            with self._lock:
                if not self._is_running:
                    break
                if self._is_paused:
                    continue
                self._hb_seq += 1
                seq = self._hb_seq

            hb = create_hb(seq=seq)
            try:
                self._send_callback(hb)
            except Exception as e:
                logger.error(f"Failed to send heartbeat: {e}")
