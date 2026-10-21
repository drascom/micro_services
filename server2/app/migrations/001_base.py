"""Base schema: runtime settings (README §6)."""

VERSION = 1


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
