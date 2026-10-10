"""Admin "Sorunlar" screen data + admin actions on a problem (T14 item 4). The routes (``admin/routes_problems.py``) only
validate input and map errors; Turkish labels for the screen live here (one place)."""
from __future__ import annotations

import builtins
from typing import Any, Optional

from ... import db, settings
from ...core.catalog import write as catalog_write
from .. import jobs
from ..sitemap.schema import STAGE_PAGE
from . import gaps, notify, repair, store

STATE_TR = {"waiting_user": "seni bekliyor", "waiting_llm": "LLM bekliyor", "open": "onarılıyor", "diagnosing": "onarılıyor",
            "acting": "onarılıyor", "verifying": "doğrulanıyor", "resolved": "çözüldü, kullanıcı onayı bekliyor",
            "confirmed": "onaylandı", "waiting_capability": "motor desteği bekliyor", "closed_not_public": "halka kapalı",
            "exhausted": "vazgeçildi", "closed": "vazgeçildi"}
CLASS_TR = {
    "recovered": "akış yeniden çalışıyor", "client_side": "sunucu tarafı sağlam, cihaz oynatamadı",
    "host_needs_headers": "video sunucusu Referer/başlık istiyor", "wrong_media": "oynatıcı yanlış videoyu veriyor (süre tutmuyor)",
    "unknown_host": "tanınmayan video sağlayıcısı", "provider_changed": "video sağlayıcısı değişmiş",
    "stream_refused": "akış reddediliyor", "player_not_found": "sayfada oynatıcı bulunamıyor",
    "capability_missing": "motorun desteklemediği oynatıcı", "page_unreachable": "sayfaya ulaşılamıyor",
    "no_source": "kayıtlı kaynak yok", "no_player": "sayfada oynatıcı yok", "not_public": "içerik herkese açık değil",
    "image_broken": "afiş / görseller kırık", "structure_changed": "sayfa yapısı değişmiş",
    "missing_field": "başlıkların çoğunda afiş yok", "links_gone": "bağlantıların çoğu kaldırılmış"}
ACTIVE = ("waiting_user", "waiting_llm", "waiting_capability", "open", "diagnosing", "acting", "verifying")
CLOSED_KEEP = 50
STAGE_OF = {page: stage for stage, page in STAGE_PAGE.items()}


def _titles(ids: set) -> dict:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r["title"] for r in db.query(f"SELECT id, title FROM library_items WHERE id IN ({marks})", tuple(ids))}


def _where(p: dict, titles: dict) -> str:
    if p["scope"] in ("site", "host"):
        stage = STAGE_OF.get(p.get("page_type") or "")
        return " · ".join(x for x in (p.get("site_id") or p.get("host") or "-", stage or p.get("page_type") or "") if x)
    title = titles.get(p.get("canonical_id") or "") or p.get("canonical_id") or "-"
    ep = (p.get("episode_id") or "").rsplit(":", 2)
    episode = f"S{ep[-2][1:]}B{ep[-1][1:]}" if len(ep) == 3 and ep[-2].startswith("s") and ep[-1].startswith("e") else ""
    return " · ".join(x for x in (title, episode, p.get("site_id") or "") if x)


def _reporters() -> dict:
    return {r["problem_id"]: r["n"] for r in db.query("SELECT problem_id, COUNT(DISTINCT profile_id) AS n FROM problem_watchers "
                                                       "GROUP BY problem_id")}


def row_view(p: dict, titles: dict, reporters: dict) -> dict:
    n = reporters.get(p["id"], 0)
    rep = repair.info(p)
    return {"id": p["id"], "state": p["state"], "state_label": STATE_TR.get(p["state"], p["state"]), "scope": p["scope"],
            "site_id": p.get("site_id"), "page_type": p.get("page_type"), "stage": STAGE_OF.get(p.get("page_type") or ""),
            "class": p.get("class"), "what": CLASS_TR.get(p.get("class") or "", p.get("class") or "teşhis bekleniyor"),
            "where": _where(p, titles), "reporter": f"{n} profil" if n else "sistem", "opened_at": p.get("opened_at"),
            "updated_at": p.get("updated_at"), "canonical_id": p.get("canonical_id"), "episode_id": p.get("episode_id"),
            "parent_id": p.get("parent_id"), "gap_id": p.get("gap_id"), "repair": {k: rep.get(k) for k in (
                "status", "stage", "result", "version", "applied", "pending_apply") if k in rep}}


def listing(state: str = "", site_id: str = "") -> dict:
    """``waiting_user`` first, then the other working / waiting ones, then the last 50 closed."""
    flt: dict = {}
    if site_id:
        flt["site_id"] = site_id
    if state == "active":
        flt["state"] = builtins.list(ACTIVE)
    elif state == "closed":
        flt["state"] = [s for s in store.STATES if s not in ACTIVE]
    elif state:
        if state not in store.STATES:
            raise ValueError("bilinmeyen durum")
        flt["state"] = state
    rows = store.list(flt, limit=1000)
    active = [p for p in rows if p["state"] in ACTIVE]
    closed = sorted((p for p in rows if p["state"] not in ACTIVE), key=lambda p: -(p.get("updated_at") or 0))[:CLOSED_KEEP]
    active.sort(key=lambda p: (0 if p["state"] == "waiting_user" else 1, -(p.get("updated_at") or 0)))
    picked = active + closed
    titles = _titles({p["canonical_id"] for p in picked if p.get("canonical_id")})
    reporters = _reporters()
    sites = [r["site_id"] for r in db.query("SELECT DISTINCT site_id FROM problems WHERE site_id IS NOT NULL ORDER BY site_id")]
    return {"problems": [row_view(p, titles, reporters) for p in picked], "counts": counts(), "sites": sites,
            "states": STATE_TR}


def counts() -> dict:
    """The top strip: problems waiting for the admin (top level only: a linked child waits with its site problem) and every
    open (not finished) one."""
    rows = {r["state"]: r["n"] for r in db.query("SELECT state, COUNT(*) AS n FROM problems GROUP BY state")}
    waiting = db.query_one("SELECT COUNT(*) FROM problems WHERE state='waiting_user' AND parent_id IS NULL")[0]
    return {"waiting_user": waiting, "open": sum(n for s, n in rows.items() if s not in store.FINISHED)}


def waiting_for_you(stage_rows: builtins.list) -> int:
    """"senden bekleyen": top-level ``waiting_user`` problems + ``needs_human`` stages that no waiting repair problem already
    stands for (a repair that stopped on its stage card is ONE thing to do, not two)."""
    probs = [store.to_dict(r) for r in db.query("SELECT * FROM problems WHERE state='waiting_user' AND parent_id IS NULL")]
    covered = {(p.get("site_id"), repair.info(p).get("stage")) for p in probs}
    stages = sum(1 for r in stage_rows if r["state"] in ("needs_human", "review") and (r["site_id"], r["stage"]) not in covered)
    return len(probs) + stages


def detail(problem_id: int) -> Optional[dict]:
    p = store.get(problem_id)
    if p is None:
        return None
    titles = _titles({p["canonical_id"]} if p.get("canonical_id") else set())
    out = row_view(p, titles, _reporters())
    out["evidence"] = p.get("evidence") or {}
    out["events"] = [{k: e[k] for k in ("id", "at", "actor", "kind", "note", "data")} for e in p["events"]][-80:]
    out["watchers"] = len(p["watchers"])
    linked = store.list({"parent_id": p["id"]}, limit=50)
    if p.get("parent_id"):
        parent = store.row(p["parent_id"])
        if parent:
            linked.insert(0, parent)
    lt = _titles({x["canonical_id"] for x in linked if x.get("canonical_id")})
    out["linked"] = [row_view(x, lt, {}) for x in linked]
    out["gap"] = gaps.get(p["gap_id"]) if p.get("gap_id") else None
    out["attempts"], out["llm_runs"], out["deadline_at"] = p.get("attempts"), p.get("llm_runs"), p.get("deadline_at")
    rep = repair.info(p)
    out["version"] = {"site_id": p.get("site_id"), "version": rep["version"]} if rep.get("version") else None
    out["card"] = _card(p, rep) if p["state"] == "waiting_user" else None
    out["actions"] = actions_for(p, rep)
    return out


def _card(p: dict, rep: dict) -> Optional[dict]:
    """The stage card of the repair (same data as the site page's Harita card)."""
    stage_id = rep.get("stage") or STAGE_OF.get(p.get("page_type") or "")
    if not stage_id or not p.get("site_id"):
        return None
    from ..stages import status
    try:
        stages = status.overview(p["site_id"])["stages"]
    except Exception:
        return None
    return next((s for s in stages if s["id"] == stage_id), None)


def actions_for(p: dict, rep: dict) -> builtins.list[str]:
    out = []
    if not repair.running(p):
        out.append("retry")
    if p["state"] not in ("closed_not_public", "exhausted", "closed", "confirmed"):
        out += ["close_not_public", "close_unimportant"]
    if rep.get("applied") and rep.get("version"):
        out.append("rollback")
    if p["state"] == "waiting_user" and rep.get("pending_apply"):
        out.append("apply")
    if p["state"] in ("waiting_user", "waiting_llm") and (rep.get("stage") or STAGE_OF.get(p.get("page_type") or "")):
        out.append("hint")
    return out


# --------------------------------------------------------------------------- actions

def retry(problem_id: int) -> dict:
    """Admin "yeniden dene": back to ``open`` with a fresh deadline (and a fresh LLM repair allowance), the job runs now."""
    from . import run
    p = store.row(problem_id)
    if p is None:
        raise KeyError(problem_id)
    if repair.running(p):
        raise ValueError("onarım sürüyor; bitince yeniden dene")
    deadline = store.now() + int(settings.get("problem_deadline_s") or 900)
    ev = dict(p.get("evidence") or {})
    ev.pop("repair", None)
    if p["state"] != "open":
        p = store.transition(problem_id, "open", actor="admin", kind="retry", note="admin yeniden denedi", next_at=0,
                             deadline_at=deadline, llm_runs=0, evidence=ev)
    else:
        store.update(problem_id, next_at=0, deadline_at=deadline, llm_runs=0, evidence=ev)
        store.event(problem_id, "admin", "retry", "admin yeniden denedi")
    key = run.job_key(problem_id)
    jobs.cancel(key)   # a job waiting on an old step starts over
    run.enqueue(store.row(problem_id), admin=True)   # admin round: its repair is outside the LLM budget
    return {"state": "open"}


def close(problem_id: int, reason: str) -> dict:
    """``not_public`` -> ``closed_not_public`` (+ its pages blocked, K5); ``unimportant`` -> closed / ``exhausted``."""
    from . import diagnose as diag, run
    p = store.row(problem_id)
    if p is None:
        raise KeyError(problem_id)
    if reason not in ("not_public", "unimportant"):
        raise ValueError("neden not_public ya da unimportant olmalı")
    if p["state"] in ("closed_not_public", "exhausted", "closed"):
        raise ValueError("sorun zaten kapalı")
    if reason == "not_public":
        if p["scope"] in ("episode", "title"):
            for row in diag.rows_of(p):
                if row["resolver"] == "page":
                    catalog_write.mark_blocked(row["source"], row["locator"], kind="episode" if p.get("episode_id") else "movie",
                                               reason="admin: halka kapalı", via="gate")
        to = "closed_not_public"
    else:
        to = "closed" if "closed" in store.ALLOWED[p["state"]] else "exhausted"
    if to not in store.ALLOWED[p["state"]]:   # e.g. confirmed: reopen-only
        raise ValueError("bu durumdaki sorun kapatılamaz")
    jobs.cancel(run.job_key(problem_id))
    done = store.transition(problem_id, to, actor="admin", kind="closed", note="halka kapalı" if reason == "not_public" else "önemsiz",
                            resolution=f"admin:{reason}")
    notify.send(done["id"])
    if done["scope"] == "site":
        run.finish_children(done)
    return {"state": done["state"]}


def set_gap(gap_id: int, status: str) -> dict:
    return gaps.set_status(gap_id, status)


def gap_list() -> builtins.list[dict]:
    return gaps.list_all()


def stage_badges(site_id: str) -> dict:
    """``{stage_id: open problem count}`` for the site page's Harita cards."""
    out: dict[str, Any] = {}
    for p in store.list({"site_id": site_id, "state": builtins.list(ACTIVE)}, limit=500):
        stage = STAGE_OF.get(p.get("page_type") or "")
        if stage:
            out[stage] = out.get(stage, 0) + 1
    return out
