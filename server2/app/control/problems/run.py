"""Job kind ``problem`` (``llm=False``, key ``problem:<id>``): ``open -> diagnosing -> acting -> verifying -> resolved``.

The problem's state (DB) says where it stands; ``ctx.checkpoint`` keeps the progress inside ``acting`` (actions already done),
so a job that died mid-way resumes where it stopped. A failed round: ``attempts+1``, back to ``open`` with ``next_at`` (1 min,
then 10 min); the 3rd failed round -> ``exhausted``. Only a measurement (:func:`actions.measure`) resolves.

Episode -> site escalation: 2 open episode/title problems with the same site + class + page type -> one site problem; both get
``parent_id`` and wait for it; when the site problem waits (user / capability) its children wait in the same state
(``park_children``); when it resolves its children are moved to ``verifying``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ... import db, settings
from ...core.catalog import write as catalog_write
from .. import jobs, notes
from . import actions, diagnose as diag, gaps, notify, repair, store

log = logging.getLogger("diziflix.problems")

KIND = "problem"
MAX_ATTEMPTS = 3
BACKOFF_S = (60, 600)
#: classes a site-wide fix (T14 repair) handles: two episodes with the same one = a site problem
ESCALATE = ("page_unreachable", "player_not_found", "unknown_host", "provider_changed", "stream_refused", "wrong_media")
TERMINAL = store.FINISHED
#: stop this early before the job's deadline (timeout 300 s): a step started later could outlive it and fail the job
DEADLINE_MARGIN_S = 60


def _out_of_time(ctx) -> bool:
    deadline = getattr(ctx, "deadline", None)
    return bool(deadline) and time.time() >= float(deadline) - DEADLINE_MARGIN_S


def _yield() -> Any:
    """The state is on disk (problem row + checkpoint): the next run continues where this one stopped; no attempt is used."""
    return jobs.Retry(0, "süre doldu, sonraki turda kaldığı yerden", yield_=True)


def job_key(problem_id: int) -> str:
    return f"problem:{int(problem_id)}"


def enqueue(problem: dict, *, run_after: float = 0, priority: int = 0, admin: bool = False) -> int:
    """``admin``: the admin started this round (retry / report / gap shipped): its repair run is outside the LLM budget."""
    return jobs.enqueue(KIND, job_key(problem["id"]), site_id=problem.get("site_id"),
                        payload={"problem_id": problem["id"], **({"admin": True} if admin else {})},
                        run_after=run_after, priority=priority)


def _base(problem: dict, ctx) -> dict:
    """A fresh checkpoint payload that keeps the round's ``admin`` flag (restart-safe)."""
    admin = bool(((getattr(ctx, "job", None) or {}).get("payload") or {}).get("admin"))
    return {"problem_id": problem["id"], **({"admin": True} if admin else {})}


def _deadline_s() -> int:
    return int(settings.get("problem_deadline_s") or 900)


# --------------------------------------------------------------------------- escalation

def _escalate(problem: dict, verdict: dict) -> Optional[dict]:
    """Link ``problem`` (and the other open problem with the same site/class/page type) to a site problem; returns it."""
    klass, site, page_type = verdict["class"], verdict.get("site_id"), verdict.get("page_type") or ""
    if klass not in ESCALATE or not site or problem.get("scope") not in ("episode", "title"):
        return None
    others = db.query("SELECT id FROM problems WHERE scope IN ('episode','title') AND site_id=? AND class=? AND COALESCE(page_type,'')=? "
                      f"AND id!=? AND state NOT IN ({','.join('?' * len(TERMINAL))})",
                      (site, klass, page_type, problem["id"], *TERMINAL))
    if not others:
        return None
    parent, created = store.open_or_get(
        store.site_fp(site, page_type, klass), scope="site", site_id=site, page_type=page_type, layer=verdict.get("layer"),
        host=verdict.get("host"), deadline_at=store.now() + _deadline_s(),
        evidence={"children_class": klass, "first": verdict.get("evidence")}, **{"class": klass})
    if not created and parent["state"] in TERMINAL:
        parent = store.transition(parent["id"], "open", actor="system", kind="reopened", note="aynı sınıfta yeni bölüm sorunu",
                                  attempts=int(parent["attempts"] or 0) + 1, deadline_at=store.now() + _deadline_s())
    for pid in [problem["id"], *[o["id"] for o in others]]:
        store.update(pid, parent_id=parent["id"])
        store.event(pid, "system", "linked", f"site sorunu #{parent['id']}")
    store.event(parent["id"], "system", "children", f"{len(others) + 1} bağlı sorun")
    enqueue(parent)
    return parent


def _parent_open(problem: dict) -> Optional[dict]:
    if not problem.get("parent_id"):
        return None
    parent = store.row(problem["parent_id"])
    return parent if parent and parent["state"] not in TERMINAL else None


def finish_children(parent: dict) -> None:
    """The site problem got an answer: resolved -> children re-measured (``verifying``); exhausted / closed -> same answer."""
    for child in store.list({"parent_id": parent["id"]}):
        if child["state"] in TERMINAL:
            continue
        try:
            if parent["state"] == "resolved":
                store.transition(child["id"], "verifying", actor="system", kind="parent_resolved", note=f"site sorunu #{parent['id']}")
                if not jobs.wake(job_key(child["id"])):
                    enqueue(child)
            elif parent["state"] in ("exhausted", "closed_not_public"):
                store.transition(child["id"], parent["state"], actor="system", kind="parent_closed", note=f"site sorunu #{parent['id']}")
                notify.send(child["id"])
        except store.InvalidTransition:
            log.info("child %s of %s left as is", child["id"], parent["id"])


def park_children(parent: dict) -> None:
    """The site problem waits (``waiting_user`` / ``waiting_capability``): its children wait in the same state instead of
    sitting in ``acting`` + a job ``Wait`` (no notification; the client's ``finder.state`` turns ``not_found``). Their jobs
    are woken and end ``Done`` on the waiting state; ``finish_children`` takes them on when the parent gets its answer."""
    for child in store.list({"parent_id": parent["id"]}):
        if child["state"] in TERMINAL or child["state"] == parent["state"]:
            continue
        try:
            store.transition(child["id"], parent["state"], actor="system", kind="parent_waiting",
                             note=f"site sorunu #{parent['id']}")
        except store.InvalidTransition:
            log.info("child %s of %s left as is", child["id"], parent["id"])
            continue
        jobs.wake(job_key(child["id"]))


# --------------------------------------------------------------------------- outcomes

def _resolve(problem: dict, method: str, site: Optional[str], note: str) -> Any:
    ev = dict(problem.get("evidence") or {})
    ev.update(method=method, found_site=site or problem.get("site_id") or "")
    done = store.transition(problem["id"], "resolved", actor="system", kind="resolved", note=note, resolution=method, evidence=ev)
    notify.send(done["id"])
    if done["scope"] == "site":
        finish_children(done)
    return jobs.Done({"state": "resolved", "method": method})


def _fail_round(problem: dict, ctx, why: str) -> Any:
    attempts = int(problem.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        done = store.transition(problem["id"], "exhausted", actor="system", kind="exhausted", note=why, attempts=attempts)
        notify.send(done["id"])
        if done["scope"] == "site":
            finish_children(done)
        return jobs.Done({"state": "exhausted"})
    wait_s = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
    store.transition(problem["id"], "open", actor="system", kind="round_failed", note=why, attempts=attempts,
                     next_at=store.now() + wait_s)
    ctx.checkpoint(0, {"problem_id": problem["id"]})
    return jobs.Retry(wait_s, why)


def _waiting_user(problem: dict, verdict: dict, why: str) -> Any:
    """The admin decides (a broken image, ...); repairs go through ``repair.start`` (T14)."""
    done = store.transition(problem["id"], "waiting_user", actor="system", kind="needs_repair", note=why)
    if done["scope"] == "site":
        park_children(done)
    where = f"{verdict.get('site_id') or problem.get('site_id') or '-'} / {verdict.get('page_type') or '-'}"
    notes.add("action", f"Sorun #{problem['id']} ({verdict['class']}, {where}): onarım gerekiyor ({why})",
              site_id=verdict.get("site_id") or problem.get("site_id"), ref_kind="problem", ref_id=str(problem["id"]))
    return jobs.Done({"state": done["state"], "class": verdict["class"]})


# --------------------------------------------------------------------------- steps

def _diagnosing(problem: dict, ctx) -> Any:
    verdict = diag.diagnose(problem)
    fields = {"class": verdict["class"], "layer": verdict["layer"], "page_type": verdict.get("page_type") or None,
              "evidence": {**(problem.get("evidence") or {}), "diagnosis": verdict.get("evidence"), "rows": verdict.get("rows")}}
    for key in ("site_id", "source_id", "host"):
        if verdict.get(key) and problem.get("scope") != "site":
            fields[key] = verdict[key]
    store.update(problem["id"], **fields)
    store.event(problem["id"], "system", "diagnosed", f"{verdict['class']} -> {verdict['action']}", verdict.get("evidence"))
    problem = store.row(problem["id"])
    action = verdict["action"]
    if action == "close_not_public":
        for rowinfo in verdict.get("close_rows") or []:
            try:
                catalog_write.mark_blocked(rowinfo["site"], rowinfo["url"], kind=rowinfo["kind"], reason=str(rowinfo["reason"])[:80],
                                           via="gate")
            except Exception:
                log.warning("mark_blocked failed for problem %s", problem["id"], exc_info=True)
        store.transition(problem["id"], "closed_not_public", actor="system", kind="closed", note=verdict["class"])
        notify.send(problem["id"])
        return jobs.Done({"state": "closed_not_public"})
    if action == "waiting_capability":
        gap = gaps.from_diagnosis(problem, verdict)   # T14: one capability_gaps row per key, the site joins its list
        done = store.transition(problem["id"], "waiting_capability", actor="system", kind="capability", note=gap["key"],
                                gap_id=gap["id"])
        notify.send(problem["id"])
        if done["scope"] == "site":
            park_children(done)
        return jobs.Done({"state": "waiting_capability"})
    if action == "admin":
        why = {"links_gone": "sitede kaybolan sayfalar (404/410); kaynakları kapatıldı, LLM onarımı gerekmez"}
        return _waiting_user(problem, verdict, why.get(verdict["class"], "afiş/görsel kırık"))
    parent = _escalate(problem, verdict)
    if parent is not None:
        store.transition(problem["id"], "acting", actor="system", kind="linked", note=f"site sorunu #{parent['id']} bekleniyor")
        return jobs.Wait(f"parent problem {parent['id']}")
    store.transition(problem["id"], "acting", actor="system", kind="act", note=action)
    ctx.checkpoint(2, {**_base(problem, ctx), "verdict": verdict, "done": []})
    return None


def _acting(problem: dict, ctx) -> Any:
    payload = ctx.job.get("payload") or {}
    if repair.running(problem):   # T14: the stage repairs in its own job; this one waits (a lost result is settled)
        return repair.wait(problem)
    verdict = payload.get("verdict")
    if not verdict:   # no checkpointed diagnosis (e.g. linked child woken without parent): diagnose again
        store.transition(problem["id"], "open", actor="system", kind="rediagnose")
        ctx.checkpoint(0, _base(problem, ctx))
        return None
    done: list = list(payload.get("done") or [])
    content = problem.get("scope") in ("episode", "title")
    plan = []
    if content and verdict["class"] != "stream_refused":   # the stream that failed on two engines is not "found" by a re-resolve
        plan.append("retry_sources")
    if verdict["action"] in actions.ACTIONS:
        plan.append(verdict["action"])
    if content:
        plan.append("search_elsewhere")
    found: Optional[dict] = None
    for name in plan:
        if name in [d["action"] for d in done]:
            step = next(d for d in done if d["action"] == name)
        else:
            if _out_of_time(ctx):
                return _yield()
            fn = actions.ACTIONS.get(name) or getattr(actions, name)
            step = fn(problem, verdict)
            store.event(problem["id"], "system", "step", step["note"], step)
            done.append(step)
            ctx.checkpoint(2, {**payload, "done": done})
        if step["ok"]:
            found = step
            break
    if found is not None:
        method = {"retry": "retry", "search": "search"}.get(found["name"], "heal")
        store.transition(problem["id"], "verifying", actor="system", kind="verify", note=found["action"])
        ctx.checkpoint(3, {**payload, "done": done, "method": method})
        return None
    if verdict["action"] == "repair" or verdict["class"] == "unknown_host":
        # T14: the stage of the page type runs in repair mode (unknown host whose dry run failed: A6, the provider stage)
        return repair.start(problem, verdict["class"], verdict.get("page_type") or problem.get("page_type") or "",
                            admin=bool(payload.get("admin")))
    store.transition(problem["id"], "verifying", actor="system", kind="verify", note="eylem sonrası ölçüm")
    ctx.checkpoint(3, {**payload, "done": done, "method": "retry"})
    return None


def _verifying(problem: dict, ctx) -> Any:
    payload = ctx.job.get("payload") or {}
    if problem["scope"] == "site":
        children = [c for c in store.list({"parent_id": problem["id"]}) if c["state"] not in TERMINAL]
        if not children and repair.repaired(problem):   # T14: a system problem: the repair's measurement is the measurement
            return _resolve(problem, payload.get("method") or "heal", problem.get("site_id"), "onarım ölçümü geçti")
        for child in children:
            got = actions.measure(child)
            if got["ok"]:
                return _resolve(problem, payload.get("method") or "heal", got.get("site"), got["note"])
        return _fail_round(problem, ctx, "bağlı bölümlerde ölçüm geçmedi")
    got = actions.measure(problem)
    store.event(problem["id"], "system", "measured", got["note"], {"ok": got["ok"], "tried": got.get("tried")})
    if got["ok"]:
        return _resolve(problem, payload.get("method") or "retry", got.get("site"), got["note"])
    return _fail_round(problem, ctx, got["note"])


def handle(job: dict, ctx) -> Any:
    pid = (job.get("payload") or {}).get("problem_id")
    problem = store.row(pid) if pid else None
    if problem is None:
        return jobs.Done({"state": "missing"})
    for _ in range(6):   # open -> diagnosing -> acting -> verifying within one run
        state = problem["state"]
        if state in TERMINAL or state.startswith("waiting_"):
            return jobs.Done({"state": state})
        if _parent_open(problem) and state != "verifying":
            return jobs.Wait(f"parent problem {problem['parent_id']}")
        if _out_of_time(ctx):
            return _yield()
        if state == "open":
            store.transition(problem["id"], "diagnosing", actor="system", kind="diagnose")
            ctx.checkpoint(1, _base(problem, ctx))
            outcome = None
        elif state == "diagnosing":
            outcome = _diagnosing(problem, ctx)
        elif state == "acting":
            outcome = _acting(problem, ctx)
        else:
            outcome = _verifying(problem, ctx)
        if outcome is not None:
            return outcome
        problem = store.row(problem["id"])
    return jobs.Retry(5, "too many steps in one run")


def on_fail(job: dict, error: str) -> None:
    """The job itself gave up (crashes / timeouts): the problem is exhausted and its watchers hear it."""
    pid = (job.get("payload") or {}).get("problem_id")
    problem = store.row(pid) if pid else None
    if problem is None or problem["state"] in TERMINAL:
        return
    try:
        store.transition(problem["id"], "exhausted", actor="system", kind="job_failed", note=str(error)[:200])
        notify.send(problem["id"])
    except store.InvalidTransition:
        pass


def recover_missing() -> int:
    """Startup / scheduler: a working problem whose job row is not active (never queued, lost) is queued again."""
    n = 0
    rows = db.query(f"SELECT p.* FROM problems p LEFT JOIN jobs j ON j.key = 'problem:' || p.id AND j.state IN "
                    f"('queued','running','waiting') WHERE p.state IN ({','.join('?' * len(store.WORKING))}) AND j.id IS NULL",
                    store.WORKING)
    for r in rows:
        problem = store.to_dict(r)
        enqueue(problem, run_after=max(0, int(problem.get("next_at") or 0)))
        n += 1
    return n
