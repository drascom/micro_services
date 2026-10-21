"""Playback helpers shared by A5-A7 and the K0 source fill (control side of ``core.play``):

- ``play_ctx(site_map, get)``: a ``PlayCtx`` whose site pages come from the stage fetcher (cached, same host only);
  every other request (provider pages, streams) goes through ``core.play.transport`` (netguard inside);
- ``placeholder_marker(html)``: what marks a "not public" placeholder page (a frame or an element named telif/copyright/...);
- ``follow_sample``: ``core.play.follow`` on one page with a deadline, as a small evidence dict;
- ``check_stream``: ``streamdiag.quick_check`` + ``streamprobe.summarize`` (the duration must be plausible).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from ...core.extract import outline
from ...core.extract._util import SAFE_CLASS, host_key
from ...core.fetch import FetchError
from ...core.play import blocked as play_blocked, follow as play_follow, streamdiag, streamprobe, transport
from ..sitemap import adapters

FOLLOW_SECONDS = 25.0
MIN_MINUTES = 10.0                 # expected length unknown: a full video is at least this long
REJECT_CODES = ("gone", "expired", "server_blocked", "unreachable", "challenge")


class StageTransport:
    """``PlayCtx.fetch``: ``page()`` reads the site's own pages through ``get(url, page_type) -> Page | None`` (the stage
    fetcher: page cache, foreign-host refusal); every other attribute is ``core.play.transport``'s (looked up per call)."""

    def __init__(self, get: Callable[[str, str], Any]) -> None:
        self._get = get

    def page(self, ctx: Any, url: str, timeout: Optional[float] = None) -> str:
        page = self._get(url, "player")
        if page is None:   # keep the stage fetcher's error code (http_404 ...): a refused intermediate page is retried
            errors = getattr(getattr(self._get, "__self__", None), "errors", None) or []
            last = errors[-1] if errors and isinstance(errors[-1], dict) and errors[-1].get("url") == url else {}
            code = str(last.get("code") or "unavailable")
            status = int(code[5:]) if code.startswith("http_") and code[5:].isdigit() else None
            raise FetchError(code, "page not fetched", status=status, url=url)   # with the Referer (core.play.resolve)
        return page.html

    def __getattr__(self, name: str) -> Any:
        return getattr(transport, name)


def play_ctx(site_map: dict, get: Callable[[str, str], Any], kind: Optional[str] = None):
    """``core.play.PlayCtx`` of ``site_map`` with the stage fetcher, ``None`` without a ``player`` page (``kind``:
    ``episode``/``movie`` puts that type's ``by_type`` resolvers first, T30)."""
    ctx = adapters.play_ctx(site_map, fetch=StageTransport(get), kind=kind)
    return None if ctx is None or isinstance(ctx, dict) else ctx


def placeholder_marker(html: str) -> Optional[dict]:
    """``{kind: iframe|selector, word, src?|selector?}`` when the page carries a "not public" placeholder (a frame whose
    address says telif/copyright/..., or an element outside the menu/header/footer whose class / id / image says it),
    else None. Only meaningful on a page without a player candidate."""
    hint = play_blocked.placeholder_hint(html)
    if hint:
        return {"kind": "iframe", "word": hint["word"], "src": hint["src"]}
    tree = HTMLParser(html or "")
    words = play_blocked.PLACEHOLDER_WORDS
    for node in tree.css("[class], [id], img[src]"):
        if node.tag in ("html", "body", "head") or outline.region_of(node) is not None:
            continue
        attrs = node.attributes
        options: list[tuple[str, str]] = []
        for cls in (attrs.get("class") or "").split():
            if SAFE_CLASS.match(cls):
                options.append((cls, f"{node.tag}.{cls}"))
        ident = attrs.get("id") or ""
        if ident and SAFE_CLASS.match(ident):
            options.append((ident, f"#{ident}"))
        if node.tag == "img":
            name = (urlsplit(attrs.get("src") or "").path.rsplit("/", 1) + [""])[1] or ""
            found = words.search(name)
            if found:
                options.append((name, f'img[src*="{found.group(0).lower()}"]'))
        for token, selector in options:
            found = words.search(token)
            if found:
                try:
                    if tree.css_first(selector) is None:
                        continue
                except Exception:
                    continue
                return {"kind": "selector", "word": found.group(0).lower(), "selector": selector}
    return None


def follow_sample(pctx, url: str, *, html: Optional[str] = None, seconds: float = FOLLOW_SECONDS,
                  all_alternatives: bool = False) -> dict:
    """``core.play.follow`` of one page as evidence: ``{url, ok, stage, error, blocked, reason, candidates, streams, duration,
    alternatives}`` (``candidates`` = ``[{url, host, resolver_type, ok, error}]``; ``all_alternatives``: every source
    alternative resolved to the end, ``alternatives`` = one report each)."""
    res = play_follow.follow(pctx, url, deadline=time.monotonic() + seconds, html=html, all_alternatives=all_alternatives)
    return {"url": url, "ok": bool(res.ok), "stage": res.stage, "error": str(res.error or "")[:200],
            "blocked": bool(res.blocked), "reason": str(res.reason or "")[:200], "candidates": list(res.candidates or [])[:6],
            "streams": list(res.streams or [])[:6], "duration": int(res.duration or 0),
            "alternatives": list(res.alternatives or [])[:12]}


MAX_STREAM_TESTS = 4               # streams of one alternative tried before it counts as not playing
#: how far a failed stream got (the best failure is reported, not the worst)
_STEP_RANK = {"master": 1, "variant": 2, "segment": 3, "decode": 4}


def check_stream(streams: list, locator: str = "") -> dict:
    """``{ok, note, duration_min?, play?, failed?}`` of one alternative's streams: each stream (media only, at most
    :data:`MAX_STREAM_TESTS`, in the provider's order = best first) is tested until one passes (:func:`check_one`); the
    alternative plays when ONE of them plays, the failing ones are named in the note. Nothing plays: the furthest failure
    (playback step) is reported."""
    from ...core.play import direct
    usable, seen = [], set()
    for s in streams or []:
        if isinstance(s, dict) and s.get("url") and s["url"] not in seen:
            seen.add(s["url"])
            usable.append(s)
    usable, junk = direct.media_only(usable)
    if not usable:
        return {"ok": False, "note": "akış yok" + (f" ({direct.DROP_NOTE.format(n=len(junk))})" if junk else "")}
    failed: list[tuple[dict, dict]] = []
    for stream in usable[:MAX_STREAM_TESTS]:
        got = check_one(stream, usable, locator)
        if got["ok"]:
            if failed:
                got["note"] = (got["note"] + "; oynamayan: " + "; ".join(_fail_line(s, c) for s, c in failed))[:300]
                got["failed"] = [{"url": s["url"][:200], "note": c.get("note", "")[:120]} for s, c in failed]
            return got
        failed.append((stream, got))
    best_stream, best = max(failed, key=lambda sc: sc[1].get("rank", 0))
    others = [(s, c) for s, c in failed if s is not best_stream]
    out = dict(best)
    if others:
        out["note"] = (out["note"] + "; diğer: " + "; ".join(_fail_line(s, c) for s, c in others))[:300]
        out["failed"] = [{"url": s["url"][:200], "note": c.get("note", "")[:120]} for s, c in failed]
    return out


def _fail_line(stream: dict, check: dict) -> str:
    name = urlsplit(stream.get("url") or "").path.rsplit("/", 1)[-1][:40] or "?"
    return f"{name}: {str(check.get('note') or '-')[:80]}"


def check_one(stream: dict, usable: list, locator: str = "") -> dict:
    """One stream: the host did not refuse (``quick_check``; uncertain = not refused), the real playback test passes and
    the length is plausible (unknown expected length: >= ``MIN_MINUTES``; an unknown length passes with a note).
    ``rank`` = how far it got (0 refused .. 5 played but too short, 6 plays)."""
    verdict = streamdiag.quick_check([stream])
    if verdict and verdict.get("code") in REJECT_CODES + streamdiag.BLOCKED and not verdict.get("learn"):
        return {"ok": False, "note": f"akış reddedildi ({verdict.get('code')})", "rank": 0}
    summary = streamprobe.summarize(stream["url"], headers=stream.get("request_headers"), kind=stream.get("type"),
                                    locator=locator)
    play = summary.get("play")
    if isinstance(play, dict) or not summary.get("ok"):   # the real playback test decides (user decision), not the address
        from ...core.play import playtest
        if not (play or {}).get("played"):
            note = playtest.summary(play) if play else "akış açılamadı: " + str(summary.get("note") or summary.get("http") or "-")
            rank = _STEP_RANK.get(str((play or {}).get("step") or ""), 0)
            return {"ok": False, "note": note[:200], "rank": rank, **({"play": play} if play else {})}
    policy = _keep_policy(usable, stream, summary)
    minutes = summary.get("duration_min")
    if minutes is not None and minutes < MIN_MINUTES:
        return {"ok": False, "note": f"akış çok kısa ({minutes} dk; fragman/reklam olabilir)", "duration_min": minutes,
                "rank": 5}
    note = f"{minutes} dk" if minutes is not None else "süre ölçülemedi"
    if isinstance(play, dict):
        from ...core.play import playtest
        note += "; " + playtest.summary(play)
    if summary.get("policy_note"):
        note += "; " + summary["policy_note"]
    return {"ok": True, "note": note, "rank": 6, "stream_url": stream["url"][:300],
            **({"duration_min": minutes} if minutes is not None else {}),
            **({"play": play} if isinstance(play, dict) else {}), **policy}


def _keep_policy(usable: list, first: dict, summary: dict) -> dict:
    """The Referer policy the playback test found (``refpolicy``): written onto the streams of the same host (``request_headers``
    + ``referer_policy``, in place: the alternative / map carries it) and remembered for the host, so playback and the stream
    proxy use it without retrying. ``{referer_policy, policy_note?}`` for the check, ``{}`` without one."""
    from ...core.play import refpolicy
    policy = summary.get("referer_policy")
    if not summary.get("ok") or policy not in refpolicy.POLICIES:
        return {}
    host = urlsplit(first.get("url") or "").hostname
    for stream in usable:
        if urlsplit(stream.get("url") or "").hostname == host:
            applied = refpolicy.apply(stream, policy)
            stream.clear()
            stream.update(applied)
    refpolicy.remember(first.get("url") or "", policy)
    return {"referer_policy": policy, **({"policy_note": summary["policy_note"]} if summary.get("policy_note") else {})}


def candidate_hosts(sample: dict) -> list[str]:
    return [c.get("host") or host_key(c.get("url") or "") for c in sample.get("candidates") or []]


def own_page_failure(sample: dict) -> bool:
    """The chain stopped on the site's own player page (no candidate found), not at a provider."""
    return not sample.get("ok") and not sample.get("blocked") and sample.get("stage") in ("page", "discover")
