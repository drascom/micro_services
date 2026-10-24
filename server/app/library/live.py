"""Live TV (Canlı TV): the famelack channel list -> ``channels`` table -> the ``live_tv`` section and channel streams.

Source (``scraper/configs/famelack.yaml``, NOT a scraper site; ``scraper.config.LIVE_CONFIGS``): per country a gzipped JSON
list ``[{nanoid, name, sources: {streams: [m3u8], youtube: [embed]}, languages, country, isGeoBlocked}]`` plus per category
the same schema (all countries mixed; filtered by ``country``). A channel is kept when its ``languages`` share one code with
the config's ``languages`` (empty list = no filter). New country = add its code to ``countries``; nothing here is per country.

Ingest (:func:`run`, hourly via :func:`tick` from the scheduler, admin switch ``settings.live_tv_auto``; CLI
``python -m tools.live_ingest``): conditional GET (ETag / If-None-Match, bodies cached under ``DATA_DIR/live/``), nothing
changed and no probe due -> quick exit; else upsert by ``nanoid``, channels of a scanned country missing from the source
become ``active=0``. Health: every m3u8 gets a short GET (bounded parallelism) - ``#EXTM3U`` = ``probe: ok`` - and a CORS check
(no ``Access-Control-Allow-Origin`` -> handed out through the stream proxy). Playback reports (``/api/playback-report`` with a
channel stream's ``attempt_token``) keep the per-stream status the same way video sources do (1 failure = suspect, 3 = broken,
a success = healthy). A channel with no usable m3u8 AND no YouTube is hidden from the clients (admin still lists it).

Wire: cards (``type``/``card_kind`` ``channel``, ``channel_no`` 1..N by name), rows ``live_all`` + ``live_<category>``,
``/api/streams/ch_<nanoid>`` (m3u8 first, then YouTube embeds, broken m3u8 last; ``live: true``, ``channel``). See API.md.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

from .. import config, db

log = logging.getLogger("diziflix.live")

CONFIG_NAME = "famelack"
ID_PREFIX = "ch_"
TOKEN_PREFIX = "ch:"          # playback_attempts.source_id of a channel stream: "ch:<nanoid>|<stream key>"
SECTION_HEROES = 6
CACHE_TTL = 30.0              # seconds the visible channel list is reused (writes in this process invalidate it at once)
PROBE_ORIGIN = "http://diziflix.local"
PROBE_BYTES = 65536
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DEVICE_CODES = ("unsupported", "decode", "autoplay", "aborted", "offline")   # device-side failures: never counted

DEFAULTS: dict[str, Any] = {
    "countries": ["tr"], "languages": ["tur"], "interval_minutes": 60,
    "urls": {"country": "https://raw.githubusercontent.com/famelack/famelack-data/main/tv/compressed/countries/{cc}.json",
             "category": "https://raw.githubusercontent.com/famelack/famelack-data/main/tv/compressed/categories/{cat}.json"},
    "categories": ["animation", "auto", "business", "classic", "comedy", "cooking", "culture", "documentary", "education",
                   "entertainment", "family", "general", "kids", "legislative", "lifestyle", "movies", "music", "news",
                   "outdoor", "relax", "religious", "science", "series", "shop", "show", "sports", "top-news", "travel",
                   "weather"],
    "probe": {"parallel": 8, "timeout": 6, "every_minutes": 180},
}

# Turkish row titles, in row order (categories not listed here follow, by channel count)
CATEGORY_TITLES = {
    "news": "Haber", "top-news": "Öne Çıkan Haberler", "general": "Genel", "sports": "Spor", "kids": "Çocuk",
    "entertainment": "Eğlence", "series": "Dizi", "movies": "Film", "music": "Müzik", "documentary": "Belgesel",
    "religious": "Din", "business": "Ekonomi", "culture": "Kültür", "education": "Eğitim", "family": "Aile",
    "comedy": "Komedi", "classic": "Klasik", "animation": "Animasyon", "lifestyle": "Yaşam", "cooking": "Yemek",
    "travel": "Gezi", "outdoor": "Doğa", "science": "Bilim", "show": "Şov", "shop": "Alışveriş", "auto": "Otomobil",
    "legislative": "Meclis", "relax": "Rahatlama", "weather": "Hava Durumu",
}
ALL_ROW = "live_all"
ALL_TITLE = "Tüm kanallar"

_run_lock = threading.Lock()
_view_lock = threading.Lock()
_view: Optional[tuple[float, list[dict]]] = None   # (built at, visible channels in channel_no order)
_last_tick_check = 0.0


# ---- config / state ----------------------------------------------------------------------------------------------------
def config_path() -> str:
    from ..scraper import config as scfg
    return os.path.join(scfg.CONFIG_DIR, CONFIG_NAME + ".yaml")


def load_config(path: Optional[str] = None) -> dict:
    """The famelack config merged over :data:`DEFAULTS` (a missing / broken file = the defaults)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path or config_path(), "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        data = {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("live config unreadable (%s); using defaults", exc)
        data = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            elif value is not None:
                cfg[key] = value
    cfg["countries"] = [str(c).strip().lower() for c in cfg.get("countries") or [] if str(c).strip()]
    cfg["languages"] = [str(c).strip().lower() for c in cfg.get("languages") or [] if str(c).strip()]
    return cfg


def _state_dir() -> str:
    return os.path.join(config.DATA_DIR, "live")


def _state_path() -> str:
    return os.path.join(_state_dir(), "state.json")


def load_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(_state_dir(), exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, _state_path())


def _body_path(url: str) -> str:
    return os.path.join(_state_dir(), hashlib.sha1(url.encode()).hexdigest()[:16] + ".gz")


# ---- fetch / parse -------------------------------------------------------------------------------------------------------
def parse_body(raw: bytes) -> list[dict]:
    """A famelack file: gzipped JSON list (plain JSON accepted too); anything else = ``[]``."""
    if not raw:
        return []
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("nanoid")] if isinstance(data, list) else []


def _fetch(client, url: str, etag: Optional[str]) -> tuple[int, Optional[bytes], Optional[str]]:
    """``(status, body or None, etag)``; 304 -> body None."""
    headers = {"User-Agent": UA}
    if etag:
        headers["If-None-Match"] = etag
    resp = client.get(url, headers=headers)
    if resp.status_code == 304:
        return 304, None, etag
    if resp.status_code == 404:
        return 404, b"", None
    resp.raise_for_status()
    return resp.status_code, resp.content, resp.headers.get("etag")


def _download(client, urls: list[str], state: dict, use_etag: bool) -> tuple[dict[str, list[dict]], bool]:
    """``({url: entries}, changed)``: every url's current list (from the cache on 304); ``changed`` = any body differs."""
    etags = state.setdefault("etags", {})
    out, changed = {}, False
    for url in urls:
        cached = _body_path(url)
        etag = etags.get(url) if use_etag and os.path.exists(cached) else None
        status, body, new_etag = _fetch(client, url, etag)
        if status == 304:
            with open(cached, "rb") as fh:
                body = fh.read()
        else:
            changed = True
            if use_etag:
                os.makedirs(_state_dir(), exist_ok=True)
                with open(cached, "wb") as fh:
                    fh.write(body or b"")
                if new_etag:
                    etags[url] = new_etag
                else:
                    etags.pop(url, None)
        out[url] = parse_body(body or b"")
    return out, changed


def _strings(value) -> list[str]:
    return [str(v).strip() for v in value if isinstance(v, str) and str(v).strip()] if isinstance(value, list) else []


def build(cfg: dict, countries: dict[str, list[dict]], categories: dict[str, list[dict]]) -> dict[str, dict]:
    """``{nanoid: record}`` of the channels to keep: country lists filtered by language, categories joined in.

    ``countries`` = ``{cc: entries}``, ``categories`` = ``{category: entries}``. A record:
    ``{nanoid, name, country, languages, categories, streams, youtube, geo_blocked}``; a channel without any stream or
    YouTube link is dropped (nothing to play)."""
    langs = set(cfg.get("languages") or [])
    out: dict[str, dict] = {}
    for cc, entries in countries.items():
        for e in entries:
            nid = str(e.get("nanoid") or "").strip()
            name = str(e.get("name") or "").strip()
            languages = [l.lower() for l in _strings(e.get("languages"))]
            if not nid or not name or (langs and not langs & set(languages)):
                continue
            src = e.get("sources") if isinstance(e.get("sources"), dict) else {}
            streams = list(dict.fromkeys(u for u in _strings(src.get("streams")) if u.startswith(("http://", "https://"))))
            youtube = list(dict.fromkeys(u for u in _strings(src.get("youtube")) if u.startswith("https://")))
            if not streams and not youtube:
                continue
            out[nid] = {"nanoid": nid, "name": name, "country": (str(e.get("country") or cc)).lower(), "languages": languages,
                        "categories": [], "streams": streams, "youtube": youtube, "geo_blocked": bool(e.get("isGeoBlocked"))}
    for cat in cfg.get("categories") or []:
        for e in categories.get(cat, []):
            rec = out.get(str(e.get("nanoid") or ""))
            if rec is not None and cat not in rec["categories"]:
                rec["categories"].append(cat)
    return out


# ---- probe ----------------------------------------------------------------------------------------------------------------
def probe_url(client, url: str, headers: Optional[dict] = None) -> dict:
    """Short GET of a playlist: ``{probe: ok|fail, cors: bool, error}``; ``ok`` = the body starts with ``#EXTM3U``.
    ``headers`` = the channel's own request headers (M3U ``#EXTVLCOPT``; library/m3u.py) over the default UA."""
    try:
        with client.stream("GET", url, headers={"User-Agent": UA, "Origin": PROBE_ORIGIN, **(headers or {})}) as resp:
            if resp.status_code >= 400:
                return {"probe": "fail", "cors": False, "error": f"HTTP {resp.status_code}"}
            buf = b""
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) >= 16 or len(buf) >= PROBE_BYTES:
                    break
            acao = resp.headers.get("access-control-allow-origin", "")
            cors = acao.strip() in ("*", PROBE_ORIGIN)
        head = buf.lstrip(b"\xef\xbb\xbf \r\n\t")[:7]
        if head != b"#EXTM3U":
            return {"probe": "fail", "cors": cors, "error": "not a playlist"}
        return {"probe": "ok", "cors": cors, "error": None}
    except Exception as exc:   # timeouts, TLS, DNS ...
        return {"probe": "fail", "cors": False, "error": type(exc).__name__}


def probe_all(urls: list[str], cfg: dict, client=None, headers: Optional[dict] = None) -> dict[str, dict]:
    """``{url: probe result}`` for ``urls`` (``probe.parallel`` at a time, ``probe.timeout`` seconds each);
    ``headers`` = optional ``{url: request headers}``."""
    headers = headers or {}
    pcfg = cfg.get("probe") or {}
    own = client is None
    if own:
        import httpx
        client = httpx.Client(timeout=float(pcfg.get("timeout") or 6), follow_redirects=True)
    try:
        workers = max(1, min(int(pcfg.get("parallel") or 8), len(urls) or 1))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="live-probe") as pool:
            return dict(zip(urls, pool.map(lambda u: probe_url(client, u, headers.get(u)), urls)))
    finally:
        if own:
            client.close()


# ---- health helpers ---------------------------------------------------------------------------------------------------
def _loads(value, default):
    try:
        out = json.loads(value) if value else default
        return out if isinstance(out, type(default)) else default
    except (TypeError, ValueError):
        return default


def stream_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def usable(h: Optional[dict]) -> bool:
    """A stream worth offering first: a playback success, or not broken and not failing the server's probe."""
    h = h or {}
    if h.get("status") == "healthy":
        return True
    return h.get("status") != "broken" and h.get("probe") != "fail"


def channel_health(streams: list[str], youtube: list[str], health: dict) -> str:
    """``ok`` (a usable m3u8) | ``youtube`` (only YouTube left) | ``dead`` (nothing playable)."""
    if any(usable(health.get(u)) for u in streams):
        return "ok"
    return "youtube" if youtube else "dead"


def invalidate() -> None:
    global _view
    with _view_lock:
        _view = None


# ---- upsert ---------------------------------------------------------------------------------------------------------------
def upsert(records: dict[str, dict], countries: list[str], now: Optional[int] = None) -> dict:
    """Write ``records`` (by nanoid); active channels of ``countries`` not in ``records`` become ``active=0``.
    ``{new, updated, unchanged, deactivated}``. Stream health of a url that stays is kept."""
    now = int(time.time()) if now is None else int(now)
    stats = dict.fromkeys(("new", "updated", "unchanged", "deactivated"), 0)
    with closing(db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = {r["nanoid"]: r for r in conn.execute("SELECT * FROM channels")}
        for nid, rec in records.items():
            old = existing.get(nid)
            fields = (rec["name"], rec["country"], json.dumps(rec["languages"]), json.dumps(rec["categories"]),
                      json.dumps(rec["streams"]), json.dumps(rec["youtube"]), int(rec["geo_blocked"]))
            if old is None:
                health = {}
                conn.execute("INSERT INTO channels (id,nanoid,name,country,languages,categories,streams,youtube,geo_blocked,"
                             "stream_health,health,active,seen_at,updated_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                             (ID_PREFIX + nid, nid, *fields, "{}", channel_health(rec["streams"], rec["youtube"], health),
                              now, now, now))
                stats["new"] += 1
                continue
            health = _loads(old["stream_health"], {})
            health = {u: h for u, h in health.items() if u in rec["streams"] or u in rec["youtube"]}
            same = (old["name"], old["country"], old["languages"], old["categories"], old["streams"], old["youtube"],
                    old["geo_blocked"]) == fields and old["active"] == 1
            conn.execute("UPDATE channels SET name=?,country=?,languages=?,categories=?,streams=?,youtube=?,geo_blocked=?,"
                         "stream_health=?,health=?,active=1,seen_at=?,updated_at=? WHERE nanoid=?",
                         (*fields, json.dumps(health), channel_health(rec["streams"], rec["youtube"], health), now,
                          old["updated_at"] if same else now, nid))
            stats["unchanged" if same else "updated"] += 1
        for nid, old in existing.items():
            if nid not in records and old["active"] == 1 and old["country"] in countries and not old["source_id"]:
                conn.execute("UPDATE channels SET active=0,updated_at=? WHERE nanoid=?", (now, nid))
                stats["deactivated"] += 1
    invalidate()
    return stats


def apply_probe(results: dict[str, dict], now: Optional[int] = None) -> None:
    """Write probe results into the stream health of the active channels (status / failures of playback kept)."""
    now = int(time.time()) if now is None else int(now)
    with closing(db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for r in conn.execute("SELECT nanoid,streams,youtube,stream_health FROM channels WHERE active=1").fetchall():
            streams, youtube = _loads(r["streams"], []), _loads(r["youtube"], [])
            health = _loads(r["stream_health"], {})
            touched = False
            for url in streams:
                if url in results:
                    h = dict(health.get(url) or {})
                    h.update(results[url], probed_at=now)
                    if h["probe"] == "ok" and h.get("status") == "broken":
                        h.update(status="suspect", failures=1)   # the playlist answers again: give it another chance
                    health[url] = h
                    touched = True
            if touched:
                conn.execute("UPDATE channels SET stream_health=?,health=? WHERE nanoid=?",
                             (json.dumps(health), channel_health(streams, youtube, health), r["nanoid"]))
    invalidate()


# ---- run ------------------------------------------------------------------------------------------------------------------
def run(country: Optional[str] = None, dry_run: bool = False, force: bool = False, probe: bool = True,
        client=None, cfg: Optional[dict] = None, trigger: str = "manual") -> dict:
    """One ingest pass. ``country`` = only that code (default: the config's ``countries``); ``dry_run`` = fetch, filter
    and probe without writing anything (no ETag either); ``force`` = ignore ETags and the probe interval.
    Summary: ``{countries, total, kept, with_streams, youtube_only, changed, upsert, probed, healthy, visible, ...}``.
    Every non-dry pass (success, "no change" quick exit or error) is one Olay defteri event (:func:`_record_event`,
    ``trigger`` auto | manual | cli); an error is recorded and re-raised."""
    cfg = cfg or load_config()
    countries = [country.lower()] if country else list(cfg["countries"])
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "running"}
    started = time.time()
    started_iso = _iso(started)
    own = client is None
    try:
        summary = _run(cfg, countries, dry_run, force, probe, client, own, started)
    except Exception as exc:
        if not dry_run:
            _record_event({"started_at": started_iso, "trigger": trigger, "countries": countries, "status": "error",
                           "duration": round(time.time() - started, 1), "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        raise
    finally:
        _run_lock.release()
    if not dry_run:
        _record_event(_event_of(summary, started_iso, trigger))
    return summary


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _event_of(summary: dict, started_iso: str, trigger: str) -> dict:
    """The Olay defteri entry of a finished pass: status ``success`` | ``no_change`` (sources unchanged, ETag 304, nothing
    upserted; a health probe may still have run), counters flat, health counts from the table after the pass."""
    up = summary.get("upsert") or {}
    ev: dict[str, Any] = {"started_at": started_iso, "trigger": trigger, "countries": summary.get("countries") or [],
                          "status": "no_change" if not summary.get("changed") and not summary.get("upsert") else "success",
                          "duration": summary.get("seconds"), "changed": bool(summary.get("changed")),
                          "total": summary.get("total"), "kept": summary.get("kept"),
                          "new": up.get("new", 0), "updated": up.get("updated", 0), "deactivated": up.get("deactivated", 0),
                          "unchanged": up.get("unchanged", 0), "upserted": bool(up)}
    for k in ("probed", "probe_ok", "no_cors"):
        if k in summary:
            ev[k] = summary[k]
    try:
        ev.update(counts())
    except Exception:  # pragma: no cover - telemetry must never fail a scan
        pass
    return ev


def _record_event(entry: dict) -> None:
    """One live scan into the ops feed (``scraper/state.py`` ``record_ops_live``; admin Olay defteri ``kind: live``,
    ``site`` = :data:`CONFIG_NAME`). Never raises."""
    try:
        from ..scraper import state as sstate
        sstate.record_ops_live({"site": CONFIG_NAME, "at": entry.get("started_at"), **entry})
    except Exception:  # pragma: no cover
        log.warning("live: ops event not recorded", exc_info=True)


def _run(cfg: dict, countries: list[str], dry_run: bool, force: bool, probe: bool, client, own: bool, started: float) -> dict:
    try:
        if own:
            import httpx
            client = httpx.Client(timeout=30, follow_redirects=True)
        state = {} if dry_run else load_state()
        urls = cfg["urls"]
        c_urls = {cc: urls["country"].format(cc=cc) for cc in countries}
        k_urls = {cat: urls["category"].format(cat=cat) for cat in cfg.get("categories") or []}
        lists, changed = _download(client, list(c_urls.values()) + list(k_urls.values()), state, use_etag=not dry_run and not force)
        by_cc = {cc: lists[u] for cc, u in c_urls.items()}
        by_cat = {cat: [e for e in lists[u] if str(e.get("country") or "").lower() in countries] for cat, u in k_urls.items()}
        records = build(cfg, by_cc, by_cat)
        summary: dict[str, Any] = {
            "countries": countries, "total": sum(len(v) for v in by_cc.values()), "kept": len(records),
            "with_streams": sum(1 for r in records.values() if r["streams"]),
            "youtube_only": sum(1 for r in records.values() if not r["streams"] and r["youtube"]),
            "geo_blocked": sum(1 for r in records.values() if r["geo_blocked"]),
            "changed": changed, "dry_run": dry_run,
        }
        pcfg = cfg.get("probe") or {}
        probe_due = force or dry_run or time.time() - float(state.get("probed_at") or 0) >= float(pcfg.get("every_minutes") or 180) * 60
        if not dry_run:
            if changed or force or not state.get("ran_at"):
                # a country whose file came back empty (404 / broken) keeps its channels instead of losing them all
                summary["upsert"] = upsert(records, [cc for cc in countries if by_cc.get(cc)])
            elif not probe_due:
                summary["quick_exit"] = True
        if probe and probe_due:
            stream_urls = sorted({u for r in records.values() for u in r["streams"]})
            results = probe_all(stream_urls, cfg)
            summary["probed"] = len(stream_urls)
            summary["probe_ok"] = sum(1 for v in results.values() if v["probe"] == "ok")
            summary["no_cors"] = sum(1 for v in results.values() if v["probe"] == "ok" and not v["cors"])
            ok_channels = sum(1 for r in records.values() if any(results.get(u, {}).get("probe") == "ok" for u in r["streams"]))
            summary["healthy"] = ok_channels
            summary["hidden"] = sum(1 for r in records.values() if not r["youtube"]
                                    and not any(results.get(u, {}).get("probe") == "ok" for u in r["streams"]))
            if not dry_run:
                apply_probe(results)
                state["probed_at"] = int(time.time())
        if not dry_run:
            state["ran_at"] = int(time.time())
            state["last"] = {k: v for k, v in summary.items() if k != "dry_run"}
            _save_state(state)
            summary["visible"] = len(visible_channels(fresh=True))
        summary["seconds"] = round(time.time() - started, 1)
        log.info("live ingest: %s", summary)
        return summary
    finally:
        if own and client is not None:
            client.close()


def is_running() -> bool:
    return _run_lock.locked()


def due(cfg: Optional[dict] = None, now: Optional[float] = None) -> bool:
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    return now - float(load_state().get("ran_at") or 0) >= float(cfg.get("interval_minutes") or 60) * 60


def tick() -> Optional[dict]:
    """Scheduler job (every minute): run when the admin switch is on and the interval has passed. Never raises."""
    from .. import settings
    try:
        if not settings.live_tv_auto():
            return None
        _m3u_tick()
        if is_running() or not due():
            return None
        return run(trigger="auto")
    except Exception:
        log.exception("live ingest failed")
        return None


def _m3u_tick() -> None:
    """M3U link sources (library/m3u.py) are re-fetched on the same switch and interval as famelack. Never raises."""
    try:
        from . import m3u
        m3u.refresh_due()
    except Exception:
        log.exception("m3u refresh failed")


# ---- read side ----------------------------------------------------------------------------------------------------------
def _fold(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _row_to_channel(r) -> dict:
    return {"id": r["id"], "nanoid": r["nanoid"], "title": r["name"], "country": r["country"],
            "languages": _loads(r["languages"], []), "categories": _loads(r["categories"], []),
            "streams": _loads(r["streams"], []), "youtube": _loads(r["youtube"], []), "geo_blocked": bool(r["geo_blocked"]),
            "stream_health": _loads(r["stream_health"], {}), "health": r["health"], "active": bool(r["active"]),
            "seen_at": r["seen_at"], "updated_at": r["updated_at"],
            # M3U sources (library/m3u.py); famelack rows: source_id None, no logo / headers / labels
            "source_id": r["source_id"], "logo": r["logo"] or None, "headers": _loads(r["headers"], {}),
            "labels": _loads(r["labels"], {})}


def all_channels() -> list[dict]:
    """Every channel row (admin), active first, by name."""
    try:
        rows = db.query("SELECT * FROM channels")
    except Exception:
        return []
    out = [_row_to_channel(r) for r in rows]
    out.sort(key=lambda c: (not c["active"], _fold(c["title"]), c["nanoid"]))
    return out


def visible_channels(fresh: bool = False) -> list[dict]:
    """Active channels the clients see (a usable m3u8 or a YouTube link), by name, each with ``channel_no`` 1..N."""
    global _view
    now = time.monotonic()
    with _view_lock:
        if not fresh and _view is not None and now - _view[0] < CACHE_TTL:
            return _view[1]
    chans = [c for c in all_channels() if c["active"] and channel_health(c["streams"], c["youtube"], c["stream_health"]) != "dead"]
    for n, c in enumerate(chans, 1):
        c["channel_no"] = n
    with _view_lock:
        _view = (now, chans)
    return chans


def by_id(channel_id: str) -> Optional[dict]:
    """A channel by ``ch_<nanoid>`` (visible ones carry ``channel_no``; an active hidden one too, with 0)."""
    for c in visible_channels():
        if c["id"] == channel_id:
            return c
    r = db.query_one("SELECT * FROM channels WHERE id=? AND active=1", (channel_id,))
    if not r:
        return None
    c = _row_to_channel(r)
    c["channel_no"] = 0
    return c


def card(c: dict) -> dict:
    """A channel card: the Item fields (no artwork: empty urls) + ``channel_no``, ``categories``, ``has_youtube``."""
    return {
        "id": c["id"], "card_kind": "channel", "card_key": "channel:" + c["id"], "type": "channel",
        "availability": {"state": "ready", "reason": None, "has_trailer": False},
        "title": c["title"], "year": None, "card": "", "portrait": "", "backdrop": "", "has_backdrop": False,
        "overview": "", "genres": [c.get("labels", {}).get(k) or CATEGORY_TITLES.get(k, k) for k in c["categories"]],
        "rating": None, "country": c["country"], "followers": None, "badge": None, "sources": [source_name(c)], "tmdb_id": None,
        "imdb_id": None, "playback": "video", "progress": None,
        "channel_no": c.get("channel_no", 0), "categories": list(c["categories"]), "has_youtube": bool(c["youtube"]),
        "geo_blocked": c["geo_blocked"], "live": True, "logo": c.get("logo") or None,
    }


def source_name(c: dict) -> str:
    """``famelack`` or ``m3u`` (a channel of an admin M3U source, library/m3u.py)."""
    return "m3u" if c.get("source_id") else "famelack"


def hero_card(c: dict) -> dict:
    return {**card(c), "logo_text": c["title"], "tagline": "Canlı", "in_mylist": False}


def row_ids() -> list[str]:
    """``live_all`` then ``live_<category>`` of the categories with a visible channel (known order, then by count)."""
    counts: dict[str, int] = {}
    for c in visible_channels():
        for k in c["categories"]:
            counts[k] = counts.get(k, 0) + 1
    order = list(CATEGORY_TITLES)
    cats = sorted(counts, key=lambda k: (order.index(k) if k in order else len(order), -counts[k], k))
    return ([ALL_ROW] if visible_channels() else []) + ["live_" + k for k in cats]


def is_row(row_id: str) -> bool:
    return row_id == ALL_ROW or (row_id.startswith("live_") and bool(row_id[5:]))


def pool(row_id: str) -> list[dict]:
    chans = visible_channels()
    if row_id == ALL_ROW:
        return chans
    if row_id.startswith("live_"):
        cat = row_id[5:]
        return [c for c in chans if cat in c["categories"]]
    return []


def row_title(row_id: str) -> str:
    if row_id == ALL_ROW:
        return ALL_TITLE
    cat = row_id[5:]
    if cat in CATEGORY_TITLES:
        return CATEGORY_TITLES[cat]
    for c in visible_channels():   # an M3U group-title (library/m3u.py: category slug -> original title)
        title = (c.get("labels") or {}).get(cat)
        if title:
            return title
    return cat.replace("-", " ").title()


def heroes(limit: int = SECTION_HEROES) -> list[dict]:
    """A few channels with a working m3u8 (channel_no order; YouTube-only ones fill up)."""
    chans = visible_channels()
    first = [c for c in chans if channel_health(c["streams"], c["youtube"], c["stream_health"]) == "ok"]
    rest = [c for c in chans if c not in first]
    return [hero_card(c) for c in (first + rest)[:limit]]


def rows_json(row_limit: int) -> list[dict]:
    out = []
    for rid in row_ids():
        items = pool(rid)
        if items:
            out.append({"id": rid, "title": row_title(rid), "loaded": True, "items": [card(c) for c in items[:row_limit]],
                        "total": len(items), "offset": 0})
    return out


def row_page(row_id: str, offset: int, limit: int) -> dict:
    items = pool(row_id)
    return {"id": row_id, "title": row_title(row_id), "items": [card(c) for c in items[offset:offset + limit]],
            "offset": offset, "limit": limit, "total": len(items)}


# ---- streams + playback reports ------------------------------------------------------------------------------------------
def _embed_url(url: str) -> str:
    url = url.replace("://www.youtube.com/embed/", "://www.youtube-nocookie.com/embed/").replace(
        "://youtube.com/embed/", "://www.youtube-nocookie.com/embed/")
    return url + ("&" if "?" in url else "?") + "autoplay=1"


def _stream(c: dict, url: str, kind: str, token: str, label: str) -> dict:
    return {"url": url, "type": kind, "quality": "auto", "label": label, "attempt_token": token, "kind": "live",
            "source_id": c["id"], "source": source_name(c), "variant_id": stream_key(url), "audio_lang": None,
            "sub_mode": "none", "hard_lang": None, "sub_known": False, "site_lang_hint": None, "mirror_of": None}


def streams(c: dict) -> list[dict]:
    """Raw streams in play order: usable m3u8, then YouTube, then broken / probe-failed m3u8. Every stream gets its own
    attempt token (``playback_attempts.source_id`` = ``ch:<nanoid>|<key>``). A playlist the probe found without CORS carries
    ``stream_proxy: true`` (``streamproxy.proxy_reason`` -> ``recipe``: handed out through the proxy, the flag is stripped)."""
    health = c["stream_health"]
    good = [u for u in c["streams"] if usable(health.get(u))]
    bad = [u for u in c["streams"] if u not in good]
    now = int(time.time())
    out = []
    labels: dict[str, int] = {}

    def token_for(url: str) -> str:
        token = uuid.uuid4().hex
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)",
                   (token, f"{TOKEN_PREFIX}{c['nanoid']}|{stream_key(url)}", now))
        return token

    def label(text: str) -> str:
        labels[text] = labels.get(text, 0) + 1
        return text if labels[text] == 1 else f"{text} ({labels[text]})"

    def hls(url: str) -> dict:
        s = _stream(c, url, "hls", token_for(url), label(f"{urlparse(url).hostname or 'akış'} · canlı"))
        h = health.get(url) or {}
        if h.get("probe") == "ok" and h.get("cors") is False:
            s["stream_proxy"] = True
        if c.get("headers"):   # M3U #EXTVLCOPT Referer / User-Agent: only the server's proxy can send them
            s["request_headers"] = dict(c["headers"])
            s["stream_proxy"] = True
        return s

    out += [hls(u) for u in good]
    out += [_stream(c, _embed_url(u), "embed", token_for(u), label("YouTube · canlı")) for u in c["youtube"]]
    out += [hls(u) for u in bad]
    return out


def owns_token(token: str) -> bool:
    r = db.query_one("SELECT source_id FROM playback_attempts WHERE token=?", (token,))
    return bool(r and str(r["source_id"]).startswith(TOKEN_PREFIX))


def feedback(token: str, event: str, code: str = "", engine: str = "") -> dict:
    """A channel stream's playback report: success -> healthy; a failure (not a device code) -> failures + 1,
    suspect, broken at 3. One answer per attempt and event (``duplicate``)."""
    now = int(time.time())
    with closing(db.connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        a = conn.execute("SELECT * FROM playback_attempts WHERE token=?", (token,)).fetchone()
        if not a or now - a["created_at"] > 86400:
            raise ValueError("Oynatma denemesi bulunamadı veya süresi doldu")
        success = event == "success"
        if a["success_at" if success else "failure_at"]:
            return {"ok": True, "duplicate": True}
        if success:
            conn.execute("UPDATE playback_attempts SET success_at=?,engine=COALESCE(NULLIF(?,''),engine) WHERE token=?",
                         (now, engine[:30], token))
        else:
            conn.execute("UPDATE playback_attempts SET failure_at=?,error_code=?,engine=? WHERE token=?",
                         (now, code[:80], engine[:30], token))
        nid, _, key = str(a["source_id"])[len(TOKEN_PREFIX):].partition("|")
        r = conn.execute("SELECT streams,youtube,stream_health FROM channels WHERE nanoid=?", (nid,)).fetchone()
        if not r:
            return {"ok": True}
        streams_, youtube = _loads(r["streams"], []), _loads(r["youtube"], [])
        url = next((u for u in streams_ + youtube if stream_key(u) == key), None)
        if url is None:
            return {"ok": True}
        health = _loads(r["stream_health"], {})
        h = dict(health.get(url) or {})
        if success:
            h.update(status="healthy", failures=0, error=None, success_at=now)
            if a["failure_counted"]:
                conn.execute("UPDATE playback_attempts SET failure_counted=0 WHERE token=?", (token,))
        elif code not in DEVICE_CODES:
            failures = int(h.get("failures") or 0) + 1
            h.update(status="broken" if failures >= 3 else "suspect", failures=failures, error=code or "playback_failed",
                     failed_at=now)
            conn.execute("UPDATE playback_attempts SET failure_counted=1 WHERE token=?", (token,))
        else:
            return {"ok": True}
        health[url] = h
        conn.execute("UPDATE channels SET stream_health=?,health=? WHERE nanoid=?",
                     (json.dumps(health), channel_health(streams_, youtube, health), nid))
    invalidate()
    return {"ok": True}


# ---- admin ----------------------------------------------------------------------------------------------------------------
def counts() -> dict:
    """Channel counts from the table: ``active`` / ``inactive`` (gone from the source), ``visible`` (clients see it),
    ``hidden`` (active but nothing playable), ``ok`` (a usable m3u8), ``youtube_only``."""
    chans = all_channels()
    act = [c for c in chans if c["active"]]
    hs = [channel_health(c["streams"], c["youtube"], c["stream_health"]) for c in act]
    return {"channels": len(chans), "active": len(act), "inactive": len(chans) - len(act),
            "visible": sum(1 for h in hs if h != "dead"), "hidden": hs.count("dead"),
            "ok": hs.count("ok"), "youtube_only": hs.count("youtube")}


def source_summary() -> dict:
    """The light row of the admin "Siteler" tab (``live_sources`` of ``/api/ops/sites/manage`` and ``/api/ops/overview``):
    a live TV source, NOT a scraper site (no heal / baseline / onboarding). ``next_scan_at`` = None when the switch is off."""
    from .. import settings
    cfg = load_config()
    st = load_state()
    enabled = settings.live_tv_auto()
    ran = st.get("ran_at")
    every = float(cfg.get("interval_minutes") or 60) * 60
    nxt = None
    if enabled:
        nxt = _iso(max(time.time(), float(ran or 0) + every))
    last = None
    try:
        from ..scraper import state as sstate
        rows = sstate.list_ops("live", CONFIG_NAME, 1)
        last = rows[0] if rows else None
    except Exception:
        pass
    return {"source": CONFIG_NAME, "kind": "live", "label": "Canlı TV", "countries": cfg["countries"],
            "languages": cfg["languages"], "interval_minutes": cfg.get("interval_minutes"), "enabled": enabled,
            "running": is_running(), "ran_at": _iso(float(ran)) if ran else None, "next_scan_at": nxt,
            "last_event": last, "counts": counts()}


def admin_view() -> dict:
    """Status + every channel for the admin "Canlı TV" card."""
    from .. import settings
    cfg = load_config()
    st = load_state()
    chans = all_channels()
    visible = {c["id"]: c["channel_no"] for c in visible_channels()}
    items = []
    for c in chans:
        h = c["stream_health"]
        items.append({"id": c["id"], "title": c["title"], "country": c["country"], "active": c["active"],
                      "source": source_name(c), "source_id": c["source_id"],
                      "visible": c["id"] in visible, "channel_no": visible.get(c["id"]),
                      "health": channel_health(c["streams"], c["youtube"], h) if c["active"] else "inactive",
                      "categories": c["categories"], "geo_blocked": c["geo_blocked"], "youtube": len(c["youtube"]),
                      "streams": [{"url": u, "probe": (h.get(u) or {}).get("probe"), "cors": (h.get(u) or {}).get("cors"),
                                   "status": (h.get(u) or {}).get("status") or "unknown",
                                   "error": (h.get(u) or {}).get("error")} for u in c["streams"]]})
    return {"enabled": settings.live_tv_auto(), "running": is_running(), "countries": cfg["countries"],
            "languages": cfg["languages"], "interval_minutes": cfg.get("interval_minutes"),
            "ran_at": st.get("ran_at"), "probed_at": st.get("probed_at"), "last": st.get("last"),
            "counts": {"total": len(chans), "active": sum(1 for c in chans if c["active"]), "visible": len(visible),
                       "ok": sum(1 for i in items if i["health"] == "ok"),
                       "youtube_only": sum(1 for i in items if i["health"] == "youtube"),
                       "dead": sum(1 for i in items if i["health"] == "dead")},
            "channels": items}
