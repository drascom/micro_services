"""Admin Katalog (T16): every title (visible or not, K5 is for clients only), its seasons / episodes / video sources, an
inspect-only resolution trace and a side-effect-free stream list for the in-panel player.

Inspection never changes health: ``resolve`` and ``streams`` write no ``video_sources`` status / failures, mint no
``playback_attempts`` token, write no ``progress`` and emit no event. The explicit admin actions (disable / enable,
"Kaynak ekle", unlist, delete, TMDB refresh) are the only writes.

    GET    /api/control/catalog?site=&type=&status=&q=&sort=&page=
    GET    /api/control/catalog/{id}
    GET    /api/control/catalog/streams/{id}?episode=&source=
    POST   /api/control/catalog/sources/{sid}/resolve {force}
    POST   /api/control/catalog/sources/{sid}/disable | enable
    POST   /api/control/catalog/sources/{sid}/report            (problem with actor admin; 503 without T13)
    POST   /api/control/catalog/resolve-url {site, url}          (a stage card sample)
    POST   /api/control/catalog/{id}/sources {url, episode?}     ("Kaynak ekle": provider library check -> provider/embed/direct row)
    POST   /api/control/catalog/{id}/tmdb-refresh
    POST   /api/control/catalog/{id}/unlist | relist
    POST   /api/control/catalog/bulk {ids, action: delete|unlist|relist}
    DELETE /api/control/catalog/{id}?site=                       (that site's record; no site = every record)
"""
from __future__ import annotations

import hashlib
import importlib
import ipaddress
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Request

from .. import db
from ..control.crawl import manual as crawl_manual
from ..core.catalog import cache, hooks as cat_hooks, write
from ..core.catalog.textnorm import fold
from ..core.play import follow as play_follow, hooks as play_hooks, sources, streamdiag, streamprobe, streamproxy, tracks
from ..core.play.providers import registry
from ..errors import ApiError

router = APIRouter()

P = "/api/control/catalog"
PAGE_SIZE = 50
MAX_URL = 2048
PROBE_MAX = 6
ADMIN_SOURCE = "admin"     # video_sources.source of rows the admin added (origin=admin)
STATUSES = ("visible", "no_source", "not_public", "broken", "disabled", "unlisted")
UNPLAYABLE = ("no_source", "not_public", "broken", "disabled")
STATUS_TEXT = {"visible": "görünür", "no_source": "kaynak bekliyor", "not_public": "halka kapalı",
               "broken": "kaynakları bozuk", "disabled": "devre dışı", "unlisted": "liste dışı"}
SOURCE_TEXT = {"healthy": "sağlam", "unknown": "bilinmiyor", "suspect": "şüpheli", "broken": "bozuk", "blocked": "halka kapalı",
               "disabled": "devre dışı", "no_source": "kaynak yok"}
NO_SOURCE_REASON = "taramada kaynak bulunamadı"   # T28: the crawl read the episode / film page and found no candidate
_EP_ID = re.compile(r":s(\d+):e(\d+)$")


def _err(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def _json(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _unlisted() -> dict:
    return {r["canonical_id"]: r["at"] for r in db.query("SELECT canonical_id, at FROM catalog_unlisted")}


# ------------------------------------------------------------------------------------------------ status of a title

def title_status(rows: list, *, unlisted: bool = False, series_blocked: Optional[str] = None) -> dict:
    """``{status, text, reason}`` of one title from its ``video_sources`` rows (trailers ignored). ``visible`` = what K5 shows
    clients (at least one ``unknown|healthy``); the rest says why not."""
    full = [r for r in rows if r["kind"] != "trailer"]
    by = defaultdict(int)
    for r in full:
        by[r["status"]] += 1
    if unlisted:
        status, reason = "unlisted", "admin listeden çıkardı (kayıt duruyor)"
    elif by["unknown"] or by["healthy"]:
        status, reason = "visible", ""
    elif series_blocked is not None:
        status, reason = "not_public", "halka kapalı: " + (series_blocked or "dizi sayfası")
    elif not full:
        status, reason = "no_source", "henüz video kaynağı yok"
    elif by["blocked"]:
        blocked = next((r["last_error"] for r in full if r["status"] == "blocked" and r["last_error"]), "")
        status, reason = "not_public", "halka kapalı (telif / erişim engeli)" + (f": {blocked}" if blocked else "")
    elif by["no_source"] and not (by["broken"] or by["suspect"]):
        status, reason = "no_source", NO_SOURCE_REASON
    elif by["broken"] or by["suspect"]:
        status, reason = "broken", f"tüm kaynaklar bozuk ya da şüpheli ({by['broken']} bozuk, {by['suspect']} şüpheli)"
    else:
        status, reason = "disabled", "tüm kaynaklar devre dışı"
    return {"status": status, "text": STATUS_TEXT[status], "reason": reason}


def source_summary(rows: list, kind: str) -> dict:
    full = [r for r in rows if r["kind"] != "trailer"]
    by = defaultdict(int)
    for r in full:
        by[r["status"]] += 1
    episodes = len({r["episode_id"] for r in full if r["kind"] == "episode"})
    parts = [f"{episodes} bölüm"] if kind == "series" else [f"{len(full)} kaynak"]
    parts += [f"{by[s]} {SOURCE_TEXT[s]}" for s in ("healthy", "unknown", "suspect", "broken", "blocked", "disabled", "no_source")
              if by[s]]
    return {"episodes": episodes, "sources": len(full), "by_status": dict(by), "text": " · ".join(parts)}


# ------------------------------------------------------------------------------------------------ list

def _gather() -> list[dict]:
    """Every library title with its sites, origins, source rows and status (one read view)."""
    with db.read_transaction():
        items = db.query("SELECT id, type, title, year, poster_url, tmdb_poster_url, tmdb_id, added_at FROM library_items")
        recs = db.query("SELECT site_id, key, canonical_id FROM source_items")
        origins = db.query("SELECT site_id, source_key, kind, value FROM source_origins ORDER BY position")
        vs = db.query("SELECT id, canonical_id, source, kind, status, episode_id, last_error FROM video_sources")
        blocked = db.query("SELECT site, source_key, reason FROM blocked_pages WHERE kind='series' AND status='blocked'")
        unlisted = _unlisted()
    sites, keys = defaultdict(list), {}
    for r in recs:
        if r["canonical_id"]:
            if r["site_id"] not in sites[r["canonical_id"]]:
                sites[r["canonical_id"]].append(r["site_id"])
            keys[(r["site_id"], r["key"])] = r["canonical_id"]
    org = defaultdict(list)
    for o in origins:
        cid = keys.get((o["site_id"], o["source_key"]))
        if cid:
            org[cid].append({"site": o["site_id"], "kind": o["kind"], "value": o["value"]})
    rows = defaultdict(list)
    for r in vs:
        rows[r["canonical_id"]].append(r)
    series_blocked = {}
    for b in blocked:
        cid = keys.get((b["site"], b["source_key"]))
        if cid:
            series_blocked[cid] = b["reason"] or "dizi sayfası"
    out = []
    for it in items:
        cid = it["id"]
        mine = rows.get(cid, [])
        out.append({
            "id": cid, "type": it["type"], "title": it["title"], "year": it["year"],
            "poster_url": it["tmdb_poster_url"] or it["poster_url"], "tmdb_matched": bool(it["tmdb_id"]),
            "added_at": it["added_at"] or 0, "sites": sites.get(cid, []) + ([ADMIN_SOURCE] if any(
                r["source"] == ADMIN_SOURCE for r in mine) else []),
            "origins": org.get(cid, [])[:12], "summary": source_summary(mine, it["type"]),
            **title_status(mine, unlisted=cid in unlisted, series_blocked=series_blocked.get(cid)),
            "_fold": fold(it["title"])})
    return out


@router.get(P)
def catalog_list(site: str = "", type: str = "", status: str = "", q: str = "", sort: str = "added", page: int = 1) -> dict:
    items = _gather()
    counts = defaultdict(int)
    for it in items:
        counts[it["status"]] += 1
    if site:
        items = [i for i in items if site in i["sites"]]
    if type in ("movie", "series"):
        items = [i for i in items if i["type"] == type]
    if status == "unplayable":
        items = [i for i in items if i["status"] in UNPLAYABLE]
    elif status in STATUSES:
        items = [i for i in items if i["status"] == status]
    elif status != "all":   # default: everything but the unlisted ones
        items = [i for i in items if i["status"] != "unlisted"]
    needle = fold(q)
    if needle:
        items = [i for i in items if needle in i["_fold"]]
    if sort == "name":
        items.sort(key=lambda i: (i["_fold"], i["id"]))
    else:
        items.sort(key=lambda i: (-(i["added_at"] or 0), i["id"]))
    total = len(items)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    chunk = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    for i in chunk:
        i.pop("_fold", None)
    all_sites = sorted({r["site_id"] for r in db.query("SELECT DISTINCT site_id FROM source_items")})
    return {"items": chunk, "total": total, "page": page, "pages": pages, "page_size": PAGE_SIZE,
            "counts": {s: counts.get(s, 0) for s in STATUSES} | {"unplayable": sum(counts.get(s, 0) for s in UNPLAYABLE)},
            "status_text": STATUS_TEXT, "sites": all_sites}


# ------------------------------------------------------------------------------------------------ detail

def _source_out(r) -> dict:
    return {"id": r["id"], "site": r["source"], "admin": r["source"] == ADMIN_SOURCE, "kind": r["kind"], "url": r["locator"],
            "resolver": r["resolver"], "media_type": r["media_type"], "label": r["label"], "status": r["status"],
            "status_text": SOURCE_TEXT.get(r["status"], r["status"]), "failures": r["failures"], "last_error": r["last_error"],
            "last_checked_at": r["last_checked_at"], "last_success_at": r["last_success_at"],
            "resolved_at": r["resolved_at"], "diagnosis": streamdiag.public_diagnosis(r),
            **_alt_out(r), **_candidate_out(r)}


def _candidate_out(r) -> dict:
    """T28: the candidate address the crawl stored (``candidate_url``, ``candidate_at``, ``candidates``)."""
    try:
        url, at, raw = r["candidate_url"], r["candidate_at"], r["candidate_json"]
    except (IndexError, KeyError):
        return {}
    return {"candidate_url": url, "candidate_at": at, "candidates": _json(raw, [])[:8]}


def _alt_out(r) -> dict:
    """The source alternative of a per-alternative row (T21): ``{alt_index, alt_label, alt_host}``; nothing for a whole-page row."""
    try:
        key = r["alt_key"]
    except (IndexError, KeyError):
        return {}
    if not key:
        return {}
    return {"alt_index": r["alt_index"], "alt_label": r["alt_label"] or "", "alt_host": r["alt_host"] or ""}


def _item(cid: str):
    row = db.query_one("SELECT * FROM library_items WHERE id=?", (cid,))
    if row is None:
        alias = db.query_one("SELECT canonical_id FROM catalogue_aliases WHERE alias=?", (cid,))
        row = alias and db.query_one("SELECT * FROM library_items WHERE id=?", (alias["canonical_id"],))
    if not row:
        raise _err(404, "not_found", "Başlık bulunamadı")
    return row


def _problems():
    """T13's problem store when installed (else None: the "sorun olarak kaydet" button is hidden)."""
    try:
        mod = importlib.import_module("..control.problems.store", __package__)
    except ImportError:
        return None
    return mod if callable(getattr(mod, "open_or_get", None)) else None


@router.get(P + "/streams/{cid}")
def admin_streams(cid: str, request: Request, episode: str = "", source: str = "") -> dict:
    """The streams the in-panel player uses: same resolution as the client, no side effect (no attempt token, no progress,
    no event, no health change). A source that needs the proxy comes back with a signed proxy URL."""
    item = _item(cid)
    cid = item["id"]
    if source:
        rows = db.query("SELECT * FROM video_sources WHERE id=? AND canonical_id=?", (source, cid))
        if not rows:
            raise _err(404, "not_found", "Kaynak bulunamadı")
    else:
        rows = db.query("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' "
                        "AND status!='disabled' ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,"
                        "source,id", (cid, episode or ""))
    output, soft, errors, duration = [], [], [], 0
    for row in rows:
        res = _inspect(row, force=False)
        if not res["ok"]:
            errors.append({"source_id": row["id"], "site": row["source"], "stage": res["stage"], "error": res["error"]})
            continue
        for s in res["streams"]:
            output.append({**tracks.with_defaults(s, row["id"]), "source_id": row["id"], "source": row["source"],
                           "kind": row["kind"]})
        soft += [t for t in res["subtitles"] if isinstance(t, dict) and t.get("url")]
        duration = duration or res["duration"]
    base = streamproxy.public_base(request.headers, request.url.scheme)
    return {"streams": sources.public_streams(output, base), "subtitles": tracks.public_subtitles(soft),
            "audio": tracks.public_audio(output), "duration": duration, "errors": errors}


@router.get(P + "/{cid}")
def catalog_detail(cid: str) -> dict:
    row = _item(cid)
    cid = row["id"]
    recs = db.query("SELECT site_id, key, source_url, fetched_at FROM source_items WHERE canonical_id=? ORDER BY site_id", (cid,))
    records = []
    for r in recs:
        orig = db.query("SELECT kind, value, position, seen_at FROM source_origins WHERE site_id=? AND source_key=? "
                        "ORDER BY kind, position", (r["site_id"], r["key"]))
        records.append({"site": r["site_id"], "key": r["key"], "url": r["source_url"], "fetched_at": r["fetched_at"],
                        "origins": [dict(o) for o in orig]})
    vs = db.query("SELECT * FROM video_sources WHERE canonical_id=? ORDER BY season, episode, source, alt_index, id", (cid,))
    tm_eps = {(e["season"], e["episode"]): e for e in db.query(
        "SELECT season, episode, title, air_date FROM library_episodes WHERE canonical_id=?", (cid,))}
    seasons: dict[int, dict] = {}
    for v in vs:
        if v["kind"] != "episode":
            continue
        eps = seasons.setdefault(v["season"], {})
        tm = tm_eps.get((v["season"], v["episode"]))
        ep = eps.setdefault(v["episode"], {
            "id": v["episode_id"], "season": v["season"], "episode": v["episode"],
            "title": v["episode_title"] or (tm["title"] if tm else None) or f"{v['episode']}. Bölüm",
            "air_date": v["episode_air_date"] or (tm["air_date"] if tm else None), "sources": []})
        ep["sources"].append(_source_out(v))
    season_list = []
    for n in sorted(seasons):
        eps = []
        for number in sorted(seasons[n]):
            ep = seasons[n][number]
            ep_rows = [v for v in vs if v["episode_id"] == ep["id"]]
            eps.append({**ep, "source_count": len(ep["sources"]), "health": title_status(ep_rows)})
        season_list.append({"season": n, "episodes": eps})
    blocked = db.query_one("SELECT b.reason FROM blocked_pages b JOIN source_items s ON s.site_id=b.site AND s.key=b.source_key "
                           "WHERE s.canonical_id=? AND b.kind='series' AND b.status='blocked'", (cid,))
    unlisted = db.query_one("SELECT at FROM catalog_unlisted WHERE canonical_id=?", (cid,))
    return {
        "id": cid, "type": row["type"], "title": row["title"], "original_title": row["original_title"], "year": row["year"],
        "genres": _json(row["genres"], []), "overview": row["overview"] or "",
        "poster_url": row["tmdb_poster_url"] or row["poster_url"], "backdrop_url": row["tmdb_backdrop_url"] or row["backdrop_url"],
        "tmdb_id": row["tmdb_id"], "imdb_id": row["imdb_id"], "tmdb_status": row["tmdb_enrich_status"],
        "added_at": row["added_at"], "updated_at": row["updated_at"],
        "records": records,
        "field_provenance": {r["field"]: r["source"] for r in db.query(
            "SELECT field, source FROM field_provenance WHERE canonical_id=? ORDER BY field", (cid,))},
        "seasons": season_list,
        "sources": [_source_out(v) for v in vs if v["kind"] == "movie"],
        "trailers": [_source_out(v) for v in vs if v["kind"] == "trailer"],
        "summary": source_summary(vs, row["type"]),
        **title_status(vs, unlisted=unlisted is not None,
                       series_blocked=(blocked["reason"] or "dizi sayfası") if blocked else None),
        "unlisted_at": unlisted["at"] if unlisted else None,
        "problems_available": _problems() is not None,
    }


# ------------------------------------------------------------------------------------------------ resolution (inspect only)

def _inspect(row, force: bool) -> dict:
    """Resolve one source without touching the database: ``{ok, stage, error, streams, subtitles, audio, duration,
    candidates, from_cache, page_ok, ms}``. A still-valid stored resolution is reused unless ``force``."""
    base = {"ok": False, "stage": "", "error": "", "streams": [], "subtitles": [], "audio": [], "duration": 0,
            "candidates": [], "from_cache": False, "ms": 0, "blocked": False}
    if row["resolver"] in ("direct", "embed"):
        stream = {"url": row["locator"], "type": "embed" if row["resolver"] == "embed" else row["media_type"],
                  "quality": "auto", "label": row["label"] or row["resolver"]}
        return {**base, "ok": True, "stage": "stream", "streams": [stream], "direct": True}
    if row["resolver"] == "provider" and force:
        started = time.monotonic()
        try:
            got = registry.resolve(row["locator"], referer="", extra=[registry.YOUTUBE]) or {}
        except Exception as exc:
            got = {"error": " ".join(str(exc).split())[:200]}
        streams = list(got.get("streams") or [])
        return {**base, "ok": bool(streams), "stage": "stream" if streams else "provider", "provider_row": True,
                "error": "" if streams else (got.get("error") or "Video sağlayıcısı çözülemedi"), "streams": streams,
                "subtitles": list(got.get("subtitles") or []), "audio": list(got.get("audio") or []),
                "duration": int(got.get("duration_s") or 0), "ms": int((time.monotonic() - started) * 1000)}
    if not force:
        hit = sources._cached(row)   # pure read of the stored resolution (no refresh, no write)
        if hit:
            cached = hit[0]
            return {**base, "ok": bool(cached.get("streams")), "stage": "stream", "from_cache": True,
                    "streams": list(cached.get("streams") or []), "subtitles": list(cached.get("subtitles") or []),
                    "audio": list(cached.get("audio") or []), "duration": int(cached.get("duration") or 0)}
    if row["resolver"] == "provider":
        return _inspect(row, force=True)
    ctx = play_hooks.ctx_for(row["source"])
    res = play_follow.follow(ctx, row["locator"])
    return {**base, "ok": res.ok, "stage": res.stage, "error": res.error, "streams": res.streams, "subtitles": res.subtitles,
            "audio": res.audio, "duration": res.duration, "candidates": res.candidates, "ms": res.ms, "blocked": res.blocked,
            "reason": res.reason}


def _probe(stream: dict, locator: str) -> dict:
    if stream.get("type") not in ("hls", "mp4"):
        return {"skipped": "gömülü oynatıcı (yoklanmaz)"}
    got = streamprobe.probe(stream["url"], stream.get("request_headers"), stream.get("type"), locator)
    out = {"ok": bool(got.get("ok")), "http": got.get("http"), "variants": got.get("variants"),
           "encrypted": got.get("encrypted"), "ms": got.get("ms"), "note": got.get("note")}
    if got.get("duration_s"):
        out["duration_min"] = round(got["duration_s"] / 60, 1)
    if isinstance(out["variants"], list):
        out["variants"] = len(out["variants"])
    return out


def _trace(row, res: dict, request: Request) -> dict:
    """The inspect result as steps: page -> discover -> provider -> stream (the failing step is ``failed_stage``)."""
    order = ["page", "discover", "provider", "stream"]
    reached = order.index(res["stage"]) if res["stage"] in order else 0
    failed = "" if res["ok"] else (res["stage"] or "page")
    cands = res.get("candidates") or []
    steps = []
    if res.get("direct"):
        steps.append({"step": "page", "ok": True, "note": "doğrudan kaynak (sayfa yok): " + row["resolver"]})
    elif res.get("provider_row"):
        steps.append({"step": "provider", "ok": res["ok"], "note": "sağlayıcı adresi her oynatmada çözülür"
                      + ("" if res["ok"] else ": " + res["error"])})
    elif res.get("from_cache"):
        steps.append({"step": "page", "ok": True, "note": "geçerli önbellekteki çözüm kullanıldı (önbelleksiz için yeniden çöz)"})
    else:
        steps.append({"step": "page", "ok": reached > 0 or res["ok"], "note": res["error"] if failed == "page" else "sayfa getirildi"})
        if reached >= 1 or failed == "discover":
            steps.append({"step": "discover", "ok": bool(cands), "candidates": cands,
                          "note": res["error"] if failed == "discover" else f"{len(cands)} aday"})
        if reached >= 2:
            steps.append({"step": "provider", "ok": res["ok"], "providers": sorted({c.get("provider") for c in cands
                                                                                    if c.get("provider")}),
                          "note": res["error"] if failed == "provider" else "sağlayıcı çözdü"})
    base = streamproxy.public_base(request.headers, request.url.scheme)
    streams = res["streams"][:20]
    public = sources.public_streams([{**s, "source_id": row["id"]} for s in streams], base)
    workers = min(4, max(1, len(streams[:PROBE_MAX])))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="admin-probe") as pool:
        probes = list(pool.map(lambda s: _probe(s, row["locator"]), streams[:PROBE_MAX]))
    out_streams = []
    for i, (s, p) in enumerate(zip(streams, public)):
        out_streams.append({"url": s.get("url"), "type": s.get("type"), "quality": s.get("quality"), "width": s.get("width"),
                            "height": s.get("height"), "bitrate": s.get("bitrate"), "label": s.get("label"),
                            "provider": s.get("provider"), "proxied": p.get("proxied", False),
                            "proxy_reason": p.get("proxy_reason"), "probe": probes[i] if i < len(probes) else None})
    steps.append({"step": "stream", "ok": bool(out_streams), "note": f"{len(out_streams)} akış" if out_streams else
                  (res["error"] if failed == "stream" else "akış yok")})
    return {"source_id": row["id"], "site": row["source"], "url": row["locator"], "ok": res["ok"], "failed_stage": failed,
            "error": res["error"], "blocked": res.get("blocked", False), "from_cache": res.get("from_cache", False),
            "ms": res.get("ms", 0), "steps": steps, "streams": out_streams,
            "subtitles": [{"lang": t.get("lang"), "label": t.get("label"), "format": t.get("format"), "url": t.get("url")}
                          for t in res["subtitles"]],
            "audio": [{"lang": a.get("lang"), "label": a.get("label"), "default": bool(a.get("default"))} for a in res["audio"]],
            "duration": res["duration"]}


def _source(sid: str):
    row = db.query_one("SELECT * FROM video_sources WHERE id=?", (sid,))
    if row is None:
        raise _err(404, "not_found", "Kaynak bulunamadı")
    return row


@router.post(P + "/sources/{sid}/resolve")
def resolve_source(sid: str, request: Request, body: dict = Body(default={})) -> dict:
    row = _source(sid)
    return _trace(row, _inspect(row, force=bool((body or {}).get("force"))), request)


@router.post(P + "/resolve-url")
def resolve_url(request: Request, body: dict = Body(default={})) -> dict:
    """A stage card sample (A5/A6): follow ``url`` with the site's play context. Inspect only, no database."""
    site = str((body or {}).get("site") or "").strip()
    url = _url((body or {}).get("url"))
    if not db.query_one("SELECT 1 FROM sites WHERE site_id=?", (site,)):
        raise _err(404, "not_found", "Site bulunamadı")
    row = {"id": "", "source": site, "locator": url, "resolver": "page", "media_type": "", "label": ""}
    return _trace(row, _inspect(row, force=True), request)


@router.post(P + "/sources/{sid}/disable")
def disable_source(sid: str) -> dict:
    _source(sid)
    db.execute("UPDATE video_sources SET status='disabled', updated_at=? WHERE id=?", (int(time.time()), sid))
    cache.refresh_soon()
    return {"ok": True, "status": "disabled"}


@router.post(P + "/sources/{sid}/enable")
def enable_source(sid: str) -> dict:
    row = _source(sid)
    if row["status"] != "disabled":
        raise _err(409, "not_disabled", "Kaynak devre dışı değil")
    db.execute("UPDATE video_sources SET status='unknown', failures=0, updated_at=? WHERE id=?", (int(time.time()), sid))
    cache.refresh_soon()
    return {"ok": True, "status": "unknown"}


@router.post(P + "/sources/{sid}/report")
def report_source(sid: str, body: dict = Body(default={})) -> dict:
    """"Bu kaynak çalışmıyor": open (or join) the episode / title problem with actor ``admin`` (T13's store) and queue it.
    No watcher (the admin is not a client profile)."""
    row = _source(sid)
    store = _problems()
    if store is None:
        raise _err(503, "problems_unavailable", "Sorun kaydı henüz kurulu değil")
    note = str((body or {}).get("note") or "admin: kaynak panelde oynamadı")[:500]
    episode_id = row["episode_id"] or ""
    problem, _created = store.open_or_get(store.episode_fp(row["canonical_id"], episode_id),
                                          scope="episode" if episode_id else "title", site_id=row["source"],
                                          canonical_id=row["canonical_id"], episode_id=episode_id, source_id=sid,
                                          evidence={"origin": "admin", "source_id": sid})
    run = importlib.import_module("..control.problems.run", __package__)
    deadline = store.now() + run._deadline_s()
    store.event(problem["id"], "admin", "admin_report", note, {"source_id": sid})
    if problem["state"] in ("resolved", "confirmed", "closed", "exhausted"):
        problem = store.transition(problem["id"], "open", actor="admin", kind="reopened", note="admin_report",
                                   attempts=int(problem.get("attempts") or 0) + 1, next_at=0, deadline_at=deadline)
    elif problem["state"] == "closed_not_public" or problem["state"].startswith("waiting_"):
        # the admin says it should play: waiting / "not public" is looked at again (same attempts)
        problem = store.transition(problem["id"], "open", actor="admin", kind="reopened", note="admin_report",
                                   next_at=0, deadline_at=deadline)
    else:
        problem = store.update(problem["id"], deadline_at=deadline) or store.row(problem["id"])
    if problem["state"] in store.WORKING:
        run.enqueue(problem, admin=True)   # admin report: the repair it leads to is outside the LLM budget
    return {"ok": True, "problem_id": problem["id"], "state": problem["state"], "deadline_at": problem.get("deadline_at")}


# ------------------------------------------------------------------------------------------------ admin "Kaynak ekle"

def _url(value: Any) -> str:
    url = value.strip() if isinstance(value, str) else ""
    if not url or len(url) > MAX_URL:
        raise _err(400, "bad_url", "Geçerli bir adres ver")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc:
        raise _err(400, "bad_url", "Yalnız http(s) adresi")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if (ip is not None and not ip.is_global) or host == "localhost" or host.endswith(".local"):
        raise _err(400, "bad_url", "İç ağ adresi kullanılamaz")
    return url


@router.post(P + "/{cid}/sources")
def add_source(cid: str, body: dict = Body(default={})) -> dict:
    """The admin pastes a link from another public source: the provider library (+ generic YouTube) or, for a media file, a
    probe must accept it; then it becomes a ``provider`` (known link, resolved per play) / ``embed`` / ``direct`` row (``source='admin'``, status ``unknown``) and the
    title / episode is client-visible again (K5)."""
    item = _item(cid)
    cid = item["id"]
    url = _url((body or {}).get("url"))
    episode_id = str((body or {}).get("episode") or "").strip()
    season = number = None
    ep_title = None
    if item["type"] == "series":
        m = _EP_ID.search(episode_id)
        if not episode_id.startswith(cid + ":") or not m:
            raise _err(400, "bad_request", "Dizi için bölüm seç")
        season, number = int(m.group(1)), int(m.group(2))
        known = db.query_one("SELECT episode_title FROM video_sources WHERE canonical_id=? AND episode_id=? "
                             "AND episode_title IS NOT NULL LIMIT 1", (cid, episode_id))
        ep_title = known["episode_title"] if known else None
    else:
        episode_id = ""
    result = registry.resolve(url, referer="", extra=[registry.YOUTUBE])
    stream = (result or {}).get("streams", [None])[0] if result else None
    probe = None
    if stream is None:
        kind = "hls" if ".m3u8" in urlsplit(url).path.lower() else "mp4"
        probe = streamprobe.summarize(url, None, kind)
        if not probe.get("ok"):
            raise _err(422, "not_playable", "Adres sağlayıcı kütüphanesinde tanınmadı ve akış yoklaması geçmedi"
                       + (f" ({probe.get('note') or probe.get('http')})" if probe.get("note") or probe.get("http") else ""))
        stream = {"url": url, "type": kind}
        label = "admin"
    else:
        label = (result or {}).get("provider") or "admin"
        if stream.get("type") in ("hls", "mp4"):
            probe = streamprobe.summarize(stream["url"], stream.get("request_headers"), stream["type"])
            if not probe.get("ok"):
                raise _err(422, "not_playable", "Sağlayıcı akış verdi ama yoklama geçmedi")
    # A link the provider library knows is stored as the PROVIDER address and resolved on every play (its media address may be
    # signed and expire; core.play.sources caches it until valid_until). An embed player address is stable: kept as ``embed``.
    # Only a plain media file nobody recognises stays ``direct``.
    if result and stream.get("type") != "embed":
        resolver, locator = "provider", url
    else:
        resolver, locator = ("embed" if stream.get("type") == "embed" else "direct"), stream["url"]
    media_type = stream.get("type") or "mp4"
    sid = "vs_admin_" + hashlib.sha256(f"{cid}|{episode_id}|{locator}".encode()).hexdigest()[:20]
    now = int(time.time())
    db.execute(
        "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,media_type,"
        "label,episode_title,status,failures,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'unknown',0,?) "
        "ON CONFLICT(id) DO UPDATE SET status='unknown', failures=0, last_error=NULL, updated_at=excluded.updated_at",
        (sid, cid, ADMIN_SOURCE, cid, episode_id, season, number, "episode" if episode_id else "movie", locator, resolver,
         media_type, str(label)[:40], ep_title, now))
    cache.refresh_soon()
    return {"ok": True, "source_id": sid, "resolver": resolver, "media_type": media_type, "provider": label, "probe": probe,
            "expires": False}


# ------------------------------------------------------------------------------------------------ title actions

@router.post(P + "/{cid}/crawl")
def crawl_title(cid: str) -> dict:
    """T27 "Bu başlığı şimdi tara": the title page -> episodes -> source rows now, outside the crawl quotas."""
    cid = _item(cid)["id"]
    jid = crawl_manual.start_title(cid)
    if jid is None:
        raise _err(409, "no_active_site", "Başlığın etkin bir sitede kaydı yok")
    return {"job_id": jid}


@router.get(P + "/{cid}/crawl")
def crawl_title_state(cid: str) -> dict:
    return crawl_manual.title_job(_item(cid)["id"])


@router.post(P + "/{cid}/tmdb-refresh")
def tmdb_refresh(cid: str) -> dict:
    cid = _item(cid)["id"]
    if cat_hooks.enqueue_tmdb is None:
        raise _err(503, "tmdb_unavailable", "TMDB işi bağlı değil")
    db.execute("UPDATE library_items SET tmdb_enrich_status=NULL, tmdb_checked_at=NULL WHERE id=?", (cid,))
    job = cat_hooks.enqueue_tmdb(cid)
    if job is None:
        raise _err(409, "tmdb_off", "TMDB anahtarı yok; zenginleştirme kapalı")
    return {"ok": True, "job": job}


def _set_unlisted(cids: list, on: bool) -> int:
    n = 0
    with db.transaction() as conn:
        for cid in cids:
            if on:
                n += conn.execute("INSERT OR IGNORE INTO catalog_unlisted(canonical_id, at) VALUES (?,?)",
                                  (cid, int(time.time()))).rowcount
            else:
                n += conn.execute("DELETE FROM catalog_unlisted WHERE canonical_id=?", (cid,)).rowcount
    cache.refresh_soon()
    return n


@router.post(P + "/{cid}/unlist")
def unlist(cid: str) -> dict:
    return {"ok": True, "changed": _set_unlisted([_item(cid)["id"]], True)}


@router.post(P + "/{cid}/relist")
def relist(cid: str) -> dict:
    return {"ok": True, "changed": _set_unlisted([_item(cid)["id"]], False)}


def _delete(cid: str, site: str = "") -> dict:
    keys = db.query("SELECT site_id, key FROM source_items WHERE canonical_id=?" + (" AND site_id=?" if site else ""),
                    (cid, site) if site else (cid,))
    if site and not keys and site != ADMIN_SOURCE:
        raise _err(404, "not_found", "Bu sitenin kaydı yok")
    removed = 0
    for k in keys:
        write.remove_title(k["site_id"], k["key"])
        removed += 1
    if not site or site == ADMIN_SOURCE:
        if db.query_one("SELECT 1 FROM video_sources WHERE canonical_id=? AND source=?", (cid, ADMIN_SOURCE)):
            write.remove_title(ADMIN_SOURCE, cid)
            removed += 1
    if not db.query_one("SELECT 1 FROM library_items WHERE id=?", (cid,)):
        db.execute("DELETE FROM catalog_unlisted WHERE canonical_id=?", (cid,))
    return {"removed_records": removed}


@router.delete(P + "/{cid}")
def delete_title(cid: str, site: str = "") -> dict:
    cid = _item(cid)["id"]
    out = _delete(cid, site.strip())
    return {"ok": True, **out, "title_gone": db.query_one("SELECT 1 FROM library_items WHERE id=?", (cid,)) is None}


@router.post(P + "/bulk")
def bulk(body: dict = Body(default={})) -> dict:
    ids = [i for i in (body or {}).get("ids") or [] if isinstance(i, str) and i][:500]
    action = (body or {}).get("action")
    if not ids or action not in ("delete", "unlist", "relist"):
        raise _err(400, "bad_request", "ids ve action (delete|unlist|relist) gerekli")
    known = [r["id"] for r in db.query("SELECT id FROM library_items WHERE id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))]
    if action == "delete":
        for cid in known:
            _delete(cid)
        return {"ok": True, "done": len(known)}
    return {"ok": True, "done": _set_unlisted(known, action == "unlist")}
