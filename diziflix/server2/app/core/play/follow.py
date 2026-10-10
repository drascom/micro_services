"""``follow``: THE playback verifier (stage measurement T09, diagnosis T13, repair check T14). No database.

The old sandbox follow-playback + resolve-one helpers merged: open the episode / film page, apply the gate rules,
find candidates with the site map resolvers, resolve each (hand-off -> provider -> streams) within ``deadline``.

    {ok, stage: page|discover|provider|stream, candidates: [{url, host, resolver_type, ok, error}], streams: [], blocked, ms}

``stage`` = how far the chain got (``stream`` = at least one stream).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import blocked as sblocked, candidates as cands, gate, resolve
from .ctx import PlayCtx

DEFAULT_SECONDS = 30.0
MIN_PAGE_SECONDS = 1.0   # less time than this left for the page fetch -> stage "page", error "deadline"


@dataclass
class FollowResult:
    ok: bool
    stage: str
    candidates: list = field(default_factory=list)
    streams: list = field(default_factory=list)
    blocked: bool = False
    ms: int = 0
    error: str = ""
    reason: str = ""                  # blocked: the rule's reason / placeholder hint
    subtitles: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    duration: int = 0
    alternatives: list = field(default_factory=list)   # every source alternative: {key, label, host, resolver_type, ok, ...}

    def as_dict(self) -> dict:
        return asdict(self)


def alternative_report(candidate: dict, outcome: Optional[dict]) -> dict:
    """One source alternative as measured: ``{key, label, host, resolver_type, resolver, ok, error, ms, provider, streams}``."""
    rep = resolve.candidate_report(candidate, outcome)
    outcome = outcome or {}
    return {"key": cands.alt_key(candidate), "label": str(candidate.get("label") or rep["host"])[:60], "host": rep["host"],
            "resolver_type": rep["resolver_type"], "resolver": candidate.get("resolver"), "ok": rep["ok"],
            "error": rep["error"], "ms": int(outcome.get("ms") or 0), "provider": outcome.get("provider") or "",
            "streams": [dict(s) for s in (outcome.get("streams") or [])[:3]],
            **({"referer": outcome["referer"]} if outcome.get("referer") else {}),
            **({"needs_referer": True} if outcome.get("needs_referer") else {})}


def follow(ctx: PlayCtx, page_url: str, *, deadline: Optional[float] = None, html: Optional[str] = None,
           load_cookies: Any = None, all_alternatives: bool = False) -> FollowResult:
    """Verify that ``page_url`` plays. ``deadline`` = absolute ``time.monotonic()`` (default now + 30 s); ``html`` = an
    already fetched page (no page request then). ``all_alternatives``: every source alternative is resolved to the end (no
    early stop once one plays) and reported in ``alternatives``."""
    started = time.monotonic()
    deadline = started + DEFAULT_SECONDS if deadline is None else deadline

    def done(**kw) -> FollowResult:
        return FollowResult(ms=int((time.monotonic() - started) * 1000), **kw)

    if html is None:
        left = deadline - time.monotonic()
        if left < MIN_PAGE_SECONDS:
            return done(ok=False, stage="page", error="deadline")
        try:
            html = gate.load_page(ctx, page_url, timeout=left)
        except Exception as exc:
            return done(ok=False, stage="page", error=" ".join(str(exc).split())[:200])
    if not html:
        return done(ok=False, stage="page", error="boş sayfa")
    hit = sblocked.match(html, gate.episode_rules(ctx), "episode_page")
    if hit:
        return done(ok=False, stage="page", blocked=True, reason=hit["reason"], error=resolve.BLOCKED_PREFIX + hit["reason"])
    found = cands.discover(ctx, html, page_url)
    if not found:
        hint = sblocked.placeholder_hint(html)
        if hint:   # no rule yet, but the only frame looks like a "not public" placeholder
            return done(ok=False, stage="discover", blocked=True, reason=f"yer tutucu çerçeve ({hint['word']}): {hint['src']}",
                        error="sayfada oynatıcı yok, yer tutucu çerçeve var")
        return done(ok=False, stage="discover", error="sayfada video sağlayıcısı bulunamadı")
    if time.monotonic() >= deadline:
        return done(ok=False, stage="discover", error="süre doldu",
                    candidates=[resolve.candidate_report(c, None) for c in found])
    outcomes = resolve.run_candidates(found, lambda raw: resolve.resolve_one(ctx, raw, page_url, load_cookies=load_cookies),
                                      deadline=deadline, wait_all=all_alternatives)
    report = [resolve.candidate_report(c, o) for c, o in zip(found, outcomes)]
    alts = [alternative_report(c, o) for c, o in zip(found, outcomes)]
    merged = resolve.merge(found, [o or {"streams": [], "ms": 0, "duration": 0} for o in outcomes], locator=page_url)
    if not merged["streams"]:
        first = next((r["error"] for r in report if r["error"]), "") or "sağlayıcı akış vermedi"
        return done(ok=False, stage="provider", candidates=report, error=first, alternatives=alts)
    return done(ok=True, stage="stream", candidates=report, streams=merged["streams"], subtitles=merged["subtitles"],
                audio=merged["audio"], duration=int(merged.get("duration") or 0), alternatives=alts)
