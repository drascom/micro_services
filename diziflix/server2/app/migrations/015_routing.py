"""Routing (T12, README §5.1 ``routing_rules``): "media from there goes to that group". Rules turn ``source_origins`` into
``library_lists``; LLM drafts wait in ``routing_suggestions`` until the admin accepts them; ``routing_lists`` remembers which
list ids routing wrote (so a rule that is removed empties its list and lists routing never wrote are never touched)."""

VERSION = 15


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS routing_rules ("
        " id INTEGER PRIMARY KEY, site_id TEXT, match_kind TEXT NOT NULL, match_value TEXT NOT NULL,"
        " target_kind TEXT NOT NULL, target_value TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0,"
        " enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL DEFAULT 'admin', note TEXT NOT NULL DEFAULT '',"
        " created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS routing_rules_site ON routing_rules(site_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS routing_suggestions ("
        " id INTEGER PRIMARY KEY, site_id TEXT NOT NULL, rule TEXT NOT NULL, new_category TEXT, origin TEXT NOT NULL,"
        " state TEXT NOT NULL DEFAULT 'pending', rule_id INTEGER, created_at INTEGER NOT NULL, decided_at INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS routing_suggestions_site ON routing_suggestions(site_id, state)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS routing_lists (site_id TEXT NOT NULL, list_id TEXT NOT NULL, PRIMARY KEY(site_id, list_id))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS routing_state (site_id TEXT PRIMARY KEY, auto_suggest_at INTEGER)")
