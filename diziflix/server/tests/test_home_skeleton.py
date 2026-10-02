"""Home skeleton rows (continue, trending_*, series, ...) in the admin "Kategoriler" list: seeded into ``home_categories``
(kind 'system'), locked (no delete / rename / hide) but movable, categories can sit between them, and ``homelayout.layout``
follows the table (old fixed order when the table is empty/unreadable). No network, temp DB."""
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
DEFAULT = ["continue", "trending_series", "series", "trending_movies", "noteworthy_movies", "movies", "mylist"]


def mk(i, kind="series"):
    return dict(id=i, type=kind, title=i.upper(), year=2020, overview="", genres=["Dram"], rating=None, followers=None,
                added_at=1000, seasons=[], availability=dict(state="ready", reason=None, has_trailer=False),
                backdrop_url="https://img.test/%s.jpg" % i)


def snap_of(*items):
    return SimpleNamespace(items=list(items), by_id={i["id"]: i for i in items}, source="library",
                           slug_genre={}, by_genre={}, newest=[], top10=[], upcoming=None, real_home=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        p.start()
        self.addCleanup(p.stop)
        db.init()
        self.client = TestClient(app)
        self.snap = snap_of(*[mk("s%d" % n) for n in range(1, 9)], mk("m1", "movie"), mk("m2", "movie"))

    def slugs(self):
        return [r["slug"] for r in cats.ordered_rows()]

    def member(self, slug, *cids):
        for pos, cid in enumerate(cids):
            db.execute("INSERT OR REPLACE INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)",
                       ("category_%s_siteA" % slug, cid, pos))

    def compose_ids(self, saved=()):
        with patch.object(rows, "mylist_ids", return_value=list(saved)):
            return [r["id"] for r in hl.compose(self.snap, "p1", {}, NOW)["rows"]]


class Seeding(Base):
    def test_seed_is_idempotent_and_default_order(self):
        self.assertTrue(hl.seed_skeleton())
        self.assertFalse(hl.seed_skeleton())
        self.assertEqual(self.slugs(), DEFAULT)
        self.assertTrue(all(r["kind"] == "system" and r["locked"] for r in cats.ordered_rows()))
        self.assertEqual([r["title"] for r in cats.ordered_rows()][:2], ["İzlemeye Devam Et", "Haftanın Trendleri · Diziler"])

    def test_existing_categories_land_between_trending_series_and_series(self):
        cats.create("Kore", "kore")
        cats.create("Komedi", "komedi")
        hl.seed_skeleton()
        self.assertEqual(self.slugs(), ["continue", "trending_series", "kore", "komedi", "series", "trending_movies",
                                        "noteworthy_movies", "movies", "mylist"])

    def test_seed_never_overrides_the_admin_order(self):
        hl.seed_skeleton()
        order = ["mylist", "movies"] + [s for s in DEFAULT if s not in ("mylist", "movies")]
        cats.reorder(order)
        self.assertFalse(hl.seed_skeleton())
        self.assertEqual(self.slugs(), order)

    def test_a_missing_skeleton_row_is_reinserted_in_its_place(self):
        hl.seed_skeleton()
        db.execute("DELETE FROM home_categories WHERE slug = 'noteworthy_movies'")
        self.assertTrue(hl.seed_skeleton())
        self.assertEqual(self.slugs(), DEFAULT)

    def test_new_category_goes_after_the_last_category_then_next_ones_follow(self):
        hl.seed_skeleton()
        cats.create("Kore", "kore")
        cats.create("Komedi", "komedi")
        self.assertEqual(self.slugs(), ["continue", "trending_series", "kore", "komedi", "series", "trending_movies",
                                        "noteworthy_movies", "movies", "mylist"])
        with self.assertRaises(cats.CategoryError) as cm:   # skeleton slugs without underscore are reserved
            cats.create("Series")
        self.assertEqual(cm.exception.status, 409)

    def test_kind_column_migrates_an_old_table(self):
        db.execute("DROP TABLE home_categories")
        db.execute("CREATE TABLE home_categories (slug TEXT PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL, "
                   "enabled INTEGER NOT NULL DEFAULT 1, min_items INTEGER NOT NULL DEFAULT 6, "
                   "created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO home_categories (slug, title, position) VALUES ('kore', 'Kore', 0)")
        db.init()
        self.assertEqual(cats.get("kore")["kind"], "category")
        hl.seed_skeleton()
        self.assertEqual(self.slugs()[:3], ["continue", "trending_series", "kore"])


class Layout(Base):
    def test_default_layout_is_the_old_fixed_order(self):
        self.assertEqual(hl.layout("p"), DEFAULT)
        self.assertEqual(hl.layout("p"), hl._fixed_layout())          # table seeded = same as no table
        cats.create("Kore", "kore")
        self.assertEqual(hl.layout("p"), hl._fixed_layout())          # ... also with a category

    def test_empty_table_falls_back_to_the_fixed_order(self):
        with patch.object(hl, "seed_skeleton", return_value=False):
            self.assertEqual(hl.layout("p"), DEFAULT)
        db.execute("DROP TABLE home_categories")
        self.assertEqual(hl.layout("p"), DEFAULT)

    def test_reordering_the_skeleton_shows_in_layout(self):
        hl.seed_skeleton()
        order = ["series", "continue", "movies", "trending_series", "trending_movies", "noteworthy_movies", "mylist"]
        cats.reorder(order)
        self.assertEqual(hl.layout("p"), order)

    def test_category_between_skeleton_rows_and_disabled_one_left_out(self):
        cats.create("Kore", "kore")
        cats.create("Komedi", "komedi")
        self.member("kore", *["s%d" % n for n in range(1, 8)])
        self.member("komedi", *["s%d" % n for n in range(1, 8)])
        hl.seed_skeleton()
        cats.reorder(["continue", "trending_series", "series", "kore", "trending_movies", "noteworthy_movies", "movies",
                      "komedi", "mylist"])
        self.assertEqual(hl.layout("p"), ["continue", "trending_series", "series", "cat_kore", "trending_movies",
                                          "noteworthy_movies", "movies", "cat_komedi", "mylist"])
        ids_ = self.compose_ids()
        self.assertLess(ids_.index("series"), ids_.index("cat_kore"))
        self.assertLess(ids_.index("cat_kore"), ids_.index("movies"))
        self.assertLess(ids_.index("movies"), ids_.index("cat_komedi"))
        cats.update("komedi", enabled=False)
        self.assertNotIn("cat_komedi", hl.layout("p"))

    def test_continue_and_mylist_hide_when_empty_wherever_they_sit(self):
        hl.seed_skeleton()
        cats.reorder(["mylist", "continue", "series", "movies", "trending_series", "trending_movies", "noteworthy_movies"])
        self.assertEqual(hl.layout("p")[:2], ["mylist", "continue"])
        ids_ = self.compose_ids()
        self.assertNotIn("mylist", ids_)
        self.assertNotIn("continue", ids_)
        ids_ = self.compose_ids(saved=["s1"])
        self.assertEqual(ids_[0], "mylist")

    def test_extra_home_layout_ids_and_switched_off_rows(self):
        with patch.object(config, "HOME_LAYOUT", ["series", "genre_x", "movies"]):
            self.assertEqual(hl.layout("p"), ["series", "genre_x", "movies"])


class Api(Base):
    def test_get_lists_the_skeleton_locked_and_without_counts(self):
        cats.create("Kore", "kore")
        got = self.client.get("/api/ops/categories").json()["categories"]
        self.assertEqual([c["slug"] for c in got], ["continue", "trending_series", "kore", "series", "trending_movies",
                                                    "noteworthy_movies", "movies", "mylist"])
        sysrow = got[0]
        self.assertEqual((sysrow["kind"], sysrow["locked"], sysrow["enabled"]), ("system", True, True))
        self.assertNotIn("titles", sysrow)
        self.assertEqual((got[2]["kind"], "lists" in got[2], "locked" in got[2]), ("category", True, False))

    def test_locked_rows_cannot_be_deleted_renamed_or_hidden(self):
        self.client.get("/api/ops/categories")
        r = self.client.delete("/api/ops/categories/series")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "locked"))
        for body in ({"title": "x"}, {"enabled": False}, {"min_items": 3}):
            r = self.client.put("/api/ops/categories/trending_series", json=body)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "locked"))
        self.assertEqual(self.slugs(), DEFAULT)
        self.assertEqual(cats.ordered_rows()[1]["title"], "Haftanın Trendleri · Diziler")

    def test_order_accepts_skeleton_and_categories_and_shows_on_boot(self):
        self.client.post("/api/ops/categories", json={"title": "Kore", "slug": "kore"})
        self.member("kore", *["s%d" % n for n in range(1, 8)])
        order = ["kore", "continue", "series", "trending_series", "movies", "trending_movies", "noteworthy_movies", "mylist"]
        r = self.client.put("/api/ops/categories-order", json={"order": order})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["slug"] for c in r.json()["categories"]], order)
        r = self.client.put("/api/ops/categories-order", json={"order": ["zzz"]})
        self.assertEqual(r.json()["error"]["code"], "unknown_slug")
        r = self.client.put("/api/ops/categories-order", json={"order": ["mylist"]})   # the rest follow
        self.assertEqual([c["slug"] for c in r.json()["categories"]][:2], ["mylist", "kore"])
        self.client.put("/api/ops/categories-order", json={"order": order})
        with patch.object(cache, "get", return_value=self.snap), patch.object(rows, "get_cache", return_value=self.snap):
            boot = self.client.get("/api/boot", params={"profile": "p1", "layout": "tv-v1"}).json()
        got = [x["id"] for x in boot["rows"]]
        self.assertEqual(got[0], "cat_kore")
        self.assertLess(got.index("series"), got.index("movies"))     # (empty rows such as trending_* are left out)
        self.assertIn("heroes", boot)


if __name__ == "__main__":
    unittest.main()
