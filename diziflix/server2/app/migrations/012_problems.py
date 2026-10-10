"""Problem record (T13, README §5.7): one row per unsolved situation (episode / title / site / host), its event log, the profiles
waiting for it and the engine capability gaps (filled by T14)."""

VERSION = 12


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS problems ("
        " id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, site_id TEXT, page_type TEXT, host TEXT,"
        " canonical_id TEXT, episode_id TEXT, source_id TEXT, class TEXT, layer TEXT, state TEXT NOT NULL DEFAULT 'open',"
        " parent_id INTEGER, gap_id INTEGER, evidence TEXT, attempts INTEGER NOT NULL DEFAULT 0,"
        " llm_runs INTEGER NOT NULL DEFAULT 0, next_at INTEGER, deadline_at INTEGER, opened_at INTEGER NOT NULL,"
        " updated_at INTEGER NOT NULL, resolved_at INTEGER, confirmed_at INTEGER, closed_at INTEGER, resolution TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS problems_state ON problems(state, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS problems_item ON problems(canonical_id, episode_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS problems_site ON problems(site_id, class, page_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS problems_parent ON problems(parent_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS problem_events ("
        " id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL, at INTEGER NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,"
        " note TEXT, data TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS problem_events_problem ON problem_events(problem_id, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS problem_watchers ("
        " problem_id INTEGER NOT NULL, profile_id TEXT NOT NULL, canonical_id TEXT, episode_id TEXT NOT NULL DEFAULT '',"
        " notified_at INTEGER, PRIMARY KEY(problem_id, profile_id, episode_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS capability_gaps ("
        " id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT, detail TEXT, status TEXT NOT NULL DEFAULT 'open',"
        " first_at INTEGER, sites TEXT, shipped_at INTEGER, catalog_hash TEXT)"
    )
