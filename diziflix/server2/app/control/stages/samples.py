"""T25 sample editor: the samples a stage uses (the admin's own, the previous stage's handoff, automatic ones), with the last
measurement result; delete (= excluded for good, "geri al" restores), edit (old excluded, new = the admin's), add, and the
"Yalnız benim örneklerimi kullan" switch. Nothing here starts a run: the admin re-runs with "Örneklerle yeniden çalıştır"
(``run``). Everything lives in ``site_stages`` (``user_samples``, ``excluded_samples``, ``own_samples_only``), so a chain reset
(``engine.reset_after``) keeps it."""
from __future__ import annotations

from typing import Optional

from ..sitemap import meta as map_meta, store as map_store
from . import actions, engine, state

SRC_OWN, SRC_PREV, SRC_AUTO = "senin", "önceki aşamadan", "otomatik"
MAX_EXCLUDED = 50
#: page types whose stage passes with ONE sample (A2: one confirmed list; A7: one known title)
_ONE = ("listing", "search")


def _norm(url: str) -> str:
    return (url or "").rstrip("/")


def _has(urls, url: str) -> bool:
    return any(_norm(u) == _norm(url) for u in urls or [])


def min_needed(stage_id: str, site_map: Optional[dict] = None) -> int:
    """The fewest samples the stage measures with (its ``samples_count`` / ``series_samples`` threshold)."""
    stage = engine.stage_of(stage_id)
    if stage.page_type in _ONE:
        return 1
    rules = map_meta.expect(site_map or {}, stage.page_type) if stage.page_type in map_meta.EXPECT else {}
    for name in ("samples_count", "series_samples"):
        if name in rules:   # "1 çalışan örnek yeter": a coverage threshold never asks for more than one sample
            return 1 if rules[name].get("coverage") else max(1, int(rules[name].get("bound") or 1))
    return 1


def own_samples(row: dict) -> list[str]:
    excluded = row.get("excluded_samples") or []
    return [u for u in row.get("user_samples") or [] if not _has(excluded, u)]


def too_few(site_id: str, stage_id: str, row: Optional[dict] = None) -> str:
    """"" or the message shown while "Yalnız benim örneklerim" is on with fewer own samples than the stage needs."""
    row = row if row is not None else (state.get(site_id, stage_id) or {})
    if not row.get("own_samples_only"):
        return ""
    need, have = min_needed(stage_id, map_store.draft(site_id)), len(own_samples(row))
    return f"En az {need} örnek gerekiyor; şu an {have}" if have < need else ""


def listing(site_id: str, stage_id: str, row: Optional[dict] = None) -> dict:
    """``{rows: [{url, source, ok, note, measured, used}], excluded: [...], own_only, min, own_count, warning}``."""
    engine.stage_of(stage_id)
    row = row if row is not None else (state.get(site_id, stage_id) or {})
    excluded = list(row.get("excluded_samples") or [])
    own = own_samples(row)
    handed = (engine.handoff_in(site_id, stage_id) or {}).get("urls") or []
    measured = {_norm(s.get("url")): s for s in ((row.get("measurement") or {}).get("samples") or [])
                if isinstance(s, dict) and s.get("url")}
    own_only = bool(row.get("own_samples_only"))
    rows, seen = [], set()

    def add(url: str, source: str) -> None:
        key = _norm(url)
        if not key or key in seen or _has(excluded, url):
            return
        seen.add(key)
        m = measured.get(key) or {}
        rows.append({"url": url, "source": source, "measured": bool(m), "ok": m.get("ok") if m else None,
                     "note": str(m.get("note") or "")[:120], "used": source == SRC_OWN or not own_only})

    for url in own:
        add(url, SRC_OWN)
    for s in (row.get("measurement") or {}).get("samples") or []:
        url = s.get("url") if isinstance(s, dict) else s
        if url:
            add(url, SRC_PREV if _has(handed, url) else SRC_AUTO)
    need = min_needed(stage_id, map_store.draft(site_id))
    return {"rows": rows, "excluded": excluded, "own_only": own_only, "min": need, "own_count": len(own),
            "warning": too_few(site_id, stage_id, row)}


def _busy(site_id: str, stage_id: str) -> None:
    if actions._running(site_id, stage_id):
        raise actions.StageBusy("Aşama çalışıyor; bitince tekrar dene")


def add(site_id: str, stage_id: str, url: str) -> dict:
    """"Ekle": the admin's new sample (same checks as "örnek adres ver"; a list address gives its rows, the home page is
    refused as a sample); saved only."""
    out = actions.sample(site_id, stage_id, url, run=False)
    return {**out, **listing(site_id, stage_id)}


def delete(site_id: str, stage_id: str, url: str) -> dict:
    """"Sil": the address is excluded for good (an automatic / handoff one never comes back; an own one stays listed under
    "Silinenler" and "geri al" makes it the admin's again)."""
    actions._site(site_id)
    engine.stage_of(stage_id)
    url = str(url or "").strip()
    if not url:
        raise ValueError("adres boş")
    _busy(site_id, stage_id)
    row = state.get(site_id, stage_id) or {}
    excluded = [url] + [u for u in row.get("excluded_samples") or [] if _norm(u) != _norm(url)]
    state.update(site_id, stage_id, excluded_samples=excluded[:MAX_EXCLUDED])
    state.add_message(site_id, stage_id, "user", f"Örnek çıkarıldı: {url}")
    return listing(site_id, stage_id)


def restore(site_id: str, stage_id: str, url: str) -> dict:
    """"geri al" from "Silinenler": no longer excluded."""
    actions._site(site_id)
    engine.stage_of(stage_id)
    _busy(site_id, stage_id)
    row = state.get(site_id, stage_id) or {}
    excluded = [u for u in row.get("excluded_samples") or [] if _norm(u) != _norm(url)]
    state.update(site_id, stage_id, excluded_samples=excluded)
    state.add_message(site_id, stage_id, "user", f"Örnek geri alındı: {url}")
    return listing(site_id, stage_id)


def edit(site_id: str, stage_id: str, old: str, new: str) -> dict:
    """"Düzenle": ``new`` is checked like an added sample first (another host / refused address -> ``ValueError``, nothing
    changes); then ``old`` is excluded and dropped from the admin's own list, ``new`` is the admin's."""
    old = str(old or "").strip()
    if not old:
        raise ValueError("eski adres boş")
    _busy(site_id, stage_id)
    out = actions.sample(site_id, stage_id, new, run=False)
    if out.get("home"):
        return {**out, **listing(site_id, stage_id)}
    saved = out.get("saved") or []
    if _has(saved, old):
        return {**out, **listing(site_id, stage_id)}
    row = state.get(site_id, stage_id) or {}
    excluded = [old] + [u for u in row.get("excluded_samples") or [] if _norm(u) != _norm(old)]
    pinned = [u for u in row.get("user_samples") or [] if _norm(u) != _norm(old)]
    state.update(site_id, stage_id, excluded_samples=excluded[:MAX_EXCLUDED], user_samples=pinned)
    state.add_message(site_id, stage_id, "user", f"Örnek düzenlendi: {old} -> {', '.join(saved[:3])}")
    return {**out, **listing(site_id, stage_id)}


def toggle(site_id: str, stage_id: str, on: bool) -> dict:
    """"Yalnız benim örneklerimi kullan" (kept across runs and chain resets)."""
    actions._site(site_id)
    engine.stage_of(stage_id)
    state.update(site_id, stage_id, own_samples_only=1 if on else 0)
    state.add_message(site_id, stage_id, "user", f"Yalnız benim örneklerim: {'açık' if on else 'kapalı'}")
    return listing(site_id, stage_id)


def run(site_id: str, stage_id: str) -> dict:
    """"Örneklerle yeniden çalıştır" (refused while the switch is on with too few own samples)."""
    actions._site(site_id)
    engine.stage_of(stage_id)
    msg = too_few(site_id, stage_id)
    if msg:
        raise ValueError(msg)
    return engine.enqueue(site_id, stage_id, fresh=True)
