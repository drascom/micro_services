"""Repair action (T14 item 1, K8): a diagnosis that points at a page type runs that stage again in REPAIR mode.

Preconditions: the LLM is healthy, the site's background budget allows a call, no stage job of the site is active and no other
repair of the site runs. Otherwise the problem waits in ``waiting_llm`` (``next_at`` = budget renewal / a few minutes) and
:func:`tick` (job ``repair_tick``) takes it on again. At most :data:`MAX_LLM_RUNS` repair runs that used the LLM per problem,
then ``waiting_user``.

The stage run (``stages.actions.rerun(..., repair=True)``) discovers first (an LLM-free fix is used as is), measures the broken
samples + two good ones and passes only when the broken ones pass and the good ones did not break. Its end comes back through
``engine.RESULT_HOOKS`` (:func:`on_stage_result`):

- ``passed*`` + ``repair_autoapply``: a new ``site_versions`` row (``created_by=repair``) is already active -> ``info`` admin note,
  problem ``verifying`` (the problem core's measurement decides ``resolved``);
- ``passed*`` without autoapply: the measured patch waits in ``evidence.repair.pending`` (never in the draft, so a later
  normal stage pass cannot commit it), problem ``waiting_user`` + ``action`` note (:func:`apply_pending`);
- ``needs_human``: problem ``waiting_user``; the question card is the stage card (a hint runs the repair again, :func:`hint`);
- ``engine_gap``: a ``capability_gaps`` record, problem ``waiting_capability``.

The repair's own state lives in the problem's ``evidence.repair`` (``status`` running|done, stage, run, samples, result).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional

from ... import db, settings
from ...core.llm import budget as llm_budget, health as llm_health
from .. import jobs, notes
from ..jobs import store as job_store
from ..sitemap import store as map_store
from ..sitemap.schema import STAGE_PAGE
from . import diagnose as diag, gaps, notify, store

log = logging.getLogger("diziflix.problems")

MAX_LLM_RUNS = 2
BUSY_RETRY_S = 300
CLAIM_GRACE_S = 120   # a repair claimed but not yet queued as a stage job is not settled by ``tick`` before this
LLM_DOWN_RETRY_S = 600
GOOD_SAMPLES = 2
MAX_BROKEN = 2
STAGE_OF = {page: stage for stage, page in STAGE_PAGE.items()}
PASSED = ("passed", "passed_warn")

#: is the LLM usable? (tests replace it; ``core.llm.health`` caches 60 s)
LLM_OK: Callable[[], bool] = lambda: bool(llm_health.check().get("ok"))   # noqa: E731


def stage_for(page_type: str) -> Optional[str]:
    return STAGE_OF.get(page_type or "")


def info(problem: dict) -> dict:
    return dict((problem.get("evidence") or {}).get("repair") or {})


def _save(problem_id: int, **fields: Any) -> dict:
    row = store.row(problem_id) or {}
    ev = dict(row.get("evidence") or {})
    ev["repair"] = {**(ev.get("repair") or {}), **fields}
    store.update(problem_id, evidence=ev)
    return ev["repair"]


def running(problem: dict) -> bool:
    return info(problem).get("status") == "running"


def repaired(problem: dict) -> bool:
    """The repair stage's measurement passed (it is the system measurement of a site problem without children)."""
    rep = info(problem)
    return rep.get("status") == "done" and rep.get("result") in PASSED and rep.get("applied") is not False


def _next_midnight() -> int:
    lt = time.localtime(store.now())
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 5, 0, 0, -1)))


def _label(site_id: str, stage_id: str) -> str:
    from ..stages import engine
    title = getattr(engine.STAGES.get(stage_id), "title", "") or stage_id
    return f"{site_id} · {stage_id} {title}"


# --------------------------------------------------------------------------- samples

def _rows_urls(problem: dict, site_id: str, page_type: str) -> list[str]:
    if page_type == "title":
        rows = db.query("SELECT source_url FROM source_items WHERE site_id=? AND canonical_id=? AND source_url IS NOT NULL",
                        (site_id, problem.get("canonical_id") or ""))
        return [r["source_url"] for r in rows]
    return [r["locator"] for r in diag.rows_of(problem) if r["source"] == site_id and r["resolver"] == "page"]


def samples_for(problem: dict, site_id: str, page_type: str) -> dict:
    """``{broken: [...], good: [...]}``: the problem's own addresses (its rows / children's rows / the crawl's evidence) and up
    to two addresses the stage measured fine before (``meta.<type>.samples``)."""
    broken: list[str] = []
    if problem.get("scope") in ("episode", "title"):
        broken += _rows_urls(problem, site_id, page_type)
    else:
        for child in store.list({"parent_id": problem["id"]}, limit=20):
            broken += _rows_urls(child, site_id, page_type)
        ev = problem.get("evidence") or {}
        broken += [p.get("url") for p in (ev.get("pages") or []) if isinstance(p, dict) and p.get("url")]
        broken += [u for u in ev.get("urls") or [] if isinstance(u, str)]
    broken = list(dict.fromkeys(u for u in broken if u))[:MAX_BROKEN]
    site_map = map_store.active(site_id) or {}
    meta = (((site_map.get("meta") or {}).get(page_type) or {}).get("samples")) or []
    # the good samples least alike the broken ones first: a rule (and its address pattern) measured on varied pages generalises
    rest = [u for u in meta if u not in broken]
    rest.sort(key=lambda u: max((len(os.path.commonprefix([u, b])) for b in broken), default=0))
    return {"broken": broken, "good": rest[:GOOD_SAMPLES]}


# --------------------------------------------------------------------------- waiting states

def _park(problem: dict) -> None:
    if problem.get("scope") == "site":
        from . import run
        run.park_children(problem)


def _move(problem: dict, to_state: str, kind: str, why: str, **fields: Any) -> dict:
    """``transition``; already in ``to_state`` (a waiting problem that still waits) -> fields + one event only."""
    if problem["state"] == to_state:
        if fields:
            store.update(problem["id"], **fields)
        store.event(problem["id"], "system", kind, why)
        return store.row(problem["id"])
    return store.transition(problem["id"], to_state, actor="system", kind=kind, note=why, **fields)


def _wait_llm(problem: dict, why: str, next_at: int) -> Any:
    done = _move(problem, "waiting_llm", "waiting_llm", why, next_at=int(next_at))
    _save(problem["id"], status="waiting", why=why)
    return jobs.Done({"state": done["state"], "next_at": int(next_at)})


def _wait_user(problem: dict, why: str, stage_id: str = "") -> Any:
    done = _move(problem, "waiting_user", "needs_admin", why)
    _park(done)
    site = problem.get("site_id") or ""
    where = _label(site, stage_id) if stage_id else f"{site or '-'} / {problem.get('page_type') or '-'}"
    notes.add("action", f"Sorun #{problem['id']} ({problem.get('class') or '-'}, {where}): {why}"[:500],
              site_id=site or None, ref_kind="problem", ref_id=str(problem["id"]))
    return jobs.Done({"state": done["state"]})


def _site_busy(site_id: str, problem_id: int) -> str:
    rows = db.query("SELECT key FROM jobs WHERE kind='stage' AND site_id=? AND state IN ('queued','running','waiting')", (site_id,))
    if rows:
        return f"site için aşama işi sürüyor ({rows[0]['key']})"
    for row in db.query("SELECT * FROM problems WHERE site_id=? AND state='acting' AND id!=?", (site_id, int(problem_id))):
        if running(store.to_dict(row)):
            return f"sitede başka bir onarım sürüyor (#{row['id']})"
    return ""


# --------------------------------------------------------------------------- start

def start(problem: dict, klass: str, page_type: str, *, hint: str = "", admin: bool = False) -> Any:
    """Run the stage of ``page_type`` in repair mode for ``problem`` (state ``acting``, ``waiting_llm`` or ``waiting_user``).
    Returns the problem job's outcome: ``Wait`` while the stage runs, ``Done`` when the problem has to wait."""
    from ..stages import actions as stage_actions
    site_id = problem.get("site_id") or ""
    stage_id = stage_for(page_type)
    if not stage_id or not site_id:
        return _wait_user(problem, f"{klass}: onarılacak aşama belirlenemedi")
    site = map_store.get(site_id)
    if site is None or site["status"] != "active":
        return _wait_user(problem, f"{klass}: site etkin değil, onarım yapılamaz", stage_id)
    if not admin and int(problem.get("llm_runs") or 0) >= MAX_LLM_RUNS:
        return _wait_user(problem, f"{MAX_LLM_RUNS} LLM onarım koşusu sonuç vermedi; ipucu ya da karar gerekiyor", stage_id)
    if not LLM_OK():
        return _wait_llm(problem, "LLM şu an kullanılamıyor", store.now() + LLM_DOWN_RETRY_S)
    if not admin:
        allowed, why = llm_budget.allowed(site_id, background=True)
        if not allowed:
            return _wait_llm(problem, f"LLM bütçesi doldu ({why})", _next_midnight())
    samples = samples_for(problem, site_id, page_type)
    # "one repair per site": check + claim in ONE transaction (two problems of a site never both pass the check)
    with db.transaction():
        busy = _site_busy(site_id, problem["id"])
        if not busy:
            if problem["state"] != "acting":
                problem = store.transition(problem["id"], "acting", actor="admin" if admin else "system", kind="repair",
                                           note=f"{stage_id} onarımı")
            _save(problem["id"], status="running", stage=stage_id, page_type=page_type, klass=klass, run=None,
                  started_at=store.now(), claimed=True)
            problem = store.row(problem["id"])
    if busy:
        return _wait_llm(problem, busy, store.now() + BUSY_RETRY_S)
    ev = problem.get("evidence") or {}
    evidence = {"class": klass, "diagnosis": ev.get("diagnosis"), "reasons": ev.get("reasons"), "pages": ev.get("pages")}
    try:
        got = stage_actions.rerun(site_id, stage_id, repair=True, evidence=evidence, samples=samples, problem_id=problem["id"],
                                  klass=klass, hint=hint, admin=admin)
    except Exception as exc:
        log.warning("repair of problem %s not queued", problem["id"], exc_info=True)
        _save(problem["id"], status="failed", claimed=False)
        return _wait_user(problem, f"onarım başlatılamadı: {type(exc).__name__}", stage_id)
    if not got.get("queued"):
        return _wait_llm(problem, "aşama işi zaten sırada", store.now() + BUSY_RETRY_S)
    _save(problem["id"], status="running", stage=stage_id, page_type=page_type, klass=klass, run=got.get("run"),
          started_at=store.now(), broken=samples["broken"], good=samples["good"], result=None, applied=None, version=None,
          pending_apply=False, pending=None, claimed=False)
    store.event(problem["id"], "system", "repair_started", f"{stage_id} onarım kipinde yeniden çalışıyor"
                + (" (ipucuyla)" if hint else ""), {"stage": stage_id, "samples": samples})
    return jobs.Wait(f"repair {stage_id}")


def _ensure_job(problem: dict, method: Optional[str] = None) -> None:
    """The problem's job: a waiting one is woken (its payload gets ``method``); none -> a new one."""
    from . import run
    key = run.job_key(problem["id"])
    job = job_store.get_by_key(key)
    if job is not None and job["state"] == "waiting":
        if method:
            job_store.update_payload(job["id"], {**(job.get("payload") or {}), "method": method})
        jobs.wake(key)
        return
    if job is not None and job["state"] in ("queued", "running"):
        return
    rep = info(problem)
    jobs.enqueue(run.KIND, key, site_id=problem.get("site_id"),
                 payload={"problem_id": problem["id"], "method": method,
                          "verdict": {"class": problem.get("class"), "page_type": rep.get("page_type") or problem.get("page_type"),
                                      "action": "repair", "layer": problem.get("layer")}, "done": []})


# --------------------------------------------------------------------------- results

def settle(problem: dict, result: str, extra: dict) -> str:
    """Apply a repair run's result to its problem. Returns the problem's new state."""
    rep = info(problem)
    stage_id, site_id = rep.get("stage") or "", problem.get("site_id") or ""
    llm_used = bool(extra.get("llm_used"))
    if llm_used:
        store.update(problem["id"], llm_runs=int(problem.get("llm_runs") or 0) + 1)
    applied = extra.get("applied", True) is not False
    pending = extra.get("pending") if result in PASSED and not applied else None
    _save(problem["id"], status="done", result=result, version=extra.get("version"), applied=applied if result in PASSED else None,
          pending_apply=bool(pending), pending=pending, llm_used=llm_used)
    store.event(problem["id"], "llm" if llm_used else "system", "repair_result", f"{stage_id}: {result}",
                {"version": extra.get("version"), "applied": applied, "note": extra.get("note")})
    if result in PASSED and applied:
        version = extra.get("version")
        how = "LLM düzeltmesi doğrulandı ve uygulandı" if llm_used else "düzeltme keşifle bulundu, doğrulandı ve uygulandı"
        notes.add("info", f"{_label(site_id, stage_id)}: {how} (sürüm {version})", site_id=site_id,
                  ref_kind="problem", ref_id=str(problem["id"]))
        store.transition(problem["id"], "verifying", actor="llm" if llm_used else "system", kind="repaired",
                         note=f"sürüm {version}", data={"version": version})
        _ensure_job(store.row(problem["id"]), method="heal")
        return "verifying"
    if result in PASSED and not pending:
        _wait_user(store.row(problem["id"]), "onarım ölçümü geçti ama bekleyen yama kayboldu; aşamayı yeniden çalıştır", stage_id)
        _ensure_job(store.row(problem["id"]))
        return "waiting_user"
    if result in PASSED:
        notes.add("action", f"{_label(site_id, stage_id)}: onarım ölçümü geçti, yama onayını bekliyor (sorun #{problem['id']}; "
                  "kendiliğinden uygulama kapalı)", site_id=site_id, ref_kind="problem", ref_id=str(problem["id"]))
        done = store.transition(problem["id"], "waiting_user", actor="system", kind="repair_pending", note="taslak onay bekliyor")
        _park(done)
        _ensure_job(done)
        return "waiting_user"
    if result == "engine_gap":
        gap = gaps.from_stage(site_id, stage_id, extra.get("evidence"), extra.get("note") or "")
        done = store.transition(problem["id"], "waiting_capability", actor="system", kind="capability", note=gap["key"],
                                gap_id=gap["id"])
        notify.send(done["id"])
        _park(done)
        _ensure_job(done)
        return "waiting_capability"
    if extra.get("budget"):
        _wait_llm(problem, "LLM bütçesi doldu", _next_midnight())
        _ensure_job(store.row(problem["id"]))
        return "waiting_llm"
    _wait_user(store.row(problem["id"]), f"onarım ölçümü geçmedi ({str(extra.get('note') or result)[:160]}); "
               "aşama kartına ipucu yaz", stage_id)
    _ensure_job(store.row(problem["id"]))
    return "waiting_user"


def on_stage_result(site_id: str, stage_id: str, result: str, extra: dict) -> None:
    """``engine.RESULT_HOOKS``: a repair run ended -> its problem; any other run: gaps + problems the admin fixed via the card."""
    rep = extra.get("repair") or {}
    pid = rep.get("problem_id")
    if pid:
        problem = store.row(pid)
        if problem is not None and problem["state"] == "acting" and running(problem):
            settle(problem, result, extra)
        return
    if result == "engine_gap":
        gaps.from_stage(site_id, stage_id, extra.get("evidence"), extra.get("note") or "")
    if result in PASSED and extra.get("applied", True) is not False:
        for row in db.query("SELECT * FROM problems WHERE site_id=? AND state='waiting_user'", (site_id,)):
            problem = store.to_dict(row)
            if info(problem).get("stage") != stage_id:
                continue
            _save(problem["id"], status="done", result=result, version=extra.get("version"), applied=True, pending_apply=False)
            store.transition(problem["id"], "verifying", actor="admin", kind="stage_passed",
                             note=f"{stage_id} aşama kartından geçti (sürüm {extra.get('version')})")
            _ensure_job(store.row(problem["id"]), method="heal")


def wait(problem: dict) -> Any:
    """The problem job ran while its repair is marked running: the stage job still active -> ``Wait``; it is gone (result
    lost in a crash) -> settle from the stage row. Returns None when the problem moved on (the caller re-reads it)."""
    from ..stages import engine, state as stage_state
    rep = info(problem)
    site_id, stage_id = problem.get("site_id") or "", rep.get("stage") or ""
    job = job_store.get_by_key(engine.job_key(site_id, stage_id))
    if job is not None and job["state"] in job_store.ACTIVE_STATES:
        return jobs.Wait(f"repair {stage_id}")
    row = stage_state.get(site_id, stage_id) or {}
    result = row.get("state") or "needs_human"
    if result not in (*PASSED, "engine_gap"):
        result = "needs_human"
    site = map_store.get(site_id) or {}
    applied = result not in PASSED or bool(settings.get("repair_autoapply"))
    ev = row.get("evidence") or {}
    new = settle(problem, result, {"applied": applied, "version": site.get("active_version"), "evidence": ev,
                                   "note": "aşama sonucu kurtarıldı", "llm_used": bool(ev.get("llm")),
                                   "pending": ev.get("pending") if result in PASSED and not applied else None})
    return None if new == "verifying" else jobs.Done({"state": new})


# --------------------------------------------------------------------------- admin

def hint(problem_id: int, text: str) -> dict:
    """The question card's hint: the repair runs again with it (admin-started: no budget / run cap), problem -> ``acting``."""
    problem = store.row(problem_id)
    if problem is None:
        raise KeyError(problem_id)
    text = str(text or "").strip()
    if not text:
        raise ValueError("ipucu boş")
    if problem["state"] not in ("waiting_user", "waiting_llm"):
        raise ValueError("ipucu yalnız bekleyen soruna verilir")
    rep = info(problem)
    page_type = rep.get("page_type") or problem.get("page_type") or ""
    store.event(problem_id, "admin", "hint", text[:300])
    out = start(problem, rep.get("klass") or problem.get("class") or "", page_type, hint=text, admin=True)
    problem = store.row(problem_id)
    if problem["state"] == "acting":
        _ensure_job(problem)
    return {"state": problem["state"], "outcome": type(out).__name__}


def apply_pending(problem_id: int) -> dict:
    """``repair_autoapply`` off: the admin applies the measured patch (kept in ``evidence.repair.pending``, never in the
    draft) -> draft + provider recipes + new version (``repair``), problem ``verifying``."""
    from ..crawl import plan as crawl_plan, run as crawl_run
    from ..stages import engine
    problem = store.row(problem_id)
    if problem is None:
        raise KeyError(problem_id)
    rep = info(problem)
    if problem["state"] != "waiting_user" or not rep.get("pending_apply") or not rep.get("pending"):
        raise ValueError("uygulanmayı bekleyen onarım yok")
    site_id, stage_id = problem["site_id"], rep.get("stage") or ""
    version = engine.apply_pending(site_id, stage_id, rep["pending"],
                                   note=f"sorun #{problem_id} {problem.get('class') or ''} (admin onayı)")
    try:
        if crawl_plan.stage_passed(site_id, stage_id):
            crawl_run.kick(site_id)
    except Exception:
        log.warning("crawl resume after apply failed", exc_info=True)
    _save(problem_id, pending_apply=False, pending=None, applied=True, version=version)
    notes.add("info", f"{_label(site_id, stage_id)}: onarım admin onayıyla uygulandı (sürüm {version})", site_id=site_id,
              ref_kind="problem", ref_id=str(problem_id))
    store.transition(problem_id, "verifying", actor="admin", kind="repair_applied", note=f"sürüm {version}")
    _ensure_job(store.row(problem_id), method="heal")
    return {"state": "verifying", "version": version}


def _restore_parts(site_id: str, stage_id: str, page_type: str, source_version: int) -> int:
    """Only the repaired part (``pages.<type>`` + ``meta.<type>``; A7 also ``gate``) of the ACTIVE map goes back to its
    content in ``source_version``; everything else stays. A new ``admin`` version."""
    old = map_store.load(map_store.export_yaml(site_id, source_version))
    data = map_store.active(site_id) or map_store.draft(site_id)
    for top, key in (("pages", page_type), ("meta", page_type)):
        if key in (old.get(top) or {}):
            data.setdefault(top, {})[key] = old[top][key]
        else:
            (data.get(top) or {}).pop(key, None)
    if stage_id == "A7":
        if "gate" in old:
            data["gate"] = old["gate"]
        else:
            data.pop("gate", None)
    with db.transaction():
        map_store.save_draft(site_id, data)
        return map_store.commit(site_id, created_by="admin", note=f"{stage_id} ({page_type}) v{source_version} içeriğine geri alındı")


def rollback(problem_id: int) -> dict:
    """Undo an applied repair: only the repaired page type goes back to the version before it (a new ``admin`` version; the
    other page types keep later changes); the problem reopens."""
    from . import run
    problem = store.row(problem_id)
    if problem is None:
        raise KeyError(problem_id)
    rep = info(problem)
    version = rep.get("version")
    if not rep.get("applied") or not version:
        raise ValueError("geri alınacak onarım sürümü yok")
    site_id = problem["site_id"]
    stage_id = rep.get("stage") or ""
    page_type = rep.get("page_type") or STAGE_PAGE.get(stage_id) or ""
    if not page_type:
        raise ValueError("onarılan sayfa türü bilinmiyor")
    older = [v["version"] for v in map_store.versions(site_id) if v["version"] < int(version)]
    if not older:
        raise ValueError("önceki sürüm yok")
    new = _restore_parts(site_id, stage_id, page_type, older[-1])
    _save(problem_id, applied=False, rolled_back_to=older[-1])
    store.event(problem_id, "admin", "rollback", f"v{version} {page_type} geri alındı -> v{new} (v{older[-1]} içeriği)")
    if problem["state"] not in ("open",):
        try:
            problem = store.transition(problem_id, "open", actor="admin", kind="reopened", note="onarım geri alındı",
                                       deadline_at=store.now() + int(settings.get("problem_deadline_s") or 900))
        except store.InvalidTransition:
            pass
    run.enqueue(store.row(problem_id), admin=True)
    return {"version": new, "restored": older[-1]}


# --------------------------------------------------------------------------- tick

def tick() -> dict:
    """Job ``repair_tick``: ``waiting_llm`` problems whose ``next_at`` came start again; a ``running`` repair whose stage job is
    gone is settled from the stage row."""
    from ..stages import engine
    out = {"resumed": 0, "settled": 0}
    for problem in store.list({"state": "waiting_llm"}, limit=200):
        if int(problem.get("next_at") or 0) > store.now():
            continue
        rep = info(problem)
        page_type = rep.get("page_type") or problem.get("page_type") or ""
        if not rep.get("klass") and not stage_for(page_type):
            continue   # a waiting_llm of another kind (not a repair)
        got = start(problem, rep.get("klass") or problem.get("class") or "", page_type)
        if isinstance(got, jobs.Wait):
            _ensure_job(store.row(problem["id"]))
            out["resumed"] += 1
    for problem in store.list({"state": "acting"}, limit=200):
        rep = info(problem)
        if not running(problem) or (rep.get("claimed") and store.now() - int(rep.get("started_at") or 0) < CLAIM_GRACE_S):
            continue   # a claim being turned into a stage job right now is not "lost"
        job = job_store.get_by_key(engine.job_key(problem.get("site_id") or "", info(problem).get("stage") or ""))
        if job is None or job["state"] not in job_store.ACTIVE_STATES:
            wait(problem)
            out["settled"] += 1
    return out


def tick_job(job, ctx):
    return jobs.Done(tick())
