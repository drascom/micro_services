"""The client's view of the problem record (T13 item 9, README §5.7): ``finder`` of ``/api/streams`` and ``/api/source-finder``.

``searching`` only while the problem (or the site problem it waits for) is working and its ``deadline_at`` has not passed;
``found`` = ``resolved|confirmed``; ``not_found`` = everything else. ``steps`` = the last 6 action steps
(``{name: retry|search|heal, ok, ms, note}``).
"""
from __future__ import annotations

from typing import Optional

from ... import db
from . import store

STEPS = 6


def _effective_state(problem: dict) -> str:
    state = problem["state"]
    if problem.get("parent_id") and state not in ("verifying", *store.FINISHED):
        parent = store.row(problem["parent_id"])
        if parent is not None and parent["state"] not in store.FOUND:
            return parent["state"]
    return state


def _state(problem: Optional[dict]) -> str:
    if problem is None:
        return "not_found"
    state = _effective_state(problem)
    if state in store.FOUND:
        return "found"
    if state in store.SEARCHING and store.now() < int(problem.get("deadline_at") or 0):
        return "searching"
    return "not_found"


def client_state(canonical_id: str, episode_id: str = "", profile_id: str = "") -> dict:
    """``{"state": searching|not_found|found}`` of a title / episode."""
    episode_id = "" if episode_id == canonical_id else (episode_id or "")
    return {"state": _state(store.by_fingerprint(store.episode_fp(canonical_id, episode_id)))}


def finder_status(canonical_id: str, episode_id: Optional[str] = None) -> dict:
    """``{state: idle|searching|found|not_found, steps[], updated_at}``; ``episode_id=None`` = the title's latest problem."""
    if episode_id is None:
        row = db.query_one("SELECT * FROM problems WHERE canonical_id=? AND scope IN ('episode','title') AND fingerprint NOT LIKE "
                           "'%:image' ORDER BY updated_at DESC, id DESC LIMIT 1", (canonical_id,))
        problem = store.to_dict(row)
    else:
        problem = store.by_fingerprint(store.episode_fp(canonical_id, "" if episode_id == canonical_id else episode_id))
    if problem is None:
        return {"state": "idle", "steps": [], "updated_at": 0}
    steps = []
    for e in store.events(problem["id"], "step")[-STEPS:]:
        data = e["data"] or {}
        steps.append({"name": data.get("name") or "heal", "ok": bool(data.get("ok")), "ms": int(data.get("ms") or 0),
                      "note": str(data.get("note") or e.get("note") or "")[:200]})
    return {"state": _state(problem), "steps": steps, "updated_at": int(problem["updated_at"] or 0)}
