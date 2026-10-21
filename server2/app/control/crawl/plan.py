"""The daily plan of a site's crawl (LLM-free):

- **K0** (on activation and every day): the home page into the frontier (priority 100); its cards follow at 100.
- **K1** (once A2 passed): the map's ``listings[]`` (priority 10). Their title pages are the full import, spread over
  ``backfill_days``: daily quota = ``ceil(pending / days left)`` (days left = ``backfill_days - days gone``, at least 1),
  where pending counts the K1 title pages still to read plus the ones read today (stable during the day, recomputed when
  ``backfill_days`` changes or a day was skipped).
- **K2** (job ``refresh`` at ``refresh_hour``): home + every listing's first page again; running series (last episode
  newer than 30 days or in the future) again; other titles every 14 days; ``gone`` rows one more time after 7 days.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from ... import db
from ..sitemap import labels, store as map_store
from ..stages import common, state as stage_state
from . import drift, frontier, state

ONGOING_DAYS = 30
REVISIT_DAYS = 14
STAGE_TYPES = {"A1": "home", "A2": "listing", "A3": "title", "A4": "season", "A5": "episode"}
_LISTING_ORIGIN = {"genre": "genre", "type": "type"}


def site_map(site_id: str) -> dict:
    return map_store.active(site_id) or map_store.draft(site_id)


def home_url(smap: dict) -> str:
    home = (smap.get("pages") or {}).get("home") or {}
    return common.site_url(str(smap.get("base_url") or ""), home.get("url") or "/")


def a2_passed(site_id: str) -> bool:
    return stage_state.state_of(site_id, "A2") in stage_state.PASSED


# ---------------------------------------------------------------------------------------------- K1

def ensure_k1(site_id: str, smap: Optional[dict] = None) -> int:
    """A2 passed and the full import never began: the listings go into the frontier. Returns the rows added."""
    st = state.get(site_id)
    if st.get("k1_start") or not a2_passed(site_id):
        return 0
    added = seed_listings(site_id, smap)
    state.update(site_id, lambda d: d.update(k1_start=int(state.now()), quota_day=0, quota_used=0))
    return added


def seed_listings(site_id: str, smap: Optional[dict] = None) -> int:
    """Every ``listings[]`` entry of the map into the frontier (idempotent: known rows stay as they are). A list that
    comes after the full import is K2. Returns the rows added."""
    smap = smap or site_map(site_id)
    base = str(smap.get("base_url") or "")
    priority = frontier.K2 if state.get(site_id).get("k1_done_at") else frontier.K1
    added = 0
    for item in labels.listing_items(smap):   # T17: confirmed title lists only (never ignored / episode lists)
        url = common.site_url(base, item["url"])
        origin = (_LISTING_ORIGIN.get(item.get("kind") or "", "listing"), str(item.get("name") or item["url"]))
        added += frontier.add(site_id, url, "listing", priority=priority, origin=origin, position=1)
    return added


def drop_excluded(site_id: str, smap: Optional[dict] = None) -> int:
    """T17: rows the crawl took from menu links now labelled ``ignore`` / ``content: episode`` (the list page, its next pages
    and the not-yet-read pages found only there) and not-yet-read cards of ignored home sections leave the frontier.
    Pages already read stay (their catalog rows too). Returns the rows deleted."""
    smap = smap or site_map(site_id)
    base = str(smap.get("base_url") or "")
    lists = {r["url"] for p in labels.excluded_paths(smap) for r in
             db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND url=? AND page_type='listing'",
                      (site_id, common.site_url(base, p)))}
    todo = list(lists)
    while todo:   # next pages of a dropped list
        rows = db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='listing' AND parent_url=?",
                        (site_id, todo.pop()))
        for r in rows:
            if r["url"] not in lists:
                lists.add(r["url"])
                todo.append(r["url"])
    origins = set()
    for url in lists:
        origins |= {(k, v) for k, v, _ in frontier.origins(site_id, url)}
    home = (smap.get("pages") or {}).get("home") or {}
    origins |= {("section", str(s.get("title") or s.get("id") or "")) for s in home.get("sections") or []
                if isinstance(s, dict) and s.get("ignore")}
    drop = set(lists)
    for r in db.query("SELECT url, parent_url FROM crawl_frontier WHERE site_id=? AND page_type IN ('title','episode') "
                      "AND state IN ('new','failed')", (site_id,)):
        own = {(k, v) for k, v, _ in frontier.origins(site_id, r["url"]) if k != "block_type"}   # a type hint, not a list
        if (own and own <= origins) or (not own and r["parent_url"] in lists):
            drop.add(r["url"])
    if not drop:
        return 0
    with db.transaction() as conn:
        for url in drop:
            conn.execute("DELETE FROM crawl_frontier WHERE site_id=? AND url=?", (site_id, url))
            conn.execute("DELETE FROM crawl_origins WHERE site_id=? AND url=?", (site_id, url))
    return len(drop)


def pending_k1(site_id: str) -> int:
    row = db.query_one("SELECT COUNT(*) FROM crawl_frontier WHERE site_id=? AND page_type='title' AND priority<? "
                       "AND state IN ('new','failed')", (site_id, frontier.K2))
    return int(row[0])


def quota(site_id: str, now: Optional[float] = None, st: Optional[dict] = None) -> dict:
    """``{active, day, days, quota, used, left, pending}`` of today's K1 quota (``active`` False before K1 / after it)."""
    t = state.now() if now is None else now
    st = state.get(site_id) if st is None else st
    start = st.get("k1_start")
    days = state.site_setting(site_id, "backfill_days")
    if not start or st.get("k1_done_at"):
        return {"active": False, "day": 0, "days": days, "quota": 0, "used": 0, "left": 0, "pending": 0}
    day = max(0, int((t - start) // state.DAY_S))
    used = int(st.get("quota_used") or 0) if st.get("quota_day") == day else 0
    pending = pending_k1(site_id)
    remaining = max(1, days - day)
    total = int(math.ceil((pending + used) / remaining)) if pending + used else 0
    return {"active": True, "day": day, "days": days, "quota": total, "used": used, "left": max(0, total - used),
            "pending": pending, "day_ends": int(start + (day + 1) * state.DAY_S)}


def count_quota(site_id: str, n: int, now: Optional[float] = None) -> None:
    if n <= 0:
        return
    t = state.now() if now is None else now

    def fn(data: dict) -> None:
        start = data.get("k1_start")
        if not start:
            return
        day = max(0, int((t - start) // state.DAY_S))
        if data.get("quota_day") != day:
            data.update(quota_day=day, quota_used=0)
        data["quota_used"] = int(data.get("quota_used") or 0) + n
    state.update(site_id, fn)


def check_k1_done(site_id: str) -> bool:
    st = state.get(site_id)
    if not st.get("k1_start") or st.get("k1_done_at"):
        return bool(st.get("k1_done_at"))
    open_lists = db.query_one("SELECT COUNT(*) FROM crawl_frontier WHERE site_id=? AND page_type='listing' "
                              "AND state IN ('new','failed')", (site_id,))[0]
    if pending_k1(site_id) or open_lists:
        return False
    state.update(site_id, lambda d: d.update(k1_done_at=int(state.now())))
    return True


# ---------------------------------------------------------------------------------------------- K0 / K2

def k0_done(site_id: str, now: Optional[float] = None) -> bool:
    """The highlights tier (K0) finished: the home page was read and no K0 page is still due (rows waiting for a later
    retry / a rule do not hold it back)."""
    if db.query_one("SELECT 1 FROM crawl_frontier WHERE site_id=? AND page_type='home' AND state='done' LIMIT 1",
                    (site_id,)) is None:
        return False
    t = int(state.now() if now is None else now)
    return db.query_one("SELECT 1 FROM crawl_frontier WHERE site_id=? AND priority>=? AND state='new' AND next_at<=? LIMIT 1",
                        (site_id, frontier.K0, t)) is None



def _again(site_id: str, url: str, t: float, priority: Optional[int] = None) -> None:
    row = frontier.get(site_id, url)
    if row and row["state"] == "done":
        frontier.reschedule(site_id, url, t, priority=priority)


def _air_date(value) -> Optional[float]:
    text = str(value or "")[:10]
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d"))
    except ValueError:
        return None


def refresh(site_id: str, now: Optional[float] = None) -> dict:
    """K0 + K1 seeding + K2 marks (DB only; the ``crawl`` job reads the pages)."""
    t = state.now() if now is None else now
    smap = site_map(site_id)
    out = {"home": 0, "listings": 0, "titles": 0, "gone": 0}
    home = home_url(smap)
    if not frontier.add(site_id, home, "home", priority=frontier.K0, now=t):
        _again(site_id, home, t)
    out["home"] = 1
    seeded = ensure_k1(site_id, smap)
    if not seeded:
        for row in db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='listing' AND parent_url IS NULL "
                            "AND state='done'", (site_id,)):
            frontier.reschedule(site_id, row["url"], t)
            out["listings"] += 1
    ongoing_after = t - ONGOING_DAYS * state.DAY_S
    stale = t - REVISIT_DAYS * state.DAY_S
    rows = db.query(
        "SELECT f.url, f.crawled_at, MAX(vs.episode_air_date) AS last_air, MAX(vs.kind='episode') AS series "
        "FROM crawl_frontier f LEFT JOIN source_items si ON si.site_id=f.site_id AND si.source_url=f.url "
        "LEFT JOIN video_sources vs ON vs.source=si.site_id AND vs.source_key=si.key "
        "WHERE f.site_id=? AND f.page_type='title' AND f.state='done' GROUP BY f.url", (site_id,))
    for row in rows:
        air = _air_date(row["last_air"])
        running = bool(row["series"]) and air is not None and air >= ongoing_after
        if running or (row["crawled_at"] or 0) <= stale:
            frontier.reschedule(site_id, row["url"], t, priority=frontier.K2)
            out["titles"] += 1
    out["sources"] = recheck_sources(site_id, t)
    for row in db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND state='gone' AND next_at<=?", (site_id, int(t))):
        db.execute("UPDATE crawl_frontier SET state='new', next_at=?, fail_count=? WHERE site_id=? AND url=?",
                   (int(t), frontier.MAX_FAILS - 1, site_id, row["url"]))
        out["gone"] += 1
    state.update(site_id, lambda d: d.update(refresh_date=state.today(t), refresh_at=int(t)))
    return out


def recheck_sources(site_id: str, now: Optional[float] = None) -> int:
    """T28: episode pages whose candidates are older than ``source_recheck_days`` are read again (K2 below titles)."""
    from ...core.play import prefill
    t = state.now() if now is None else now
    cutoff = int(t - prefill.recheck_days() * state.DAY_S)
    return db.execute("UPDATE crawl_frontier SET state='new', next_at=? WHERE site_id=? AND page_type='sources' "
                      "AND state='done' AND mark!='blocked' AND COALESCE(crawled_at,0)<=?", (int(t), site_id, cutoff)).rowcount


def rules_changed(site_id: str, *, regate: bool = False) -> int:
    """A later stage gave the rule some rows waited for (``wait_rules``); ``regate``: a new gate rule (A6/A7) -> every
    title is judged again (blocked ones stay blocked). Raised to K2 so the full import's quota does not pay twice."""
    t = int(state.now())
    n = db.execute("UPDATE crawl_frontier SET state='new', next_at=?, mark='', priority=MAX(priority, ?) "
                   "WHERE site_id=? AND mark='wait_rules'", (t, frontier.K2, site_id)).rowcount
    if regate:
        n += db.execute("UPDATE crawl_frontier SET state='new', next_at=?, mark='', priority=MAX(priority, ?) "
                        "WHERE site_id=? AND page_type='title' AND state='done' AND mark IN ('ok', 'gate_wait', '')",
                        (t, frontier.K2, site_id)).rowcount
    return n


def stage_passed(site_id: str, stage_id: str) -> bool:
    """A stage passed on an active site: its stopped page type goes on; A2 starts K1. True when work was added."""
    page_type = STAGE_TYPES.get(stage_id)
    resumed = bool(page_type) and drift.resume(site_id, page_type)
    seeded = 0
    if stage_id == "A2":   # A2 passed again: listings the new map added reach the frontier too
        seeded = seed_listings(site_id) if state.get(site_id).get("k1_start") else ensure_k1(site_id)
    return bool(resumed or seeded)


def set_schedule(site_id: str, *, backfill_days: Optional[int] = None, refresh_hour: Optional[int] = None) -> None:
    if backfill_days is not None:
        db.execute("UPDATE sites SET backfill_days=?, updated_at=? WHERE site_id=?", (int(backfill_days), int(time.time()), site_id))
    if refresh_hour is not None:
        db.execute("UPDATE sites SET refresh_hour=?, updated_at=? WHERE site_id=?", (int(refresh_hour), int(time.time()), site_id))


def refresh_due(site_id: str, now: Optional[float] = None) -> bool:
    t = state.now() if now is None else now
    if time.localtime(t).tm_hour < state.site_setting(site_id, "refresh_hour"):
        return False
    return state.get(site_id).get("refresh_date") != state.today(t)
