"""Small, complete, idempotent catalogue writes (README §5.8).

The crawl hands over ONE record at a time (frontier, T11); every function here is a single ``db.transaction()``,
gives the same result when called again with the same input, and ends with ``cache.refresh_soon()``.

    upsert_title(site_id, rec, *, origins=()) -> canonical_id
    upsert_episodes(site_id, title_key, episodes) -> int
    set_movie_source(site_id, title_key, url) -> None
    mark_blocked(site_id, url, *, kind, reason, via) -> None   (+ unmark_blocked)
    remove_title(site_id, key) -> None
    delete_site(site_id) -> dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from ... import config, db
from . import cache, enrich, hooks, identity
from .textnorm import clean_title, is_placeholder_image

log = logging.getLogger("catalog.write")

TYPES = ("movie", "series")
REQUIRED = ("key", "type", "title", "source_url")
# fields a record may carry (README §5.8 + the optional extras the old normalizer produced)
REC_FIELDS = ("title", "original_title", "year", "overview", "genres", "rating", "runtime", "country", "followers", "cast",
              "poster_url", "backdrop_url", "trailer_url", "tmdb_id", "imdb_id")
_MERGE_FIELDS = ("type", "title", "original_title", "year", "overview", "genres", "rating", "runtime", "country",
                 "followers", "cast", "poster_url", "backdrop_url")
BLOCKED_KINDS = ("episode", "movie", "series")
BLOCKED_RECHECK_DAYS = 7.0        # a blocked page is "fresh" (never written) for this long, then judged again


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != []


def _http_url(value: Any) -> Optional[str]:
    p = urlparse(str(value or ""))
    return str(value) if p.scheme in ("http", "https") and p.hostname else None


# --- placeholder artwork: URLs several different titles share ----------------------------------------------------

SHARED_ART_MIN = 3        # the same image URL on >= this many different titles = a site placeholder, not a poster
_SHARED_TTL = 60.0
_shared_cache: dict[str, tuple[float, frozenset]] = {}


def shared_art_urls(conn, force: bool = False) -> frozenset:
    """Poster/backdrop URLs that >= ``SHARED_ART_MIN`` different canonical titles carry in their SOURCE items (60 s cache per
    database). Never raises."""
    key = str(config.DB_PATH)
    hit = _shared_cache.get(key)
    now = time.monotonic()
    if hit and not force and now - hit[0] < _SHARED_TTL:
        return hit[1]
    urls: set[str] = set()
    try:
        for field in ("poster_url", "backdrop_url"):
            for r in conn.execute(
                    f"SELECT json_extract(normalized,'$.{field}') AS u FROM source_items "
                    f"WHERE json_extract(normalized,'$.{field}') LIKE 'http%' "
                    f"GROUP BY u HAVING COUNT(DISTINCT canonical_id) >= ?", (SHARED_ART_MIN,)):
                if r["u"]:
                    urls.add(r["u"])
    except Exception as exc:
        log.debug("shared art urls skipped: %s", exc)
    out = frozenset(urls)
    _shared_cache[key] = (now, out)
    return out


# --- merge -------------------------------------------------------------------------------------------------------

def merge_canonical(conn, canonical_id: str) -> None:
    """Rebuild one library_items row from every source_item bound to it.

    Rule: TMDB > the most recently fetched source; a field is taken from the first source (in that order) with a non-empty
    value and ``field_provenance`` records the winner. A placeholder poster/backdrop counts as empty. ``added_at`` is set
    once (first write) and never changes here."""
    rows = conn.execute(
        "SELECT site_id, normalized, fetched_at FROM source_items WHERE canonical_id = ?", (canonical_id,)).fetchall()
    sources = []
    for r in rows:
        try:
            norm = json.loads(r["normalized"] or "{}")
        except (TypeError, ValueError):
            norm = {}
        sources.append((1 if r["site_id"] == "tmdb" else 0, r["fetched_at"] or 0, r["site_id"], norm))
    sources.sort(key=lambda t: (-t[0], -t[1], t[2]))
    if not sources:
        return
    shared = shared_art_urls(conn)

    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for _, _, source, norm in sources:
        for f in _MERGE_FIELDS:
            if f not in merged and _filled(norm.get(f)):
                if f in ("poster_url", "backdrop_url") and is_placeholder_image(norm[f], shared):
                    continue
                merged[f] = norm[f]
                provenance[f] = source

    known_ids = {r["provider"]: r["external_id"] for r in conn.execute("SELECT * FROM external_ids WHERE canonical_id=?", (canonical_id,))}
    tmdb_id = int(known_ids["tmdb"]) if known_ids.get("tmdb") else None
    now = int(time.time())
    existing = conn.execute("SELECT added_at, popularity FROM library_items WHERE id = ?", (canonical_id,)).fetchone()
    added_at = existing["added_at"] if existing and existing["added_at"] else now
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
    conn.execute("DELETE FROM field_provenance WHERE canonical_id=?", (canonical_id,))
    for field, source in provenance.items():
        conn.execute("INSERT OR REPLACE INTO field_provenance (canonical_id, field, source) VALUES (?,?,?)",
                     (canonical_id, field, source))


# --- titles ------------------------------------------------------------------------------------------------------

def _validate(rec: Any) -> dict:
    if not isinstance(rec, dict):
        raise ValueError("rec must be a dict")
    for name in REQUIRED:
        if not isinstance(rec.get(name), str) or not rec[name].strip():
            raise ValueError(f"rec.{name} is required")
    if rec["type"] not in TYPES:
        raise ValueError(f"rec.type must be one of {TYPES}")
    if not _http_url(rec["source_url"]):
        raise ValueError("rec.source_url must be an http(s) URL")
    return rec


def _norm_of(rec: dict) -> dict:
    """The stored ``normalized`` record: the known fields of ``rec`` (placeholder artwork dropped, title cleaned)."""
    norm: dict[str, Any] = {"source_key": rec["key"].strip(), "type": rec["type"], "source_url": rec["source_url"].strip()}
    for f in REC_FIELDS:
        if _filled(rec.get(f)):
            norm[f] = rec[f]
    norm["title"] = clean_title(rec["title"], series=rec["type"] == "series")
    year = norm.get("year")
    if year is not None:
        try:
            norm["year"] = int(year)
        except (TypeError, ValueError):
            norm.pop("year")
    if isinstance(norm.get("genres"), str):
        norm["genres"] = [g.strip() for g in norm["genres"].split(",") if g.strip()]
    for f in ("poster_url", "backdrop_url"):
        if f in norm and (not _http_url(norm[f]) or is_placeholder_image(norm[f])):
            norm.pop(f)
    if isinstance(rec.get("extra"), dict) and rec["extra"]:
        norm["extra"] = rec["extra"]
    return norm


def _tmdb_wanted(cid: str) -> bool:
    try:
        return enrich.needs_lookup(cid)
    except Exception as exc:  # TMDB settings trouble never breaks a write
        log.warning("tmdb check skipped: %s", type(exc).__name__)
        return False


def upsert_title(site_id: str, rec: dict, *, origins: Iterable[tuple] = ()) -> str:
    """Write one title record of ``site_id``; returns its canonical id.

    ``rec`` = ``{key, type: movie|series, title, source_url, year?, poster_url?, backdrop_url?, genres?, overview?, extra?}``.
    Earlier non-empty fields of the same source stay when this record lacks them. ``origins`` = ``[(kind, value, position)]``
    (kind: section|listing|genre|type) are added / refreshed (``seen_at``); origins not seen this time stay."""
    if not site_id or not isinstance(site_id, str):
        raise ValueError("site_id is required")
    _validate(rec)
    norm = _norm_of(rec)
    key = norm["source_key"]
    now = int(time.time())
    with db.transaction() as conn:
        previous = conn.execute("SELECT canonical_id, normalized FROM source_items WHERE site_id=? AND key=?",
                                (site_id, key)).fetchone()
        if previous:
            try:
                old = json.loads(previous["normalized"] or "{}")
            except (TypeError, ValueError):
                old = {}
            for field, value in old.items():
                if not _filled(norm.get(field)) and _filled(value):
                    norm[field] = value
            for bound in conn.execute("SELECT provider,external_id FROM external_ids WHERE canonical_id=?",
                                      (previous["canonical_id"],)):
                norm.setdefault(bound["provider"] + "_id", bound["external_id"])
        cid = identity.choose(conn, site_id, norm, previous)
        raw = {k: v for k, v in rec.items() if k != "extra"}
        conn.execute(
            """INSERT INTO source_items (site_id, key, canonical_id, raw, normalized, source_url, trailer_url, fetched_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(site_id, key) DO UPDATE SET
                 canonical_id=excluded.canonical_id, raw=excluded.raw, normalized=excluded.normalized,
                 source_url=excluded.source_url,
                 trailer_url=COALESCE(excluded.trailer_url, source_items.trailer_url),
                 fetched_at=excluded.fetched_at""",
            (site_id, key, cid, json.dumps(raw, ensure_ascii=False, default=str), json.dumps(norm, ensure_ascii=False),
             norm["source_url"], norm.get("trailer_url"), now))
        merge_canonical(conn, cid)
        conn.execute("UPDATE video_sources SET canonical_id=? WHERE source=? AND source_key=? AND canonical_id!=?",
                     (cid, site_id, key, cid))
        for origin in origins or ():
            kind, value, position = (tuple(origin) + (0,))[:3]
            if not kind or value is None or value == "":
                continue
            conn.execute(
                """INSERT INTO source_origins (site_id, source_key, kind, value, position, seen_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(site_id, source_key, kind, value) DO UPDATE SET position=excluded.position,
                   seen_at=excluded.seen_at""",
                (site_id, key, str(kind), str(value), int(position or 0), now))
    if _tmdb_wanted(cid) and hooks.enqueue_tmdb is not None:
        try:
            hooks.enqueue_tmdb(cid)
        except Exception as exc:
            log.warning("tmdb job not queued for %s: %s", cid, type(exc).__name__)
    cache.refresh_soon()
    return cid


# --- video sources -----------------------------------------------------------------------------------------------

def _source_id(site_id: str, key: str, kind: str, season: Any, episode: Any, entry_key: str) -> str:
    identity_text = "|".join([site_id, key, kind, str(season), str(episode), entry_key])
    return "vs_" + hashlib.sha256(identity_text.encode()).hexdigest()[:24]


def _fresh_blocked(conn, site_id: str, now: Optional[float] = None) -> set[str]:
    cutoff = (time.time() if now is None else now) - BLOCKED_RECHECK_DAYS * 86400
    return {r[0] for r in conn.execute(
        "SELECT url FROM blocked_pages WHERE site=? AND status='blocked' AND kind IN ('episode','movie') AND checked_at>?",
        (site_id, cutoff))}


def _title_row(conn, site_id: str, title_key: str):
    row = conn.execute("SELECT canonical_id, normalized FROM source_items WHERE site_id=? AND key=?",
                       (site_id, title_key)).fetchone()
    if row is None or not row["canonical_id"]:
        raise LookupError(f"unknown title {site_id}/{title_key}: upsert_title first")
    try:
        norm = json.loads(row["normalized"] or "{}")
    except (TypeError, ValueError):
        norm = {}
    return row["canonical_id"], norm.get("type") or "movie"


_UPSERT_SOURCE = """INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,
    kind,locator,resolver,media_type,label,episode_title,episode_air_date,updated_at,alt_key,alt_index,alt_label,alt_host)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET canonical_id=excluded.canonical_id,label=excluded.label,
    alt_index=excluded.alt_index,alt_label=excluded.alt_label,alt_host=excluded.alt_host,
    episode_id=excluded.episode_id,
    episode_title=COALESCE(excluded.episode_title,episode_title),
    episode_air_date=COALESCE(excluded.episode_air_date,episode_air_date),
    updated_at=excluded.updated_at,
    resolved_payload=CASE WHEN locator!=excluded.locator THEN NULL ELSE resolved_payload END,
    resolved_at=CASE WHEN locator!=excluded.locator THEN NULL ELSE resolved_at END,
    trailer_dead=CASE WHEN locator!=excluded.locator THEN 0 ELSE trailer_dead END,
    locator=excluded.locator"""


def _alts(alternatives: Optional[Iterable[dict]]) -> list[dict]:
    """The site's source alternatives in A6 order (``[{key, label?, host?}]``); ``[{key: ''}]`` = one row for the whole page."""
    out = [a for a in alternatives or () if isinstance(a, dict) and isinstance(a.get("key"), str) and a["key"]]
    return out or [{"key": ""}]


def _write_alternatives(conn, base_id_parts: tuple, values: tuple, alternatives: list[dict], episode_id: str, kind: str,
                        site_id: str, title_key: str) -> int:
    """One row per alternative (same page, same episode); rows of alternatives that are gone (and the whole-page row once
    there are alternatives) are removed. Returns the rows written."""
    keys = []
    for index, alt in enumerate(alternatives):
        key = alt["key"]
        keys.append(key)
        sid = _source_id(*base_id_parts[:-1], base_id_parts[-1] + (f"|alt:{key}" if key else ""))
        conn.execute(_UPSERT_SOURCE, (sid, *values, key, index, (alt.get("label") or None) if key else None,
                                      (alt.get("host") or None) if key else None))
    marks = ",".join("?" * len(keys))
    conn.execute(f"DELETE FROM video_sources WHERE source=? AND source_key=? AND episode_id=? AND kind=? AND alt_key NOT IN ({marks})",
                 (site_id, title_key, episode_id, kind, *keys))
    return len(keys)


def upsert_episodes(site_id: str, title_key: str, episodes: Iterable[dict],
                    alternatives: Optional[Iterable[dict]] = None) -> int:
    """``[{season, episode, url, title?, air_date?}]`` -> ``video_sources`` rows (``kind=episode``, ``resolver=page``, locator =
    the episode page). ``alternatives`` (A6 order, ``pages.sources.alternatives``): one row PER source alternative of the
    page (backup sources, own health); without them one row for the whole page. The row id is stable per (site, title,
    season, episode, alternative): a new ``url`` replaces the locator and drops the stored resolution; health fields
    (status, failures, ...) are never touched. Pages with a fresh blocked verdict are not written. Returns the rows
    written."""
    alts = _alts(alternatives)
    written = 0
    now = int(time.time())
    with db.transaction() as conn:
        cid, kind = _title_row(conn, site_id, title_key)
        if kind != "series":
            raise ValueError(f"{site_id}/{title_key} is not a series")
        blocked = _fresh_blocked(conn, site_id)
        for ep in episodes or ():
            if not isinstance(ep, dict):
                continue
            season, number, url = ep.get("season"), ep.get("episode"), _http_url(ep.get("url"))
            if not url or url in blocked:
                continue
            if not isinstance(season, int) or not isinstance(number, int) or isinstance(season, bool) \
                    or season < 0 or number < 1:
                continue
            episode_id = f"{cid}:s{season}:e{number}"
            values = (cid, site_id, title_key, episode_id, season, number, "episode", url, "page",
                      "hls" if ".m3u8" in url else "mp4", site_id, (ep.get("title") or None), (ep.get("air_date") or None), now)
            written += _write_alternatives(conn, (site_id, title_key, "episode", season, number, f"ep:{season}:{number}"),
                                           values, alts, episode_id, "episode", site_id, title_key)
    if written:
        cache.refresh_soon()
    return written


def set_movie_source(site_id: str, title_key: str, url: str, alternatives: Optional[Iterable[dict]] = None) -> None:
    """The film page of a movie title as its playback source (``kind=movie``, ``resolver=page``). Same id rule as episodes:
    a new ``url`` replaces the locator and drops the stored resolution. A fresh blocked page is not written."""
    url = _http_url(url)
    if not url:
        raise ValueError("url must be an http(s) URL")
    now = int(time.time())
    with db.transaction() as conn:
        cid, kind = _title_row(conn, site_id, title_key)
        if kind != "movie":
            raise ValueError(f"{site_id}/{title_key} is not a movie")
        if url in _fresh_blocked(conn, site_id):
            return
        values = (cid, site_id, title_key, "", None, None, "movie", url, "page", "hls" if ".m3u8" in url else "mp4", site_id,
                  None, None, now)
        _write_alternatives(conn, (site_id, title_key, "movie", None, None, "movie"), values, _alts(alternatives), "", "movie",
                            site_id, title_key)
    cache.refresh_soon()


# --- not public (blocked_pages) ------------------------------------------------------------------------------------

def mark_blocked(site_id: str, url: str, *, kind: str, reason: str, via: str, status: str = "blocked",
                 key: str = "") -> None:
    """Store the verdict "this page / series is not public" (``status`` blocked | retry; ``via`` rule | gate). A blocked episode /
    film page that is already a source turns ``status='blocked'`` (health untouched) and is not written again while the verdict
    is fresh. K5 for clients: a ``blocked`` source is not ``unknown|healthy``, so the episode leaves the client's season list
    (``cache.client_seasons``) and the title leaves client lists once it has no other such source; the admin still sees it."""
    if kind not in BLOCKED_KINDS:
        raise ValueError(f"kind must be one of {BLOCKED_KINDS}")
    if status not in ("blocked", "retry"):
        raise ValueError("status must be blocked or retry")
    now = int(time.time())
    with db.transaction() as conn:
        if not key:
            row = conn.execute("SELECT source_key FROM video_sources WHERE source=? AND locator=? LIMIT 1", (site_id, url)).fetchone() \
                or conn.execute("SELECT key AS source_key FROM source_items WHERE site_id=? AND source_url=? LIMIT 1",
                                (site_id, url)).fetchone()
            key = row["source_key"] if row else ""
        conn.execute(
            "INSERT INTO blocked_pages(site,url,kind,source_key,status,via,reason,checked_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site,url) DO UPDATE SET kind=excluded.kind,source_key=excluded.source_key,status=excluded.status,"
            "via=excluded.via,reason=excluded.reason,checked_at=excluded.checked_at",
            (site_id, url, kind, key, status, via or "", (reason or "")[:200], now))
        if status == "blocked" and kind in ("episode", "movie"):
            conn.execute("UPDATE video_sources SET status='blocked', updated_at=? WHERE source=? AND locator=?", (now, site_id, url))
        if status == "blocked" and kind in ("series", "movie") and key:
            # the whole title is not public: every source row it already has goes (K5), not only the page's own
            conn.execute("UPDATE video_sources SET status='blocked', updated_at=? WHERE source=? AND source_key=?",
                         (now, site_id, key))
    cache.refresh_soon()


def unmark_blocked(site_id: str, url: str) -> None:
    """The page is public again: its verdict goes and a source that was blocked by it is ``unknown`` again."""
    now = int(time.time())
    with db.transaction() as conn:
        row = conn.execute("SELECT kind, source_key FROM blocked_pages WHERE site=? AND url=?", (site_id, url)).fetchone()
        conn.execute("DELETE FROM blocked_pages WHERE site=? AND url=?", (site_id, url))
        conn.execute("UPDATE video_sources SET status='unknown', updated_at=? WHERE source=? AND locator=? AND status='blocked'",
                     (now, site_id, url))
        if row and row["kind"] in ("series", "movie") and row["source_key"]:
            # rows of the title that are not blocked by their own page verdict come back
            conn.execute("UPDATE video_sources SET status='unknown', updated_at=? WHERE source=? AND source_key=? "
                         "AND status='blocked' AND locator NOT IN (SELECT url FROM blocked_pages WHERE site=? "
                         "AND status='blocked')", (now, site_id, row["source_key"], site_id))
    cache.refresh_soon()


# --- removal -------------------------------------------------------------------------------------------------------

def _purge(conn, where_vs: str, where_si: str, params: tuple, out: dict) -> None:
    """Delete the selected source rows; orphaned canonical titles go with their seasons / ids / lists, shared ones are re-merged.
    User data (progress, mylist, continue_hidden) is never touched. Runs inside the caller's transaction."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _cand (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _vs (id TEXT PRIMARY KEY)")
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _orph (id TEXT PRIMARY KEY)")
    try:
        conn.execute(f"INSERT OR IGNORE INTO _cand SELECT canonical_id FROM source_items WHERE {where_si} AND canonical_id IS NOT NULL", params)
        conn.execute(f"INSERT OR IGNORE INTO _cand SELECT canonical_id FROM video_sources WHERE {where_vs}", params)
        conn.execute(f"INSERT OR IGNORE INTO _vs SELECT id FROM video_sources WHERE {where_vs}", params)
        out["playback_attempts"] += conn.execute("DELETE FROM playback_attempts WHERE source_id IN (SELECT id FROM _vs)").rowcount
        out["video_sources"] += conn.execute(f"DELETE FROM video_sources WHERE {where_vs}", params).rowcount
        out["source_items"] += conn.execute(f"DELETE FROM source_items WHERE {where_si}", params).rowcount
        conn.execute("""INSERT INTO _orph SELECT c.id FROM _cand c
                        WHERE NOT EXISTS (SELECT 1 FROM source_items s WHERE s.canonical_id=c.id)
                          AND NOT EXISTS (SELECT 1 FROM video_sources v WHERE v.canonical_id=c.id)""")
        for table in ("library_seasons", "library_episodes", "external_ids", "field_provenance", "catalogue_aliases"):
            conn.execute(f"DELETE FROM {table} WHERE canonical_id IN (SELECT id FROM _orph)")
        out["library_lists"] += conn.execute("DELETE FROM library_lists WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
        out["identity_reviews"] += conn.execute("DELETE FROM identity_reviews WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
        out["notifications"] += conn.execute("DELETE FROM notifications WHERE canonical_id IN (SELECT id FROM _orph)").rowcount
        out["library_items"] += conn.execute("DELETE FROM library_items WHERE id IN (SELECT id FROM _orph)").rowcount
        kept = [r[0] for r in conn.execute("SELECT id FROM _cand WHERE id NOT IN (SELECT id FROM _orph)").fetchall()]
        for cid in kept:   # shared titles: fields come from the remaining sources only
            merge_canonical(conn, cid)
        out["shared_kept"] += len(kept)
    finally:
        for temp in ("_cand", "_vs", "_orph"):
            conn.execute(f"DROP TABLE IF EXISTS {temp}")


_COUNTERS = ("source_items", "video_sources", "library_lists", "library_items", "notifications", "playback_attempts",
             "identity_reviews", "shared_kept", "blocked_pages", "source_origins")


def remove_title(site_id: str, key: str) -> dict:
    """Take one source title of ``site_id`` out of the catalogue (its sources, origins, identity review). The canonical title
    goes only when no other site serves it; user data stays. Returns counters."""
    out = {k: 0 for k in _COUNTERS}
    with db.transaction() as conn:
        _purge(conn, "source=? AND source_key=?", "site_id=? AND key=?", (site_id, key), out)
        out["identity_reviews"] += conn.execute("DELETE FROM identity_reviews WHERE source=? AND source_key=?", (site_id, key)).rowcount
        out["source_origins"] += conn.execute("DELETE FROM source_origins WHERE site_id=? AND source_key=?", (site_id, key)).rowcount
    cache.refresh_soon()
    return out


def delete_site(site_id: str) -> dict:
    """Every catalogue row ``site_id`` fed (sources, origins, verdicts, its lists, identity reviews); shared titles are
    re-merged, user data stays. Returns counters (``kept_user_data``: user rows of titles that left)."""
    from . import lists
    out = {k: 0 for k in _COUNTERS}
    site_lists = lists.site_list_ids(site_id)
    with db.transaction() as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("progress", "mylist", "continue_hidden")}
        _purge(conn, "source=?", "site_id=?", (site_id,), out)
        out["identity_reviews"] += conn.execute("DELETE FROM identity_reviews WHERE source=?", (site_id,)).rowcount
        out["blocked_pages"] += conn.execute("DELETE FROM blocked_pages WHERE site=?", (site_id,)).rowcount
        out["source_origins"] += conn.execute("DELETE FROM source_origins WHERE site_id=?", (site_id,)).rowcount
        for lid in site_lists:
            out["library_lists"] += conn.execute("DELETE FROM library_lists WHERE list_id=?", (lid,)).rowcount
        out["kept_user_data"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    cache.refresh_soon()
    return out
