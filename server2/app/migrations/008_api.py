"""T04b: the two default profiles of the client API (the old server seeded them on an empty table)."""

VERSION = 8


def up(conn):
    if conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]:
        return
    import time
    now = int(time.time())
    for row in (("p1", "İsmet", 0, "a1", now), ("p2", "Çocuk", 1, "a2", now)):
        conn.execute("INSERT INTO profiles (id,name,is_kids,avatar_seed,created_at) VALUES (?,?,?,?,?)", row)
