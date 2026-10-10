"""TMDB enrichment of the canonical library.

Policy: TMDB owns poster/backdrop (separate ``tmdb_*`` columns; the source images stay as fallback). Other TMDB fields only
fill gaps in the source data. Failures never propagate; attempts are stamped so unmatched titles are not re-queried before
``TMDB_RETRY_DAYS`` pass.

No background thread: a write queues ``jobs kind=tmdb`` through ``hooks.enqueue_tmdb``; the control layer runs
:func:`run_job` for it. Settings (``tmdb_auto``, ``tmdb_types``) come from the ``settings`` table, read live.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Optional

from ... import db, settings
from . import identity, tmdb
from .textnorm import is_placeholder_image

log = logging.getLogger("catalog.enrich")
ERROR_BREAKER = 3          # consecutive TMDB errors before the batch gives up
TMDB_RETRY_DAYS = 7.0
TMDB_CONCURRENCY = 4
TMDB_BUDGET_SECONDS = 90.0

FILL_FIELDS = ("overview", "original_title", "genres", "rating", "runtime")


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != []


def new_counters() -> dict[str, Any]:
    return {"matched": 0, "unmatched": 0, "review": 0, "skipped": 0,
            "deferred": 0, "errors": 0, "seconds": 0.0}


def _types() -> list[str]:
    value = settings.get("tmdb_types")
    return list(value) if isinstance(value, (list, tuple)) else ["movie", "series"]


def auto_enabled() -> bool:
    """Automatic enrichment: a TMDB key is configured AND the admin's ``tmdb_auto`` switch is on (read live)."""
    return tmdb.enabled() and bool(settings.get("tmdb_auto"))


def type_enabled(media_type: Optional[str], types: Optional[Iterable[str]] = None) -> bool:
    """Key present and ``media_type`` selected (admin ``tmdb_types``; live)."""
    if not tmdb.enabled():
        return False
    return (media_type or "movie") in (_types() if types is None else types)


def should_run(media_type: Optional[str], state: Optional[dict], force: bool = False,
               now: Optional[float] = None, types: Optional[Iterable[str]] = None) -> bool:
    """False when disabled, already matched, or attempted inside the retry window."""
    if not type_enabled(media_type, types):
        return False
    if force:
        return True
    state = state or {}
    if state.get("status") == "matched":
        return False
    checked = state.get("checked_at")
    if checked and (now or time.time()) - checked < TMDB_RETRY_DAYS * 86400:
        return False
    return True


def needs_lookup(cid: str) -> bool:
    """Should a write queue a TMDB job for ``cid``? Automatic enrichment on, its type enabled, not matched, outside the
    retry window."""
    if not auto_enabled():
        return False
    r = db.query_one("SELECT type,tmdb_enrich_status,tmdb_checked_at FROM library_items WHERE id=?", (cid,))
    if r is None:
        return False
    return should_run(r["type"], {"status": r["tmdb_enrich_status"], "checked_at": r["tmdb_checked_at"]})


def run_batch(jobs: dict[str, dict], budget: Optional[float] = None,
              workers: Optional[int] = None) -> tuple[dict[str, dict], dict[str, Any]]:
    """Look up ``jobs`` (key -> find() kwargs) with bounded concurrency and time.

    Returns ``(results, counters)``; keys that ran out of budget (or hit the error breaker) are absent from ``results``
    and counted as deferred."""
    counters = new_counters()
    results: dict[str, dict] = {}
    if not jobs:
        return results, counters
    budget = TMDB_BUDGET_SECONDS if budget is None else budget
    deadline = time.monotonic() + budget
    started = time.monotonic()
    lock = threading.Lock()
    streak = [0]

    def work(key: str, kwargs: dict) -> None:
        if time.monotonic() >= deadline or streak[0] >= ERROR_BREAKER:
            return
        try:
            res = tmdb.find(**kwargs)
        except Exception as exc:  # defensive: enrichment must never raise
            res = {"status": "error", "error": type(exc).__name__}
        with lock:
            streak[0] = streak[0] + 1 if res.get("status") == "error" else 0
            results[key] = res

    with ThreadPoolExecutor(max_workers=max(1, workers or TMDB_CONCURRENCY)) as pool:
        for key, kwargs in jobs.items():
            pool.submit(work, key, kwargs)
    for key in jobs:
        res = results.get(key)
        if res is None:
            counters["deferred"] += 1
        elif res.get("status") == "error":
            counters["errors"] += 1
        elif res.get("status") in counters:
            counters[res["status"]] += 1
    counters["seconds"] = round(time.monotonic() - started, 2)
    return results, counters


def fill_norm(norm: dict, data: dict) -> None:
    """Apply TMDB identity + gap-filling to a normalized source item (in place). Never touches the source's
    poster_url/backdrop_url and never overwrites a field the source already provides."""
    norm["tmdb_id"] = data.get("tmdb_id")
    if data.get("imdb_id"):
        norm["imdb_id"] = data["imdb_id"]
    for k in FILL_FIELDS:
        if not _filled(norm.get(k)) and _filled(data.get(k)):
            norm[k] = data[k]


def apply_result(conn, cid: str, site: str, key: str, res: dict, now: Optional[int] = None) -> None:
    """Persist the outcome for canonical ``cid`` (call after merge_canonical)."""
    now = int(now or time.time())
    status = res.get("status")
    if status == "matched":
        data = res.get("data") or {}
        conn.execute(
            "UPDATE library_items SET tmdb_poster_url=?, tmdb_backdrop_url=?, "
            "tmdb_enrich_status='matched', tmdb_checked_at=? WHERE id=?",
            (data.get("poster_url"), data.get("backdrop_url"), now, cid))
    elif status in ("review", "unmatched"):
        conn.execute("UPDATE library_items SET tmdb_enrich_status=?, tmdb_checked_at=? WHERE id=?", (status, now, cid))
        if status == "review":
            row = conn.execute("SELECT reason FROM identity_reviews WHERE source=? AND source_key=?", (site, key)).fetchone()
            if not row or row["reason"] in ("missing_external_id", "tmdb_low_confidence"):
                identity.review(conn, site, key, cid, "tmdb_low_confidence", res.get("candidates") or [])


def persist_match(conn, cid: str, res: dict) -> Optional[str]:
    """Bind identity, fill gaps in every source row, store artwork. Returns the final id (``None`` when the row vanished or
    the identity conflicts)."""
    from .write import merge_canonical  # local: write imports this module
    if not conn.execute("SELECT 1 FROM library_items WHERE id=?", (cid,)).fetchone():
        return None  # merged away / removed since the lookup
    data = res["data"]
    try:
        cid = identity.bind(conn, cid, data.get("tmdb_id"), data.get("imdb_id"))
    except ValueError:
        identity.review(conn, "tmdb", cid, cid, "conflicting_identities", [data.get("tmdb_id")])
        apply_result(conn, cid, "tmdb", cid, {"status": "unmatched"})
        return None
    for row in conn.execute("SELECT site_id,key,normalized FROM source_items WHERE canonical_id=?", (cid,)).fetchall():
        try:
            norm = json.loads(row["normalized"] or "{}")
        except ValueError:
            norm = {}
        fill_norm(norm, data)
        conn.execute("UPDATE source_items SET normalized=? WHERE site_id=? AND key=?",
                     (json.dumps(norm, ensure_ascii=False), row["site_id"], row["key"]))
    merge_canonical(conn, cid)
    apply_result(conn, cid, "tmdb", cid, res)
    return cid


def _state_of(conn, cid: str) -> Optional[dict]:
    r = conn.execute("SELECT tmdb_enrich_status s, tmdb_id t FROM library_items WHERE id=?", (cid,)).fetchone()
    return None if r is None else {"status": r["s"], "tmdb_id": r["t"]}


def _write_results(results: dict[str, dict], force: bool, counters: dict[str, Any]) -> int:
    """Persist ``results`` (cid -> find() result) in one transaction. Returns titles written. A row that is gone, or already
    matched meanwhile (unless ``force``), is skipped."""
    written = 0
    with db.transaction() as conn:
        for cid, res in results.items():
            status = res.get("status")
            cur = _state_of(conn, cid)
            if cur is None or (not force and cur["status"] == "matched"):
                counters["skipped"] += 1
                continue
            if status == "matched":
                if persist_match(conn, cid, res) is not None:
                    written += 1
            elif status in ("review", "unmatched"):
                apply_result(conn, cid, "tmdb", cid, res)
    return written


def _jobs_for(cids: Iterable[str], force: bool, counters: dict[str, Any]) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    for cid in dict.fromkeys(cids):
        r = db.query_one("SELECT id,type,title,original_title,year,tmdb_id,imdb_id,tmdb_enrich_status,tmdb_checked_at "
                         "FROM library_items WHERE id=?", (cid,))
        if r is None:
            continue
        if not should_run(r["type"], {"status": r["tmdb_enrich_status"], "checked_at": r["tmdb_checked_at"]}, force=force):
            counters["skipped"] += 1
            continue
        jobs[cid] = dict(title=r["title"], year=r["year"], original_title=r["original_title"],
                         media_type=r["type"] or "movie", tmdb_id=r["tmdb_id"], imdb_id=r["imdb_id"])
    return jobs


def enrich_items(cids: Iterable[str], budget: Optional[float] = None, force: bool = False) -> dict[str, Any]:
    """TMDB-enrich canonical titles: poster/backdrop/ids/gap fields + (series) the season data. Obeys ``tmdb_auto`` +
    ``tmdb_types`` + the retry window. Blocking and network-bound (a job handler). Returns counters (+ ``written``,
    ``ids``: the final ids written); never raises."""
    counters = new_counters()
    counters["written"] = 0
    started = time.monotonic()
    try:
        if not auto_enabled():
            return counters
        jobs = _jobs_for(cids, force, counters)
        results, c = run_batch(jobs, budget=budget)
        for k in ("matched", "unmatched", "review", "deferred", "errors"):
            counters[k] += c[k]
        errs = [str(r.get("error") or "") for r in results.values() if r.get("status") == "error"]
        if errs:   # short error code (e.g. "HTTP 401", "http_429"; never the key): the job handler decides on retry
            counters["error"] = errs[0]
        if results:
            counters["written"] = _write_results(results, force, counters)
        series = [cid for cid, j in jobs.items() if j["media_type"] == "series"] if counters["written"] else []
        if series:   # the title just got its TMDB id: its seasons/episodes can be filled right away
            from . import seasons
            series = [(db.query_one("SELECT canonical_id FROM catalogue_aliases WHERE alias=?", (cid,)) or [cid])[0]
                      for cid in series]
            seasons.auto_enrich(series, budget=budget)
    except Exception as exc:  # TMDB / storage trouble must never break the caller
        log.warning("title enrichment skipped: %s", type(exc).__name__)
        counters["errors"] += 1
    counters["seconds"] = round(time.monotonic() - started, 2)
    return counters


def run_job(canonical_id: str) -> dict[str, Any]:
    """Handler body of a ``jobs kind=tmdb`` job (payload ``{"canonical_id": ...}``): enrich, then refresh the snapshot soon."""
    res = enrich_items([canonical_id])
    if res.get("written"):
        from . import cache
        cache.refresh_soon()
    return res


# --- backfill (admin job / CLI) ------------------------------------------------------------------------------------

def select_todo(media_type: str, limit: int = 0, force: bool = False):
    """Library rows of ``media_type`` still needing a lookup -> ``(rows, total_in_library)``. A title whose only poster is a
    site "no picture" file and that TMDB has no art for counts as missing (looked up again even inside the retry window)."""
    from .write import shared_art_urls
    rows = db.query("SELECT id,type,title,original_title,year,tmdb_id,imdb_id,tmdb_enrich_status,"
                    "tmdb_checked_at,poster_url,tmdb_poster_url FROM library_items WHERE type=? "
                    "ORDER BY added_at DESC,id", (media_type,))
    types = _types()
    with db.read_transaction() as conn:
        shared = shared_art_urls(conn)
    out = []
    for r in rows:
        state = {"status": r["tmdb_enrich_status"], "checked_at": r["tmdb_checked_at"]}
        no_art = not r["tmdb_poster_url"] and bool(r["poster_url"]) and is_placeholder_image(r["poster_url"], shared)
        if should_run(media_type, state, force=force, types=types) or (
                no_art and r["tmdb_enrich_status"] != "matched" and type_enabled(media_type, types)):
            out.append(r)
        if limit and len(out) >= limit:
            break
    return out, len(rows)


def backfill(kind: str, dry_run: bool = False, limit: int = 0,
             on_progress: Optional[Callable[[dict], None]] = None, *, force: bool = False,
             chunk: int = 25, budget: float = 3600) -> dict[str, Any]:
    """TMDB-enrich the existing library for one media type (``movie`` | ``series``): pick titles that still need a lookup,
    look them up in chunks (bounded concurrency), write each chunk in one transaction. ``dry_run``: look up, write nothing.
    Returns ``{kind, dry_run, total, lookup, counters, aborted, written}``."""
    todo, total = select_todo(kind, limit, force)
    counters = new_counters()
    started = time.monotonic()
    written = 0
    aborted: Optional[str] = None
    counters["skipped"] = total - len(todo)
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        jobs = {r["id"]: dict(title=r["title"], year=r["year"], original_title=r["original_title"],
                              media_type=kind, tmdb_id=r["tmdb_id"], imdb_id=r["imdb_id"]) for r in part}
        results, c = run_batch(jobs, budget=budget)
        for k in ("matched", "unmatched", "review", "deferred", "errors"):
            counters[k] += c[k]
        if not dry_run:
            written += _write_results(results, force, counters)
        if on_progress:
            on_progress({"done": min(i + chunk, len(todo)), "total": len(todo), "counters": counters})
        if c["errors"] and c["errors"] >= len(part):
            aborted = "tmdb_unreachable"
            break
    counters["seconds"] = round(time.monotonic() - started, 1)
    if written:
        from . import cache
        cache.refresh_soon()
    return {"kind": kind, "dry_run": dry_run, "total": total, "lookup": len(todo), "counters": counters,
            "aborted": aborted, "written": written}
