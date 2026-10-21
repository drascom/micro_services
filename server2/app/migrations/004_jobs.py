"""Persistent job queue + admin notes (README §5.1, §5.3; T05)."""

VERSION = 4


def up(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            key TEXT UNIQUE NOT NULL,
            site_id TEXT,
            state TEXT NOT NULL DEFAULT 'queued',
            step INTEGER NOT NULL DEFAULT 0,
            payload TEXT,
            result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            priority INTEGER NOT NULL DEFAULT 0,
            run_after INTEGER NOT NULL DEFAULT 0,
            lease_until INTEGER,
            lease_owner TEXT,
            error TEXT,
            wait_reason TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            finished_at INTEGER
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_pick ON jobs(state, run_after, priority)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_site ON jobs(site_id, state)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS admin_notes (
            id INTEGER PRIMARY KEY,
            at INTEGER NOT NULL,
            level TEXT NOT NULL,
            site_id TEXT,
            text TEXT NOT NULL,
            ref_kind TEXT,
            ref_id TEXT,
            read_at INTEGER
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_admin_notes_unread ON admin_notes(read_at, id)")
