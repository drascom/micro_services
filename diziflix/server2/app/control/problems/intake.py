"""Event subscribers (T13 item 3): client / core signals -> problem rows + watchers + a ``problem`` job.

``stream_missing`` / ``playback_failed`` (device codes excluded) open the content's problem (or add a watcher), reopen a
``resolved``/``confirmed``/``closed`` one (``attempts+1``) and set ``deadline_at``; ``playback_ok`` / ``progress`` feed the K9
confirmation; ``stream_diag`` adds evidence (a ``learn`` verdict wakes the job at once); ``image_broken`` opens a title-scope
``image_broken`` problem.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ... import db, settings
from ...core import events
from . import confirm, notify, run, store

log = logging.getLogger("diziflix.problems")

DEVICE_CODES = ("unsupported", "decode", "autoplay", "aborted", "offline")
REOPEN_EXHAUSTED_S = 86400


def _deadline() -> int:
    return store.now() + int(settings.get("problem_deadline_s") or 900)


def report(canonical_id: str, episode_id: str, profile_id: str, *, origin: str, data: Optional[dict] = None,
           source_id: str = "") -> Optional[dict]:
    """Open (or join) the problem of a title / episode; returns it after queuing its job."""
    if not canonical_id:
        return None
    episode_id = episode_id or ""
    problem, created = store.open_or_get(store.episode_fp(canonical_id, episode_id),
                                         scope="episode" if episode_id else "title", canonical_id=canonical_id,
                                         episode_id=episode_id, source_id=source_id or None, deadline_at=_deadline(),
                                         evidence={"origin": origin, "report": data or {}})
    store.event(problem["id"], "client" if origin != "stream_missing" else "system", origin, "", data)
    if not created:
        state = problem["state"]
        reopen = state in ("resolved", "confirmed", "closed")
        fresh = state == "exhausted" and store.now() - int(problem.get("closed_at") or 0) >= REOPEN_EXHAUSTED_S
        if reopen or fresh:
            problem = store.transition(problem["id"], "open", actor="client" if origin != "stream_missing" else "system",
                                       kind="reopened", note=origin, attempts=0 if fresh else int(problem["attempts"] or 0) + 1,
                                       deadline_at=_deadline(), next_at=0,
                                       evidence={**(problem.get("evidence") or {}), "origin": origin, "report": data or {}})
    store.watch(problem["id"], profile_id, canonical_id, episode_id)
    if problem["state"] in store.WORKING:
        run.enqueue(problem, run_after=max(0, int(problem.get("next_at") or 0)) if not created else 0)
    else:
        notify.send(problem["id"])   # a new watcher of a finished / waiting problem hears the answer that is already there
    return problem


def on_stream_missing(canonical_id: str = "", episode_id: str = "", profile_id: str = "", entries: Any = None, **_: Any) -> None:
    report(canonical_id, episode_id, profile_id, origin="stream_missing", data={"entries": list(entries or [])[:6]})


def on_playback_failed(source_id: str = "", canonical_id: str = "", episode_id: str = "", profile_id: str = "", code: str = "",
                       engine: str = "", detail: str = "", **_: Any) -> None:
    if code in DEVICE_CODES:
        return
    report(canonical_id, episode_id, profile_id, origin="playback_failed", source_id=source_id,
           data={"source_id": source_id, "code": code, "engine": engine, "detail": (detail or "")[:120]})


def on_playback_ok(source_id: str = "", canonical_id: str = "", episode_id: str = "", profile_id: str = "", **_: Any) -> None:
    problem = store.by_fingerprint(store.episode_fp(canonical_id, episode_id or ""))
    if problem is not None and problem["state"] in ("resolved", "confirmed"):
        store.event(problem["id"], "client", "playback_ok", "", {"source_id": source_id, "profile": profile_id})


def on_progress(profile_id: str = "", canonical_id: str = "", episode_id: str = "", position: Any = 0, duration: Any = 0,
                **_: Any) -> None:
    confirm.on_progress(profile_id, canonical_id, episode_id or "", position)


def on_stream_diag(source_id: str = "", verdict: Any = None, **_: Any) -> None:
    row = db.query_one("SELECT canonical_id, episode_id FROM video_sources WHERE id=?", (source_id,)) if source_id else None
    if row is None:
        return
    problem = store.by_fingerprint(store.episode_fp(row["canonical_id"], row["episode_id"] or ""))
    if problem is None or problem["state"] in store.FINISHED:
        return
    store.event(problem["id"], "system", "stream_diag", (verdict or {}).get("code", ""), {"source_id": source_id, "verdict": verdict})
    if (verdict or {}).get("learn") and problem["state"] in store.WORKING:
        run.enqueue(problem, priority=5)   # a verdict that knows the cure: no reason to wait


def on_image_broken(canonical_id: str = "", kind: str = "", url: str = "", reason: str = "", **_: Any) -> None:
    if not canonical_id:
        return
    problem, created = store.open_or_get(f"title:{canonical_id}:image", scope="title", canonical_id=canonical_id, episode_id="",
                                         deadline_at=_deadline(), evidence={"kind": kind, "url": url, "reason": reason},
                                         **{"class": "image_broken", "layer": "image"})
    if created:
        run.enqueue(problem)
    else:
        store.event(problem["id"], "system", "image_broken", reason, {"kind": kind, "url": url})


def _image_broken(**payload: Any) -> None:
    """T14: a broken image is counted per site (``system``), no per-title problem (:func:`on_image_broken` stays for callers)."""
    from . import system
    system.on_image_broken(**payload)


def _structure_changed(**payload: Any) -> None:
    from . import system
    system.on_structure_changed(**payload)


SUBSCRIPTIONS = {"stream_missing": on_stream_missing, "playback_failed": on_playback_failed, "playback_ok": on_playback_ok,
                 "progress": on_progress, "stream_diag": on_stream_diag, "image_broken": _image_broken,
                 "structure_changed": _structure_changed}


def subscribe() -> None:
    for name, fn in SUBSCRIPTIONS.items():
        events.subscribe(name, fn)
