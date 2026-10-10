"""Site note (user's description of the site, max 4000 chars): ``sites.notes`` + ``notes_updated_at``; ``notes_applied_at`` =
when the stages last started a run with the note (create / "Not değişti" rerun), so the admin can see that it changed."""

VERSION = 18


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sites)")}
    for name, kind in (("notes", "TEXT"), ("notes_updated_at", "INTEGER"), ("notes_applied_at", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE sites ADD COLUMN {name} {kind}")
