"""The admin "Tarama" view of one site's crawl (three tiers, counts, quota, stopped page types)."""
from __future__ import annotations

from ... import db
from .. import jobs
from ..sitemap import store as map_store
from . import drift, frontier, manual, plan, process, run, state


def _titles(site_id: str, where: str, args: tuple = ()) -> dict:
    row = db.query_one(f"SELECT COUNT(*) AS n, SUM(state='done') AS d FROM crawl_frontier WHERE site_id=? "
                       f"AND page_type='title' AND {where}", (site_id, *args))
    return {"discovered": int(row["n"] or 0), "done": int(row["d"] or 0)}


def _job(key: str) -> dict:
    job = jobs.store.get_by_key(key)
    return {k: job.get(k) for k in ("state", "run_after", "finished_at", "error", "wait_reason")} if job else {}


def view(site_id: str) -> dict:
    site = map_store.get(site_id)
    if site is None:
        raise LookupError(site_id)
    st = state.get(site_id)
    t = state.now()
    stats = frontier.stats(site_id)
    q = plan.quota(site_id, t, st)
    k0 = _titles(site_id, "priority>=?", (frontier.K0,))
    k1 = _titles(site_id, "priority<?", (frontier.K0,))
    if not st.get("k1_start"):
        k1_state = "waiting_a2" if not plan.a2_passed(site_id) else "pending"
    else:
        k1_state = "done" if st.get("k1_done_at") else "running"
    home = frontier.get(site_id, plan.home_url(plan.site_map(site_id))) or {}
    stopped = [{"page_type": pt, "label": drift.LABEL.get(pt, pt), "stage": drift.PAGE.get(pt, ("", ""))[1],
                "at": info.get("at"), "evidence": info.get("evidence")} for pt, info in (st.get("stopped") or {}).items()]
    titles = stats["by_type"].get("title") or {}
    return {
        "site_id": site_id, "status": site["status"], "paused": bool(st.get("paused")),
        "hold_until": int(st.get("hold_until") or 0) if int(st.get("hold_until") or 0) > t else None,
        "backfill_days": state.site_setting(site_id, "backfill_days"), "refresh_hour": state.site_setting(site_id, "refresh_hour"),
        "progress": {"done": int(titles.get("done") or 0), "discovered": int(titles.get("discovered") or 0)},
        "tiers": [
            {"id": "k0", "title": "Öne çıkanlar", "state": "done" if home.get("state") == "done" else
             ("pending" if home else "waiting"), **k0},
            {"id": "k1", "title": "Tam aktarım", "state": k1_state, **k1, "day": q.get("day", 0) + 1 if q["active"] else None,
             "days": q["days"], "quota": q["quota"], "quota_used": q["used"], "quota_left": q["left"]},
            {"id": "k2", "title": "Günlük tazeleme", "state": "scheduled" if st.get("refresh_date") else "waiting",
             "last": st.get("refresh_at"), "due": stats["new"]},
        ],
        "stats": stats, "stopped": stopped,
        "plan": {"days": q["days"], "k1_active": q["active"], "quota": q["quota"], "quota_used": q["used"],
                 "gate_used": process.gate_used(site_id, st), "gate_limit": process.gate_daily(),
                 "pending": int(stats["new"]) + int(stats["failed"]), "gate_wait": int(stats["gate_wait"])},
        "manual": _manual(site_id, st),
        "jobs": {"crawl": _job(run.crawl_key(site_id)), "refresh": _job(run.refresh_key(site_id))},
    }


def _manual(site_id: str, st: dict) -> dict:
    """The admin "crawl everything now" job: ``{running, done, total, ok, failed, state, took_s, finished_at}``."""
    m = dict(st.get("manual") or {})
    if not m:
        return {}
    m["running"] = manual.all_active(site_id)
    if m["running"]:
        m["total"] = int(m.get("done") or 0) + manual.remaining(site_id, int(m.get("started_at") or 0))
    return m


def progress(site_id: str) -> dict:
    """Sites screen column: ``{done, discovered, blocked, failed}`` of title pages."""
    stats = frontier.stats(site_id)
    titles = stats["by_type"].get("title") or {}
    return {"done": int(titles.get("done") or 0), "discovered": int(titles.get("discovered") or 0),
            "blocked": stats["blocked"], "failed": stats["failed"] + stats["gone"]}
