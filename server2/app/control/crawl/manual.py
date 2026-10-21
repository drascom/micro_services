"""Admin-triggered crawls (T27), outside the daily K1 quota and the daily gate budget (neither is counted). Politeness
stays: ``core.fetch`` paces the host (``delay_ms``, ``concurrency``, robots, host back-off) and a site that blocks
(challenge cooldown, ``hold_until``) is not crawled.

- ``crawl_all`` (key ``crawl_all:<site>``, payload ``{site_id, trigger: admin, started_at, ...}``): every pending frontier
  page of the site now (``new`` incl. ``gate_wait`` / quota-waiting K1 rows, due ``failed`` rows; pages found on the way
  too). A page is taken once per job (``crawled_at < started_at``), so a page that waits again is not looped. Progress is
  checkpointed per batch (a restart goes on); the admin "durdur" cancels the job, the remaining pages keep their plan.
- ``crawl_title`` (key ``crawl_title:<cid>``): one title's pages now (title page -> gate -> episodes, then its season
  pages, then its episode pages for their source candidates, T28); a title missing from the frontier is added from its ``source_url``."""
from __future__ import annotations

import time
from typing import Any, Optional

from ... import db
from .. import jobs, notes
from ..sitemap import classify
from . import drift, frontier, plan, process, run, state

ALL, TITLE = "crawl_all", "crawl_title"
ACTIVE = ("queued", "running", "waiting")
TIMEOUT_S = 900

_ERRORS = {
    "challenge": "site engelledi (challenge)", "http_429": "site engelledi (429)", "blocked": "kapı: halka kapalı (telif)",
    "no_episodes": "bölüm bulunamadı", "no_title": "başlık sayfası okunamadı", "gate_no_probe": "kapı için bölüm sayfası okunamadı",
    "gate_retry": "kapı sonucu belirsiz, 1 saat sonra yeniden", "wait_rules": "sitenin kuralları henüz hazır değil",
    "hold": "site taramayı engelliyor (bekleme süresi)", "inactive": "site etkin değil", "no_url": "başlık adresi yok",
}


def all_key(site_id: str) -> str:
    return f"{ALL}:{site_id}"


def title_key(cid: str) -> str:
    return f"{TITLE}:{cid}"


def _job(key: str) -> Optional[dict]:
    return jobs.store.get_by_key(key)


def all_active(site_id: str) -> bool:
    job = _job(all_key(site_id))
    return bool(job and job["state"] in ACTIVE)


def _held(site_id: str) -> bool:
    return int(state.get(site_id).get("hold_until") or 0) > state.now()


def _dur(seconds: float) -> str:
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s} sn"
    if s < 3600:
        return f"{s // 60} dk {s % 60} sn"
    return f"{s // 3600} sa {(s % 3600) // 60} dk"


# ---------------------------------------------------------------------------------------------- crawl all

def _pending_where(site_id: str, started: int) -> tuple[str, tuple]:
    stopped = list(drift.stopped(site_id))
    extra = f" AND page_type NOT IN ({','.join('?' * len(stopped))})" if stopped else ""
    return ("site_id=? AND ((state='new' AND mark NOT IN ('blocked','wait_rules')) OR (state='failed' AND next_at<=?)) "
            f"AND COALESCE(crawled_at, 0) < ?{extra}", (site_id, int(state.now()), int(started), *stopped))


def remaining(site_id: str, started: int) -> int:
    where, args = _pending_where(site_id, started)
    return int(db.query_one(f"SELECT COUNT(*) FROM crawl_frontier WHERE {where}", args)[0])


def _take(site_id: str, started: int, n: int) -> list[dict]:
    where, args = _pending_where(site_id, started)
    return [dict(r) for r in db.query(f"SELECT * FROM crawl_frontier WHERE {where} "
                                      "ORDER BY priority DESC, next_at ASC, discovered_at ASC, position ASC LIMIT ?", (*args, n))]


def start_all(site_id: str) -> dict:
    """Queue the site's "crawl everything now" job (an active one is returned as is)."""
    job = _job(all_key(site_id))
    if job and job["state"] in ACTIVE:
        return {"job_id": job["id"], "total": (state.get(site_id).get("manual") or {}).get("total", 0)}
    started = int(state.now())
    total = remaining(site_id, started)
    payload = {"site_id": site_id, "trigger": "admin", "started_at": started, "wall_start": time.time(),
               "done": 0, "ok": 0, "failed": 0}
    jid = jobs.enqueue(ALL, all_key(site_id), site_id=site_id, payload=payload, priority=7)
    state.update(site_id, lambda d: d.update(manual={"job_id": jid, "state": "running", "started_at": started,
                                                     "total": total, "done": 0, "ok": 0, "failed": 0}))
    return {"job_id": jid, "total": total}


def _finish(site_id: str, p: dict, how: str, text: str) -> dict:
    took = max(0.0, time.time() - float(p.get("wall_start") or time.time()))
    info = {"state": how, "finished_at": int(state.now()), "took_s": int(round(took)),
            "done": p.get("done", 0), "ok": p.get("ok", 0), "failed": p.get("failed", 0)}
    state.update(site_id, lambda d: d.setdefault("manual", {}).update(info))
    notes.add("info" if how == "done" else "warn",
              f"{site_id}: elle tarama {text}: {p.get('done', 0)} sayfa ({p.get('ok', 0)} başarılı, "
              f"{p.get('failed', 0)} hata), süre {_dur(took)}", site_id=site_id, ref_kind="crawl", ref_id=site_id)
    return info


def stop_all(site_id: str) -> dict:
    """Admin "durdur": the job is cancelled; pages not read yet keep their normal plan."""
    job = _job(all_key(site_id))
    if not job or job["state"] not in ACTIVE or not jobs.cancel(all_key(site_id)):
        return {"stopped": False}
    p = dict(job.get("payload") or {})
    p.update({k: v for k, v in (state.get(site_id).get("manual") or {}).items() if k in ("done", "ok", "failed")})
    _finish(site_id, p, "cancelled", "durduruldu")
    return {"stopped": True, "left": remaining(site_id, int(p.get("started_at") or 0))}


def run_all(job: dict, jctx) -> Any:
    p = dict(job.get("payload") or {})
    site_id = p.get("site_id") or job.get("site_id")
    bad = run._site_ok(site_id)
    if bad is not None:
        return bad
    started = int(p.get("started_at") or state.now())
    if _held(site_id):
        _finish(site_id, p, "held", "durdu (site engelliyor)")
        return jobs.Done({**p, "stopped": "hold"})
    st = state.get(site_id)
    ctx = process.context(site_id, k1_complete=bool(st.get("k1_done_at")), admin=True)
    plan.ensure_k1(site_id, ctx.map)
    expired = getattr(jctx, "expired", None) or (lambda: False)
    jid = job.get("id")
    cancelled = (lambda: (jobs.store.get(jid) or {}).get("state") != "running") if jid else (lambda: False)

    def stop() -> bool:
        return expired() or cancelled()

    step = int(job.get("step") or 0)
    while True:
        if stop():
            if cancelled():
                return jobs.Done(p)          # cancelled: the store no longer takes our result
            if jctx is not None:
                jctx.checkpoint(step, p)
            return jobs.Retry(0, "deadline: the manual crawl goes on in the next run", yield_=True)
        rows = _take(site_id, started, run.BATCH)
        if not rows:
            break
        pairs = [(r, o) for r, o in zip(rows, run.run_batch(site_id, rows, ctx, stop)) if o is not None]
        t = int(state.now())
        with db.transaction() as conn:   # taken once per job, also when the page waits again (gate retry, block)
            for r, _o in pairs:
                conn.execute("UPDATE crawl_frontier SET crawled_at=? WHERE site_id=? AND url=? AND COALESCE(crawled_at,0)<?",
                             (max(t, started), site_id, r["url"], started))
        outs = [o for _, o in pairs]
        p["done"] = int(p.get("done") or 0) + len(outs)
        p["ok"] = int(p.get("ok") or 0) + sum(1 for o in outs if o.ok)
        p["failed"] = int(p.get("failed") or 0) + sum(1 for o in outs if not o.ok)
        run.batch_done(site_id, len(outs))
        state.update(site_id, lambda d: d.setdefault("manual", {}).update(done=p["done"], ok=p["ok"], failed=p["failed"]))
        if cancelled():
            return jobs.Done(p)
        step += 1
        if jctx is not None and hasattr(jctx, "checkpoint"):
            jctx.checkpoint(step, p)
        if run._hold(site_id, outs):
            _finish(site_id, p, "held", "durdu (site engelliyor)")
            return jobs.Done({**p, "stopped": "hold"})
    plan.check_k1_done(site_id)
    _finish(site_id, p, "done", "bitti")
    return jobs.Done(p)


# ---------------------------------------------------------------------------------------------- one title

def _counts(site_id: str, key: str) -> dict:
    row = db.query_one("SELECT COUNT(DISTINCT CASE WHEN kind='episode' THEN season END) AS s, "
                       "COUNT(DISTINCT CASE WHEN kind='episode' THEN season || ':' || episode END) AS e, COUNT(*) AS k "
                       "FROM video_sources WHERE source=? AND source_key=? AND kind IN ('episode','movie')", (site_id, key))
    return {"seasons": int(row["s"] or 0), "episodes": int(row["e"] or 0), "sources": int(row["k"] or 0)}


def crawl_title_page(site_id: str, url: str) -> dict:
    """The title page now (+ its season pages), outside the quota and the gate budget. ``{ok, error?, seasons, episodes,
    sources, pages, text}``."""
    out: dict = {"site": site_id, "url": url, "ok": False, "pages": 0}
    site = run.map_store.get(site_id)
    if not site or site["status"] != "active":
        return {**out, "error": "inactive", "text": _ERRORS["inactive"]}
    if not url:
        return {**out, "error": "no_url", "text": _ERRORS["no_url"]}
    if _held(site_id):
        return {**out, "error": "hold", "text": _ERRORS["hold"]}
    st = state.get(site_id)
    ctx = process.context(site_id, k1_complete=bool(st.get("k1_done_at")), admin=True)
    if not classify.same_site(url, ctx.base):
        return {**out, "error": "foreign_host", "text": "adres sitenin değil"}
    out["added"] = frontier.add(site_id, url, "title", priority=frontier.K0)
    row = frontier.get(site_id, url)
    res = process.process(site_id, row, ctx)
    run.apply(site_id, row, res)
    out["pages"] = 1
    error = res.error if not res.ok else ("blocked" if res.mark == "blocked" else
                                          res.error if res.mark in ("empty", "gate_wait") else
                                          "wait_rules" if res.mark == "wait_rules" else "")
    if res.ok and res.mark in ("ok", ""):
        for srow in db.query("SELECT * FROM crawl_frontier WHERE site_id=? AND page_type='season' AND parent_url=? "
                             "AND state!='gone' ORDER BY position, url", (site_id, url)):
            sres = process.process(site_id, dict(srow), ctx)
            run.apply(site_id, dict(srow), sres)
            out["pages"] += 1
        for prow in db.query("SELECT * FROM crawl_frontier WHERE site_id=? AND page_type='sources' AND parent_url=? "
                             "AND state!='gone' AND mark!='blocked' ORDER BY url", (site_id, url)):
            pres = process.process(site_id, dict(prow), ctx)   # T28: every episode page's source candidates too
            run.apply(site_id, dict(prow), pres)
            out["pages"] += 1
    run.batch_done(site_id, out["pages"])
    key = classify.title_key(ctx.map, url)
    counts = _counts(site_id, key) if key else {"seasons": 0, "episodes": 0, "sources": 0}
    out.update(counts)
    if error:
        out.update(error=error, text=_ERRORS.get(error, error))
    else:
        out.update(ok=True, text=f"{counts['seasons']} sezon, {counts['episodes']} bölüm, {counts['sources']} kaynak yazıldı")
    return out


def start_title(cid: str) -> Optional[int]:
    if not run._title_rows(cid):
        return None
    return jobs.enqueue(TITLE, title_key(cid), payload={"canonical_id": cid, "trigger": "admin"}, priority=9)


def title_job(cid: str) -> dict:
    job = _job(title_key(cid))
    if not job:
        return {}
    return {k: job.get(k) for k in ("id", "state", "result", "error", "updated_at", "finished_at")}


def run_title(job: dict, jctx) -> Any:
    cid = (job.get("payload") or {}).get("canonical_id") or ""
    results = [crawl_title_page(site_id, url) for site_id, url in run._title_rows(cid)]
    text = " · ".join((f"{r['site']}: " if len(results) > 1 else "") + r["text"] for r in results) or "site kaydı yok"
    return jobs.Done({"ok": any(r["ok"] for r in results), "text": text, "sites": results})


def register() -> None:
    jobs.register(ALL, run_all, timeout_s=TIMEOUT_S)
    jobs.register(TITLE, run_title, timeout_s=TIMEOUT_S)
