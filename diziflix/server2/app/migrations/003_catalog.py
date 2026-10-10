"""Catalogue tables (T04a, README §5.1): copied from the old ``SCHEMA`` (+ every additive ``_migrate`` column) minus
``finder_jobs``, ``playback_issues`` and the ``source_items.resolved_*`` columns; plus ``source_origins``.

Column names follow the old schema (``video_sources.source``, ``identity_reviews.source``, ``blocked_pages.site``) except
``source_items``, keyed ``(site_id, key)``. State markers that used to live inside ``source_items.normalized``
(``_series_inventory``, ``_gate``, ...) do not exist here: crawl state is the frontier's (T11)."""

VERSION = 3

_TABLES = [
    """CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, is_kids INTEGER NOT NULL DEFAULT 0,
        avatar_seed TEXT NOT NULL DEFAULT 'a', created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS progress (
        profile_id TEXT NOT NULL, item_id TEXT NOT NULL, episode_id TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0, duration INTEGER NOT NULL DEFAULT 0, watched INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL, PRIMARY KEY (profile_id, episode_id))""",
    """CREATE TABLE IF NOT EXISTS mylist (
        profile_id TEXT NOT NULL, item_id TEXT NOT NULL, added_at INTEGER NOT NULL, PRIMARY KEY (profile_id, item_id))""",
    """CREATE TABLE IF NOT EXISTS continue_hidden (
        profile_id TEXT NOT NULL, item_id TEXT NOT NULL, hidden_at INTEGER NOT NULL, PRIMARY KEY (profile_id, item_id))""",
    "CREATE INDEX IF NOT EXISTS idx_progress_profile ON progress(profile_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_progress_item ON progress(profile_id, item_id)",
    "CREATE INDEX IF NOT EXISTS idx_mylist_profile ON mylist(profile_id, added_at DESC)",
    """CREATE TABLE IF NOT EXISTS library_items (
        id TEXT PRIMARY KEY, tmdb_id INTEGER, type TEXT NOT NULL DEFAULT 'movie', title TEXT NOT NULL,
        original_title TEXT, year INTEGER, overview TEXT, genres TEXT, rating REAL, runtime INTEGER, country TEXT,
        followers INTEGER, cast TEXT, poster_url TEXT, backdrop_url TEXT, popularity REAL DEFAULT 0,
        added_at INTEGER DEFAULT 0, updated_at INTEGER NOT NULL, imdb_id TEXT, tmdb_poster_url TEXT,
        tmdb_backdrop_url TEXT, tmdb_enrich_status TEXT, tmdb_checked_at INTEGER)""",
    """CREATE TABLE IF NOT EXISTS source_items (
        site_id TEXT NOT NULL, key TEXT NOT NULL, canonical_id TEXT, raw TEXT, normalized TEXT,
        source_url TEXT, trailer_url TEXT, fetched_at INTEGER NOT NULL, PRIMARY KEY (site_id, key))""",
    """CREATE TABLE IF NOT EXISTS field_provenance (
        canonical_id TEXT NOT NULL, field TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (canonical_id, field))""",
    """CREATE TABLE IF NOT EXISTS library_lists (
        list_id TEXT NOT NULL, canonical_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (list_id, canonical_id))""",
    "CREATE INDEX IF NOT EXISTS idx_source_canonical ON source_items(canonical_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_tmdb ON library_items(tmdb_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_lists_list ON library_lists(list_id, position)",
    """CREATE TABLE IF NOT EXISTS home_categories (
        slug TEXT PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1, min_items INTEGER NOT NULL DEFAULT 6,
        created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0,
        kind TEXT NOT NULL DEFAULT 'category')""",
    "CREATE TABLE IF NOT EXISTS catalogue_aliases (alias TEXT PRIMARY KEY, canonical_id TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS external_ids (
        provider TEXT NOT NULL, media_type TEXT NOT NULL, external_id TEXT NOT NULL,
        canonical_id TEXT NOT NULL, PRIMARY KEY(provider, media_type, external_id), UNIQUE(canonical_id, provider))""",
    """CREATE TABLE IF NOT EXISTS identity_reviews (
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_key TEXT NOT NULL,
        canonical_id TEXT, reason TEXT NOT NULL, candidates TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'pending', updated_at INTEGER NOT NULL, UNIQUE(source, source_key))""",
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
        proxy_required INTEGER NOT NULL DEFAULT 0, last_diag TEXT, proxy_headers TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_video_item ON video_sources(canonical_id, episode_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_video_health ON video_sources(canonical_id, status, kind)",
    "CREATE INDEX IF NOT EXISTS idx_video_source ON video_sources(source, source_key)",
    """CREATE TABLE IF NOT EXISTS playback_attempts (
        token TEXT PRIMARY KEY, source_id TEXT NOT NULL, created_at INTEGER NOT NULL,
        success_at INTEGER, failure_at INTEGER, error_code TEXT, engine TEXT,
        failure_counted INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS idx_attempt_created ON playback_attempts(created_at)",
    """CREATE TABLE IF NOT EXISTS trailer_checks (
        video_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT, checked_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS library_seasons (
        canonical_id TEXT NOT NULL, season INTEGER NOT NULL, name TEXT, overview TEXT, air_date TEXT,
        tmdb_poster_url TEXT, status TEXT, checked_at INTEGER, PRIMARY KEY (canonical_id, season))""",
    """CREATE TABLE IF NOT EXISTS library_episodes (
        canonical_id TEXT NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL,
        title TEXT, overview TEXT, air_date TEXT, runtime_minutes INTEGER, tmdb_still_url TEXT,
        checked_at INTEGER, PRIMARY KEY (canonical_id, season, episode))""",
    """CREATE TABLE IF NOT EXISTS stream_host_rules (
        host TEXT PRIMARY KEY, proxy_required INTEGER NOT NULL DEFAULT 1, headers TEXT NOT NULL DEFAULT '{}',
        reason TEXT NOT NULL DEFAULT '', learned_from_source TEXT NOT NULL DEFAULT '', learned_at INTEGER NOT NULL,
        last_ok_at INTEGER, fail_count INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL,
        suspended INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
        canonical_id TEXT, episode_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, read_at INTEGER)""",
    "CREATE INDEX IF NOT EXISTS idx_notif_profile ON notifications(profile_id, id)",
    """CREATE TABLE IF NOT EXISTS blocked_pages (
        site TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'episode',
        source_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, via TEXT NOT NULL DEFAULT '',
        reason TEXT, checked_at INTEGER NOT NULL, PRIMARY KEY (site, url))""",
    "CREATE INDEX IF NOT EXISTS idx_blocked_key ON blocked_pages(site, source_key, kind)",
    # Where a title was seen on its site (home section, listing, genre, type page): routing (T12) derives library_lists
    # from it. Rows are never deleted by a later upsert that did not see them again (``seen_at`` tells their age).
    """CREATE TABLE IF NOT EXISTS source_origins (
        site_id TEXT NOT NULL, source_key TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0, seen_at INTEGER NOT NULL,
        PRIMARY KEY (site_id, source_key, kind, value))""",
    "CREATE INDEX IF NOT EXISTS idx_origins_value ON source_origins(site_id, kind, value)",
]


def up(conn):
    for stmt in _TABLES:
        conn.execute(stmt)
