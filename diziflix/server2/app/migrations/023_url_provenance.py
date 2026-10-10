"""T30: a frontier row records WHERE its link was found (``origin_role``: ``title_row`` = a ``content: title`` listing /
section row, ``episode_row`` = a home episode card / a title page's episode list, ``season_link``, ``title_link`` = an
episode page's link to its title, ``next_page``, ``seed``, ``unknown``) and the title key the crawl gave it
(``item_key``, may be a fallback key when the map's key regex does not fit the address)."""

VERSION = 23


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(crawl_frontier)")}
    if "origin_role" not in cols:
        conn.execute("ALTER TABLE crawl_frontier ADD COLUMN origin_role TEXT NOT NULL DEFAULT ''")
    if "item_key" not in cols:
        conn.execute("ALTER TABLE crawl_frontier ADD COLUMN item_key TEXT")
