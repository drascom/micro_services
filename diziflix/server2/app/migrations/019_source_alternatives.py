"""Every source alternative of an episode / film page is its own ``video_sources`` row (backup sources, per-alternative health):
``alt_key`` (``core.play.candidates.alt_key``; '' = the whole page, every alternative), ``alt_index`` (A6 order), ``alt_label``,
``alt_host``."""

VERSION = 19


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_sources)")}
    for name, kind in (("alt_key", "TEXT NOT NULL DEFAULT ''"), ("alt_index", "INTEGER NOT NULL DEFAULT 0"),
                       ("alt_label", "TEXT"), ("alt_host", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE video_sources ADD COLUMN {name} {kind}")
