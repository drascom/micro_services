"""Site maps (T07, README §5.1/§5.5): one row per site + its committed map versions. The map itself lives in the DB
(``sites.draft_yaml`` = working copy, ``site_versions.yaml`` = history); nothing is written to ``configs/``."""

VERSION = 7


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sites ("
        " site_id TEXT PRIMARY KEY, display_name TEXT, base_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',"
        " active_version INTEGER, draft_yaml TEXT, backfill_days INTEGER, refresh_hour INTEGER,"
        " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS site_versions ("
        " site_id TEXT NOT NULL, version INTEGER NOT NULL, yaml TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " created_by TEXT NOT NULL, note TEXT, PRIMARY KEY(site_id, version))"
    )
