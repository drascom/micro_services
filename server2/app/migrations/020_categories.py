"""Admin "Kategoriler" (T24): ``home_categories`` gets a dynamic category's definition (``source``, JSON; '' = only the
routing lists ``category_<slug>_*``) and a row limit (``max_items``, 0 = none); the fixed (skeleton) rows are seeded here
(``core.catalog.categories.seed_system``, idempotent; ``homelayout`` still re-adds a missing one lazily)."""

VERSION = 20


def up(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(home_categories)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE home_categories ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    if "max_items" not in cols:
        conn.execute("ALTER TABLE home_categories ADD COLUMN max_items INTEGER NOT NULL DEFAULT 0")
    from app.core.catalog import categories
    categories.seed_system(conn)
