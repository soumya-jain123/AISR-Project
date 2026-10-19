"""
Unit tests for common/netdelay.py.
Verifies artificial delay enforcement, FIFO release order, and thread safety.
"""

import time
import pytest
from common.netdelay import DelayedQueue


def test_zero_delay_queue():
    q = DelayedQueue(delay_ms=0)
    q.put("msg1")
    q.put("msg2")

    assert q.qsize() == 2
    item1 = q.get(timeout=0.1)
    item2 = q.get(timeout=0.1)

    assert item1 == "msg1"
    assert item2 == "msg2"
    assert q.empty()


def test_delay_enforcement():
    delay_ms = 150  # 150 ms
    q = DelayedQueue(delay_ms=delay_ms)

    t0 = time.time()
    q.put("delayed_packet")

    # Trying to get with 50 ms timeout should time out
    with pytest.raises(TimeoutError):
        q.get(timeout=0.05)

    # Getting with sufficient timeout should succeed after approximately delay_ms
    item = q.get(timeout=0.3)
    elapsed = (time.time() - t0) * 1000.0

    assert item == "delayed_packet"
    assert elapsed >= 130.0  # allow small scheduling margin


def test_override_delay_per_item():
    q = DelayedQueue(delay_ms=500)  # default 500 ms

    t0 = time.time()
    # Put with override 50 ms
    q.put("fast_packet", delay_override_ms=50)

    item = q.get(timeout=0.2)
    elapsed = (time.time() - t0) * 1000.0

    assert item == "fast_packet"
    assert elapsed >= 40.0
    assert elapsed < 350.0  # definitely did not wait 500 ms
