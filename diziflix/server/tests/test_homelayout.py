"""Home screen layout (app/homelayout.py): row order and ids, the 3+3 slider, ``trend_score``, the trending / "Dikkate Değer"
(classics) / "Tüm ..." pools, ``HOME_ONLY_READY``, the catalogue's ``sort=trending|popular`` and the old row ids.

Pure-function tests use a fixed clock (``NOW``, mid 2026); pool tests use two fake sites (``library_lists`` of a temp DB).
No network.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import datetime
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import catalog, config, db, homelayout as hl, rows

NOW = 1785000000.0                                   # 2026-07-25 (a mid-year instant: no local-timezone year edge)
THIS_YEAR = datetime.date.fromtimestamp(NOW).year
DAY = 86400


def mk(i, kind="movie", added=None, year=THIS_YEAR - 5, rating=None, followers=None, backdrop=True, state="ready", air_days=None, **kw):
    """Catalogue item (default year: neither "new" nor old enough for a classic). ``added`` = days before NOW (None = no
    stamp); ``air_days`` (series) = age of the newest episode."""
    out = dict(id=i, type=kind, title=i.upper(), year=year, overview="", genres=["Dram"], rating=rating, followers=followers,
               added_at=0 if added is None else NOW - added * DAY, seasons=[],
               availability=dict(state=state, reason=None, has_trailer=False))
    if backdrop:
        out["backdrop_url"] = "https://img.test/%s.jpg" % i
    if air_days is not None:
        air = datetime.date.fromtimestamp(NOW - air_days * DAY).isoformat()
        out["seasons"] = [dict(season=1, title="1. Sezon", episodes=[
            dict(id=i + ":s1:e1", season=1, episode=1, title="Bir", air_date=air, availability=dict(state="ready"))])]
    out.update(kw)
    return out


def snap_of(*items, source="library"):
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
        for name in ("mylist_ids",):
            p = patch.object(rows, name, return_value=[])
            p.start()
            self.addCleanup(p.stop)

    def lists(self, list_id, *cids):
        for pos, cid in enumerate(cids):
            db.execute("INSERT INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)", (list_id, cid, pos))

    def pool(self, snap, row_id):
        return hl.pool(snap, row_id, "p", {}, NOW)

    def compose(self, snap, **kw):
        return hl.compose(snap, "p", {}, NOW, **kw)


# ---------------------------------------------------------------------------------------------------------------------
# trend_score
# ---------------------------------------------------------------------------------------------------------------------
class TrendScoreTests(unittest.TestCase):
    def parts(self, item, sig=None):
        return hl.score_parts(item, sig, NOW)

    def test_trending_membership_is_50_minus_position_best_site_wins_never_below_5(self):
        sig = hl.Signals(trending={"a": 0, "b": 10, "c": 80})
        self.assertEqual([self.parts(mk(i), sig)["trending"] for i in "abcd"], [50.0, 40.0, 5.0, 0.0])

    def test_other_role_lists_add_20_each_up_to_40(self):
        sig = hl.Signals(roles={"a": {"featured"}, "b": {"featured", "latest_movies"},
                                "c": {"featured", "latest_movies", "noteworthy_movies"}})
        self.assertEqual([self.parts(mk(i), sig)["roles"] for i in "abcd"], [20.0, 40.0, 40.0, 0.0])

    def test_added_at_windows(self):
        got = {d: self.parts(mk("x", added=d))["added"] for d in (0, 3, 14, 15, 45, 46, 400)}
        self.assertEqual(got, {0: 15.0, 3: 15.0, 14: 15.0, 15: 8.0, 45: 8.0, 46: 0.0, 400: 0.0})
        self.assertEqual(self.parts(mk("x", added=None))["added"], 0.0)                  # no stamp
        self.assertEqual(self.parts(mk("x", added=-5))["added"], 15.0)                  # stamp in the future = brand new

    def test_release_year_this_or_last_year(self):
        got = {y: self.parts(mk("x", year=y))["year"] for y in (THIS_YEAR + 1, THIS_YEAR, THIS_YEAR - 1, THIS_YEAR - 2, 0, None)}
        self.assertEqual(got, {THIS_YEAR + 1: 10.0, THIS_YEAR: 10.0, THIS_YEAR - 1: 10.0, THIS_YEAR - 2: 0.0, 0: 0.0, None: 0.0})

    def test_a_series_with_an_episode_of_the_last_two_weeks(self):
        self.assertEqual(self.parts(mk("s", "series", air_days=3))["episode"], 15.0)
        self.assertEqual(self.parts(mk("s", "series", air_days=14))["episode"], 15.0)
        self.assertEqual(self.parts(mk("s", "series", air_days=15))["episode"], 0.0)
        self.assertEqual(self.parts(mk("m", "movie", air_days=3))["episode"], 0.0)       # only series count
        future = mk("s", "series", air_days=-3)
        future["seasons"][0]["episodes"][0]["availability"] = dict(state="unavailable")
        self.assertEqual(self.parts(future)["episode"], 0.0)                              # announced, not out yet

    def test_rating_above_6_five_points_per_step_capped_at_20(self):
        got = {r: self.parts(mk("x", rating=r))["rating"] for r in (None, 5.9, 6, 7, 8.5, 10, 12)}
        self.assertEqual(got, {None: 0.0, 5.9: 0.0, 6: 0.0, 7: 5.0, 8.5: 12.5, 10: 20.0, 12: 20.0})

    def test_followers_are_log_scaled_up_to_10(self):
        f = lambda n: self.parts(mk("x", followers=n))["followers"]
        self.assertEqual((f(None), f(0), f(-4)), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(f(99), 10 * math.log10(100) / math.log10(10001))
        self.assertLess(f(10), f(100))
        self.assertLess(f(100), f(1000))
        self.assertEqual((f(10000), f(10 ** 9)), (10.0, 10.0))

    def test_missing_or_odd_fields_score_zero_and_never_raise(self):
        self.assertEqual(hl.trend_score({"id": "x"}, None, NOW), 0.0)
        self.assertEqual(hl.trend_score({}, hl.Signals(), NOW), 0.0)
        odd = {"id": "x", "type": "series", "year": "abc", "rating": "n/a", "followers": object(), "added_at": "?",
               "seasons": [{"episodes": None}, "junk"]}
        self.assertEqual(hl.trend_score(odd, hl.Signals(), NOW), 0.0)
        nan = mk("n", rating=float("nan"), followers=float("nan"))
        self.assertEqual(self.parts(nan)["rating"] + self.parts(nan)["followers"], 0.0)

    def test_score_is_the_sum_of_its_parts_and_deterministic(self):
        sig = hl.Signals(trending={"x": 2}, roles={"x": {"featured"}})
        item = mk("x", "series", added=2, year=THIS_YEAR, rating=8.0, followers=1000, air_days=1)
        parts = self.parts(item, sig)
        self.assertEqual(parts, {"trending": 48.0, "roles": 20.0, "added": 15.0, "year": 10.0, "episode": 15.0,
                                 "rating": 10.0, "followers": parts["followers"]})
        self.assertAlmostEqual(hl.trend_score(item, sig, NOW), sum(parts.values()))
        self.assertEqual(hl.trend_score(item, sig, NOW), hl.trend_score(item, sig, NOW))
        # `popular`: the time-based parts (added / year / episode) are left out
        self.assertAlmostEqual(hl.trend_score(item, sig, NOW, include_fresh=False), sum(parts.values()) - 15 - 10 - 15)

    def test_a_classic_gets_no_bonus_for_being_one(self):
        old = mk("old", rating=9.0, year=1960)
        self.assertTrue(hl.is_classic(old, NOW))
        self.assertEqual(hl.trend_score(old, None, NOW), 15.0)                            # the rating part only
        self.assertEqual(hl.trend_score(dict(old, year=THIS_YEAR - 5), None, NOW), 15.0)  # same as a non-classic


class SignalsTests(DbCase):
    def test_best_trending_position_roles_and_trending_lists(self):
        snap = snap_of(*[mk(i) for i in ("a", "b", "c", "d", "g")])
        self.lists("trending_siteA", "ghost", "a", "b", "c")      # `ghost` is not in the snapshot: dropped, no position
        self.lists("trending_siteB", "c", "d")
        self.lists("featured_siteA", "a")
        self.lists("latest_movies_siteB", "a", "d")
        self.lists("noteworthy_movies_siteA", "d")
        self.lists("genre_trending", "g")                          # other list families never count
        sig = hl.signals(snap)
        self.assertEqual(sig.trending, {"a": 0, "b": 1, "c": 0, "d": 1})
        self.assertEqual(sig.roles, {"a": {"featured", "latest_movies"}, "d": {"latest_movies", "noteworthy_movies"}})
        self.assertEqual([ids(l) for l in sig.trending_lists], [["a", "b", "c"], ["c", "d"]])

    def test_empty_without_a_library_snapshot(self):
        self.lists("trending_siteA", "a")
        sig = hl.signals(snap_of(mk("a"), source="mock"))
        self.assertEqual((sig.trending, sig.roles, sig.trending_lists), ({}, {}, []))
        self.assertEqual(hl.signals(SimpleNamespace(items=[mk("a")])).trending, {})


# ---------------------------------------------------------------------------------------------------------------------
# layout / rows
# ---------------------------------------------------------------------------------------------------------------------
class LayoutTests(DbCase):
    def full_library(self):
        items = [mk("s1", "series", added=1), mk("s2", "series", added=2), mk("m1", added=3), mk("m2", added=4)]
        return snap_of(*items)

    def test_default_layout_ids_and_titles(self):
        self.assertEqual(hl.layout("p"), ["continue", "trending_series", "series", "trending_movies", "noteworthy_movies",
                                          "movies", "mylist"])
        self.assertEqual(config.HOME_LAYOUT, hl.layout("anyone"))
        self.assertEqual([hl.title(None, r) for r in hl.layout("p")],
                         ["İzlemeye Devam Et", "Haftanın Trendleri · Diziler", "Tüm Diziler", "Haftanın Trendleri · Filmler",
                          "Dikkate Değer Filmler", "Tüm Filmler", "Listem"])

    def test_layout_can_be_overridden_and_drops_repeats(self):
        with patch.object(config, "HOME_LAYOUT", ["movies", "series", "movies"]):
            self.assertEqual(hl.layout("p"), ["movies", "series"])
            self.assertEqual([r["id"] for r in self.compose(self.full_library())["rows"]], ["movies", "series"])

    def test_compose_follows_the_layout_function_of_the_profile(self):
        with patch.object(hl, "layout", side_effect=lambda pid: ["movies", "continue"] if pid == "kid" else ["series"]) as lay:
            snap = self.full_library()
            self.assertEqual([r["id"] for r in hl.compose(snap, "kid", {}, NOW)["rows"]], ["movies"])   # empty continue: no row
            self.assertEqual([r["id"] for r in hl.compose(snap, "p", {}, NOW)["rows"]], ["series"])
        self.assertEqual([c.args[0] for c in lay.call_args_list], ["kid", "p"])

    def test_rows_come_in_layout_order_with_the_boot_row_fields(self):
        snap = self.full_library()
        self.lists("trending_siteA", "s2", "m2")
        out = self.compose(snap)
        self.assertEqual([r["id"] for r in out["rows"]],
                         ["trending_series", "series", "trending_movies", "movies"])   # no continue / noteworthy / mylist data
        for r in out["rows"]:
            self.assertEqual((r["loaded"], r["offset"], r["total"]), (True, 0, len(r["items"])))
            self.assertEqual(r["title"], hl.TITLES[r["id"]])
        by = {r["id"]: ids(r["items"]) for r in out["rows"]}
        self.assertEqual(by["series"], ["s1", "s2"])         # newest ADDED first
        self.assertEqual(by["movies"], ["m1", "m2"])
        self.assertEqual(by["trending_series"][0], "s2")     # the site's trending list first
        self.assertEqual(by["trending_movies"][0], "m2")

    def test_rows_page_size_is_20_total_counts_the_pool(self):
        snap = snap_of(*[mk("m%02d" % n, added=n) for n in range(30)])
        row = next(r for r in self.compose(snap)["rows"] if r["id"] == "movies")
        self.assertEqual((len(row["items"]), row["total"]), (20, 30))

    def test_empty_pools_leave_no_row(self):
        self.assertEqual(self.compose(snap_of()), {"heroes": [], "rows": []})
        only_movies = snap_of(mk("m1", added=1))
        self.assertEqual([r["id"] for r in self.compose(only_movies)["rows"]], ["trending_movies", "movies"])

    def test_mylist_is_last_and_only_when_filled(self):
        snap = self.full_library()
        with patch.object(rows, "mylist_ids", return_value=["m2", "gone", "s1"]):
            out = self.compose(snap)
        self.assertEqual(out["rows"][-1]["id"], "mylist")
        self.assertEqual(ids(out["rows"][-1]["items"]), ["m2", "s1"])
        self.assertNotIn("mylist", [r["id"] for r in self.compose(snap)["rows"]])

    def test_mylist_and_continue_keep_titles_without_a_source(self):
        gone = mk("gone", state="unavailable")
        snap = snap_of(gone)
        pmap = {"gone": dict(episode_id="gone", position=5, duration=100, pct=5, updated_at=9)}
        with patch.object(rows, "mylist_ids", return_value=["gone"]):
            out = hl.compose(snap, "p", pmap, NOW)
        self.assertEqual([r["id"] for r in out["rows"]], ["continue", "mylist"])      # the rest of the home: nothing playable

    def test_boot_wraps_compose_with_the_old_envelope_and_hero_is_the_first_hero(self):
        snap = snap_of(mk("s1", "series", added=1), mk("m1", added=2))
        out = rows.tv_boot(snap, "p", {})
        self.assertEqual((out["layout"], out["catalog_total"], out["source"]), ("tv-v1", 2, ""))
        self.assertEqual(out["hero"], out["heroes"][0])
        self.assertEqual((rows.tv_boot(snap_of(), "p", {})["hero"], rows.tv_boot(snap_of(), "p", {})["heroes"]), (None, []))


# ---------------------------------------------------------------------------------------------------------------------
# hero / slider
# ---------------------------------------------------------------------------------------------------------------------
class HeroTests(DbCase):
    def heroes(self, snap, **kw):
        return self.compose(snap, **kw)["heroes"]

    def library(self, n_series=5, n_movies=5, **kw):
        """Titles with strictly decreasing ratings (s0 > s1 > ..., m0 > m1 > ...): the score order is the index order."""
        return snap_of(*[mk("s%d" % n, "series", rating=9.0 - n * 0.5, **kw) for n in range(n_series)],
                       *[mk("m%d" % n, "movie", rating=9.0 - n * 0.5, **kw) for n in range(n_movies)])

    def test_three_series_and_three_movies_alternating_best_first(self):
        got = self.heroes(self.library())
        self.assertEqual(ids(got), ["s0", "m0", "s1", "m1", "s2", "m2"])
        self.assertEqual([h["type"] for h in got], ["series", "movie"] * 3)

    def test_a_short_type_is_topped_up_from_the_other_never_above_six(self):
        self.assertEqual(ids(self.heroes(self.library(5, 1))), ["s0", "m0", "s1", "s2", "s3", "s4"])
        self.assertEqual(ids(self.heroes(self.library(1, 5))), ["s0", "m0", "m1", "m2", "m3", "m4"])
        self.assertEqual(ids(self.heroes(self.library(2, 0))), ["s0", "s1"])
        self.assertEqual(ids(self.heroes(self.library(0, 4))), ["m0", "m1", "m2", "m3"])
        self.assertEqual(len(self.heroes(self.library(9, 9))), 6)

    def test_sizes_are_configurable(self):
        with patch.object(config, "HERO_SERIES", 1), patch.object(config, "HERO_MOVIES", 2):
            self.assertEqual(ids(self.heroes(self.library())), ["s0", "m0", "m1"])
        with patch.object(config, "HERO_SERIES", 0), patch.object(config, "HERO_MOVIES", 0):
            self.assertEqual(self.heroes(self.library()), [])

    def test_a_backdrop_is_required(self):
        snap = snap_of(mk("s0", "series", rating=9.5, backdrop=False), mk("s1", "series", rating=5.0),
                       mk("m0", rating=9.5, backdrop=False), mk("m1", rating=5.0))
        self.assertEqual(ids(self.heroes(snap)), ["s1", "m1"])

    def test_only_playable_titles_unless_the_filter_is_off(self):
        snap = snap_of(mk("s0", "series", rating=9.5, state="unavailable"), mk("s1", "series", rating=5.0),
                       mk("m0", rating=9.5, state="check_required"), mk("m1", rating=5.0))
        self.assertEqual(ids(self.heroes(snap)), ["s1", "m1"])
        with patch.object(config, "HOME_ONLY_READY", False):
            self.assertEqual(ids(self.heroes(snap)), ["s0", "m0", "s1", "m1"])

    def test_the_score_ranks_trending_over_rating_over_nothing_and_ties_break_by_rating_then_id(self):
        snap = snap_of(mk("s_hi_rating", "series", rating=9.9), mk("s_listed", "series", rating=1.0),
                       mk("b", "series"), mk("a", "series"), mk("c", "series", rating=6.5))
        self.lists("trending_siteA", "s_listed")
        got = ids(self.heroes(snap))
        self.assertEqual(got[0], "s_listed")                       # 50 trending beats 9.9 rating (19.5)
        self.assertEqual(got[1:], ["s_hi_rating", "c", "a", "b"])  # then by score; a/b tie (0): id; no movies: series top-up
        self.assertEqual(ids(self.heroes(snap_of(mk("b"), mk("a")))), ["a", "b"])   # equal scores: id

    def test_role_lists_recency_and_followers_feed_the_score(self):
        snap = snap_of(mk("plain", "series"), mk("fresh", "series", added=2), mk("featured", "series"),
                       mk("fans", "series", followers=5000))
        self.lists("featured_siteA", "featured")
        self.assertEqual(ids(self.heroes(snap)), ["featured", "fresh", "fans", "plain"])     # 20 / 15 / ~9.2 / 0

    def test_card_fields_taglines_and_my_list_flag(self):
        snap = snap_of(mk("trend", "series"), mk("newf", "movie", added=1), mk("old", "movie", year=1999, rating=7.0))
        self.lists("trending_siteA", "trend")
        with patch.object(rows, "mylist_ids", return_value=["newf"]):
            got = {h["id"]: h for h in self.heroes(snap)}
        self.assertEqual(got["trend"]["tagline"], "Haftanın Dizisi")
        self.assertEqual(got["newf"]["tagline"], "Yeni")
        self.assertEqual(got["old"]["tagline"], "Popüler")
        self.assertEqual([got[i]["in_mylist"] for i in ("trend", "newf", "old")], [False, True, False])
        self.assertTrue(all(h["logo_text"] == h["title"] and h["has_backdrop"] and h["card_kind"] == "title" for h in got.values()))
        self.lists("trending_siteB", "newf")
        with patch.object(rows, "mylist_ids", return_value=[]):
            self.assertEqual({h["id"]: h["tagline"] for h in self.heroes(snap)}["newf"], "Haftanın Filmi")

    def test_progress_overlay_reaches_the_hero_card(self):
        snap = snap_of(mk("m0"))
        prog = dict(episode_id="m0", position=30, duration=100, pct=30, updated_at=1)
        self.assertEqual(hl.compose(snap, "p", {"m0": prog}, NOW)["heroes"][0]["progress"]["pct"], 30)

    def test_a_classic_is_no_hero_unless_a_site_lists_it_as_trending(self):
        snap = snap_of(mk("new", "movie", year=THIS_YEAR, rating=6.5), mk("classic", "movie", year=1970, rating=9.5))
        self.assertEqual(ids(self.heroes(snap)), ["new"])
        self.lists("trending_siteA", "classic")
        self.assertEqual(ids(self.heroes(snap)), ["classic", "new"])


# ---------------------------------------------------------------------------------------------------------------------
# type pools: all titles / trending
# ---------------------------------------------------------------------------------------------------------------------
class TypePoolTests(DbCase):
    def test_all_titles_pools_hold_every_title_of_the_type_newest_added_first_ties_by_id(self):
        snap = snap_of(mk("m1", added=5), mk("m2", added=9), mk("m3", added=1), mk("mb", added=5), mk("s1", "series", added=7))
        self.lists("latest_movies_siteA", "m3")                 # a role list no longer restricts the row
        self.lists("trending_siteA", "m1")
        self.assertEqual(ids(self.pool(snap, "movies")), ["m3", "m1", "mb", "m2"])
        self.assertEqual(ids(self.pool(snap, "series")), ["s1"])

    def test_every_site_is_classified_into_the_same_type_pools(self):
        """Canonical items of any site are just library items: the pools never look at the site."""
        snap = snap_of(mk("a1", "series", added=2, sources=["siteA"]), mk("b1", "series", added=1, sources=["siteB"]),
                       mk("a2", added=2, sources=["siteA"]), mk("b2", added=1, sources=["siteB", "siteA"]))
        self.assertEqual(ids(self.pool(snap, "series")), ["b1", "a1"])
        self.assertEqual(ids(self.pool(snap, "movies")), ["b2", "a2"])

    def test_titles_without_a_source_stay_off_the_home_but_in_the_catalogue(self):
        snap = snap_of(mk("ok"), mk("none", state="unavailable"), mk("sus", state="check_required"))
        self.assertEqual(ids(self.pool(snap, "movies")), ["ok"])
        with patch.object(rows, "progress_map", return_value={}):
            cat = catalog.list_items(snap, "p", sort="new", limit=50)
        self.assertEqual(sorted(ids(cat["items"])), ["none", "ok", "sus"])           # catalogue / search keep them
        self.assertEqual(ids(cat["items"])[0], "ok")                                   # ready first, as before
        with patch.object(config, "HOME_ONLY_READY", False):
            self.assertEqual(sorted(ids(self.pool(snap, "movies"))), ["none", "ok", "sus"])

    def test_trending_takes_the_types_titles_of_the_lists_round_robin_and_ignores_the_other_type(self):
        snap = snap_of(*[mk(i) for i in ("a1", "a2", "a3", "b1", "b2", "shared")], mk("sa", "series"), mk("sb", "series"))
        self.lists("trending_siteA", "a1", "sa", "a2", "shared", "a3")
        self.lists("trending_siteB", "b1", "shared", "sb", "b2")
        self.assertEqual(ids(self.pool(snap, "trending_movies"))[:6], ["a1", "b1", "a2", "shared", "b2", "a3"])
        self.assertEqual(ids(self.pool(snap, "trending_series"))[:2], ["sa", "sb"])

    def test_trending_is_topped_up_with_scored_titles_of_its_type_only(self):
        snap = snap_of(mk("t1"), mk("rated", rating=8.0), mk("fresh", added=3), mk("zero"), mk("serie", "series", rating=9.9, added=1),
                       mk("feat", added=500))
        self.lists("trending_siteA", "t1")
        self.lists("featured_siteA", "feat", "serie")
        got = ids(self.pool(snap, "trending_movies"))
        self.assertEqual(got[0], "t1")                                    # the list first
        self.assertEqual(got[1:], ["feat", "fresh", "rated"])             # then by score: 20 (featured) / 15 (new) / 10 (rating)
        self.assertNotIn("zero", got)                                     # score 0 is never added
        self.assertNotIn("serie", got)                                    # another type

    def test_trending_fill_stops_at_20_but_a_longer_list_stays_whole(self):
        many = [mk("x%02d" % n, rating=8.0 + n / 100.0) for n in range(40)]
        snap = snap_of(mk("t1"), *many)
        self.lists("trending_siteA", "t1")
        got = ids(self.pool(snap, "trending_movies"))
        self.assertEqual((len(got), got[0], got[1]), (20, "t1", "x39"))   # t1 + the 19 best rated
        self.lists("trending_siteB", *["x%02d" % n for n in range(30)])
        self.assertEqual(len(self.pool(snap, "trending_movies")), 31)       # lists alone: 31 > 20, nothing added, nothing cut

    def test_trending_lists_and_fill_respect_the_playable_filter(self):
        snap = snap_of(mk("t1", state="unavailable"), mk("t2"), mk("r1", rating=9.0, state="unavailable"), mk("r2", rating=8.0))
        self.lists("trending_siteA", "t1", "t2")
        self.assertEqual(ids(self.pool(snap, "trending_movies")), ["t2", "r2"])
        with patch.object(config, "HOME_ONLY_READY", False):
            self.assertEqual(ids(self.pool(snap, "trending_movies")), ["t1", "t2", "r1", "r2"])

    def test_trending_without_any_list_or_rating_still_works_from_recency(self):
        """A single site with no trending list and no ratings: `added_at` / year order the row; no error."""
        snap = snap_of(mk("old", added=300, year=2001), mk("new", added=2, year=THIS_YEAR), mk("mid", added=30, year=2019))
        self.assertEqual(ids(self.pool(snap, "trending_movies")), ["new", "mid"])    # `old` scores 0
        self.assertEqual(self.pool(snap_of(mk("old", added=300, year=2001)), "trending_movies"), [])

    def test_classics_do_not_fill_the_trending_row(self):
        snap = snap_of(mk("recent", rating=7.0, year=THIS_YEAR), mk("classic", rating=9.4, year=1975))
        self.assertEqual(ids(self.pool(snap, "trending_movies")), ["recent"])


# ---------------------------------------------------------------------------------------------------------------------
# "Dikkate Değer Filmler" = noteworthy lists + classics
# ---------------------------------------------------------------------------------------------------------------------
class NoteworthyTests(DbCase):
    def test_classic_thresholds_rating_and_age(self):
        y = THIS_YEAR - 15
        cases = {"exact": mk("exact", rating=7.5, year=y), "rating_low": mk("rating_low", rating=7.4, year=y),
                 "too_young": mk("too_young", rating=9.0, year=y + 1), "no_year": mk("no_year", rating=9.0, year=None),
                 "year_zero": mk("year_zero", rating=9.0, year=0), "no_rating": mk("no_rating", year=1950),
                 "series": mk("series", "series", rating=9.0, year=1950), "old_fine": mk("old_fine", rating=8.0, year=1940)}
        got = {k: hl.is_classic(v, NOW) for k, v in cases.items()}
        self.assertEqual(got, {"exact": True, "rating_low": False, "too_young": False, "no_year": False, "year_zero": False,
                               "no_rating": False, "series": False, "old_fine": True})
        self.assertEqual(ids(self.pool(snap_of(*cases.values()), "noteworthy_movies")), ["old_fine", "exact"])

    def test_thresholds_are_configurable(self):
        movie = mk("m", rating=7.0, year=THIS_YEAR - 5)
        self.assertFalse(hl.is_classic(movie, NOW))
        with patch.object(config, "CLASSIC_MIN_RATING", 7.0), patch.object(config, "CLASSIC_MIN_AGE_YEARS", 5):
            self.assertTrue(hl.is_classic(movie, NOW))

    def test_list_members_first_in_list_order_then_classics_best_rated_first_no_repeats(self):
        snap = snap_of(mk("n1", rating=5.0, year=THIS_YEAR), mk("n2", rating=6.0, year=2024), mk("n3", rating=8.9, year=1980),
                       mk("c1", rating=9.1, year=1970), mk("c2", rating=8.0, year=1990), mk("c3", rating=8.0, year=1985),
                       mk("modern", rating=9.9, year=2020), mk("ser", "series", rating=9.9, year=1960))
        self.lists("noteworthy_movies_siteA", "n1", "ser", "n3")        # `n3` is also a classic: listed ONCE, at its list place
        self.lists("noteworthy_movies_siteB", "n2", "n3")
        got = ids(self.pool(snap, "noteworthy_movies"))
        self.assertEqual(got, ["n1", "n2", "n3", "c1", "c2", "c3"])      # round-robin lists (movies only), then classics
        self.assertEqual(len(got), len(set(got)))
        self.assertNotIn("modern", got)

    def test_playable_filter_applies_to_the_list_and_to_classics(self):
        snap = snap_of(mk("n1", state="unavailable"), mk("n2"), mk("c1", rating=9.0, year=1970, state="unavailable"),
                       mk("c2", rating=8.0, year=1970))
        self.lists("noteworthy_movies_siteA", "n1", "n2")
        self.assertEqual(ids(self.pool(snap, "noteworthy_movies")), ["n2", "c2"])
        with patch.object(config, "HOME_ONLY_READY", False):
            self.assertEqual(ids(self.pool(snap, "noteworthy_movies")), ["n1", "n2", "c1", "c2"])

    def test_empty_pool_means_no_row_and_the_row_sits_before_all_movies(self):
        snap = snap_of(mk("m1", added=1, rating=5.0, year=THIS_YEAR))
        self.assertNotIn("noteworthy_movies", [r["id"] for r in self.compose(snap)["rows"]])
        snap = snap_of(mk("m1", added=1), mk("c1", rating=8.0, year=1970, added=2))
        got = [r["id"] for r in self.compose(snap)["rows"]]
        self.assertEqual(got, ["trending_movies", "noteworthy_movies", "movies"])
        self.assertEqual(hl.title(snap, "noteworthy_movies"), "Dikkate Değer Filmler")

    def test_classics_score_nothing(self):
        snap = snap_of(mk("c1", rating=8.0, year=1970))
        before = hl.trend_score(snap.by_id["c1"], hl.signals(snap), NOW)
        self.assertEqual(before, 10.0)                                     # the rating part, no classic bonus
        self.assertEqual(ids(self.pool(snap, "noteworthy_movies")), ["c1"])
        self.assertEqual(hl.trend_score(snap.by_id["c1"], hl.signals(snap), NOW), before)

    def test_the_row_endpoint_pages_the_same_pool(self):
        from app.routers import rows as route
        snap = snap_of(*[mk("c%02d" % n, rating=8.0 + n / 100.0, year=1970) for n in range(25)])
        self.assertIn("noteworthy_movies", route.STATIC_ROWS)
        with patch.object(route.cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(rows.catalog_view, "select", return_value=snap), patch.object(rows, "progress_map", return_value={}):
            page = route.get_row("noteworthy_movies", profile="p", offset=20, limit=20, source="")
            first = route.get_row("noteworthy_movies", profile="p", offset=0, limit=20, source="")
        self.assertEqual((page["title"], page["total"], ids(page["items"])), ("Dikkate Değer Filmler", 25, ["c04", "c03", "c02", "c01", "c00"]))
        self.assertEqual(ids(first["items"]), ids(self.pool(snap, "noteworthy_movies"))[:20])
        boot_row = next(r for r in self.compose(snap)["rows"] if r["id"] == "noteworthy_movies")
        self.assertEqual(ids(boot_row["items"]), ids(first["items"]))


# ---------------------------------------------------------------------------------------------------------------------
# catalogue sorts, old row ids
# ---------------------------------------------------------------------------------------------------------------------
class CatalogSortTests(DbCase):
    def listing(self, snap, sort):
        with patch.object(rows, "progress_map", return_value={}), patch.object(catalog, "time", SimpleNamespace(time=lambda: NOW)):
            return catalog.list_items(snap, "p", sort=sort, limit=50)

    def snap(self):
        return snap_of(mk("listed", rating=6.0), mk("fresh", added=1, year=THIS_YEAR), mk("rated", rating=9.0, year=1990),
                       mk("fans", followers=9000, year=1990), mk("nothing", year=1990),
                       mk("none_ready", rating=10.0, state="unavailable"))

    def test_sort_trending_ranks_by_trend_score_ready_first(self):
        snap = self.snap()
        self.lists("trending_siteA", "listed")
        got = ids(self.listing(snap, "trending")["items"])
        self.assertEqual(got, ["listed", "fresh", "rated", "fans", "nothing", "none_ready"])   # 50 / 25 / 15 / ~9.7 / 0, unplayable last

    def test_sort_popular_leaves_out_the_time_based_parts(self):
        snap = self.snap()
        self.lists("trending_siteA", "listed")
        got = ids(self.listing(snap, "popular")["items"])
        self.assertEqual(got, ["listed", "rated", "fans", "fresh", "nothing", "none_ready"])   # `fresh` scores 0 without recency

    def test_existing_sorts_are_unchanged(self):
        snap = self.snap()
        self.assertEqual(ids(self.listing(snap, "title")["items"]), sorted(ids(snap.items), key=str.upper))
        self.assertEqual(ids(self.listing(snap, "new")["items"])[:1], ["fresh"])

    def test_sorts_work_for_snapshots_without_lists_and_with_odd_items(self):
        snap = SimpleNamespace(items=[dict(id="a", type="movie", title="A", year=None, overview="", genres=[])])
        for sort in ("trending", "popular"):
            self.assertEqual(ids(self.listing(snap, sort)["items"]), ["a"])


class HttpTests(unittest.TestCase):
    """Through the real app on the seeded contract library (tests/_contract_seed.py)."""

    @classmethod
    def setUpClass(cls):
        import _contract_seed as seed
        cls.seed = seed
        cls._ctx = seed.seeded_client()
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def get(self, path, status=200):
        r = self.c.get(path)
        self.assertEqual(r.status_code, status, "%s -> %s %s" % (path, r.status_code, r.text[:200]))
        return r.json()

    def test_every_boot_row_and_every_old_row_id_is_fetchable(self):
        boot = self.get("/api/boot?profile=p1&layout=tv-v1")
        for r in boot["rows"]:
            page = self.get("/api/row/%s?profile=p1&offset=0&limit=100" % r["id"])
            self.assertEqual((page["title"], page["total"]), (r["title"], r["total"]), r["id"])
        for old in ("trending", "new_series", "latest_series", "new_episodes", "latest_episodes", "new_movies", "noteworthy_movies",
                    "new", "top10", "mylist", "continue", "series", "movies", "trending_series", "trending_movies"):
            self.assertEqual(self.get("/api/row/%s?profile=p1" % old)["id"], old)
        self.get("/api/row/not_a_row?profile=p1", 404)

    def test_catalog_accepts_the_new_sorts(self):
        for sort in ("trending", "popular", "new", "year", "title"):
            body = self.get("/api/catalog?profile=p1&sort=%s&limit=50" % sort)
            self.assertEqual(body["total"], len(body["items"]), sort)
        trending = [i["id"] for i in self.get("/api/catalog?profile=p1&sort=trending&limit=50")["items"]]
        self.assertEqual(trending[:2], [self.seed.SERIES, self.seed.FILM])         # both on the trending list + featured
        self.get("/api/catalog?profile=p1&sort=bogus", 422)

    def test_the_home_hides_titles_without_a_source_the_catalogue_keeps_them(self):
        s = self.seed
        home = {i["id"] for r in self.get("/api/boot?profile=p1&layout=tv-v1")["rows"] if r["id"] not in ("continue", "mylist")
                for i in self.get("/api/row/%s?profile=p1&limit=100" % r["id"])["items"]}
        catalogue = {i["id"] for i in self.get("/api/catalog?profile=p1&limit=50")["items"]}
        for hidden in (s.METAONLY, s.DEADTRAILER, s.NOSEASONS):
            self.assertNotIn(hidden, home)
            self.assertIn(hidden, catalogue)


if __name__ == "__main__":
    unittest.main()
