"""K9 confirmation (T13 item 7): a ``resolved`` problem becomes ``confirmed`` when a watching profile played the content
``confirm_seconds`` (10) further than its first position reported after ``resolved_at`` with no ``playback_failed`` in between
(a failure reopens the problem through ``intake``). Problems nobody watches stay ``resolved`` and are ``closed`` after 7 days."""
from __future__ import annotations

from typing import Any

from ... import db, settings
from . import store

UNWATCHED_CLOSE_S = 7 * 86400


def on_progress(profile_id: str, canonical_id: str, episode_id: str, position: Any) -> bool:
    """True when this report confirmed the problem."""
    if not profile_id or not canonical_id:
        return False
    problem = store.by_fingerprint(store.episode_fp(canonical_id, episode_id or ""))
    if problem is None or problem["state"] != "resolved":
        return False
    if not db.query_one("SELECT 1 FROM problem_watchers WHERE problem_id=? AND profile_id=?", (problem["id"], profile_id)):
        return False
    try:
        pos = float(position or 0)
    except (TypeError, ValueError):
        return False
    since = int(problem.get("resolved_at") or 0)
    base = next((e for e in store.events(problem["id"], "progress_base", since) if (e["data"] or {}).get("profile") == profile_id), None)
    if base is None:
        store.event(problem["id"], "client", "progress_base", f"{pos:g}", {"profile": profile_id, "position": pos})
        return False
    need = float(settings.get("confirm_seconds") or 10)
    if pos - float(base["data"].get("position") or 0) < need:
        return False
    if store.events(problem["id"], "playback_failed", since):
        return False
    store.transition(problem["id"], "confirmed", actor="client", kind="confirmed",
                     note=f"{profile_id}: {pos - float(base['data']['position']):g} sn hatasız oynatma")
    return True


def sweep() -> int:
    """``resolved`` problems without a watcher, older than 7 days -> ``closed`` (never auto-``confirmed``)."""
    cutoff = store.now() - UNWATCHED_CLOSE_S
    rows = db.query("SELECT p.id FROM problems p WHERE p.state='resolved' AND p.resolved_at < ? AND NOT EXISTS "
                    "(SELECT 1 FROM problem_watchers w WHERE w.problem_id = p.id)", (cutoff,))
    for r in rows:
        store.transition(r["id"], "closed", actor="system", kind="closed", note="izleyicisi yok, 7 gün")
    return len(rows)
