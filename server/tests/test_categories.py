"""Admin "Kategoriler" (app/library/categories.py, routers/ops_categories.py) and the home rows ``cat_<slug>``
(app/homelayout.py): CRUD, slug, order, min_items / disabled, row order, multi-site merge, multi-category titles,
``/api/boot`` + ``/api/row``. No network, temp DB."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import cache, config, db, homelayout as hl, rows
from app.library import categories as cats
from app.main import app

NOW = 1785000000.0
READY = dict(state="ready", reason=None, has_trailer=False)


def mk(i, kind="series", state="ready"):
    return dict(id=i, type=kind, title=i.upper(), year=2020, overview="", genres=["Dram"], rating=None, followers=None,
                added_at=0, seasons=[], availability=dict(READY, state=state), backdrop_url="https://img.test/%s.jpg" % i)


def snap_of(*items):
    return SimpleNamespace(items=list(items), by_id={i["id"]: i for i in items}, source="library",
                           slug_genre={}, by_genre={}, newest=[], top10=[], upcoming=None, real_home=True)


def ids(items):
    return [i["id"] for i in items]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        p.start()
        self.addCleanup(p.stop)
        db.init()
        self.client = TestClient(app)

    def member(self, slug, site, *cids):
        for pos, cid in enumerate(cids):
            db.execute("INSERT OR REPLACE INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)",
                       ("category_%s_%s" % (slug, site), cid, pos))

    def trending(self, site, *cids):
        for pos, cid in enumerate(cids):
            db.execute("INSERT OR REPLACE INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)",
                       ("trending_" + site, cid, pos))

    def compose(self, snap):
        return hl.compose(snap, "p1", {}, NOW)

    def row_ids(self, snap):
        return [r["id"] for r in self.compose(snap)["rows"]]


class SlugAndCrud(Base):
    def test_slugify(self):
        self.assertEqual(cats.slugify("Kore Dizileri"), "kore-dizileri")
        self.assertEqual(cats.slugify("  Çocuk & Aile — Şenlik! "), "cocuk-aile-senlik")
        self.assertEqual(cats.slugify("İstanbul ığüöşç"), "istanbul-iguosc")
        self.assertEqual(cats.slugify("!!!"), "")
        s = cats.slugify("a" * 100 + " b")
        self.assertLessEqual(len(s), 40)
        self.assertTrue(cats.valid_slug("kore-dizileri"))
        self.assertFalse(cats.valid_slug("Kore_Dizileri"))

    def test_slug_parsing_of_ids(self):
        self.assertEqual(cats.slug_of_list("category_kore-dizileri_yabancidizi"), "kore-dizileri")
        self.assertEqual(cats.slug_of_list("category_kore_site_with_underscore"), "kore")
        self.assertIsNone(cats.slug_of_list("trending_x"))
        self.assertEqual(cats.slug_of_row("cat_kore"), "kore")
        self.assertIsNone(cats.slug_of_row("series"))

    def test_create_update_reorder_delete(self):
        self.assertEqual(cats.list_all(), [])
        a = cats.create("Kore Dizileri")
        self.assertEqual((a["slug"], a["position"], a["enabled"], a["min_items"]), ("kore-dizileri", 0, True, 6))
        cats.create("Komedi")
        cats.create("Aksiyon", slug="action")
        self.assertEqual([c["slug"] for c in cats.list_all()], ["kore-dizileri", "komedi", "action"])
        with self.assertRaises(cats.CategoryError) as cm:
            cats.create("Kore Dizileri")
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "slug_exists"))
        cats.update("komedi", title="Komedi Dizileri", enabled=False, min_items=3)
        c = cats.get("komedi")
        self.assertEqual((c["title"], c["enabled"], c["min_items"]), ("Komedi Dizileri", False, 3))
        self.assertEqual([c["slug"] for c in cats.list_all(include_disabled=False)], ["kore-dizileri", "action"])
        cats.reorder(["action", "kore-dizileri"])    # komedi not listed: stays after them
        self.assertEqual([c["slug"] for c in cats.list_all()], ["action", "kore-dizileri", "komedi"])
        with self.assertRaises(cats.CategoryError):
            cats.reorder(["nope"])
        for bad in (0, 101, "x"):
            with self.assertRaises(cats.CategoryError):
                cats.update("komedi", min_items=bad)
        with self.assertRaises(cats.CategoryError):
            cats.update("komedi", title="   ")
        cats.delete("komedi")
        self.assertFalse(cats.exists("komedi"))
        with self.assertRaises(cats.CategoryError) as cm:
            cats.delete("komedi")
        self.assertEqual(cm.exception.status, 404)

    def test_deleted_slug_returns_with_its_membership(self):
        cats.create("Kore Dizileri")
        self.member("kore-dizileri", "siteA", "a", "b")
        cats.delete("kore-dizileri")
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM library_lists")["n"], 2)   # membership stays
        again = cats.create("Kore Dizileri")
        self.assertEqual(again["slug"], "kore-dizileri")
        self.assertEqual(cats.membership()["kore-dizileri"]["category_kore-dizileri_siteA"], ["a", "b"])

    def test_list_all_counts(self):
        cats.create("Kore Dizileri")
        cats.create("Boş")
        self.member("kore-dizileri", "siteA", "a", "b", "gone")
        self.member("kore-dizileri", "siteB", "b", "c")
        snap = snap_of(mk("a"), mk("b", state="unavailable"), mk("c"))
        with patch.object(cache, "get", return_value=snap):
            got = {c["slug"]: c for c in cats.list_all()}
        self.assertEqual((got["kore-dizileri"]["lists"], got["kore-dizileri"]["titles"], got["kore-dizileri"]["playable"]), (2, 4, 2))
        self.assertEqual((got["bos"]["lists"], got["bos"]["titles"], got["bos"]["playable"]), (0, 0, 0))


class HomeRows(Base):
    def setUp(self):
        super().setUp()
        self.items = [mk("s%d" % n) for n in range(1, 9)] + [mk("m1", "movie")]
        self.snap = snap_of(*self.items)

    def test_no_categories_no_cat_rows(self):
        self.assertEqual([r for r in self.row_ids(self.snap) if r.startswith("cat_")], [])

    def test_row_after_trending_series_before_all_series_in_admin_order(self):
        cats.create("Kore Dizileri", "kore")
        cats.create("Komedi", "komedi")
        for slug in ("kore", "komedi"):
            self.member(slug, "siteA", *["s%d" % n for n in range(1, 7)])
        self.trending("siteA", "s1", "s2")
        ids_ = self.row_ids(self.snap)
        self.assertEqual(ids_[:4], ["trending_series", "cat_kore", "cat_komedi", "series"])
        cats.reorder(["continue", "trending_series", "komedi", "kore"])
        self.assertEqual(self.row_ids(self.snap)[:4], ["trending_series", "cat_komedi", "cat_kore", "series"])
        row = [r for r in self.compose(self.snap)["rows"] if r["id"] == "cat_kore"][0]
        self.assertEqual((row["title"], row["total"], row["loaded"]), ("Kore Dizileri", 6, True))
        cats.update("kore", title="Kore")
        self.assertEqual([r["title"] for r in self.compose(self.snap)["rows"] if r["id"] == "cat_kore"], ["Kore"])

    def test_min_items_threshold_counts_playable_and_disabled_hides(self):
        cats.create("Kore", "kore")
        self.member("kore", "siteA", "s1", "s2", "s3", "s4", "s5")
        self.assertNotIn("cat_kore", self.row_ids(self.snap))              # 5 < default 6
        cats.update("kore", min_items=5)
        self.assertIn("cat_kore", self.row_ids(self.snap))
        cats.update("kore", enabled=False)
        self.assertNotIn("cat_kore", self.row_ids(self.snap))
        cats.update("kore", enabled=True)
        unplayable = snap_of(*[mk("s%d" % n, state="unavailable" if n > 2 else "ready") for n in range(1, 9)])
        self.assertNotIn("cat_kore", self.row_ids(unplayable))             # HOME_ONLY_READY: only 2 playable
        with patch.object(config, "HOME_ONLY_READY", False):
            self.assertNotIn("cat_kore", self.row_ids(unplayable))         # still < min_items playable
            cats.update("kore", min_items=2)
            row = [r for r in self.compose(unplayable)["rows"] if r["id"] == "cat_kore"][0]
            self.assertEqual(row["total"], 5)                              # not-ready titles listed when ready-only is off

    def test_only_ready_titles_listed(self):
        cats.create("Kore", "kore")
        cats.update("kore", min_items=1)
        self.member("kore", "siteA", "s1", "s2", "s3")
        snap = snap_of(mk("s1"), mk("s2", state="unavailable"), mk("s3"))
        row = [r for r in self.compose(snap)["rows"] if r["id"] == "cat_kore"][0]
        self.assertEqual([i["id"] for i in row["items"]], ["s1", "s3"])

    def test_sites_merge_round_robin_without_repeats_and_titles_in_many_categories(self):
        cats.create("Kore", "kore")
        cats.create("Komedi", "komedi")
        cats.update("kore", min_items=1)
        cats.update("komedi", min_items=1)
        self.member("kore", "siteA", "s1", "s2", "s3")
        self.member("kore", "siteB", "s4", "s2", "s5")
        self.member("komedi", "siteA", "s2", "s6")
        snap = self.snap
        with patch.object(rows, "mylist_ids", return_value=[]):
            kore = hl.pool(snap, "cat_kore", "p1", {}, NOW)
            komedi = hl.pool(snap, "cat_komedi", "p1", {}, NOW)
        self.assertEqual(ids(kore), ["s1", "s4", "s2", "s3", "s5"])
        self.assertEqual(ids(komedi), ["s2", "s6"])                         # s2 is in both categories

    def test_boot_and_row_endpoints(self):
        cats.create("Kore Dizileri", "kore")
        self.member("kore", "siteA", *["s%d" % n for n in range(1, 8)])
        with patch.object(cache, "get", return_value=self.snap), patch.object(rows, "get_cache", return_value=self.snap):
            boot = self.client.get("/api/boot", params={"profile": "p1", "layout": "tv-v1"}).json()
            got = [r["id"] for r in boot["rows"]]
            self.assertLess(got.index("cat_kore"), got.index("series"))
            self.assertEqual([r["title"] for r in boot["rows"] if r["id"] == "cat_kore"], ["Kore Dizileri"])
            r = self.client.get("/api/row/cat_kore", params={"profile": "p1", "limit": 3, "offset": 2})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual((body["id"], body["title"], body["total"], len(body["items"])), ("cat_kore", "Kore Dizileri", 7, 3))
            self.assertEqual(self.client.get("/api/row/cat_yok", params={"profile": "p1"}).status_code, 404)


class AdminApi(Base):
    def test_endpoints_and_error_envelope(self):
        c = self.client
        self.assertEqual([x["kind"] for x in c.get("/api/ops/categories").json()["categories"]], ["system"] * 7)   # skeleton only
        r = c.post("/api/ops/categories", json={"title": "Kore Dizileri"})
        self.assertEqual((r.status_code, r.json()["slug"]), (200, "kore-dizileri"))
        r = c.post("/api/ops/categories", json={"title": "Kore Dizileri"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "slug_exists"))
        self.assertIn("message", r.json()["error"])
        self.assertEqual(c.post("/api/ops/categories", json={"title": " "}).json()["error"]["code"], "invalid_title")
        self.assertEqual(c.post("/api/ops/categories", json={"title": "x", "slug": "Bad_Slug"}).json()["error"]["code"], "invalid_slug")
        c.post("/api/ops/categories", json={"title": "Komedi"})
        r = c.put("/api/ops/categories/komedi", json={"title": "Komedi Dizileri", "enabled": False, "min_items": 4})
        self.assertEqual((r.status_code, r.json()["enabled"], r.json()["min_items"]), (200, False, 4))
        self.assertEqual(c.put("/api/ops/categories/komedi", json={"enabled": "yes"}).status_code, 400)
        self.assertEqual(c.put("/api/ops/categories/komedi", json={"min_items": 0}).json()["error"]["code"], "invalid_min_items")
        r = c.put("/api/ops/categories/yok", json={"title": "x"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"))
        r = c.put("/api/ops/categories-order", json={"order": ["komedi", "kore-dizileri"]})
        self.assertEqual([x["slug"] for x in r.json()["categories"] if x["kind"] == "category"], ["komedi", "kore-dizileri"])
        self.assertEqual(c.put("/api/ops/categories-order", json={"order": ["zzz"]}).json()["error"]["code"], "unknown_slug")
        self.assertEqual(c.put("/api/ops/categories-order", json={"order": "x"}).status_code, 400)
        listed = [x for x in c.get("/api/ops/categories").json()["categories"] if x["kind"] == "category"]
        self.assertEqual([x["slug"] for x in listed], ["komedi", "kore-dizileri"])
        self.assertEqual(set(listed[0]), {"slug", "title", "position", "enabled", "min_items", "kind", "lists", "titles",
                                          "playable"})
        self.assertEqual(c.delete("/api/ops/categories/komedi").json(), {"deleted": "komedi"})
        self.assertEqual(c.delete("/api/ops/categories/komedi").status_code, 404)

    def test_admin_asset_served(self):
        self.assertEqual(self.client.get("/admin/categories.js").status_code, 200)
        self.assertIn('id="tab-categories"', self.client.get("/admin").text)


if __name__ == "__main__":
    unittest.main()
