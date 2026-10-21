"""Perf work: gzip/ETag/304, image prewarm de-dup + negative cache, non-blocking
detail hydrate, persistent db connections. No network."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, images


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_gzip_etag_304(self):
        pid = db.query_one("SELECT id FROM profiles LIMIT 1")["id"]
        url = f"/api/boot?layout=tv-v1&profile={pid}"
        r = self.client.get(url, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-encoding"), "gzip")
        tag = r.headers["etag"]
        self.assertIn("max-age", r.headers["cache-control"])
        r2 = self.client.get(url, headers={"If-None-Match": tag})
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(r2.content, b"")
        self.assertEqual(r2.headers["etag"], tag)
        r3 = self.client.get(url, headers={"If-None-Match": 'W/"nope"'})
        self.assertEqual(r3.status_code, 200)

    def test_errors_and_other_routes_untouched(self):
        r = self.client.get("/api/boot")  # no profile -> 400 JSON error, no etag
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("etag", r.headers)
        self.assertEqual(self.client.get("/api/health").status_code, 200)


class ImagePrewarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p1 = patch.object(config, "IMG_CACHE_DIR", self.tmp.name)
        p2 = patch.object(config, "REMOTE_IMG_HOSTS", ["img.test"])
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.addCleanup(self.tmp.cleanup)
        images._neg_cache.clear()

    def test_concurrent_requests_share_one_download(self):
        calls = []

        def fake(url):
            calls.append(url)
            time.sleep(0.2)
            return _png()

        with patch.object(images, "_http_fetch", fake):
            out = []
            ts = [threading.Thread(target=lambda: out.append(
                images.remote_jpeg_bytes("https://img.test/a.png", (342, 192)))) for _ in range(6)]
            [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(out))

    def test_original_reused_for_other_sizes(self):
        calls = []
        with patch.object(images, "_http_fetch", lambda u: calls.append(u) or _png()):
            images.remote_jpeg_bytes("https://img.test/b.png", (342, 192))
            images.remote_jpeg_bytes("https://img.test/b.png", (300, 450))
        self.assertEqual(len(calls), 1)

    def test_failure_is_negative_cached(self):
        calls = []
        with patch.object(images, "_http_fetch", lambda u: calls.append(u) or None):
            self.assertIsNone(images.remote_jpeg_bytes("https://img.test/x.png", (342, 192)))
            self.assertIsNone(images.remote_jpeg_bytes("https://img.test/x.png", (300, 450)))
        self.assertEqual(len(calls), 1)
        with patch.object(config, "IMG_NEG_TTL", 0), \
                patch.object(images, "_http_fetch", lambda u: calls.append(u) or None):
            images._neg_cache.clear()
            images.remote_jpeg_bytes("https://img.test/x.png", (342, 192))
            images.remote_jpeg_bytes("https://img.test/x.png", (342, 192))
        self.assertEqual(len(calls), 3)

    def test_prewarm_fills_cache_bounded(self):
        items = [{"id": f"i{n}", "poster_url": f"https://img.test/{n}.png",
                  "backdrop_url": f"https://img.test/w{n}.png"} for n in range(5)]
        items.append({"id": "off", "poster_url": "https://evil.example/x.png"})
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def fake(url):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            with lock:
                active["now"] -= 1
            return None if url.endswith("/w4.png") else _png()

        with patch.object(images, "_http_fetch", fake), \
                patch.object(config, "IMG_PREWARM_CONCURRENCY", 3):
            stats = images.prewarm(items)
            self.assertGreaterEqual(stats["failed"], 1)
            self.assertGreater(stats["done"], 0)
            self.assertLessEqual(active["max"], 3)
            again = images.prewarm(items)  # only the failed ones remain
        self.assertLess(again["targets"], stats["targets"])
        self.assertEqual(again["done"], 0)


class DetailNonBlockingTests(unittest.TestCase):
    def test_detail_returns_immediately_and_dedups(self):
        from app.routers import detail as dmod
        started = []
        release = threading.Event()

        def slow(item_id):
            started.append(item_id)
            release.wait(5)
            return False

        data = {"id": "m1", "type": "movie", "overview": ""}
        snap = type("S", (), {"source": "library"})()
        with patch.object(dmod.rows, "detail", return_value=data), \
                patch.object(dmod.cache, "get", return_value=snap), \
                patch("app.library.hydrate_item_metadata", slow):
            t0 = time.monotonic()
            out = dmod.detail("m1", "")
            dmod.detail("m1", "")
            dmod.detail("m1", "")
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(out, data)
            time.sleep(0.1)
            self.assertEqual(started, ["m1"])
            release.set()
            for _ in range(50):
                if not dmod._inflight:
                    break
                time.sleep(0.02)
            self.assertFalse(dmod._inflight)


class DbConnectionTests(unittest.TestCase):
    def test_reuse_and_path_switch(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            with patch.object(config, "DB_PATH", os.path.join(d1, "a.db")):
                db.init()
                db.query("SELECT 1"); c1 = db._local.conn
                db.query("SELECT 1"); self.assertIs(db._local.conn, c1)
                db.execute("UPDATE profiles SET name='Z' WHERE id='p1'")
                self.assertEqual(db.query_one("SELECT name FROM profiles WHERE id='p1'")["name"], "Z")
                # a second thread gets its own connection
                seen = []
                t = threading.Thread(target=lambda: (db.query("SELECT 1"), seen.append(db._local.conn)))
                t.start(); t.join()
                self.assertIsNot(seen[0], c1)
            with patch.object(config, "DB_PATH", os.path.join(d2, "b.db")):
                db.init()
                self.assertEqual(db.query_one("SELECT name FROM profiles WHERE id='p1'")["name"], "İsmet")
                self.assertIsNot(db._local.conn, c1)


if __name__ == "__main__":
    unittest.main()
