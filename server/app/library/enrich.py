"""TMDB enrichment orchestration for the canonical library.

Policy: TMDB owns poster/backdrop (stored in separate ``tmdb_*`` columns; the
source images stay as fallback). Other TMDB fields only fill gaps in the source
data. Failures never propagate; attempts are stamped so unmatched titles are not
re-queried before ``TMDB_RETRY_DAYS`` pass.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .. import config, db, settings
from . import identity, tmdb
from .normalize import is_placeholder_image

log = logging.getLogger("library.enrich")
ERROR_BREAKER = 3  # consecutive TMDB errors before the batch gives up

FILL_FIELDS = ("overview", "original_title", "genres", "rating", "runtime")


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != []


# --- placeholder artwork: URLs several different titles share ----------------------------------------------------

SHARED_ART_MIN = 3        # the same image URL on >= this many different titles = a site placeholder, not a poster
_SHARED_TTL = 60.0
_shared_cache: dict[str, tuple[float, frozenset]] = {}


def shared_art_urls(conn, force: bool = False) -> frozenset:
    """Poster/backdrop URLs that >= ``SHARED_ART_MIN`` different canonical titles carry in their SOURCE items (60 s cache per
    database). Counted on ``source_items.normalized`` (not on ``library_items``, which loses the URL once it is judged a
    placeholder). Never raises (an old SQLite without JSON1 = no heuristic)."""
    key = str(config.DB_PATH)
    hit = _shared_cache.get(key)
    now = time.monotonic()
    if hit and not force and now - hit[0] < _SHARED_TTL:
        return hit[1]
    urls: set[str] = set()
    try:
        for field in ("poster_url", "backdrop_url"):
            for r in conn.execute(
                    f"SELECT json_extract(normalized,'$.{field}') AS u FROM source_items "
                    f"WHERE json_extract(normalized,'$.{field}') LIKE 'http%' "
                    f"GROUP BY u HAVING COUNT(DISTINCT canonical_id) >= ?", (SHARED_ART_MIN,)):
                if r["u"]:
                    urls.add(r["u"])
    except Exception as exc:
        log.debug("shared art urls skipped: %s", exc)
    out = frozenset(urls)
    _shared_cache[key] = (now, out)
    return out


def new_counters() -> dict[str, Any]:
    return {"matched": 0, "unmatched": 0, "review": 0, "skipped": 0,
            "deferred": 0, "errors": 0, "seconds": 0.0}


def auto_enabled() -> bool:
    """Ingest-time enrichment: a TMDB key is configured AND the admin's ``tmdb_auto`` switch is on.

    Read live on every ingest (``app.settings``; ``.env`` only supplies defaults)."""
    return tmdb.enabled() and settings.tmdb_auto()


def type_enabled(media_type: Optional[str], types: Optional[Iterable[str]] = None) -> bool:
    """Key present and ``media_type`` selected (admin ``tmdb_types``; live). Applies to ingest AND backfill."""
    if not tmdb.enabled():
        return False
    return (media_type or "movie") in (settings.tmdb_types() if types is None else types)


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
    if checked and (now or time.time()) - checked < config.TMDB_RETRY_DAYS * 86400:
        return False
    return True


def run_batch(jobs: dict[str, dict], budget: Optional[float] = None,
              workers: Optional[int] = None) -> tuple[dict[str, dict], dict[str, Any]]:
    """Look up ``jobs`` (key -> find() kwargs) with bounded concurrency and time.

    Returns ``(results, counters)``; keys that ran out of budget (or hit the error
    breaker) are absent from ``results`` and counted as deferred.
    """
    counters = new_counters()
    results: dict[str, dict] = {}
    if not jobs:
        return results, counters
    budget = config.TMDB_BUDGET_SECONDS if budget is None else budget
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
            if res.get("status") == "error":
                streak[0] += 1
            else:
                streak[0] = 0
            results[key] = res

    with ThreadPoolExecutor(max_workers=max(1, workers or config.TMDB_CONCURRENCY)) as pool:
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
    """Apply TMDB identity + gap-filling to a normalized source item (in place).

    Never touches poster_url/backdrop_url of the source and never overwrites a
    field the source already provides."""
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
        conn.execute("UPDATE library_items SET tmdb_enrich_status=?, tmdb_checked_at=? WHERE id=?",
                     (status, now, cid))
        if status == "review":
            row = conn.execute("SELECT reason FROM identity_reviews WHERE source=? AND source_key=?",
                               (site, key)).fetchone()
            if not row or row["reason"] in ("missing_external_id", "tmdb_low_confidence"):
                identity.review(conn, site, key, cid, "tmdb_low_confidence", res.get("candidates") or [])


def load_state(conn, cid: Optional[str]) -> dict:
    if not cid:
        return {}
    row = conn.execute("SELECT tmdb_enrich_status, tmdb_checked_at, tmdb_poster_url FROM library_items WHERE id=?",
                       (cid,)).fetchone()
    if not row:
        return {}
    return {"status": row["tmdb_enrich_status"], "checked_at": row["tmdb_checked_at"],
            "has_art": bool(row["tmdb_poster_url"])}


# --- backfill (admin job + CLI share this) -----------------------------------

DECISION = {"matched": "auto", "review": "review", "unmatched": "unmatched",
            "error": "error"}


@contextlib.contextmanager
def ingest_lock():
    """Cross-process/thread lock serialising ingests and backfill *writes*.

    ``ingest_source`` holds it for a whole scan; ``backfill`` only takes it around each
    chunk's DB writes (lookups run lock-free), so both can never rewrite the same
    canonical row at once."""
    lock_path = Path(config.DB_PATH).with_suffix(".ingest.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def select_todo(conn, media_type: str, limit: int = 0, force: bool = False):
    """Library rows of ``media_type`` still needing a lookup -> ``(rows, total_in_library)``.

    A title whose only poster is a site "no picture" file (``is_placeholder_image``) and that TMDB has no art for counts as
    missing: it is looked up again even inside the retry window (unless it is already matched). Once the sweep
    (``clean_placeholder_art``) emptied that poster the normal retry window applies again."""
    rows = conn.execute("SELECT id,type,title,original_title,year,tmdb_id,imdb_id,tmdb_enrich_status,"
                        "tmdb_checked_at,poster_url,tmdb_poster_url FROM library_items WHERE type=? "
                        "ORDER BY added_at DESC,id", (media_type,)).fetchall()
    types = settings.tmdb_types()
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


def clean_placeholder_art(conn) -> int:
    """Library rows whose poster/backdrop is a site "no picture" file are merged again (``merge_canonical`` drops it), so
    records written before the placeholder rule lose it. Returns rows rebuilt; caller holds the ingest lock + commits."""
    from .ingest import merge_canonical  # local: ingest imports this module
    shared = shared_art_urls(conn, force=True)
    n = 0
    for r in conn.execute("SELECT id,poster_url,backdrop_url FROM library_items").fetchall():
        if any(u and is_placeholder_image(u, shared) for u in (r["poster_url"], r["backdrop_url"])):
            merge_canonical(conn, r["id"])
            n += 1
    return n


# --- titles that arrive outside a scan (live search hit, opened detail): background enrichment -----------------

_pending: set[str] = set()
_pending_lock = threading.Lock()
_recent: dict[str, float] = {}
RECHECK_SECONDS = 300.0


def needs_lookup(cid: str) -> bool:
    """True when ``cid`` is in the library, its type is enabled for TMDB, and it is neither matched nor inside the retry window."""
    conn = db.connect()
    try:
        r = conn.execute("SELECT type,tmdb_enrich_status,tmdb_checked_at FROM library_items WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    if r is None:
        return False
    return should_run(r["type"], {"status": r["tmdb_enrich_status"], "checked_at": r["tmdb_checked_at"]})


def enrich_items(cids: Iterable[str], budget: Optional[float] = None, force: bool = False) -> dict[str, Any]:
    """TMDB-enrich canonical titles that were added/updated outside an ingest scan (live search hits, an opened detail):
    poster/backdrop/year/overview + (series) the season data. Obeys ``tmdb_auto`` + ``tmdb_types`` + the retry window. Blocking and
    network-bound: callers run it in a thread (``schedule``). Returns counters (+ ``written``); never raises."""
    counters = new_counters()
    counters["written"] = 0
    started = time.monotonic()
    try:
        if not auto_enabled():
            return counters
        jobs: dict[str, dict] = {}
        conn = db.connect()
        try:
            for cid in dict.fromkeys(cids):
                r = conn.execute("SELECT id,type,title,original_title,year,tmdb_id,imdb_id,tmdb_enrich_status,tmdb_checked_at "
                                 "FROM library_items WHERE id=?", (cid,)).fetchone()
                if r is None:
                    continue
                state = {"status": r["tmdb_enrich_status"], "checked_at": r["tmdb_checked_at"]}
                if not should_run(r["type"], state, force=force):
                    counters["skipped"] += 1
                    continue
                jobs[cid] = dict(title=r["title"], year=r["year"], original_title=r["original_title"],
                                 media_type=r["type"] or "movie", tmdb_id=r["tmdb_id"], imdb_id=r["imdb_id"])
        finally:
            conn.close()
        results, c = run_batch(jobs, budget=budget)
        for k in ("matched", "unmatched", "review", "deferred", "errors"):
            counters[k] += c[k]
        if results:
            counters["written"] = _write_results(results, force, counters, stale_check=False)
        series = [cid for cid, j in jobs.items() if j["media_type"] == "series"] if counters["written"] else []
        if series:   # the title just got its TMDB id: its seasons/episodes can be filled right away
            from . import seasons
            conn = db.connect()
            try:   # a title merged into an older one by its TMDB id keeps working through the alias
                series = [(conn.execute("SELECT canonical_id FROM catalogue_aliases WHERE alias=?", (cid,)).fetchone() or [cid])[0]
                          for cid in series]
            finally:
                conn.close()
            seasons.auto_enrich(series, budget=budget)
    except Exception as exc:  # TMDB / storage trouble must never break search or detail
        log.warning("title enrichment skipped: %s", exc)
        counters["errors"] += 1
    counters["seconds"] = round(time.monotonic() - started, 2)
    return counters


def schedule(cids: Iterable[str], on_done: Optional[Callable[[dict], None]] = None) -> bool:
    """Background ``enrich_items`` for the given titles (de-duplicated per title, each title re-checked at most every
    ``RECHECK_SECONDS``); a no-op while TMDB is off. ``on_done(counters)`` runs when something was written (e.g. catalogue refresh)."""
    if not auto_enabled():
        return False
    now = time.monotonic()
    todo: list[str] = []
    with _pending_lock:
        for cid in dict.fromkeys(cids):
            if cid in _pending or now - _recent.get(cid, -RECHECK_SECONDS) < RECHECK_SECONDS:
                continue
            _pending.add(cid)
            _recent[cid] = now
            todo.append(cid)
    if not todo:
        return False

    def run() -> None:
        try:
            res = enrich_items(todo)
            if res.get("written") and on_done:
                on_done(res)
        except Exception as exc:  # a background task never crashes noisily
            log.warning("background title enrichment failed: %s", exc)
        finally:
            with _pending_lock:
                _pending.difference_update(todo)

    threading.Thread(target=run, daemon=True, name="title-enrich").start()
    return True


def _cand_text(c: dict) -> str:
    return "%s (%s) tmdb=%s" % (c.get("title"), c.get("year") or "?", c.get("id"))


def verbose_line(row, res) -> str:
    """One line per title: ``[decision] source (year) -> candidate, score, reason``."""
    src = "%s (%s)" % (row["title"], row["year"] or "?")
    if res is None:
        return "  [deferred] %s | budget exhausted" % src
    status = res.get("status")
    if status == "error":
        return "  [error] %s | %s" % (src, res.get("error") or "tmdb error")
    cand = res.get("candidate") or (res.get("candidates") or [None])[0]
    if status == "matched" and not cand:
        target = "tmdb id (known)"
    else:
        target = _cand_text(cand) if cand else "-"
    extra = ""
    if cand and cand.get("original_title") and cand.get("original_title") != cand.get("title"):
        extra = " orig=%r" % cand["original_title"]
    score = res.get("score", cand.get("score") if cand else None)
    return "  [%s] %s -> %s score=%s%s | %s" % (
        DECISION.get(status, status), src, target,
        "%.3f" % score if isinstance(score, (int, float)) else "-", extra, res.get("reason") or "")


def persist_match(conn, cid: str, res: dict) -> Optional[str]:
    """Bind identity, fill gaps in every source row, store artwork. Returns the final id
    (``None`` when the row vanished or the identity conflicts)."""
    if not conn.execute("SELECT 1 FROM library_items WHERE id=?", (cid,)).fetchone():
        return None  # merged away / removed since the lookup
    data = res["data"]
    try:
        cid = identity.bind(conn, cid, data.get("tmdb_id"), data.get("imdb_id"))
    except ValueError:
        identity.review(conn, "tmdb_backfill", cid, cid, "conflicting_identities", [data.get("tmdb_id")])
        apply_result(conn, cid, "tmdb_backfill", cid, {"status": "unmatched"})
        return None
    from .ingest import merge_canonical  # local: ingest imports this module
    for row in conn.execute("SELECT source,source_key,normalized FROM source_items WHERE canonical_id=?", (cid,)).fetchall():
        try:
            norm = json.loads(row["normalized"] or "{}")
        except ValueError:
            norm = {}
        fill_norm(norm, data)
        conn.execute("UPDATE source_items SET normalized=? WHERE source=? AND source_key=?",
                     (json.dumps(norm, ensure_ascii=False), row["source"], row["source_key"]))
    merge_canonical(conn, cid)
    apply_result(conn, cid, "tmdb_backfill", cid, res)
    return cid


_TMDB_IMG = "/t/p/"


def _thumb(url: Optional[str]) -> Optional[str]:
    """TMDB image url -> tiny (w92) variant for the admin preview table."""
    if not url or _TMDB_IMG not in url:
        return url
    head, _, tail = url.partition(_TMDB_IMG)
    return head + _TMDB_IMG + "w92/" + tail.split("/", 1)[-1]


def _cand_view(c: Optional[dict]) -> Optional[dict]:
    if not c:
        return None
    return {"tmdb_id": c.get("id"), "title": c.get("title"), "year": c.get("year"),
            "original_title": c.get("original_title"), "score": c.get("score"),
            "poster_url": _thumb(c.get("poster_url"))}


def preview_item(row, res: Optional[dict]) -> dict:
    """One looked-up title as stored in the preview file (auto items carry ``data`` for a later apply)."""
    item: dict[str, Any] = {"id": row["id"], "title": row["title"], "year": row["year"],
                            "original_title": row["original_title"]}
    if res is None:
        return {**item, "decision": "deferred", "reason": "budget exhausted", "score": None,
                "candidate": None, "alternatives": []}
    status = res.get("status")
    cands = [_cand_view(c) for c in (res.get("candidates") or [])]
    best = res.get("candidate")
    data = res.get("data") or {}
    if status == "matched":
        cand = _cand_view(best) or {"tmdb_id": data.get("tmdb_id"), "title": None, "year": None,
                                    "original_title": data.get("original_title"), "score": res.get("score"),
                                    "poster_url": None}
        if data.get("poster_url"):
            cand["poster_url"] = _thumb(data["poster_url"])  # what would actually be stored
        item.update(decision="auto", candidate=cand, alternatives=[], data=data)
    elif status in ("review", "unmatched"):
        item.update(decision=status, candidate=cands[0] if cands else None, alternatives=cands[1:3])
    else:
        item.update(decision="error", candidate=None, alternatives=[])
    score = res.get("score")
    if score is None and cands:
        score = cands[0].get("score")
    item["score"] = score
    item["reason"] = res.get("reason") or res.get("error") or ""
    return item


def _state_of(conn, cid: str) -> Optional[dict]:
    r = conn.execute("SELECT tmdb_enrich_status s, tmdb_id t FROM library_items WHERE id=?", (cid,)).fetchone()
    return None if r is None else {"status": r["s"], "tmdb_id": r["t"]}


def _write_results(results: dict[str, dict], force: bool, counters: dict[str, Any],
                   stale_check: bool) -> int:
    """Persist ``results`` (cid -> find() result) under the ingest lock. Returns titles written.

    ``stale_check``: results may predate the DB state (applying an older preview), so a row that is
    gone, or already matched (unless ``force``), is skipped instead of overwritten."""
    written = 0
    with ingest_lock():
        conn = db.connect()
        try:
            for cid, res in results.items():
                status = res.get("status")
                if stale_check:
                    cur = _state_of(conn, cid)
                    if cur is None or (not force and (cur["status"] == "matched")):
                        counters["skipped"] += 1
                        if status in counters:
                            counters[status] -= 1
                        continue
                if status == "matched":
                    if persist_match(conn, cid, res) is not None:
                        written += 1
                elif status in ("review", "unmatched"):
                    apply_result(conn, cid, "tmdb_backfill", cid, res)
            conn.commit()
        finally:
            conn.close()
    return written


def backfill(kind: str, dry_run: bool = False, limit: int = 0,
             on_progress: Optional[Callable[[dict], None]] = None, *, force: bool = False,
             on_start: Optional[Callable[[int, int], None]] = None, collect: bool = False,
             preview: Optional[dict] = None, chunk: int = 25, budget: float = 3600) -> dict[str, Any]:
    """TMDB-enrich the existing library for one media type (``movie`` | ``series``).

    Shared by ``tools.tmdb_enrich`` (CLI) and the admin job. Steps: pick titles that still need
    a lookup (``select_todo``), look them up in chunks (lock-free, bounded concurrency), then write
    each chunk under ``ingest_lock``. Only ``auto`` (matched) decisions change item data; review /
    unmatched keep the existing bookkeeping (status stamp + identity review queue).

    ``dry_run``: look up, write nothing. ``collect``: also return per-title ``items`` (preview rows).
    ``preview``: a previous dry-run result (``items`` with ``data``) - applied WITHOUT new TMDB queries.
    ``on_start(total_in_library, todo)`` once, ``on_progress({done,total,counters,rows,results})`` per chunk.

    Returns ``{kind, dry_run, total, lookup, counters, aborted, written, items, from_preview}``.
    """
    conn = db.connect()
    try:
        if preview is not None:
            todo, total = None, conn.execute("SELECT COUNT(*) FROM library_items WHERE type=?", (kind,)).fetchone()[0]
        else:
            todo, total = select_todo(conn, kind, limit, force)
    finally:
        conn.close()

    counters = new_counters()
    items: list[dict] = []
    started = time.monotonic()
    written = 0
    aborted: Optional[str] = None

    if preview is not None:  # apply a stored preview: no TMDB traffic
        force = bool(preview.get("force"))
        pitems = [i for i in preview.get("items") or [] if i.get("id")]
        counters["skipped"] = max(0, total - len(pitems))
        if on_start:
            on_start(total, len(pitems))
        for start in range(0, len(pitems), chunk):
            part = pitems[start:start + chunk]
            results: dict[str, dict] = {}
            for it in part:
                d = it.get("decision")
                if d == "auto" and it.get("data"):
                    results[it["id"]] = {"status": "matched", "data": it["data"], "score": it.get("score"),
                                         "reason": it.get("reason")}
                    counters["matched"] += 1
                elif d in ("review", "unmatched"):
                    cands = [{"id": c["tmdb_id"], "title": c.get("title"), "year": c.get("year"),
                              "score": c.get("score")} for c in ([it.get("candidate")] + list(it.get("alternatives") or []))
                             if c and c.get("tmdb_id")]
                    results[it["id"]] = {"status": d, "candidates": cands, "reason": it.get("reason")}
                    counters[d] += 1
                elif d == "error":
                    counters["errors"] += 1
                else:
                    counters["deferred"] += 1
            written += _write_results(results, force, counters, stale_check=True) if results else 0
            if on_progress:
                on_progress({"done": min(start + chunk, len(pitems)), "total": len(pitems),
                             "counters": counters, "rows": part, "results": results})
        counters["seconds"] = round(time.monotonic() - started, 1)
        return {"kind": kind, "dry_run": False, "total": total, "lookup": len(pitems), "counters": counters,
                "aborted": None, "written": written, "items": items, "from_preview": True}

    counters["skipped"] = total - len(todo)
    if on_start:
        on_start(total, len(todo))
    if not dry_run:   # rows that kept a site "no picture" file from before the placeholder rule: rebuilt without it
        with ingest_lock():
            conn = db.connect()
            try:
                if clean_placeholder_art(conn):
                    conn.commit()
            finally:
                conn.close()
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        jobs = {r["id"]: dict(title=r["title"], year=r["year"], original_title=r["original_title"],
                              media_type=kind, tmdb_id=r["tmdb_id"], imdb_id=r["imdb_id"]) for r in part}
        results, c = run_batch(jobs, budget=budget)
        for k in ("matched", "unmatched", "review", "deferred", "errors"):
            counters[k] += c[k]
        if collect:
            items.extend(preview_item(r, results.get(r["id"])) for r in part)
        if not dry_run:
            written += _write_results(results, force, counters, stale_check=False)
        if on_progress:
            on_progress({"done": min(i + chunk, len(todo)), "total": len(todo), "counters": counters,
                         "rows": part, "results": results})
        if c["errors"] and c["errors"] >= len(part):
            aborted = "tmdb_unreachable"
            break
    counters["seconds"] = round(time.monotonic() - started, 1)
    return {"kind": kind, "dry_run": dry_run, "total": total, "lookup": len(todo), "counters": counters,
            "aborted": aborted, "written": written, "items": items, "from_preview": False}
