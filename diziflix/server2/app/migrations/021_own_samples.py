"""T25: ``site_stages.own_samples_only`` = the admin's "Yalnız benim örneklerimi kullan" switch per stage (1 = the stage
measures ONLY the admin's own samples: no automatic / handoff ones). Kept across re-runs and chain resets."""

VERSION = 21


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(site_stages)")}
    if "own_samples_only" not in cols:
        conn.execute("ALTER TABLE site_stages ADD COLUMN own_samples_only INTEGER NOT NULL DEFAULT 0")
