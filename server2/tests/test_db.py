import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import sqlite3
import tempfile
import unittest

from app import config, db, settings

_MIG_002 = '''VERSION = 2


def up(conn):
    conn.execute("CREATE TABLE extra (id INTEGER PRIMARY KEY, v TEXT)")
'''


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")

    def tearDown(self):
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class MigrationTests(DbTestCase):
    def test_applies_once_then_only_new(self):
        mig = os.path.join(self.tmp, "migrations")
        os.makedirs(mig)
        shutil.copy(os.path.join(db.MIGRATIONS_DIR, "001_base.py"), mig)
        self.assertEqual(db.init(mig), [1])
        self.assertEqual(db.init(mig), [])  # second boot: nothing re-applied
        with open(os.path.join(mig, "002_extra.py"), "w") as fh:
            fh.write(_MIG_002)
        self.assertEqual(db.init(mig), [2])  # only the new one
        versions = [r["version"] for r in db.query("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, [1, 2])
        db.execute("INSERT INTO extra(v) VALUES ('x')")

    def test_duplicate_version_fails(self):
        mig = os.path.join(self.tmp, "migrations")
        os.makedirs(mig)
        shutil.copy(os.path.join(db.MIGRATIONS_DIR, "001_base.py"), mig)
        shutil.copy(os.path.join(db.MIGRATIONS_DIR, "001_base.py"), os.path.join(mig, "002_dup.py"))
        with self.assertRaises(RuntimeError):
            db.init(mig)

    def test_failing_migration_leaves_no_trace(self):
        mig = os.path.join(self.tmp, "migrations")
        os.makedirs(mig)
        with open(os.path.join(mig, "001_bad.py"), "w") as fh:
            fh.write("VERSION = 1\n\ndef up(conn):\n    conn.execute('CREATE TABLE half (a)')\n    raise ValueError('boom')\n")
        with self.assertRaises(ValueError):
            db.init(mig)
        self.assertIsNone(db.query_one("SELECT name FROM sqlite_master WHERE name = 'half'"))
        self.assertEqual(db.query("SELECT version FROM schema_migrations"), [])


class TransactionTests(DbTestCase):
    def setUp(self):
        super().setUp()
        db.init()
        db.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")

    def test_error_rolls_back_every_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction():
                db.execute("INSERT INTO t VALUES ('a')")
                db.execute("INSERT INTO t VALUES ('b')")
                db.execute("INSERT INTO t VALUES ('a')")  # duplicate -> error
        self.assertEqual(db.query("SELECT k FROM t"), [])

    def test_commit_and_nested_join(self):
        with db.transaction():
            db.execute("INSERT INTO t VALUES ('a')")
            with db.transaction():
                db.execute("INSERT INTO t VALUES ('b')")
        self.assertEqual(sorted(r["k"] for r in db.query("SELECT k FROM t")), ["a", "b"])


class SettingsTests(DbTestCase):
    def test_defaults_and_roundtrip(self):
        db.init()
        self.assertEqual(settings.get("backfill_days"), 3)
        settings.set("backfill_days", 5)
        settings.set("tmdb_types", ["movie"])
        self.assertEqual(settings.get("backfill_days"), 5)
        self.assertEqual(settings.all()["tmdb_types"], ["movie"])
        self.assertEqual(settings.get("unknown", "x"), "x")


if __name__ == "__main__":
    unittest.main()
