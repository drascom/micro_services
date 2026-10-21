"""Stage engine (T08, README §5.1/§5.6): one row per (site, stage) + the stage's message history (user hints, short
system summaries, LLM notes). ``runs`` counts stage runs (the LLM session id is ``<site>:<stage>:<run>``);
``user_samples`` = addresses the admin added for this stage (JSON list, newest first)."""

VERSION = 9


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS site_stages ("
        " site_id TEXT NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', round INTEGER NOT NULL DEFAULT 0,"
        " patch TEXT, measurement TEXT, evidence TEXT, started_at INTEGER, updated_at INTEGER,"
        " runs INTEGER NOT NULL DEFAULT 0, user_samples TEXT,"
        " PRIMARY KEY(site_id, stage))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stage_messages ("
        " id INTEGER PRIMARY KEY, site_id TEXT NOT NULL, stage TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,"
        " at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS stage_messages_site ON stage_messages(site_id, stage, id)")
