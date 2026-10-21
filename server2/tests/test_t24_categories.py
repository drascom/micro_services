import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app import config, db, main
from app.control.routing import engine, rules
from app.core.catalog import cache, categories, write

B = "/api/control/categories"


def title(site, key, name, kind="series", origins=(), **kw):
    cid = write.upsert_title(site, {"key": key, "type": kind, "title": name, "source_url": f"https://{site}.test/{key}",
                                    **kw}, origins=origins)
    if kind == "series":
        write.upsert_episodes(site, key, [{"season": 1, "episode": 1, "url": f"https://{site}.test/{key}/1-1"}])
    else:
        write.set_movie_source(site, key, f"https://{site}.test/{key}/izle")
    return cid


class CategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        cls._old = config.DB_PATH
        config.DB_PATH = os.path.join(cls.tmp, "cats.db")
        db.close_thread()
        db.init()
        cache.reset()
        cls.c = TestClient(main.app)   # no lifespan: no job executor
        now = int(time.time())
        db.execute("INSERT INTO sites(site_id,display_name,base_url,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   ("alpha", "Alfa", "https://alpha.test", "active", now, now))
        cls.d1 = title("alpha", "dizi/a", "Ada", genres=["Dram"], rating=8.0, origins=[("listing", "Kore Dizileri", 2)])
        cls.d2 = title("alpha", "dizi/b", "Bulut", genres=["Dram", "Komedi"], rating=7.0,
                       origins=[("listing", "Kore Dizileri", 1)])
        cls.m1 = title("alpha", "film/c", "Ceviz", "movie", genres=["Dram"], rating=6.0)
        cache.refresh()

    @classmethod
    def tearDownClass(cls):
        cache.reset()
        db.close_thread()
        config.DB_PATH = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        db.execute("DELETE FROM home_categories WHERE kind = 'category'")
        db.execute("UPDATE home_categories SET enabled = 1, max_items = 0")
        db.execute("DELETE FROM routing_rules")
        engine.apply("alpha")
        self.c.put(B + "-order", json={"order": list(categories.SYSTEM_DEFAULT)})

    def boot(self):
        r = self.c.get("/api/boot?profile=p1&layout=tv-v1")
        self.assertEqual(r.status_code, 200, r.text[:300])
        return [row["id"] for row in r.json()["rows"]], r.json()

    def add(self, title_, source=None, **kw):
        r = self.c.post(B, json={"title": title_, "source": source, "min_items": 1, **kw})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["category"]

    def test_fixed_seed_and_hide_rename(self):
        rows = [r for r in db.query("SELECT slug, kind FROM home_categories WHERE kind = 'system'")]
        self.assertEqual({r["slug"] for r in rows}, set(categories.SYSTEM_DEFAULT))   # migration 020 seed
        self.assertIn("movies", self.boot()[0])
        self.assertEqual(self.c.put(B + "/movies", json={"enabled": False, "title": "Filmler"}).status_code, 200)
        ids, _ = self.boot()
        self.assertNotIn("movies", ids)
        self.assertEqual(self.c.delete(B + "/series").json()["error"]["code"], "locked")   # fixed: hide only
        self.c.put(B + "/movies", json={"enabled": True})
        _, body = self.boot()
        self.assertEqual(next(r["title"] for r in body["rows"] if r["id"] == "movies"), "Filmler")

    def test_dynamic_genre_order_and_empty_hidden(self):
        self.add("Komedi Dizileri", {"kind": "genre", "genre": "komedi", "type": "series"})
        self.add("Korku", {"kind": "genre", "genre": "Korku"})            # no title: never shown
        ids, body = self.boot()
        self.assertNotIn("cat_korku", ids)
        self.assertEqual(ids.index("cat_komedi-dizileri"), ids.index("trending_series") + 1)
        row = next(r for r in body["rows"] if r["id"] == "cat_komedi-dizileri")
        self.assertEqual(([i["id"] for i in row["items"]], row["title"]), ([self.d2], "Komedi Dizileri"))

    def test_reorder_changes_boot(self):
        self.add("Dram", {"kind": "genre", "genre": "Dram"})
        before, _ = self.boot()
        self.assertLess(before.index("cat_dram"), before.index("series"))
        order = [s for s in categories.SYSTEM_DEFAULT] + ["dram"]
        self.assertEqual(self.c.put(B + "-order", json={"order": order}).status_code, 200)
        after, _ = self.boot()
        self.assertGreater(after.index("cat_dram"), after.index("movies"))

    def test_row_content_limit_and_preview(self):
        self.add("Dram", {"kind": "genre", "genre": "Dram"}, max_items=2)
        row = self.c.get("/api/row/cat_dram?profile=p1").json()
        self.assertEqual(([i["id"] for i in row["items"]], row["total"]), ([self.d1, self.d2], 2))   # rating order, cut
        prev = self.c.get(B + "/dram/preview").json()
        self.assertEqual((prev["count"], prev["shown"], len(prev["items"])), (3, 2, 2))
        site = self.c.post(B + "/preview", json={"source": {"kind": "site", "site_id": "alpha", "origin": "listing",
                                                            "value": "KORE DİZİLERİ"}}).json()
        self.assertEqual([i["id"] for i in site["items"]], [self.d2, self.d1])   # origin position order
        bad = self.c.post(B, json={"title": "X", "source": {"kind": "site", "site_id": "nope"}})
        self.assertEqual(bad.json()["error"]["code"], "invalid_source")

    def test_routing_rule_fills_category(self):
        cat = self.add("Asya")            # source: routing lists only
        self.assertNotIn("cat_asya", self.boot()[0])
        rules.add("alpha", {"match_kind": "listing", "match_value": "kore dizileri", "target_kind": "category",
                            "target_value": cat["slug"]})
        engine.apply("alpha")
        row = self.c.get("/api/row/cat_asya?profile=p1").json()
        self.assertEqual([i["id"] for i in row["items"]], [self.d2, self.d1])
        self.assertIn("cat_asya", self.boot()[0])
        listed = self.c.get(B).json()
        asya = next(c for c in listed["categories"] if c["slug"] == "asya")
        self.assertEqual((asya["kind"], asya["titles"], asya["source"]["kind"]), ("category", 2, "lists"))
        self.assertIn("Dram", [g["value"] for g in listed["options"]["genres"]])


if __name__ == "__main__":
    unittest.main()
