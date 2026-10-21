"""Admin-driven TMDB backfill: one in-process job, a stored preview, and its bookkeeping.

* ``begin`` validates + reserves the single TMDB slot in the active-job registry
  (``scraper.state``, key ``("tmdb", "tmdb")``; shown in the admin job bar as
  ``TMDB zenginleştirme (film|dizi) x/y``); ``execute`` runs it (call from a background task).
* A dry run (``dry_run=True``) stores its per-title decisions in ``data/tmdb_preview.json`` (atomic
  write). ``dry_run=False`` right after a fresh preview (< ``PREVIEW_TTL``, same type, not yet applied)
  writes exactly the previewed ``auto`` decisions WITHOUT querying TMDB again; otherwise it runs live.
* ``scope="seasons"`` (kind ``series``) is the second job type: season posters + episode titles/overviews/
  air dates/runtimes/stills for the TMDB-matched series (``library/seasons.py``). Same slot, preview/apply
  flow and event; its preview lives in ``data/tmdb_seasons_preview.json`` (per-series season/episode counts
  and sample images; the apply payload is stored with it).
* On finish a ``kind=tmdb`` event (counters, duration, dry-run flag) is appended to the ops history and,
  after a real apply, ``cache.refresh_and_prewarm()`` makes the new artwork reach the server image cache.

The TMDB key is never read here, logged or returned; ``tmdb.enabled()`` only says whether one exists.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Optional

from .. import config, settings
from ..scraper import state as sstate
from . import enrich, seasons, tmdb

log = logging.getLogger("library.tmdb_admin")

JOB_SITE = JOB_KIND = "tmdb"
PREVIEW_PATH = os.path.join(config.DATA_DIR, "tmdb_preview.json")
SEASONS_PREVIEW_PATH = os.path.join(config.DATA_DIR, "tmdb_seasons_preview.json")
PREVIEW_TTL = 30 * 60  # seconds a preview may be applied without re-querying TMDB
KIND_LABEL = {"movie": "film", "series": "dizi"}
DECISIONS = ("auto", "review", "unmatched", "error", "deferred")
SCOPES = ("items", "seasons")

_preview_lock = threading.RLock()


class StartError(Exception):
    """Job cannot start (mapped to an HTTP error by the router)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --- preview file -----------------------------------------------------------

def _path(scope: str) -> str:
    return SEASONS_PREVIEW_PATH if scope == "seasons" else PREVIEW_PATH


def load_preview(scope: str = "items") -> Optional[dict[str, Any]]:
    """The stored preview of ``scope`` (``items`` = title matching, ``seasons``), or None when absent/corrupt."""
    try:
        with open(_path(scope), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or data.get("kind") not in settings.TMDB_KINDS:
        return None
    if (data.get("scope") or "items") != scope or (scope == "seasons" and data.get("kind") != "series"):
        return None
    return data


def save_preview(data: dict[str, Any]) -> None:
    path = _path(data.get("scope") or "items")
    with _preview_lock:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmdb_preview.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def preview_age(preview: dict[str, Any], now: Optional[float] = None) -> float:
    return max(0.0, (time.time() if now is None else now) - float(preview.get("created_at") or 0))


def preview_usable(preview: Optional[dict[str, Any]], kind: str, now: Optional[float] = None) -> bool:
    """Fresh (< PREVIEW_TTL), same type and not applied yet."""
    return bool(preview and preview.get("kind") == kind and not preview.get("applied_at")
                and preview_age(preview, now) < PREVIEW_TTL)


def decision_counts(preview: dict[str, Any]) -> dict[str, int]:
    counts = {d: 0 for d in (seasons.DECISIONS if preview.get("scope") == "seasons" else DECISIONS)}
    for it in preview.get("items") or []:
        d = it.get("decision")
        if d in counts:
            counts[d] += 1
    return counts


def preview_summary(preview: Optional[dict[str, Any]], now: Optional[float] = None) -> Optional[dict[str, Any]]:
    if not preview:
        return None
    out = {"kind": preview.get("kind"), "scope": preview.get("scope") or "items", "created_at": preview.get("created_iso"),
           "age_seconds": round(preview_age(preview, now), 1),
           "fresh": preview_usable(preview, preview.get("kind") or "", now),
           "applied": bool(preview.get("applied_at")), "applied_at": preview.get("applied_iso"),
           "limit": preview.get("limit"), "force": bool(preview.get("force")),
           "counts": decision_counts(preview), "lookup": len(preview.get("items") or []),
           "total": preview.get("total_in_library"), "seconds": (preview.get("counters") or {}).get("seconds"),
           "ttl_seconds": PREVIEW_TTL}
    if out["scope"] == "seasons":
        c = preview.get("counters") or {}
        out["totals"] = {k: c.get(k, 0) for k in ("series", "seasons", "episodes", "posters", "stills", "empty")}
    return out


def _mark_applied(kind: str, created_at: Any, written: int, scope: str = "items") -> None:
    with _preview_lock:
        cur = load_preview(scope)
        if cur and cur.get("kind") == kind and cur.get("created_at") == created_at:
            cur["applied_at"] = time.time()
            cur["applied_iso"] = sstate._now()
            cur["written"] = written
            save_preview(cur)


# --- job --------------------------------------------------------------------

def begin(kind: str, dry_run: bool, limit: Optional[int] = None, force: bool = False,
          use_preview: bool = True, trigger: str = "manual", scope: str = "items") -> dict[str, Any]:
    """Validate and reserve the single TMDB job slot. Returns the job spec for ``execute``."""
    if kind not in settings.TMDB_KINDS:
        raise StartError(422, "validation_error", "kind must be movie or series")
    if scope not in SCOPES or (scope == "seasons" and kind != "series"):
        raise StartError(422, "validation_error", "scope must be items, or seasons (series only)")
    if not tmdb.enabled():
        raise StartError(400, "tmdb_not_configured", "TMDB anahtarı tanımlı değil")
    if kind not in settings.tmdb_types():
        raise StartError(400, "tmdb_type_disabled",
                         "%s için TMDB zenginleştirme seçili değil; Ayarlar'da türü işaretleyin"
                         % ("Dizi" if kind == "series" else "Film"))
    preview = None
    if not dry_run and use_preview:
        cand = load_preview(scope)
        if preview_usable(cand, kind):
            preview = cand
    if not sstate.activity_start(JOB_SITE, JOB_KIND, trigger):
        raise StartError(409, "already_running", "Bir TMDB işi zaten çalışıyor")
    label = ("TMDB sezon ve bölüm görselleri (dizi)" if scope == "seasons"
             else "TMDB zenginleştirme (%s)" % KIND_LABEL[kind])
    sstate.activity_update(JOB_SITE, JOB_KIND, label=label, media_type=kind, scope=scope, dry_run=bool(dry_run),
                           from_preview=preview is not None, done=0, total=None, phase="lookup")
    return {"kind": kind, "scope": scope, "dry_run": bool(dry_run), "limit": limit or 0, "force": bool(force),
            "trigger": trigger, "preview": preview, "label": label}


def _refresh_catalogue() -> None:
    from .. import cache
    cache.refresh_and_prewarm()


def execute(job: dict[str, Any]) -> dict[str, Any]:
    """Run a reserved job to completion (blocking). Never raises; records the ``tmdb`` event."""
    kind, dry_run, label = job["kind"], job["dry_run"], job["label"]
    scope = job.get("scope", "items")
    started_at, t0 = sstate._now(), time.monotonic()
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    refresh_error: Optional[str] = None
    try:
        def on_progress(info: dict) -> None:
            sstate.activity_update(JOB_SITE, JOB_KIND, done=info["done"], total=info["total"],
                                   label="%s %d/%d" % (label, info["done"], info["total"]))

        def on_start(total: int, todo: int) -> None:
            sstate.activity_update(JOB_SITE, JOB_KIND, total=todo, done=0,
                                   label="%s 0/%d" % (label, todo))

        if scope == "seasons":
            result = seasons.backfill(dry_run=dry_run, limit=job["limit"], force=job["force"],
                                      on_progress=on_progress, on_start=on_start, collect=dry_run,
                                      preview=job["preview"])
        else:
            result = enrich.backfill(kind, dry_run=dry_run, limit=job["limit"], force=job["force"],
                                     on_progress=on_progress, on_start=on_start, collect=dry_run,
                                     preview=job["preview"])
        if dry_run:
            now = time.time()
            save_preview({
                "version": 1, "kind": kind, "scope": scope, "created_at": now,
                "created_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "limit": job["limit"] or None, "force": job["force"],
                "total_in_library": result["total"], "counters": result["counters"],
                "aborted": result["aborted"], "items": result["items"],
            })
        elif job["preview"] is not None:
            _mark_applied(kind, job["preview"].get("created_at"), result["written"], scope)
        if not dry_run and result["written"]:
            sstate.activity_update(JOB_SITE, JOB_KIND, phase="refresh", label="%s: katalog yenileniyor" % label)
            try:
                _refresh_catalogue()
            except Exception as exc:  # the write succeeded; the periodic refresh will catch up
                log.exception("catalogue refresh after tmdb backfill failed")
                refresh_error = type(exc).__name__
    except Exception as exc:  # pragma: no cover - defensive: a job must never kill the worker thread
        log.exception("tmdb backfill failed")
        error = ("%s: %s" % (type(exc).__name__, exc))[:300]
    finally:
        sstate.activity_end(JOB_SITE, JOB_KIND)
    counters = dict((result or {}).get("counters") or {})
    duration = round(time.monotonic() - t0, 2)
    status = "error" if error else "partial" if (result or {}).get("aborted") else "success"
    event = {
        "site": JOB_SITE, "started_at": started_at, "duration": duration, "trigger": job["trigger"],
        "status": status, "media_type": kind, "scope": scope, "dry_run": dry_run,
        "from_preview": bool((result or {}).get("from_preview")), "limit": job["limit"] or None,
        "force": job["force"], "lookup": (result or {}).get("lookup"), "total": (result or {}).get("total"),
        "written": (result or {}).get("written", 0),
        "matched": counters.get("matched", 0), "unmatched": counters.get("unmatched", 0),
        "review": counters.get("review", 0), "skipped": counters.get("skipped", 0),
        "deferred": counters.get("deferred", 0), "errors": counters.get("errors", 0),
        "seconds": counters.get("seconds", duration), "aborted": (result or {}).get("aborted"),
        "error": error or (("catalog refresh: " + refresh_error) if refresh_error else None),
    }
    if scope == "seasons":  # season/episode job: title-match counters are not applicable (kept 0 for feed consumers)
        event.update({k: counters.get(k, 0) for k in ("series", "seasons", "episodes", "posters", "stills", "empty")})
        event.update(matched=0, review=0, unmatched=0)
    sstate.record_ops_tmdb(event)
    return event


def run_now(kind: str, dry_run: bool, limit: Optional[int] = None, force: bool = False,
            use_preview: bool = True, trigger: str = "manual", scope: str = "items") -> dict[str, Any]:
    """``begin`` + ``execute`` in the calling thread (tests / scripts)."""
    return execute(begin(kind, dry_run, limit, force, use_preview, trigger, scope))
