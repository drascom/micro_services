"""Runtime settings managed from the admin panel, stored as JSON in the `settings` table (README §6)."""
from __future__ import annotations

import json
from typing import Any

from . import db

DEFAULTS: dict[str, Any] = {
    "backfill_days": 3,
    "refresh_hour": 4,
    "crawl_gate_daily_per_site": 60,
    "source_recheck_days": 7,          # T28: episode pages are read again for their source candidates after this many days   # T27: gate probe pages per site and day (admin-triggered crawls are not counted)
    "llm_daily_calls_per_site": 40,
    "repair_autoapply": True,
    "problem_deadline_s": 900,
    "confirm_seconds": 10,
    "tmdb_auto": True,
    "tmdb_types": ["movie", "series"],
    "image_hosts_extra": [],
    "stream_proxy_hosts": [],     # T03: hosts whose streams always go through the stream proxy
    "stream_proxy_force": False,  # T03: proxy every stream
}

_MISSING = object()


def get(key: str, default: Any = _MISSING) -> Any:
    """Stored value; else the explicit ``default``; else the built-in default (None if unknown)."""
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is not None:
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            pass
    if default is not _MISSING:
        return default
    return DEFAULTS.get(key)


def set(key: str, value: Any) -> None:  # noqa: A001 - module API name from the task contract
    db.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def all() -> dict[str, Any]:  # noqa: A001
    out = dict(DEFAULTS)
    for row in db.query("SELECT key, value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
    return out
