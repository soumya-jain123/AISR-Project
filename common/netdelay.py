"""
Artificial network delay simulation.
Provides an OS-independent, exact, repeatable delayed queue mechanism.
Holds incoming or outgoing messages for a specified delay (in milliseconds).
"""

import heapq
import itertools
import threading
import time
from typing import Any, Optional, Tuple


class DelayedQueue:
    """
    Thread-safe priority-delayed queue.
    Items placed into this queue are released only after `delay_ms` milliseconds
    have elapsed since they were enqueued.
    """

    def __init__(self, delay_ms: float = 0.0) -> None:
        self._delay_sec: float = max(0.0, float(delay_ms) / 1000.0)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        # Heap elements: (release_time, counter, item)
        self._heap: list[Tuple[float, int, Any]] = []
        self._counter = itertools.count()
        self._closed = False

    @property
    def delay_ms(self) -> float:
        with self._lock:
            return self._delay_sec * 1000.0

    def set_delay_ms(self, delay_ms: float) -> None:
        """Update artificial delay at runtime."""
        with self._lock:
            self._delay_sec = max(0.0, float(delay_ms) / 1000.0)
            self._condition.notify_all()

    def put(self, item: Any, delay_override_ms: Optional[float] = None) -> None:
        """
        Enqueue an item to be released after delay.
        Optionally override the queue's global delay for this specific item.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot put to a closed DelayedQueue")

            delay_sec = (
                max(0.0, float(delay_override_ms) / 1000.0)
                if delay_override_ms is not None
                else self._delay_sec
            )
            release_time = time.time() + delay_sec
            count = next(self._counter)
            heapq.heappush(self._heap, (release_time, count, item))
            self._condition.notify()

    def get(self, timeout: Optional[float] = None) -> Any:
        """
        Get the next available item whose delay has elapsed.
        Blocks until an item is ready, queue is closed, or timeout expires.
        Raises TimeoutError if timeout expires without an item ready.
        """
        deadline = time.time() + timeout if timeout is not None else None

        with self._lock:
            while True:
                if self._closed and not self._heap:
                    return None

                now = time.time()

                if self._heap:
                    earliest_release, _, item = self._heap[0]
                    wait_sec = earliest_release - now
                    if wait_sec <= 0:
                        # Earliest item is ready
                        heapq.heappop(self._heap)
                        return item

                    # Earliest item is not ready yet, need to wait
                    if deadline is not None:
                        remaining_timeout = deadline - now
                        if remaining_timeout <= 0:
                            raise TimeoutError("Timed out waiting for delayed item")
                        wait_time = min(wait_sec, remaining_timeout)
                    else:
                        wait_time = wait_sec

                    self._condition.wait(timeout=wait_time)
                else:
                    # Heap is empty, wait for an item to be put
                    if deadline is not None:
                        remaining_timeout = deadline - now
                        if remaining_timeout <= 0:
                            raise TimeoutError("Timed out waiting for delayed item")
                        self._condition.wait(timeout=remaining_timeout)
                    else:
                        self._condition.wait()

    def qsize(self) -> int:
        """Return the number of items currently in the queue (pending + ready)."""
        with self._lock:
            return len(self._heap)

    def empty(self) -> bool:
        """Check if queue has any items."""
        with self._lock:
            return len(self._heap) == 0

    def close(self) -> None:
        """Close the queue, unblocking any waiting threads."""
        with self._lock:
            self._closed = True
            self._condition.notify_all()
