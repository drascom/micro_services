"""SQLite storage: profiles, watch progress, my-list and hidden continue-watching entries."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable, Optional

from . import config

_lock = threading.Lock()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        is_kids INTEGER NOT NULL DEFAULT 0,
        avatar_seed TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS progress (
        profile_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        episode_id TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        duration INTEGER NOT NULL DEFAULT 0,
        watched INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (profile_id, episode_id)
    )""",
    """CREATE TABLE IF NOT EXISTS mylist (
        profile_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        added_at INTEGER NOT NULL,
        PRIMARY KEY (profile_id, item_id)
    )""",
    # soft "remove from Continue Watching": progress rows stay; an item is hidden from the `continue` row while
    # hidden_at >= its latest progress.updated_at (new progress brings it back by itself)
    """CREATE TABLE IF NOT EXISTS continue_hidden (
        profile_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        hidden_at INTEGER NOT NULL,
        PRIMARY KEY (profile_id, item_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_progress_profile ON progress(profile_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_progress_item ON progress(profile_id, item_id)",
    "CREATE INDEX IF NOT EXISTS idx_mylist_profile ON mylist(profile_id, added_at DESC)",
    # --- canonical, source-agnostic film library ---------------------------
    """CREATE TABLE IF NOT EXISTS library_items (
        id TEXT PRIMARY KEY,
        tmdb_id INTEGER,
        type TEXT NOT NULL DEFAULT 'movie',
        title TEXT NOT NULL,
        original_title TEXT,
        year INTEGER,
        overview TEXT,
        genres TEXT,
        rating REAL,
        runtime INTEGER,
        country TEXT,
        followers INTEGER,
        cast TEXT,
        poster_url TEXT,
        backdrop_url TEXT,
        popularity REAL DEFAULT 0,
        added_at INTEGER DEFAULT 0,
        updated_at INTEGER NOT NULL,
        imdb_id TEXT,
        tmdb_poster_url TEXT,
        tmdb_backdrop_url TEXT,
        tmdb_enrich_status TEXT,
        tmdb_checked_at INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS source_items (
        source TEXT NOT NULL,
        source_key TEXT NOT NULL,
        canonical_id TEXT,
        raw TEXT,
        normalized TEXT,
        source_url TEXT,
        trailer_url TEXT,
        resolved_url TEXT,
        resolved_quality TEXT,
        resolved_duration INTEGER,
        resolved_streams TEXT,
        resolved_at INTEGER,
        fetched_at INTEGER NOT NULL,
        PRIMARY KEY (source, source_key)
    )""",
    """CREATE TABLE IF NOT EXISTS field_provenance (
        canonical_id TEXT NOT NULL,
        field TEXT NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (canonical_id, field)
    )""",
    # Home-screen list membership (real sinemalar collections: new / upcoming /
    # per-genre). ``position`` preserves the source page order. One film can
    # belong to many lists (e.g. several genres); genre rows are driven by this
    # table, not by card-parsed genre labels.
    """CREATE TABLE IF NOT EXISTS library_lists (
        list_id TEXT NOT NULL,
        canonical_id TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (list_id, canonical_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_source_canonical ON source_items(canonical_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_tmdb ON library_items(tmdb_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_lists_list ON library_lists(list_id, position)",
]


SCHEMA += [
    # Admin-managed home-screen categories (app/library/categories.py). Membership lives in library_lists
    # (``category_<slug>_<site>``), so deleting a row here keeps the titles' membership.
    """CREATE TABLE IF NOT EXISTS home_categories (
        slug TEXT PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, min_items INTEGER NOT NULL DEFAULT 6,
        created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0)""",
]


SCHEMA += [
    "CREATE TABLE IF NOT EXISTS catalogue_aliases (alias TEXT PRIMARY KEY, canonical_id TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS external_ids (
        provider TEXT NOT NULL, media_type TEXT NOT NULL, external_id TEXT NOT NULL,
        canonical_id TEXT NOT NULL, PRIMARY KEY(provider, media_type, external_id),
        UNIQUE(canonical_id, provider))""",
    """CREATE TABLE IF NOT EXISTS identity_reviews (
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_key TEXT NOT NULL,
        canonical_id TEXT, reason TEXT NOT NULL, candidates TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'pending', updated_at INTEGER NOT NULL,
        UNIQUE(source, source_key))""",
    """CREATE TABLE IF NOT EXISTS video_sources (
        id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, source TEXT NOT NULL,
        source_key TEXT NOT NULL, episode_id TEXT NOT NULL DEFAULT '',
        season INTEGER, episode INTEGER, kind TEXT NOT NULL,
        locator TEXT NOT NULL, resolver TEXT NOT NULL DEFAULT 'page',
        media_type TEXT NOT NULL DEFAULT 'mp4', label TEXT, language TEXT,
        episode_title TEXT, episode_overview TEXT, episode_still_url TEXT,
        episode_runtime INTEGER, episode_air_date TEXT,
        status TEXT NOT NULL DEFAULT 'unknown', failures INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, last_checked_at INTEGER, last_success_at INTEGER,
        resolved_payload TEXT, resolved_at INTEGER, updated_at INTEGER NOT NULL,
        trailer_dead INTEGER NOT NULL DEFAULT 0, trailer_checked_at INTEGER,
        proxy_required INTEGER NOT NULL DEFAULT 0, last_diag TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_video_item ON video_sources(canonical_id, episode_id, kind)",
    """CREATE TABLE IF NOT EXISTS playback_attempts (
        token TEXT PRIMARY KEY, source_id TEXT NOT NULL, created_at INTEGER NOT NULL,
        success_at INTEGER, failure_at INTEGER, error_code TEXT, engine TEXT,
        failure_counted INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_attempt_created ON playback_attempts(created_at)",
    # admin Kütüphane K/T toplaması (canonical_id, status, kind) kapsayıcı indeksle tablo okumadan yapılır
    "CREATE INDEX IF NOT EXISTS idx_video_health ON video_sources(canonical_id, status, kind)",
    # trailer liveness verdicts per YouTube video id (library/trailer_check.py). state: 'ok' | 'dead' (transient errors
    # are never stored). video_sources.trailer_dead mirrors the verdict for the trailer rows (admin badge, catalogue
    # availability); it is NOT a playback failure: status / failures / the admin K/T count stay untouched.
    """CREATE TABLE IF NOT EXISTS trailer_checks (
        video_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT, checked_at INTEGER NOT NULL)""",
    # TMDB season / episode metadata (library/seasons.py). Separate from video_sources on purpose: a
    # provider row is per site and per video, TMDB data is per (series, season, episode) and must not be
    # duplicated (or lost when a source disappears); the source columns stay untouched and API/catalogue
    # merge them at read time (source value wins when non-empty, artwork: TMDB first, source fallback).
    # ``status``: 'ok' (TMDB answered) | 'empty' (TMDB has nothing: 404 / no episodes) - both stamped with
    # ``checked_at`` so an empty season is not asked again before TMDB_RETRY_DAYS.
    """CREATE TABLE IF NOT EXISTS library_seasons (
        canonical_id TEXT NOT NULL, season INTEGER NOT NULL,
        name TEXT, overview TEXT, air_date TEXT, tmdb_poster_url TEXT,
        status TEXT, checked_at INTEGER,
        PRIMARY KEY (canonical_id, season))""",
    """CREATE TABLE IF NOT EXISTS library_episodes (
        canonical_id TEXT NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL,
        title TEXT, overview TEXT, air_date TEXT, runtime_minutes INTEGER, tmdb_still_url TEXT,
        checked_at INTEGER,
        PRIMARY KEY (canonical_id, season, episode))""",
]


SCHEMA += [
    # Source finder (library/sourcefinder.py): one row per background job that looked for a playable source of a title /
    # episode (``episode_id`` '' = film). ``state``: searching | found | not_found; ``trigger``: play; ``steps`` = JSON list
    # of {name, ok, ms, note} (retry | search | heal); ``method`` / ``source_id`` of the source that was found.
    """CREATE TABLE IF NOT EXISTS finder_jobs (
        id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL, episode_id TEXT NOT NULL DEFAULT '',
        profile_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT '',
        started_at INTEGER NOT NULL, finished_at INTEGER, steps TEXT NOT NULL DEFAULT '[]',
        source_id TEXT, method TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_finder_item ON finder_jobs(canonical_id, episode_id, id DESC)",
    # Per-profile notifications (GET /api/notifications?since=<id>): ``kind`` source_found, ``payload`` = JSON with the
    # display fields (title, season, episode, site, method), ``read_at`` set by POST /api/notifications/read.
    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
        canonical_id TEXT, episode_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, read_at INTEGER)""",
    "CREATE INDEX IF NOT EXISTS idx_notif_profile ON notifications(profile_id, id)",
    # Content that is not public (yaml ``blocked:`` rules / ``availability_gate:``; library/gate.py): one row per page (or
    # series) whose verdict is stored. ``kind``: episode | movie | series (a series row has the series page as ``url`` and its
    # ``source_key``: ingest does not write a series with a fresh blocked row); ``status``: blocked | retry (a transient
    # error: judged again by the next scan, never counted as "no player"); ``via``: rule (a ``blocked:`` rule matched) |
    # gate (``availability_gate``: no player on the page); ``reason`` = the rule's reason / the gate's finding. A blocked
    # row is judged again after BLOCKED_RECHECK_DAYS (``checked_at``).
    """CREATE TABLE IF NOT EXISTS blocked_pages (
        site TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'episode',
        source_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, via TEXT NOT NULL DEFAULT '',
        reason TEXT, checked_at INTEGER NOT NULL, PRIMARY KEY (site, url))""",
    "CREATE INDEX IF NOT EXISTS idx_blocked_key ON blocked_pages(site, source_key, kind)",
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_local = threading.local()


def _file_key(path: str) -> tuple:
    try:
        return (path, os.stat(path).st_ino)
    except OSError:
        return (path, None)


def _close_quiet(conn: Optional[sqlite3.Connection]) -> None:
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover
            pass


def _shared() -> sqlite3.Connection:
    """Per-thread persistent connection, re-opened when DB_PATH (or the file
    behind it) changes, e.g. a test pointing config.DB_PATH at a temp DB."""
    key = _file_key(config.DB_PATH)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "key", None) != key:
        _close_quiet(conn)
        conn = connect()  # creates the file if missing
        _local.conn = conn
        _local.key = _file_key(config.DB_PATH)
    return conn


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    conn = _shared()
    try:
        return list(conn.execute(sql, tuple(params)).fetchall())
    except sqlite3.ProgrammingError:
        _close_quiet(conn)
        _local.conn = None
        raise


@contextlib.contextmanager
def read_transaction():
    """One consistent read view for the ``db.query`` calls made inside the block (this thread's connection).

    WAL: the view is fixed at the first SELECT and never blocks a writer (an ingest commit in the middle of a
    multi-query catalogue load is simply not seen), it is dropped again on exit. Nested / already inside a
    transaction: joins it. Read-only use: writes on this connection inside the block are not supported."""
    conn = _shared()
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if began:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover
                pass


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with _lock:
        conn = _shared()
        try:
            conn.execute(sql, tuple(params))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:  # pragma: no cover
                pass
            raise


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by earlier versions."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(progress)")}
    if cols and "watched" not in cols:
        conn.execute("ALTER TABLE progress ADD COLUMN watched INTEGER NOT NULL DEFAULT 0")
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(source_items)")}
    if scols:
        for col, decl in (
            ("resolved_url", "TEXT"),
            ("resolved_quality", "TEXT"),
            ("resolved_duration", "INTEGER"),
            ("resolved_streams", "TEXT"),
            ("resolved_at", "INTEGER"),
        ):
            if col not in scols:
                conn.execute(f"ALTER TABLE source_items ADD COLUMN {col} {decl}")
    lcols = {r["name"] for r in conn.execute("PRAGMA table_info(library_items)")}
    if "imdb_id" not in lcols:
        conn.execute("ALTER TABLE library_items ADD COLUMN imdb_id TEXT")
    for col, decl in (("country", "TEXT"), ("followers", "INTEGER"), ("cast", "TEXT"),
                      ("tmdb_poster_url", "TEXT"), ("tmdb_backdrop_url", "TEXT"),
                      ("tmdb_enrich_status", "TEXT"), ("tmdb_checked_at", "INTEGER")):
        if col not in lcols:
            conn.execute(f"ALTER TABLE library_items ADD COLUMN {col} {decl}")
    for row in conn.execute("SELECT id, type, tmdb_id, imdb_id FROM library_items"):
        for provider in ("tmdb", "imdb"):
            value = row[provider + "_id"]
            if value:
                conn.execute("INSERT OR IGNORE INTO external_ids VALUES (?,?,?,?)",
                             (provider, row["type"], str(value), row["id"]))
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
    if pcols and "avatar_seed" not in pcols:
        conn.execute("ALTER TABLE profiles ADD COLUMN avatar_seed TEXT NOT NULL DEFAULT 'a'")
    # Legacy profiles stored the profile id as the seed (e.g. p1/p2); those are not
    # catalogue seeds. Map the two seeded defaults onto valid catalogue entries so the
    # edit screen can highlight the current selection. Custom seeds are left untouched.
    if pcols:
        conn.execute("UPDATE profiles SET avatar_seed = 'a1' WHERE id = 'p1' AND avatar_seed = 'p1'")
        conn.execute("UPDATE profiles SET avatar_seed = 'a2' WHERE id = 'p2' AND avatar_seed = 'p2'")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(video_sources)")}
    for col, decl in (
        ("episode_title", "TEXT"), ("episode_overview", "TEXT"),
        ("episode_still_url", "TEXT"), ("episode_runtime", "INTEGER"),
        ("episode_air_date", "TEXT"),
        ("trailer_dead", "INTEGER NOT NULL DEFAULT 0"), ("trailer_checked_at", "INTEGER"),
        # stream proxy (app/streamproxy.py): the source's streams were learned to need the server's proxy; last_diag = the
        # verdict of the last playback-failure probe (library/streamdiag.py, JSON <= 600 bytes)
        ("proxy_required", "INTEGER NOT NULL DEFAULT 0"), ("last_diag", "TEXT"),
    ):
        if vcols and col not in vcols:
            conn.execute(f"ALTER TABLE video_sources ADD COLUMN {col} {decl}")
    # failure_counted: this attempt's failure was added to video_sources.failures (videos.feedback can
    # take it back when the same attempt later reports success). Attempts still open at upgrade time:
    # a failure reported without a success is assumed counted (device-side codes are never counted).
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(playback_attempts)")}
    if acols and "failure_counted" not in acols:
        conn.execute("ALTER TABLE playback_attempts ADD COLUMN failure_counted INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE playback_attempts SET failure_counted=1 WHERE failure_at IS NOT NULL "
                     "AND success_at IS NULL AND COALESCE(error_code,'') NOT IN "
                     "('unsupported','decode','autoplay','aborted','offline')")


def init() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    with _lock:
        conn = connect()
        try:
            for stmt in SCHEMA:
                conn.execute(stmt)
            _migrate(conn)
            conn.commit()
            seeded = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
            if seeded == 0:
                now = int(time.time())
                conn.executemany(
                    "INSERT INTO profiles (id,name,is_kids,avatar_seed,created_at) VALUES (?,?,?,?,?)",
                    [
                        ("p1", "İsmet", 0, "a1", now),
                        ("p2", "Çocuk", 1, "a2", now),
                    ],
                )
                conn.commit()
        finally:
            conn.close()


def new_profile_id() -> str:
    return "p_" + uuid.uuid4().hex[:10]
