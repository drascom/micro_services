"""Crawl jobs (README §5.3):

- ``crawl`` (key ``crawl:<site>``): up to ``BATCH`` due frontier pages per run, in parallel (the ``core.fetch`` limiter
  paces the host), LLM-free. More due work -> the job queues itself again (``Retry(yield_)``: a planned yield, not a
  failure); K1 pages beyond today's quota wait, the rest of the quota is spread over the day. Every page is written to
  the frontier as soon as it is done, so a run that dies (lost lease) never fetches a finished page again.
- ``refresh`` (key ``refresh:<site>``): the daily plan (``plan.refresh``) at ``refresh_hour``; also right on activation.
- ``crawl_tick`` (every 15 min): queues due refreshes and wakes the crawl of sites with due pages.
- ``inventory`` (key ``inventory:<cid>``, bound to the client detail hydrate): the series' title pages are read now
  (not while the site holds / its title type is stopped, nor a page read in the last ``INVENTORY_FRESH_S``).
Every run that read pages ends with one ``crawl_batch_done(site_id, pages)`` event (routing applies once per batch).
A run past its deadline starts no new page (the ones in flight finish; the rest stay due for the next run).

Block protection: ``challenge`` / ``http_429`` three times in a row -> the site's crawl holds for 6 hours (+ admin note);
those pages do not use the quota and do not climb the failure ladder."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ... import db
from ...core import events
from .. import jobs, notes
from ..sitemap import classify, store as map_store
from . import drift, frontier, plan, process, state

CRAWL, REFRESH, TICK, INVENTORY = "crawl", "refresh", "crawl_tick", "inventory"
BATCH = 20
WORKERS = 4
TICK_S = 900
HOLD_S = 6 * 3600
BLOCK_CODES = ("challenge", "http_429")
BLOCK_STREAK = 3
BLOCK_RETRY_S = 3600
PAUSED = "site paused"          # == stages.engine.PAUSED (resume of the site wakes these)
CRAWL_PAUSED = "crawl paused"
TIMEOUT_S = 900
INVENTORY_FRESH_S = 6 * 3600
OBSOLETE_KINDS = ("k0", "k0_sources")   # T08 jobs the crawl replaced


def crawl_key(site_id: str) -> str:
    return f"{CRAWL}:{site_id}"


def refresh_key(site_id: str) -> str:
    return f"{REFRESH}:{site_id}"


def kick(site_id: str) -> Optional[int]:
    """The site's crawl job runs as soon as a worker is free (a queued one waiting for its spread slot is made due)."""
    job = jobs.store.get_by_key(crawl_key(site_id))
    if job and job["state"] == "queued":
        jobs.store.requeue(job["id"])
        return job["id"]
    if job and job["state"] in ("running", "waiting"):
        return job["id"]
    return jobs.enqueue(CRAWL, crawl_key(site_id), site_id=site_id, payload={"site_id": site_id}, priority=5)


def activate(site_id: str) -> int:
    """The site just became active: today's plan runs now (K0 first)."""
    return jobs.enqueue(REFRESH, refresh_key(site_id), site_id=site_id, payload={"site_id": site_id}, priority=6)


def _site_ok(site_id: Optional[str]) -> Any:
    """``None`` when the job may go on, else the job result to return."""
    site = map_store.get(site_id) if site_id else None
    if site is None:
        return jobs.Fail(f"unknown site {site_id}")
    if site["status"] == "paused":
        return jobs.Wait(PAUSED)
    if site["status"] != "active":
        return jobs.Done({"skipped": "site not active"})
    return None


# ---------------------------------------------------------------------------------------------- one page

def apply(site_id: str, row: dict, out: process.Outcome) -> None:
    """Write one page's outcome: frontier state, structure streak, ``page_crawled``."""
    url, page_type = row["url"], row["page_type"]
    if not out.ok:
        if out.error in BLOCK_CODES:
            frontier.reschedule(site_id, url, state.now() + BLOCK_RETRY_S)
        else:
            frontier.failed(site_id, url, out.error)
    elif out.retry_at:
        frontier.reschedule(site_id, url, out.retry_at, mark=out.mark)
    else:
        frontier.done(site_id, url, mark=out.mark)
    if out.ok and page_type in drift.PAGE and out.mark not in ("wait_rules", "blocked"):
        bad = out.fingerprint_ok is False or out.zero
        drift.record(site_id, page_type, url, bad=bad, reasons=out.reasons or (["0 satır"] if out.zero else []))
    events.emit("page_crawled", site_id=site_id, url=url, page_type=page_type, ok=out.ok,
                fingerprint_ok=out.fingerprint_ok, counts=dict(out.counts))


def _one(site_id: str, row: dict, ctx: process.Ctx, stop=None) -> Optional[process.Outcome]:
    try:
        if stop is not None and stop():
            return None            # past the job's deadline: the row stays due, nothing is fetched
        out = process.process(site_id, row, ctx)
        apply(site_id, row, out)
        return out
    finally:
        db.close_thread()


def run_batch(site_id: str, rows: list[dict], ctx: process.Ctx, stop=None) -> list[Optional[process.Outcome]]:
    """One outcome per row (``None``: not started because ``stop()`` said the time is up). Returns after every started
    page was written (the pool is joined)."""
    if not rows:
        return []
    workers = max(1, min(WORKERS, len(rows), int((ctx.map.get("fetch") or {}).get("concurrency") or 2) * 2))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crawl") as pool:
        return list(pool.map(lambda r: _one(site_id, r, ctx, stop), rows))


def batch_done(site_id: str, pages: int) -> None:
    if pages:
        events.emit("crawl_batch_done", site_id=site_id, pages=pages)


# ---------------------------------------------------------------------------------------------- jobs

def _hold(site_id: str, outcomes: list[process.Outcome]) -> bool:
    """Count block answers in a row; three -> the site holds ``HOLD_S`` (+ note). True when it holds now."""
    held = {}

    def fn(data: dict) -> None:
        streak = int(data.get("challenge_streak") or 0)
        for out in outcomes:
            if out.error in BLOCK_CODES:
                streak += 1
            elif out.ok:
                streak = 0
            if streak >= BLOCK_STREAK:
                data["hold_until"] = int(state.now()) + HOLD_S
                held["code"] = out.error
                streak = 0
                break
        data["challenge_streak"] = streak
    state.update(site_id, fn)
    if held:
        notes.add("warn", f"{site_id}: site taramayı engelliyor ({held['code']} x{BLOCK_STREAK}); tarama 6 saat duruyor",
                  site_id=site_id, ref_kind="crawl", ref_id=site_id)
        db.execute("UPDATE crawl_frontier SET next_at=MAX(next_at, ?) WHERE site_id=? AND state IN ('new','failed')",
                   (int(state.now()) + HOLD_S, site_id))
    return bool(held)


def run_crawl(job: dict, jctx) -> Any:
    site_id = (job.get("payload") or {}).get("site_id") or job.get("site_id")
    stop = _site_ok(site_id)
    if stop is not None:
        return stop
    st = state.get(site_id)
    if st.get("paused"):
        return jobs.Wait(CRAWL_PAUSED)
    t = state.now()
    if int(st.get("hold_until") or 0) > t:
        return jobs.Retry(int(st["hold_until"] - t), "site blocks the crawl", yield_=True)
    from . import manual
    if manual.all_active(site_id):   # the admin's "crawl everything now" holds the frontier: no double fetch
        return jobs.Retry(300, "manual crawl running", yield_=True)
    ctx = process.context(site_id, k1_complete=bool(st.get("k1_done_at")))
    plan.ensure_k1(site_id, ctx.map)
    stopped = list(drift.stopped(site_id))
    q = plan.quota(site_id, t)
    rows = frontier.take(site_id, BATCH, t, skip_types=stopped, quota=q["left"] if q["active"] else None)
    stop = getattr(jctx, "expired", None)
    pairs = [(r, o) for r, o in zip(rows, run_batch(site_id, rows, ctx, stop)) if o is not None]
    outcomes = [o for _, o in pairs]
    # K1 quota: only pages really fetched (a block answer or a page waiting for its rule does not pay)
    used = sum(1 for r, o in pairs if frontier.is_quota(r) and o.fetched and o.error not in BLOCK_CODES)
    plan.count_quota(site_id, used, t)
    result = {"pages": len(outcomes), "ok": sum(1 for o in outcomes if o.ok),
              "failed": sum(1 for o in outcomes if not o.ok), "quota_used": used}
    batch_done(site_id, len(outcomes))
    if len(outcomes) < len(rows):
        return jobs.Retry(0, "deadline: the rest of the batch goes on in the next run", yield_=True)
    if _hold(site_id, outcomes):
        return jobs.Retry(HOLD_S, "site blocks the crawl", yield_=True)
    if plan.check_k1_done(site_id) and q["active"]:
        notes.add("info", f"{site_id}: tam aktarım bitti; bundan sonra yalnız günlük tazeleme", site_id=site_id,
                  ref_kind="crawl", ref_id=site_id)
    stopped = list(drift.stopped(site_id))
    t = state.now()   # rows the batch discovered are due at their discovery second, which may be after the batch began
    if frontier.take(site_id, 1, t, skip_types=stopped, quota=0):
        return jobs.Retry(0, "more pages", yield_=True)
    q = plan.quota(site_id, t)
    if q["active"] and q["left"] and frontier.take(site_id, 1, t, skip_types=stopped, quota=q["left"]):
        slots = max(1, math.ceil(q["left"] / BATCH))
        spread = max(60, int((q["day_ends"] - t) / (slots + 1)))
        return jobs.Retry(spread, "quota spread over the day", yield_=True)
    return jobs.Done(result)


def run_refresh(job: dict, jctx) -> Any:
    site_id = (job.get("payload") or {}).get("site_id") or job.get("site_id")
    stop = _site_ok(site_id)
    if stop is not None:
        return stop
    out = plan.refresh(site_id)
    kick(site_id)
    return jobs.Done(out)


def tick(now: Optional[float] = None) -> dict:
    """Due refreshes are queued; sites with due pages get their crawl job."""
    t = state.now() if now is None else now
    out = {"refresh": [], "crawl": []}
    for site in map_store.list_sites():
        sid = site["site_id"]
        if site["status"] != "active":
            continue
        if plan.refresh_due(sid, t):
            jobs.enqueue(REFRESH, refresh_key(sid), site_id=sid, payload={"site_id": sid}, priority=6)
            out["refresh"].append(sid)
            continue
        st = state.get(sid)
        if st.get("paused") or int(st.get("hold_until") or 0) > t:
            continue
        q = plan.quota(sid, t, st)
        if frontier.take(sid, 1, t, skip_types=list(st.get("stopped") or {}), quota=q["left"] if q["active"] else None):
            job = jobs.store.get_by_key(crawl_key(sid))
            if not job or job["state"] not in ("running", "waiting"):
                kick(sid)
                out["crawl"].append(sid)
    return out


def _tick_job(job, jctx):
    return jobs.Done(tick())


# ---------------------------------------------------------------------------------------------- titles from elsewhere

def _title_rows(canonical_id: str) -> list[tuple[str, str]]:
    return [(r["site_id"], r["source_url"]) for r in db.query(
        "SELECT si.site_id, si.source_url FROM source_items si JOIN sites s ON s.site_id=si.site_id "
        "WHERE si.canonical_id=? AND s.status='active' AND si.source_url IS NOT NULL", (canonical_id,))]


def request_inventory(canonical_id: str) -> Optional[int]:
    """Bound to the client detail hydrate (``api._hydrate.inventory``): a series without episodes is read now."""
    if not _title_rows(canonical_id):
        return None
    return jobs.enqueue(INVENTORY, f"{INVENTORY}:{canonical_id}", payload={"canonical_id": canonical_id}, priority=8)


def run_inventory(job: dict, jctx) -> Any:
    cid = (job.get("payload") or {}).get("canonical_id")
    out = {"pages": 0, "episodes": 0, "skipped": 0}
    t = state.now()
    for site_id, url in _title_rows(cid or ""):
        st = state.get(site_id)
        smap = plan.site_map(site_id)
        if st.get("paused") or int(st.get("hold_until") or 0) > t or "title" in (st.get("stopped") or {}) \
                or not classify.same_site(url, str(smap.get("base_url") or "")):
            out["skipped"] += 1
            continue
        frontier.add(site_id, url, "title", priority=frontier.K0)
        row = frontier.get(site_id, url)
        if row["mark"] == "blocked":
            continue
        if row["state"] == "done" and (row["crawled_at"] or 0) > t - INVENTORY_FRESH_S:
            out["skipped"] += 1    # read a moment ago: another detail view does not fetch it again
            continue
        ctx = process.context(site_id, k1_complete=bool(st.get("k1_done_at")))
        res = process.process(site_id, row, ctx)
        apply(site_id, row, res)
        batch_done(site_id, 1)
        out["pages"] += 1
        out["episodes"] += int(res.counts.get("episodes") or 0)
        if res.counts.get("seasons"):
            kick(site_id)
    return jobs.Done(out)


def from_search(site_id: str, urls: list[str]) -> int:
    """Titles a search imported without playback rows: the crawl reads them (series page -> episodes -> sources)."""
    site = map_store.get(site_id)
    if not site or site["status"] != "active":
        return 0
    added = 0
    for url in urls:
        if url and frontier.add(site_id, url, "title", priority=frontier.K2):
            added += 1
    if added:
        kick(site_id)
    return added


# ---------------------------------------------------------------------------------------------- admin

def set_paused(site_id: str, paused: bool) -> dict:
    state.update(site_id, lambda d: d.update(paused=bool(paused)))
    woken = False
    if not paused:
        woken = jobs.wake(crawl_key(site_id))
        kick(site_id)
    return {"paused": bool(paused), "woken": bool(woken)}


def refresh_now(site_id: str) -> int:
    return jobs.enqueue(REFRESH, refresh_key(site_id), site_id=site_id, payload={"site_id": site_id}, priority=6)


def drop_obsolete_jobs() -> int:
    """Startup: T08's ``k0`` / ``k0_sources`` jobs have no handler any more; active ones end ``failed`` (``obsolete``)."""
    marks = ",".join("?" * len(OBSOLETE_KINDS))
    try:
        return db.execute(f"UPDATE jobs SET state='failed', error='obsolete', lease_until=NULL, lease_owner=NULL "
                          f"WHERE kind IN ({marks}) AND state IN ('queued','running','waiting')", OBSOLETE_KINDS).rowcount
    except Exception:   # no jobs table yet (a partial test schema)
        return 0


def register() -> None:
    from . import manual
    drop_obsolete_jobs()
    manual.register()
    jobs.register(CRAWL, run_crawl, timeout_s=TIMEOUT_S)
    jobs.register(REFRESH, run_refresh)
    jobs.register(INVENTORY, run_inventory, timeout_s=TIMEOUT_S)
    jobs.register(TICK, _tick_job)
    jobs.schedule.every(TICK, TICK, TICK_S)
