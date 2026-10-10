"""LLM-free actions of the problem core (T13 item 5) + the measurement that alone may resolve a problem.

Each action returns a step ``{name, ok, ms, note, ...}`` (``name`` = the client's ``retry|search|heal``; ``action`` = ours).
Order (``run``): ``retry_sources`` -> the diagnosis' action -> ``search_elsewhere``. Until T14 the "repair" action ends in
``waiting_user`` + an admin note (``run``).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ... import db
from ...core.play import hostrules, provider_match, sources
from . import diagnose as diag

#: ``fn(canonical_id, episode_id, exclude_sites) -> int`` (sources added on other active sites). Bound by the search layer
#: (T09/T11); unbound = the step is skipped.
search_hook: Optional[Callable[[str, str, list], int]] = None


def _step(action: str, ok: bool, started: float, note: str, **extra: Any) -> dict:
    name = {"retry_sources": "retry", "search_elsewhere": "search"}.get(action, "heal")
    return {"name": name, "action": action, "ok": bool(ok), "ms": int((time.monotonic() - started) * 1000),
            "note": str(note)[:200], **extra}


def measure_row(problem: dict, row: Any) -> dict:
    """Forced resolution of one source (no stored link, no negative cache) + probe + length. ``{ok, note, source_id, site}``."""
    out = {"source_id": row["id"], "site": row["source"], "ok": False}
    try:
        sources.invalidate(row["id"])
        result = sources.resolve_source(row, force=True)
    except Exception as exc:
        return {**out, "note": f"çözümleme: {' '.join(str(exc).split())[:120]}"}
    streams = [s for s in result.get("streams") or [] if isinstance(s, dict) and s.get("url")]
    if not streams:
        return {**out, "note": "çözümleme akış vermedi"}
    verdict = diag.probe_streams(streams, row["locator"])
    if diag.refused(verdict):
        return {**out, "note": f"yoklama reddetti ({verdict.get('code')} {verdict.get('http') or ''})".strip(),
                "probe": {k: verdict.get(k) for k in ("code", "http")}}
    dur = diag.duration_check(streams, int(result.get("duration") or 0), problem.get("canonical_id") or "",
                              problem.get("episode_id") or "", row["locator"])
    if dur["match"] == "short":
        return {**out, "note": "akış beklenen süreden belirgin kısa", "duration": dur}
    return {**out, "ok": True, "note": f"{len(streams)} akış, yoklama {'iyi' if verdict else 'belirsiz'}", "duration": dur}


def measure(problem: dict) -> dict:
    """The ONLY way to ``resolved``: some source of the content resolves (forced), the probe does not refuse it and its
    length fits (when known). ``{ok, note, source_id?, site?, tried}``."""
    rows = diag.rows_of(problem)
    tried = []
    for row in rows:
        got = measure_row(problem, row)
        tried.append(got)
        if got["ok"]:
            if row["status"] in ("broken", "suspect"):   # a broken source that plays again is usable again
                db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL WHERE id=?", (row["id"],))
            return {"ok": True, "note": got["note"], "source_id": row["id"], "site": row["source"], "tried": tried}
    note = "; ".join(t["note"] for t in tried[:3]) if tried else "kaynak yok"
    return {"ok": False, "note": f"{len(tried)} kaynak denendi, akış yok ({note})"[:200], "tried": tried}


def retry_sources(problem: dict, verdict: Optional[dict] = None) -> dict:
    started = time.monotonic()
    got = measure(problem)
    n = len(got.get("tried") or [])
    note = f"{sum(1 for t in got['tried'] if t['ok'])}/{n} kaynak çözüldü" if got["ok"] else got["note"]
    return _step("retry_sources", got["ok"], started, note, site=got.get("site"))


def search_elsewhere(problem: dict, verdict: Optional[dict] = None) -> dict:
    started = time.monotonic()
    if problem.get("scope") not in ("episode", "title"):
        return _step("search_elsewhere", False, started, "yalnız bölüm/başlık sorunu için")
    if search_hook is None:
        return _step("search_elsewhere", False, started, "arama yeteneği bağlı değil", skipped=True)
    have = [r["source"] for r in db.query("SELECT DISTINCT source FROM video_sources WHERE canonical_id=?",
                                          (problem.get("canonical_id") or "",))]
    try:
        added = int(search_hook(problem.get("canonical_id") or "", problem.get("episode_id") or "", have) or 0)
    except Exception as exc:
        return _step("search_elsewhere", False, started, f"arama hatası: {type(exc).__name__}")
    if not added:
        return _step("search_elsewhere", False, started, "başka sitede bulunamadı")
    got = measure(problem)
    return _step("search_elsewhere", got["ok"], started, f"{added} yeni kaynak; " + got["note"], site=got.get("site"))


def learn_host_rule(problem: dict, verdict: dict) -> dict:
    started = time.monotonic()
    ev = verdict.get("evidence") or {}
    host = verdict.get("host") or ""
    ok = hostrules.learn(host, ev.get("learned_headers") or {}, ev.get("reason") or "referer", verdict.get("source_id") or "")
    if ok and verdict.get("source_id"):
        sources.invalidate(verdict["source_id"])
    return _step("learn_host_rule", ok, started, f"{host}: host kuralı yazıldı ({ev.get('reason') or 'referer'})" if ok
                 else f"{host}: host kuralı yazılamadı")


def provider_dry_run(problem: dict, verdict: dict) -> dict:
    """``provider_match`` on the unknown player: a recipe that reads it but whose host match is too narrow is widened through
    A6's checked chain (``add_host_checked``: validate + update gate + no other active site breaks); anything else is left
    to repair."""
    started = time.monotonic()
    ev = verdict.get("evidence") or {}
    url = ev.get("player_url") or ""
    if not url:
        return _step("provider_dry_run", False, started, "oynatıcı adresi yok")
    try:
        found = provider_match.match(url, referer=ev.get("referer") or "", deadline=time.monotonic() + 40)
    except Exception as exc:
        return _step("provider_dry_run", False, started, f"eşleme hatası: {type(exc).__name__}")
    rec = found.get("recommendation") or {}
    if rec.get("action") == "add_host" and rec.get("recipe_yaml"):
        from ..stages.a6_sources import add_host_checked   # A6's gated save: validate + update gate + other sites
        try:
            got = add_host_checked("", rec)
        except Exception as exc:
            return _step("provider_dry_run", False, started, f"tarif yazılamadı: {type(exc).__name__}", recommendation="add_host")
        if not got["ok"]:
            return _step("provider_dry_run", False, started, f"{rec.get('recipe')}: {got['note']}", recommendation="add_host")
        return _step("provider_dry_run", True, started, f"{rec['recipe']} tarifine {rec.get('host')} eklendi (ölçümle)",
                     recommendation="add_host")
    return _step("provider_dry_run", False, started, f"eşleşen sağlayıcı yok ({rec.get('action') or '-'})",
                 recommendation=rec.get("action"))


def next_candidate(problem: dict, verdict: dict) -> dict:
    """``wrong_media``: is there another stream on the page whose length fits (the duration ranking then serves it first)?"""
    started = time.monotonic()
    from ...core.play import streamprobe
    ev = verdict.get("evidence") or {}
    expected = (ev.get("duration") or {}).get("expected_min")
    streams = [s for s in ev.get("streams") or [] if s.get("url")][1:]
    if not streams or not expected:
        return _step("next_candidate", False, started, "başka aday yok")
    probes = streamprobe.summarize_many([{"url": s["url"], "kind": s.get("type") if s.get("type") in ("hls", "mp4") else None,
                                          "expected_min": expected} for s in streams[:5]])
    fit = sum(1 for p in probes if p.get("duration_match") == "ok")
    return _step("next_candidate", bool(fit), started, f"{fit}/{len(probes)} aday süreye uyuyor")


ACTIONS = {"learn_host_rule": learn_host_rule, "provider_dry_run": provider_dry_run, "next_candidate": next_candidate}
