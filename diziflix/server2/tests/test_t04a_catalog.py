import _sandbox  # noqa: F401  (must be first)

import io
import json
import logging
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from app import config, db, settings
from app.core import events
from app.core.catalog import cache, enrich, hooks, images, lists, tmdb, write

CATALOG_DIR = os.path.join(os.path.dirname(db.MIGRATIONS_DIR), "core", "catalog")


def rec(key, title="Dark", year=2017, kind="series", **kw):
    return {"key": key, "type": kind, "title": title, "year": year, "source_url": f"https://a.example/{key}", **kw}


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        mig = os.path.join(self.tmp, "migrations")   # only this task's schema: other tasks' migrations are not our concern
        os.makedirs(mig)
        for name in ("001_base.py", "003_catalog.py", "019_source_alternatives.py"):
            shutil.copy(os.path.join(db.MIGRATIONS_DIR, name), mig)
        db.init(mig)
        cache.reset()
        images.reset()
        events.clear()
        self.queued = []
        hooks.bind_tmdb(self.queued.append)

    def tearDown(self):
        hooks.bind_tmdb(None)
        cache.reset()
        events.clear()
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def count(self, table, where="1", params=()):
        return db.query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)["n"]


class UpsertTitleTests(CatalogCase):
    def test_idempotent_and_added_at_kept(self):
        cid = write.upsert_title("s1", rec("dizi/dark"))
        db.execute("UPDATE library_items SET added_at=1000 WHERE id=?", (cid,))
        self.assertEqual(write.upsert_title("s1", rec("dizi/dark")), cid)
        self.assertEqual(self.count("library_items"), 1)
        self.assertEqual(self.count("source_items"), 1)
        self.assertEqual(db.query_one("SELECT added_at FROM library_items WHERE id=?", (cid,))["added_at"], 1000)

    def test_rejects_incomplete_record(self):
        with self.assertRaises(ValueError):
            write.upsert_title("s1", {"key": "x", "type": "movie", "title": "X"})   # no source_url
        with self.assertRaises(ValueError):
            write.upsert_title("s1", rec("x", kind="show"))

    def test_identity_merge_across_sites_and_review_when_ambiguous(self):
        a = write.upsert_title("s1", rec("dizi/dark"))
        b = write.upsert_title("s2", rec("show-dark", title="Dark izle"))
        self.assertEqual(a, b)
        self.assertEqual(self.count("library_items"), 1)
        db.execute("INSERT INTO library_items (id,type,title,year,updated_at) VALUES ('other','series','Dark',2017,0)")
        c = write.upsert_title("s3", rec("dark"))
        self.assertNotIn(c, (a, "other"))
        row = db.query_one("SELECT reason, candidates FROM identity_reviews WHERE source='s3'")
        self.assertEqual(row["reason"], "conflicting_identities")
        self.assertEqual(set(json.loads(row["candidates"])), {a, "other"})

    def test_origins_added_old_ones_stay(self):
        write.upsert_title("s1", rec("dizi/dark"), origins=[("section", "s1", 0)])
        write.upsert_title("s1", rec("dizi/dark"), origins=[("genre", "aksiyon", 3)])
        rows = {(r["kind"], r["value"], r["position"]) for r in db.query("SELECT * FROM source_origins")}
        self.assertEqual(rows, {("section", "s1", 0), ("genre", "aksiyon", 3)})

    def test_placeholder_poster_not_written(self):
        cid = write.upsert_title("s1", rec("m", kind="movie", poster_url="https://a.example/img/no-image.jpg"))
        self.assertIsNone(db.query_one("SELECT poster_url FROM library_items WHERE id=?", (cid,))["poster_url"])
        norm = json.loads(db.query_one("SELECT normalized FROM source_items")["normalized"])
        self.assertNotIn("poster_url", norm)


class EpisodeTests(CatalogCase):
    def eps(self, url1="https://a.example/dark/1x1"):
        return [{"season": 1, "episode": 1, "url": url1, "title": "Secrets"},
                {"season": 1, "episode": 2, "url": "https://a.example/dark/1x2"}]

    def test_health_kept_locator_change_resets_resolution_blocked_skipped(self):
        cid = write.upsert_title("s1", rec("dizi/dark"))
        self.assertEqual(write.upsert_episodes("s1", "dizi/dark", self.eps()), 2)
        db.execute("UPDATE video_sources SET status='healthy', failures=1, resolved_payload='{}', resolved_at=5")
        self.assertEqual(write.upsert_episodes("s1", "dizi/dark", self.eps()), 2)
        self.assertEqual(self.count("video_sources"), 2)
        self.assertEqual(self.count("video_sources", "status='healthy' AND failures=1 AND resolved_payload='{}'"), 2)
        write.upsert_episodes("s1", "dizi/dark", self.eps("https://a.example/dark/1x1-new"))
        ep1 = db.query_one("SELECT * FROM video_sources WHERE episode=1")
        self.assertEqual((ep1["locator"], ep1["resolved_payload"], ep1["status"]), ("https://a.example/dark/1x1-new", None, "healthy"))
        self.assertEqual(ep1["episode_id"], f"{cid}:s1:e1")
        write.mark_blocked("s1", "https://a.example/dark/1x3", kind="episode", reason="telif", via="rule")
        n = write.upsert_episodes("s1", "dizi/dark", [{"season": 1, "episode": 3, "url": "https://a.example/dark/1x3"}])
        self.assertEqual(n, 0)
        self.assertEqual(self.count("video_sources"), 2)

    def test_remove_title_keeps_user_data(self):
        cid = write.upsert_title("s1", rec("dizi/dark"), origins=[("section", "s1", 0)])
        write.upsert_episodes("s1", "dizi/dark", self.eps())
        db.execute("INSERT INTO progress VALUES ('p1',?,?,10,100,0,1)", (cid, f"{cid}:s1:e1"))
        db.execute("INSERT INTO mylist VALUES ('p1',?,1)", (cid,))
        write.remove_title("s1", "dizi/dark")
        for table in ("library_items", "source_items", "video_sources", "source_origins"):
            self.assertEqual(self.count(table), 0, table)
        self.assertEqual((self.count("progress"), self.count("mylist")), (1, 1))

    def test_delete_site_keeps_shared_title_and_lists_of_others(self):
        cid = write.upsert_title("s1", rec("dizi/dark"))
        write.upsert_title("s1_b", rec("dark-b"))
        lists.set_list(lists.list_id("trending", "s1"), [cid])
        lists.set_list(lists.list_id("trending", "s1_b"), [cid])
        lists.set_list(lists.list_id("category", "s1", "kore"), [cid])
        out = write.delete_site("s1")
        self.assertEqual(out["library_items"], 0)
        self.assertEqual(self.count("library_items"), 1)
        self.assertEqual(sorted(r["list_id"] for r in db.query("SELECT list_id FROM library_lists")), ["trending_s1_b"])


class SnapshotTests(CatalogCase):
    def test_visible_only_with_playable_source(self):
        a = write.upsert_title("s1", rec("dizi/dark"))
        b = write.upsert_title("s1", rec("m", title="Up", year=2009, kind="movie"))
        write.upsert_episodes("s1", "dizi/dark", [{"season": 1, "episode": 1, "url": "https://a.example/d/1"}])
        snap = cache.refresh()
        self.assertEqual([i["id"] for i in snap.visible], [a])
        self.assertIn(b, snap.by_id)
        self.assertEqual(snap.first_episode(a)["id"], f"{a}:s1:e1")


class TmdbTests(CatalogCase):
    def test_empty_key_write_ok_and_no_job(self):
        self.assertEqual(config.TMDB_ACCESS_KEY, "")
        write.upsert_title("s1", rec("dizi/dark"))
        self.assertEqual(self.queued, [])
        with mock.patch.object(config, "TMDB_ACCESS_KEY", "k" * 32):
            cid = write.upsert_title("s1", rec("m", title="Up", year=2009, kind="movie"))
            self.assertEqual(self.queued, [cid])
            settings.set("tmdb_auto", False)
            write.upsert_title("s1", rec("m2", title="Cars", year=2006, kind="movie"))
            self.assertEqual(len(self.queued), 1)

    def test_key_never_in_logs_or_errors(self):
        secret = "eyJSECRETTOKENVALUE" + "x" * 40
        write.upsert_title("s1", rec("m", title="Up", year=2009, kind="movie"))
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)

        from app.core.fetch import FetchError
        v3 = "0123456789abcdef0123456789abcdef"
        leak = FetchError("http_401", f"HTTP 401 for https://api.themoviedb.org/3/x?api_key={v3}", 401,
                          url=f"https://api.themoviedb.org/3/x?api_key={v3}")
        errors = []
        try:
            with mock.patch.object(config, "TMDB_ACCESS_KEY", secret):
                with mock.patch.object(tmdb, "_http", side_effect=RuntimeError("GET https://api/?api_key=" + secret)):
                    r1 = tmdb.find("Up", 2009)
                    r2 = enrich.enrich_items([db.query_one("SELECT id FROM library_items")["id"]])
                with mock.patch.object(tmdb, "_http", side_effect=leak):
                    r3 = tmdb.find("Up", 2009)
                    r4 = tmdb.season(1, 1)
            with mock.patch.object(config, "TMDB_ACCESS_KEY", v3):   # v3 key in the query string: the error text is clean
                import ipaddress
                from app.core.fetch import netguard
                public = mock.patch.object(netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")])
                for patcher in (mock.patch.object(tmdb, "_http", side_effect=leak), public):
                    with patcher:   # the mocked FetchError, then the real core.fetch path (no DNS; the sandbox refuses the connect)
                        with self.assertRaises(tmdb.TmdbError) as cm:
                            tmdb._get("/search/movie", query="Up")
                        errors.append(cm.exception)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        self.assertEqual((str(errors[0]), errors[0].status), ("HTTP 401", 401))
        for exc in errors:
            self.assertNotIn(v3, f"{exc!s} {exc!r} {exc.__cause__!r} {vars(exc)}")
            self.assertTrue(exc.__suppress_context__)
        self.assertEqual(r1["status"], "error")
        text = buf.getvalue() + json.dumps([r1, r2, r3, r4], default=str)
        self.assertNotIn(secret, text)
        self.assertNotIn(v3, text)
        self.assertNotIn("SECRETTOKEN", text)


class ImageTests(CatalogCase):
    def test_broken_poster_emits_once(self):
        got = []
        events.subscribe("image_broken", lambda **kw: got.append(kw))
        settings.set("image_hosts_extra", ["posters.example.com"])
        images.reset()
        with mock.patch.object(images, "_http_fetch", return_value=None):
            for _ in range(2):
                data, _etag, remote = images.artwork("t1", "portrait", "https://cdn.posters.example.com/x.jpg",
                                                     (300, 450), "Dark")
                images._neg_cache.clear()
        self.assertFalse(remote)
        self.assertTrue(data.startswith(b"\xff\xd8"))   # placeholder jpeg, still served
        images.artwork("t1", "portrait", "http://10.0.0.1/x.jpg", (300, 450), "Dark")
        self.assertEqual([(g["reason"], g["canonical_id"]) for g in got], [("http", "t1"), ("host_not_allowed", "t1")])


    def test_remote_fetch_goes_through_core_fetch(self):
        with mock.patch.object(images, "remote_host_allowed", return_value=True):   # allow-list passes,
            for url in ("http://10.0.0.1/x.jpg", "http://127.0.0.1:8091/x.jpg"):      # netguard still refuses
                self.assertIsNone(images._http_fetch(url))


class RuleTests(unittest.TestCase):
    def test_no_site_specific_code(self):
        for name in os.listdir(CATALOG_DIR):
            if name.endswith(".py"):
                with open(os.path.join(CATALOG_DIR, name), encoding="utf-8") as fh:
                    self.assertIsNone(re.search(r"@register\b", fh.read()), name)


if __name__ == "__main__":
    unittest.main()
