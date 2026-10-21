"""Stage cards of a site (T10 shows ``overview`` as is)."""
from __future__ import annotations

from ...core.llm import budget as llm_budget
from ..sitemap import store as map_store
from . import engine, state

MESSAGES = 30

#: actions allowed per state (names = ``actions`` functions; ``skip_stage`` = ``skip`` without a field)
_ACTIONS = {
    "pending": ("sample",),
    "running": (),
    "passed": ("rerun", "hint", "sample", "skip"),
    "passed_warn": ("rerun", "hint", "sample", "skip"),
    "review": ("review", "rerun"),
    "needs_human": ("hint", "sample", "skip", "rerun", "mark_gap"),
    "engine_gap": ("hint", "rerun"),
    "skipped": ("rerun",),
}
_STATE_TR = {"pending": "bekliyor", "running": "çalışıyor", "passed": "geçti", "passed_warn": "geçti (uyarılı)", "review": "onayın bekleniyor",
             "needs_human": "yardım gerekiyor", "engine_gap": "motor boşluğu", "skipped": "atlandı"}


def _summary(stage, row: dict) -> str:
    st = row["state"]
    m = row.get("measurement") or {}
    ev = row.get("evidence") or {}
    if st in state.PASSED:
        line = getattr(stage, "result_line", lambda p: "")(row.get("patch"))
        return f"{_STATE_TR[st]}: {line}"[:300]
    if st == "review":
        rv = ev.get("review") or {}
        if rv.get("candidates"):
            return "onayın bekleniyor: LLM'in kuralları ve sistem tahmini farklı sonuç buldu (hangisi?)"
        if rv.get("blocks"):
            return f"onayın bekleniyor: {len(rv['blocks'])} liste bloğu (hangisi dizi/film listesi?)"
        return (f"onayın bekleniyor: {len(rv.get('sections') or [])} bölüm, {len(rv.get('links') or [])} bağlantı"
                + (f", {len(rv['suggested'])} not önerisi" if rv.get("suggested") else ""))
    if st == "needs_human":
        bad = [c for c in m.get("checks") or [] if not c.get("ok")]
        names = ", ".join(c["name"] for c in bad[:4])
        return f"yardım gerekiyor: {names or (m.get('notes') or ['-'])[0]}"[:300]
    if st == "engine_gap":
        return f"motor boşluğu: {ev.get('gap') or '-'}"[:300]
    if st == "running":
        return f"çalışıyor (tur {row.get('round') or 0})" if row.get("round") else "çalışıyor (keşif)"
    return _STATE_TR.get(st, st)


def _home_options() -> list:
    """T30: the "Ana ekranda" dropdown (fixed rows + the current dynamic categories + slider + yok)."""
    from ..routing import home_targets
    return home_targets.options()


def _review(row: dict):
    """A1 review card (T17): the items to label + the meaning choices; ``None`` unless the stage waits in ``review``."""
    if row.get("state") != "review":
        return None
    from ..sitemap import labels
    rv = (row.get("evidence") or {}).get("review") or {}
    return {"sections": rv.get("sections") or [], "links": rv.get("links") or [], "suggested": rv.get("suggested") or [],
            "blocks": rv.get("blocks") or [], "candidates": rv.get("candidates") or [],
            "llm_read": bool(rv.get("llm_read")), "home_options": _home_options(),
            "block_choices": [{"id": c, "label": labels.BLOCK_TR[c]} for c in labels.BLOCK_CHOICES],
            "first": bool(rv.get("first")),
            "section_choices": list(labels.SECTION_CHOICES), "meanings": [{"id": m, "label": labels.MEANING_TR[m]}
                                                                         for m in labels.MEANINGS]}


def _handoff_line(site_id: str, stage_id: str) -> str:
    """One line for the card: what the previous chain stage handed over (T18)."""
    h = engine.handoff_in(site_id, stage_id)
    if not h:
        return ""
    urls = h.get("urls") or []
    extra = [f"{len(h[k])} {label}" for k, label in (("listings", "liste"), ("title_cards", "başlık kartı"),
                                                    ("episode_cards", "bölüm kartı")) if h.get(k)]
    if h.get("url_pattern"):
        extra.append(f"kalıp {h['url_pattern']}")
    if h.get("title_urls"):
        extra.append(f"{len(h['title_urls'])} başlık sayfası")
    first = f", ilk: {urls[0]}" if urls else ""
    what = {"A5": "oynatıcı sayfası", "A6": "oynayan örnek"}.get(h["from"], "adres")
    return (f"önceki aşamadan gelen ({h['from']}): {len(urls)} {what}" + (", " + ", ".join(extra) if extra else "")
            + first)[:300]


def _actions(stage, st: str) -> list[str]:
    out = list(_ACTIONS.get(st, ()))
    if "sample" in out:
        out.append("exclude")
    if stage.optional and st not in ("running", "skipped"):
        out.append("skip_stage")
    return out


def _sample_list(site_id: str, stage_id: str, row: dict) -> dict:
    """T25 "Örnekler" table of the card (``samples.listing``); ``{}`` when the stage row is missing / unreadable."""
    from . import samples
    try:
        return samples.listing(site_id, stage_id, row if row.get("site_id") else {})
    except Exception:
        return {}


def llm_line(site_id: str, stage_id: str, row: dict) -> str:
    """"LLM çağrısı: N (senin başlattığın, bütçe dışı)" / "(sistem, bütçeden)" for the stage's latest run ("" = none)."""
    if not row.get("started_at"):
        return ""
    try:
        got = llm_budget.stage_calls(site_id, stage_id, int(row["started_at"]))
    except Exception:
        return ""
    if not got["calls"]:
        return ""
    parts = ([f"{got['admin']} senin başlattığın, bütçe dışı"] if got["admin"] else []) + \
            ([f"{got['system']} sistem, bütçeden"] if got["system"] else [])
    if len(parts) == 1:
        return f"LLM çağrısı: {got['calls']} ({parts[0].split(' ', 1)[1]})"
    return f"LLM çağrısı: {got['calls']} ({'; '.join(parts)})"


def overview(site_id: str) -> dict:
    """``{site, stages: [{id, title, page_type, state, summary, checks, samples, evidence, messages, actions, needs,
    optional, round, runs, user_samples, updated_at}]}`` in stage order."""
    site = map_store.get(site_id)
    if site is None:
        raise map_store.SiteNotFound(site_id)
    site = {k: v for k, v in site.items() if k != "draft_yaml"}
    rows = state.all_for(site_id)
    out = []
    for sid, stage in engine.STAGES.items():
        row = rows.get(sid) or {"state": "pending", "round": 0, "runs": 0, "user_samples": [], "measurement": None,
                                "evidence": None, "patch": None, "updated_at": None}
        m = row.get("measurement") or {}
        out.append({
            "id": sid, "title": stage.title, "page_type": stage.page_type, "state": row["state"],
            "summary": _summary(stage, row), "checks": m.get("checks") or [], "samples": m.get("samples") or [],
            "notes": m.get("notes") or [], "evidence": row.get("evidence") or {},
            "messages": state.messages(site_id, sid, limit=MESSAGES), "actions": _actions(stage, row["state"]),
            "needs": list(stage.needs), "optional": stage.optional, "round": row.get("round") or 0,
            "runs": row.get("runs") or 0, "user_samples": row.get("user_samples") or [], "updated_at": row.get("updated_at"),
            "review": _review(row),
            "excluded_samples": row.get("excluded_samples") or [],
            "own_samples_only": bool(row.get("own_samples_only")),
            "sample_list": _sample_list(site_id, sid, row),
            "waiting_for": engine.waiting_for(site_id, sid, rows),
            "handoff_in": _handoff_line(site_id, sid),
            "llm_line": llm_line(site_id, sid, row),
        })
    from . import actions
    note = {"text": site.get("notes") or "", "updated_at": site.get("notes_updated_at"),
            "changed": actions.notes_changed(site_id)}
    for st in out:
        st["notes_changed"] = note["changed"]
    return {"site": site, "stages": out, "notes": note}
