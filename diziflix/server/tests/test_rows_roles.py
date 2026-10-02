"""Home rows are role based: every site's ``<role>_<site_id>`` list (``library_lists``) feeds the row.

Covers ``rows._role_lists`` (round-robin merge across sites, de-dup by canonical id, no ``genre_*``/``source_*``
mix-in), the rows built on it (trending / noteworthy / new episodes / movies / hero) and the ingest side
(``parses_main_list`` / ``load_collection`` / the ``featured_<site>`` list). Network-free: fake ``fetch.page``,
fake ``run_site``, temp DB.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import config, db, rows
from app.library import ingest as ingest_module, normalize as nz
from app.scraper import config as scfg, state
from app.scraper.collections import HOME_ROLES, list_id
from app.scraper.runner import RunResult

READY = dict(state="ready", reason=None, has_trailer=False)


def item(i, kind="movie", added=1, state_="ready"):
    return dict(id=i, type=kind, title=i, year=2025, overview="", genres=["Dram"], added_at=added,
                availability=dict(READY, state=state_))


def series(i, episode=2, added=1):
    out = item(i, "series", added)
    out["seasons"] = [dict(season=1, title="1. Sezon", episodes=[
        dict(id=f"{i}:s1:e{episode}", season=1, episode=episode, title=f"Bölüm {episode}", air_date=None,
             availability=dict(READY))])]
    return out


def snapshot(*items, source="library"):
    return SimpleNamespace(items=list(items), by_id={i["id"]: i for i in items}, source=source)


def ids(items):
    return [i["id"] for i in items]


class DbCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()

    def lists(self, list_id_, *cids):
        for pos, cid in enumerate(cids):
            db.execute("INSERT INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)", (list_id_, cid, pos))


class RoleListTests(DbCase):
    def test_trending_is_a_round_robin_of_every_sites_list_without_repeats(self):
        snap = snapshot(*[item(i) for i in ("a1", "a2", "a3", "shared", "b1", "b2", "g1", "s1", "n1")])
        self.lists("trending_siteA", "ghost", "a1", "a2", "a3", "shared")  # `ghost` is not in the snapshot
        self.lists("trending_siteB", "b1", "shared", "b2")
        self.lists("genre_trending", "g1")        # other list families never mix in
        self.lists("source_siteA", "s1")
        self.lists("latest_movies_siteA", "n1")
        got = rows._role_lists(snap, "trending")
        self.assertEqual(ids(got), ["a1", "b1", "a2", "shared", "a3", "b2"])
        self.assertEqual(ids(rows.row_pool(snap, "trending", "", {})), ids(got))
        self.assertEqual(ids(rows._role_lists(snap, "latest_movies")), ["n1"])

    def test_only_home_roles_and_library_snapshots_are_read(self):
        snap = snapshot(item("a1"))
        self.lists("trending_siteA", "a1")
        self.lists("genre_siteA", "a1")
        self.assertEqual(rows._role_lists(snap, "genre"), [])
        self.assertEqual(rows._role_lists(snap, "catalog"), [])
        self.assertEqual(rows._role_lists(snapshot(item("a1"), source="mock"), "trending"), [])
        self.assertEqual(rows._role_lists(SimpleNamespace(by_id={"a1": item("a1")}), "trending"), [])
        self.assertEqual(set(HOME_ROLES), {"trending", "latest_episodes", "latest_series", "latest_movies",
                                           "noteworthy_movies", "featured"})

    def test_one_site_is_exactly_its_list_in_position_order(self):
        """Old behaviour (fixed `*_yabancidizi` ids): the list as stored, minus ids the snapshot does not know."""
        snap = snapshot(*[item(i) for i in ("x", "y", "z", "w")])
        self.lists("trending_yabancidizi", "z", "gone", "x", "w", "y")
        self.lists("noteworthy_movies_yabancidizi", "y", "x")
        legacy = [snap.by_id[r["canonical_id"]] for r in db.query(
            "SELECT canonical_id FROM library_lists WHERE list_id=? ORDER BY position,canonical_id",
            ("trending_yabancidizi",)) if r["canonical_id"] in snap.by_id]
        self.assertEqual(rows.row_pool(snap, "trending", "", {}), legacy)
        self.assertEqual(ids(legacy), ["z", "x", "w", "y"])
        self.assertEqual(ids(rows.row_pool(snap, "noteworthy_movies", "", {})), ["y", "x"])

    def test_source_view_keeps_only_that_sites_items(self):
        """``catalog_view.select`` narrows ``by_id``: the other site's cards drop out of the merged list."""
        snap = snapshot(item("a1"), item("a2"), item("b1"))
        self.lists("trending_siteA", "a1", "a2")
        self.lists("trending_siteB", "b1")
        view = copy.copy(snap)
        view.by_id = {i: snap.by_id[i] for i in ("a1", "a2")}
        self.assertEqual(ids(rows._role_lists(view, "trending")), ["a1", "a2"])


class RowTests(DbCase):
    def test_new_episodes_merge_sites_one_card_per_series(self):
        snap = snapshot(series("sa1", 5), series("sa2", 3), series("sb1", 7), series("shared", 9),
                        item("film"), series("pending"), series("old"))
        snap.by_id["pending"]["seasons"][0]["episodes"][0]["availability"] = dict(READY, state="unavailable")
        self.lists("latest_episodes_siteA", "sa1", "film", "shared", "sa2")
        self.lists("latest_episodes_siteB", "sb1", "shared", "pending")
        entries = rows.row_pool(snap, "new_episodes", "", {})
        self.assertEqual(ids(entries), ["sa1", "sb1", "shared", "sa2"])   # film + not-ready episode left out
        self.assertEqual([e["_card"]["episode"]["id"] for e in entries], ["sa1:s1:e5", "sb1:s1:e7", "shared:s1:e9", "sa2:s1:e3"])
        self.assertEqual(ids(rows.row_pool(snap, "latest_episodes", "", {})), ids(entries))   # alias row id

    def test_new_series_row_merges_sites_series_only_one_card_per_production(self):
        snap = snapshot(series("sa1"), series("sa2"), series("sb1"), series("shared"), item("film"),
                        series("ep_only"), series("other"))
        self.lists("latest_series_siteA", "sa1", "film", "shared", "sa2", "ghost")   # a film is no series card
        self.lists("latest_series_siteB", "sb1", "shared")
        self.lists("latest_episodes_siteA", "ep_only")      # the episodes lists feed another row
        self.lists("trending_siteA", "other")
        entries = rows.row_pool(snap, "new_series", "", {})
        self.assertEqual(ids(entries), ["sa1", "sb1", "shared", "sa2"])
        self.assertEqual(ids(rows.row_pool(snap, "latest_series", "", {})), ids(entries))   # role name = alias
        self.assertFalse(any("_card" in e for e in entries))  # plain series cards, not episode cards
        self.assertEqual(ids(rows.row_pool(snap, "new_episodes", "", {})), ["ep_only"])
        self.assertEqual(rows.row_title(snap, "new_series"), "Yeni Eklenen Diziler")
        self.assertEqual(rows.row_title(snap, "latest_series"), "Yeni Eklenen Diziler")
        self.assertEqual(rows.row_pool(snapshot(series("sa1"), source="mock"), "new_series", "", {}), [])

    def test_new_series_and_new_episodes_rows_are_no_home_rows_any_more_but_stay_fetchable(self):
        snap = snapshot(item("t1"), series("n1"), series("e1"), series("s9"), item("m1"))
        self.lists("trending_siteA", "t1")
        self.lists("latest_episodes_siteA", "e1")
        self.lists("latest_series_siteA", "n1", "film_not_in_snapshot")
        with patch.object(rows, "mylist_ids", return_value=[]):
            out = rows.tv_boot(snap, "p", {})
        home_ids = [r["id"] for r in out["rows"]]
        self.assertNotIn("new_series", home_ids)
        self.assertNotIn("new_episodes", home_ids)
        self.assertNotIn("trending", home_ids)
        self.assertEqual(home_ids, ["trending_series", "series", "trending_movies", "movies"])
        self.assertEqual(ids(rows.row_pool(snap, "new_series", "", {})), ["n1"])
        self.assertEqual(ids(rows.row_pool(snap, "new_episodes", "", {})), ["e1"])
        self.assertEqual(ids(rows.row_pool(snap, "trending", "", {})), ["t1"])
        self.assertEqual(rows.row_title(snap, "trending"), "Trendler")

    def test_new_series_row_endpoint_pages_and_is_known(self):
        from app.routers import rows as route
        snap = snapshot(*[series("n%d" % n) for n in range(25)])
        self.lists("latest_series_siteA", *["n%d" % n for n in range(25)])
        self.assertIn("new_series", route.STATIC_ROWS)
        self.assertIn("latest_series", route.STATIC_ROWS)
        with patch.object(route.cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(rows.catalog_view, "select", return_value=snap), \
                patch.object(rows, "progress_map", return_value={}):
            page = route.get_row("new_series", profile="p", offset=20, limit=20, source="")
        self.assertEqual((page["id"], page["title"], page["total"]), ("new_series", "Yeni Eklenen Diziler", 25))
        self.assertEqual([i["id"] for i in page["items"]], ["n20", "n21", "n22", "n23", "n24"])

    def test_movies_row_is_every_movie_newest_added_first_whatever_the_latest_movies_lists_say(self):
        """Home layout (homelayout.py): `movies` = "Tüm Filmler" = ALL movies; the `latest_movies_<site>` lists are only a
        scoring signal now (they used to restrict this row to the lists' titles)."""
        snap = snapshot(item("m1", added=5), item("m2", added=9), item("m3", added=1), item("m4", added=3), series("s1"))
        self.lists("trending_siteA", "m1")
        self.assertEqual(ids(rows.row_pool(snap, "movies", "", {})), ["m2", "m1", "m4", "m3"])
        self.lists("latest_movies_siteA", "m3", "m1")
        self.lists("latest_movies_siteB", "m4")
        self.assertEqual(ids(rows.row_pool(snap, "movies", "", {})), ["m2", "m1", "m4", "m3"])   # unchanged by the lists
        self.assertEqual(ids(rows.row_pool(snap, "new_movies", "", {})), ["m2", "m1", "m4", "m3"])   # DATA-CONTRACT name
        self.assertEqual(rows.row_title(snap, "new_movies"), "Tüm Filmler")

    def test_boot_rows_and_heroes_come_from_all_sites(self):
        """Trending lists of EVERY site feed `trending_movies` (round-robin); the other role lists (latest_*, featured,
        noteworthy) now only score titles for the slider; `noteworthy_movies` keeps its own row."""
        def art(i, **kw):
            return dict(i, backdrop_url="https://img.test/%s.jpg" % i["id"], **kw)
        snap = snapshot(art(item("t1", added=3)), art(item("t2", added=2)), series("e1"), art(item("f1", added=9)),
                        art(item("f2", added=8)), item("n1", added=1))
        self.lists("trending_siteA", "t1")
        self.lists("trending_siteB", "t2")
        self.lists("latest_episodes_siteB", "e1")
        self.lists("noteworthy_movies_siteA", "n1")
        self.lists("featured_siteA", "f1")
        self.lists("featured_siteB", "f2")
        with patch.object(rows, "mylist_ids", return_value=[]):
            out = rows.tv_boot(snap, "p", {})
        by_row = {r["id"]: [i["id"] for i in r["items"]] for r in out["rows"]}
        self.assertEqual(by_row["trending_movies"][:2], ["t1", "t2"])
        self.assertEqual(by_row["noteworthy_movies"], ["n1"])
        self.assertEqual(by_row["trending_series"], ["e1"])     # no trending series: filled by score (latest_episodes member)
        self.assertNotIn("new_episodes", by_row)
        # slider = every backdrop title of the library (films only here), best score first: trending t1/t2 lead
        self.assertEqual([h["id"] for h in out["heroes"]][:2], ["t1", "t2"])
        self.assertEqual(set(h["id"] for h in out["heroes"]), {"t1", "t2", "f1", "f2"})
        self.assertEqual(out["hero"]["id"], "t1")

    def test_hero_needs_a_backdrop_and_playable_titles(self):
        no_art = item("noart", added=7)
        art = dict(item("art", added=1), backdrop_url="https://img.test/art.jpg")
        snap = snapshot(no_art, art)
        with patch.object(rows, "mylist_ids", return_value=[]):
            self.assertEqual([h["id"] for h in rows.tv_boot(snap, "p", {})["heroes"]], ["art"])
            snap_none = snapshot(no_art)
            out = rows.tv_boot(snap_none, "p", {})
        self.assertEqual((out["hero"], out["heroes"]), (None, []))


# ---------------------------------------------------------------------------------------------------------------
# ingest: which collections are parsed on their own, and the featured_<site> list
# ---------------------------------------------------------------------------------------------------------------
LIST_ROW = '<li class="row"><a href="{href}"><h5>{title}</h5><img src="/p/{slug}.jpg"></a></li>'
OWN_PAGE = "<ul>" + "".join(LIST_ROW.format(href=f"/film/{s}", title=t, slug=s)
                           for s, t in (("own-one", "Own One"), ("own-two", "Own Two"))) + "</ul>"
FIELDS = {"title": {"selector": "h5"}, "detail_url": {"selector": "a", "attr": "href"},
          "poster_url": {"selector": "img", "attr": "src"}}


def fake_normalize(raw):
    slug = (raw.get("detail_url") or "").rstrip("/").rsplit("/", 1)[-1]
    if not raw.get("title") or not slug:
        return None
    return {"source_key": slug, "type": "movie", "title": raw["title"], "original_title": None,
            "year": raw.get("year") or 2024, "overview": "", "genres": [], "rating": None, "runtime": None,
            "country": None, "followers": None, "cast": [], "poster_url": None, "backdrop_url": None,
            "trailer_url": None, "source_url": "https://%s.test/film/%s" % ("site", slug)}


def main_card(slug, title, **extra):
    return dict({"title": title, "detail_url": f"/film/{slug}", "poster_url": f"/p/{slug}.jpg", "year": 2024}, **extra)


def site_config(site, collections):
    data = {"base_url": f"https://{site.lower()}.test", "list_url": "/", "schema": "HomepageItem", "version": 1,
            "list": {"row_selector": "li.main", "fields": copy.deepcopy(FIELDS)}, "collections": collections}
    return scfg.SiteConfig(site, data, f"/nonexistent/{site}.yaml")


class IngestCollectionTests(DbCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_ingest(self, site, collections, main_items, page=OWN_PAGE):
        cfg = site_config(site, collections)
        fetched = []

        def fake_page(_cfg, url, **_kw):
            fetched.append(url)
            return page

        with patch.dict(nz._REGISTRY, {site: fake_normalize}), \
                patch.object(ingest_module, "run_site", return_value=RunResult(site, items=main_items, drift={"drift": False})), \
                patch.object(scfg, "load_site", return_value=cfg), \
                patch.object(ingest_module.fetch, "page", side_effect=fake_page), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False):
            result = ingest_module._ingest_source(site)
        self.assertIsNone(result["error"])
        return result, fetched

    def members(self, list_id_):
        return [r["title"] for r in db.query(
            "SELECT i.title FROM library_lists l JOIN library_items i ON i.id=l.canonical_id "
            "WHERE l.list_id=? ORDER BY l.position", (list_id_,))]

    def test_parses_main_list_only_without_own_selector_or_fields(self):
        cfg = site_config("siteA", [])
        base = {"id": "x", "role": "trending", "path": "/"}
        self.assertTrue(ingest_module.parses_main_list(cfg, base))
        self.assertTrue(ingest_module.parses_main_list(cfg, {**base, "path": "https://sitea.test/"}))
        self.assertTrue(ingest_module.parses_main_list(cfg, {**base, "row_selector": "li.main", "fields": FIELDS}))  # same as main
        self.assertFalse(ingest_module.parses_main_list(cfg, {**base, "row_selector": "li.other"}))
        self.assertFalse(ingest_module.parses_main_list(cfg, {**base, "fields": {"title": {"selector": "h2"}}}))
        self.assertFalse(ingest_module.parses_main_list(cfg, {**base, "path": "/trends"}))

    def test_load_collection_filters_the_main_result_or_parses_its_own_page(self):
        cfg = site_config("siteA", [])
        main = [main_card("m1", "Main One"), main_card("m2", "Main Two", featured="poster-media")]
        with patch.object(ingest_module.fetch, "page", return_value=OWN_PAGE) as page:
            plain = ingest_module.load_collection(
                cfg, {"id": "x", "path": "/", "required_fields": ["featured"]}, main, 20)
            self.assertEqual([i["title"] for i in plain], ["Main Two"])
            page.assert_not_called()
            own = ingest_module.load_collection(
                cfg, {"id": "y", "path": "/", "row_selector": "li.row", "fields": FIELDS}, main, 20)
            self.assertEqual([i["title"] for i in own], ["Own One", "Own Two"])
            self.assertEqual(page.call_count, 1)
            self.assertEqual(page.call_args.args[1], "https://sitea.test/")

    def test_collection_with_own_selector_on_the_main_path_is_parsed_separately(self):
        main = [main_card("m1", "Main One"), main_card("m2", "Main Two")]
        collections = [
            {"id": list_id("trending", "siteA"), "title": "Trendler", "path": "/", "role": "trending",
             "row_selector": "li.row", "fields": FIELDS},
            {"id": list_id("latest_movies", "siteA"), "title": "Yeni", "path": "/", "role": "latest_movies"},
        ]
        result, fetched = self.run_ingest("siteA", collections, main)
        self.assertEqual(fetched, ["https://sitea.test/"])           # only the override fetched a page
        self.assertEqual(self.members("trending_siteA"), ["Own One", "Own Two"])   # NOT filtered from the main cards
        self.assertEqual(self.members("latest_movies_siteA"), ["Main One", "Main Two"])
        self.assertEqual({c["id"]: c["count"] for c in result["collections"]},
                         {"trending_siteA": 2, "latest_movies_siteA": 2})

    def test_featured_role_collection_is_written_as_featured_site_for_any_site(self):
        main = [main_card("m1", "Main One"), main_card("m2", "Main Two", featured="hero")]
        collections = [{"id": "hero_row", "title": "Öne çıkanlar", "path": "/", "role": "featured",
                        "required_fields": ["featured"]}]
        self.run_ingest("siteB", collections, main)
        self.assertEqual(self.members("featured_siteB"), ["Main Two"])
        self.assertEqual(db.query("SELECT 1 FROM library_lists WHERE list_id='hero_row'"), [])

    def test_hero_cards_of_the_main_list_still_feed_featured_without_a_featured_collection(self):
        """yabancidizi's behaviour (no `role: featured` collection, hero cards carry `featured: poster-media`)."""
        main = [main_card("m1", "Main One"), main_card("m2", "Main Two", featured="poster-media")]
        self.run_ingest("siteA", [], main)
        self.assertEqual(self.members("featured_siteA"), ["Main Two"])
        self.assertEqual(self.members("source_siteA"), ["Main One", "Main Two"])

    def test_ingested_lists_feed_the_rows_of_both_sites(self):
        collections = lambda site: [{"id": list_id("trending", site), "title": "T", "path": "/", "role": "trending"}]
        self.run_ingest("siteA", collections("siteA"), [main_card("a1", "A One"), main_card("a2", "A Two")])
        self.run_ingest("siteB", collections("siteB"), [main_card("b1", "B One"), main_card("a1", "A One")])
        by_id = {r["id"]: dict(item(r["id"]), title=r["title"]) for r in db.query("SELECT id,title FROM library_items")}
        got = rows.row_pool(SimpleNamespace(by_id=by_id, items=list(by_id.values()), source="library"), "trending", "", {})
        # siteA: A One, A Two; siteB: B One, A One (the same production: one card)
        self.assertEqual([i["title"] for i in got], ["A One", "B One", "A Two"])


if __name__ == "__main__":
    unittest.main()
