"""T18: ``site_stages.excluded_samples`` = sample addresses the admin removed from a stage's measurement (JSON list; kept
across re-runs and downstream resets); ``handoff`` = what a passed stage hands the next stage of the A1-A7 chain (JSON:
``{urls: [...], ...}``)."""

VERSION = 17


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(site_stages)")}
    if "excluded_samples" not in cols:
        conn.execute("ALTER TABLE site_stages ADD COLUMN excluded_samples TEXT")
    if "handoff" not in cols:
        conn.execute("ALTER TABLE site_stages ADD COLUMN handoff TEXT")
