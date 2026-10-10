"""T14: ``problem_signals`` = small on-disk counters / markers of the problem core (a broken image per title for the site-wide
image threshold, the capability catalogue hash checked at startup). ``(kind, ref)`` is unique."""

VERSION = 16


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS problem_signals ("
        " kind TEXT NOT NULL, ref TEXT NOT NULL, site_id TEXT, at INTEGER NOT NULL, data TEXT, PRIMARY KEY(kind, ref))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS problem_signals_site ON problem_signals(kind, site_id)")
