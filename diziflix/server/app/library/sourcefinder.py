"""Source finder: a play request that ends without a playable stream starts a background search for one.

``videos.streams()`` calls :func:`request` when a full video (not a trailer) yields no stream (no source row, every source
failed, or all of them are marked broken). :func:`request` is cheap (a few DB reads, one insert, a daemon thread) and never
raises, so the play answer is not delayed; it only reports ``{"state": ...}`` for the optional ``finder`` field of the
``/api/streams`` answer. The job (one per title + episode at a time) runs three steps, each recorded as
``{"name", "ok", "ms", "note"}`` (``ok`` = this step produced a playable stream) and the first one that finds a stream ends it:

1. ``retry``  - every page source of the episode (broken ones too) is resolved again BY FORCE (``videos.resolve_source(row,
   force=True)``); a source that resolves is usable again (``broken`` / ``suspect`` -> ``unknown``).
2. ``search`` - the title is searched on the other sites that can search and have no source of it yet
   (``SOURCEFINDER_MAX_SITES``, ``search_all.search_sites``); a hit that resolved to the SAME canonical id gets its episode
   inventory read (``series_crawl.run_stage(only=key)``, never the whole catalogue) and the sources of the wanted
   season/episode are resolved.
3. ``heal``   - only when a failed resolution shows candidates but no stream (or a video host no provider covers), the LLM
   account is healthy, heal is enabled, the site is not in a heal cooldown and the daily budget
   (``SOURCEFINDER_DAILY_BUDGET`` agent runs per 24 h) allows it: ``heal.heal_site_playback(site, evidence, trigger="finder")``;
   a repair that was applied makes the failed sources resolve again.

A stream that resolves but is refused by its host (``streamdiag.quick_check`` <= 3 s: HTTP 401/403, an error page, also for the server's
own probe; ``SOURCEFINDER_PROBE=0`` skips it, an unclear answer counts as found) is NOT found: the step note says "akış çözüldü ama
erişilemiyor (HTTP 403)", there is no notification, and the heal step gets ``stream_blocked`` evidence (also noted in the playback heal window).

A found source becomes a ``notifications`` row (kind ``source_found``) for the profile that asked and is already stored the
usual way (``video_sources`` row + resolved payload with ``valid_until``), so the next play answers from it. Every finished job
is a ``finder_jobs`` row (admin event ``kind=finder``, found or not); a job without result tells the user nothing
(``GET /api/source-finder/<id>`` says ``not_found``).

Limits: one job per (title, episode); the same (title, episode) is not started again within ``SOURCEFINDER_COOLDOWN`` seconds
after a finished job (found or not; the cooldown lives in ``finder_jobs``, it survives a restart); ``SOURCEFINDER_MAX_JOBS``
jobs at once; the search step of a title is not repeated by its other episodes within the cooldown. A step that raises is a
failed step, never a dead job.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .. import config as app_config, db
from . import streamdiag, videos

log = logging.getLogger("diziflix.sourcefinder")

STEPS = ("retry", "search", "heal")
MAX_STEPS = 6        # steps handed out by the status endpoint
NOTE_CHARS = 140
NOTIFY_LIMIT = 50    # notifications per GET
KIND_FOUND = "source_found"
METHODS = ("retry", "search", "heal")

_lock = threading.Lock()
_inflight: dict[tuple[str, str], dict] = {}   # (canonical id, episode id) -> {job_id, steps, started, ...} of a running job
_title_search: dict[str, float] = {}          # canonical id -> monotonic time of its last search step (shared by its episodes)
_heal_runs: list[float] = []                  # epoch seconds of the repair-agent runs this process started (daily budget)
_EPISODE_ID = re.compile(r"^(?P<cid>.+):s(?P<season>\d+):e(?P<episode>\d+)$")


def _now() -> int:
    return int(time.time())


def _short(value: Any, limit: int = NOTE_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit]


def reset() -> None:
    """Forget the in-memory bookkeeping (tests)."""
    with _lock:
        _inflight.clear()
        _title_search.clear()
        del _heal_runs[:]


def _start_background(name: str, fn) -> None:
    """Run ``fn`` in a daemon thread (tests replace this to run it inline or to collect it)."""
    threading.Thread(target=fn, daemon=True, name=name).start()


# --- the request ------------------------------------------------------------------------------------------------

def _state(state: str, *, started: bool = False, reason: str = "", job_id: Optional[int] = None) -> dict:
    out = {"state": state, "started": started, "reason": reason}
    if job_id is not None:
        out["job_id"] = job_id
    return out


def _episode_numbers(cid: str, episode_id: str) -> Optional[tuple[int, int]]:
    """``(season, episode)`` of an episode id of this title, else None."""
    match = _EPISODE_ID.match(episode_id)
    if match and match.group("cid") == cid:
        return int(match.group("season")), int(match.group("episode"))
    row = db.query_one("SELECT season,episode FROM video_sources WHERE canonical_id=? AND episode_id=? AND season IS NOT NULL LIMIT 1",
                       (cid, episode_id))
    return (int(row["season"]), int(row["episode"])) if row and row["episode"] is not None else None


def request(canonical_id: str, episode_id: str = "", profile_id: str = "", trigger: str = "play") -> dict:
    """Start a source-finder job for a title / episode that could not be played. Never raises, never blocks on the job.

    ``{"state": "searching" | "found" | "not_found" | "idle", "started": bool, "reason": str[, "job_id": int]}``: ``searching``
    = a job runs (started now or earlier), ``found`` / ``not_found`` = the last finished job of this episode is still inside
    the cooldown (no new job), ``idle`` = nothing was started (``reason``: disabled | busy | no_title | unknown_title |
    episode_required | unknown_episode | error)."""
    try:
        return _request(str(canonical_id or ""), str(episode_id or ""), str(profile_id or ""), str(trigger or "play"))
    except Exception as exc:   # the play answer must never fail because of this
        log.warning("source finder request failed: %s", exc)
        return _state("idle", reason="error")


def _request(cid: str, ep: str, profile_id: str, trigger: str) -> dict:
    if not app_config.SOURCEFINDER_ENABLED:
        return _state("idle", reason="disabled")
    if ep == cid:
        ep = ""
    if not cid:
        return _state("idle", reason="no_title")
    item = db.query_one("SELECT id,type,title FROM library_items WHERE id=?", (cid,))
    if not item:
        return _state("idle", reason="unknown_title")
    if item["type"] == "series":
        if not ep:
            return _state("idle", reason="episode_required")
        if not _episode_numbers(cid, ep):
            return _state("idle", reason="unknown_episode")
    key = (cid, ep)
    with _lock:
        running = _inflight.get(key)
        if running:
            if profile_id not in running["profiles"]:
                running["profiles"].append(profile_id)   # another profile hit the same dead end: it gets the notification too
            return _state("searching", reason="running", job_id=running["job_id"])
        cooldown = app_config.SOURCEFINDER_COOLDOWN
        last = db.query_one("SELECT state,finished_at FROM finder_jobs WHERE canonical_id=? AND episode_id=? "
                            "AND state IN ('found','not_found') ORDER BY id DESC LIMIT 1", (cid, ep))
        if cooldown > 0 and last and last["finished_at"] and _now() - int(last["finished_at"]) < cooldown:
            return _state(last["state"], reason="cooldown")
        if len(_inflight) >= app_config.SOURCEFINDER_MAX_JOBS:
            return _state("idle", reason="busy")
        db.execute("INSERT INTO finder_jobs(canonical_id,episode_id,profile_id,state,trigger,started_at) VALUES (?,?,?,?,?,?)",
                   (cid, ep, profile_id, "searching", trigger[:20], _now()))
        job_id = int(db.query_one("SELECT MAX(id) AS id FROM finder_jobs WHERE canonical_id=? AND episode_id=?", (cid, ep))["id"])
        job = {"job_id": job_id, "key": key, "cid": cid, "ep": ep, "profile": profile_id, "profiles": [profile_id], "title": item["title"],
               "type": item["type"], "steps": [], "started": time.monotonic(), "updated_at": _now()}
        numbers = _episode_numbers(cid, ep) if ep else None
        job["season"], job["episode"] = numbers if numbers else (None, None)
        _inflight[key] = job
    try:
        _start_background(f"finder-{job_id}", lambda: _run(job))
    except Exception as exc:   # the thread could not start: no job happened, leave no row (and no cooldown) behind
        log.warning("source finder thread did not start: %s", exc)
        with _lock:
            _inflight.pop(key, None)
        db.execute("DELETE FROM finder_jobs WHERE id=?", (job_id,))
        return _state("idle", reason="error")
    return _state("searching", started=True, job_id=job_id)


# --- the job ----------------------------------------------------------------------------------------------------

def _run(job: dict) -> None:
    """The job thread: the steps in order, the first one that finds a stream ends it. Never raises."""
    found: Optional[dict] = None
    error = ""
    try:
        for name in STEPS:
            started = time.monotonic()
            try:
                out = globals()["_step_" + name](job) or {}   # looked up by name: tests replace a step
            except Exception as exc:   # one step failing is a note, not the end of the job
                log.warning("source finder %s step of %s failed: %s", name, job["key"], exc)
                out = {"note": "hata: " + _short(f"{type(exc).__name__}: {exc}", 100)}
            step = {"name": name, "ok": bool(out.get("found")), "ms": int((time.monotonic() - started) * 1000),
                    "note": _short(out.get("note"))}
            with _lock:
                job["steps"].append(step)
                job["updated_at"] = _now()
            _save_steps(job)
            if out.get("found"):
                found = out
                break
    except Exception as exc:
        error = _short(exc)
        log.warning("source finder job %s failed: %s", job["key"], exc)
    finally:
        _finish(job, found=found, error=error)


def _save_steps(job: dict) -> None:
    try:
        db.execute("UPDATE finder_jobs SET steps=? WHERE id=?", (json.dumps(job["steps"][:MAX_STEPS], ensure_ascii=False), job["job_id"]))
    except Exception as exc:
        log.warning("source finder: steps not saved: %s", exc)


def _finish(job: dict, *, found: Optional[dict], error: str = "") -> None:
    """Close the job row, notify the profile of a found source, drop the single-flight entry. Never raises."""
    try:
        db.execute("UPDATE finder_jobs SET state=?,finished_at=?,steps=?,source_id=?,method=?,error=? WHERE id=?",
                   ("found" if found else "not_found", _now(), json.dumps(job["steps"][:MAX_STEPS], ensure_ascii=False),
                    found.get("source_id") if found else None, found.get("method") if found else None, error or None,
                    job["job_id"]))
    except Exception as exc:
        log.warning("source finder: job row not closed: %s", exc)
    if found:
        try:
            _notify(job, found)
        except Exception as exc:
            log.warning("source finder: notification not written: %s", exc)
    with _lock:
        _inflight.pop(job["key"], None)


def _notify(job: dict, found: dict) -> None:
    payload = {"title": job["title"], "season": job.get("season"), "episode": job.get("episode"),
               "site": found.get("site") or "", "method": found.get("method") or "", "source_id": found.get("source_id") or ""}
    if found.get("recipe"):
        payload["recipe"] = found["recipe"]
    with _lock:
        profiles = list(job.get("profiles") or [job["profile"]])
    for profile in profiles:
        db.execute("INSERT INTO notifications(profile_id,kind,canonical_id,episode_id,payload,created_at) VALUES (?,?,?,?,?,?)",
                   (profile, KIND_FOUND, job["cid"], job["ep"], json.dumps(payload, ensure_ascii=False), _now()))


# --- helpers shared by the steps --------------------------------------------------------------------------------

def _resolve_all(rows: list) -> list[tuple[Any, Optional[dict], Optional[Exception]]]:
    """``[(row, result, error)]``: every row resolved by force, the rows of one episode concurrently."""
    def one(row):
        try:
            return row, videos.resolve_source(row, force=True), None
        except Exception as exc:
            return row, None, exc
    workers = max(1, min(app_config.RESOLVE_SOURCES_PARALLEL, len(rows)))
    if workers <= 1:
        return [one(row) for row in rows]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="finder-resolve") as pool:
        return list(pool.map(one, rows))


def _playable(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("streams"))


def _mark_usable(row) -> None:
    """A source that resolves again is offered again (same rule as ``videos.retry``: a resolution is not proof of playback)."""
    db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL,last_checked_at=? "
               "WHERE id=? AND status IN ('broken','suspect')", (_now(), row["id"]))


def _failed(job: dict, row, error: Any, blocked: Optional[dict] = None, issue: Any = None) -> None:
    """Remember a failed resolution (evidence for the heal step). ``blocked`` = the ``stream_blocked`` event of a stream that
    resolved but is refused (:func:`_probe_streams`); ``issue`` = the ``playback_issues`` row of a stream that resolves but a client
    could not play (:func:`_unplayable`)."""
    with _lock:
        job.setdefault("failed", {})[row["id"]] = {"row": row, "error": _short(error, 200), **({"blocked": blocked} if blocked else {}),
                                                   **({"issue": dict(issue)} if issue is not None else {})}


def _unplayable(row, result: Any) -> Optional[tuple]:
    """``(issue row, note)`` when ``result`` is the very stream a client just failed to play (the source has an open ``playback_issues`` row
    and every resolved stream is the same file: same host + path), else None. A different / new stream, a Referer / proxy the server
    learned for the source (class ``proxy_learned``) or an issue without a recorded stream is NOT held back: resolving again is
    not proof of playback, but a changed stream is worth offering."""
    from . import playissues
    issue = playissues.open_issue(row["id"])
    if issue is None or not issue["stream_group"] or issue["issue_class"] == "proxy_learned":
        return None
    from .. import streamproxy
    groups = {streamproxy.group_of(s["url"]) for s in (result or {}).get("streams") or [] if isinstance(s, dict) and s.get("url")}
    if not groups or groups - {issue["stream_group"]}:
        return None
    return issue, f"akış çözüldü ama oynatılamıyor ({playissues.label(issue['issue_class'])})"


def _probe_streams(row, result: Any) -> Optional[dict]:
    """A quick look (<= ``streamdiag.QUICK_SECONDS``) at the stream a source just resolved to: the verdict of the server's own
    probe, or None = uncertain (no answer in time / nothing probeable / any error: the old behaviour, the stream counts as found).
    Tests replace this."""
    if not app_config.SOURCEFINDER_PROBE:
        return None
    return streamdiag.quick_check((result or {}).get("streams") or [], learned=bool(row["proxy_required"]) if "proxy_required" in row.keys() else False)


def _refused(row, result: Any) -> Optional[dict]:
    """The ``playheal`` stream_blocked event when the freshly resolved stream of ``row`` is refused (HTTP 401/403, an error page,
    also for the server's own probe), else None. The refusal is also noted in the playback heal window (the ``stream_blocked`` signal)."""
    verdict = _probe_streams(row, result)
    if verdict and verdict.get("learn"):   # a Referer / the proxy fixes it: keep the learning (source + stream host); the stream counts as found
        streamdiag.persist_learning(row["id"], verdict)
    if not verdict or verdict.get("code") not in streamdiag.HEAL_CODES or verdict.get("learn"):
        return None
    keys = row.keys()
    job = {"source_id": row["id"], "site": row["source"], "locator": row["locator"], "episode_id": row["episode_id"] or "",
           "source_kind": row["kind"], "resolver": row["resolver"] if "resolver" in keys else "page"}
    event = streamdiag.blocked_event(job, verdict)
    try:
        from ..scraper import playheal
        playheal.record_stream_blocked(row["source"], event)
    except Exception:
        pass
    return {**event, "code": verdict["code"], "http": verdict.get("http")}


def _http_label(event: dict) -> str:
    return f"HTTP {event['http']}" if event.get("http") else {"not_media": "video yerine hata sayfası"}.get(str(event.get("code")), "reddedildi")


def _resolve_found(job: dict, rows: list, method: str, label: str = "") -> dict:
    """Resolve ``rows`` by force: ``{"found": True, ...}`` for the first that plays, else a note. Failures are kept as evidence."""
    out = _resolve_all(rows)
    good = [(row, result) for row, result, exc in out if exc is None and _playable(result)]
    for row, result, exc in out:
        if exc is not None and videos.is_blocked(exc):
            continue   # a "not public" placeholder is no repair evidence: the heal step must not run for it (library/gate.py)
        if exc is not None or not _playable(result):
            _failed(job, row, exc or "akış yok")
    held = []
    for row, result in list(good):   # the stream a client just failed to play is not "found" again
        verdict = _unplayable(row, result)
        if verdict:
            good.remove((row, result))
            held.append((row, verdict[1]))
            _failed(job, row, verdict[1], issue=verdict[0])
    refused = []
    for row, result in list(good):   # resolving is not enough: a stream the host refuses (403) is no "found" source
        event = _refused(row, result)
        if event:
            good.remove((row, result))
            refused.append((row, event))
            _failed(job, row, f"akış çözüldü ama erişilemiyor ({_http_label(event)})", blocked=event)
    for row, _result in good:
        _mark_usable(row)
    if not good and refused:
        return {"note": f"{label}akış çözüldü ama erişilemiyor ({_http_label(refused[0][1])})".strip()}
    if not good and held:
        return {"note": f"{label}{held[0][1]}".strip()}
    if good:
        row = good[0][0]
        return {"found": True, "method": method, "site": row["source"], "source_id": row["id"],
                "note": f"{label}{len(good)}/{len(rows)} kaynak çözüldü".strip()}
    reason = next((_short(exc, 90) for _row, _result, exc in out if exc is not None), "akış yok")
    return {"note": f"{label}{len(rows)} kaynak denendi, akış yok ({reason})".strip()}


def _episode_rows(cid: str, ep: str, site: Optional[str] = None, only_page: bool = False) -> list:
    sql = ("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' AND status NOT IN ('disabled','blocked')"
           + (" AND resolver='page'" if only_page else "") + (" AND source=?" if site else "")
           + " ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 WHEN 'suspect' THEN 2 ELSE 3 END,source,id")
    return db.query(sql, (cid, ep) + ((site,) if site else ()))


# --- step 1: retry ----------------------------------------------------------------------------------------------

def _step_retry(job: dict) -> dict:
    rows = _episode_rows(job["cid"], job["ep"], only_page=True)
    if not rows:
        return {"note": "bu bölüm için sayfa kaynağı kaydı yok"}
    return _resolve_found(job, rows, "retry")


# --- step 2: search other sites ---------------------------------------------------------------------------------

def _searchable_sites() -> list[str]:
    from ..scraper import site_search
    fn = getattr(site_search, "search_sites", None)
    return list(fn()) if fn else list(getattr(site_search, "_REGISTRY", {}))


def _plays_video(site: str) -> bool:
    """A site that only offers trailers (``playback: trailer``) cannot give a full video."""
    from ..scraper import config as scfg
    try:
        return scfg.load_site(site).data.get("playback") != "trailer"
    except Exception:
        return False


def _search_sites(query: str, sites: list[str]) -> dict:
    from . import search_all
    return search_all.search_sites(query, sites=sites, limit=5)


def _hydrate(site: str, key: str) -> dict:
    """Read the episode inventory of ONE series page of ``site`` (the on-demand path of the series crawl, not a catalogue crawl)."""
    from ..scraper import config as scfg
    from . import series_crawl
    cfg = scfg.load_site(site)
    if not series_crawl.enabled(cfg):
        return {"skipped": "inventory not supported"}
    return series_crawl.run_stage(cfg, site, force=True, limit=1, only=key)


def _queries(job: dict) -> list[str]:
    row = db.query_one("SELECT title,original_title FROM library_items WHERE id=?", (job["cid"],))
    out: list[str] = []
    for text in ((row["title"], row["original_title"]) if row else (job["title"],)):
        text = " ".join(str(text or "").split())
        if len(text) >= 3 and text.lower() not in [q.lower() for q in out]:
            out.append(text)
    return out


def _step_search(job: dict) -> dict:
    cid, ep = job["cid"], job["ep"]
    cooldown = app_config.SOURCEFINDER_COOLDOWN
    with _lock:
        last = _title_search.get(cid)
        if cooldown > 0 and last is not None and time.monotonic() - last < cooldown:
            return {"note": "başlık kısa süre önce arandı (atlandı)"}
    have = {r["source"] for r in db.query("SELECT DISTINCT source FROM source_items WHERE canonical_id=?", (cid,))}
    sites = [s for s in _searchable_sites() if s not in have and _plays_video(s)][:app_config.SOURCEFINDER_MAX_SITES]
    if not sites:
        return {"note": "aranacak başka site yok"}
    queries = _queries(job)
    matched: list[str] = []
    problems: list[str] = []
    for query in queries:
        result = _search_sites(query, sites) or {}
        if any((result.get(s) or {}).get("ok") for s in sites):
            with _lock:   # a title whose search really ran is not searched again by its other episodes (all sites down: retry soon)
                _title_search[cid] = time.monotonic()
        matched = [s for s in sites if (result.get(s) or {}).get("ok") and cid in ((result.get(s) or {}).get("ids") or [])]
        problems = [f"{s}: {_short((result.get(s) or {}).get('error') or (result.get(s) or {}).get('skipped'), 40)}"
                    for s in sites if not (result.get(s) or {}).get("ok")]
        if matched:
            break
    if not matched:
        return {"note": _short(f"{len(sites)} sitede arandı, eşleşme yok" + (f" ({'; '.join(problems[:2])})" if problems else ""))}
    notes: list[str] = []
    for site in matched:
        if job["type"] == "series":
            for row in db.query("SELECT source_key FROM source_items WHERE source=? AND canonical_id=?", (site, cid)):
                try:
                    _hydrate(site, row["source_key"])
                except Exception as exc:
                    notes.append(f"{site}: envanter okunamadı ({_short(exc, 60)})")
        rows = _episode_rows(cid, ep, site=site)
        if not rows:
            notes.append(f"{site}: bu bölüm yok" if job["type"] == "series" else f"{site}: kaynak yok")
            continue
        out = _resolve_found(job, rows, "search", f"{site}: ")
        if out.get("found"):
            return out
        notes.append(out.get("note") or "")
    return {"note": _short("; ".join(n for n in notes if n) or "eşleşen sitede kaynak bulunamadı")}


# --- step 3: repair agent ---------------------------------------------------------------------------------------

def _transient(candidate: dict) -> bool:
    error = str(candidate.get("error") or "")
    return error.startswith(("zaman aşımı", "ValueError: kısa süre önce"))


def _host_known(host: str) -> bool:
    try:
        from ..scraper import heal_agent
        return bool(heal_agent._host_covered(host))
    except Exception:
        return True   # cannot tell: do not call it unknown


def _signal(entry: dict) -> str:
    """Why a failed resolution is worth a repair run: a candidate was found but gave no stream, or its video host is not
    covered by any provider. '' = no (the page had no candidate, or it only timed out)."""
    if isinstance(entry.get("stream"), dict):
        return "akış erişilemiyor"
    bad = [c for c in (entry.get("candidates") or []) if isinstance(c, dict) and not c.get("ok") and not _transient(c)]
    for candidate in bad:
        host = str(candidate.get("host") or "")
        if host and not _host_known(host):
            return f"bilinmeyen host: {host}"
    return "aday var ama akış yok" if bad else ""


def _entry_of(job_row: dict, site: str) -> dict:
    """The resolution trace of one failed source: the playheal window entry, else the site's last resolver event, else the
    bare error."""
    from ..scraper import playheal, state
    row, sid = job_row["row"], job_row["row"]["id"]
    if job_row.get("issue"):   # the stream resolves but a client could not play it: the evidence is the playback issue row
        from . import playissues
        return playissues.failing_entry(job_row["issue"])
    if job_row.get("blocked"):   # the stream resolved but its host refuses it: the evidence is the probe, not a resolution trace
        return playheal.blocked_failing(job_row["blocked"])
    entry = next((e for e in playheal._snapshot(site) if e["source_id"] == sid and not e["ok"]), None)
    if entry is None:
        last = (state.get_site_state(site) or {}).get("last_resolver") or {}
        if last.get("source_id") == sid and not last.get("ok"):
            entry = playheal._entry({**last, "locator": row["locator"], "resolver": row["resolver"], "status": row["status"]})
    if entry is None:
        entry = {"ok": False, "source_id": sid, "kind": row["kind"], "episode_id": row["episode_id"] or "", "locator": row["locator"],
                 "error": job_row["error"], "stage": "", "host": "", "candidates": []}
    return entry


def _evidence(site: str, entries: list[dict]) -> dict:
    """The ``heal.heal_site_playback`` evidence of this episode's failed sources of ``site`` (the shape ``playheal.evaluate`` hands over)."""
    from ..scraper import playheal
    keep = ("source_id", "kind", "episode_id", "locator", "error", "stage", "host", "candidates")
    refused = {e["source_id"] for e in entries if isinstance(e.get("stream"), dict)}
    good = [e for e in playheal._snapshot(site) if e["ok"] and e["source_id"] not in refused][::-1][:playheal.MAX_OK]
    examples = [{"source_id": e["source_id"], "locator": e["locator"]} for e in good]
    if not examples:
        examples = [{"source_id": r["id"], "locator": r["locator"]} for r in db.query(
            "SELECT id,locator FROM video_sources WHERE source=? AND resolver='page' AND kind!='trailer' AND status='healthy' "
            "ORDER BY last_success_at DESC LIMIT ?", (site, playheal.MAX_OK))]
    if entries and all(isinstance(e.get("stream"), dict) for e in entries):   # streams that resolve but are refused: the stream_blocked evidence
        group = [{**e["stream"], "source_id": e["source_id"], "kind": e.get("kind"), "episode_id": e.get("episode_id"),
                  "locator": e.get("locator"), "stream_type": e["stream"].get("type"), "http": e["stream"].get("http")} for e in entries]
        return playheal.blocked_evidence(site, group, examples)
    return {"site": site, "window": {"n": len(entries) + len(examples), "failed": len(entries)},
            "failing": [{k: e.get(k) for k in keep + (("stream",) if e.get("stream") else ())} for e in entries[:playheal.MAX_FAILING]],
            "ok_examples": examples}


def heal_runs_today() -> int:
    """Repair-agent runs the finder started in the last 24 h: this process's own count or what the ops heal history says
    (``trigger == "finder"``, a cooldown skip is not a run), whichever is more."""
    cutoff = time.time() - 86400
    with _lock:
        _heal_runs[:] = [t for t in _heal_runs if t >= cutoff]
        mine = len(_heal_runs)
    try:
        from ..scraper import state
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
        logged = sum(1 for h in state.list_ops("heals", None, 200)
                     if h.get("trigger") == "finder" and h.get("outcome") != "skipped_cooldown" and str(h.get("at") or "") >= stamp)
    except Exception:
        logged = 0
    return max(mine, logged)


def _step_heal(job: dict) -> dict:
    failed = dict(job.get("failed") or {})
    if not failed:
        return {"note": "çözümleme izi yok (atlandı)"}
    from .. import llm_health
    from ..scraper import heal as sheal, playheal, state
    if not sheal._enabled():
        return {"note": "heal kapalı (atlandı)"}
    by_site: dict[str, list[dict]] = {}
    signals: dict[str, str] = {}
    for item in failed.values():
        site = item["row"]["source"]
        entry = _entry_of(item, site)
        by_site.setdefault(site, []).append(entry)
        signals.setdefault(site, _signal(entry))
    sites = [s for s in sorted(by_site, key=lambda s: -len(by_site[s])) if signals.get(s)]
    if not sites:
        return {"note": "iz 'aday var ama akış yok' / bilinmeyen host göstermiyor (atlandı)"}
    status = (llm_health.check() or {}).get("status")
    if status != llm_health.VALID:
        return {"note": f"LLM hesabı hazır değil: {status or 'bilinmiyor'} (atlandı)"}
    notes: list[str] = []
    for site in sites:
        if state.get_heal_cooldown(site):
            notes.append(f"{site}: heal cooldown")
            continue
        if heal_runs_today() >= app_config.SOURCEFINDER_DAILY_BUDGET:
            notes.append("günlük ajan bütçesi doldu")
            break
        from . import playissues
        issue_ids = [i["row"]["id"] for i in failed.values() if i["row"]["source"] == site and i.get("issue")]
        if issue_ids and playissues.recently_triggered(site, issue_ids):
            notes.append(f"{site}: oynatma heal'i zaten başlatıldı")   # one repair per issue: the playback trigger already runs / ran it
            continue
        if not playheal._reserve(site):
            notes.append(f"{site}: heal zaten çalışıyor")
            continue
        try:
            with _lock:
                _heal_runs.append(time.time())
            evidence = (playissues.evidence(site, force=True) if issue_ids else None) or _evidence(site, by_site[site])
            if issue_ids:
                playissues.mark_triggered(site, code=str((evidence.get("issue") or {}).get("code") or ""))
            result = sheal.heal_site_playback(site, evidence=evidence, trigger="finder")
        finally:
            playheal._release(site)
        result = result if isinstance(result, dict) else {}
        if result.get("outcome") == sheal.FIXED and result.get("applied"):
            rows = [i["row"] for i in failed.values() if i["row"]["source"] == site]
            recipe = next((r.get("name") for r in (result.get("recipes") or []) if isinstance(r, dict) and r.get("name")), "")
            out = _resolve_found(job, rows, "heal", f"{site}: onarım sonrası ")
            if out.get("found"):
                if recipe:
                    out["recipe"] = recipe
                return out
            notes.append(f"{site}: onarım uygulandı, akış yine yok")
        else:
            notes.append(f"{site}: {result.get('outcome') or 'sonuçsuz'}" + (f" ({_short(result.get('reason'), 60)})" if result.get("reason") else ""))
    return {"note": _short("; ".join(notes) or "ajan çalışmadı")}


# --- reading side: status, notifications, admin events ----------------------------------------------------------

def _steps_of(text: Any) -> list[dict]:
    try:
        steps = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return [{"name": str(s.get("name") or ""), "ok": bool(s.get("ok")), "ms": int(s.get("ms") or 0), "note": _short(s.get("note"))}
            for s in steps if isinstance(s, dict)][-MAX_STEPS:]


def status(canonical_id: str, episode_id: Optional[str] = None) -> dict:
    """``{"state": idle | searching | found | not_found, "steps": [<= 6 short], "updated_at": epoch s}`` of the finder for a title /
    episode. ``episode_id`` None = the title's newest job whatever the episode (a series opened without an episode)."""
    cid = str(canonical_id or "")
    ep = None if episode_id is None else ("" if episode_id == cid else str(episode_id))
    with _lock:
        live = next((j for k, j in _inflight.items() if k[0] == cid and (ep is None or k[1] == ep)), None)
        if live is not None:
            return {"state": "searching", "steps": [dict(s) for s in live["steps"]][-MAX_STEPS:], "updated_at": int(live["updated_at"])}
    row = (db.query_one("SELECT * FROM finder_jobs WHERE canonical_id=? ORDER BY id DESC LIMIT 1", (cid,)) if ep is None else
           db.query_one("SELECT * FROM finder_jobs WHERE canonical_id=? AND episode_id=? ORDER BY id DESC LIMIT 1", (cid, ep)))
    if row is None or row["state"] not in ("found", "not_found"):   # no job, or one that was cut off by a restart
        return {"state": "idle", "steps": [], "updated_at": 0}
    return {"state": row["state"], "steps": _steps_of(row["steps"]), "updated_at": int(row["finished_at"] or row["started_at"])}


def notifications(profile_id: str, since: int = 0) -> dict:
    """Unread notifications of a profile newer than ``since`` (oldest first, at most ``NOTIFY_LIMIT``): ``{"items": [...],
    "last_id": int}``; ``last_id`` = the newest id handed out (``since`` when there is none)."""
    since = max(0, int(since or 0))
    rows = db.query("SELECT * FROM notifications WHERE profile_id=? AND id>? AND read_at IS NULL ORDER BY id LIMIT ?",
                    (str(profile_id or ""), since, NOTIFY_LIMIT))
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        items.append({"id": row["id"], "kind": row["kind"], "canonical_id": row["canonical_id"] or "",
                      "episode_id": row["episode_id"] or "", "title": str(payload.get("title") or ""),
                      "season": payload.get("season"), "episode": payload.get("episode"), "site": str(payload.get("site") or ""),
                      "method": str(payload.get("method") or ""), "created_at": int(row["created_at"])})
    return {"items": items, "last_id": items[-1]["id"] if items else since}


def mark_read(profile_id: str, upto: int) -> int:
    """Mark the profile's notifications up to id ``upto`` as read; returns how many were still unread."""
    upto = max(0, int(upto or 0))
    before = db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE profile_id=? AND id<=? AND read_at IS NULL",
                          (str(profile_id or ""), upto))["n"]
    db.execute("UPDATE notifications SET read_at=? WHERE profile_id=? AND id<=? AND read_at IS NULL", (_now(), str(profile_id or ""), upto))
    return int(before)


def _iso(epoch: Any) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch or 0)))


def events(limit: int = 200) -> list[dict]:
    """Finished jobs as admin events (``kind=finder``), newest first: ``{id, kind, at, site, canonical_id, episode_id, title,
    season, episode, state, method, steps, source_id, error, trigger, seconds}``; ``site`` = the site of the source that was
    found (empty for ``not_found``)."""
    rows = db.query("""SELECT j.*, li.title AS title, vs.source AS site, vs.season AS season, vs.episode AS episode
        FROM finder_jobs j LEFT JOIN library_items li ON li.id=j.canonical_id LEFT JOIN video_sources vs ON vs.id=j.source_id
        WHERE j.finished_at IS NOT NULL ORDER BY j.id DESC LIMIT ?""", (max(1, int(limit)),))
    out = []
    for row in rows:
        numbers = _EPISODE_ID.match(row["episode_id"] or "")
        season = row["season"] if row["season"] is not None else (int(numbers.group("season")) if numbers else None)
        episode = row["episode"] if row["episode"] is not None else (int(numbers.group("episode")) if numbers else None)
        out.append({"id": row["id"], "kind": "finder", "at": _iso(row["finished_at"]), "site": row["site"] or "",
                    "canonical_id": row["canonical_id"], "episode_id": row["episode_id"] or "", "title": row["title"] or row["canonical_id"],
                    "season": season, "episode": episode, "state": row["state"], "method": row["method"] or "",
                    "steps": _steps_of(row["steps"]), "source_id": row["source_id"] or "", "error": row["error"] or "",
                    "trigger": row["trigger"] or "", "seconds": max(0, int(row["finished_at"]) - int(row["started_at"]))})
    return out
