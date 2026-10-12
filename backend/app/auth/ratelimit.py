"""Slowing down password guessing, in memory.

After FREE_FAILURES wrong passwords for one key (an email, or an IP address),
each further attempt must wait twice as long as the last, up to MAX_WAIT_S.
A success clears the key. Waits grow rather than lock the account outright,
so nobody can lock someone else out by guessing at their email.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

FREE_FAILURES = 5
FIRST_WAIT_S = 15.0
MAX_WAIT_S = 15 * 60.0
FORGET_AFTER_S = 60 * 60.0


class Backoff:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}   # key -> (failures, last)

    def wait_s(self, key: str) -> float:
        """Seconds before `key` may try again; 0 means now."""
        with self._lock:
            failures, last = self._state.get(key, (0, 0.0))
        if failures < FREE_FAILURES:
            return 0.0
        wait = min(FIRST_WAIT_S * 2 ** (failures - FREE_FAILURES), MAX_WAIT_S)
        return max(0.0, last + wait - self._clock())

    def failed(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            failures, last = self._state.get(key, (0, 0.0))
            if now - last > FORGET_AFTER_S:
                failures = 0
            self._state[key] = (failures + 1, now)
            if len(self._state) > 10_000:                 # bound the memory
                cutoff = now - FORGET_AFTER_S
                self._state = {k: v for k, v in self._state.items() if v[1] > cutoff}

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._state.clear()


class Window:
    """At most `limit` events per key within `period_s`."""

    def __init__(self, limit: int, period_s: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.period_s = period_s
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def hit(self, key: str) -> float:
        """Record an event; returns 0, or the seconds to wait if over the limit."""
        now = self._clock()
        with self._lock:
            recent = [t for t in self._events.get(key, []) if now - t < self.period_s]
            if len(recent) >= self.limit:
                self._events[key] = recent
                return recent[0] + self.period_s - now
            recent.append(now)
            self._events[key] = recent
            return 0.0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


logins = Backoff()
access_requests = Window(limit=5, period_s=60 * 60.0)


def reset() -> None:
    logins.clear()
    access_requests.clear()
