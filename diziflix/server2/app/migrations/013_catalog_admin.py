"""Admin catalogue (T16): titles the admin took off the list ("Liste dışı bırak"). The record stays (a later crawl that sees
the title again does not bring it back: the row is keyed by canonical id); clients never see it (``cache.Snapshot.visible``)
and the admin Katalog hides it unless asked for (``status=unlisted``)."""

VERSION = 13


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS catalog_unlisted ("
                 " canonical_id TEXT PRIMARY KEY, at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '')")
