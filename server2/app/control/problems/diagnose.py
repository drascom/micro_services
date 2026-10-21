"""LLM-free diagnosis (T13 item 4, ARCHITECTURE-PROPOSAL §6.3): measured evidence -> ``{class, layer, page_type, action, evidence}``.

Each source row of the problem's content is followed for real (``core.play.follow``, forced: no stored resolution, no negative
cache), resolved streams are probed (``streamdiag.diagnose`` with the page as Referer candidate) and, when the expected length is
known, measured (``streamprobe``). Every verdict that rejects something writes the MEASUREMENT it rests on into ``evidence``
(no rejection by a host name / title guess). The best row decides (the most fixable class first).
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlparse

from ... import db
from ...core.play import follow as play_follow, gate, hooks as play_hooks, streamdiag, streamprobe
from ...core.play.providers import registry

FOLLOW_SECONDS = 40.0
PROBE_SECONDS = 15.0
MAX_ROWS = 4

# class -> (layer, page_type, action)
TABLE: dict[str, tuple[str, str, str]] = {
    "recovered": ("-", "", "verify"),                     # a stream plays server side now (no client failure on record)
    "client_side": ("-", "", "verify"),                   # server side is sound; the client failed
    "host_needs_headers": ("host", "", "learn_host_rule"),
    "wrong_media": ("resolvers", "player", "next_candidate"),
    "unknown_host": ("provider", "sources", "provider_dry_run"),
    "provider_changed": ("provider", "sources", "repair"),
    "stream_refused": ("provider", "sources", "repair"),
    "player_not_found": ("resolvers", "player", "repair"),
    "capability_missing": ("capability", "sources", "waiting_capability"),
    "page_unreachable": ("fetch", "", "retry"),
    "no_source": ("content", "", "search"),
    "no_player": ("content", "", "close_not_public"),
    "not_public": ("content", "", "close_not_public"),
    "image_broken": ("image", "", "admin"),
    # T14 system problems (site scope; their page type comes from the problem row)
    "structure_changed": ("map", "", "repair"),
    "missing_field": ("map", "title", "repair"),
    "links_gone": ("content", "title", "admin"),          # 404/410 pages: sources already off; no map to repair, no LLM
}
ORDER = list(TABLE)   # the most fixable first

# embed players that are a whole product of their own (no provider can turn them into a stream yet): an engine capability.
# YouTube is not here: the provider library knows it (opt-in ``registry.YOUTUBE``) -> a known provider (``known_provider``).
EMBED_ONLY = re.compile(r"(^|\.)(vimeo\.com|dailymotion\.com)$", re.I)
#: opt-in providers (outside a site page's default pool) that still count as "known" for the diagnosis
OPT_IN = [registry.YOUTUBE]
# a player is there even though no resolver picked it up
_TRACE = [("iframe", re.compile(r"<iframe\b", re.I)), ("video", re.compile(r"<video\b", re.I)),
          ("media_url", re.compile(r"\.(?:m3u8|mp4)\b", re.I)),
          ("player_script", re.compile(r"jwplayer|videojs|video\.js|plyr|clappr|hls\.js|flowplayer|playerjs", re.I)),
          ("player_attr", re.compile(r"data-(?:video|embed|player|src)\s*=|id=[\"'][^\"']*player", re.I))]


def player_trace(html: str) -> dict:
    """``{iframe: n, video: n, ...}`` of player traces on a page (only the non-zero ones)."""
    out = {}
    for name, rx in _TRACE:
        n = len(rx.findall(html or ""))
        if n:
            out[name] = n
    return out


def _host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def known_provider(url: str) -> Optional[str]:
    for provider in registry.providers(extra=OPT_IN):
        try:
            if provider.matches(url):
                return provider.name
        except Exception:
            continue
    return None


def probe_streams(streams: list, locator: str = "", timeout: float = PROBE_SECONDS) -> Optional[dict]:
    """``streamdiag.diagnose`` of ``streams`` with the page as Referer candidate; None = uncertain (nothing probeable / no answer)."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="problem-probe")
    try:
        future = pool.submit(streamdiag.diagnose, {"streams": [s for s in streams if isinstance(s, dict)], "locator": locator})
        return future.result(timeout=max(0.1, timeout))
    except Exception:
        return None
    finally:
        pool.shutdown(wait=False)


def refused(verdict: Optional[dict]) -> bool:
    return bool(verdict) and verdict.get("code") != "reachable"


def duration_check(streams: list, duration_s: int, canonical_id: str, episode_id: str, locator: str = "") -> dict:
    """``{match, duration_s, expected_min}``; ``match`` ok | short | long | unknown (no expected length -> unknown, no probe)."""
    expected = streamprobe.expected_runtime(canonical_id, episode_id)
    if not expected:
        return {"match": "unknown", "duration_s": duration_s, "expected_min": None}
    if not duration_s and streams:
        first = streams[0]
        summary = streamprobe.summarize(first.get("url"), first.get("request_headers"),
                                        first.get("type") if first.get("type") in ("hls", "mp4") else None, locator, expected)
        return {"match": summary.get("duration_match", "unknown"), "duration_min": summary.get("duration_min"),
                "expected_min": expected}
    return {"match": streamprobe.duration_match(duration_s, expected), "duration_s": duration_s, "expected_min": expected}


def rows_of(problem: dict, include_broken: bool = True) -> list:
    statuses = ("disabled", "blocked") if include_broken else ("disabled", "blocked", "broken")
    return db.query(f"SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' AND status NOT IN "
                    f"({','.join('?' * len(statuses))}) ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END, "
                    f"source, id LIMIT ?", (problem.get("canonical_id") or "", problem.get("episode_id") or "", *statuses, MAX_ROWS))


def _verdict(klass: str, row: Any = None, **evidence: Any) -> dict:
    layer, page_type, action = TABLE[klass]
    out = {"class": klass, "layer": layer, "page_type": page_type, "action": action, "evidence": evidence}
    if row is not None:
        out.update(site_id=row["source"], source_id=row["id"], locator=row["locator"])
    return out


def _client_engines(problem: dict) -> set:
    if not problem.get("id"):
        return set()
    rows = db.query("SELECT data FROM problem_events WHERE problem_id=? AND kind='playback_failed'", (problem["id"],))
    engines = set()
    for r in rows:
        try:
            engine = (json.loads(r["data"] or "{}") or {}).get("engine")
        except ValueError:
            engine = None
        if engine:
            engines.add(engine)
    return engines


def diagnose_row(problem: dict, row: Any) -> dict:
    """The verdict of one ``video_sources`` row."""
    cid, ep = problem.get("canonical_id") or "", problem.get("episode_id") or ""
    locator = row["locator"]
    if row["resolver"] in ("direct", "embed"):
        streams = [{"url": locator, "type": "embed" if row["resolver"] == "embed" else row["media_type"]}]
        result_duration, candidates = 0, []
    elif row["resolver"] == "provider":   # admin-added provider address: the provider library resolves it, no site page
        try:
            got = registry.resolve(locator, referer="", extra=[registry.YOUTUBE]) or {}
        except Exception as exc:
            got = {"error": " ".join(str(exc).split())[:200]}
        streams = [s for s in got.get("streams") or [] if isinstance(s, dict) and s.get("url")]
        if not streams:
            out = _verdict("provider_changed", row, stage="provider", provider=known_provider(locator) or "",
                           error=got.get("error") or "sağlayıcı akış vermedi")
            out["host"] = _host(locator)
            return out
        result_duration, candidates = int(got.get("duration_s") or 0), []
    else:
        ctx = play_hooks.ctx_for(row["source"])
        started = time.monotonic()
        try:
            html = gate.load_page(ctx, locator, timeout=FOLLOW_SECONDS / 2)
        except Exception as exc:
            return _verdict("page_unreachable", row, stage="page", error=" ".join(str(exc).split())[:200],
                            ms=int((time.monotonic() - started) * 1000))
        res = play_follow.follow(ctx, locator, html=html, deadline=time.monotonic() + FOLLOW_SECONDS)
        if res.blocked:
            return _verdict("not_public", row, stage=res.stage, reason=res.reason, error=res.error)
        if res.stage == "page":
            return _verdict("page_unreachable", row, stage="page", error=res.error)
        if res.stage == "discover" and not res.candidates:
            trace = player_trace(html)
            if trace:
                return _verdict("player_not_found", row, stage="discover", trace=trace, error=res.error)
            return _verdict("no_player", row, stage="discover", trace={}, error=res.error,
                            note="sayfa geldi; iframe/video/oynatıcı betiği/medya adresi yok")
        if not res.ok:
            cands = res.candidates or []
            known = [(c, c.get("provider") or known_provider(c.get("url") or "")) for c in cands]
            if any(name for _, name in known):
                c = next(c for c, name in known if name)
                out = _verdict("provider_changed", row, stage=res.stage, candidates=cands[:4], provider=next(n for _, n in known if n),
                               error=c.get("error") or res.error)
                out["host"] = c.get("host") or _host(c.get("url"))
                if out["evidence"]["provider"] in {p.name for p in OPT_IN}:
                    out["evidence"]["opt_in"] = True
                    out["evidence"]["note"] = "sağlayıcı biliniyor ama sitenin oynatıcı havuzunda değil (isteğe bağlı sağlayıcı)"
                return out
            embed = next((c for c in cands if EMBED_ONLY.search(c.get("host") or _host(c.get("url")))), None)
            if embed:
                out = _verdict("capability_missing", row, stage=res.stage, candidates=cands[:4],
                               gap_key=f"embed:{(embed.get('host') or _host(embed.get('url'))).removeprefix('www.')}")
                out["host"] = embed.get("host") or _host(embed.get("url"))
                return out
            first = cands[0] if cands else {}
            out = _verdict("unknown_host", row, stage=res.stage, candidates=cands[:4], error=res.error,
                           player_url=first.get("url") or "", referer=locator)
            out["host"] = first.get("host") or _host(first.get("url"))
            return out
        streams, result_duration, candidates = res.streams, res.duration, res.candidates
    verdict = probe_streams(streams, locator)
    probe_ev = {k: verdict.get(k) for k in ("code", "http", "learn", "stream")} if verdict else None
    if refused(verdict):
        stream_host = ((verdict.get("stream") or {}).get("host")) or _host(streams[0].get("url"))
        if verdict.get("learn"):
            headers = verdict.get("learned_headers") or {}
            out = _verdict("host_needs_headers", row, probe=probe_ev, learned_headers=headers,
                           reason="ip" if verdict.get("code") == "ip_bound" else "referer")
        else:
            out = _verdict("stream_refused", row, probe=probe_ev)
        out["host"] = stream_host
        return out
    dur = duration_check(streams, result_duration, cid, ep, locator)
    if dur["match"] == "short":
        return _verdict("wrong_media", row, probe=probe_ev, duration=dur,
                        streams=[{"url": s.get("url"), "type": s.get("type")} for s in streams[:6]])
    engines = _client_engines(problem)
    if len(engines) >= 2:
        return _verdict("stream_refused", row, probe=probe_ev, duration=dur, engines=sorted(engines),
                        note="sunucu yoklaması iyi ama iki ayrı istemci motoru oynatamadı")
    if engines:
        return _verdict("client_side", row, probe=probe_ev, duration=dur, engines=sorted(engines),
                        note="sunucu tarafı sağlam")
    return _verdict("recovered", row, probe=probe_ev, duration=dur, candidates=candidates[:4])


def diagnose(problem: dict) -> dict:
    """``{class, layer, page_type, action, evidence, site_id?, source_id?, host?, rows[]}`` of a problem. LLM-free."""
    if problem.get("class") == "image_broken":
        return _verdict("image_broken", None, **(problem.get("evidence") or {}))
    if problem.get("scope") in ("site", "host"):
        klass = problem.get("class") or "player_not_found"
        out = _verdict(klass if klass in TABLE else "player_not_found", None, inherited=True)
        out["action"] = "admin" if out["action"] == "admin" else "repair"
        out["page_type"] = problem.get("page_type") or out["page_type"]   # T14: a system problem names its page type
        return out
    rows = rows_of(problem)
    if not rows:
        return _verdict("no_source", None, note="bu içerik için kayıtlı kaynak yok")
    verdicts = []
    for row in rows:
        try:
            verdicts.append(diagnose_row(problem, row))
        except Exception as exc:   # one broken row never stops the diagnosis
            verdicts.append(_verdict("page_unreachable", row, stage="diagnose", error=f"{type(exc).__name__}: {exc}"[:200]))
    best = min(verdicts, key=lambda v: ORDER.index(v["class"]))
    best = dict(best)
    best["rows"] = [{"source_id": v.get("source_id"), "site": v.get("site_id"), "class": v["class"]} for v in verdicts]
    if best["class"] in ("not_public", "no_player"):
        best["close_rows"] = [{"site": v.get("site_id"), "url": v.get("locator"), "reason": v["evidence"].get("reason") or v["class"],
                               "kind": "movie" if not problem.get("episode_id") else "episode"}
                              for v in verdicts if v["class"] in ("not_public", "no_player")]
    return best
