"""Watcher notifications (T13 item 8): ``resolved`` -> ``source_found`` (the old payload: title, season, episode, site, method);
``exhausted`` / ``closed_not_public`` / ``waiting_capability`` -> ``source_unavailable`` (payload + ``reason``). A watcher is
notified once per answer (``problem_watchers.notified_at``; a reopened problem clears it). Written via ``core.catalog.notify``."""
from __future__ import annotations

import re

from ... import db
from ...core.catalog import notify as catalog_notify
from . import store

UNAVAILABLE = {"exhausted": "kaynak bulunamadı", "closed_not_public": "içerik herkese açık değil",
               "waiting_capability": "kaynak var, motor desteği bekleniyor"}
_EP = re.compile(r":s(\d+):e(\d+)$")


def _payload(problem: dict, episode_id: str, canonical_id: str) -> dict:
    item = db.query_one("SELECT title FROM library_items WHERE id=?", (canonical_id,))
    match = _EP.search(episode_id or "")
    ev = problem.get("evidence") or {}
    return {"title": item["title"] if item else "", "season": int(match.group(1)) if match else None,
            "episode": int(match.group(2)) if match else None, "site": ev.get("found_site") or problem.get("site_id") or "",
            "method": ev.get("method") or ""}


def send(problem_id: int) -> int:
    """Notify every not-yet-notified watcher of the problem's current answer. Returns how many were written."""
    problem = store.row(problem_id)
    if problem is None:
        return 0
    state = problem["state"]
    if state == "resolved":
        kind, reason = "source_found", ""
    elif state in UNAVAILABLE:
        kind, reason = "source_unavailable", UNAVAILABLE[state]
    else:
        return 0
    sent = 0
    for w in store.watchers(problem_id, pending_only=True):
        if not store.mark_notified(problem_id, w["profile_id"], w["episode_id"]):
            continue   # someone else just sent it
        payload = _payload(problem, w["episode_id"], w["canonical_id"] or problem.get("canonical_id") or "")
        if reason:
            payload["reason"] = reason
        catalog_notify.add(w["profile_id"], kind, w["canonical_id"] or "", w["episode_id"] or "", payload)
        sent += 1
    if sent:
        store.event(problem_id, "system", "notified", f"{kind} x{sent}")
    return sent
