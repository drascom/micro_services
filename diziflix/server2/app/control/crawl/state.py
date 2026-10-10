"""Per-site crawl state (table ``crawl_sites``, one JSON row per site) and the crawl clock (``now``, injectable in tests).

Keys: ``k1_start`` (epoch the full import began), ``k1_done_at``, ``quota_day`` / ``quota_used`` (K1 title pages of that
backfill day), ``gate_date`` / ``gate_used`` (gate probe pages of that calendar day), ``paused`` (admin), ``hold_until``
(block protection), ``challenge_streak``, ``drift`` (``{page_type: {streak, reasons[]}}``), ``stopped``
(``{page_type: {at, evidence}}``), ``refresh_date`` (the last daily refresh), ``last_run``."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from ... import db, settings

#: the crawl clock (tests replace it)
now: Callable[[], float] = time.time

DAY_S = 86400


def today(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now() if t is None else t))


def get(site_id: str) -> dict:
    row = db.query_one("SELECT data FROM crawl_sites WHERE site_id=?", (site_id,))
    if row is None:
        return {}
    try:
        data = json.loads(row["data"] or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _write(conn, site_id: str, data: dict) -> None:
    conn.execute("INSERT INTO crawl_sites(site_id, data, updated_at) VALUES (?,?,?) ON CONFLICT(site_id) DO UPDATE SET "
                 "data=excluded.data, updated_at=excluded.updated_at",
                 (site_id, json.dumps(data, ensure_ascii=False), int(now())))


def update(site_id: str, fn: Callable[[dict], Any]) -> dict:
    """Read-modify-write in one transaction (``fn`` mutates the dict in place); returns the new state."""
    with db.transaction() as conn:
        row = conn.execute("SELECT data FROM crawl_sites WHERE site_id=?", (site_id,)).fetchone()
        try:
            data = json.loads(row["data"]) if row else {}
        except ValueError:
            data = {}
        fn(data)
        _write(conn, site_id, data)
    return data


def delete(site_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM crawl_sites WHERE site_id=?", (site_id,))
        conn.execute("DELETE FROM crawl_frontier WHERE site_id=?", (site_id,))
        conn.execute("DELETE FROM crawl_origins WHERE site_id=?", (site_id,))


def site_setting(site_id: str, name: str) -> int:
    """``backfill_days`` / ``refresh_hour`` of the site (``sites`` column), else the global setting."""
    row = db.query_one(f"SELECT {name} FROM sites WHERE site_id=?", (site_id,)) if name in ("backfill_days", "refresh_hour") \
        else None
    value = row[name] if row is not None else None
    if value is None:
        value = settings.get(name, 3 if name == "backfill_days" else 4)
    return int(value)
