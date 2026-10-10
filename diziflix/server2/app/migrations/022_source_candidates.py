"""T28: the crawl stores each source alternative's PERSISTENT candidate address (provider / embed page, same-site
intermediate page; never the signed final stream): ``candidate_url`` (the first one), ``candidate_json``
(``[{url, referer, needs_referer, kind, label, host}]``), ``candidate_at`` (when the crawl / a playback last saw it)."""

VERSION = 22


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_sources)")}
    for name, kind in (("candidate_url", "TEXT"), ("candidate_json", "TEXT"), ("candidate_at", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE video_sources ADD COLUMN {name} {kind}")
