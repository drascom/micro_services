"""Per-thread trail of what a provider resolver did, so a failed resolution is explainable.

Provider ``resolve()`` functions keep their contract (a dict or ``None``); they additionally ``note`` each stage
(host, outcome, milliseconds, short error) here. ``library/videos.py`` brackets one candidate with
``begin()`` / ``take()`` and hands the events to ``state.record_resolver``. Outside such a bracket ``note`` does
nothing, so providers stay usable standalone and in tests.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_local = threading.local()


def begin() -> None:
    _local.events = []


def take() -> list[dict[str, Any]]:
    events = getattr(_local, "events", None) or []
    _local.events = None
    return events


def short(exc: object, limit: int = 160) -> str:
    text = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc or "")
    return " ".join(text.split())[:limit]


def note(stage: str, host: str, ok: bool, started: float, error: Optional[object] = None) -> None:
    """Record one stage; ``started`` is a ``time.monotonic()`` reading."""
    events = getattr(_local, "events", None)
    if events is None:
        return
    events.append({"stage": stage, "host": host or "", "ok": bool(ok),
                   "ms": int((time.monotonic() - started) * 1000), "error": "" if ok else short(error)})
