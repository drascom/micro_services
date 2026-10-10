"""Periodic jobs: ``every(kind, key, seconds)`` / ``daily(kind, key, hour)``.

Definitions are code configuration; the persisted "last run" lives in ``settings`` as ``sched:<key>`` (epoch seconds),
so a restart never enqueues the same daily job twice. Check + enqueue + stamp happen in one transaction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ... import db, settings
from . import store


@dataclass
class _Def:
    kind: str
    key: str
    seconds: Optional[int] = None
    hour: Optional[int] = None
    payload: Any = None


_defs: dict[str, _Def] = {}


def every(kind: str, key: str, seconds: int, payload: Any = None) -> None:
    _defs[key] = _Def(kind, key, seconds=int(seconds), payload=payload)


def daily(kind: str, key: str, hour: int, payload: Any = None) -> None:
    _defs[key] = _Def(kind, key, hour=int(hour), payload=payload)


def clear() -> None:
    _defs.clear()


def _due(d: _Def, last: Optional[float], now: float) -> bool:
    if d.seconds is not None:
        return last is None or now - last >= d.seconds
    lt = time.localtime(now)
    if lt.tm_hour < (d.hour or 0):
        return False
    return last is None or time.strftime("%Y-%m-%d", time.localtime(last)) != time.strftime("%Y-%m-%d", lt)


def tick(now: Optional[float] = None) -> list[str]:
    """Enqueue every due definition once. Returns the keys enqueued."""
    t = time.time() if now is None else now
    fired = []
    for d in list(_defs.values()):
        skey = f"sched:{d.key}"
        with db.transaction():
            last = settings.get(skey, None)
            if not _due(d, last, t):
                continue
            store.enqueue(d.kind, d.key, payload=d.payload, now=t)
            settings.set(skey, int(t))
        fired.append(d.key)
    return fired


def install_defaults() -> None:
    every("cache_refresh", "cache_refresh", 900)
    daily("jobs_gc", "jobs_gc", 3)
