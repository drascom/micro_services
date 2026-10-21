"""Admin info notes (README §5.1 ``admin_notes``): short messages for the admin, e.g. "repair applied" (K8)."""
from __future__ import annotations

import builtins
import time
from typing import Optional

from .. import db

LEVELS = ("info", "warn", "action")


def add(level: str, text: str, *, site_id: Optional[str] = None, ref_kind: Optional[str] = None,
        ref_id: Optional[str] = None) -> int:
    if level not in LEVELS:
        raise ValueError(f"note level must be one of {LEVELS}")
    cur = db.execute(
        "INSERT INTO admin_notes(at, level, site_id, text, ref_kind, ref_id) VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), level, site_id, str(text), ref_kind, None if ref_id is None else str(ref_id)),
    )
    return int(cur.lastrowid)


def unread() -> builtins.list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM admin_notes WHERE read_at IS NULL ORDER BY id DESC")]


def list(limit: int = 50, site_id: Optional[str] = None) -> builtins.list[dict]:  # noqa: A001 - contract name
    if site_id is None:
        rows = db.query("SELECT * FROM admin_notes ORDER BY id DESC LIMIT ?", (int(limit),))
    else:
        rows = db.query("SELECT * FROM admin_notes WHERE site_id=? ORDER BY id DESC LIMIT ?", (site_id, int(limit)))
    return [dict(r) for r in rows]


def mark_read(upto_id: Optional[int] = None) -> int:
    """Mark every unread note with ``id <= upto_id`` (all when None) as read. Returns how many."""
    now = int(time.time())
    if upto_id is None:
        cur = db.execute("UPDATE admin_notes SET read_at=? WHERE read_at IS NULL", (now,))
    else:
        cur = db.execute("UPDATE admin_notes SET read_at=? WHERE read_at IS NULL AND id<=?", (now, int(upto_id)))
    return cur.rowcount
