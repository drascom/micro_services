"""In-process events: the one-way channel from `core` (and `api`) to `control` (README §5.2).

``emit`` is synchronous and swallows subscriber errors (logged), so a failing subscriber never breaks the publisher.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger("diziflix.events")

_subs: dict[str, list[Callable[..., Any]]] = {}
_lock = threading.Lock()


def subscribe(name: str, fn: Callable[..., Any]) -> None:
    with _lock:
        handlers = _subs.setdefault(name, [])
        if fn not in handlers:
            handlers.append(fn)


def emit(name: str, **payload: Any) -> None:
    with _lock:
        handlers = list(_subs.get(name, ()))
    for fn in handlers:
        try:
            fn(**payload)
        except Exception:
            log.exception("event %s: subscriber %r failed", name, fn)


def clear() -> None:
    """Drop every subscription (tests)."""
    with _lock:
        _subs.clear()
