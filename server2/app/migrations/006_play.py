"""Playback (T03): first-sight probe marker of stream hosts (README T03 item 10).

``stream_host_seen``: a stream host the server has already probed once on its first successful resolution (one probe per
host; a fix it found is written to ``stream_host_rules`` by ``hostrules.learn``)."""

VERSION = 6


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stream_host_seen ("
        " host TEXT PRIMARY KEY, first_at INTEGER NOT NULL, checked_at INTEGER, code TEXT,"
        " learned INTEGER NOT NULL DEFAULT 0, source_id TEXT NOT NULL DEFAULT '')"
    )
