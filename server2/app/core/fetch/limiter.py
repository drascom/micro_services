"""Thread-safe request limiter: global concurrency, per-host concurrency + spacing, back-off after 429 / challenge.

``with LIMITER.slot(host, concurrency, delay_ms): ...`` waits for a host slot, keeps at least ``delay_ms`` between
the STARTS of two requests to one host (plus the host's current back-off), then takes a global slot. ``penalize(host)``
after a 429 / challenge doubles the host back-off (1x, 2x, 4x ... capped at ``BACKOFF_MAX_S``); ``reset(host)`` after a
success clears it.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Iterator

GLOBAL_CONCURRENCY = 8
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 60.0


class _Host:
    __slots__ = ("cond", "active", "next_start", "strikes", "blocked_until")

    def __init__(self, lock: threading.Lock):
        self.cond = threading.Condition(lock)
        self.active = 0
        self.next_start = 0.0
        self.strikes = 0
        self.blocked_until = 0.0


class Limiter:
    def __init__(self, global_concurrency: int = GLOBAL_CONCURRENCY):
        self._lock = threading.Lock()
        self._global = threading.BoundedSemaphore(global_concurrency)
        self._hosts: dict[str, _Host] = {}

    def _host(self, host: str) -> _Host:
        state = self._hosts.get(host)
        if state is None:
            state = self._hosts[host] = _Host(self._lock)
        return state

    @contextlib.contextmanager
    def slot(self, host: str, concurrency: int = 2, delay_ms: int = 0) -> Iterator[None]:
        concurrency = max(1, int(concurrency or 1))
        delay = max(0.0, (delay_ms or 0) / 1000.0)
        with self._lock:
            state = self._host(host)
            while state.active >= concurrency:
                state.cond.wait()
            state.active += 1
            now = time.monotonic()
            start = max(now, state.next_start, state.blocked_until)
            state.next_start = start + delay
        try:
            wait = start - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            with self._global_slot():
                yield
        finally:
            with self._lock:
                state.active -= 1
                state.cond.notify()

    @contextlib.contextmanager
    def _global_slot(self) -> Iterator[None]:
        self._global.acquire()
        try:
            yield
        finally:
            self._global.release()

    def penalize(self, host: str) -> float:
        """A 429 / challenge: the next request to ``host`` waits longer. Returns the back-off now in force (seconds)."""
        with self._lock:
            state = self._host(host)
            state.strikes += 1
            wait = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (state.strikes - 1)))
            state.blocked_until = max(state.blocked_until, time.monotonic() + wait)
            return wait

    def reset(self, host: str) -> None:
        with self._lock:
            state = self._hosts.get(host)
            if state is not None:
                state.strikes = 0
                state.blocked_until = 0.0

    def backoff_s(self, host: str) -> float:
        """The current back-off step of ``host`` (0 = none)."""
        with self._lock:
            state = self._hosts.get(host)
            if state is None or not state.strikes:
                return 0.0
            return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (state.strikes - 1)))

    def clear(self) -> None:
        with self._lock:
            self._hosts.clear()


LIMITER = Limiter()
