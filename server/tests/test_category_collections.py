"""Category collections (yaml ``role: category`` + ``category: <slug>``, list id ``category_<slug>_<site_id>``): the yaml check of the
sandbox, the ingest lists, onboarding's re-keying / first message / hardening criteria / step view. The category registry
(``library/categories``) is faked: no network, no pi, no real data."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import db
from app.routers import onboard_sandbox as sb
from app.scraper import collections as col, onboard, onboard_pipeline as pl

import test_rows_roles as trr   # helpers only (DbCase, site_config, main_card, fake_normalize, FIELDS, OWN_PAGE)

CATS = [{"slug": "kore-dizileri", "title": "Kore Dizileri", "position": 0, "enabled": True, "min_items": 4},
        {"slug": "anime", "title": "Anime", "position": 1, "enabled": True, "min_items": 4}]


@contextmanager
def fake_categories(cats=CATS):
    slugs = {c["slug"] for c in cats}
    with patch.object(col, "known_categories", return_value=list(cats)), \
            patch.object(col, "category_exists", side_effect=lambda slug: slug in slugs):
        yield


def cat(site, slug, **extra):
    return dict({"id": col.list_id("category", site, slug), "title": slug, "path": "/" + slug, "role": "category", "category": slug}, **extra)


def check(collections, site="demo", strict=False):
    data = {"site_id": site, "base_url": f"https://{site}.example", "collections": collections}
    with fake_categories():
        return sb._check_collections(data, site, strict)


class VocabularyTests(unittest.TestCase):
    def test_category_is_a_role_but_no_home_role(self):
        self.assertIn("category", col.ROLES)
        self.assertNotIn("category", col.HOME_ROLES)
        self.assertEqual(col.list_id("category", "demo", "anime"), "category_anime_demo")
        self.assertEqual(col.list_id("trending", "demo"), "trending_demo")   # the other roles are unchanged
        self.assertIsNone(col.check_category_slug("kore-dizileri"))
        for bad in (None, "", "Anime", "kore_dizileri", "a b", "-x"):
            self.assertIsNotNone(col.check_category_slug(bad), bad)
        self.assertIsNone(col.category_of({"role": "trending", "category": "anime"}))   # other roles ignore the key


class YamlCheckTests(unittest.TestCase):
    def test_valid_category_collection(self):
        pairs, errors, _warnings = check([cat("demo", "anime"), cat("demo", "kore-dizileri")], strict=True)
        self.assertEqual(errors, [])
        self.assertEqual([e["category"] for _s, e in pairs], ["anime", "kore-dizileri"])

    def test_the_slug_is_required_and_has_a_format(self):
        for bad in (None, "Anime", "a_b"):
            spec = cat("demo", "anime")
            spec["category"] = bad
            _pairs, errors, _w = check([spec])
            self.assertTrue(any("category" in e for e in errors), (bad, errors))

    def test_the_id_must_follow_the_slug_and_the_site(self):
        _p, errors, _w = check([dict(cat("demo", "anime"), id="anime_demo")])
        self.assertTrue(any("category_anime_demo" in e for e in errors), errors)
        _p, errors, _w = check([dict(cat("demo", "anime"), id="category_anime_other")])
        self.assertTrue(any("category_anime_demo" in e for e in errors), errors)

    def test_one_collection_per_slug_but_the_same_path_may_repeat_with_other_slugs(self):
        _p, errors, _w = check([cat("demo", "anime"), cat("demo", "anime")])
        self.assertTrue(any("twice" in e for e in errors), errors)
        _p, errors, _w = check([cat("demo", "anime", path="/x"), cat("demo", "kore-dizileri", path="/x")])
        self.assertEqual(errors, [])

    def test_the_key_is_for_role_category_only(self):
        _p, errors, _w = check([{"id": "trending_demo", "title": "T", "path": "/", "role": "trending", "category": "anime"}])
        self.assertTrue(any("only for role category" in e for e in errors), errors)

    def test_unknown_slug_is_an_error_only_when_strict_and_asks_the_user(self):
        _p, errors, _w = check([cat("demo", "yerli")], strict=False)
        self.assertEqual(errors, [])   # a deleted category keeps its lists: no check outside a new site
        pairs, errors, _w = check([cat("demo", "yerli")], strict=True)
        self.assertTrue(any("yerli" in e and "ask_user" in e for e in errors), errors)
        self.assertEqual(pairs[0][1]["unknown_category"], "yerli")

    def test_the_failing_hint_names_the_unknown_category(self):
        pairs, _e, _w = check([cat("demo", "yerli")], strict=True)
        out = {"collections": [e for _s, e in pairs]}
        hint = sb._failing_hint("collections_valid_count", {"min": 3}, out)
        self.assertIn("yerli", hint)
        self.assertIn("ask_user", hint)
        self.assertIn("category:<slug>", hint)
        self.assertIn("eklensin mi", hint)

    def test_analyze_applies_the_strict_check_to_a_new_site_only(self):
        yaml_text = ("site_id: demo\nbase_url: https://demo.example\nlist_url: /\ncollections:\n"
                     "  - {id: category_yerli_demo, title: Yerli, path: /yerli, role: category, category: yerli}\n")
        with fake_categories(), patch.dict(_os.environ, {"ONBOARD_HARDEN": "1"}):
            new = sb._analyze_report(yaml_text, None, None, 1e12, collections=True, site_hint="demo", harden=True)
            edit = sb._analyze_report(yaml_text, None, None, 1e12, collections=True, site_hint="demo", harden=False)
        self.assertTrue(any("not a registered category" in e for e in new["errors"]), new["errors"])
        self.assertFalse(any("not a registered category" in e for e in edit["errors"]), edit["errors"])


def entry(role, valid=5, poster=1.0, status="ok", slug=None):
    out = {"id": f"{role}_demo", "role": role, "status": status, "valid_count": valid, "count": valid, "would_ingest": valid,
           "field_fill": {"poster_url": poster}, "normalize_ok_ratio": 1.0, "errors": [], "samples": []}
    if slug:
        out["category"] = slug
    return out


class CriteriaTests(unittest.TestCase):
    DATA = {"playback": "trailer", "collections": [], "normalize": {"type": "series"}}

    def harden(self, entries):
        pairs = [({"id": e["id"], "role": e["role"]}, e) for e in entries]
        return sb._hardening(self.DATA, {"types": {"series": 5}}, pairs, True, [], None, {}, None)[0]

    def test_a_category_collection_is_no_series_signal(self):
        self.assertEqual(self.harden([entry("category", slug="anime")])["series_signal_collection"]["ok"], False)
        self.assertTrue(self.harden([entry("category", slug="anime"), entry("trending")])["series_signal_collection"]["ok"])

    def test_a_category_collection_needs_posters_like_any_section(self):
        crit = self.harden([entry("trending"), entry("category", poster=0.3, slug="anime")])["collection_poster_fill"]
        self.assertFalse(crit["ok"])
        self.assertTrue(self.harden([entry("trending"), entry("category", poster=0.9, slug="anime")])["collection_poster_fill"]["ok"])
        hint = sb._failing_hint("collection_poster_fill", crit, {"collections": [entry("category", poster=0.3, slug="anime")]})
        self.assertIn("category", hint)

    def test_a_category_collection_has_its_own_item_count_check(self):
        crit = sb._collection_criteria([({}, entry("trending")), ({}, entry("category", valid=2, slug="anime"))])
        self.assertEqual(crit["collections_valid_count"]["value"], 2)
        self.assertFalse(crit["collections_valid_count"]["ok"])


class RekeyAndMessageTests(unittest.TestCase):
    def test_rekey_follows_the_site_id_and_keeps_the_slug(self):
        data = {"collections": [cat("old", "anime"), cat("old", "kore-dizileri"),
                                {"id": "trending_old", "title": "T", "path": "/", "role": "trending"},
                                {"id": "myown", "title": "X", "path": "/x", "role": "category", "category": "anime"}]}
        changed = onboard._rekey_collections(data, "newsite", ["old"])
        self.assertEqual(changed, [("category_anime_old", "category_anime_newsite"),
                                   ("category_kore-dizileri_old", "category_kore-dizileri_newsite"),
                                   ("trending_old", "trending_newsite")])
        self.assertEqual(data["collections"][3]["id"], "myown")   # not the id convention of the old site: left alone

    def test_rekey_without_a_known_old_site_takes_any_valid_suffix(self):
        data = {"collections": [cat("zzz", "anime")]}
        onboard._rekey_collections(data, "newsite", [])
        self.assertEqual(data["collections"][0]["id"], "category_anime_newsite")

    def test_rekey_leaves_a_category_without_a_valid_slug_alone(self):
        data = {"collections": [{"id": "category__old", "path": "/", "role": "category"}]}
        self.assertEqual(onboard._rekey_collections(data, "newsite", ["old"]), [])

    def test_first_message_carries_the_categories(self):
        with fake_categories():
            line = onboard.categories_line()
            message = onboard.first_message("https://demo.example", "anime: /anime", line)
        self.assertEqual(line, "Mevcut kategoriler: kore-dizileri (Kore Dizileri), anime (Anime)")
        self.assertEqual(message.splitlines(), ["/skill:diziflix-site-onboarding https://demo.example", line, "Kullanıcı notu: anime: /anime"])
        with fake_categories([]):
            self.assertEqual(onboard.categories_line(), "Mevcut kategoriler: yok")


class StepViewTests(unittest.TestCase):
    def test_the_app_view_lists_a_category_row(self):
        report = {"collections": [entry("trending", 12), entry("category", 9, slug="kore-dizileri")], "ingest": {"item_limit": 30}}
        with fake_categories():
            rows = pl._app(report)["rows"]
            home = pl._step_home(report, [])
        self.assertIn({"key": "category_kore-dizileri", "title": "Kore Dizileri", "count": 9, "from": "collection"}, rows)
        self.assertIn("Kore Dizileri", home["summary"])

    def test_an_unregistered_category_shows_its_slug(self):
        with fake_categories([]):
            self.assertEqual(pl._category_title({"category": "yerli"}), "yerli")


class IngestTests(trr.DbCase):
    def setUp(self):
        super().setUp()
        self.case = trr.IngestCollectionTests("run_ingest")
        self.case.temp = self.temp
        trr.IngestCollectionTests.setUp(self.case)
        self.addCleanup(self.case.doCleanups)

    def ingest(self, site, collections, main):
        result, _fetched = self.case.run_ingest(site, collections, main)
        return result

    def test_the_category_list_is_written_under_category_slug_site(self):
        main = [trr.main_card("m1", "Main One"), trr.main_card("m2", "Main Two")]
        collections = [dict(cat("siteA", "anime"), path="/", row_selector="li.row", fields=trr.FIELDS)]
        with fake_categories():
            result = self.ingest("siteA", collections, main)
        self.assertEqual(self.case.members("category_anime_siteA"), ["Own One", "Own Two"])
        self.assertEqual({c["id"] for c in result["collections"]}, {"category_anime_siteA", "source_siteA"})

    def test_a_wrong_id_in_the_yaml_is_replaced_by_the_convention(self):
        main = [trr.main_card("m1", "Main One")]
        collections = [dict(cat("siteA", "anime"), id="whatever", path="/")]
        with fake_categories():
            self.ingest("siteA", collections, main)
        self.assertEqual(self.case.members("category_anime_siteA"), ["Main One"])
        self.assertEqual(db.query("SELECT 1 FROM library_lists WHERE list_id='whatever'"), [])

    def test_a_deleted_category_still_gets_its_list_at_run_time(self):
        main = [trr.main_card("m1", "Main One")]
        with fake_categories([]):   # nothing registered any more
            self.ingest("siteA", [cat("siteA", "anime", path="/")], main)
        self.assertEqual(self.case.members("category_anime_siteA"), ["Main One"])

    def test_two_categories_may_share_a_title_and_a_missing_slug_is_skipped(self):
        main = [trr.main_card("m1", "Main One"), trr.main_card("m2", "Main Two")]
        collections = [cat("siteA", "anime", path="/"), cat("siteA", "kore-dizileri", path="/"),
                       {"id": "category__siteA", "title": "x", "path": "/", "role": "category"}]
        with fake_categories():
            result = self.ingest("siteA", collections, main)
        self.assertEqual(self.case.members("category_anime_siteA"), ["Main One", "Main Two"])
        self.assertEqual(self.case.members("category_kore-dizileri_siteA"), ["Main One", "Main Two"])
        self.assertEqual(db.query("SELECT 1 FROM library_lists WHERE list_id LIKE 'category!_!_%' ESCAPE '!'"), [])
        self.assertNotIn("category__siteA", {c["id"] for c in result["collections"]})

    def test_item_limit_applies(self):
        main = [trr.main_card(f"m{i}", f"Main {i}") for i in range(5)]
        real = trr.site_config

        def limited(site, collections):
            cfg = real(site, collections)
            cfg.data["item_limit"] = 2
            return cfg
        with fake_categories(), patch.object(trr, "site_config", limited):
            self.ingest("siteA", [cat("siteA", "anime", path="/")], main)
        self.assertEqual(len(self.case.members("category_anime_siteA")), 2)


if __name__ == "__main__":
    unittest.main()
