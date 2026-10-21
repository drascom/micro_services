"""T01: sample page store of core.fetch (body files under DATA_DIR/pages/<site>/<sha1>.html)."""

VERSION = 2


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sample_pages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " site_id TEXT NOT NULL,"
        " page_type TEXT NOT NULL DEFAULT 'page',"
        " url TEXT NOT NULL,"
        " final_url TEXT NOT NULL DEFAULT '',"
        " status INTEGER NOT NULL DEFAULT 200,"
        " engine TEXT NOT NULL DEFAULT 'http',"
        " sha1 TEXT NOT NULL,"
        " bytes INTEGER NOT NULL DEFAULT 0,"
        " ms INTEGER NOT NULL DEFAULT 0,"
        " fetched_at REAL NOT NULL,"
        " UNIQUE(site_id, url))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sample_pages_type ON sample_pages(site_id, page_type, fetched_at)")
