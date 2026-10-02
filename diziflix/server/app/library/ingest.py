"""Ingest a source into the canonical library.

Flow (``ingest_source``):
  scraper.run_site(site) -> normalize each item -> upsert source_items ->
  resolve/create canonical id (TMDB when available, else provisional slug+year)
  -> merge every source_item bound to that canonical into library_items.

TMDB is optional (see tmdb.py). Without a key everything runs on provisional
canonical ids and never touches the network beyond the scraper itself.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import unicodedata
from typing import Any, Callable, Optional

from urllib.parse import urljoin

from .. import config, db
from ..scraper import run_site
from ..scraper import collections as scollections, config as scfg, drift, fetch, parse, schema, site_extractors, state
from . import tmdb, identity, videos, enrich, seasons, series_crawl, series_dir, gate
from .normalize import normalize

log = logging.getLogger("library.ingest")
_discovery_lock = threading.RLock()

# Progress hook: ``fn(stage)`` called at the stages of a scan where the library just changed, so a long scan is
# visible to its consumers (the server's catalogue snapshot) while it is still running. Set by ``cache.start``;
# unset (None) for the CLI, which then only writes the database. ``ingest`` never imports ``cache``.
# Stages: "titles" (title rows committed), "inventory" (one series' episodes written; many calls),
# "seasons" (TMDB season pass done). The hook does its own debouncing.
_progress_hook: Optional[Callable[[str], None]] = None


def set_progress_hook(fn: Optional[Callable[[str], None]]) -> None:
    global _progress_hook
    _progress_hook = fn


def get_progress_hook() -> Optional[Callable[[str], None]]:
    return _progress_hook


def _progress(stage: str, site: str) -> None:
    """Tell the hook (if any) what just finished. A hook that fails must never break or slow an ingest."""
    hook = _progress_hook
    if hook is None:
        return
    try:
        hook(stage)
    except Exception as exc:
        log.warning("ingest %s: progress hook (%s) failed: %s", site, stage, exc)

# First-page only, capped: genre pages have hundreds of pages (9k+ films total);
# a home row needs ~20-30 cards, so one list page per collection is plenty.
COLLECTION_LIMIT = 30
DETAIL_METADATA_TTL = 6 * 3600

# Source precedence for multi-source field merges (higher wins). Extend as
# sources are added; TMDB, once live, is the metadata authority.
SOURCE_PRECEDENCE = {"tmdb": 100, "sinemalar": 10}

_MERGE_FIELDS = (
    "type", "title", "original_title", "year", "overview",
    "genres", "rating", "runtime", "country", "followers", "cast",
    "poster_url", "backdrop_url",
)


def slugify(text: str) -> str:
    text = text or ""
    # Turkish-aware fold before stripping diacritics.
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"),
                 ("ç", "c"), ("ö", "o"), ("ü", "u")):
        text = text.replace(a, b).replace(a.upper(), b)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "untitled"


def canonical_id(norm: dict, tmdb_id: Optional[int] = None) -> str:
    if tmdb_id:
        return f"tmdb_tv_{tmdb_id}" if norm.get("type") == "series" else f"tmdb_{tmdb_id}"
    if identity.identifiers(norm).get("imdb"):
        return "imdb_" + identity.identifiers(norm)["imdb"]
    base = slugify(norm.get("original_title") or norm.get("title") or "")
    year = norm.get("year")
    key = f"{base}-{year}" if year else base
    return f"series-{key}" if norm.get("type") == "series" else key


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != []


def merge_canonical(conn, canonical_id: str) -> None:
    """Rebuild one library_items row from every source_item bound to it.

    Multi-source ready: fields are taken from the highest-precedence source that
    provides a non-empty value; ``field_provenance`` records the winner.
    """
    rows = conn.execute(
        "SELECT source, normalized FROM source_items WHERE canonical_id = ?",
        (canonical_id,),
    ).fetchall()
    sources = []
    for r in rows:
        try:
            norm = json.loads(r["normalized"] or "{}")
        except (TypeError, ValueError):
            norm = {}
        sources.append((SOURCE_PRECEDENCE.get(r["source"], 0), r["source"], norm))
    sources.sort(key=lambda t: -t[0])
    if not sources:
        return

    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    tmdb_id: Optional[int] = None
    added_candidate: Optional[int] = None
    for _, source, norm in sources:
        if norm.get("tmdb_id") and tmdb_id is None:
            tmdb_id = norm["tmdb_id"]
        if norm.get("_added_at") is not None and added_candidate is None:
            added_candidate = norm["_added_at"]
        for f in _MERGE_FIELDS:
            if f not in merged and _filled(norm.get(f)):
                merged[f] = norm[f]
                provenance[f] = source

    # Description ownership is independent of general metadata precedence:
    # Sinemalar first; otherwise retain the earliest discoverer's nonempty text.
    description_sources = sorted(sources, key=lambda t: (
        t[1] != "sinemalar", t[2].get("_added_at") or float("inf"), t[1]))
    for _, source, norm in description_sources:
        if isinstance(norm.get("overview"), str) and norm["overview"].strip():
            merged["overview"] = norm["overview"]
            provenance["overview"] = source
            break

    known_ids = {r["provider"]: r["external_id"] for r in conn.execute("SELECT * FROM external_ids WHERE canonical_id=?", (canonical_id,))}
    tmdb_id = int(known_ids["tmdb"]) if known_ids.get("tmdb") else None
    now = int(time.time())
    existing = conn.execute(
        "SELECT added_at, popularity FROM library_items WHERE id = ?", (canonical_id,)
    ).fetchone()
    added_at = existing["added_at"] if existing else (added_candidate if added_candidate is not None else now)
    popularity = merged.get("rating") or (existing["popularity"] if existing else 0) or 0

    conn.execute(
        """INSERT INTO library_items
           (id, tmdb_id, type, title, original_title, year, overview, genres,
            rating, runtime, country, followers, cast, poster_url, backdrop_url,
            popularity, added_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             tmdb_id=excluded.tmdb_id, type=excluded.type, title=excluded.title,
             original_title=excluded.original_title, year=excluded.year,
             overview=excluded.overview, genres=excluded.genres, rating=excluded.rating,
             runtime=excluded.runtime, country=excluded.country,
             followers=excluded.followers, cast=excluded.cast,
             poster_url=excluded.poster_url,
             backdrop_url=excluded.backdrop_url, popularity=excluded.popularity,
             updated_at=excluded.updated_at""",
        (
            canonical_id, tmdb_id, merged.get("type", "movie"), merged.get("title", ""),
            merged.get("original_title"), merged.get("year"), merged.get("overview", ""),
            json.dumps(merged.get("genres", []), ensure_ascii=False),
            merged.get("rating"), merged.get("runtime"),
            merged.get("country"), merged.get("followers"),
            json.dumps(merged.get("cast", []), ensure_ascii=False),
            merged.get("poster_url"), merged.get("backdrop_url"),
            popularity, added_at, now,
        ),
    )
    conn.execute("UPDATE library_items SET imdb_id=? WHERE id=?", (known_ids.get("imdb"), canonical_id))
    for field, source in provenance.items():
        conn.execute(
            "INSERT OR REPLACE INTO field_provenance (canonical_id, field, source) VALUES (?,?,?)",
            (canonical_id, field, source),
        )


def _keep_series_facts(norm: dict, old: dict) -> None:
    """A fresh card must not shrink a series' episode inventory nor replace page-derived facts with its thinner ones."""
    if norm.get("type") != "series":
        return
    if old.get("video_sources") and norm.get("video_sources"):
        norm["video_sources"] = series_crawl.merge_sources(old["video_sources"], norm["video_sources"])
    series_crawl.reapply_detail(norm, old)


def _collection_items(items: list[dict], collection: dict, limit: int) -> list[dict]:
    """Apply config-owned membership filters and ordering to parsed cards."""
    required = collection.get("required_fields") or []
    excluded = collection.get("excluded_fields") or []
    selected = [
        item for item in items
        if all(_filled(item.get(field)) for field in required)
        and not any(_filled(item.get(field)) for field in excluded)
    ]
    sort_by = collection.get("sort_by")
    if sort_by:
        selected.sort(
            key=lambda item: item.get(sort_by) if item.get(sort_by) is not None else -1,
            reverse=bool(collection.get("sort_desc")),
        )
    return selected[:limit]


# Rows a scan's collection fetches parsed BEFORE ``item_limit`` / required / sort rules cut them (the series directory, library/series_dir.py,
# is built from whole list pages: an alphabetical archive collection names every series page, not just the first ``item_limit``).
_row_sink: contextvars.ContextVar = contextvars.ContextVar("ingest_collection_rows", default=None)


def _fetch_collection(cfg: scfg.SiteConfig, collection: dict, limit: int) -> list[dict]:
    """Fetch one list page with optional collection-specific parse rules."""
    path = collection["path"]
    row_selector = collection.get("row_selector") or cfg.row_selector
    fields = collection.get("fields") or cfg.list_fields
    html = fetch.page(cfg, urljoin(cfg.base_url, path), wait_for=row_selector)
    raw = parse.parse_list(html, row_selector, fields)
    valid, metrics = schema.validate_items(cfg.schema, raw)
    verdict = drift.detect(metrics, {"min_fill_ratio": cfg.baseline().get("min_fill_ratio", .5)})
    if verdict["drift"]:
        raise ValueError(f"invalid collection {path}: {verdict['reasons']}")
    sink = _row_sink.get()
    if sink is not None:
        sink.extend(valid)
    return _collection_items(valid, collection, limit)


def parses_main_list(cfg: scfg.SiteConfig, collection: dict) -> bool:
    """True when ``collection`` reads the MAIN list page (``list_url``) with the main parse rules: its items are then
    filtered out of the already-fetched main result (no second fetch). A collection on that same path with its own
    ``row_selector`` or ``fields`` (different from the main ones) needs a parse of its own (``_fetch_collection``)."""
    if urljoin(cfg.base_url, collection["path"]) != urljoin(cfg.base_url, cfg.list_url):
        return False
    row_selector, fields = collection.get("row_selector"), collection.get("fields")
    return not ((row_selector and row_selector != cfg.row_selector) or (fields and fields != cfg.list_fields))


def load_collection(cfg: scfg.SiteConfig, collection: dict, main_items: list[dict], limit: int) -> list[dict]:
    """Items of one collection: filtered from ``main_items`` (the main list's valid items) when it parses the main
    page the main way (``parses_main_list``), else fetched and parsed on its own (``_fetch_collection``)."""
    if parses_main_list(cfg, collection):
        return _collection_items(main_items, collection, limit)
    return _fetch_collection(cfg, collection, limit)


def _enrich_series_catalogs(cfg: scfg.SiteConfig, by_key: dict[str, dict],
                            raw_by_key: dict[str, dict], reports: list[dict],
                            errors: list[dict], *, force: bool = False,
                            cards: Optional[dict[str, tuple[int, int]]] = None) -> None:
    """Read the full season/episode inventory of the given series into their ``normalized`` dicts (in place).

    A series is read only when ``series_crawl.due`` says so (``force`` skips that check); one request per
    series normally (its page lists every season), never a video provider. Failures are recorded on the series
    (backoff marker) and in ``errors``; they never propagate. Budgets/delays are the stage's business
    (``series_crawl.run_stage``)."""
    if not series_crawl.enabled(cfg):
        return
    for key, norm in by_key.items():
        if norm.get("type") != "series":
            continue
        card = (cards or {}).get(key)
        report_id = "episodes_" + key.replace("/", "_")
        if not force and series_crawl.due(norm.get(series_crawl.MARKER), card) is None:
            reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri",
                            "url": norm.get("source_url"), "status": "cached",
                            "count": len(norm.get("video_sources") or []), "duration_seconds": 0})
            continue
        # a card that links an EPISODE page: the series page comes from the site's list rows (library/series_dir.py); none known = no
        # request and no error ("unknown": the crawl leaves the series alone until its source_url changes)
        if series_dir.ensure(cfg, norm) == "unknown":
            reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri", "url": norm.get("source_url"),
                            "status": "unknown", "duration_seconds": 0})
            continue
        url = series_crawl.series_url(cfg, key, norm)
        started = time.monotonic()
        try:
            result = series_crawl.crawl(cfg, url)
        except series_crawl.NotSeriesPage:   # the page is no series page whatever the directory said: not an error, not retried
            series_dir.mark_unknown(norm)
            reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri", "url": url, "status": "unknown",
                            "duration_seconds": round(time.monotonic() - started, 2)})
            continue
        except Exception as exc:
            log.warning("ingest %s: series %s failed: %s", cfg.site_id, key, exc)
            series_crawl.mark_failed(norm, exc, time.time())
            errors.append({"list": report_id, "error": str(exc)})
            reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri", "url": url,
                            "status": "error", "error": str(exc),
                            "duration_seconds": round(time.monotonic() - started, 2)})
            continue
        now = time.time()
        # content that is not public (yaml ``blocked:`` / ``availability_gate:``; library/gate.py): judged before anything is written;
        # the probes keep the politeness delay after the series page that was just read
        pacer = gate.Pacer()
        pacer.fetched = True
        verdict = series_crawl.judge_series(cfg, key, norm, result, url, now, pacer=pacer)
        if verdict is not None and not series_crawl.gate_apply(norm, result, verdict, now):
            status = "blocked" if verdict["state"] == gate.BLOCKED else "retry"
            log.warning("ingest %s: series %s %s: %s", cfg.site_id, key, status, verdict.get("reason"))
            reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri", "url": url, "status": status,
                            "reason": verdict.get("reason"), "gate": verdict, "pages": result["pages"],
                            "duration_seconds": round(time.monotonic() - started, 2)})
            continue
        new = series_crawl.apply(norm, result, now, card)
        for warning in result["warnings"]:
            log.warning("series inventory %s/%s: %s", cfg.site_id, key, warning)
        marker = norm[series_crawl.MARKER]
        reports.append({"id": report_id, "title": (norm.get("title") or key) + " bölümleri", "url": url,
                        "status": "success", "count": marker["episodes"], "new": new,
                        "seasons": marker["seasons"], "unaired": marker["unaired"], "pages": result["pages"],
                        "complete": marker["complete"], "structured": result["structured"],
                        "warnings": result["warnings"],
                        **({"gate": verdict} if verdict is not None and verdict.get("blocked") else {}),
                        "duration_seconds": round(time.monotonic() - started, 2)})


def ingest_discovered_items(site: str, items: list[dict]) -> list[str]:
    """Cache lightweight live-search hits without adding them to home lists."""
    cfg = scfg.load_site(site)
    now = int(time.time())
    ordered_ids: list[str] = []
    # a series / film with a fresh "not public" verdict (library/gate.py) is not cached from a search card; one that was not
    # judged yet may stay on the card, opening it reads the inventory (``hydrate_series_item``) which judges and hides it
    blocked_keys = gate.blocked_item_keys(site) if gate.configured(cfg) else set()
    # a search hit that is an EPISODE card resolves to its series page like a scan's card (library/series_dir.py; no request)
    directory = series_dir.for_site(cfg) if series_dir.spec_of(cfg) else None
    with _discovery_lock:
        conn = db.connect()
        try:
            for raw in items:
                norm = normalize(site, raw)
                if not norm:
                    continue
                if directory is not None:
                    series_dir.ensure(cfg, norm, directory)
                key = norm["source_key"]
                if key in blocked_keys:
                    continue
                previous = conn.execute(
                    "SELECT canonical_id,normalized FROM source_items WHERE source=? AND source_key=?",
                    (site, key),
                ).fetchone()
                old = {}
                if previous:
                    try:
                        old = json.loads(previous["normalized"] or "{}")
                    except (TypeError, ValueError):
                        old = {}
                    for field, value in old.items():
                        if not _filled(norm.get(field)) and _filled(value):
                            norm[field] = value
                    _keep_series_facts(norm, old)
                # Search cache entries must not become the newest home cards.
                norm.setdefault("_added_at", old.get("_added_at", 0))
                cid = identity.choose(conn, site, norm, previous)
                conn.execute(
                    """INSERT INTO source_items
                       (source,source_key,canonical_id,raw,normalized,source_url,trailer_url,fetched_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(source,source_key) DO UPDATE SET
                         canonical_id=excluded.canonical_id,raw=excluded.raw,
                         normalized=excluded.normalized,source_url=excluded.source_url,
                         trailer_url=COALESCE(excluded.trailer_url,source_items.trailer_url),
                         fetched_at=excluded.fetched_at""",
                    (site, key, cid, json.dumps(raw, ensure_ascii=False),
                     json.dumps(norm, ensure_ascii=False), norm.get("source_url"),
                     norm.get("trailer_url"), now),
                )
                merge_canonical(conn, cid)
                videos.sync_source(conn, site, key, cid, norm, cfg)
                if cid not in ordered_ids:
                    ordered_ids.append(cid)
            conn.commit()
        finally:
            conn.close()
    return ordered_ids


def hydrate_item_metadata(item_id: str) -> bool:
    """Fetch and cache stable detail metadata for one title.

    Called by both the bounded post-scan warmup and the on-demand detail fallback.
    Only sources with declarative detail selectors are considered, so this never
    expands into a catalogue crawl or resolves a video provider.
    """
    with _discovery_lock:
        rows = db.query(
            "SELECT * FROM source_items WHERE canonical_id=? ORDER BY source='sinemalar' DESC",
            (item_id,),
        )
        for row in rows:
            try:
                cfg = scfg.load_site(row["source"])
                norm = json.loads(row["normalized"] or "{}")
                raw = json.loads(row["raw"] or "{}")
            except (FileNotFoundError, TypeError, ValueError):
                continue
            has_owned_extractor = site_extractors.supports_detail_metadata(row["source"])
            if not cfg.detail_fields and not has_owned_extractor:
                continue
            detail_at = int(norm.get("_detail_metadata_at") or 0)
            if detail_at > int(time.time()) - DETAIL_METADATA_TTL:
                continue

            raw_url = raw.get("detail_url") or norm.get("source_url") or row["source_url"]
            if not raw_url:
                continue
            detail_url = urljoin(cfg.base_url, raw_url)
            try:
                html = fetch.page(cfg, detail_url)
                detail = parse.parse_detail(html, cfg.detail_fields) if cfg.detail_fields else {}
                owned_detail = site_extractors.detail_metadata(row["source"], html, detail_url)
            except Exception as exc:
                log.warning("detail metadata %s failed: %s", item_id, exc)
                continue

            enriched_raw = dict(raw)
            enriched_raw["detail_url"] = detail_url
            for field, value in detail.items():
                if _filled(value):
                    enriched_raw[field] = value
            refreshed = normalize(row["source"], enriched_raw)
            if not refreshed:
                continue

            changed = False
            for field, value in refreshed.items():
                if _filled(value) and norm.get(field) != value:
                    norm[field] = value
                    changed = True
            for field, value in owned_detail.items():
                if _filled(value) and norm.get(field) != value:
                    norm[field] = value
                    changed = True
            norm["_detail_metadata_at"] = int(time.time())

            conn = db.connect()
            try:
                conn.execute(
                    """UPDATE source_items
                       SET raw=?,normalized=?,source_url=?,trailer_url=?,fetched_at=?
                       WHERE source=? AND source_key=?""",
                    (json.dumps(enriched_raw, ensure_ascii=False),
                     json.dumps(norm, ensure_ascii=False), norm.get("source_url"),
                     norm.get("trailer_url"), int(time.time()),
                     row["source"], row["source_key"]),
                )
                merge_canonical(conn, item_id)
                videos.sync_source(conn, row["source"], row["source_key"], item_id, norm, cfg)
                conn.commit()
            finally:
                conn.close()
            if changed:
                return True
        return False


def _round_robin_members(memberships: list[tuple[str, str, int]],
                         list_ids: list[str], limit: int) -> list[str]:
    """Choose a small, fair detail-warmup window from configured home rows."""
    buckets: dict[str, list[str]] = {list_id: [] for list_id in list_ids}
    for list_id, item_id, _position in memberships:
        if list_id in buckets and item_id not in buckets[list_id]:
            buckets[list_id].append(item_id)
    selected: list[str] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for list_id in list_ids:
            bucket = buckets[list_id]
            if depth < len(bucket) and bucket[depth] not in selected:
                selected.append(bucket[depth])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        depth += 1
    return selected


def prewarm_item_metadata(item_ids: list[str]) -> dict[str, int]:
    """Warm stable detail metadata sequentially without resolving video hosts."""
    attempted = cached = updated = 0
    for item_id in item_ids:
        attempted += 1
        before = db.query_one(
            "SELECT MAX(CAST(json_extract(normalized, '$._detail_metadata_at') AS INTEGER)) AS at "
            "FROM source_items WHERE canonical_id=?",
            (item_id,),
        )
        before_at = int(before["at"] or 0) if before else 0
        changed = hydrate_item_metadata(item_id)
        after = db.query_one(
            "SELECT MAX(CAST(json_extract(normalized, '$._detail_metadata_at') AS INTEGER)) AS at "
            "FROM source_items WHERE canonical_id=?",
            (item_id,),
        )
        after_at = int(after["at"] or 0) if after else 0
        if after_at:
            cached += 1
        if changed or after_at > before_at:
            updated += 1
    return {"attempted": attempted, "cached": cached, "updated": updated}


def hydrate_series_item(item_id: str) -> bool:
    """Read one series' season inventory when its detail is opened (only if it is due: never read, unfinished,
    stale or behind a newer episode) and write it. True when the library changed."""
    with _discovery_lock:
        # the first source of the title (``series_crawl.source_rows`` order: sites with an inventory module, then by name)
        # that is a series AND has the inventory stage enabled: a title with several sources must not look at a source that
        # has none (the same row ``series_crawl.inventory_due`` decided on)
        found = None
        for row in series_crawl.source_rows(item_id):
            try:
                cfg = scfg.load_site(row["source"])
                norm = json.loads(row["normalized"] or "{}")
                raw = json.loads(row["raw"] or "{}")
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if norm.get("type") == "series" and series_crawl.enabled(cfg):
                found = (row, cfg, norm, raw)
                break
        if found is None:
            return False
        row, cfg, norm, raw = found
        before = json.dumps(norm, sort_keys=True)
        reports: list[dict] = []
        errors: list[dict] = []
        key = row["source_key"]
        _enrich_series_catalogs(cfg, {key: norm}, {key: raw}, reports, errors)
        if json.dumps(norm, sort_keys=True) == before:
            return False
        if series_crawl.gate_removes(norm):   # opened a series that turned out not to be public: it leaves the library
            gate.remove_items(row["source"], [key])
            return True
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE source_items SET normalized=?,fetched_at=?,trailer_url=COALESCE(?,trailer_url),source_url=COALESCE(?,source_url) "
                "WHERE source=? AND source_key=?",
                (json.dumps(norm, ensure_ascii=False), int(time.time()), norm.get("trailer_url"), norm.get("source_url"), row["source"], key),
            )
            merge_canonical(conn, item_id)
            videos.sync_source(conn, row["source"], key, item_id, norm, cfg)
            conn.commit()
        finally:
            conn.close()
        return not errors


COVERAGE_SAMPLES = 5


def _coverage(site: str, cfg, by_key: dict[str, dict]) -> dict[str, Any]:
    """What the titles written by this scan can be played from: one light read of ``video_sources`` (per source key counts)
    for the keys of THIS run, taken after the series inventory stage. ``items`` / ``with_sources`` (any non-trailer source),
    ``series_items`` / ``series_without_sources`` (series items with no episode source), ``sample_without_sources`` (at most
    ``COVERAGE_SAMPLES`` ``{source_key, detail_url}``). A series without episodes that the inventory stage has not reached yet
    (still due: deferred by its budget, or its read failed) is not "without sources" but ``series_pending``: it is left out of
    ``series_items`` (the denominator), so a slow inventory never looks like a normalize problem."""
    keys = list(by_key)
    cov: dict[str, Any] = {"items": len(keys), "with_sources": 0, "series_items": 0, "series_without_sources": 0,
                           "series_pending": 0, "sample_without_sources": [], "playback": cfg.data.get("playback")}
    if not keys:
        return cov
    have = {r["source_key"]: (int(r["episodes"] or 0), int(r["playable"] or 0)) for r in db.query(
        "SELECT source_key, SUM(kind='episode') AS episodes, SUM(kind!='trailer') AS playable FROM video_sources "
        "WHERE source=? GROUP BY source_key", (site,))}
    cov["with_sources"] = sum(1 for k in keys if have.get(k, (0, 0))[1] > 0)
    inventory = series_crawl.enabled(cfg)
    for key in keys:
        norm = by_key[key]
        if norm.get("type") != "series":
            continue
        if have.get(key, (0, 0))[0] == 0 and inventory:
            row = db.query_one("SELECT normalized FROM source_items WHERE source=? AND source_key=?", (site, key))
            try:
                marker = (json.loads(row["normalized"] or "{}") if row else {}).get(series_crawl.MARKER)
            except (TypeError, ValueError):
                marker = None
            marker = marker if isinstance(marker, dict) else None
            if series_crawl.due(marker, series_crawl.latest_card_episode(norm)) is not None or (marker and not marker.get("ok_at")):
                cov["series_pending"] += 1
                continue
        cov["series_items"] += 1
        if have.get(key, (0, 0))[0] == 0:
            cov["series_without_sources"] += 1
            if len(cov["sample_without_sources"]) < COVERAGE_SAMPLES:
                cov["sample_without_sources"].append(
                    {"source_key": key, "detail_url": norm.get("source_url") or norm.get("detail_url") or key})
    return cov


def coverage_warnings(coverage: dict[str, Any]) -> list[str]:
    """The Olay defteri warning lines of a scan's coverage: a site whose series items mostly have no episode source (at least
    ``PLAYHEAL_COVERAGE_MIN_SERIES`` series, ``PLAYHEAL_COVERAGE_RATIO`` of them; ``scraper/playheal.coverage_signal``)."""
    from ..scraper import playheal
    if not playheal.coverage_signal(coverage):
        return []
    return [f"{coverage['series_without_sources']} of {coverage['series_items']} series without episode sources: "
            "normalize.episode_source missing or the site needs a series inventory"]


def _ingest_source(site: str) -> dict[str, Any]:
    """Scrape every home-screen collection of ``site`` and merge into the library.

    The main list (``list_url``) still runs through ``run_site`` so drift/heal and
    per-run state are unchanged; the healed config is then reused to fetch the
    extra collections (upcoming + per-genre). Films are deduped across
    collections and each film's list/genre memberships are recorded in
    ``library_lists`` (a film is fetched once; extra memberships are just rows)."""
    main_started = time.monotonic()
    result = run_site(site, persist=True)
    main_duration = round(time.monotonic() - main_started, 2)
    if result.error or result.drift.get("drift"):
        log.warning("ingest %s: scraper error: %s", site, result.error)
        return {"source": site, "error": result.error or "catalogue validation failed", "scraped": 0, "ingested": 0}

    cfg = scfg.load_site(site)  # reload: run_site may have healed + bumped version
    collections = cfg.collections

    # Build the ordered work list: (list_id, items, genre_slug|None). The main
    # /filmler page we already fetched via run_site is the "new" collection when
    # one is declared with role "new"; extra collections are fetched here.
    main_id = f"source_{site}"
    limit = max(1, min(200, int(cfg.data.get("item_limit", COLLECTION_LIMIT))))
    collection_errors: list[dict] = []
    collection_reports: list[dict] = []
    work: list[tuple[str, list[dict], Optional[str]]] = []
    handled_main = False
    archive_rows: list[dict] = []   # whole collection pages (pre-limit) for the series directory
    sink_token = _row_sink.set(archive_rows)
    try:
        if collections:
            for c in collections:
                cid_list = c.get("id") or ""
                role = c.get("role")
                if role == "featured":  # the hero list is always `featured_<site>` (what the home hero merges by role)
                    cid_list = scollections.list_id("featured", site)
                elif role == scollections.CATEGORY_ROLE:  # `category_<slug>_<site>`; an unregistered (deleted) category still gets its list
                    cslug = scollections.category_of(c)
                    if scollections.check_category_slug(cslug):
                        log.warning("ingest %s: category collection %s skipped: %s", site, cid_list,
                                    scollections.check_category_slug(cslug))
                        collection_errors.append({"list": cid_list, "error": "category slug missing or invalid"})
                        continue
                    cid_list = scollections.list_id(role, site, cslug)
                gslug = c.get("genre") if role == "genre" else None
                if parses_main_list(cfg, c):
                    items = _collection_items(result.items, c, limit)
                    work.append((cid_list or main_id, items, gslug))
                    collection_reports.append({"id": cid_list or main_id, "title": c.get("title", cid_list),
                        "url": urljoin(cfg.base_url, c["path"]), "status": "success",
                        "count": len(items), "duration_seconds": main_duration})
                    handled_main = True
                    continue
                started = time.monotonic()
                try:
                    items = _fetch_collection(cfg, c, limit)
                except Exception as exc:  # graceful: skip a broken collection
                    log.warning("ingest %s: collection %s failed: %s", site, cid_list, exc)
                    collection_errors.append({"list": cid_list, "error": str(exc)})
                    collection_reports.append({"id": cid_list, "title": c.get("title", cid_list),
                        "url": urljoin(cfg.base_url, c["path"]), "status": "error", "error": str(exc),
                        "duration_seconds": round(time.monotonic() - started, 2)})
                    continue
                collection_reports.append({"id": cid_list, "title": c.get("title", cid_list),
                    "url": urljoin(cfg.base_url, c["path"]), "status": "success", "count": len(items),
                    "duration_seconds": round(time.monotonic() - started, 2)})
                if items:
                    work.append((cid_list, items, gslug))
    finally:
        _row_sink.reset(sink_token)
    if not handled_main and not any(w[0] == main_id for w in work):
        work.insert(0, (main_id, result.items[:limit], None))
        collection_reports.insert(0, {"id": main_id, "title": "Ana sayfa", "url": urljoin(cfg.base_url, cfg.list_url),
            "status": "success", "count": len(result.items[:limit]), "duration_seconds": main_duration})

    # Configured detail discoveries use the same normalizer, identity and merge path.
    for path in cfg.data.get("detail_pages", []):
        started = time.monotonic()
        url = urljoin(cfg.base_url, path)
        try:
            html = fetch.page(cfg, url)
            item = parse.parse_detail(html, cfg.detail_fields)
            item["detail_url"] = url
            valid, _ = schema.validate_items(cfg.schema, [item])
            if not valid or not valid[0].get("year"):
                raise ValueError("detail title/year could not be verified")
            valid[0]["_detail_checked"] = True
            work.append(("detail_" + site + "_" + slugify(path), valid, None))
            collection_reports.append({"id": path, "title": valid[0]["title"], "url": url,
                "status": "success", "count": 1, "duration_seconds": round(time.monotonic()-started,2)})
        except Exception as exc:
            collection_errors.append({"list": path, "error": str(exc)})
            collection_reports.append({"id": path, "title": path, "url": url,
                "status": "error", "error": str(exc), "duration_seconds": round(time.monotonic()-started,2)})

    # Hero list `featured_<site>`: a `role: featured` collection wins; else the main list's hero cards (class
    # `poster-media`, the `featured` list field) when the site marks any (yabancidizi).
    featured_id = scollections.list_id("featured", site)
    if not any(w[0] == featured_id for w in work):
        featured_items = [i for i in result.items if i.get("featured") == "poster-media"]
        if featured_items:
            work.append((featured_id, featured_items, None))

    # A title can occur first as a sparse hero, then as a richer movie card.
    # Merge before choosing an ID; all memberships must refer to that same ID.
    rejected = 0
    by_key: dict[str, dict] = {}
    raw_by_key: dict[str, dict] = {}
    for _list_id, items, _genre in work:
        for raw in items:
            norm = normalize(site, raw)
            if not norm:
                rejected += 1
                continue
            key = norm["source_key"]
            target = by_key.setdefault(key, dict(norm))
            raw_by_key.setdefault(key, raw)
            if norm.get("video_sources"):
                combined = target.get("video_sources", []) + norm["video_sources"]
                target["video_sources"] = list({json.dumps(v, sort_keys=True): v for v in combined}.values())
            for field, value in norm.items():
                if not _filled(target.get(field)) and _filled(value):
                    target[field] = value

    # A card that links an EPISODE page ("Son Eklenen Bölümler") becomes a record of its SERIES page: the site's whole list rows (this scan's list
    # + collection pages, and the series pages the library knows) are the directory, so the inventory stage can read the series (no request here)
    dir_stats = {"resolved": 0, "unknown": 0}
    try:
        if series_dir.spec_of(cfg):
            normalized_rows = (normalize(site, raw) for raw in list(result.items) + archive_rows)
            series_dir.remember(cfg, series_dir.build(cfg, (n for n in normalized_rows if n)))
            directory = series_dir.for_site(cfg)
            for norm in by_key.values():
                outcome = series_dir.ensure(cfg, norm, directory)
                if outcome == "resolved":
                    dir_stats["resolved"] += 1
                elif outcome == "unknown":
                    dir_stats["unknown"] += 1
    except Exception as exc:   # the directory is an extra: a card it cannot resolve stays as it was
        log.warning("ingest %s: series directory skipped: %s", site, exc)

    # Content that is not public never enters the library (yaml ``blocked:`` / ``availability_gate:``, library/gate.py): a series /
    # film with a fresh blocked verdict is skipped; films and card-only series are judged BEFORE they are written (a series with an
    # inventory is judged after it, in series_crawl); what is blocked and already in the library leaves it (user data stays)
    gate_stats = gate.new_counters()
    gate_skip: set[str] = set()
    if gate.configured(cfg):
        try:
            gate_skip = gate.blocked_item_keys(site) & set(by_key)
            gate_stats["items"] += len(gate_skip)
            screened = gate.screen(cfg, site, {k: n for k, n in by_key.items() if k not in gate_skip})
            gate_skip |= set(screened["dropped"])
            gate.merge_counters(gate_stats, screened["counters"])
            leaving = (gate.blocked_item_keys(site) | set(screened["removed_keys"])) & set(by_key)
            if leaving:
                gate_stats["removed"] += gate.remove_items(site, leaving)
        except Exception as exc:  # the gate is an extra: a failing check never breaks an ingest
            log.warning("ingest %s: availability gate skipped: %s", site, exc)

    now = int(time.time())
    added = updated = unchanged = 0
    ingested = 0
    scraped = 0
    canonical_ids: set[str] = set()
    seen_keys: set[str] = set()
    resolved_ids: dict[str, str] = {}
    # list_id -> ordered [(position, canonical_id)]
    memberships: list[tuple[str, str, int]] = []

    # Keep network lookups outside the SQLite write transaction. Enrichment is
    # best-effort: any failure leaves the source data/artwork untouched.
    tmdb_hits: dict[str, dict] = {}
    enrich_stats = enrich.new_counters()
    try:
        jobs: dict[str, dict] = {}
        if enrich.auto_enabled():  # key present AND the admin's "tmdb_auto" switch on (read live)
            rc = db.connect()
            try:
                for key, norm in by_key.items():
                    prev = rc.execute("SELECT canonical_id FROM source_items WHERE source=? AND source_key=?",
                                      (site, key)).fetchone()
                    prev_cid = prev["canonical_id"] if prev else None
                    state = enrich.load_state(rc, prev_cid)
                    if not enrich.should_run(norm.get("type", "movie"), state):
                        enrich_stats["skipped"] += 1
                        continue
                    known = {r["provider"]: r["external_id"] for r in rc.execute(
                        "SELECT provider,external_id FROM external_ids WHERE canonical_id=?", (prev_cid,))} if prev_cid else {}
                    jobs[key] = dict(
                        title=norm.get("title", ""), year=norm.get("year"),
                        original_title=norm.get("original_title"), media_type=norm.get("type", "movie"),
                        tmdb_id=norm.get("tmdb_id") or known.get("tmdb"),
                        imdb_id=norm.get("imdb_id") or known.get("imdb"))
            finally:
                rc.close()
            tmdb_hits, batch = enrich.run_batch(jobs)
            for k, v in batch.items():
                enrich_stats[k] = enrich_stats[k] + v if k != "seconds" else v
    except Exception as exc:  # never let TMDB break ingest
        log.warning("ingest %s: tmdb enrichment skipped: %s", site, exc)
        tmdb_hits = {}
    conn = db.connect()
    try:
        for list_id, items, _gslug in work:
            for pos, raw in enumerate(items):
                scraped += 1
                norm = normalize(site, raw)
                if not norm:
                    continue

                key = norm["source_key"]
                if key in gate_skip:
                    continue   # blocked / not verified yet: not written (library/gate.py)
                norm = by_key[key]
                if key in resolved_ids:
                    memberships.append((list_id, resolved_ids[key], pos))
                    continue

                previous = conn.execute(
                    "SELECT canonical_id, normalized FROM source_items WHERE source=? AND source_key=?",
                    (site, key),
                ).fetchone()
                if previous:
                    old = json.loads(previous["normalized"] or "{}")
                    for field, value in old.items():
                        if not _filled(norm.get(field)) and _filled(value):
                            norm[field] = value
                    _keep_series_facts(norm, old)

                if previous:
                    for bound in conn.execute("SELECT provider,external_id FROM external_ids WHERE canonical_id=?", (previous["canonical_id"],)):
                        norm.setdefault(bound["provider"] + "_id", bound["external_id"])
                hit = tmdb_hits.get(key)
                if hit and hit.get("status") == "matched":
                    enrich.fill_norm(norm, hit["data"])

                cid = identity.choose(conn, site, norm, previous)
                resolved_ids[key] = cid
                memberships.append((list_id, cid, pos))
                canonical_ids.add(cid)

                if norm["source_key"] in seen_keys:
                    continue  # dedup: fetch/store each film once per run
                seen_keys.add(norm["source_key"])
                if not previous:
                    added += 1
                elif {k: v for k, v in norm.items() if not k.startswith("_")} != {k: v for k, v in old.items() if not k.startswith("_")}:
                    updated += 1
                else:
                    unchanged += 1
                norm.setdefault("_added_at", now + len(seen_keys))  # first-seen order

                conn.execute(
                    """INSERT INTO source_items
                       (source, source_key, canonical_id, raw, normalized,
                        source_url, trailer_url, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(source, source_key) DO UPDATE SET
                         canonical_id=excluded.canonical_id, raw=excluded.raw,
                         normalized=excluded.normalized, source_url=excluded.source_url,
                         trailer_url=COALESCE(excluded.trailer_url, source_items.trailer_url),
                         fetched_at=excluded.fetched_at""",
                    (
                        site, norm["source_key"], cid,
                        json.dumps(raw_by_key[key], ensure_ascii=False),
                        json.dumps(norm, ensure_ascii=False),
                        norm.get("source_url"), norm.get("trailer_url"), now,
                    ),
                )
                merge_canonical(conn, cid)
                if hit:
                    enrich.apply_result(conn, cid, site, key, hit, now)
                videos.sync_source(conn, site, key, cid, norm, cfg)
                ingested += 1

        aliases = {r["alias"]: r["canonical_id"] for r in conn.execute("SELECT * FROM catalogue_aliases")}
        memberships = [(lid, aliases.get(cid, cid), pos) for lid, cid, pos in memberships]
        # Full rebuild of the membership table for this run's lists.
        list_ids = {m[0] for m in memberships}
        for lid in list_ids:
            conn.execute("DELETE FROM library_lists WHERE list_id = ?", (lid,))
        conn.executemany(
            "INSERT OR REPLACE INTO library_lists (list_id, canonical_id, position) VALUES (?,?,?)",
            memberships,
        )
        conn.commit()
    finally:
        conn.close()
    _progress("titles", site)  # every title of this scan is readable now: do not make clients wait for the rest
    for key in gate_skip:   # the rest of the scan (inventory, coverage, TMDB) only knows the titles that were written
        by_key.pop(key, None)

    # Full season/episode inventory of the series that need it (never read, unfinished, stale, or behind a new
    # episode on this run's home feed): budgeted + polite (series_crawl.run_stage), best-effort, runs even when
    # the home cards were "unchanged" and covers series that are not on the home page any more. It goes before
    # the TMDB season pass so the new seasons/episodes get their TMDB artwork in the same run.
    crawl_stats = series_crawl.new_counters()
    try:
        crawl_stats = series_crawl.run_stage(cfg, site, by_key, on_progress=lambda: _progress("inventory", site))
    except Exception as exc:  # never let the inventory stage break ingest
        log.warning("ingest %s: series inventory skipped: %s", site, exc)
    crawl_stats["resolved"] = int(crawl_stats.get("resolved") or 0) + dir_stats["resolved"]
    crawl_stats["unknown"] = int(crawl_stats.get("unknown") or 0) + dir_stats["unknown"]
    crawled_ids = crawl_stats.pop("cids", [])
    for key in crawl_stats.pop("gone", []):   # series the inventory stage found blocked: out of the library, out of this scan's numbers
        by_key.pop(key, None)
    gate.merge_counters(gate_stats, crawl_stats.get("blocked"))
    try:   # blocked episode / film pages of earlier scans that are old enough are judged again (a lifted block gets its content back)
        gate.recheck(cfg, site)
    except Exception as exc:
        log.warning("ingest %s: blocked recheck skipped: %s", site, exc)
    coverage: Optional[dict[str, Any]] = None
    warnings: list[str] = []
    try:   # after the inventory stage, so series it has not reached do not look "without sources"
        coverage = _coverage(site, cfg, by_key)
        warnings = coverage_warnings(coverage)
        for line in warnings:
            log.warning("ingest %s: %s", site, line)
    except Exception as exc:  # a coverage problem never breaks the ingest
        log.warning("ingest %s: coverage skipped: %s", site, exc)

    # TMDB season posters + episode metadata/stills for the series this run touched (best-effort; only
    # seasons the library really has and only when still missing/stale; same TMDB time budget as the title
    # lookups; obeys tmdb_auto + tmdb_types like they do). ingest_source holds the ingest lock already.
    season_stats = seasons.new_counters()
    try:
        series_ids = [cid for key, cid in resolved_ids.items() if by_key.get(key, {}).get("type") == "series"]
        series_ids += [cid for cid in crawled_ids if cid not in series_ids]
        if series_ids:
            left = max(0.0, config.TMDB_BUDGET_SECONDS - float(enrich_stats.get("seconds") or 0))
            season_stats = seasons.auto_enrich(series_ids, budget=left, locked=True)
    except Exception as exc:  # never let TMDB break ingest
        log.warning("ingest %s: tmdb season enrichment skipped: %s", site, exc)
    _progress("seasons", site)

    lists_n = len({m[0] for m in memberships})
    prewarm_lists = [str(value) for value in cfg.data.get("detail_prewarm_lists", []) if value]
    prewarm_limit = max(0, min(50, int(cfg.data.get("detail_prewarm_limit", 0))))
    prewarm_ids = _round_robin_members(memberships, prewarm_lists, prewarm_limit)
    detail_cache = prewarm_item_metadata(prewarm_ids) if prewarm_ids else {
        "attempted": 0, "cached": 0, "updated": 0,
    }
    log.info("ingest %s: scraped=%d ingested=%d canonical=%d lists=%d tmdb=%s",
             site, scraped, ingested, len(canonical_ids), lists_n, tmdb.enabled())
    return {
        "source": site,
        "scraped": scraped,
        "ingested": ingested,
        "canonical": len(canonical_ids),
        "lists": lists_n,
        "collection_errors": collection_errors,
        "collections": collection_reports,
        "added": added, "updated": updated, "unchanged": unchanged,
        "duplicates": sum(len(items) for _, items, _ in work) - len(by_key) - rejected,
        "rejected": rejected,
        "config_version": cfg.version,
        "fetch_mode": cfg.fetch_mode,
        "partial": bool(collection_errors),
        "detail_cache": detail_cache,
        "tmdb": tmdb.enabled(),
        "tmdb_enrich": enrich_stats,
        "tmdb_seasons": season_stats,
        "series_crawl": crawl_stats,
        **({"blocked": gate_stats} if gate.active(gate_stats) else {}),
        "coverage": coverage,
        "warnings": warnings,
        "error": None,
    }


def ingest_source(site: str, trigger: str = "cli") -> dict[str, Any]:
    """Serialize CLI, scheduler and admin ingests; publish the same outcome."""
    if site not in scfg.list_sites():
        raise ValueError(f"unknown scraper site {site!r}")
    with enrich.ingest_lock():
        started = time.monotonic()
        started_at = state._now()
        owned = state.activity_start(site, "scan", trigger)
        try:
            result = _ingest_source(site)
        except Exception as exc:
            log.exception("ingest failed for %s", site)
            result = {"source": site, "error": str(exc), "ingested": 0}
        finally:
            if owned:
                state.activity_end(site, "scan")
        import uuid
        result.update({"run_id": uuid.uuid4().hex, "started_at": started_at,
                       "finished_at": state._now(), "duration_seconds": round(time.monotonic() - started, 2),
                       "status": "error" if result.get("error") else "partial" if result.get("partial") else "success"})
        state.record_ingest(site, result)
        dur = result["duration_seconds"]
        scraped = result.get("scraped") or 0
        state.record_ops_run({
            "site": site, "started_at": started_at, "duration": dur, "trigger": trigger,
            "status": result["status"], "scraped": scraped, "ingested": result.get("ingested") or 0,
            "pages": 1 + len(result.get("collections") or []),
            "rate": round(scraped / dur, 2) if dur > 0 else None,
            "error": result.get("error"),
            "collection_errors": len(result.get("collection_errors") or []),
            "tmdb_enrich": result.get("tmdb_enrich"),
            "tmdb_seasons": result.get("tmdb_seasons"),
            "series_crawl": None if (result.get("series_crawl") or {}).get("disabled") else
            {k: v for k, v in (result.get("series_crawl") or {}).items() if k != "items"} or None,
            **({"blocked": result["blocked"]} if result.get("blocked") else {}),
            **({"coverage": result["coverage"]} if result.get("coverage") else {}),
            **({"warnings": result["warnings"]} if result.get("warnings") else {}),
        })
        if not result.get("error"):
            try:   # a measured line for the site's handoff note (the first scans, then at most daily on a change); never breaks the scan
                from ..scraper import site_handoff
                site_handoff.record_scan(site, result)
            except Exception as exc:
                log.warning("ingest %s: handoff scan findings skipped: %s", site, exc)
        if (result.get("coverage") is not None or result.get("series_crawl") is not None) and not result.get("error"):
            try:   # the "no sources" / "series pages unreadable" signals of this scan (at most one repair run per scan: playheal consumes it)
                from ..scraper import playheal
                if result.get("coverage") is not None:
                    playheal.record_coverage(site, result["coverage"])
                playheal.record_crawl(site, result.get("series_crawl"))
                playheal.maybe_trigger(site)
            except Exception as exc:
                log.warning("ingest %s: playheal coverage hook failed: %s", site, exc)
        return result
