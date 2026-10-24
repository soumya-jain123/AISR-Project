"""Network impairment: adds delay and random loss on the CLIENT side.

delay_ms is the extra ROUND-TRIP delay (half is added on the way out, half on
the way back). loss is the probability (0..1) that a message is dropped, applied
independently to every request and every reply. Impairment is applied at
application level, so it works the same for the socket and MQTT transports.
"""
import heapq
import itertools
import random
import threading
import time


class DelayLine:
    """One background thread that runs callbacks at their due time, in order."""

    def __init__(self):
        self._heap = []
        self._cv = threading.Condition()
        self._count = itertools.count()
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def call_later(self, delay, fn, *args):
        with self._cv:
            heapq.heappush(self._heap, (time.monotonic() + delay,
                                        next(self._count), fn, args))
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while self._running and (
                        not self._heap or self._heap[0][0] > time.monotonic()):
                    wait = None if not self._heap else max(
                        0.0, self._heap[0][0] - time.monotonic())
                    self._cv.wait(wait)
                if not self._running:
                    return
                _, _, fn, args = heapq.heappop(self._heap)
            try:
                fn(*args)
            except Exception:
                pass

    def close(self):
        with self._cv:
            self._running = False
            self._cv.notify()


class ImpairedTransport:
    """Wraps any transport (connect/send/set_handler/close)."""

    def __init__(self, inner, delay_ms=0.0, loss=0.0, seed=None):
        self.inner = inner
        self.one_way = max(0.0, delay_ms) / 2000.0
        self.loss = min(max(loss, 0.0), 1.0)
        self.rng = random.Random(seed)
        self.line = DelayLine()
        self.handler = None
        self.dropped_up = 0
        self.dropped_down = 0
        inner.set_handler(self._down)

    def set_handler(self, fn):
        self.handler = fn

    def connect(self):
        self.inner.connect()

    def send(self, msg):
        if self.rng.random() < self.loss:
            self.dropped_up += 1
            return
        if self.one_way > 0:
            self.line.call_later(self.one_way, self.inner.send, msg)
        else:
            self.inner.send(msg)

    def _down(self, msg):
        if self.rng.random() < self.loss:
            self.dropped_down += 1
            return
        if self.one_way > 0:
            self.line.call_later(self.one_way, self._deliver, msg)
        else:
            self._deliver(msg)

    def _deliver(self, msg):
        if self.handler:
            self.handler(msg)

    def close(self):
        self.line.close()
        self.inner.close()
