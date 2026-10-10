"""SQLite storage layer: per-thread connections, WAL, transactions and versioned migrations.

Migrations are files ``app/migrations/NNN_name.py`` exposing ``VERSION: int`` and ``up(conn)``. ``init()`` applies the
missing ones in version order, each inside its own ``BEGIN IMMEDIATE`` transaction, and records them in
``schema_migrations``. ``up`` must use ``conn.execute`` (not ``executescript``, which would commit on its own).
"""
from __future__ import annotations

import atexit
import contextlib
import importlib.util
import os
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Iterator, Optional

from . import config

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
_MIGRATION_FILE = re.compile(r"^(\d{3})_\w+\.py$")

# Process-wide writer lock (re-entrant: execute() inside transaction() on the same thread joins it).
_lock = threading.RLock()
_local = threading.local()


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """New connection in autocommit mode (transactions are explicit: ``transaction()`` / ``BEGIN``)."""
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _file_key(path: str) -> tuple:
    try:
        return (path, os.stat(path).st_ino)
    except OSError:
        return (path, None)


def _close_quiet(conn: Optional[sqlite3.Connection]) -> None:
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover
            pass


class _Closer:
    """Lives in the thread-local next to the connection: when the thread ends (e.g. an idle anyio / thread-pool worker
    retires) its locals are dropped and this closes the connection instead of leaving it to the GC unclosed."""
    __slots__ = ("conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __del__(self) -> None:
        _close_quiet(self.conn)


def _shared() -> sqlite3.Connection:
    """Per-thread persistent connection, re-opened when DB_PATH (or the file
    behind it) changes, e.g. a test pointing config.DB_PATH at a temp DB."""
    key = _file_key(config.DB_PATH)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "key", None) != key:
        _close_quiet(conn)
        conn = connect()  # creates the file if missing
        _local.conn = conn
        _local.closer = _Closer(conn)
        _local.key = _file_key(config.DB_PATH)
    return conn


def close_thread() -> None:
    """Close this thread's shared connection (worker threads on exit, tests on teardown)."""
    _close_quiet(getattr(_local, "conn", None))
    _local.conn = None
    _local.closer = None


# The main thread's connection is closed explicitly at interpreter exit: at shutdown the GC may finalize the connection
# before its thread-local ``_Closer`` (``ResourceWarning: unclosed database``).
atexit.register(close_thread)


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    conn = _shared()
    try:
        return list(conn.execute(sql, tuple(params)).fetchall())
    except sqlite3.ProgrammingError:
        _close_quiet(conn)
        _local.conn = None
        raise


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    """One statement. Outside a transaction it commits on its own; inside ``transaction()`` (same thread) it joins it."""
    with _lock:
        return _shared().execute(sql, tuple(params))


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Atomic multi-statement write: ``BEGIN IMMEDIATE`` ... ``COMMIT``; any exception rolls everything back.

    Use ``db.execute`` / ``db.query`` (or the yielded connection) inside the block. Nested use joins the outer
    transaction (the outer block decides commit/rollback)."""
    with _lock:
        conn = _shared()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover
                pass
            raise
        conn.execute("COMMIT")


@contextlib.contextmanager
def read_transaction() -> Iterator[sqlite3.Connection]:
    """One consistent read view for the ``db.query`` calls made inside the block (this thread's connection).

    WAL: the view is fixed at the first SELECT and never blocks a writer, it is dropped again on exit. Nested /
    already inside a transaction: joins it. Read-only use: writes on this connection inside the block are not supported."""
    conn = _shared()
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if began:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover
                pass


# --------------------------------------------------------------------------- migrations

def _load_migrations(directory: str) -> list[tuple[int, str, Any]]:
    found: dict[int, tuple[str, Any]] = {}
    for name in sorted(os.listdir(directory)):
        if not _MIGRATION_FILE.match(name):
            continue
        path = os.path.join(directory, name)
        spec = importlib.util.spec_from_file_location(f"_diziflix_migration_{name[:-3]}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        version = getattr(module, "VERSION", None)
        if not isinstance(version, int) or not callable(getattr(module, "up", None)):
            raise RuntimeError(f"migration {name}: needs an int VERSION and an up(conn) function")
        if version in found:
            raise RuntimeError(f"migration version {version} is defined twice: {found[version][0]} and {name}")
        found[version] = (name, module)
    return [(v, n, m) for v, (n, m) in sorted(found.items())]


def init(migrations_dir: Optional[str] = None) -> list[int]:
    """Create the schema: apply every migration not yet in ``schema_migrations``. Returns the versions applied now."""
    migrations = _load_migrations(migrations_dir or MIGRATIONS_DIR)
    applied_now: list[int] = []
    with _lock:
        conn = _shared()
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, module in migrations:
            if version in done:
                continue
            with transaction() as tx:
                module.up(tx)
                tx.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, int(time.time())))
            applied_now.append(version)
    return applied_now
