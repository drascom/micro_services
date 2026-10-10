"""Stage engine (README §5.6): job kind ``stage`` (``llm=True``), key ``stage:<site>:<Ax>``. Steps (``ctx.checkpoint``):

0 start -> 1 ``discover`` (<= 30 s; sample pages go to ``core.fetch.pages``) -> 2 ``verify(discovery)``; passed -> finish.
3 prompt + ``core.llm.complete`` (120 s) + ``extract_yaml`` + scope check on a COPY of the draft -> 4 ``verify(llm patch)``;
failed -> back to 3 with the measurement in the next prompt; at most ``MAX_LLM_ROUNDS``. Result: ``passed`` /
``passed_warn`` (patch + meta written to the draft, committed when the site is active) / ``needs_human`` / ``engine_gap``.

Round 1 goes with ``session=<site>:<stage>:<run>`` and the full prompt; round 2 sends only the new measurement into the
same session (full prompt without a session when it was lost). The session is ended when the run finishes.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import sqlite3
import time
from typing import Any, Callable, Optional

from ... import db, settings
from ...core import fetch as core_fetch
from ...core.fetch import pages as page_store
from ...core.llm import budget as llm_budget, client as llm_client, parse as llm_parse
from .. import jobs, notes
from ..sitemap import adapters, schema as map_schema, site_notes, store as map_store
from . import state
from .a1_home import HomeStage
from .a2_listing import ListingStage
from .a3_title import TitleStage
from .a4_episodes import EpisodesStage
from .a5_player import PlayerStage
from .a6_sources import SourcesStage
from .a7_search_gate import SearchGateStage
from .base import Finding, Measurement, Stage, StageCtx

log = logging.getLogger("diziflix.stages")

KIND = "stage"
PAUSED = "site paused"       # Wait reason while the site is paused; resume wakes these jobs
DISCOVER_S = 30
LLM_TIMEOUT_S = 120
MAX_LLM_ROUNDS = 2
PAGE_CACHE_S = 3600            # a stage run (and its restart) reuses the pages it fetched
FETCH_TIMEOUT_S = 20
JOB_TIMEOUT_S = 600
#: stages that must pass before a draft site becomes active (README §5.6: A1 + A3)
ACTIVATION = ("A1", "A3")
#: A1 review (T17): a passing A1 with sections / menu links the admin has not labelled yet waits in ``review``
#: (tests of the older flows switch it off)
A1_REVIEW = True
#: LLM-first (T23): a stage with ``reads_first`` starts with the LLM's own read of its pages (purpose + page map, one call
#: + format fix); the LLM rules and the LLM-free discovery are measured alike, the measurement is the referee. Repair runs
#: keep the old order. (tests of the older flows switch it off)
LLM_FIRST = True

#: injectable (tests): ``FETCHER(url, *, site, page_type, timeout) -> Page`` and the LLM call
FETCHER: Callable[..., Any] = core_fetch.get
COMPLETE: Callable[..., Any] = llm_client.complete

STAGES: dict[str, Stage] = {}

#: T14: ``fn(site_id, stage_id, result, info)`` called when a run ends (``info``: ``repair`` payload, ``version``, ``applied``,
#: ``source``, ``llm_used``, ``budget``, ``gap``, ``evidence``). Bound by ``control.problems`` (stages never import it).
RESULT_HOOKS: list = []


def _report(site_id: str, stage_id: str, result: str, p: dict, **info: Any) -> None:
    info.setdefault("repair", p.get("repair"))
    info.setdefault("llm_used", bool(p.get("llm_rounds")))
    for fn in list(RESULT_HOOKS):
        try:
            fn(site_id, stage_id, result, info)
        except Exception:   # a listener never takes the stage result back
            log.exception("stage result hook failed")


def repair_ok(p: dict, m: Measurement) -> bool:
    """Repair mode (T14): the broken samples pass now AND every good sample that was measured still passes (regression)."""
    rep = p.get("repair") or {}
    if not rep:
        return True
    seen = {s.get("url"): s for s in m.samples or [] if isinstance(s, dict)}
    for url in rep.get("broken") or []:
        if not (seen.get(url) or {}).get("ok"):
            return False
    return all(seen[u].get("ok") for u in rep.get("good") or [] if u in seen)


def _passes(p: dict, m: Measurement) -> bool:
    return bool(m.ok) and repair_ok(p, m)


def add_stage(stage: Stage) -> None:
    """Register a stage (T09 adds A4-A7 the same way)."""
    STAGES[stage.id] = stage


for _stage in (HomeStage(), ListingStage(), TitleStage(), EpisodesStage(), PlayerStage(), SourcesStage(), SearchGateStage()):
    add_stage(_stage)


def job_key(site_id: str, stage_id: str) -> str:
    return f"stage:{site_id}:{stage_id}"


def session_key(site_id: str, stage_id: str, run: int) -> str:
    return f"{site_id}:{stage_id}:{run}"


def stage_of(stage_id: str) -> Stage:
    stage = STAGES.get(stage_id)
    if stage is None:
        raise LookupError(f"unknown stage {stage_id!r}")
    return stage


# ---------------------------------------------------------------------------------------------- chain (T18)

def chain() -> list[str]:
    """The A1-A7 chain (user decision): a stage starts only when the one before it is passed*/skipped."""
    return list(STAGES)


def prev_of(stage_id: str) -> Optional[str]:
    order = chain()
    i = order.index(stage_id) if stage_id in order else -1
    return order[i - 1] if i > 0 else None


def after(stage_id: str) -> list[str]:
    order = chain()
    return order[order.index(stage_id) + 1:] if stage_id in order else []


def before(stage_id: str) -> list[str]:
    order = chain()
    return order[:order.index(stage_id)] if stage_id in order else []


def upstream_sig(site_id: str, stage_id: str) -> str:
    """Hash of what the stages before ``stage_id`` handed over (their handoffs): a running stage whose input changed under
    it starts its discovery again."""
    earlier = set(before(stage_id))
    if not earlier:
        return "-"
    # the handoff column holds the last PASSED result (a re-running upstream stage keeps it until it passes again)
    rows = db.query("SELECT stage, handoff FROM site_stages WHERE site_id=? ORDER BY stage", (site_id,))
    body = "|".join(f"{r['stage']}:{r['handoff']}" for r in rows if r["stage"] in earlier)
    return hashlib.sha1(body.encode()).hexdigest()[:16]


def _priority(stage_id: str) -> int:
    order = list(STAGES)
    return 100 - (order.index(stage_id) if stage_id in order else 50)


# ---------------------------------------------------------------------------------------------- fetch

def fetch_fn(site_id: str, site_ctx, deadline: float = 0.0, fresh_at: float = 0.0) -> Callable[[str, str], Any]:
    """``fetch(url, page_type) -> Page``: the stored copy when fresh (a restarted run does not fetch again), else
    ``FETCHER`` (``core.fetch.get``: netguard, robots, limiter) and the page is stored. ``fresh_at`` (T18: an admin rerun /
    hint / sample): pages stored before that moment are not used, so the run's first fetch of a page goes to the site."""
    def fetch(url: str, page_type: str = "page"):
        max_age = PAGE_CACHE_S if not fresh_at else max(0.0, min(PAGE_CACHE_S, time.time() - fresh_at))
        hit = page_store.load(site_id, url, max_age_s=max_age) if max_age > 0 else None
        if hit is not None:
            return hit
        timeout = FETCH_TIMEOUT_S
        if deadline:
            timeout = min(timeout, max(0.5, deadline - time.time()))
        page = FETCHER(url, site=site_ctx, page_type=page_type, timeout=timeout)
        page_store.save(site_id, page_type, page)
        return page
    return fetch


MAX_HINTS = 5


def stage_hints(site_id: str, stage_id: str, hint: str = "") -> list[str]:
    """This run's hint + the stage's earlier admin hints (newest first; action lines such as "Örnek adres: ..." are not hints)."""
    out = [hint] if hint else []
    for msg in reversed(state.messages(site_id, stage_id, role="user", limit=30)):
        text = str(msg.get("text") or "")
        if text and not text.startswith(state.ACTION_TEXTS) and text not in out:
            out.append(text)
    return out[:MAX_HINTS]


def handoff_in(site_id: str, stage_id: str) -> dict:
    """The previous chain stage's handoff (``{from, urls, ...}``), ``{}`` when there is none."""
    prev = prev_of(stage_id)
    if not prev:
        return {}
    row = state.get(site_id, prev) or {}
    if row.get("state") not in state.PASSED or not isinstance(row.get("handoff"), dict):
        return {}
    return {"from": prev, **row["handoff"]}


def build_ctx(site_id: str, stage: Stage, *, hint: str = "", deadline: float = 0.0, first: Optional[list] = None,
              repair: bool = False, fresh_at: float = 0.0, downstream: Optional[dict] = None) -> StageCtx:
    """``first``: sample addresses measured before the user's (repair mode: the broken + good samples). Samples = first +
    the admin's own + the previous stage's handoff (T18), without the excluded ones."""
    site_map = map_store.draft(site_id)
    adapters.note_browser_refused(site_map)   # fetch.mode browser while unguarded -> http (+ admin note)
    site_ctx = adapters.site_ctx(site_map)
    row = state.get(site_id, stage.id) or {}
    excluded = list(row.get("excluded_samples") or [])
    pinned = list(row.get("user_samples") or [])
    handoff = handoff_in(site_id, stage.id)
    passed_on = list(handoff.get("urls") or []) if stage.consumes_handoff and not repair else []
    ctx = StageCtx(site_id=site_id, map=site_map, site_ctx=site_ctx, hint=hint,
                   samples=[], fetch=fetch_fn(site_id, site_ctx, deadline, fresh_at),
                   deadline=deadline, repair=repair, excluded=excluded, hints=stage_hints(site_id, stage.id, hint),
                   pinned=pinned, handoff=handoff, note=site_notes.get(site_id),
                   own_only=bool(row.get("own_samples_only")) and not repair, downstream=dict(downstream or {}))
    ctx.samples = [u for u in dict.fromkeys(list(first or []) + pinned + passed_on) if ctx.allowed(u)]
    return ctx


# ---------------------------------------------------------------------------------------------- queue

def enqueue(site_id: str, stage_id: str, *, hint: str = "", created_by: Optional[str] = None,
            background: bool = False, failed_patch: Optional[dict] = None, repair: Optional[dict] = None,
            fresh: bool = False, trigger: Optional[str] = None, downstream: Optional[dict] = None) -> dict:
    """Queue a NEW run of the stage. ``trigger`` ``admin`` (default: an admin action) | ``system`` (budget-limited): admin
    runs are never blocked by the daily LLM budget and do not use it up; the trigger rides in the job payload (checkpoint ->
    restart-safe) and passes on to the chain's followers (``queue_followers``) until a system-started run replaces it.
    ``background=True`` = ``trigger="system"``; a repair run is admin only when ``repair["admin"]``. While a run is already queued/running nothing is queued (``queued: False``).
    ``failed_patch`` (a hand-back from a later stage, with a hint): the patch that already failed downstream; when the
    run's discovery produces it again the run goes on to the LLM round (with the hint) instead of passing."""
    stage_of(stage_id)
    key = job_key(site_id, stage_id)
    current = jobs.store.get_by_key(key)
    if current is not None and current["state"] in ("queued", "running", "waiting"):
        return {"queued": False, "job_id": current["id"], "reason": current["state"]}
    normalize_chain(site_id)
    b = blocker(site_id, stage_id)
    if b is not None:   # the chain: an earlier stage is unresolved -> this one waits (it runs when its turn comes)
        hold(site_id, stage_id, b)
        return {"queued": False, "reason": "waiting", "waiting_for": b}
    run = state.next_run(site_id, stage_id)
    state.update(site_id, stage_id, state="pending", round=0)
    hold_after(site_id, stage_id)   # every later stage waits for this run (at most one stage asks the admin at a time)
    if repair:
        trigger = "admin" if repair.get("admin") else "system"
    elif trigger not in TRIGGERS:
        trigger = "system" if background else "admin"
    payload = {"site_id": site_id, "stage": stage_id, "run": run, "hint": hint or "",
               "created_by": created_by, "trigger": trigger, "background": trigger == "system"}
    if failed_patch:
        payload["failed_patch"] = failed_patch
    if downstream:   # a hand-back: the later stage's measurement goes to the LLM as "A6 sonucu" (never as an admin hint)
        payload["downstream"] = downstream
    if fresh:   # T18: an admin rerun / hint / sample: the site may have changed, the page cache is bypassed
        payload["fresh_at"] = time.time()
    if repair:   # T14 repair mode: ``{problem_id, class, evidence, broken[], good[]}``; budget only when not admin-started
        payload.update(repair=repair, created_by="repair")
    job_id = jobs.enqueue(KIND, key, site_id=site_id, payload=payload, priority=_priority(stage_id))
    return {"queued": True, "job_id": job_id, "run": run}


#: a stage that waits for an earlier one: ``held_by`` = that stage, ``held_prev`` = its own state before the hold
#: (restored when the earlier stage passes again with the SAME result; ``None`` = it runs again)
RESTORABLE = ("passed", "passed_warn", "needs_human", "engine_gap", "review")


def blocker(site_id: str, stage_id: str, rows: Optional[dict] = None) -> Optional[str]:
    """The first earlier chain stage that is not done (passed* / skipped): while there is one, ``stage_id`` may not run
    and may not hold a result (``None`` = free to run)."""
    rows = rows if rows is not None else state.all_for(site_id)
    for sid in before(stage_id):
        row = rows.get(sid)
        if row is not None and row.get("state") not in state.DONE:   # no row: a site the stages never ran on
            return sid
    return None


def _rerunnable(row: Optional[dict]) -> bool:
    """A pending stage that never ran, or was put back by an earlier stage (``reset_by`` / ``held_by``)."""
    if not row:
        return True
    ev = row.get("evidence") or {}
    return row["state"] == "pending" and (not row["runs"] or bool(ev.get("reset_by") or ev.get("held_by")))


def ready_followers(site_id: str) -> list[str]:
    """Pending stages (never run, or put back by an earlier stage) whose EVERY earlier chain stage is done (passed /
    passed_warn / skipped). The chain (T18) replaces the per-stage ``needs`` graph: A1 -> A2 -> ... -> A7."""
    rows = state.all_for(site_id)
    out = []
    for sid in chain():
        if not prev_of(sid) or not _rerunnable(rows.get(sid)):
            continue
        if blocker(site_id, sid, rows) is None:
            out.append(sid)
    return out


def waiting_for(site_id: str, stage_id: str, rows: Optional[dict] = None) -> Optional[str]:
    """The earlier chain stage a pending stage waits for (``None`` = not waiting)."""
    rows = rows if rows is not None else state.all_for(site_id)
    if (rows.get(stage_id) or {}).get("state", "pending") != "pending":
        return None
    return blocker(site_id, stage_id, rows)


def _cancel_run(site_id: str, stage_id: str) -> bool:
    """A queued / waiting run of the stage is cancelled (a running one stops itself at its next step: ``_gate``)."""
    job = jobs.store.get_by_key(job_key(site_id, stage_id))
    if job is not None and job["state"] in ("queued", "waiting"):
        return jobs.store.cancel(job_key(site_id, stage_id))
    return False


def hold(site_id: str, stage_id: str, by: str, rows: Optional[dict] = None) -> bool:
    """``stage_id`` waits for the earlier stage ``by``: ``pending`` at once, its queued run cancelled; its result (patch,
    measurement, handoff), the admin's samples and exclusions are kept so an unchanged ``by`` gives it back."""
    rows = rows if rows is not None else state.all_for(site_id)
    row = rows.get(stage_id)
    if not row or row["state"] == "skipped":
        return False
    ev = dict(row.get("evidence") or {})
    cancelled = _cancel_run(site_id, stage_id)
    if row["state"] == "running":
        return cancelled   # its own job notices the earlier stage at its next step (``_gate``)
    if row["state"] == "pending":
        if not row["runs"] or ev.get("held_by") or ev.get("reset_by"):
            if cancelled and not ev.get("held_by") and not ev.get("reset_by"):
                state.update(site_id, stage_id, evidence={**ev, "held_by": by, "held_prev": None})
            return cancelled
        prev = None   # queued again by an admin action: it runs again after ``by``
    else:
        prev = row["state"] if row["state"] in RESTORABLE else None
    ev.update(held_by=by, held_prev=prev)
    state.update(site_id, stage_id, state="pending", round=0, evidence=ev)
    state.add_message(site_id, stage_id, "system", f"{by} yeniden çalışıyor; bu aşama {by} bitince devam eder")
    return True


def hold_after(site_id: str, stage_id: str) -> list[str]:
    """``stage_id`` runs again (rerun, hint, sample, note rerun, hand-back, repair): every later chain stage waits for it."""
    rows = state.all_for(site_id)
    return [sid for sid in after(stage_id) if hold(site_id, sid, stage_id, rows)]


def normalize_chain(site_id: str) -> list[str]:
    """Bring a site whose chain went out of order (two stages waiting for the admin at once, a stage holding a result
    while an earlier one is unresolved) back in line: every stage after the first unresolved one waits for it."""
    rows = state.all_for(site_id)
    out = []
    for sid in chain():
        b = blocker(site_id, sid, rows)
        row = rows.get(sid)
        if b is None or not row:
            continue
        if row["state"] in RESTORABLE or (row["state"] == "pending" and _job_active(site_id, sid)):
            if hold(site_id, sid, b, rows):
                out.append(sid)
    return out


def _job_active(site_id: str, stage_id: str) -> bool:
    job = jobs.store.get_by_key(job_key(site_id, stage_id))
    return job is not None and job["state"] in ("queued", "running", "waiting")


def restore_after(site_id: str, stage_id: str) -> list[str]:
    """``stage_id`` passed again with the SAME result: the later stages it held get their previous state back, in chain
    order, up to the first one that is not done (it keeps waiting for the admin) or has to run again."""
    rows = state.all_for(site_id)
    out = []
    for sid in after(stage_id):
        row = rows.get(sid) or {}
        ev = row.get("evidence") or {}
        if row.get("state") == "skipped":
            continue
        if row.get("state") != "pending" or not ev.get("held_by") or ev.get("held_prev") not in RESTORABLE:
            break
        prev = ev["held_prev"]
        state.update(site_id, sid, state=prev, evidence={k: v for k, v in ev.items() if k not in ("held_by", "held_prev")})
        state.add_message(site_id, sid, "system", f"{stage_id} değişmedi; önceki sonuç geri geldi")
        out.append(sid)
        if prev not in state.DONE:
            break
    return out


def reset_after(site_id: str, stage_id: str) -> list[str]:
    """``stage_id`` passed again with a CHANGED result: every later chain stage that already ran goes back to ``pending``
    (measurement / evidence / handoff / auto samples cleared; the admin's samples and exclusions kept) and re-runs in order
    (the next one starts when the previous one finishes). A queued run reads the new draft anyway; a running one notices
    the change itself (``upstream_sig``)."""
    rows = state.all_for(site_id)
    done = []
    for sid in after(stage_id):
        row = rows.get(sid)
        if not row or row["state"] in ("running", "skipped") or (row["state"] == "pending" and not row["runs"]):
            continue
        if (row.get("evidence") or {}).get("reset_by"):
            continue
        job = jobs.store.get_by_key(job_key(site_id, sid))
        if job is not None and job["state"] in ("queued", "running", "waiting"):
            continue
        state.update(site_id, sid, state="pending", round=0, measurement=None, patch=None, handoff=None,
                     evidence={"reset_by": stage_id})
        state.add_message(site_id, sid, "system", f"{stage_id} değişti; bu aşama yeni bilgilerle yeniden çalışıyor")
        done.append(sid)
    if done:
        with db.transaction():
            data = map_store.draft(site_id)
            for sid in done:
                entry = (data.get("meta") or {}).get(STAGES[sid].page_type)
                if isinstance(entry, dict):
                    for key in ("samples", "fingerprint"):
                        entry.pop(key, None)
                    if not entry:
                        data["meta"].pop(STAGES[sid].page_type, None)
            map_store.save_draft(site_id, data)
    return done


TRIGGERS = ("admin", "system")


def trigger_of(p: Optional[dict]) -> str:
    """The run's trigger (old payloads without one: ``background`` decides)."""
    p = p or {}
    t = p.get("trigger")
    return t if t in TRIGGERS else ("system" if p.get("background") else "admin")


def current_trigger(site_id: str, stage_id: str) -> str:
    """The trigger of the stage's latest job (``admin`` when none)."""
    job = jobs.store.get_by_key(job_key(site_id, stage_id))
    return trigger_of((job or {}).get("payload")) if job else "admin"


def queue_followers(site_id: str, trigger: str = "admin") -> list[str]:
    """Followers of a chain run inherit its trigger (an admin-started A2 -> A3..A7 are admin runs too)."""
    queued = []
    for sid in ready_followers(site_id):
        if enqueue(site_id, sid, trigger=trigger)["queued"]:
            queued.append(sid)
    return queued


# ---------------------------------------------------------------------------------------------- llm patch

def scope_patch(stage: Stage, data: Any) -> tuple[Optional[dict], Optional[str]]:
    """The LLM answer as ``{pages: {<type>: ...}}`` (+ the stage's ``extra_keys``); anything else -> error (nothing written)."""
    pt = stage.page_type
    extra = set(stage.extra_keys)
    if not isinstance(data, dict):
        return None, "yanıt bir eşleme değil"
    if set(data) == {pt}:
        data = {"pages": data}
    allowed = {"pages"} | extra
    if not data or not set(data) <= allowed:
        return None, f"yanıt kapsam dışı anahtarlara dokunuyor: {', '.join(sorted(map(str, set(data) - allowed))) or '-'}"
    out: dict = {"pages": {}}
    if "pages" in data:
        pages = data["pages"]
        if not isinstance(pages, dict) or set(pages) != {pt} or not isinstance(pages[pt], dict):
            others = sorted(map(str, set(pages) - {pt})) if isinstance(pages, dict) else ["?"]
            return None, f"yanıt başka sayfa türüne dokunuyor: {', '.join(others) or '?'}"
        out["pages"][pt] = pages[pt]
    for key in extra & set(data):
        out[key] = data[key]
    return out, None


def map_problems(ctx: StageCtx, stage: Stage, patch: dict) -> list[str]:
    """Schema errors of the draft with ``patch``'s page REPLACING the draft's (validated on a copy)."""
    data = copy.deepcopy(ctx.map)
    entry = (data.get("meta") or {}).get(stage.page_type)
    if isinstance(entry, dict):   # the run writes its own samples: the last pass's (maybe excluded) ones do not judge it
        for key in ("samples", "fingerprint"):
            entry.pop(key, None)
    page = ((patch or {}).get("pages") or {}).get(stage.page_type)
    if page is None and stage.extra_keys:
        data.setdefault("pages", {}).pop(stage.page_type, None)
    else:
        data.setdefault("pages", {})[stage.page_type] = copy.deepcopy(page or {})
    for key in stage.extra_keys:
        if key in (patch or {}):
            data[key] = copy.deepcopy(patch[key])
    return [f"{p.path}: {p.message}" for p in map_schema.errors(map_schema.validate(data))]


def measure(stage: Stage, ctx: StageCtx, patch: dict) -> Measurement:
    """``verify`` on the patch; an empty or schema-invalid patch fails with a ``map_valid`` check."""
    page = ((patch or {}).get("pages") or {}).get(stage.page_type)
    if not page and not any((patch or {}).get(k) for k in stage.extra_keys):
        return Measurement(checks=[{"name": "proposal", "value": 0, "bound": 1, "op": "min", "ok": False,
                                    "optional": False}], notes=["öneri yok"])
    problems = map_problems(ctx, stage, patch)
    if problems:
        return Measurement(checks=[{"name": "map_valid", "value": 0, "bound": 1, "op": "min", "ok": False,
                                    "optional": False}], notes=["harita geçersiz: " + "; ".join(problems[:3])])
    # the page REPLACES the draft's page during measurement too
    view = copy.deepcopy(ctx.map)
    view.setdefault("pages", {})[stage.page_type] = {}
    probe = dataclasses.replace(ctx, map=view)
    try:
        return stage.verify(probe, patch)
    except Exception as exc:   # a broken rule never kills the job
        log.exception("verify %s failed", stage.id)
        return Measurement(checks=[{"name": "verify_error", "value": 0, "bound": 1, "op": "min", "ok": False,
                                    "optional": False}], notes=[f"ölçüm hatası: {type(exc).__name__}"])


def _ask_llm(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, last: Measurement, *, read: bool = False) -> dict:
    """One LLM round -> ``{patch?, error?, prompt_chars}``. ``read`` (T23): the first read (purpose + page maps; the reply
    carries ``interpretation`` + ``rules``; budget-counted only in a system-started run)."""
    rnd = int(p.get("round") or 1)
    session = session_key(ctx.site_id, stage.id, int(p.get("run") or 0))
    from . import prompts_a1_a3, prompts_read
    in_session = True
    bg = bool(p.get("background"))   # admin runs (incl. the first read) are outside the budget
    refs: dict = {}
    if read:
        pages = stage.read_pages(ctx, finding)
        maps = prompts_read.page_maps(pages)
        refs = prompts_read.refs_of(maps)
        LAST_INTERPRETATION.clear()
        text = site_notes.with_note(ctx.site_id, prompts_read.read_prompt(stage, ctx, finding, p.get("hint") or "", pages, maps))
        reply = COMPLETE(text, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=LLM_TIMEOUT_S,
                         session=session, background=bg)
        p["session_ok"] = bool(reply.ok)
    elif rnd > 1 and p.get("session_ok"):
        text = prompts_a1_a3.follow_up(stage, last, p.get("hint") if p.get("hint_new") else "")
        reply = COMPLETE(text, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=LLM_TIMEOUT_S,
                         session=session, background=bool(p.get("background")))
        if getattr(reply, "session_lost", False):
            text = site_notes.with_note(ctx.site_id, stage.prompt(ctx, finding, p.get("hint") or "", last))
            reply = COMPLETE(text, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=LLM_TIMEOUT_S,
                             background=bool(p.get("background")))
            in_session = False
    else:
        text = stage.prompt(ctx, finding, p.get("hint") or "", last)
        if p.get("repair"):   # T14: the broken / good samples and what the current rule does on them
            from . import prompts_repair
            text = prompts_repair.add(text, stage, ctx, p["repair"], lambda patch: measure(stage, ctx, patch))
        text = site_notes.with_note(ctx.site_id, text)   # the site note heads every full prompt (stable, cacheable prefix)
        reply = COMPLETE(text, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=LLM_TIMEOUT_S,
                         session=session, background=bool(p.get("background")))
        p["session_ok"] = bool(reply.ok)
    out: dict = {"round": rnd, "prompt_chars": len(text), **({"read": True} if read else {})}
    if not reply.ok:
        out["error"] = f"llm_call_failed:{reply.error}"
        return out
    answer = reply.text
    first_items = _interpretation_of(reply.text) if read else None
    if getattr(stage, "investigates", False):   # A5/A6: the LLM may ask the server for investigation steps first
        from . import investigate
        answer, why = investigate.loop(stage, ctx, p, reply.text, complete=COMPLETE, session=session if in_session else None,
                                       first_prompt=text, finding=finding, background=bg)
        if p.get("investigation"):
            finding.evidence["investigation"] = list(p["investigation"])[-investigate.TRAIL_MAX:]
            if isinstance(p.get("finding"), dict):
                p["finding"].setdefault("evidence", {})["investigation"] = finding.evidence["investigation"]
        if answer is None:
            out["error"] = why
            return out
    patch, err = reply_patch(stage, ctx, answer, read=read)
    if patch is None and in_session and _fix_allowed(ctx.site_id, {**p, "background": bg}):
        # T18: ONE format-fix request in the same session and the same round (counts against the budget, not as a round)
        fix = (prompts_read.format_fix(stage, err, list(LAST_REPLY_TYPES)) if read
               else prompts_a1_a3.format_fix(stage, err, list(LAST_REPLY_TYPES)))
        again = COMPLETE(fix, purpose="stage", site_id=ctx.site_id, stage=stage.id, timeout=LLM_TIMEOUT_S,
                         session=session, background=bg)
        out["format_fix"] = True
        if again.ok:
            patch, err2 = reply_patch(stage, ctx, again.text, read=read)
            err = err if patch is not None else f"{err}; biçim düzeltmesi: {err2}"
        else:
            err = f"{err}; biçim düzeltmesi: llm_call_failed:{again.error}"
    if read:
        items = LAST_INTERPRETATION if LAST_INTERPRETATION else (first_items or [])
        out["interpretation"] = prompts_read.resolve(items, refs)
    if patch is None:
        out["error"] = err
    else:
        notes_ = list(LAST_REPLY_NOTES)
        merge = getattr(stage, "merge_llm", None)
        if merge is not None and not p.get("repair"):   # A5: LLM-free vs LLM rules measured by playing (repair: its own gate)
            patch, extra = merge(ctx, finding, patch)
            notes_ += list(extra)
            for line in extra:
                state.add_message(ctx.site_id, stage.id, "system", str(line)[:300])
        out["patch"] = patch
        if notes_:
            out["notes"] = notes_[:6]
    return out


def _discover_fallback(stage: Stage, ctx: StageCtx, p: dict, finding: Finding):
    """The LLM could not settle an ambiguous discovery: the (passing) discovery patch is used after all."""
    m = measure(stage, ctx, finding.patch)
    m.notes.insert(0, "LLM seçimi kullanılamadı; keşif sonucu kullanıldı")
    state.add_message(ctx.site_id, stage.id, "system", m.notes[0])
    if _passes(p, m):
        return _finish(stage, ctx, p, finding, finding.patch, m, "discover")
    return _needs_human(stage, ctx, p, finding, m)


def _fix_allowed(site_id: str, p: dict) -> bool:
    if not p.get("background"):
        return True
    allowed, _why = llm_budget.allowed(site_id, background=True)
    return bool(allowed)


#: leniency notes of the last ``reply_patch`` (yaml escape repair, an unsupported ``cast`` dropped): shown with the measurement
LAST_REPLY_NOTES: list[str] = []
#: resolver types of the last ``reply_patch``'s answer (the format-fix request shows their exact schema)
LAST_REPLY_TYPES: list[str] = []
#: T23: the ``interpretation`` items of the last first-read ``reply_patch``
LAST_INTERPRETATION: list[dict] = []


def _interpretation_of(text: str) -> Optional[list]:
    """The interpretation items of a reply text (the first reply of an investigation carries them), ``None`` when none."""
    from . import prompts_read
    data, _err = llm_parse.extract_yaml(text or "")
    if not isinstance(data, dict) or "interpretation" not in data:
        return None
    return prompts_read.validate(data.get("interpretation"))[0]


def _resolver_types(data: Any, out: list) -> list:
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "resolvers" and isinstance(value, list):
                out += [str(i.get("type")) for i in value if isinstance(i, dict) and i.get("type")]
            else:
                _resolver_types(value, out)
    elif isinstance(data, list):
        for value in data:
            _resolver_types(value, out)
    return out


def reply_patch(stage: Stage, ctx: StageCtx, text: str, *, read: bool = False) -> tuple[Optional[dict], str]:
    """``(patch, "")`` of an LLM reply, or ``(None, error)``: yaml extraction (``core.llm.parse``), lenient field rules
    (``fallback: []`` dropped, a single rule wrapped), the stage's own parser or the scope check, then the schema.
    ``read`` (T23): the reply is ``{interpretation, rules}``; the items go to ``LAST_INTERPRETATION``, ``rules`` is the patch."""
    LAST_REPLY_NOTES.clear()
    data, err = llm_parse.extract_yaml(text)
    if data is None:
        return None, f"yaml değil ({err})"
    LAST_REPLY_NOTES.extend(llm_parse.LAST_NOTES)
    if read:
        from . import prompts_read
        items, data, notes_ = prompts_read.split(data)
        if items is not None:
            LAST_INTERPRETATION[:] = items
        LAST_REPLY_NOTES.extend(notes_)
        if data is None:
            return None, "rules yok (yanıt interpretation + rules olmalı)"
    LAST_REPLY_TYPES[:] = list(dict.fromkeys(_resolver_types(data, [])))
    map_schema.normalize_rules(data, LAST_REPLY_NOTES)
    custom = stage.parse_reply(ctx, data)
    if custom is not None:
        patch, err = custom
        return (patch, "") if patch is not None else (None, err or "yanıt geçersiz")
    patch, err = scope_patch(stage, data)
    if patch is None:
        return None, err or "yanıt geçersiz"
    problems = map_problems(ctx, stage, patch)
    if problems:   # the whole list (a format fix shows it line by line)
        return None, "harita geçersiz: " + "; ".join(problems[:12])
    return patch, ""


def same_json(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _page_view(data: dict, stage: Stage) -> str:
    """The stage's own part of a map (its page + extra keys), for "did the result change" checks."""
    body = {"page": (data.get("pages") or {}).get(stage.page_type), **{k: data.get(k) for k in stage.extra_keys}}
    return json.dumps(body, sort_keys=True, default=str)


def _page_view_extras(new_view: dict, stage: Stage, old_page: str) -> str:
    """``_page_view`` of the draft after ``write_patch``: extra keys missing from the patch keep their old value."""
    old = json.loads(old_page)
    body = {"page": (new_view.get("pages") or {}).get(stage.page_type),
            **{k: new_view.get(k, old.get(k)) for k in stage.extra_keys}}
    return json.dumps(body, sort_keys=True, default=str)


def same_patch(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _invalid(last: Optional[Measurement], error: str) -> Measurement:
    checks = [c for c in (last.checks if last else []) if c.get("name") != "llm_reply_invalid"]
    checks.insert(0, {"name": "llm_reply_invalid", "value": 1, "bound": 0, "op": "max", "ok": False, "optional": False})
    return Measurement(checks=checks, samples=list(last.samples) if last else [], notes=[f"LLM yanıtı geçersiz: {error}"])


# ---------------------------------------------------------------------------------------------- LLM-first (T23)

def reads_first(stage: Stage, p: dict) -> bool:
    return bool(LLM_FIRST) and bool(getattr(stage, "reads_first", False)) and not p.get("repair")


def first_read(stage: Stage, ctx: StageCtx, p: dict, finding: Finding) -> dict:
    """The LLM's first read of the run (one call + format fix), or ``{off: why}`` when the site's daily budget is spent or
    the LLM cannot be reached (the LLM-free discovery goes on alone)."""
    allowed, why = llm_budget.allowed(ctx.site_id, background=bool(p.get("background")))
    if not allowed:   # only system-started runs; an admin run is never blocked (user decision)
        return {"off": f"günlük LLM bütçesi doldu ({why})"}
    p["rounds_started"] = True
    p["round"] = 1
    attempt = _ask_llm(stage, ctx, p, finding, Measurement(), read=True)
    if str(attempt.get("error") or "").startswith("llm_call_failed"):
        attempt["off"] = f"LLM'e ulaşılamadı ({attempt['error'].split(':', 1)[-1][:60]})"
    return attempt


def score(m: Measurement) -> tuple:
    """(passed checks, sum of the ``*_fill`` values): more fields filled / more checks met = better."""
    ok = sum(1 for c in m.checks if c.get("ok"))
    fill = sum(float(c["value"]) for c in m.checks if str(c.get("name") or "").endswith("_fill")
               and isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool))
    return ok, round(fill, 3)


def _outcome(stage: Stage, ctx: StageCtx, patch: dict, m: Measurement) -> Optional[dict]:
    try:
        return stage.outcome(ctx, patch, m)
    except Exception:   # not comparable: the score decides
        log.exception("outcome %s failed", stage.id)
        return None


def _interp_line(items: list) -> str:
    parts = [f"{i.get('ref')}{(' ' + repr(i['heading'])) if i.get('heading') else ''} = {i.get('meaning') or '-'} "
             f"({'alınır' if i.get('take') else 'alınmaz'}{': ' + i['why'] if i.get('why') else ''})" for i in items[:8]]
    return "LLM'in yorumu: " + "; ".join(parts)


def referee(stage: Stage, ctx: StageCtx, p: dict, finding: Finding):
    """Both rule sets measured alike (T23). Returns the run's result, or ``None`` = go on with the older flow: the LLM was
    not usable (``p['llm_off']``: discovery alone, no LLM rounds) or neither passed (round 2 in the same session)."""
    read = p.get("read") or {}
    ev = finding.evidence if finding.evidence is not None else {}
    if read.get("off"):
        note = f"LLM kullanılamadı ({read['off']}), sistem tahmini ile devam"
        p["llm_off"] = note
        ev["llm_read"] = {"used": False, "note": note}
        (p.get("finding") or {}).setdefault("evidence", {})["llm_read"] = ev["llm_read"]
        state.add_message(ctx.site_id, stage.id, "system", note)
        return None
    items = list(read.get("interpretation") or [])
    ctx.reading = {"interpretation": items}
    if items:
        ev["interpretation"] = items
        state.add_message(ctx.site_id, stage.id, "llm", _interp_line(items)[:300])
    p["llm_rounds"] = [{k: v for k, v in read.items() if k not in ("patch", "interpretation")}]
    m_d = measure(stage, ctx, finding.patch)
    if read.get("patch"):
        m_l = measure(stage, ctx, read["patch"])
        m_l.notes.extend(str(n) for n in read.get("notes") or [])
    else:
        m_l = _invalid(None, read.get("error") or "öneri yok")
    d_ok, l_ok = bool(finding.patch) and _passes(p, m_d), bool(read.get("patch")) and _passes(p, m_l)
    if d_ok and (p.get("hint") or p.get("downstream")) and same_patch(finding.patch, p.get("failed_patch")):
        d_ok = False   # a hand-back: the discovery found the very rule that failed downstream again
        m_d.notes.insert(0, "keşif, sonraki aşamada başarısız olan yamayı yeniden buldu")
    verdict = {"used": True, "llm": {"ok": l_ok, "score": list(score(m_l)), "brief": m_l.brief()},
               "discover": {"ok": d_ok, "score": list(score(m_d)), "brief": m_d.brief()}}
    choice, why = None, ""
    if l_ok and d_ok:
        if same_patch(read["patch"], finding.patch):
            choice, why = "llm", "LLM sistem tahminini doğruladı (aynı kurallar)"
        else:
            a, b = _outcome(stage, ctx, read["patch"], m_l), _outcome(stage, ctx, finding.patch, m_d)
            if a is not None and b is not None and not same_json(a, b):
                verdict["chosen"] = "candidates"
                ev["llm_read"] = verdict
                (p.get("finding") or {}).setdefault("evidence", {}).update({"llm_read": verdict, "interpretation": items})
                return _hold_candidates(stage, ctx, p, finding, read["patch"], m_l, m_d)
            # same result: the LLM decides (the score only tells DIFFERENT results apart; it is shown on the card)
            choice, why = "llm", "ikisi de geçti, aynı sonuç; LLM kuralı alındı"
    elif l_ok:
        choice, why = "llm", "LLM kuralı ölçümü geçti, sistem tahmini geçmedi"
    elif d_ok:
        choice, why = "discover", "LLM kuralı ölçümü geçmedi (" + m_l.brief()[:120] + "); sistem tahmini alındı"
    verdict.update(chosen=choice or "none", why=why or "ikisi de geçmedi")
    ev["llm_read"] = verdict
    (p.get("finding") or {}).setdefault("evidence", {}).update({"llm_read": verdict, **({"interpretation": items} if items else {})})
    state.add_message(ctx.site_id, stage.id, "system", "Ölçüm hakemi: " + (why or "LLM kuralı da sistem tahmini de geçmedi"))
    if choice == "llm":
        return _finish(stage, ctx, p, finding, read["patch"], m_l, "llm")
    if choice == "discover":
        return _finish(stage, ctx, p, finding, finding.patch, m_d, "discover")
    p["last"] = m_l.as_dict()
    p["last_discover"] = m_d.as_dict()
    return None


def _hold_candidates(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, llm_patch: dict, m_l: Measurement,
                     m_d: Measurement):
    """Both rule sets pass but find different things: both wait as candidates, the admin picks one on the card."""
    gated = _gate(stage, ctx.site_id, p)
    if gated is not None:
        return gated
    line = getattr(stage, "result_line", lambda _p: "")
    cands, held = [], {}
    for cid, patch, m, label in (("llm", llm_patch, m_l, "LLM'in kuralları"), ("discover", finding.patch, m_d, "sistem tahmini")):
        try:
            found = (_outcome(stage, ctx, patch, m) or {}).get("urls") or []
        except Exception:
            found = []
        cands.append({"id": cid, "label": label, "summary": line(patch)[:300], "score": list(score(m)),
                      "notes": m.notes[:3], "samples": found[:5]})
        held[cid] = {"patch": patch, "measurement": m.as_dict(), "meta": stage.meta_for(ctx, patch, m)}
    review = {"candidates": cands, "at": int(time.time())}
    pending = {"candidates": held, "created_by": p.get("created_by")}
    evidence = {**(finding.evidence or {}), "source": "candidates", "llm": p.get("llm_rounds") or [], "review": review,
                "review_pending": pending}
    state.update(ctx.site_id, stage.id, state="review", patch=llm_patch, measurement=m_l.as_dict(), evidence=evidence,
                 round=int(p.get("round") or 0))
    text = "LLM'in kuralları ve sistem tahmini ölçümü geçti ama farklı şeyler buldu; hangisi doğru, seç"
    state.add_message(ctx.site_id, stage.id, "system", text)
    notes.add("action", f"{ctx.site_id} {stage.id} {stage.title}: {text}"[:500], site_id=ctx.site_id,
              ref_kind="stage", ref_id=stage.id)
    _end(ctx, stage, p)
    _report(ctx.site_id, stage.id, "review", p, evidence=evidence)
    return jobs.Done({"state": "review", "review": "candidates"})


# ---------------------------------------------------------------------------------------------- results

def _write_draft(stage: Stage, ctx: StageCtx, patch: dict, m: Measurement) -> None:
    write_patch(ctx.site_id, stage, patch, stage.meta_for(ctx, patch, m))


def write_patch(site_id: str, stage: Stage, patch: dict, meta_entry: dict) -> None:
    """The stage's page REPLACES the draft's page; ``meta.<type>`` samples/fingerprint are refreshed (skipped/expect kept)."""
    pt = stage.page_type
    with db.transaction():
        data = map_store.draft(site_id)
        if pt in (patch.get("pages") or {}):
            data.setdefault("pages", {})[pt] = copy.deepcopy(patch["pages"][pt])
        else:
            data.setdefault("pages", {}).pop(pt, None)
        for key in stage.extra_keys:
            if key in patch:
                data[key] = copy.deepcopy(patch[key])
        entry = data.setdefault("meta", {}).setdefault(pt, {})
        for key in ("samples", "fingerprint"):
            entry.pop(key, None)
        entry.update(meta_entry)
        if not entry:
            data["meta"].pop(pt, None)
        map_store.save_draft(site_id, data)


def _activate(site_id: str) -> Optional[int]:
    """A draft site whose activation stages passed -> committed (``onboard``), ``active``, the crawl's plan queued (K0)."""
    site = map_store.get(site_id)
    if site is None or site["status"] != "draft":
        return None
    if not all(state.state_of(site_id, s) in state.PASSED for s in ACTIVATION):
        return None
    version = map_store.commit(site_id, created_by="onboard", note="A1+A3 geçti")
    map_store.set_status(site_id, "active")
    notes.add("info", f"{site_id}: site etkinleşti (sürüm {version}); öne çıkanlar aktarılıyor", site_id=site_id,
              ref_kind="site", ref_id=site_id)
    from ..crawl import run as crawl_run   # T11: the crawl replaces the K0 job
    crawl_run.activate(site_id)
    return version


def crawl_plan():
    from ..crawl import plan
    return plan


def crawl_kick(site_id: str):
    from ..crawl import run
    return run.kick(site_id)


def apply_pending(site_id: str, stage_id: str, pending: dict, *, note: str) -> Optional[int]:
    """T14 ``repair_autoapply`` off: the admin approved a measured repair -> its patch (+ meta) goes into the draft, the
    stage's side effects (A6: provider recipes) are saved and a ``repair`` version is committed."""
    stage = stage_of(stage_id)
    patch = dict((pending or {}).get("patch") or {})
    if not patch:
        raise ValueError("bekleyen yama yok")
    write_patch(site_id, stage, patch, dict(pending.get("meta") or {}))
    stage.apply_extras(site_id, patch)
    return map_store.commit(site_id, created_by="repair", note=note[:200])


def _gate(stage: Stage, site_id: str, p: dict):
    """The chain (T18 + fix): an earlier stage is unresolved (it was queued again while this run was going) -> this run stops
    without a result, the stage waits (``pending``, ``held_by``) and runs again when its turn comes. ``None`` = go on."""
    b = blocker(site_id, stage.id)
    if b is None:
        return None
    row = state.get(site_id, stage.id) or {}
    ev = {k: v for k, v in (row.get("evidence") or {}).items() if k not in ("held_by", "held_prev")}
    state.update(site_id, stage.id, state="pending", round=0, evidence={**ev, "held_by": b, "held_prev": None})
    state.add_message(site_id, stage.id, "system", f"{b} yeniden çalışıyor; bu aşama durdu, {b} bitince yeniden çalışır")
    if p.get("rounds_started"):
        llm_client.end_session(session_key(site_id, stage.id, int(p.get("run") or 0)))
    if p.get("repair"):
        _report(site_id, stage.id, "needs_human", p, note=f"{b} bekleniyor")
    return jobs.Done({"state": "pending", "waiting_for": b})


def _finish(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, patch: dict, m: Measurement, source: str):
    gated = _gate(stage, ctx.site_id, p)
    if gated is not None:
        return gated
    ctx.reading = {**(ctx.reading or {}), "source": source}
    patch, review = stage.review(ctx, patch, A1_REVIEW and not p.get("repair"))
    if review:
        return _hold_review(stage, ctx, p, finding, patch, m, source, review)
    return _complete(stage, ctx, p, finding, patch, m, source)


def _hold_review(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, patch: dict, m: Measurement, source: str,
                 review: dict):
    """The run passed but the admin labels the new sections / links first: nothing reaches the draft, no follow-up runs;
    ``confirm_review`` finishes the run with the labels."""
    held = {"patch": patch, "meta": stage.meta_for(ctx, patch, m), "created_by": p.get("created_by"), "source": source}
    evidence = {**(finding.evidence or {}), "source": source, "llm": p.get("llm_rounds") or [], "review": review,
                "review_pending": held}
    state.update(ctx.site_id, stage.id, state="review", patch=patch, measurement=m.as_dict(), evidence=evidence,
                 round=int(p.get("round") or 0))
    if review.get("blocks"):   # A2: one listing page holds several blocks of the same rows
        what = f"{len(review['blocks'])} liste bloğu"
    else:
        what = f"{len(review.get('sections') or [])} bölüm, {len(review.get('links') or [])} bağlantı" + (
            f", site notundan {len(review['suggested'])} adres önerisi" if review.get("suggested") else "")
    state.add_message(ctx.site_id, stage.id, "system", f"Ölçüm geçti; onayın bekleniyor ({what}): her birinin ne olduğunu seç")
    notes.add("action", f"{ctx.site_id} {stage.id} {stage.title}: bulunanları onayla ({what})"[:500],
              site_id=ctx.site_id, ref_kind="stage", ref_id=stage.id)
    _end(ctx, stage, p)
    _report(ctx.site_id, stage.id, "review", p, evidence=evidence)
    return jobs.Done({"state": "review", "review": what})


def confirm_review(site_id: str, stage_id: str, labelled: dict) -> dict:
    """The admin's labels (``labelled``: the held patch with them applied, ``stage.apply_review``) -> the run finishes like a
    pass: draft, version (active site) / activation, follow-up stages; rows the crawl took from now-excluded links go."""
    stage = stage_of(stage_id)
    row = state.get(site_id, stage_id) or {}
    held = (row.get("evidence") or {}).get("review_pending") or {}
    if row.get("state") != "review" or not held:
        raise ValueError("bu aşamada onay bekleyen inceleme yok")
    if held.get("candidates"):
        return _confirm_candidate(stage, site_id, row, held, labelled.get("candidate"))
    before = map_store.active(site_id) or map_store.draft(site_id)
    patch = stage.apply_review(held["patch"], labelled, map_store.get(site_id) or {})
    meta = {**(held.get("meta") or {}), "reviewed": int(time.time())}
    ctx = build_ctx(site_id, stage)
    evidence = {k: v for k, v in (row.get("evidence") or {}).items() if k not in ("review", "review_pending")}
    p = {"created_by": held.get("created_by"), "round": row.get("round") or 0}
    state.add_message(site_id, stage_id, "user", "İnceleme onaylandı")
    done = _complete(stage, ctx, p, Finding({}, evidence, 0.0), patch, Measurement.from_dict(row.get("measurement")),
                     held.get("source") or "discover", meta_entry=meta)
    out = dict(getattr(done, "result", None) or {})
    try:
        out.update(_after_review(site_id, before, map_store.draft(site_id)))
    except Exception:   # follow-up work never takes the confirmation back
        log.exception("after_review %s failed", stage_id)
    return out


def _confirm_candidate(stage: Stage, site_id: str, row: dict, held: dict, choice: Optional[str]) -> dict:
    """T23: the admin picked one of the two passing rule sets; the stage's own review (A1 labels / A2 blocks) follows when
    it has one, else the run finishes like a pass."""
    cands = held.get("candidates") or {}
    if choice not in cands:
        raise ValueError("aday seçilmedi (llm | discover)")
    chosen = cands[choice]
    ctx = build_ctx(site_id, stage)
    evidence = {k: v for k, v in (row.get("evidence") or {}).items() if k not in ("review", "review_pending")}
    ctx.reading = {"interpretation": list(evidence.get("interpretation") or []), "source": choice}
    p = {"created_by": held.get("created_by"), "round": row.get("round") or 0}
    state.add_message(site_id, stage.id, "user", "Aday seçildi: " + ("LLM'in kuralları" if choice == "llm" else "sistem tahmini"))
    finding = Finding({}, {**evidence, "llm_read": {**(evidence.get("llm_read") or {}), "chosen": choice, "why": "admin seçti"}},
                      0.0)
    m = Measurement.from_dict(chosen.get("measurement"))
    patch, review = stage.review(ctx, chosen["patch"], A1_REVIEW)
    if review:
        done = _hold_review(stage, ctx, p, finding, patch, m, choice, review)
    else:
        done = _complete(stage, ctx, p, finding, patch, m, choice, meta_entry=chosen.get("meta") or None)
    return dict(getattr(done, "result", None) or {})


def _after_review(site_id: str, before: dict, after: dict) -> dict:
    """Crawl rows reached through links now labelled ``ignore`` / ``content: episode`` (or ignored sections) are dropped; the
    title listings changed and A2 already passed -> A2 runs again (pagination of the new lists); new lists are seeded."""
    from ..sitemap import labels
    out: dict = {"dropped": crawl_plan().drop_excluded(site_id, after)}
    old = {labels.path(l["url"]) for l in labels.listing_items(before)}
    new = {labels.path(l["url"]) for l in labels.listing_items(after)}
    if old != new and state.state_of(site_id, "A2") in state.PASSED:
        out["a2_rerun"] = enqueue(site_id, "A2").get("queued", False)
    if (map_store.get(site_id) or {}).get("status") == "active" and out["dropped"]:
        crawl_kick(site_id)
    return out


def _complete(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, patch: dict, m: Measurement, source: str,
              meta_entry: Optional[dict] = None):
    site_id = ctx.site_id
    result = "passed_warn" if m.warn else "passed"
    rep = p.get("repair") or {}
    site = map_store.get(site_id) or {}
    old_row = state.get(site_id, stage.id) or {}
    old_page = _page_view(map_store.draft(site_id), stage)
    new_view = {"pages": {stage.page_type: (patch.get("pages") or {}).get(stage.page_type)},
                **{k: patch[k] for k in stage.extra_keys if k in patch}}
    # T14 (K8): a repair that waits for the admin stays OUT of the draft (the next normal pass commits the draft)
    hold = bool(rep) and site.get("active_version") is not None and not settings.get("repair_autoapply")
    pending = None
    try:
        if hold:
            pending = {"patch": patch, "meta": stage.meta_for(ctx, patch, m)}
        elif meta_entry is not None:
            write_patch(site_id, stage, patch, meta_entry)
        else:
            _write_draft(stage, ctx, patch, m)
    except map_store.ValidationError as exc:
        m.checks.insert(0, {"name": "map_valid", "value": 0, "bound": 1, "op": "min", "ok": False, "optional": False})
        m.notes.insert(0, f"taslağa yazılamadı: {exc}")
        return _needs_human(stage, ctx, p, finding, m)
    evidence = {**(finding.evidence or {}), "source": source, "llm": p.get("llm_rounds") or [],
                **({"pending": pending} if pending else {})}
    evidence.pop("to_repair", None)
    if any(c.get("relaxed") for c in m.checks):   # "1 çalışan örnek yeter": the failing samples are left to the repair
        evidence["to_repair"] = [str(s["url"]) for s in m.samples if isinstance(s, dict) and not s.get("ok") and s.get("url")][:8]
    handoff = old_row.get("handoff")
    if not hold:
        try:
            handoff = stage.handoff(ctx, patch, m) or {}
        except Exception:   # the handoff never takes the pass back
            log.exception("handoff %s failed", stage.id)
            handoff = {}
    state.update(site_id, stage.id, state=result, patch=patch, measurement=m.as_dict(), evidence=evidence,
                 round=int(p.get("round") or 0), handoff=handoff)
    changed = not hold and not rep and (_page_view_extras(new_view, stage, old_page) != old_page
                                         or not same_json(handoff, old_row.get("handoff")))
    if changed and old_row.get("runs") is not None:
        reset_after(site_id, stage.id)
    else:   # same result: the stages that waited for this run get their previous state back
        restore_after(site_id, stage.id)
    if evidence.get("to_repair"):
        state.add_message(site_id, stage.id, "system", "Onarılacak örnek: " + ", ".join(evidence["to_repair"][:4]))
    warn = [c["name"] for c in m.failed(optional=True)]
    state.add_message(site_id, stage.id, "system",
                      ("Geçti" if result == "passed" else "Geçti (uyarılı: " + ", ".join(warn) + ")")
                      + (" - keşifle" if source == "discover" else " - LLM önerisiyle") + ". " + "; ".join(m.notes[:2]))
    version = None
    applied = not hold   # T14: a held repair waits for the admin (no new version yet, the draft untouched)
    if site.get("active_version") is not None:
        before = site["active_version"]
        if hold:
            pass
        elif rep:
            try:
                stage.apply_extras(site_id, patch)   # A6: the provider recipes measured in the overlay reach the library now
            except Exception:
                log.exception("apply_extras %s failed", stage.id)
            version = map_store.commit(site_id, created_by="repair",
                                       note=f"sorun #{rep.get('problem_id')} {rep.get('class') or ''}".strip()[:200])
        else:
            version = map_store.commit(site_id, created_by=p.get("created_by") or "edit", note=f"{stage.id} ({source})")
            if version != before and source == "llm":
                notes.add("info", f"{site_id} {stage.id}: LLM düzeltmesi ölçümü geçti, sürüm {version} uygulandı",
                          site_id=site_id, ref_kind="stage", ref_id=stage.id)
    else:
        version = _activate(site_id)
    try:
        if not applied:
            pass   # T14: nothing changed in the active map yet; follow-up work waits for the admin's apply
        elif site.get("status") == "active" and crawl_plan().stage_passed(site_id, stage.id):   # T11
            crawl_kick(site_id)
        if applied:
            stage.on_passed(ctx, patch, m)
    except Exception:   # follow-up work never takes the result back
        log.exception("on_passed %s failed", stage.id)
    followers = queue_followers(site_id, trigger_of(p))
    _end(ctx, stage, p)
    _report(site_id, stage.id, result, p, version=version, applied=applied, source=source, pending=pending)
    return jobs.Done({"state": result, "source": source, "version": version, "queued": followers})


def _needs_human(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, m: Measurement, *, gap: str = "",
                 budget: bool = False):
    gated = _gate(stage, ctx.site_id, p)
    if gated is not None:
        return gated
    result = "engine_gap" if gap else "needs_human"
    evidence = {**(finding.evidence or {}), "llm": p.get("llm_rounds") or [], **({"gap": gap} if gap else {})}
    from . import explain   # plain question: what was done, what is missing, what the admin can show
    secs = [] if gap else explain.sections(evidence, m.checks, m.notes)
    text = f"Motor boşluğu: {gap}" if gap else explain.plain(secs)
    evidence["question"] = text
    if secs:
        evidence["question_sections"] = secs   # the admin card renders these (headings + bullet lines)
    state.update(ctx.site_id, stage.id, state=result, measurement=m.as_dict(), evidence=evidence,
                 round=int(p.get("round") or 0))
    state.add_message(ctx.site_id, stage.id, "system", text)
    if not p.get("repair"):   # a repair run's outcome is announced by the problem record (T14)
        notes.add("action", f"{ctx.site_id} {stage.id} {stage.title}: {text}"[:500], site_id=ctx.site_id,
                  ref_kind="stage", ref_id=stage.id)
    _end(ctx, stage, p)
    _report(ctx.site_id, stage.id, result, p, gap=gap, budget=budget, evidence=evidence, note=text)
    return jobs.Done({"state": result})


def _skipped(stage: Stage, ctx: StageCtx, p: dict, finding: Finding, why: str):
    """Discovery found the stage does not apply to this site (A4 on a film-only site): ``skipped`` + a note."""
    gated = _gate(stage, ctx.site_id, p)
    if gated is not None:
        return gated
    state.update(ctx.site_id, stage.id, state="skipped", evidence=finding.evidence, round=0)
    state.add_message(ctx.site_id, stage.id, "system", f"Aşama kendiliğinden atlandı: {why}")
    restore_after(ctx.site_id, stage.id)
    followers = queue_followers(ctx.site_id, trigger_of(p))
    _end(ctx, stage, p)
    _report(ctx.site_id, stage.id, "skipped", p, note=why)
    return jobs.Done({"state": "skipped", "why": why, "queued": followers})


def _end(ctx: StageCtx, stage: Stage, p: dict) -> None:
    if p.get("rounds_started"):
        llm_client.end_session(session_key(ctx.site_id, stage.id, int(p.get("run") or 0)))


# ---------------------------------------------------------------------------------------------- failed runs

def fail_stage(site_id: str, stage_id: str, error: str, run: int = 0) -> bool:
    """A run that ended without a result (job failed / timeout / crash / cancelled): a ``running`` stage becomes
    ``needs_human`` (the card gets its buttons back), a system message says why and the run's LLM session is deleted."""
    if state.state_of(site_id, stage_id) != "running":
        return False
    first = (str(error or "").strip().splitlines() or ["bilinmeyen hata"])[0][:200]
    row = state.get(site_id, stage_id) or {}
    state.update(site_id, stage_id, state="needs_human",
                 evidence={**(row.get("evidence") or {}), "job_error": first})
    text = f"Aşama başarısız: {first}"
    state.add_message(site_id, stage_id, "system", text)
    title = getattr(STAGES.get(stage_id), "title", "")
    notes.add("action", f"{site_id} {stage_id} {title}: {text}"[:500], site_id=site_id, ref_kind="stage", ref_id=stage_id)
    llm_client.end_session(session_key(site_id, stage_id, int(run or row.get("runs") or 0)))
    return True


def on_job_failed(job: dict, error: str) -> None:
    """``jobs.register(..., on_fail=)``: the job of a stage run ended ``failed``."""
    p = job.get("payload") or {}
    if p.get("site_id") and p.get("stage"):
        fail_stage(p["site_id"], p["stage"], error, int(p.get("run") or 0))
        if p.get("repair"):
            _report(p["site_id"], p["stage"], "needs_human", p, note=f"Aşama başarısız: {str(error)[:200]}")


def reconcile() -> int:
    """Recover hook: every ``running`` stage whose job is not active any more -> ``needs_human``."""
    from ..jobs import store as job_store
    n = 0
    try:
        rows = db.query("SELECT site_id, stage FROM site_stages WHERE state='running'")
    except sqlite3.OperationalError:   # stages not migrated (a jobs-only database)
        return 0
    for row in rows:
        job = job_store.get_by_key(job_key(row["site_id"], row["stage"]))
        if job is not None and job["state"] in job_store.ACTIVE_STATES:
            continue
        error = (job or {}).get("error") or "iş bulunamadı"
        run = int(((job or {}).get("payload") or {}).get("run") or 0)
        n += fail_stage(row["site_id"], row["stage"], error, run)
    for row in db.query("SELECT DISTINCT site_id FROM site_stages"):   # a chain out of order (older runs) -> in line
        try:
            n += len(normalize_chain(row["site_id"]))
        except Exception:
            log.exception("normalize_chain %s failed", row["site_id"])
    return n


# ---------------------------------------------------------------------------------------------- handler

def run_job(job: dict, jctx) -> Any:
    p = dict(job.get("payload") or {})
    site_id, sid = p.get("site_id"), p.get("stage")
    stage = STAGES.get(sid or "")
    site = map_store.get(site_id) if site_id else None
    if stage is None or site is None:
        return jobs.Fail(f"unknown site/stage {site_id}/{sid}")
    if site["status"] == "paused":
        return jobs.Wait(PAUSED)
    gated = _gate(stage, site_id, p)
    if gated is not None:
        return gated
    step = int(job.get("step") or 0)
    rep = p.get("repair") or {}
    first = list(rep.get("broken") or []) + list(rep.get("good") or [])
    fresh_at = float(p.get("fresh_at") or 0)
    ctx = build_ctx(site_id, stage, hint=p.get("hint") or "", deadline=jctx.deadline, first=first, repair=bool(rep),
                    fresh_at=fresh_at, downstream=p.get("downstream"))
    if step == 0:
        p["upstream"] = upstream_sig(site_id, stage.id)
        row = state.get(site_id, stage.id) or {}
        ev = row.get("evidence") or {}
        clean = {k: v for k, v in ev.items() if k not in ("held_by", "held_prev")}
        state.update(site_id, stage.id, state="running", round=0, started_at=int(time.time()),
                     **({"evidence": clean} if clean != ev else {}))
        state.add_message(site_id, stage.id, "system", f"{stage.title} aşaması başladı" + (" (ipucuyla)" if p.get("hint") else "")
                          + (f" (onarım: sorun #{rep.get('problem_id')})" if rep else ""))
        step = 1
        jctx.checkpoint(step, p)
    if step == 1 and ctx.own_only:   # T25 "Yalnız benim örneklerim" with too few of them: say so, nothing is measured
        from . import samples as stage_samples
        few = stage_samples.too_few(site_id, stage.id)
        if few:
            return _needs_human(stage, ctx, p, Finding({}, {"summary": few, "own_only": few}, 0.0), Measurement(notes=[few]))
    if step == 1:
        dctx = build_ctx(site_id, stage, hint=p.get("hint") or "", deadline=min(jctx.deadline, time.time() + DISCOVER_S),
                         first=first, repair=bool(rep), fresh_at=fresh_at, downstream=p.get("downstream"))
        try:
            finding = stage.discover(dctx)
        except Exception as exc:
            log.exception("discover %s failed", stage.id)
            finding = Finding({}, {"summary": f"keşif hatası: {type(exc).__name__}"}, 0.0)
        if dctx.errors and not finding.patch:
            finding.evidence.setdefault("fetch_errors", dctx.errors[-5:])
        p["finding"] = finding.as_dict()
        state.update(site_id, stage.id, evidence=finding.evidence, patch=finding.patch or None)
        state.add_message(site_id, stage.id, "system", "Keşif: " + str(finding.evidence.get("summary") or "-"))
        if finding.evidence.get("hint_note"):
            state.add_message(site_id, stage.id, "system", str(finding.evidence["hint_note"]))
        step = 2
        jctx.checkpoint(step, p)
    finding = Finding.from_dict(p.get("finding"))
    ctx.samples = [u for u in dict.fromkeys(ctx.samples + list((finding.evidence or {}).get("samples") or []))
                   if ctx.allowed(u)]
    if step == 2:
        again = _restart_if_stale(stage, site_id, p, jctx)
        if again is not None:
            return again
        codes = {e.get("code") for e in (finding.evidence or {}).get("fetch_errors") or []}
        if not finding.patch and codes & {"challenge", "browser_unavailable"}:
            m = Measurement(notes=["sayfa getirilemedi: " + ", ".join(sorted(c for c in codes if c))])
            return _needs_human(stage, ctx, p, finding, m, gap="site tarayıcı/korumalı getirme istiyor (" +
                                ", ".join(sorted(c for c in codes if c)) + ")")
        if (finding.evidence or {}).get("skip_stage") and not finding.patch:
            return _skipped(stage, ctx, p, finding, str(finding.evidence["skip_stage"]))
        if reads_first(stage, p) and not p.get("llm_off") and not (finding.evidence or {}).get("llm_free_played"):
            # T23: the LLM reads first, the measurement referees (T32: not when the LLM-free rules already play)
            if "read" not in p:
                p["read"] = first_read(stage, ctx, p, finding)
                jctx.checkpoint(2, p)
            done = referee(stage, ctx, p, finding)
            if done is not None:
                return done
            if not p.get("llm_off"):   # neither passed: the next round goes on in the same session (+ the hint)
                m_d = Measurement.from_dict(p.get("last_discover"))
                stop = stage.escalate(ctx, finding, m_d)
                last = Measurement.from_dict(p.get("last"))
                if stop and stop[0] == "engine_gap":
                    return _needs_human(stage, ctx, p, finding, m_d, gap=stop[1])
                if min(MAX_LLM_ROUNDS, stage.max_llm_rounds) < 2:
                    return _needs_human(stage, ctx, p, finding, last)
                p.update({"round": 2})
                step = 3
                jctx.checkpoint(step, p)
    if step == 2:
        m = measure(stage, ctx, finding.patch)
        state.update(site_id, stage.id, measurement=m.as_dict())
        state.add_message(site_id, stage.id, "system", "Ölçüm (keşif): " + "; ".join(m.notes[:3]))
        repeat = bool(m.ok and (p.get("hint") or p.get("downstream")) and same_patch(finding.patch, p.get("failed_patch")))
        if m.ok and not repair_ok(p, m):   # T14: the overall checks pass but a broken sample still fails / a good one broke
            m.notes.insert(0, "onarım ölçümü: bozuk örnek hâlâ geçmiyor ya da sağlam örnek bozuldu")
            state.add_message(site_id, stage.id, "system", m.notes[0])
        if repeat:   # hand-back: discovery found the very patch that failed downstream -> the LLM round with the hint
            m.notes.insert(0, "keşif, sonraki aşamada başarısız olan yamayı yeniden buldu; ipucuyla LLM turuna geçiliyor")
            state.add_message(site_id, stage.id, "system", m.notes[0])
        elif _passes(p, m) and (finding.evidence or {}).get("ask_llm") and not p.get("hint") and not p.get("downstream"):
            # the discovery passes but is ambiguous (several candidate link groups, ownership warnings): ONE LLM choice
            # first; the discovery patch stays the fallback when the LLM rounds fail
            p["discover_fallback"] = True
            m.notes.insert(0, "keşif belirsiz (" + str(finding.evidence["ask_llm"])[:120] + "): LLM seçimi isteniyor")
            state.add_message(site_id, stage.id, "system", m.notes[0])
        elif _passes(p, m):
            return _finish(stage, ctx, p, finding, finding.patch, m, "discover")
        stop = None if repeat or p.get("discover_fallback") else stage.escalate(ctx, finding, m)
        if stop:
            return _needs_human(stage, ctx, p, finding, m, gap=stop[1] if stop[0] == "engine_gap" else "")
        p.update({"round": 1, "last": m.as_dict(), "llm_rounds": []})
        step = 3
        jctx.checkpoint(step, p)
    while step in (3, 4):
        if step == 3:
            if p.get("llm_off"):   # T23: the LLM could not be used for this run: no rounds, the discovery stands alone
                if p.get("discover_fallback"):
                    return _discover_fallback(stage, ctx, p, finding)
                last = Measurement.from_dict(p.get("last"))
                last.notes.insert(0, str(p["llm_off"]))
                return _needs_human(stage, ctx, p, finding, last, budget="bütçe" in str(p["llm_off"]))
            if p.get("background"):
                allowed, why = llm_budget.allowed(site_id, background=True)
                if not allowed:
                    if p.get("discover_fallback"):
                        return _discover_fallback(stage, ctx, p, finding)
                    return _needs_human(stage, ctx, p, finding, Measurement(notes=[f"LLM bütçesi doldu ({why})"]), budget=True)
            p["rounds_started"] = True
            state.update(site_id, stage.id, round=int(p["round"]))
            attempt = _ask_llm(stage, ctx, p, finding, Measurement.from_dict(p.get("last")))
            p["llm"] = attempt
            p["llm_rounds"] = (p.get("llm_rounds") or []) + [{k: v for k, v in attempt.items() if k != "patch"}]
            p["hint_new"] = False
            step = 4
            jctx.checkpoint(step, p)
        again = _restart_if_stale(stage, site_id, p, jctx)
        if again is not None:
            return again
        attempt = p.get("llm") or {}
        last = Measurement.from_dict(p.get("last"))
        if attempt.get("error"):
            m = _invalid(last, attempt["error"])
            state.add_message(site_id, stage.id, "llm", f"Tur {p['round']}: {attempt['error']}"[:300])
        else:
            m = measure(stage, ctx, attempt["patch"])
            m.notes.extend(str(n) for n in attempt.get("notes") or [])
            state.add_message(site_id, stage.id, "llm", f"Tur {p['round']}: öneri ölçüldü - " + "; ".join(m.notes[:2]))
            if _passes(p, m):
                return _finish(stage, ctx, p, finding, attempt["patch"], m, "llm")
            if m.ok:
                m.notes.insert(0, "onarım ölçümü: bozuk örnek hâlâ geçmiyor ya da sağlam örnek bozuldu (regresyon)")
        state.update(site_id, stage.id, measurement=m.as_dict())
        if int(p["round"]) >= min(MAX_LLM_ROUNDS, stage.max_llm_rounds):
            if p.get("discover_fallback"):
                return _discover_fallback(stage, ctx, p, finding)
            return _needs_human(stage, ctx, p, finding, m)
        p.update({"round": int(p["round"]) + 1, "last": m.as_dict()})
        step = 3
        jctx.checkpoint(step, p)
    return jobs.Fail(f"bad step {step}")


MAX_RESTARTS = 2


def _restart_if_stale(stage: Stage, site_id: str, p: dict, jctx) -> Optional[Any]:
    """An earlier chain stage changed while this run was going (T18): discovery runs again with the new data (at most
    ``MAX_RESTARTS`` times per run; the step is checkpointed, the job yields and comes back at once)."""
    gated = _gate(stage, site_id, p)
    if gated is not None:
        return gated
    if not p.get("upstream") or int(p.get("restarts") or 0) >= MAX_RESTARTS:
        return None
    sig = upstream_sig(site_id, stage.id)
    if sig == p["upstream"]:
        return None
    for key in ("finding", "round", "last", "llm", "llm_rounds", "read", "llm_off", "last_discover"):
        p.pop(key, None)
    p.update(upstream=sig, restarts=int(p.get("restarts") or 0) + 1)
    state.add_message(site_id, stage.id, "system", "Önceki aşama değişti; bu aşama yeni bilgilerle yeniden çalışıyor")
    jctx.checkpoint(1, p)
    return jobs.Retry(0, "upstream changed", yield_=True)


def register() -> None:
    """Job kinds of the stage engine (``control.wiring``)."""
    from .. import crawl
    jobs.register(KIND, run_job, llm=True, timeout_s=JOB_TIMEOUT_S, on_fail=on_job_failed)
    jobs.add_recover_hook(reconcile)
    crawl.register()   # T11: crawl / refresh / inventory replace the K0 and k0_sources jobs
