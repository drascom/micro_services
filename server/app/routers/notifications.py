"""Source-finder endpoint (library/sourcefinder.py): the finder state of a title / episode. (``/api/notifications`` +
``/read`` were removed 2026-10-24: no client called them.)

* ``GET  /api/source-finder/{item_id}?episode=``  -> ``{"state": idle|searching|found|not_found, "steps": [...], "updated_at": int}``

Additive endpoints; the error envelope is the usual one (``errors.py``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .. import cache
from ..errors import not_found
from ..library import sourcefinder

router = APIRouter(tags=["source-finder"])


@router.get("/api/source-finder/{item_id}")
def finder_state(item_id: str, episode: Optional[str] = Query(None, description="Episode id for series"),
                 debug: int = Query(0, description="1 = add debug_trace (the finder's steps)")) -> dict:
    snap = cache.get()
    item = snap.by_id.get(item_id)
    if not item:
        raise not_found("item")
    requested_id, item_id = item_id, item["id"]
    if episode == requested_id:
        episode = item_id
    if episode and episode in snap.episodes:
        episode = snap.episodes[episode][2]["id"]
    if episode and episode != item_id and (episode not in snap.episodes or snap.episodes[episode][0]["id"] != item_id):
        raise not_found("episode")
    # no episode asked: a film is its own "episode"; a series answers with its newest job whatever the episode
    out = sourcefinder.status(item_id, episode if episode else (item_id if item["type"] != "series" else None))
    if debug == 1:
        out["debug_trace"] = _finder_trace(out)
    return out


_STEP_NAMES = {"retry": "kaynakları yeniden çöz", "search": "sitelerde ara", "heal": "onarım (heal)"}


def _finder_trace(status: dict) -> list:
    """The finder's steps in the ``debug_trace`` shape of ``/api/streams?debug=1`` (``t_ms`` = cumulative step time)."""
    out, t = [], 0
    for step in status.get("steps") or []:
        t += int(step.get("ms") or 0)
        note = str(step.get("note") or "")
        result = "found" if step.get("ok") else ("error" if note.startswith("hata") else "none")
        name = str(step.get("name") or "")
        out.append({"t_ms": t, "stage": "finder", "result": result, "provider": _STEP_NAMES.get(name, name),
                    "detail": note[:200], "ms": int(step.get("ms") or 0)})
    out.append({"t_ms": t, "stage": "finder", "result": "info", "detail": "durum: " + str(status.get("state") or "")})
    return out
