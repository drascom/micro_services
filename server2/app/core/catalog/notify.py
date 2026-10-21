"""Per-profile client notifications (``notifications`` table; API.md ``/api/notifications``).

Kinds (README §5.7): ``source_found``, ``source_unavailable``, ``fixed``. ``payload`` carries the display fields the client
reads (``title``, ``season``, ``episode``, ``site``, ``method``); anything else in it is kept but not sent.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ... import db

LIMIT = 50


def add(profile_id: str, kind: str, canonical_id: str, episode_id: str = "", payload: Optional[dict] = None) -> int:
    cur = db.execute("INSERT INTO notifications(profile_id,kind,canonical_id,episode_id,payload,created_at) VALUES (?,?,?,?,?,?)",
                     (str(profile_id or ""), str(kind), str(canonical_id or ""), str(episode_id or ""),
                      json.dumps(payload or {}, ensure_ascii=False, default=str), int(time.time())))
    return int(cur.lastrowid)


def _item(row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {"id": row["id"], "kind": row["kind"], "canonical_id": row["canonical_id"] or "",
            "episode_id": row["episode_id"] or "", "title": str(payload.get("title") or ""),
            "season": payload.get("season"), "episode": payload.get("episode"), "site": str(payload.get("site") or ""),
            "method": str(payload.get("method") or ""), "created_at": int(row["created_at"])}


def unread(profile_id: str, since: int = 0) -> dict[str, Any]:
    """``{"items": [...], "last_id": int}``: unread notifications newer than ``since`` (oldest first, at most ``LIMIT``);
    ``last_id`` = the newest id handed out (``since`` when there is none)."""
    since = max(0, int(since or 0))
    rows = db.query("SELECT * FROM notifications WHERE profile_id=? AND id>? AND read_at IS NULL ORDER BY id LIMIT ?",
                    (str(profile_id or ""), since, LIMIT))
    items = [_item(r) for r in rows]
    return {"items": items, "last_id": items[-1]["id"] if items else since}


def mark_read(profile_id: str, upto: int) -> int:
    """Mark the profile's notifications up to id ``upto`` read; returns how many were still unread."""
    upto = max(0, int(upto or 0))
    with db.transaction() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE profile_id=? AND id<=? AND read_at IS NULL",
                         (str(profile_id or ""), upto)).fetchone()["n"]
        conn.execute("UPDATE notifications SET read_at=? WHERE profile_id=? AND id<=? AND read_at IS NULL",
                     (int(time.time()), str(profile_id or ""), upto))
    return int(n)
