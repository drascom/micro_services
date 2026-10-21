import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import config, db
from app.control import jobs, notes, wiring
from app.control.jobs import schedule, worker
from app.core.catalog import hooks, tmdb, write
from app.core.fetch import FetchError

KEY = "0123456789abcdef0123456789abcdef"


def rec(key, title, kind="movie"):
    return {"key": key, "type": kind, "title": title, "year": 2001, "source_url": f"https://a.example/{key}"}


def tmdb_jobs():
    return [r["key"] for r in db.query("SELECT key FROM jobs WHERE kind='tmdb' ORDER BY id")]


class TmdbAutoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        db.close_thread()
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        worker._kinds.clear()
        schedule.clear()
        hooks.bind_tmdb(None)
        wiring._tmdb_key_invalid = False

    def tearDown(self):
        hooks.bind_tmdb(None)
        worker._kinds.clear()
        schedule.clear()
        wiring._tmdb_key_invalid = False
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_queues_a_job_only_with_a_key(self):
        with mock.patch.object(config, "TMDB_ACCESS_KEY", ""):
            wiring.register()
            write.upsert_title("s1", rec("m0", "Nokey"))
        self.assertEqual(tmdb_jobs(), [])
        with mock.patch.object(config, "TMDB_ACCESS_KEY", KEY):
            cid = write.upsert_title("s1", rec("m1", "Amelie"))
            self.assertEqual(tmdb_jobs(), [f"tmdb:{cid}"])
            write.upsert_title("s1", rec("m1", "Amelie"))   # update: same key, no duplicate
        self.assertEqual(tmdb_jobs(), [f"tmdb:{cid}"])

    def test_startup_backfill_queues_null_titles_once_and_staggers(self):
        cids = [write.upsert_title("s1", rec(f"m{i}", t)) for i, t in enumerate(("Alpha", "Beta", "Gamma"))]
        self.assertEqual(tmdb_jobs(), [])   # written without a key / hook
        with mock.patch.object(config, "TMDB_ACCESS_KEY", KEY), mock.patch.object(wiring, "TMDB_BATCH", 2):
            wiring.register()
            self.assertEqual(sorted(tmdb_jobs()), sorted(f"tmdb:{c}" for c in cids))
            wiring.register()   # second start: same keys, no duplicates
            self.assertEqual(len(tmdb_jobs()), 3)
            self.assertEqual(wiring.tmdb_stats()["total"], 3)
        after = sorted(r["run_after"] for r in db.query("SELECT run_after FROM jobs WHERE kind='tmdb'"))
        self.assertEqual(after[2] - after[0], wiring.TMDB_BATCH_SPACING_S)
        self.assertIn("tmdb_backfill", schedule._defs)

    def _run(self, cid, exc):
        with mock.patch.object(config, "TMDB_ACCESS_KEY", KEY), \
                mock.patch.object(tmdb, "_http", side_effect=exc), mock.patch("time.sleep"):
            return wiring._tmdb_job({"payload": {"canonical_id": cid}}, mock.Mock())

    def test_401_notes_once_and_stops_429_retries(self):
        cid = write.upsert_title("s1", rec("m1", "Amelie"))
        out = self._run(cid, FetchError("http_429", "", 429))
        self.assertIsInstance(out, jobs.Retry)
        self.assertNotIn(KEY, out.error)
        self.assertFalse(wiring.tmdb_key_invalid())
        out = self._run(cid, FetchError("http_401", "", 401))
        self.assertIsInstance(out, jobs.Done)
        self.assertTrue(wiring.tmdb_key_invalid())
        self._run(cid, FetchError("http_401", "", 401))
        self.assertEqual([n["text"] for n in notes.unread()].count(wiring.TMDB_INVALID_NOTE), 1)
        with mock.patch.object(config, "TMDB_ACCESS_KEY", KEY):
            self.assertIsNone(wiring._enqueue_tmdb(cid))   # no more jobs after a rejected key
            self.assertEqual(wiring.tmdb_backfill()["queued"], 0)


if __name__ == "__main__":
    unittest.main()
