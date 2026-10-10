"""Tiered crawl (T11, README §5.1 ``crawl_frontier``): the per-site page queue, the origins a page was reached from, and
the small per-site crawl state (quota day, gate budget, stopped page types, block hold, admin pause)."""

VERSION = 14


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS crawl_frontier ("
        " site_id TEXT NOT NULL, url TEXT NOT NULL, page_type TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'new',"
        " priority INTEGER NOT NULL DEFAULT 10, parent_url TEXT, origin_kind TEXT, origin_value TEXT,"
        " position INTEGER NOT NULL DEFAULT 0, discovered_at INTEGER NOT NULL, crawled_at INTEGER,"
        " next_at INTEGER NOT NULL DEFAULT 0, fail_count INTEGER NOT NULL DEFAULT 0,"
        " mark TEXT NOT NULL DEFAULT '', error TEXT, PRIMARY KEY(site_id, url))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS crawl_frontier_take ON crawl_frontier(site_id, state, priority, next_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS crawl_origins ("
        " site_id TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,"
        " position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(site_id, url, kind, value))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS crawl_sites (site_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER)")
