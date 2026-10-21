"""Content that is not public: page verdicts for one site (the old ``library/gate.py``, VERDICTS ONLY).

The site map's ``gate: {require, probe, blocked: [...]}`` (README §5.5) says what is not public: ``blocked`` rules recognise a known
placeholder; ``require`` (player | stream) is the general rule "no player on the page = not public". This module only JUDGES;
storing a verdict is ``core.catalog.write.mark_blocked`` (the caller decides). No database.

Decisions (``series_verdict`` / ``item_verdict``): every checked page of a series blocked -> the series is blocked; some blocked ->
only those episodes; a transient error (HTTP 5xx / 403 / 429, timeout, network) is NOT "no player" (``retry``). A page that is gone
(HTTP 404 / 410) is "no player" only with a gate.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

from . import blocked as sblocked, limits
from .ctx import PlayCtx

OK, BLOCKED, RETRY = "ok", "blocked", "retry"
KIND_EPISODE, KIND_MOVIE, KIND_SERIES = "episode", "movie", "series"
MAX_CANDIDATES = 6
_HTTP = re.compile(r"http_(\d{3})")

_sleep = time.sleep   # tests replace it


class Pacer:
    """Politeness between the page fetches of one pass (the first one is free)."""

    def __init__(self, sleep: Optional[Callable[[float], None]] = None) -> None:
        self.sleep = sleep
        self.fetched = False

    def before_fetch(self) -> None:
        if self.fetched and limits.SERIES_CRAWL_DELAY > 0:
            (self.sleep or _sleep)(limits.SERIES_CRAWL_DELAY)
        self.fetched = True


# --- what a site configured -------------------------------------------------------------------------------------------

def rules(ctx: PlayCtx) -> list:
    return sblocked.rules_of((ctx.gate or {}).get("blocked"))


def episode_rules(ctx: PlayCtx) -> list:
    return [r for r in rules(ctx) if r.get("on") == "episode_page"]


def series_rules(ctx: PlayCtx) -> list:
    return [r for r in rules(ctx) if r.get("on") == "series_page"]


def gate_of(ctx: PlayCtx) -> dict:
    """``{probe, require}`` of the site map gate (``{}`` = no gate / switched off)."""
    raw = {k: v for k, v in (ctx.gate or {}).items() if k != "blocked"}
    return sblocked.gate_of(raw) if raw else {}


def probe_count(ctx: PlayCtx) -> int:
    gate = gate_of(ctx)
    return int(gate["probe"]) if gate else sblocked.DEFAULT_PROBE


def validate(raw: Any) -> list:
    return sblocked.validate(raw)


def validate_gate(raw: Any) -> list:
    return sblocked.validate_gate(raw)


def placeholder_hint(html: str) -> Optional[dict]:
    return sblocked.placeholder_hint(html)


# --- the page verdict -------------------------------------------------------------------------------------------------

def _verdict(state: str, reason: str = "", via: str = "", **extra: Any) -> dict:
    return {"state": state, "reason": reason[:200], "via": via, **extra}


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def http_status(exc: Exception) -> Optional[int]:
    status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    found = _HTTP.search(str(exc))
    return int(found.group(1)) if found else None


def _resolve_streams(ctx: PlayCtx, candidates: list, url: str) -> tuple:
    """``(a stream resolved, every failure looked transient)`` for ``require: stream`` (no database)."""
    from . import resolve
    outcomes = resolve.run_candidates(candidates[:MAX_CANDIDATES], lambda raw: resolve.resolve_one(ctx, raw, url))
    if any((o or {}).get("streams") for o in outcomes):
        return True, False
    errors = [str((o or {}).get("error") or "") for o in outcomes]
    return False, bool(errors) and all(e.startswith(("zaman aşımı", "ValueError: kısa süre önce")) or "timeout" in e.lower()
                                       for e in errors)


def judge_html(ctx: PlayCtx, html: str, url: str, *, kind: str = KIND_EPISODE, require: Optional[str] = None,
               rules_: Optional[list] = None, htmls: Optional[list] = None) -> dict:
    """The verdict of ONE fetched page (no network except ``require: stream``): ``{state: ok|blocked|retry, reason,
    via: rule|gate|'', evidence?, candidates?}``."""
    on = "series_page" if kind == KIND_SERIES else "episode_page"
    if rules_ is None:
        rules_ = series_rules(ctx) if kind == KIND_SERIES else episode_rules(ctx)
    for text in [html, *(htmls or [])]:
        hit = sblocked.match(text, rules_, on)
        if hit:
            return _verdict(BLOCKED, hit["reason"], "rule", evidence=hit["evidence"], rule=hit["rule"], matched=hit["by"])
    if require and kind != KIND_SERIES:
        from . import candidates as cands
        try:
            found = cands.discover(ctx, html, url)
        except Exception as exc:
            return _verdict(RETRY, "oyuncu keşfi hata verdi: " + _short(exc))
        if not found:
            return _verdict(BLOCKED, "sayfada oynatıcı bulunamadı", "gate", candidates=0)
        if require == "stream":
            try:
                got, transient = _resolve_streams(ctx, found, url)
            except Exception as exc:
                return _verdict(RETRY, "akış çözümü hata verdi: " + _short(exc), candidates=len(found))
            if not got:
                if transient:
                    return _verdict(RETRY, "akış çözümü zaman aşımına uğradı", candidates=len(found))
                return _verdict(BLOCKED, "oynatıcı var ama akış çözülemedi", "gate", candidates=len(found))
        return _verdict(OK, candidates=len(found))
    return _verdict(OK)


def error_verdict(exc: Exception, require: Optional[str] = None) -> dict:
    status = http_status(exc)
    if status in (404, 410):
        if require:
            return _verdict(BLOCKED, f"sayfa bulunamadı (HTTP {status})", "gate", gone=True)
        return _verdict(OK, f"sayfa bulunamadı (HTTP {status})", gone=True)
    return _verdict(RETRY, "geçici hata: " + _short(exc))


def load_page(ctx: PlayCtx, url: str, timeout: Optional[float] = None, referer: str = "") -> str:
    """The HTML of a site page through ``ctx.fetch.page`` (``core.fetch`` by default); ``timeout`` (seconds) is passed on,
    ``referer`` too when the loader takes one (see :func:`takes_referer`)."""
    from . import transport
    loader = getattr(ctx.fetch, "page", None) or transport.page
    kw: dict = {} if timeout is None else {"timeout": timeout}
    if referer and takes_referer(loader):
        kw["referer"] = referer
    return transport.text_of(loader(ctx, url, **kw))


def takes_referer(loader: Any) -> bool:
    """True when a site page loader accepts a ``referer`` keyword (the default transport does; a stage page cache does not)."""
    import inspect as _inspect
    try:
        params = _inspect.signature(loader).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "referer" or p.kind is p.VAR_KEYWORD for p in params)


def fetch_verdict(ctx: PlayCtx, url: str, *, kind: str = KIND_EPISODE, require: Optional[str] = None,
                  fetcher: Optional[Callable[[PlayCtx, str], str]] = None) -> dict:
    try:
        html = (fetcher or load_page)(ctx, url)
    except Exception as exc:
        return error_verdict(exc, require)
    if not html:
        return _verdict(RETRY, "boş sayfa")
    return judge_html(ctx, html, url, kind=kind, require=require)


# --- series / film verdicts -------------------------------------------------------------------------------------------

def pick_pages(episodes: list, probe: int) -> list:
    usable = sorted((e for e in episodes if isinstance(e, dict) and e.get("url") and isinstance(e.get("season"), int)
                     and isinstance(e.get("episode"), int)), key=lambda e: (e["season"], e["episode"]))
    if not usable or probe <= 0:
        return []
    order = [usable[-1], usable[0], usable[len(usable) // 2]]
    out: list = []
    for entry in order:
        if entry["url"] not in {p["url"] for p in out} and len(out) < probe:
            out.append(entry)
    return out


def series_verdict(ctx: PlayCtx, episodes: list, *, known: Optional[dict] = None, probe: Optional[int] = None,
                   require: Optional[str] = None, pacer: Optional[Pacer] = None, fetcher: Optional[Callable] = None,
                   judge: Optional[Callable[[str], dict]] = None) -> dict:
    """Judge up to ``probe`` pages of one series' inventory (``known`` = ``{url: reason}`` already blocked, not fetched).
    ``{state, reason, via, probed, pages: [...], blocked: [url], retry: [url]}``."""
    if require is None:
        require = gate_of(ctx).get("require")
    probe = probe_count(ctx) if probe is None else probe
    pacer = pacer or Pacer()
    picks = pick_pages(episodes, probe)
    out: dict = {"state": OK, "reason": "", "via": "", "probed": 0, "pages": [], "blocked": [], "retry": []}
    if not picks or not (require or episode_rules(ctx)):
        return out
    for entry in picks:
        url = entry["url"]
        if known and url in known:
            verdict = _verdict(BLOCKED, known[url] or sblocked.DEFAULT_REASON, "rule", cached=True)
        else:
            if judge:
                verdict = judge(url)
            else:
                pacer.before_fetch()
                verdict = fetch_verdict(ctx, url, kind=KIND_EPISODE, require=require, fetcher=fetcher)
            out["probed"] += 1
        out["pages"].append({"url": url, "season": entry["season"], "episode": entry["episode"], **verdict})
        if verdict["state"] == BLOCKED:
            out["blocked"].append(url)
        elif verdict["state"] == RETRY:
            out["retry"].append(url)
    states = [p["state"] for p in out["pages"]]
    if OK in states:
        out["state"] = OK
    elif RETRY in states:
        out["state"] = RETRY
        out["reason"] = next(p["reason"] for p in out["pages"] if p["state"] == RETRY)
    else:
        out["state"] = BLOCKED
        first = out["pages"][0]
        out["reason"], out["via"] = first["reason"], first["via"]
    return out


def item_verdict(ctx: PlayCtx, url: str, *, require: Optional[str] = None, pacer: Optional[Pacer] = None,
                 fetcher: Optional[Callable] = None, judge: Optional[Callable[[str], dict]] = None) -> dict:
    """The verdict of ONE playback page (a film): the same shape as ``series_verdict`` with a single page."""
    if require is None:
        require = gate_of(ctx).get("require")
    if judge:
        verdict = judge(url)
    else:
        (pacer or Pacer()).before_fetch()
        verdict = fetch_verdict(ctx, url, kind=KIND_EPISODE, require=require, fetcher=fetcher)
    return {"state": verdict["state"], "reason": verdict.get("reason") or "", "via": verdict.get("via") or "", "probed": 1,
            "pages": [{"url": url, **verdict}], "blocked": [url] if verdict["state"] == BLOCKED else [],
            "retry": [url] if verdict["state"] == RETRY else []}


def drop_episodes(entries: list, blocked_urls: set) -> list:
    if not blocked_urls:
        return list(entries)
    return [e for e in entries if not (isinstance(e, dict) and e.get("kind", "episode") == "episode" and e.get("url") in blocked_urls)]
