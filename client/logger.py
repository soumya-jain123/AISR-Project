"""
Telemetry and Command Logger for Operator Client.
Records timestamps, commands, feedback poses, latencies, and states to CSV files for analysis.
"""

import csv
import os
import threading
import time
from typing import Any, Dict, Optional


class TelemetryLogger:
    """Thread-safe CSV logger for teleoperation experiments and analysis."""

    def __init__(self, filepath: Optional[str] = None) -> None:
        if filepath is None:
            os.makedirs("results", exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join("results", f"teleop_log_{timestamp}.csv")

        self.filepath = filepath
        self._lock = threading.Lock()
        self._file = None
        self._writer = None
        self._open()

    def _open(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        self._file = open(self.filepath, mode="w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        # Header row
        self._writer.writerow([
            "timestamp",
            "seq",
            "msg_type",
            "name",
            "x",
            "y",
            "theta",
            "state",
            "linear_vel",
            "angular_vel",
            "latency_ms",
            "details",
        ])
        self._file.flush()

    def log_event(
        self,
        msg_type: str,
        seq: int = 0,
        name: str = "",
        x: float = 0.0,
        y: float = 0.0,
        theta: float = 0.0,
        state: str = "",
        linear_vel: float = 0.0,
        angular_vel: float = 0.0,
        latency_ms: float = 0.0,
        details: str = "",
    ) -> None:
        """Write a single event row to the log CSV."""
        with self._lock:
            if self._writer and self._file:
                self._writer.writerow([
                    round(time.time(), 4),
                    seq,
                    msg_type,
                    name,
                    round(x, 4),
                    round(y, 4),
                    round(theta, 4),
                    state,
                    round(linear_vel, 4),
                    round(angular_vel, 4),
                    round(latency_ms, 2),
                    details,
                ])
                self._file.flush()

    def close(self) -> None:
        """Close log file."""
        with self._lock:
            if self._file:
                try:
                    self._file.flush()
                    self._file.close()
                except Exception:
                    pass
                self._file = None
