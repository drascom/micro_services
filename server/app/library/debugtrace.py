"""Optional resolution trace for ``/api/streams?debug=1`` (the client's "Hata ayıklama" panel).

``start()`` binds a :class:`Trace` to the current context; ``add(...)`` appends a step to it (a no-op without one, so
debug=0 costs one ContextVar lookup per call site). Worker threads see the trace when submitted through :func:`bind`
(``pool.submit(bind(fn), ...)``): each submission runs in its own copy of the caller's context. Appends are locked.

A step: ``{t_ms, stage, result, site?, provider?, host?, detail?, ms?}``; ``result`` is one of ``found`` | ``none`` |
``skipped`` | ``error`` | ``timeout`` | ``info``.
"""
from __future__ import annotations

import contextvars
import threading
import time
from typing import Optional
from urllib.parse import urlparse

RESULTS = ("found", "none", "skipped", "error", "timeout", "info")
MAX_STEPS = 200

_current: contextvars.ContextVar = contextvars.ContextVar("dz_debug_trace", default=None)


class Trace:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.steps: list = []
        self._lock = threading.Lock()

    def add(self, stage: str, result: str, **fields) -> None:
        step = {"t_ms": int((time.monotonic() - self.t0) * 1000), "stage": str(stage)[:40],
                "result": result if result in RESULTS else "info"}
        for key in ("site", "provider", "host", "detail"):
            value = fields.get(key)
            if value not in (None, ""):
                step[key] = str(value)[:200]
        if isinstance(fields.get("ms"), (int, float)) and not isinstance(fields.get("ms"), bool):
            step["ms"] = int(fields["ms"])
        with self._lock:
            if len(self.steps) < MAX_STEPS:
                self.steps.append(step)

    def take(self) -> list:
        with self._lock:
            return sorted(self.steps, key=lambda s: s["t_ms"])


def start() -> Trace:
    trace = Trace()
    _current.set(trace)
    return trace


def stop() -> None:
    _current.set(None)


def active() -> Optional[Trace]:
    return _current.get()


def add(stage: str, result: str, **fields) -> None:
    trace = _current.get()
    if trace is not None:
        trace.add(stage, result, **fields)


def bind(fn):
    """``fn`` wrapped to run in a copy of the current context (the trace follows it into a pool thread)."""
    if _current.get() is None:
        return fn
    return _Bound(contextvars.copy_context(), fn)


class _Bound:
    __slots__ = ("ctx", "fn")

    def __init__(self, ctx, fn) -> None:
        self.ctx, self.fn = ctx, fn

    def __call__(self, *args, **kwargs):
        return self.ctx.copy().run(self.fn, *args, **kwargs)   # a fresh copy per call: one context cannot run in two threads


def host(url) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
