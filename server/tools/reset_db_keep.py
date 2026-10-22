"""Empty a diziflix SQLite database but keep chosen tables (default: the home categories).

Used by ``clear-remote.sh`` on the server while the service is STOPPED (stdlib only, no app imports):
deletes every row of every table except ``--keep`` ones, resets AUTOINCREMENT counters, checkpoints the WAL and VACUUMs.
The schema is untouched, so the service just opens the file again. All deletes run in one transaction (a failure leaves
the data as it was).

    python3 tools/reset_db_keep.py /path/diziflix.db [--keep home_categories,...] [--wipe-categories]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

# Tables that survive a reset. Category membership (library_lists category_<slug>_<site>) is NOT kept: it points at
# titles that are gone and the scraper's category role rewrites it on the next scan.
KEEP_TABLES = ("home_categories",)


def _tables(conn: sqlite3.Connection) -> tuple[list[str], set[str]]:
    """(plain table names, names of virtual tables + their shadow tables) without ``sqlite_%`` internals."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = [n for n, sql in rows if (sql or "").lstrip().upper().startswith("CREATE VIRTUAL")]
    shadow = {n for n, _ in rows for v in virtual if n.startswith(v + "_")}
    plain = [n for n, _ in rows if n not in virtual and n not in shadow]
    return plain + virtual, shadow


def clear_tables(conn: sqlite3.Connection, keep=KEEP_TABLES) -> list[str]:
    """Delete every row of every table of ``conn`` except ``keep`` ones (+ their AUTOINCREMENT counters) in ONE
    ``BEGIN IMMEDIATE`` transaction (rolled back on failure). ``conn`` must be in autocommit mode
    (``isolation_level=None``). Returns the cleared table names. Shared by ``reset`` (service stopped) and the admin
    live reset (``app/library/libreset.py``), so both use the same table selection."""
    keep = set(keep)
    conn.execute("PRAGMA foreign_keys=OFF")
    tables, _shadow = _tables(conn)
    cleared = [t for t in tables if t not in keep]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for t in cleared:
            conn.execute('DELETE FROM "%s"' % t.replace('"', '""'))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            conn.executemany("DELETE FROM sqlite_sequence WHERE name=?", [(t,) for t in cleared])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return cleared


def kept_counts(conn: sqlite3.Connection, keep=KEEP_TABLES) -> dict:
    tables, _shadow = _tables(conn)
    return {t: conn.execute('SELECT COUNT(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0]
            for t in tables if t in set(keep)}


def reset(db_path: str, keep=KEEP_TABLES) -> dict:
    """Wipe ``db_path`` except the ``keep`` tables; returns ``{"kept": {table: rows}, "cleared": [tables]}``."""
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        cleared = clear_tables(conn, keep)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"kept": kept_counts(conn, keep), "cleared": cleared}
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("db")
    ap.add_argument("--keep", default=",".join(KEEP_TABLES), help="comma-separated tables to keep")
    ap.add_argument("--wipe-categories", action="store_true", help="keep nothing (categories go too)")
    a = ap.parse_args(argv)
    keep = [] if a.wipe_categories else [t for t in a.keep.split(",") if t.strip()]
    res = reset(a.db, keep)
    kept = ", ".join("%s=%d" % kv for kv in sorted(res["kept"].items())) or "(yok)"
    print("temizlenen tablo: %d; korunan: %s" % (len(res["cleared"]), kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
