"""TMDB season posters + episode metadata/stills for the canonical library.

Storage (``db.py``): ``library_seasons`` (name, overview, air date, poster) and ``library_episodes`` (title,
overview, air date, runtime, still) keyed by canonical series id. They are separate from ``video_sources``
on purpose - a provider row is per site and per video, TMDB metadata is per (series, season, episode) and
must exist without any video. Source data is never overwritten: the catalogue/API merge at read time
(``merge_season`` / ``merge_episode``): the source value wins when it is non-empty, TMDB fills blanks; artwork
is TMDB first with the source image as fallback (same rule as posters).

Only seasons the library really has (``video_sources`` episode rows + the episode list inside
``source_items.normalized``) are requested - never every season TMDB knows. Budget / concurrency / timeout /
429 handling is the shared TMDB layer's (``tmdb._get``); TMDB failures never propagate:

* TMDB answered -> rows written, season stamped ``ok``.
* TMDB has nothing (404 / empty season) -> stamped ``empty``; asked again after ``TMDB_RETRY_DAYS``.
* network / rate limit / auth error -> nothing stamped (retried next run; a run of consecutive errors stops the batch).
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Mapping, Optional

from .. import config, db
from . import enrich, tmdb
from .normalize import is_placeholder_image

log = logging.getLogger("library.seasons")

SCOPE = "seasons"
DECISIONS = ("auto", "empty", "error", "deferred")


def season_id(cid: str, season: int) -> str:
    """Public id of a season (used by ``/img/{id}/portrait``); mirrors the episode id ``{cid}:s{n}:e{m}``."""
    return f"{cid}:s{season}"


def new_counters() -> dict[str, Any]:
    return {"series": 0, "seasons": 0, "episodes": 0, "posters": 0, "stills": 0, "empty": 0,
            "errors": 0, "deferred": 0, "skipped": 0, "no_tmdb": 0, "seconds": 0.0}


# --- what the library has / what is still due ---------------------------------------------------

def library_episodes(conn, cid: str) -> dict[int, set[int]]:
    """``{season: {episode, ...}}`` the library really holds for series ``cid``."""
    out: dict[int, set[int]] = {}
    for r in conn.execute("SELECT DISTINCT season, episode FROM video_sources WHERE canonical_id=? AND kind='episode' "
                          "AND season IS NOT NULL AND episode IS NOT NULL", (cid,)):
        out.setdefault(r[0], set()).add(r[1])
    for r in conn.execute("SELECT normalized FROM source_items WHERE canonical_id=?", (cid,)):
        try:
            norm = json.loads(r[0] or "{}")
        except (TypeError, ValueError):
            continue
        for e in norm.get("video_sources") or []:
            if not isinstance(e, dict) or e.get("kind") != "episode":
                continue
            s, n = e.get("season"), e.get("episode")
            if isinstance(s, int) and isinstance(n, int) and s >= 0 and n >= 1:
                out.setdefault(s, set()).add(n)
    return out


def due_seasons(conn, cid: str, force: bool = False, now: Optional[float] = None) -> list[int]:
    """Library seasons of ``cid`` that need a TMDB lookup: never asked, an ``empty`` answer older than
    ``TMDB_RETRY_DAYS``, or a stale ``ok`` season that lacks episodes the library now has."""
    have = library_episodes(conn, cid)
    if not have:
        return []
    stored = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT season, status, checked_at FROM library_seasons WHERE canonical_id=?", (cid,))}
    known: dict[int, set[int]] = {}
    for r in conn.execute("SELECT season, episode FROM library_episodes WHERE canonical_id=?", (cid,)):
        known.setdefault(r[0], set()).add(r[1])
    now = time.time() if now is None else now
    retry = config.TMDB_RETRY_DAYS * 86400
    due = []
    for s in sorted(have):
        row = stored.get(s)
        if force or row is None:
            due.append(s)
            continue
        status, checked = row
        if checked and now - checked < retry:
            continue
        if status != "ok" or not have[s] <= known.get(s, set()):
            due.append(s)
    return due


# --- lookups ---------------------------------------------------------------------------------------

def run_batch(jobs: Mapping[tuple[str, int], int], budget: Optional[float] = None,
              workers: Optional[int] = None) -> tuple[dict[tuple[str, int], dict], dict[str, Any]]:
    """Look up ``jobs`` ``{(cid, season): tmdb_id}`` with bounded concurrency and time.

    Returns ``(results, counters)``; jobs that ran out of budget (or hit the consecutive-error breaker)
    are absent from ``results`` and counted as ``deferred``."""
    counters = {"ok": 0, "empty": 0, "errors": 0, "deferred": 0, "seconds": 0.0}
    results: dict[tuple[str, int], dict] = {}
    if not jobs:
        return results, counters
    budget = config.TMDB_BUDGET_SECONDS if budget is None else budget
    deadline = time.monotonic() + budget
    started = time.monotonic()
    lock = threading.Lock()
    streak = [0]

    def work(key: tuple[str, int], tmdb_id: int) -> None:
        if time.monotonic() >= deadline or streak[0] >= enrich.ERROR_BREAKER:
            return
        try:
            res = tmdb.season(tmdb_id, key[1])
        except Exception as exc:  # defensive: enrichment must never raise
            res = {"status": "error", "error": type(exc).__name__}
        with lock:
            streak[0] = streak[0] + 1 if res.get("status") not in ("ok", "empty") else 0
            results[key] = res

    with ThreadPoolExecutor(max_workers=max(1, workers or config.TMDB_CONCURRENCY)) as pool:
        for key, tmdb_id in jobs.items():
            pool.submit(work, key, tmdb_id)
    for key in jobs:
        res = results.get(key)
        if res is None:
            counters["deferred"] += 1
        elif res.get("status") == "ok":
            counters["ok"] += 1
        elif res.get("status") == "empty":
            counters["empty"] += 1
        else:
            counters["errors"] += 1
    counters["seconds"] = round(time.monotonic() - started, 2)
    return results, counters


def tally(counters: dict[str, Any], results: Iterable[dict]) -> None:
    """Add what ``results`` contain (seasons/episodes/posters/stills; empty/errors) to ``counters``."""
    for res in results:
        st = res.get("status")
        if st == "ok":
            d = res["data"]
            counters["seasons"] += 1
            counters["episodes"] += len(d["episodes"])
            counters["posters"] += 1 if d.get("poster_url") else 0
            counters["stills"] += sum(1 for e in d["episodes"] if e.get("still_url"))
        elif st == "empty":
            counters["empty"] += 1
        else:
            counters["errors"] += 1


# --- writes ---------------------------------------------------------------------------------------

def persist(conn, cid: str, season_no: int, res: dict, now: Optional[float] = None,
            expect_tmdb_id: Optional[int] = None) -> bool:
    """Store one season lookup for series ``cid``; ``True`` when a row was written.

    Skipped when the series vanished or is bound to another TMDB id than the lookup used. TMDB values only
    replace older TMDB values (a blank never wipes a stored one); ``video_sources`` is never touched."""
    now = int(now or time.time())
    row = conn.execute("SELECT tmdb_id FROM library_items WHERE id=?", (cid,)).fetchone()
    if row is None or (expect_tmdb_id is not None and row[0] != expect_tmdb_id):
        return False
    status = res.get("status")
    if status == "ok":
        d = res["data"]
        conn.execute(
            "INSERT INTO library_seasons(canonical_id,season,name,overview,air_date,tmdb_poster_url,status,checked_at) "
            "VALUES (?,?,?,?,?,?, 'ok', ?) ON CONFLICT(canonical_id,season) DO UPDATE SET "
            "name=COALESCE(excluded.name,name), overview=COALESCE(excluded.overview,overview), "
            "air_date=COALESCE(excluded.air_date,air_date), "
            "tmdb_poster_url=COALESCE(excluded.tmdb_poster_url,tmdb_poster_url), "
            "status='ok', checked_at=excluded.checked_at",
            (cid, season_no, d.get("name"), d.get("overview"), d.get("air_date"), d.get("poster_url"), now))
        conn.executemany(
            "INSERT INTO library_episodes(canonical_id,season,episode,title,overview,air_date,runtime_minutes,"
            "tmdb_still_url,checked_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_id,season,episode) DO UPDATE SET "
            "title=COALESCE(excluded.title,title), overview=COALESCE(excluded.overview,overview), "
            "air_date=COALESCE(excluded.air_date,air_date), runtime_minutes=COALESCE(excluded.runtime_minutes,runtime_minutes), "
            "tmdb_still_url=COALESCE(excluded.tmdb_still_url,tmdb_still_url), checked_at=excluded.checked_at",
            [(cid, season_no, e["episode"], e.get("title"), e.get("overview"), e.get("air_date"),
              e.get("runtime"), e.get("still_url"), now) for e in d["episodes"]])
        return True
    if status == "empty":
        conn.execute(
            "INSERT INTO library_seasons(canonical_id,season,status,checked_at) VALUES (?,?, 'empty', ?) "
            "ON CONFLICT(canonical_id,season) DO UPDATE SET status='empty', checked_at=excluded.checked_at",
            (cid, season_no, now))
        return True
    return False  # error / deferred: not stamped, retried next time


def write_results(results: Mapping[tuple[str, int], dict], jobs: Mapping[tuple[str, int], int],
                  locked: bool = False) -> int:
    """Persist ``{(cid, season): result}`` in one transaction; returns seasons written.

    ``locked``: the caller already holds ``enrich.ingest_lock`` (ingest) - taking it again would deadlock."""
    if not results:
        return 0
    written = 0
    with (contextlib.nullcontext() if locked else enrich.ingest_lock()):
        conn = db.connect()
        try:
            for (cid, season_no), res in results.items():
                if persist(conn, cid, season_no, res, expect_tmdb_id=jobs.get((cid, season_no))):
                    written += 1
            conn.commit()
        finally:
            conn.close()
    return written


# --- automatic flow (ingest / opening a series) ---------------------------------------------------

def auto_ok() -> bool:
    """Ingest-time season enrichment: key present, the admin's ``tmdb_auto`` switch on AND ``series`` selected
    in ``tmdb_types`` (both read live)."""
    return enrich.auto_enabled() and enrich.type_enabled("series")


def enrich_series(cids: Iterable[str], budget: Optional[float] = None, force: bool = False,
                  locked: bool = False) -> dict[str, Any]:
    """Look up and store the due seasons of the given series (those bound to a TMDB id). Never raises."""
    counters = new_counters()
    started = time.monotonic()
    try:
        jobs: dict[tuple[str, int], int] = {}
        conn = db.connect()
        try:
            for cid in dict.fromkeys(cids):
                row = conn.execute("SELECT tmdb_id FROM library_items WHERE id=? AND type='series'", (cid,)).fetchone()
                if row is None or not row[0]:
                    counters["no_tmdb" if row is not None else "skipped"] += 1
                    continue
                due = due_seasons(conn, cid, force)
                if not due:
                    counters["skipped"] += 1
                    continue
                counters["series"] += 1
                for s in due:
                    jobs[(cid, s)] = int(row[0])
        finally:
            conn.close()
        results, c = run_batch(jobs, budget)
        counters["deferred"] += c["deferred"]
        write_results(results, jobs, locked)
        tally(counters, results.values())
    except Exception as exc:  # TMDB / storage trouble must never break ingest or a detail request
        log.warning("season enrichment skipped: %s", exc)
        counters["errors"] += 1
    counters["seconds"] = round(time.monotonic() - started, 2)
    return counters


def auto_enrich(cids: Iterable[str], budget: Optional[float] = None, locked: bool = False) -> dict[str, Any]:
    """``enrich_series`` when the admin settings allow it, else an empty result."""
    if not auto_ok():
        return new_counters()
    return enrich_series(cids, budget=budget, locked=locked)


# --- read side: merge TMDB values into the source's --------------------------------------------------

def load_tmdb() -> tuple[dict, dict]:
    """``({(cid, season): row}, {(cid, season, episode): row})`` for every stored TMDB season/episode."""
    try:
        seasons = {(r["canonical_id"], r["season"]): r for r in db.query("SELECT * FROM library_seasons")}
        episodes = {(r["canonical_id"], r["season"], r["episode"]): r for r in db.query("SELECT * FROM library_episodes")}
    except Exception:  # table missing (old DB not yet init()-ed): behave as "no TMDB data"
        return {}, {}
    return seasons, episodes


def placeholder_title(title: Optional[str]) -> bool:
    """Source episode titles that are only a label ("3. Bölüm") count as empty."""
    return tmdb.generic_episode_title(title)


def merge_episode(ep: dict, tm: Optional[Mapping]) -> dict:
    """Merge TMDB row ``tm`` into catalogue episode ``ep`` (in place): title/overview/runtime only when the
    source's is empty; air date from TMDB; ``still_remote`` = TMDB still, source still as fallback."""
    src_still = ep.get("still") or None
    if is_placeholder_image(src_still):
        src_still = None   # the source's "no picture" file is not a still
    ep["air_date"] = ep.get("air_date") or (tm["air_date"] if tm else None)
    ep["still_remote"] = (tm["tmdb_still_url"] if tm else None) or src_still
    if tm is not None:
        if placeholder_title(ep.get("title")) and tm["title"]:
            ep["title"] = tm["title"]
        if not ep.get("overview") and tm["overview"]:
            ep["overview"] = tm["overview"]
        if not ep.get("runtime") and tm["runtime_minutes"]:
            ep["runtime"] = tm["runtime_minutes"]
    return ep


def merge_season(season: dict, tm: Optional[Mapping]) -> dict:
    """Add ``name`` / ``overview`` / ``air_date`` / ``poster_url`` (remote TMDB poster or None) to a catalogue season."""
    name = tm["name"] if tm is not None and tm["name"] and not tmdb.generic_season_name(tm["name"]) else None
    season["name"] = name or season.get("title")
    season["overview"] = (tm["overview"] if tm is not None else None) or ""
    season["air_date"] = tm["air_date"] if tm is not None else None
    season["poster_url"] = tm["tmdb_poster_url"] if tm is not None else None
    return season


# --- admin: coverage / backfill -------------------------------------------------------------------

def coverage(conn) -> dict[str, int]:
    """Library-wide season/episode artwork coverage (episodes/seasons = those present in ``video_sources``)."""
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)
    try:
        return {
            "series": one("SELECT COUNT(DISTINCT canonical_id) FROM video_sources WHERE kind='episode'"),
            "series_tmdb": one("SELECT COUNT(DISTINCT v.canonical_id) FROM video_sources v JOIN library_items i "
                               "ON i.id=v.canonical_id WHERE v.kind='episode' AND i.tmdb_id IS NOT NULL"),
            "seasons": one("SELECT COUNT(*) FROM (SELECT DISTINCT canonical_id, season FROM video_sources "
                           "WHERE kind='episode' AND season IS NOT NULL)"),
            "seasons_checked": one("SELECT COUNT(*) FROM (SELECT DISTINCT v.canonical_id, v.season FROM video_sources v "
                                   "JOIN library_seasons s ON s.canonical_id=v.canonical_id AND s.season=v.season "
                                   "WHERE v.kind='episode' AND s.status='ok')"),
            "season_posters": one("SELECT COUNT(*) FROM (SELECT DISTINCT v.canonical_id, v.season FROM video_sources v "
                                  "JOIN library_seasons s ON s.canonical_id=v.canonical_id AND s.season=v.season "
                                  "WHERE v.kind='episode' AND s.tmdb_poster_url IS NOT NULL)"),
            "episodes": one("SELECT COUNT(*) FROM (SELECT DISTINCT canonical_id, season, episode FROM video_sources "
                            "WHERE kind='episode' AND season IS NOT NULL AND episode IS NOT NULL)"),
            "tmdb_stills": one("SELECT COUNT(*) FROM (SELECT DISTINCT v.canonical_id, v.season, v.episode FROM video_sources v "
                               "JOIN library_episodes e ON e.canonical_id=v.canonical_id AND e.season=v.season "
                               "AND e.episode=v.episode WHERE v.kind='episode' AND e.tmdb_still_url IS NOT NULL)"),
            "episode_stills": one("SELECT COUNT(*) FROM (SELECT DISTINCT v.canonical_id, v.season, v.episode FROM video_sources v "
                                  "LEFT JOIN library_episodes e ON e.canonical_id=v.canonical_id AND e.season=v.season "
                                  "AND e.episode=v.episode WHERE v.kind='episode' AND v.season IS NOT NULL "
                                  "AND (e.tmdb_still_url IS NOT NULL OR (v.episode_still_url IS NOT NULL AND v.episode_still_url!='')))"),
        }
    except Exception:  # tables missing: nothing to report
        return {k: 0 for k in ("series", "series_tmdb", "seasons", "seasons_checked", "season_posters",
                               "episodes", "tmdb_stills", "episode_stills")}


def season_strip(conn, cid: str) -> list[dict[str, Any]]:
    """Per-season summary for the admin library detail: episodes in the library, TMDB poster / stills."""
    have = library_episodes(conn, cid)
    rows = {r[0]: r for r in conn.execute("SELECT season, name, tmdb_poster_url, status FROM library_seasons WHERE canonical_id=?", (cid,))}
    stills: dict[int, int] = {}
    for r in conn.execute("SELECT season, episode FROM library_episodes WHERE canonical_id=? AND tmdb_still_url IS NOT NULL", (cid,)):
        if r[1] in have.get(r[0], ()):
            stills[r[0]] = stills.get(r[0], 0) + 1
    out = []
    for s in sorted(have):
        r = rows.get(s)
        out.append({"season": s, "id": season_id(cid, s), "name": (r["name"] if r and r["name"] else None),
                    "has_poster": bool(r and r["tmdb_poster_url"]), "episodes": len(have[s]),
                    "stills": stills.get(s, 0), "checked": bool(r and r["status"] == "ok")})
    return out


def _thumb(url: Optional[str], size: str = "w185") -> Optional[str]:
    """TMDB image url -> small variant for the admin preview."""
    if not url or "/t/p/" not in url:
        return url
    head, _, tail = url.partition("/t/p/")
    return head + "/t/p/" + size + "/" + tail.split("/", 1)[-1]


def select_todo(conn, limit: int = 0, force: bool = False) -> tuple[list[dict], int, int]:
    """Series with a TMDB id and due seasons -> ``(todo, series_in_library, series_without_tmdb)``."""
    rows = conn.execute("SELECT id,title,year,tmdb_id FROM library_items WHERE type='series' "
                        "ORDER BY added_at DESC,id").fetchall()
    todo: list[dict] = []
    no_tmdb = 0
    for r in rows:
        if not r["tmdb_id"]:
            no_tmdb += 1
            continue
        due = due_seasons(conn, r["id"], force)
        if not due:
            continue
        have = library_episodes(conn, r["id"])
        todo.append({"id": r["id"], "title": r["title"], "year": r["year"], "tmdb_id": int(r["tmdb_id"]),
                     "seasons": due, "library": {s: len(have.get(s, ())) for s in due}})
        if limit and len(todo) >= limit:
            break
    return todo, len(rows), no_tmdb


def preview_item(row: dict, results: Mapping[int, dict]) -> dict:
    """One series' lookups as stored in the preview file (``data``/``empty_seasons`` carry an apply payload)."""
    seasons, data, empty = [], [], []
    eps = posters = stills = errors = 0
    samples: list[dict] = []
    for s in row["seasons"]:
        res = results.get(s)
        st = (res or {}).get("status")
        if st == "ok":
            d = res["data"]
            n_still = sum(1 for e in d["episodes"] if e.get("still_url"))
            eps += len(d["episodes"])
            stills += n_still
            posters += 1 if d.get("poster_url") else 0
            seasons.append({"season": s, "status": "ok", "name": d.get("name"), "poster_url": _thumb(d.get("poster_url")),
                            "episodes": len(d["episodes"]), "in_library": row["library"].get(s, 0), "stills": n_still})
            data.append({"season": s, "data": d})
            for e in d["episodes"]:
                if e.get("still_url") and len(samples) < 3:
                    samples.append({"season": s, "episode": e["episode"], "title": e.get("title"),
                                    "still_url": _thumb(e["still_url"])})
        elif st == "empty":
            empty.append(s)
            seasons.append({"season": s, "status": "empty", "episodes": 0, "in_library": row["library"].get(s, 0), "stills": 0})
        elif st is None:
            seasons.append({"season": s, "status": "deferred", "episodes": 0, "in_library": row["library"].get(s, 0), "stills": 0})
        else:
            errors += 1
            seasons.append({"season": s, "status": "error", "episodes": 0, "in_library": row["library"].get(s, 0), "stills": 0})
    if data:
        decision = "auto"
        reason = "%d/%d sezon" % (len(data), len(row["seasons"]))
    elif errors:
        decision, reason = "error", "TMDB hatası"
    elif empty:
        decision, reason = "empty", "TMDB'de sezon verisi yok"
    else:
        decision, reason = "deferred", "budget exhausted"
    item: dict[str, Any] = {"id": row["id"], "title": row["title"], "year": row["year"], "tmdb_id": row["tmdb_id"],
                            "decision": decision, "reason": reason, "seasons": seasons,
                            "episodes_found": eps, "posters": posters, "stills": stills, "samples": samples}
    if data or empty:
        item["data"] = data
        item["empty_seasons"] = empty
    return item


def backfill(dry_run: bool = False, limit: int = 0, on_progress: Optional[Callable[[dict], None]] = None, *,
             force: bool = False, on_start: Optional[Callable[[int, int], None]] = None, collect: bool = False,
             preview: Optional[dict] = None, chunk: int = 10, budget: float = 3600) -> dict[str, Any]:
    """Season posters + episode metadata/stills for the existing library (admin job / CLI share this).

    Picks TMDB-matched series with due seasons (``select_todo``), looks their library seasons up in chunks of
    ``chunk`` series (lock-free, bounded concurrency), then writes each chunk under ``ingest_lock``.
    ``dry_run``: look up, write nothing. ``collect``: also return per-series ``items`` (preview rows).
    ``preview``: a previous dry-run result - applied WITHOUT new TMDB queries.
    ``on_start(series_in_library, todo)`` once, ``on_progress({done,total,counters,...})`` per chunk (unit: series).

    Returns ``{kind, scope, dry_run, total, lookup, counters, aborted, written, items, from_preview}``;
    ``written`` = seasons written."""
    counters = new_counters()
    started = time.monotonic()
    items: list[dict] = []
    written = 0
    aborted: Optional[str] = None

    if preview is not None:
        pitems = [i for i in preview.get("items") or [] if i.get("id") and i.get("decision") in ("auto", "empty")]
        conn = db.connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM library_items WHERE type='series'").fetchone()[0]
        finally:
            conn.close()
        if on_start:
            on_start(total, len(pitems))
        for start in range(0, len(pitems), chunk):
            part = pitems[start:start + chunk]
            results: dict[tuple[str, int], dict] = {}
            jobs: dict[tuple[str, int], int] = {}
            for it in part:
                counters["series"] += 1
                for d in it.get("data") or []:
                    results[(it["id"], d["season"])] = {"status": "ok", "data": d["data"]}
                    jobs[(it["id"], d["season"])] = it["tmdb_id"]
                for s in it.get("empty_seasons") or []:
                    results[(it["id"], s)] = {"status": "empty"}
                    jobs[(it["id"], s)] = it["tmdb_id"]
            written += write_results(results, jobs)
            tally(counters, results.values())
            if on_progress:
                on_progress({"done": min(start + chunk, len(pitems)), "total": len(pitems), "counters": counters})
        counters["seconds"] = round(time.monotonic() - started, 1)
        return {"kind": "series", "scope": SCOPE, "dry_run": False, "total": total, "lookup": len(pitems),
                "counters": counters, "aborted": None, "written": written, "items": items, "from_preview": True}

    conn = db.connect()
    try:
        todo, total, no_tmdb = select_todo(conn, limit, force)
    finally:
        conn.close()
    counters["no_tmdb"] = no_tmdb
    counters["skipped"] = total - no_tmdb - len(todo)
    if on_start:
        on_start(total, len(todo))
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        jobs = {}
        for row in part:
            for s in row["seasons"]:
                jobs[(row["id"], s)] = row["tmdb_id"]
        results, c = run_batch(jobs, budget=budget)
        counters["series"] += len(part)
        counters["deferred"] += c["deferred"]
        tally(counters, results.values())
        if collect:
            for row in part:
                items.append(preview_item(row, {s: results[(row["id"], s)] for s in row["seasons"] if (row["id"], s) in results}))
        if not dry_run:
            written += write_results(results, jobs)
        if on_progress:
            on_progress({"done": min(i + chunk, len(todo)), "total": len(todo), "counters": counters})
        if jobs and c["errors"] and c["errors"] >= len(jobs):
            aborted = "tmdb_unreachable"
            break
    counters["seconds"] = round(time.monotonic() - started, 1)
    return {"kind": "series", "scope": SCOPE, "dry_run": dry_run, "total": total, "lookup": len(todo),
            "counters": counters, "aborted": aborted, "written": written, "items": items, "from_preview": False}
