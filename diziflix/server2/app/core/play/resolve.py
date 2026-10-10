"""One episode / film page -> streams, without the database: the candidate core shared by ``sources`` (playback) and
``follow`` (the single playback verifier). Copied from the old ``library/videos.py`` (``_resolve_candidate``,
``_run_candidates``, ``live_limits``, negative caches), with the site config replaced by :class:`PlayCtx`.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

from . import badhosts, candidates as cands, direct, limits, streamlife, tracks
from .ctx import PlayCtx
from .providers import registry, trace

BLOCKED_PREFIX = "engelli: "


class Blocked(ValueError):
    """The playback page carries a ``blocked:`` placeholder: content that is not public, not a failure of the source."""


def is_blocked(exc: Any) -> bool:
    return isinstance(exc, Blocked) or str(exc).startswith(BLOCKED_PREFIX)


class ResolveError(ValueError):
    """A page that gave no stream: ``stage`` = how far it got (page | discover | provider), ``candidates`` = the per-candidate
    report ``[{url, host, resolver_type, ok, error}]``."""

    def __init__(self, message: str, *, stage: str, candidates: Optional[list] = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.candidates = list(candidates or [])


_neg_lock = threading.Lock()
_neg_candidates: dict = {}
_run_state = threading.local()   # .closed = the Event of the run_candidates call this worker thread serves


def late() -> bool:
    """True inside a candidate worker whose ``run_candidates`` call already returned (deadline passed): such a straggler
    must not write anything (negative cache, ...); its outcome is dropped."""
    closed = getattr(_run_state, "closed", None)
    return bool(closed is not None and closed.is_set())


def _url(value: Any) -> Optional[str]:
    p = urlparse(str(value or ""))
    return str(value) if p.scheme in ("http", "https") and p.hostname else None


def neg_get(cache: dict, key: str) -> Optional[str]:
    with _neg_lock:
        hit = cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        cache.pop(key, None)
    return None


def neg_set(cache: dict, key: str, message: Any) -> None:
    ttl = limits.RESOLVE_NEG_TTL
    if ttl <= 0:
        return
    with _neg_lock:
        if len(cache) > 2000:
            now = time.monotonic()
            for stale in [k for k, v in cache.items() if v[0] <= now]:
                cache.pop(stale, None)
        cache[key] = (time.monotonic() + ttl, str(message)[:250])


def reset_caches() -> None:
    with _neg_lock:
        _neg_candidates.clear()


def candidate_key(raw: dict) -> str:
    handoff = raw.get("handoff") or {}
    form = json.dumps(handoff["form"], sort_keys=True, default=str) if handoff.get("form") else ""
    return "|".join([str(raw.get("url") or ""), str(handoff.get("link") or ""), str(raw.get("label") or "")] + ([form] if form else []))


_LANG_RANK = {"tr": 0, "en": 2}


def rank(candidate: dict, outcome: dict) -> int:
    """Menu order bucket: measured Turkish-subtitle files first, files of unknown subtitle state next, English last."""
    streams = outcome["streams"]
    if streams and not streams[0].get("sub_known"):
        return 1
    return _LANG_RANK.get(candidate.get("lang") or "", 1)


def _with_language(stream: dict, language: Optional[str]) -> dict:
    if not language:
        return stream
    return {**stream, "label": language + " · " + str(stream.get("label") or stream.get("quality") or "Otomatik")}


def _timed_out(candidate: dict, why: str, ms: int) -> dict:
    return {"label": candidate.get("label") or "", "provider": "", "streams": [], "subtitles": [], "audio": [], "duration": 0,
            "error": "zaman aşımı: " + why, "events": [], "ms": ms, "timed_out": True, "lang": candidate.get("lang") or "",
            "resolver_type": candidate.get("resolver_type") or "", "url": str(candidate.get("url") or ""), "provider_url": ""}


SITE_HOPS = 4   # same-site intermediate player pages walked before the provider address (nested iframe chains)


def _same_site(ctx: PlayCtx, url: str, page_url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    own = {(urlparse(u).hostname or "").lower() for u in (page_url, getattr(ctx, "base_url", "") or "")}
    bare = {h[4:] if h.startswith("www.") else h for h in own if h}
    return bool(host) and (host[4:] if host.startswith("www.") else host) in bare


def _next_frame(html: str, url: str) -> str:
    """The address an intermediate page hands over: its first ``iframe[src]``, else a ``meta refresh``."""
    from selectolax.parser import HTMLParser
    tree = HTMLParser(html or "")
    node = tree.css_first("iframe[src]")
    if node is not None and (node.attributes.get("src") or "").strip():
        return urljoin(url, node.attributes["src"].strip())
    for meta in tree.css("meta[http-equiv]"):
        if (meta.attributes.get("http-equiv") or "").lower() == "refresh":
            content = meta.attributes.get("content") or ""
            low = content.lower()
            if "url=" in low:
                return urljoin(url, content[low.index("url=") + 4:].strip(" '\""))
    # no frame: a player address written into the page (data attribute / encoded in a script: atob, hex, packed ...)
    from ..extract import player as x_player
    for cand in x_player.player_candidates(html, url):
        if cand.get("resolver") and cand["url"] != url and not cand.get("trailer") and cand.get("kind") != "ajax":
            return cand["url"]
    return ""


REFERER_NOTE = "ara sayfa Referer ile açıldı"
_REFUSED = (403, 404)


def _refused(exc: Any) -> bool:
    """A 403 / 404 answer (what a page that wants its embedding page as the Referer gives without one)."""
    status = getattr(exc, "status", None)
    code = str(getattr(exc, "code", "") or "")
    return status in _REFUSED or code in ("http_403", "http_404")


def _load_hop(ctx: PlayCtx, url: str, referer: str) -> tuple[str, bool]:
    """``(html, needed_referer)`` of one same-site intermediate page. The site loader gets ``referer`` (the page the address
    was found on) when it takes one; a loader that cannot send it (a stage page cache) is asked first without it, and a
    403 / 404 answer is retried ONCE with the Referer through the playback transport (``needed_referer`` = True then)."""
    from . import gate, transport
    loader = getattr(ctx.fetch, "page", None) or transport.page
    if not referer or gate.takes_referer(loader):
        return gate.load_page(ctx, url, referer=referer), False
    try:
        return gate.load_page(ctx, url), False
    except Exception as exc:
        if not _refused(exc):
            raise
    started = time.monotonic()
    from .resolvers import _util as rutil
    try:
        html = rutil.send(ctx, "GET", url, headers={"Referer": referer}, timeout=10, retries=0)
    except Exception as exc:
        trace.note("site_hop.referer", urlparse(url).hostname or "", False, started, exc)
        raise
    trace.note("site_hop.referer", urlparse(url).hostname or "", True, started, detail=f"{REFERER_NOTE} (Referer: {referer})")
    return html, True


def direct_sources(html: str, page_url: str) -> list:
    """The direct media streams an intermediate player page writes itself (``direct.direct_streams``), only when the page
    has no frame of its own (a frame hands over to a provider). Ads are dropped and traced ("reklam olarak elendi");
    every stream carries the page as its Referer / Origin (what the browser sends for the media requests)."""
    from selectolax.parser import HTMLParser
    from . import direct
    if HTMLParser(html or "").css_first("iframe[src]") is not None:
        return []
    notes: list = []
    found = direct.direct_streams(html, page_url, notes)
    host = urlparse(page_url).hostname or ""
    for line in direct.ads_in(html, page_url):
        trace.note("site_hop.ads", host, True, time.monotonic(), detail=line)
    if not found:
        return []
    parts = urlparse(page_url)
    headers = {"Referer": page_url, "Origin": f"{parts.scheme}://{parts.netloc}"}
    from . import refpolicy
    return [refpolicy.with_known({**s, "request_headers": dict(headers)}) for s in found]   # a measured media policy wins


def walk_site_hops(ctx: PlayCtx, url: str, locator: str) -> dict:
    """A candidate on the site's OWN host that no provider claims is an intermediate player page (``/player/...``):
    it is read through the site transport and its frame / refresh target followed (at most ``SITE_HOPS``), so the
    provider address is what gets resolved (and reported). Every hop is requested with the page it was found on as the
    Referer (a chain: the previous page). ``{url, referer, needs_referer}``: ``url`` = the address reached (unchanged when
    it is not a same-site intermediate page), ``referer`` = the page that address was found on, ``needs_referer`` = an
    intermediate page answered only with the Referer."""
    current, referer, seen, needs = url, locator, set(), False
    for _ in range(SITE_HOPS):
        if current in seen or not _same_site(ctx, current, locator):
            break
        if any(p.matches(current) for p in registry.providers()):
            break
        seen.add(current)
        started = time.monotonic()
        try:
            html, needed = _load_hop(ctx, current, referer)
        except Exception as exc:
            trace.note("site_hop", urlparse(current).hostname or "", False, started, exc)
            raise ValueError(f"sitenin ara oynatıcı sayfası açılamadı ({current}): {exc}") from exc
        needs = needs or needed
        found = direct_sources(html, current)
        if found:   # a JW Player / Video.js / <video> config with the media itself: no provider needed
            trace.note("site_hop.direct", urlparse(current).hostname or "", True, started,
                       detail=f"{len(found)} doğrudan akış (sağlayıcısız), Referer: {current}")
            return {"url": current, "referer": referer, "needs_referer": needs, "streams": found}
        nxt = _next_frame(html, current)
        trace.note("site_hop", urlparse(current).hostname or "", bool(nxt), started, "" if nxt else "ara sayfada çerçeve yok",
                   detail=f"Referer: {referer}" if referer else "")
        if not nxt or not _url(nxt):
            raise ValueError(f"sitenin ara oynatıcı sayfasında sağlayıcı çerçevesi yok ({current})")
        current, referer = nxt, current
    return {"url": current, "referer": referer, "needs_referer": needs}


def site_hops(ctx: PlayCtx, url: str, locator: str) -> str:
    """The address :func:`walk_site_hops` reaches."""
    return walk_site_hops(ctx, url, locator)["url"]


def resolve_one(ctx: PlayCtx, raw: dict, locator: str, *, source_id: str = "", load_cookies: Any = None,
                use_neg: bool = False) -> dict:
    """One provider candidate, start to finish (hand-off -> provider -> streams). Never raises: a failure comes back as
    ``error``. ``{label, provider, provider_url, streams, subtitles, audio, duration, error, events, ms, lang, resolver_type}``."""
    out = {"label": raw.get("label") or "", "provider": "", "provider_url": "", "streams": [], "subtitles": [], "audio": [],
           "duration": 0, "error": "", "events": [], "ms": 0, "lang": raw.get("lang") or "",
           "resolver_type": raw.get("resolver_type") or "", "url": str(raw.get("url") or "")}
    started = time.monotonic()
    trace.begin()
    key = candidate_key(raw)
    try:
        cached_error = neg_get(_neg_candidates, key) if use_neg else None
        if cached_error:
            raise ValueError("kısa süre önce başarısız oldu: " + cached_error)
        candidate = cands.resolve_candidate(ctx, raw, locator, load_cookies)
        if not candidate:
            raise ValueError("sağlayıcı yönlendirmesi çözülemedi")
        candidate_url = urljoin(locator, candidate.get("url") or "")
        if not _url(candidate_url):
            raise ValueError("geçersiz sağlayıcı adresi")
        referer = str(candidate.get("referer") or locator)   # the page the provider address was found on
        if not isinstance(candidate.get("stream"), dict):
            hops = walk_site_hops(ctx, candidate_url, referer)
            candidate_url, referer = hops["url"], hops["referer"]
            if hops["needs_referer"]:
                out["needs_referer"] = True
            if hops.get("streams"):   # the intermediate page holds the media itself (direct stream, no provider)
                candidate = {**candidate, "stream": {"provider": candidate.get("label") or raw.get("label")
                                                     or (urlparse(candidate_url).hostname or ""), "streams": hops["streams"]}}
                out["direct"] = True
        out["provider_url"] = candidate_url
        out["referer"] = referer
        out["resolver_type"] = out["resolver_type"] or candidate.get("resolver_type") or ""
        if isinstance(candidate.get("stream"), dict) and candidate["stream"]:
            resolved = candidate["stream"]   # json_api / player_page: the type already produced the media
        else:
            resolved = registry.resolve(candidate_url, referer=referer, allowed=ctx.providers_allow)
        if not resolved or not resolved.get("streams"):
            raise ValueError("sağlayıcı akış vermedi")
        provider = resolved.get("provider") or candidate.get("label") or raw.get("label")
        out["provider"] = provider or ""
        out["duration"] = resolved.get("duration_s", resolved.get("duration", 0)) or 0
        out["cache_ttl"] = streamlife.provider_ttl(resolved.get("cache_ttl"))
        found = [{**s, "url": _url(s.get("url")), "provider": provider} for s in resolved["streams"] if _url(s.get("url"))]
        trackers = sorted({h for h in (badhosts.bad_stream_host(s["url"]) for s in found) if h})
        if trackers:
            found = [s for s in found if not badhosts.bad_stream_host(s["url"])]
            if not found:
                trace.note("stream_host", trackers[0], False, started, "reklam/izleyici adresi, video değil: " + ", ".join(trackers))
                raise ValueError("sağlayıcı yalnız reklam/izleyici adresi verdi (" + ", ".join(trackers) + ")")
        found, junk = direct.media_only(found)
        if junk:   # scripts / styles / images a loose rule matched: never a stream (A6 "çöp akış")
            line = direct.DROP_NOTE.format(n=len(junk))
            out["dropped"] = junk[:8]
            trace.note("stream_filter", urlparse(junk[0]).hostname or "", True, started,
                       detail=line + ": " + ", ".join(u.rsplit("/", 1)[-1][:40] for u in junk[:4]))
            if not found:
                raise ValueError(f"sağlayıcı medya akışı vermedi ({line})")
        if not found:
            raise ValueError("sağlayıcı geçerli akış vermedi")
        soft = [t for t in (resolved.get("subtitles") or []) if isinstance(t, dict) and _url(t.get("url"))
                and (t.get("format") or "vtt") in ("vtt", "srt")]
        variant = tracks.variant_id(source_id or "follow", str(resolved.get("variant") or ""), found[0]["url"])
        fields = {**tracks.describe(provider or "", raw.get("lang"), raw.get("language"), soft), "variant_id": variant}
        language = tracks.label_language(fields, raw.get("language"))
        out["streams"] = [{**_with_language(s, language), **fields} for s in found]
        out["subtitles"] = [{**t, "variant_id": variant, "referer": t.get("referer") or out["provider_url"]} for t in soft]
        out["audio"] = [a for a in resolved.get("audio") or [] if isinstance(a, dict)]
    except Exception as exc:
        out["error"] = trace.short(exc)
        out["streams"], out["subtitles"], out["audio"] = [], [], []
    out["events"] = trace.take()
    out["ms"] = int((time.monotonic() - started) * 1000)
    if use_neg and not late() and out["error"] and not out["error"].startswith("ValueError: kısa süre önce"):   # measurement runs never feed it
        neg_set(_neg_candidates, key, out["error"])
    return out


def _candidate_timeout(candidate: Any) -> float:
    value = candidate.get("timeout") if isinstance(candidate, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


def live_limits(candidates: list) -> tuple:
    per, total = limits.RESOLVE_CANDIDATE_TIMEOUT, limits.RESOLVE_TOTAL_TIMEOUT
    pers = [max(per, _candidate_timeout(c)) for c in candidates]
    longer = [p for p in pers if p > per]
    if longer:
        total = max(total, max(longer) + 2)
    return pers, total


def run_candidates(candidates: list, run_one: Callable[[dict], dict], *, deadline: Optional[float] = None,
                   wait_all: bool = False) -> list:
    """Resolve candidates with bounded parallelism, a per-candidate and a total time limit (``deadline`` = an absolute
    ``time.monotonic()`` that caps it further) and a short grace period once one produced streams (``wait_all``: no grace,
    every alternative gets its own time: measurements of ALL sources). Outcomes in order."""
    n = len(candidates)
    outcomes: list = [None] * n
    if not n:
        return outcomes
    grace = limits.RESOLVE_GRACE
    pers, total = live_limits(candidates)
    starts: dict = {}
    closed = threading.Event()   # set when this call returns: workers still running are stragglers (see late())

    def job(i):
        _run_state.closed = closed
        starts[i] = time.monotonic()
        return run_one(candidates[i])
    t0 = time.monotonic()
    if deadline is not None:
        total = max(0.0, min(total, deadline - t0))
    pool = ThreadPoolExecutor(max_workers=max(1, min(limits.RESOLVE_PARALLEL, n)), thread_name_prefix="resolve")
    futures = {pool.submit(contextvars.copy_context().run, job, i): i for i in range(n)}   # keeps registry.overlay
    pending = set(futures)
    first_ok = None
    try:
        while pending:
            now = time.monotonic()
            hard = t0 + total
            if first_ok is not None and not wait_all:
                hard = min(hard, first_ok + grace)
            soft = min([starts[futures[f]] + pers[futures[f]] for f in pending if futures[f] in starts] or [hard])
            if any(futures[f] not in starts for f in pending):
                soft = min(soft, now + 0.05)
            done, pending = wait(pending, timeout=max(0.0, min(hard, soft) - now), return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    outcomes[futures[f]] = f.result()
                except Exception as exc:
                    outcomes[futures[f]] = {**_timed_out(candidates[futures[f]], "", 0), "timed_out": False,
                                            "error": str(exc)[:160]}
                if outcomes[futures[f]]["streams"] and first_ok is None:
                    first_ok = time.monotonic()
            now = time.monotonic()
            if now >= min(hard, t0 + total):
                break
            expired = {f for f in pending if futures[f] in starts and starts[futures[f]] + pers[futures[f]] <= now}
            for f in expired:
                outcomes[futures[f]] = _timed_out(candidates[futures[f]], f"aday süresi ({pers[futures[f]]:g} sn) doldu",
                                                  int((now - starts[futures[f]]) * 1000))
            pending -= expired
    finally:
        now = time.monotonic()
        why = (f"ilk akıştan sonra {grace:g} sn bekleme süresi doldu" if first_ok is not None and now >= first_ok + grace - 0.05
               else f"toplam süre ({total:g} sn) doldu")
        for f in pending:
            i = futures[f]
            outcomes[i] = _timed_out(candidates[i], why, int((now - starts.get(i, t0)) * 1000))
        closed.set()   # threads are not waited for; whatever they produce from now on is dropped, never written
        pool.shutdown(wait=False, cancel_futures=True)
    return outcomes


def candidate_report(candidate: dict, outcome: Optional[dict]) -> dict:
    """``{url, host, resolver_type, ok, error}`` of one candidate (README §5.2 ``candidates[]``; T09/T13 read it). ``host`` =
    the provider host it reached (else the candidate URL's host)."""
    outcome = outcome or {}
    url = outcome.get("provider_url") or str(candidate.get("url") or "")
    ok = bool(outcome.get("streams"))
    error = ""
    if not ok:
        events = outcome.get("events") or []
        last = events[-1] if events else {}
        error = last.get("error") or outcome.get("error") or "akış yok"
    return {"url": url, "host": (urlparse(url).hostname or "").lower(), "resolver_type": outcome.get("resolver_type")
            or candidate.get("resolver_type") or "", "ok": ok, "error": str(error)[:160],
            **({"provider": outcome["provider"]} if outcome.get("provider") else {}),
            **({"ms": outcome["ms"]} if outcome.get("ms") else {}),
            **({"dropped": list(outcome["dropped"])} if outcome.get("dropped") else {})}


def merge(candidates: list, outcomes: list, *, source_id: str = "", kind: str = "", canonical_id: str = "",
          episode_id: str = "", locator: str = "", rank_by_duration: bool = False) -> dict:
    """The streams of every candidate merged into one payload ``{streams, subtitles, audio, duration, cache_ttl?}``:
    menu order, re-signed duplicates once, spare copies marked, optional duration ranking."""
    order = list(range(len(outcomes)))
    fast = limits.RESOLVE_FAST_FIRST
    order.sort(key=lambda i: (rank(candidates[i], outcomes[i]),
                              (0 if outcomes[i]["streams"] else 1, outcomes[i]["ms"] // 500 if outcomes[i]["streams"] else 0)
                              if fast else (0, 0), i))
    result: dict = {"streams": [], "subtitles": [], "audio": [], "duration": 0}
    seen_streams: set = set()
    seen_subs: set = set()
    seen_audio: set = set()
    for i in order:
        outcome = outcomes[i]
        for stream in outcome["streams"]:
            identity = tracks.file_identity(stream)
            if identity in seen_streams:
                continue
            seen_streams.add(identity)
            result["streams"].append(stream)
        for sub in outcome.get("subtitles") or []:
            identity = (sub["url"], sub.get("variant_id"))
            if identity not in seen_subs:
                seen_subs.add(identity)
                result["subtitles"].append(sub)
        for audio in outcome.get("audio") or []:
            identity = (audio.get("lang"), audio.get("label"))
            if identity not in seen_audio:
                seen_audio.add(identity)
                result["audio"].append(audio)
        result["duration"] = result["duration"] or outcome["duration"]
    if rank_by_duration and limits.STREAM_PROBE_RANK and kind != "trailer" and canonical_id:
        from . import streamprobe
        result["streams"] = streamprobe.rank_streams(result["streams"], streamprobe.expected_runtime(canonical_id, episode_id),
                                                     locator)
    tracks.share_audio(result["streams"])
    result["streams"] = tracks.link_mirrors(result["streams"])
    ttls = [o["cache_ttl"] for o in outcomes if o and o.get("streams") and o.get("cache_ttl")]
    if ttls:
        result["cache_ttl"] = min(ttls)
    return result
