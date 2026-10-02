"""Playback-triggered heal: notice that one site's sources keep failing the same way and start a repair run.

``library/videos.py`` ``_record`` feeds every page-source resolution into ``record(site, outcome)`` (the same event
summary ``state.record_resolver`` gets) and, after a failure, calls ``maybe_trigger(site)``. Both are light and never
raise, so the resolution itself is not slowed down: the evidence check is a few dict walks and the heal runs in a
background thread.

Window: the last ``PLAYHEAL_WINDOW`` DIFFERENT sources of a site, in memory (a repeated resolution of one source replaces
its earlier entry, so a user hammering "retry" on one broken episode counts once). Trailers and sources that are not
page-backed never count; neither does a failure of the page fetch itself (``sayfa: ...``: the site is down or blocking,
which a selector/resolver repair cannot fix).

Trigger (``evaluate``): at least ``PLAYHEAL_MIN_SOURCES`` different failed sources, a failure ratio of at least
``PLAYHEAL_FAIL_RATIO`` and one common cause (the same stage, the same host or the same error text in more than half of
the failures). The result is the ``evidence`` dict of ``heal.heal_site_playback`` (``site``, ``window``, ``failing``,
``ok_examples``).

``maybe_trigger`` additionally needs ``SCRAPER_HEAL_ENABLED``, no heal cooldown, no heal already running for the site
(one playback heal per site at a time, also against a running drift heal) and a healthy LLM account
(``llm_health``, cached). A skipped trigger is remembered in ``summary(site)["last_skip"]`` for the admin panel.
After the run: a ``fixed`` result empties the window (new config, new data), anything else leaves the window alone and
the heal cooldown (set by the heal, here only as a safety net) keeps the next trigger away.

``run_manual`` is the admin button: it ignores thresholds and cooldown and uses the best evidence there is (the window,
else the sources the library already marked suspect/broken, else the coverage of the latest scan).

A second signal needs no failing resolution at all: a registered site whose scan wrote series items but NO episode video
sources (``library/ingest.py`` ``coverage``; the site's normalize has no ``episode_source``, or it needs a series
inventory). ``record_coverage(site, coverage)`` remembers the latest scan's coverage; when its share of series without
sources reaches ``PLAYHEAL_COVERAGE_RATIO`` (and there are at least ``PLAYHEAL_COVERAGE_MIN_SERIES`` series) ``evaluate`` returns
evidence of ``kind == "no_sources"`` (``failing`` = series pages without sources, stage ``normalize``), which goes through the
same gates in ``maybe_trigger`` (heal enabled, cooldown, one heal per site, LLM health). One scan starts at most one such
repair: a started trigger consumes the coverage; the next scan's ``record_coverage`` re-arms it.

A third signal is the series inventory pass of a scan (``series_crawl.run_stage`` counters): at least ``PLAYHEAL_COVERAGE_MIN_SERIES``
series pages were READ and ``PLAYHEAL_COVERAGE_RATIO`` of those reads failed with an error that a selector repair can fix (a page that
is no series page any more / a changed structure; a site that is down or blocks us (HTTP / timeout / challenge errors) is not repair
matter, and a series with no known series page is skipped without an error, so neither counts). ``record_crawl(site, counters)``
remembers it; ``evaluate`` then returns evidence of ``kind == "series_inventory"`` (``failing`` = the series pages that failed, stage
``inventory``: the ``series_page`` layer), started with ``trigger="crawl"`` through the same gates, one repair per scan.

A fourth signal is a stream the server RESOLVES but its host refuses (the diagnosis of ``library/streamdiag.py``: ``forbidden`` /
``not_media`` / ``server_blocked``, i.e. the server's own probe fails too; ``blocked`` (not public) placeholders and the browser's
``hls_unsupported_browser`` never count). ``record_stream_blocked(site, event)`` keeps one event per source for 24 h; when at least
``PLAYHEAL_STREAM_MIN_SOURCES`` DIFFERENT sources of the site are refused by the same stream host, ``evaluate`` returns evidence of
``kind == "stream_blocked"`` (``failing`` = those sources with the stream host / type, the probe's answer and the headers used under
``stream``, stage ``stream``: the ``provider`` layer; the agent tries the recipe's ``stream_headers`` / ``warm_session`` / ``stream_proxy``
or reports ``needs code``), same gates as any playback heal; one repair per group (the events are consumed when it starts).

A fifth signal is the PLAYBACK ISSUE LEDGER (``library/playissues.py``, persistent): what clients report (``playback_failed`` / ``timeout`` ...) together
with the server's diagnosis (``forbidden``, ``unreachable``, ...). At least ``PLAYHEAL_ISSUE_MIN_SOURCES`` DIFFERENT sources of the site with one heal
class within ``PLAYHEAL_ISSUE_TTL`` start a repair run (evidence ``kind == "playback"`` + ``issue{code, host, provider, stream_type, note, sources}`` and
``probe``; the layer is ``provider``); the sources of a started repair are marked (``PLAYHEAL_ISSUE_COOLDOWN``) so one repair is not started twice.
A trigger that cannot start (heal off, cooldown, busy, LLM account) is remembered in ``summary(site)["last_skip"]``.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, OrderedDict
from typing import Any, Optional

from .. import config as app_config

log = logging.getLogger("diziflix.playheal")

MAX_FAILING = 8          # failing examples handed to the agent
MAX_OK = 3               # working examples handed to the agent
LLM_SKIP_SECONDS = 60    # after an unhealthy LLM account: no new thread (and no new check) for this long
INFRA_PREFIX = "sayfa:"  # videos._record error of a failed page fetch (site down / blocked): not a repair matter
NO_SOURCES = "no_sources"  # evidence["kind"] of a site whose scan produced no episode video sources
STREAM_BLOCKED = "stream_blocked"      # evidence["kind"] of a site whose streams resolve but the stream host refuses them
STREAM_BLOCK_CODES = ("forbidden", "not_media", "server_blocked")   # streamdiag verdicts that count (never ``blocked`` / ``hls_unsupported_browser``)
STREAM_BLOCK_TTL = 86400                # seconds an event counts
MAX_BLOCKED_EVENTS = 60                 # events kept per site
STREAM_HINT = ("The server resolves the stream but its host refuses it (also for the server's own probe). Try in the library recipe: "
               "`stream_headers` (Referer / Origin = the player page), `warm_session: true`, `stream_proxy: true`; if it needs a signature, "
               "a cookie from a real browser session or a TLS fingerprint, submit nothing and report `needs code: <host>`.")
SERIES_INVENTORY = "series_inventory"   # evidence["kind"] of a site whose series pages mostly could not be read in the scan's inventory pass
#: error texts of the inventory pass that say "the site is down / blocks us" (a transport problem), not "the page structure changed"
INFRA_ERRORS = ("http ", "http_", "timeout", "timed out", "connection", "obscura", "crawlee", "challenge", "cloudflare", "blocked",
                "captcha", "dns", "ssl", "refused", "unreachable", "empty response")

_lock = threading.Lock()
_windows: dict[str, "OrderedDict[str, dict]"] = {}   # site -> source id -> latest counted outcome (oldest first)
_blocked: dict[str, "OrderedDict[str, dict]"] = {}   # site -> source id -> latest stream_blocked event (oldest first)
_meta: dict[str, dict] = {}                          # site -> {last_trigger_at, last_skip, llm_skip_until}
_inflight: set[str] = set()
_threads: dict[str, threading.Thread] = {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _s(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def reset(site: Optional[str] = None) -> None:
    """Forget the window (and the remembered trigger/skip) of one site, or of all of them."""
    with _lock:
        for store in (_windows, _meta, _blocked):
            if site is None:
                store.clear()
            else:
                store.pop(site, None)


# --- window -------------------------------------------------------------------

def _err_class(text: Any) -> str:
    """Error text without the parts that differ per source (urls, numbers): the 'same error' key."""
    t = re.sub(r"https?://\S+", "<url>", str(text or "").lower())
    return re.sub(r"\d+", "#", t).strip()[:60]


def _entry(outcome: dict) -> Optional[dict]:
    """The slim window entry of one resolution event, or None when it must not count."""
    if outcome.get("kind") == "trailer" or outcome.get("resolver", "page") != "page" \
            or outcome.get("status") in ("disabled", "blocked") or outcome.get("blocked"):
        return None   # a "not public" placeholder (video_sources.status 'blocked', library/gate.py) is no resolution failure
    source_id = _s(outcome.get("source_id"), 120)
    if not source_id:
        return None
    ok = bool(outcome.get("ok"))
    error = _s(outcome.get("error"))
    if not ok and error.startswith(INFRA_PREFIX):
        return None
    cands = []
    for c in (outcome.get("candidates") or [])[:8]:
        if isinstance(c, dict):
            cands.append({"label": _s(c.get("label"), 80), "provider": _s(c.get("provider"), 60), "ok": bool(c.get("ok")),
                          "stage": _s(c.get("stage"), 60), "host": _s(c.get("host"), 100), "error": _s(c.get("error"), 160)})
    stage = host = ""
    if not ok:
        bad = [c for c in cands if not c["ok"]]
        stages = Counter(c["stage"] for c in bad if c["stage"])
        hosts = Counter(c["host"] for c in bad if c["host"])
        # no candidate at all = the page was fetched but no video provider was found on it: the discovery stage
        stage = stages.most_common(1)[0][0] if stages else ("" if cands else "discover")
        host = hosts.most_common(1)[0][0] if hosts else ""
    return {"ok": ok, "source_id": source_id, "kind": _s(outcome.get("kind"), 20),
            "episode_id": _s(outcome.get("episode_id"), 120), "locator": _s(outcome.get("locator"), 500),
            "error": error, "stage": stage, "host": host, "candidates": cands, "at": time.time()}


def record(site: str, outcome: dict) -> bool:
    """Put one resolution result into the site's window. Never raises; False when it did not count."""
    try:
        entry = _entry(outcome)
        if entry is None or not site:
            return False
        size = max(1, int(app_config.PLAYHEAL_WINDOW))
        with _lock:
            window = _windows.setdefault(site, OrderedDict())
            window.pop(entry["source_id"], None)    # one entry per source, the newest result wins
            window[entry["source_id"]] = entry
            while len(window) > size:
                window.popitem(last=False)
        return True
    except Exception:
        return False


def _snapshot(site: str) -> list[dict]:
    with _lock:
        return list((_windows.get(site) or {}).values())


def _keys(entry: dict) -> set[tuple[str, str]]:
    """What a failure is 'about': its stages, hosts and error texts."""
    keys = {("err", _err_class(entry["error"]))} if entry["error"] else set()
    bad = [c for c in entry["candidates"] if not c["ok"]]
    for c in bad:
        if c["stage"]:
            keys.add(("stage", c["stage"]))
        if c["host"]:
            keys.add(("host", c["host"]))
        if c["error"]:
            keys.add(("err", _err_class(c["error"])))
    if entry["stage"]:
        keys.add(("stage", entry["stage"]))
    keys.discard(("err", ""))
    return keys


def _cluster(failed: list[dict]) -> Optional[tuple[str, str, int]]:
    """The stage / host / error text shared by MORE THAN HALF of the failures: (kind, value, count) or None."""
    counts: Counter = Counter()
    for entry in failed:
        counts.update(_keys(entry))
    if not counts:
        return None
    # a stage or host is a better explanation than an error text when they tie
    rank = {"stage": 0, "host": 1, "err": 2}
    (kind, value), n = min(counts.items(), key=lambda kv: (-kv[1], rank[kv[0][0]], kv[0][1]))
    return (kind, value, n) if n * 2 > len(failed) else None


def evaluate(site: str, *, force: bool = False) -> Optional[dict]:
    """Evidence for ``heal.heal_site_playback`` when the site's window shows a repeating failure, else (no ``force``) the
    "stream_blocked" evidence of refused streams, else the "no sources" evidence of the latest scan's coverage when it is a
    signal and not consumed yet, else the "series_inventory" evidence of the latest scan's inventory pass, else None.

    ``force`` (admin button) only needs one failure in the window; the thresholds do not apply (and the coverage / the inventory
    pass are left to ``best_evidence``)."""
    evidence = _playback_evidence(site, force=force)
    if evidence is None:
        evidence = _blocked_evidence(site, force=force)
    if evidence is None:
        evidence = _issues_evidence(site, force=force)
    if evidence is None and not force:
        evidence = _coverage_evidence(site) or _crawl_evidence(site)
    return evidence


def _playback_evidence(site: str, *, force: bool = False) -> Optional[dict]:
    entries = _snapshot(site)
    failed = [e for e in entries if not e["ok"]]
    if not failed:
        return None
    good = [e for e in entries if e["ok"]]
    cluster = _cluster(failed)
    if not force:
        if len(failed) < max(1, int(app_config.PLAYHEAL_MIN_SOURCES)):
            return None
        if len(failed) / len(entries) < float(app_config.PLAYHEAL_FAIL_RATIO) or cluster is None:
            return None
    members = [e for e in failed if cluster and (cluster[0], cluster[1]) in _keys(e)]
    rest = [e for e in failed if e not in members]
    pick = (members[::-1] + rest[::-1])[:MAX_FAILING]       # the common cause first, newest first within
    keep = ("source_id", "kind", "episode_id", "locator", "error", "stage", "host", "candidates")
    return {"site": site, "window": {"n": len(entries), "failed": len(failed)},
            "failing": [{k: e[k] for k in keep} for e in pick],
            "ok_examples": [{"source_id": e["source_id"], "locator": e["locator"]} for e in good[::-1][:MAX_OK]]}


def _db_evidence(site: str) -> Optional[dict]:
    """Fallback for the admin button after a restart (the window is memory only): the sources of this site the library
    already marked suspect/broken, with their last error. No resolution trace (the candidates are unknown)."""
    from .. import db
    bad = db.query("""SELECT id,kind,episode_id,locator,last_error FROM video_sources WHERE source=? AND resolver='page'
        AND kind!='trailer' AND status IN ('suspect','broken') ORDER BY last_checked_at DESC LIMIT ?""", (site, MAX_FAILING))
    if not bad:
        return None
    good = db.query("""SELECT id,locator FROM video_sources WHERE source=? AND resolver='page' AND kind!='trailer'
        AND status='healthy' ORDER BY last_success_at DESC LIMIT ?""", (site, MAX_OK))
    return {"site": site, "window": {"n": len(bad) + len(good), "failed": len(bad)},
            "failing": [{"source_id": r["id"], "kind": r["kind"], "episode_id": r["episode_id"] or "",
                         "locator": r["locator"], "error": _s(r["last_error"]), "stage": "", "host": "",
                         "candidates": []} for r in bad],
            "ok_examples": [{"source_id": r["id"], "locator": r["locator"]} for r in good]}


def best_evidence(site: str) -> Optional[dict]:
    """Evidence for a manual run: the window when it holds a failure, else the library's suspect/broken sources."""
    ev = evaluate(site, force=True)
    if ev is not None:
        return ev
    try:
        ev = _db_evidence(site)
    except Exception:
        ev = None
    if ev is not None:
        return ev
    return _coverage_evidence(site, force=True) or _crawl_evidence(site, force=True)


# --- stream blocked: the server resolves a stream its host refuses ---------------------------------------------------

def _clean_headers(value: Any) -> dict:
    return {_s(k, 40): _s(v, 120) for k, v in list(value.items())[:8]} if isinstance(value, dict) else {}


def record_stream_blocked(site: str, event: dict) -> bool:
    """Remember that the server's own probe of a stream of ``site`` was refused (``streamdiag`` verdict ``forbidden`` / ``not_media`` /
    ``server_blocked``): ``event`` = ``{source_id, kind, episode_id, locator, code, http, ct, body, host, stream_type, sent, proxied}``.
    One event per source (the newest wins), kept ``STREAM_BLOCK_TTL`` seconds. Never raises; False when it did not count (a trailer,
    another code, no host / source id)."""
    try:
        if not site or not isinstance(event, dict) or event.get("code") not in STREAM_BLOCK_CODES or event.get("kind") == "trailer":
            return False
        source_id, host = _s(event.get("source_id"), 120), _s(event.get("host"), 100).lower()
        if not source_id or not host:
            return False
        entry = {"source_id": source_id, "kind": _s(event.get("kind"), 20), "episode_id": _s(event.get("episode_id"), 120),
                 "locator": _s(event.get("locator"), 500), "code": event["code"], "http": event.get("http"),
                 "ct": _s(event.get("ct"), 60), "body": _s(event.get("body"), 80), "host": host,
                 "stream_type": _s(event.get("stream_type"), 10), "sent": _clean_headers(event.get("sent")),
                 "proxied": bool(event.get("proxied")), "at": time.time(), "consumed": False}
        with _lock:
            events = _blocked.setdefault(site, OrderedDict())
            events.pop(source_id, None)
            events[source_id] = entry
            while len(events) > MAX_BLOCKED_EVENTS:
                events.popitem(last=False)
        return True
    except Exception:
        return False


def clear_stream_blocked(site: str, source_id: str) -> None:
    """The source's stream is reachable again: withdraw its event. Never raises."""
    try:
        with _lock:
            (_blocked.get(site) or {}).pop(source_id, None)
    except Exception:
        pass


def _live_blocked(site: str) -> list[dict]:
    """The site's events inside the TTL (the stale ones are dropped), oldest first."""
    cutoff = time.time() - STREAM_BLOCK_TTL
    with _lock:
        events = _blocked.get(site)
        if not events:
            return []
        for key in [k for k, e in events.items() if e["at"] < cutoff]:
            events.pop(key, None)
        return [dict(e) for e in events.values()]


def block_label(entry: dict) -> str:
    """Short Turkish reason of the admin heal record: "akış erişilemiyor (403)"."""
    code = entry.get("http")
    return f"akış erişilemiyor ({code})" if code else "akış erişilemiyor (" + {"not_media": "video yerine hata sayfası",
        "server_blocked": "sunucu da erişemiyor"}.get(str(entry.get("code")), "reddedildi") + ")"


def blocked_failing(entry: dict) -> dict:
    """One ``failing`` example of ``stream_blocked`` evidence (also built by the source finder from its own probe)."""
    stream = {"host": entry.get("host") or "", "type": entry.get("stream_type") or "", "code": entry.get("code"),
              "http": entry.get("http"), "ct": entry.get("ct") or "", "body": entry.get("body") or "",
              "sent": entry.get("sent") or {}, "proxied": bool(entry.get("proxied"))}
    return {"source_id": entry["source_id"], "kind": entry.get("kind") or "", "episode_id": entry.get("episode_id") or "",
            "locator": entry.get("locator") or "", "error": block_label(entry), "stage": "stream", "host": "", "stream": stream,
            "candidates": []}


def blocked_evidence(site: str, group: list[dict], ok_examples: Optional[list] = None) -> dict:
    """The ``heal.heal_site_playback`` evidence of refused streams (``group`` = events of one stream host, any number >= 1)."""
    if ok_examples is None:
        refused = {e["source_id"] for e in group}   # they resolve fine too, but they are the problem, not a working example
        ok_examples = [{"source_id": e["source_id"], "locator": e["locator"]}
                       for e in [e for e in _snapshot(site) if e["ok"] and e["source_id"] not in refused][::-1][:MAX_OK]]
    first = group[-1]
    return {"site": site, "kind": STREAM_BLOCKED, "window": {"n": len(group) + len(ok_examples), "failed": len(group)},
            "failing": [blocked_failing(e) for e in group[::-1][:MAX_FAILING]], "ok_examples": ok_examples[:MAX_OK],
            "stream": {"host": first.get("host"), "type": first.get("stream_type"), "code": first.get("code"), "http": first.get("http")},
            "hint": STREAM_HINT}


def _blocked_evidence(site: str, *, force: bool = False) -> Optional[dict]:
    """Evidence of ``kind == "stream_blocked"``: the largest group of not yet consumed events of one stream host with at least
    ``PLAYHEAL_STREAM_MIN_SOURCES`` different sources (``force``: the admin button, any event, consumed ones too)."""
    events = _live_blocked(site)
    if not force:
        events = [e for e in events if not e["consumed"]]
    groups: dict[str, list[dict]] = {}
    for event in events:
        groups.setdefault(event["host"], []).append(event)
    if not groups:
        return None
    group = max(groups.values(), key=lambda g: (len(g), g[-1]["at"]))
    if not force and len(group) < max(1, int(app_config.PLAYHEAL_STREAM_MIN_SOURCES)):
        return None
    return blocked_evidence(site, group)


def _issues_evidence(site: str, *, force: bool = False) -> Optional[dict]:
    """Evidence of the playback issue ledger (``library/playissues.py``): playback failures clients reported (+ the server's diagnosis) of at
    least ``PLAYHEAL_ISSUE_MIN_SOURCES`` different sources with one issue class (``force``: the admin button, any one). Never raises."""
    try:
        from ..library import playissues
        return playissues.evidence(site, force=force)
    except Exception:
        return None


def note_trigger_result(site: str, result: str) -> None:
    """Remember in ``summary(site)["last_skip"]`` why a trigger asked for by the issue ledger did not start (admin overview)."""
    if result in ("started", "no_signal", "error"):
        return
    with _lock:
        known = (_meta.get(site) or {}).get("last_skip") or {}
    if known.get("reason") != result:
        _note_skip(site, result)


def _consume_blocked(site: str, host: str) -> None:
    with _lock:
        for event in (_blocked.get(site) or {}).values():
            if event["host"] == host:
                event["consumed"] = True


# --- coverage: a scan that produced no episode sources --------------------------------------------------------

def coverage_signal(coverage: Any) -> bool:
    """Is this scan coverage (``library/ingest.py`` ``_coverage``) a "series without episode sources" signal: at least
    ``PLAYHEAL_COVERAGE_MIN_SERIES`` series items, a share of ``PLAYHEAL_COVERAGE_RATIO`` or more of them without an episode
    source, on a site that plays video (a ``playback: trailer`` site has no episodes to miss)."""
    try:
        if not isinstance(coverage, dict) or coverage.get("playback") not in (None, "video"):
            return False
        total, bad = int(coverage.get("series_items") or 0), int(coverage.get("series_without_sources") or 0)
        return total >= max(1, int(app_config.PLAYHEAL_COVERAGE_MIN_SERIES)) and bad / total >= float(app_config.PLAYHEAL_COVERAGE_RATIO)
    except Exception:
        return False


def record_coverage(site: str, coverage: dict) -> bool:
    """Remember the coverage of the site's latest scan (replaces the earlier one and re-arms the one-repair-per-scan latch).
    Never raises; True when it is a "no sources" signal (``coverage_signal``). ``maybe_trigger`` starts the repair."""
    try:
        if not site or not isinstance(coverage, dict):
            return False
        _note(site, coverage={**coverage, "at": _now(), "consumed": False})
        return coverage_signal(coverage)
    except Exception:
        return False


def _coverage_evidence(site: str, *, force: bool = False) -> Optional[dict]:
    """Evidence of ``kind == "no_sources"`` from the remembered coverage: the signal must hold and not be consumed yet
    (``force``: the admin button, any series without sources will do)."""
    with _lock:
        cov = dict((_meta.get(site) or {}).get("coverage") or {})
    bad = int(cov.get("series_without_sources") or 0)
    if not cov or bad <= 0:
        return None
    if not force and (cov.get("consumed") or not coverage_signal(cov)):
        return None
    samples = [x for x in cov.get("sample_without_sources") or [] if isinstance(x, dict)]
    return {"site": site, "kind": NO_SOURCES, "window": {"n": int(cov.get("series_items") or 0), "failed": bad},
            "failing": [{"source_id": None, "kind": "series", "episode_id": "", "locator": _s(x.get("detail_url"), 500),
                         "error": "no video_sources produced", "stage": "normalize", "host": "", "candidates": []}
                        for x in samples[:MAX_FAILING]],
            "ok_examples": [],
            "coverage": {k: cov.get(k) for k in ("items", "with_sources", "series_items", "series_without_sources", "series_pending")}}


def _consume_coverage(site: str) -> None:
    with _lock:
        cov = (_meta.get(site) or {}).get("coverage")
        if isinstance(cov, dict):
            cov["consumed"] = True


# --- crawl: a scan whose series pages could not be read ----------------------------------------------------------

def _infra(error: Any) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in INFRA_ERRORS)


def crawl_signal(rec: Any) -> bool:
    """Is this remembered inventory pass (``record_crawl``) a "series pages mostly unreadable" signal: at least
    ``PLAYHEAL_COVERAGE_MIN_SERIES`` reads attempted, ``PLAYHEAL_COVERAGE_RATIO`` of them failed, and at least one failure that is not a
    transport problem (``INFRA_ERRORS``)."""
    try:
        attempts, errors = int(rec.get("series") or 0) + int(rec.get("errors") or 0), int(rec.get("errors") or 0)
        return (attempts >= max(1, int(app_config.PLAYHEAL_COVERAGE_MIN_SERIES)) and errors / attempts >= float(app_config.PLAYHEAL_COVERAGE_RATIO)
                and bool(rec.get("failing")))
    except Exception:
        return False


def record_crawl(site: str, counters: Any) -> bool:
    """Remember the inventory pass of the site's latest scan (``series_crawl.run_stage`` counters: ``series`` = pages read, ``errors``,
    ``items`` = the per-series lines with ``status`` / ``error`` / ``url``); replaces the earlier one and re-arms the one-repair-per-scan
    latch. Never raises; True when it is a "series_inventory" signal (``crawl_signal``). ``maybe_trigger`` starts the repair."""
    try:
        if not site or not isinstance(counters, dict) or counters.get("disabled"):
            return False
        lines = [x for x in counters.get("items") or [] if isinstance(x, dict)]
        failing = [x for x in lines if x.get("status") == "error" and not _infra(x.get("error"))]
        good = [x for x in lines if x.get("status") == "success"]
        rec = {"series": int(counters.get("series") or 0), "errors": int(counters.get("errors") or 0),
               "skipped_unknown": int(counters.get("skipped_unknown") or 0),
               "failing": [{"key": _s(x.get("key"), 120), "url": _s(x.get("url"), 500), "error": _s(x.get("error"))} for x in failing[:MAX_FAILING]],
               "ok": [{"key": _s(x.get("key"), 120), "url": _s(x.get("url"), 500)} for x in good[:MAX_OK]],
               "at": _now(), "consumed": False}
        _note(site, crawl=rec)
        return crawl_signal(rec)
    except Exception:
        return False


def _crawl_evidence(site: str, *, force: bool = False) -> Optional[dict]:
    """Evidence of ``kind == "series_inventory"`` from the remembered inventory pass: the signal must hold and not be consumed yet
    (``force``: the admin button, any failed read will do)."""
    with _lock:
        rec = dict((_meta.get(site) or {}).get("crawl") or {})
    if not rec or not rec.get("failing"):
        return None
    if not force and (rec.get("consumed") or not crawl_signal(rec)):
        return None
    return {"site": site, "kind": SERIES_INVENTORY, "window": {"n": int(rec["series"]) + int(rec["errors"]), "failed": int(rec["errors"])},
            "failing": [{"source_id": None, "kind": "series", "episode_id": "", "locator": x["url"] or x["key"], "error": x["error"],
                         "stage": "inventory", "host": "", "candidates": []} for x in rec["failing"]],
            "ok_examples": [{"source_id": None, "locator": x["url"] or x["key"]} for x in rec.get("ok") or []],
            "crawl": {k: rec.get(k) for k in ("series", "errors", "skipped_unknown")}}


def _consume_crawl(site: str) -> None:
    with _lock:
        rec = (_meta.get(site) or {}).get("crawl")
        if isinstance(rec, dict):
            rec["consumed"] = True


def summary(site: str) -> dict:
    """Light state for the admin overview: ``{window_n, failed, last_trigger_at, last_skip}``."""
    entries = _snapshot(site)
    with _lock:
        meta = dict(_meta.get(site) or {})
    return {"window_n": len(entries), "failed": sum(1 for e in entries if not e["ok"]),
            "last_trigger_at": meta.get("last_trigger_at"), "last_skip": meta.get("last_skip")}


# --- trigger ------------------------------------------------------------------

def _note(site: str, **fields: Any) -> None:
    with _lock:
        _meta.setdefault(site, {}).update(fields)


def _note_skip(site: str, reason: str, *, hold: float = 0.0) -> None:
    log.warning("playback heal of %s skipped: %s", site, reason)
    _note(site, last_skip={"at": _now(), "reason": reason}, llm_skip_until=time.time() + hold if hold else 0.0)


def _reserve(site: str) -> bool:
    """One playback heal per site at a time, and not while a (drift/manual) heal of the site runs."""
    from . import state
    with _lock:
        if site in _inflight:
            return False
        if any(a["site"] == site and a["kind"] == "heal" for a in state.activity_list()):
            return False
        _inflight.add(site)
        return True


def _release(site: str) -> None:
    with _lock:
        _inflight.discard(site)


def _execute(site: str, evidence: dict, trigger: str) -> dict:
    """Run the heal and settle the window. Never raises. The caller holds the in-flight reservation."""
    from . import heal as sheal, state
    _note(site, last_trigger_at=_now())
    try:
        fn = getattr(sheal, "heal_site_playback", None)
        res = fn(site, evidence=evidence, trigger=trigger) if fn else {"outcome": "failed", "reason": "heal_site_playback missing"}
        if not isinstance(res, dict):
            res = {"outcome": "failed", "reason": "bad heal result"}
    except Exception as exc:                         # the contract says it never raises; be sure
        res = {"outcome": "failed", "reason": f"unexpected playback heal error: {exc}"}
    outcome = res.get("outcome")
    try:
        if outcome == sheal.FIXED:
            reset(site)
        elif outcome in (sheal.FAILED, sheal.NOT_APPLIED) and sheal._enabled() and not state.get_heal_cooldown(site):
            state.set_heal_cooldown(site, time.time() + sheal._int_env("SCRAPER_HEAL_COOLDOWN", 3600))
    except Exception:
        pass
    return res


def _worker(site: str, evidence: dict, trigger: str) -> None:
    try:
        if trigger != "manual":                       # an explicit admin run lets the heal itself report a bad account
            from .. import llm_health
            status = (llm_health.check() or {}).get("status")
            if status != llm_health.VALID:
                _note_skip(site, f"llm_{status or 'unknown'}", hold=LLM_SKIP_SECONDS)
                return
        _execute(site, evidence, trigger)
    except Exception as exc:
        log.warning("playback heal of %s crashed: %s", site, type(exc).__name__)
    finally:
        _release(site)


def maybe_trigger(site: str, *, sync: bool = False) -> str:
    """Start a background playback heal when the evidence says so. Cheap and never raises; returns what happened:
    ``started`` | ``disabled`` | ``heal_disabled`` | ``no_signal`` | ``cooldown`` | ``busy`` | ``llm_unhealthy`` | ``error``.
    ``sync`` runs the heal in the calling thread (tests)."""
    try:
        if not app_config.PLAYHEAL_ENABLED:
            return "disabled"
        from . import heal as sheal, state
        if not sheal._enabled():
            return "heal_disabled"
        with _lock:
            if time.time() < float((_meta.get(site) or {}).get("llm_skip_until") or 0):
                return "llm_unhealthy"
        evidence = evaluate(site)
        if evidence is None:
            return "no_signal"
        if state.get_heal_cooldown(site):
            return "cooldown"
        if not _reserve(site):
            return "busy"
        trigger = "playback"
        if isinstance(evidence.get("issue"), dict):
            _mark_issues(site, code=str(evidence["issue"].get("code") or ""))   # one repair per issue class (the ledger's dedup)
        if evidence.get("kind") == STREAM_BLOCKED:
            _consume_blocked(site, str((evidence.get("stream") or {}).get("host") or ""))   # one repair per group
            _mark_issues(site, host=str((evidence.get("stream") or {}).get("host") or ""))
        elif evidence.get("kind") == NO_SOURCES:
            _consume_coverage(site)   # one repair per scan
        elif evidence.get("kind") == SERIES_INVENTORY:
            _consume_crawl(site)
            trigger = "crawl"
        if sync:
            _worker(site, evidence, trigger)
            return "started"
        thread = threading.Thread(target=_worker, args=(site, evidence, trigger), name=f"playheal-{site}", daemon=True)
        _threads[site] = thread
        thread.start()
        return "started"
    except Exception:
        return "error"


def _mark_issues(site: str, **kw: str) -> None:
    try:
        from ..library import playissues
        playissues.mark_triggered(site, **{k: v for k, v in kw.items() if v})
    except Exception:
        pass


def wait(site: str, timeout: float = 5.0) -> None:
    """Join the background heal thread of ``site`` (tests)."""
    thread = _threads.get(site)
    if thread is not None:
        thread.join(timeout)


def begin_manual(site: str) -> tuple[Optional[dict], str]:
    """Admin button, step 1 (synchronous, so the endpoint can answer): the best evidence available (thresholds do not
    apply) and the in-flight reservation. ``(evidence, "")`` or ``(None, "no_evidence" | "already_running")``.
    A successful ``begin_manual`` MUST be followed by ``run_reserved`` (it releases the reservation)."""
    evidence = best_evidence(site)
    if evidence is None:
        return None, "no_evidence"
    if not _reserve(site):
        return None, "already_running"
    return evidence, ""


def run_reserved(site: str, evidence: dict, trigger: str = "manual") -> dict:
    """Admin button, step 2 (the endpoint's background task): run the heal (cooldown and the LLM pre-check do not apply
    to an explicit run; the heal itself reports a bad account) and release the reservation. Never raises."""
    try:
        return _execute(site, evidence, trigger)
    finally:
        _release(site)


def run_manual(site: str) -> dict:
    """``begin_manual`` + ``run_reserved`` in one blocking call: ``{started: False, reason}`` or ``{started: True, **result}``."""
    evidence, reason = begin_manual(site)
    if evidence is None:
        return {"started": False, "reason": reason}
    return {"started": True, **run_reserved(site, evidence)}
