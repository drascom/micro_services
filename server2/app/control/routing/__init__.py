"""Routing (T12): "media from there goes to that group". Rules (``rules``) x ``source_origins`` -> ``library_lists``
(``engine``, LLM-free); LLM only drafts rules (``suggest``) and the admin accepts them.

Jobs: ``routing`` (key ``routing:<site>``: ``engine.apply``; queued on every rule change and after crawl pages) and
``routing_suggest`` (llm, key ``routing_suggest:<site>``: the one automatic ``suggest`` after A3 passed and the crawl's
highlights tier (K0, ``crawl.plan.k0_done``) finished; each crawl batch's ``crawl_batch_done`` -> one ``routing`` 30 s
later checks it; a job already waiting keeps its time).
"""
from __future__ import annotations

import time
from typing import Optional

from ... import db
from ...core import events
from .. import jobs, notes
from ..crawl import plan as crawl_plan
from . import engine, rules, suggest

KIND = "routing"
SUGGEST_KIND = "routing_suggest"
CRAWL_DEBOUNCE_S = 30
PASSED = ("passed", "passed_warn")


def job_key(site_id: str) -> str:
    return f"{KIND}:{site_id}"


def schedule(site_id: Optional[str], *, delay_s: float = 0) -> list[int]:
    """Queue ``apply`` for ``site_id`` (None = every site: a rule for all sites changed). Same key queued = no-op."""
    sites = [site_id] if site_id else [r["site_id"] for r in db.query("SELECT site_id FROM sites ORDER BY site_id")]
    run_after = time.time() + delay_s if delay_s else 0
    return [jobs.enqueue(KIND, job_key(s), site_id=s, payload={"site_id": s}, run_after=run_after) for s in sites]


def _on_crawl_batch(site_id: str = "", **_):
    """One apply ``CRAWL_DEBOUNCE_S`` after a crawl batch (an apply already queued keeps its time: same key = no-op)."""
    if site_id:
        schedule(site_id, delay_s=CRAWL_DEBOUNCE_S)


def delete_site(site_id: str) -> None:
    """The site is deleted: its own rules, drafts, list bookkeeping and state go (rules for every site stay)."""
    with db.transaction() as conn:
        for table in ("routing_rules", "routing_suggestions", "routing_lists", "routing_state"):
            conn.execute(f"DELETE FROM {table} WHERE site_id = ?", (site_id,))


def _auto_suggest_due(site_id: str) -> bool:
    site = db.query_one("SELECT status FROM sites WHERE site_id = ?", (site_id,))
    if site is None or site["status"] != "active":
        return False
    a3 = db.query_one("SELECT state FROM site_stages WHERE site_id = ? AND stage = 'A3'", (site_id,))
    if a3 is None or a3["state"] not in PASSED:
        return False
    if db.query_one("SELECT 1 FROM routing_state WHERE site_id = ? AND auto_suggest_at IS NOT NULL", (site_id,)):
        return False
    if not crawl_plan.k0_done(site_id):
        return False    # the highlights tier is still being read
    return db.query_one("SELECT 1 FROM source_origins WHERE site_id = ? LIMIT 1", (site_id,)) is not None


def _routing_job(job, ctx):
    site_id = (job.get("payload") or {}).get("site_id") or job.get("site_id")
    if not site_id:
        return jobs.Fail("payload.site_id missing")
    out = engine.apply(site_id)
    if _auto_suggest_due(site_id):
        db.execute("INSERT INTO routing_state (site_id, auto_suggest_at) VALUES (?, ?) ON CONFLICT(site_id) DO UPDATE "
                   "SET auto_suggest_at = excluded.auto_suggest_at", (site_id, int(time.time())))
        jobs.enqueue(SUGGEST_KIND, f"{SUGGEST_KIND}:{site_id}", site_id=site_id, payload={"site_id": site_id})
        out["auto_suggest"] = True
    return jobs.Done(out)


def _suggest_job(job, ctx):
    from ...core.llm import budget
    site_id = (job.get("payload") or {}).get("site_id") or job.get("site_id")
    ok, why = budget.allowed(site_id, background=True)
    if not ok:
        return jobs.Retry(3600, why)
    out = suggest.suggest(site_id, background=True)
    if out["error"]:
        return jobs.Done({"error": out["error"]})
    if out["drafts"]:
        notes.add("info", f"{site_id}: {len(out['drafts'])} yönlendirme önerisi hazır", site_id=site_id,
                  ref_kind="routing", ref_id=site_id)
    return jobs.Done({"drafts": len(out["drafts"]), "invalid": out["invalid"]})


def register() -> None:
    """Startup (``control.wiring``): job kinds + the ``crawl_batch_done`` subscription."""
    jobs.register(KIND, _routing_job)
    jobs.register(SUGGEST_KIND, _suggest_job, llm=True)
    events.subscribe("crawl_batch_done", _on_crawl_batch)


__all__ = ["engine", "rules", "suggest", "schedule", "register", "delete_site", "KIND", "SUGGEST_KIND"]
