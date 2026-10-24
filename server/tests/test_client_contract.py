"""Server <-> client contract: for every endpoint, the fields the web client (client/js, packaged for Tizen and Android WebView) READ.

A seeded throw-away library (tests/_contract_seed.py: film / series with seasons + specials / series without episodes /
poster-less film / dead or missing trailer / broken and announced episodes) is served by the real FastAPI app through
TestClient; responses are validated against tests/_contract_schema.py (types, nulls, enums, additive fields allowed).
Also: episode cards (`continue`, `new_episodes`), old-client compatibility (`new`, aliases), error envelopes,
image endpoints, mutation flows and the censored sample files under docs/api-samples/. No network.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import unittest
from unittest.mock import patch

import _contract_schema as S
import _contract_seed as seed
from _contract_seed import (BROKENLAST, CLASSIC, DEADTRAILER, FILM, FILM2, FILM3, METAONLY, NOPOSTER, NOSEASONS, ONEEP, SERIES,
                            SERIES2, SERIES3, UNAIRED)


class Base(unittest.TestCase):
    """One seeded server per class (read-only tests); mutating tests use ``Fresh`` (one server per test)."""

    @classmethod
    def setUpClass(cls):
        cls._ctx = seed.seeded_client()
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def get(self, path, status=200):
        r = self.c.get(path)
        self.assertEqual(r.status_code, status, "%s -> %s %s" % (path, r.status_code, r.text[:200]))
        return r.json()

    def post(self, path, body, status=200):
        r = self.c.post(path, json=body)
        self.assertEqual(r.status_code, status, "%s -> %s %s" % (path, r.status_code, r.text[:200]))
        return r.json()

    def conforms(self, body, spec, what=""):
        self.assertEqual(S.problems(body, spec), [], what)

    def home(self, profile="p1"):
        """The tv-v1 home (``rows.tv_boot``: heroes + rows, decided by ``homelayout``); ``/api/boot`` itself was removed 2026-10-24."""
        from app import cache, rows
        return rows.tv_boot(cache.get(), profile, rows.progress_map(profile))

    def check_cards(self, items, where):
        """ITEM schema + card identity rules for a list of cards (rows, catalog, search, similar, mylist)."""
        self.conforms(items, S.lst(S.ITEM), where)
        keys = [i["card_key"] for i in items]
        self.assertEqual(len(keys), len(set(keys)), where + ": card_key must be unique within a row")
        for i in items:
            if i["card_kind"] == "episode":
                self.conforms(i, S.EPISODE_CARD, where + ":" + i["id"])
                self.assertEqual(i["card_key"], "episode:" + i["episode_id"])
                self.assertTrue(i["episode_id"].startswith(i["id"] + ":s"), "episode id belongs to the production id")
                self.assertEqual(i["primary_action"]["episode_id"], i["episode_id"])
                self.assertEqual(i["primary_action"]["item_id"], i["id"])
            else:
                self.assertEqual(i["card_key"], "title:" + i["id"])
                self.assertNotIn("episode_id", i)
                self.assertNotIn("primary_action", i)


class BootTests(Base):
    def test_tv_boot_shape_and_row_ids(self):
        b = self.home()
        self.conforms(b, S.BOOT_TV)
        self.assertEqual([r["id"] for r in b["rows"]], ["continue", "new_episodes", "latest_movies", "trending_series",
                                                         "trending_movies", "must_watch", "series", "movies"])
        self.assertEqual([r["title"] for r in b["rows"]],
                         ["İzlemeye Devam Et", "Yeni Bölümler", "Yeni Filmler", "Trend Diziler", "Trend Filmler",
                          "İzlenmesi Gereken Dizi ve Filmler", "Tüm Diziler", "Tüm Filmler"])
        self.assertTrue(all(r["loaded"] is True and r["items"] for r in b["rows"]), "tv-v1 rows arrive loaded and non-empty")
        self.assertEqual(b["catalog_total"], 14)
        self.assertEqual(b["hero"]["id"], b["heroes"][0]["id"])
        # slider: the best 3 series + 3 films WITH a backdrop, alternating series / film (not playable ones never)
        self.assertEqual([h["type"] for h in b["heroes"]], ["movie", "series"] + ["series", "movie"] * 2)
        self.assertEqual({h["id"] for h in b["heroes"]}, {SERIES, SERIES2, SERIES3, FILM, FILM2, FILM3})
        self.assertEqual([h["id"] for h in b["heroes"]][:2], [FILM, SERIES], "the site's featured list leads, in its order")
        self.assertTrue(all(h["has_backdrop"] and h["availability"]["state"] == "ready" for h in b["heroes"]))
        for r in b["rows"]:
            self.check_cards(r["items"], r["id"])

    def test_home_rows_hold_only_playable_titles_of_their_type(self):
        rows = {r["id"]: [i["id"] for i in r["items"]] for r in self.home()["rows"]}
        self.assertEqual(set(rows["series"]), {SERIES, SERIES2, SERIES3, ONEEP, UNAIRED, BROKENLAST})   # NOSEASONS: trailer only
        self.assertEqual(set(rows["movies"]), {FILM, FILM2, FILM3, NOPOSTER, CLASSIC})                  # no source: not on the home
        self.assertEqual(rows["trending_series"][0], SERIES)
        self.assertEqual(rows["trending_movies"][0], FILM)
        self.assertEqual(rows["must_watch"], [FILM2, CLASSIC], "the lists' titles, then the classics")
        self.assertNotIn(METAONLY, rows["must_watch"], "a classic without a source is not playable")
        self.assertNotIn(CLASSIC, rows["trending_movies"][1:2], "classics do not fill the trending row ahead of newer titles")
        # the catalogue still lists the titles the home leaves out
        everything = {i["id"] for i in self.get("/api/catalog?profile=p1&limit=50")["items"]}
        self.assertTrue({NOSEASONS, DEADTRAILER, METAONLY} <= everything)

    def test_every_boot_row_can_be_paged_through_the_row_endpoint(self):
        for r in self.home()["rows"]:
            page = self.get("/api/row/%s?profile=p1&offset=0&limit=20" % r["id"])
            self.conforms(page, S.ROW_PAGE, r["id"])
            self.check_cards(page["items"], r["id"])
            self.assertEqual(page["title"], r["title"])
            self.assertEqual([i["card_key"] for i in page["items"]], [i["card_key"] for i in r["items"]])

    def test_row_paging_window(self):
        page = self.get("/api/row/series?profile=p1&offset=1&limit=2")
        self.assertEqual((page["offset"], page["limit"], len(page["items"])), (1, 2, 2))
        self.assertGreater(page["total"], 2)
        self.get("/api/row/no_such_row?profile=p1", 404)

    def test_hero_less_boot_of_an_empty_library_is_valid(self):
        from types import SimpleNamespace
        from app import rows
        snap = SimpleNamespace(items=[], by_id={}, source="library", episodes={})
        b = rows.tv_boot(snap, "p1", {})
        self.conforms(b, S.BOOT_TV)
        self.assertEqual((b["hero"], b["heroes"], b["rows"], b["catalog_total"]), (None, [], [], 0))


class EpisodeCardTests(Base):
    def rows(self):
        """Home rows by id, plus the rows the home no longer shows (`new_episodes`, `new_series`) fetched by their id."""
        out = {r["id"]: r for r in self.home()["rows"]}
        for rid in ("new_episodes", "new_series"):
            out[rid] = self.get("/api/row/%s?profile=p1&offset=0&limit=20" % rid)
        return out

    def test_continue_row_mixes_a_movie_title_card_and_an_episode_card(self):
        cont = self.rows()["continue"]["items"]
        self.assertEqual([(i["id"], i["card_kind"]) for i in cont], [(FILM, "title"), (SERIES, "episode")])
        movie, ep = cont
        self.assertEqual(movie["progress"]["pct"], 46)
        self.assertEqual(ep["episode_id"], SERIES + ":s2:e1")
        self.assertEqual(ep["episode_label"], "S02 B01", "a generic '1. Bölüm' title is not repeated")
        self.assertEqual(ep["progress"], {"episode_id": SERIES + ":s2:e1", "position": 600, "duration": 2700, "pct": 22})
        self.assertEqual(ep["primary_action"], {"kind": "resume_episode", "item_id": SERIES, "episode_id": SERIES + ":s2:e1",
                                                "position": 600})
        self.assertEqual(ep["availability"]["state"], "ready")
        self.assertTrue(ep["availability"]["has_trailer"], "the trailer belongs to the production")

    def test_new_episodes_row_is_one_ready_newest_episode_per_series_in_source_order(self):
        row = self.rows()["new_episodes"]
        self.assertEqual(row["title"], "Yeni Bölümler")
        cards = row["items"]
        self.assertEqual([(i["id"], i["episode_id"]) for i in cards],
                         [(SERIES, SERIES + ":s3:e1"), (ONEEP, ONEEP + ":s1:e5"), (UNAIRED, UNAIRED + ":s1:e1")])
        self.assertEqual([i["episode_label"] for i in cards], ["S03 B01 · Yankı", "S01 B05 · Hat Kopuyor", "S01 B01 · Pilot"])
        self.assertTrue(all(i["primary_action"]["kind"] == "play_episode" and i["progress"] is None for i in cards))
        ids = {i["id"] for i in cards}
        self.assertNotIn(NOSEASONS, ids, "a series without any episode has no episode card")
        self.assertNotIn(BROKENLAST, ids, "newest episode without a live source: not a new-content card")

    def test_new_series_row_is_title_cards_of_series_only_in_source_order(self):
        row = self.rows()["new_series"]
        self.assertEqual(row["title"], "Yeni Eklenen Diziler")
        self.assertEqual([(i["id"], i["type"], i["card_kind"]) for i in row["items"]],
                         [(ONEEP, "series", "title"), (SERIES, "series", "title")])   # the film of the list is left out
        self.assertEqual(row["total"], 2)
        self.check_cards(row["items"], "new_series")
        page = self.get("/api/row/new_series?profile=p1&offset=1&limit=1")
        self.conforms(page, S.ROW_PAGE)
        self.assertEqual(([i["id"] for i in page["items"]], page["total"], page["title"]),
                         ([SERIES], 2, "Yeni Eklenen Diziler"))
        alias = self.get("/api/row/latest_series?profile=p1")
        self.assertEqual([i["id"] for i in alias["items"]], [ONEEP, SERIES])

    def test_announced_future_episode_is_not_a_new_episode(self):
        # UNAIRED's S01E02 (2099, disabled source) is not published: the card shows the newest published one
        self.assertEqual(self.rows()["new_episodes"]["items"][2]["episode_id"], UNAIRED + ":s1:e1")

    def test_episode_card_targets_exist_in_the_series_detail(self):
        for card in self.rows()["new_episodes"]["items"] + [self.rows()["continue"]["items"][1]]:
            d = self.get("/api/detail/%s?profile=p1" % card["id"])
            ids = {e["id"]: e for s in d["seasons"] for e in s["episodes"]}
            self.assertIn(card["episode_id"], ids)
            self.assertEqual(ids[card["episode_id"]]["availability"]["state"], card["availability"]["state"])

    def test_previous_episodes_row_id_stays_an_alias(self):
        a = self.get("/api/row/latest_episodes?profile=p1")
        b = self.get("/api/row/new_episodes?profile=p1")
        self.assertEqual([i["card_key"] for i in a["items"]], [i["card_key"] for i in b["items"]])
        self.assertEqual([i["id"] for i in self.get("/api/row/new_movies?profile=p1")["items"]],
                         [i["id"] for i in self.get("/api/row/movies?profile=p1")["items"]])

    def test_tv_client_open_rules_hold_for_these_cards(self):
        """home.js cardParams: episode cards and continue series cards open the series page (id = production id)
        focused on the episode; everything else opens the plain page."""
        for r in self.rows().values():
            for i in r["items"]:
                episode = i["card_kind"] == "episode" and i.get("episode_id")
                if r["id"] == "continue" and i["type"] == "series":
                    self.assertTrue(episode or (i["progress"] and i["progress"]["episode_id"]))
                self.assertTrue(i["id"] and " " not in i["id"])


class CompatTests(Base):
    """Old clients (before the episode cards) must keep working: `new` row, unchanged keys."""

    def test_generic_new_row_endpoint_is_alive(self):
        page = self.get("/api/row/new?profile=p1")
        self.conforms(page, S.ROW_PAGE)
        self.assertEqual(page["title"], "Yeni Eklenenler")

    def test_source_parameter_is_accepted_but_ignored_by_new_clients(self):
        self.get("/api/row/series?profile=p1&source=")
        self.get("/api/search?q=ka&profile=p1&source=")
        self.get("/api/sections?profile=p1&source=")
        self.assertEqual(self.get("/api/sections?profile=p1&source=zzz", 400)["error"]["code"], "bad_request")


class DetailTests(Base):
    def detail(self, iid, profile="p1"):
        d = self.get("/api/detail/%s?profile=%s" % (iid, profile))
        self.conforms(d, S.DETAIL, iid)
        self.check_cards(d["similar"], iid + ".similar")
        for s in d["seasons"]:
            self.assertEqual(s["episode_count"], len(s["episodes"]))
            self.assertEqual(s["has_poster"], False)
            self.assertIn("/img/%s/portrait" % iid, s["poster_url"], "no season poster: poster_url is the series poster")
            for e in s["episodes"]:
                self.assertEqual(e["still"], e["still_url"])
        return d

    def test_film_with_source_and_trailer(self):
        d = self.detail(FILM)
        self.assertEqual((d["type"], d["seasons"], d["runtime"], d["director"]), ("movie", [], 108, None))
        self.assertEqual(d["resume"], {"episode_id": FILM, "position": 3000})
        self.assertEqual(d["progress"]["pct"], 46)
        self.assertTrue(d["in_mylist"])
        self.assertEqual([a["kind"] for a in d["actions"]], ["resume_movie", "play_trailer"])
        self.assertEqual(d["actions"][0]["position"], 3000)
        self.assertEqual([n["id"] for n in d["source_names"]], d["sources"], "source_names = display names of sources")
        self.assertEqual(self.detail(FILM, "p2")["actions"][0], {"kind": "play_movie", "item_id": FILM})

    def test_film_without_poster_overview_year_rating(self):
        d = self.detail(NOPOSTER)
        self.assertEqual((d["year"], d["rating"], d["overview"], d["genres"], d["runtime"], d["has_backdrop"]),
                         (None, None, "", [], 0, False))
        self.assertEqual(d["runtime"], 0, "0 = unknown (never null)")
        self.assertEqual([a["kind"] for a in d["actions"]], ["play_movie"])

    def test_film_with_dead_trailer_and_no_full_source(self):
        d = self.detail(DEADTRAILER)
        self.assertEqual(d["availability"], {"state": "unavailable", "reason": "no_video_source", "has_trailer": False})
        self.assertEqual((d["playback"], d["actions"]), ("unavailable", []))

    def test_catalogue_only_film(self):
        d = self.detail(METAONLY)
        self.assertEqual((d["availability"]["state"], d["availability"]["reason"], d["playback"]),
                         ("unavailable", "no_video_source", "unavailable"))
        self.assertEqual(d["actions"], [])

    def test_series_with_seasons_specials_and_every_episode_state(self):
        d = self.detail(SERIES)
        self.assertEqual([s["season"] for s in d["seasons"]], [0, 1, 2, 3], "season 0 (specials) is listed first: clients sort")
        eps = {e["id"]: e for s in d["seasons"] for e in s["episodes"]}
        self.assertEqual(eps[SERIES + ":s2:e2"]["availability"], {"state": "unavailable", "reason": "sources_unavailable",
                                                                  "has_trailer": False})
        self.assertEqual(eps[SERIES + ":s2:e3"]["availability"]["state"], "check_required")
        self.assertTrue(eps[SERIES + ":s1:e1"]["has_still"])
        self.assertFalse(eps[SERIES + ":s1:e2"]["has_still"], "no real still: the client draws its own placeholder")
        self.assertEqual(eps[SERIES + ":s1:e1"]["runtime"], 44)
        self.assertEqual(eps[SERIES + ":s1:e2"]["runtime"], 0, "0 = unknown")
        self.assertEqual(eps[SERIES + ":s2:e1"]["progress"], {"position": 600, "duration": 2700, "pct": 22})
        self.assertIsNone(eps[SERIES + ":s1:e1"]["progress"])
        self.assertEqual(d["resume"], {"episode_id": SERIES + ":s2:e1", "position": 600})
        self.assertEqual(d["actions"][0], {"kind": "resume_episode", "item_id": SERIES, "episode_id": SERIES + ":s2:e1",
                                           "position": 600})
        self.assertEqual(d["actions"][1]["kind"], "play_trailer")
        # a profile without progress starts at the first PLAYABLE regular episode, never at the special
        d2 = self.detail(SERIES, "p2")
        self.assertEqual(d2["resume"], {"episode_id": SERIES + ":s1:e1", "position": 0})
        self.assertEqual(d2["actions"][0], {"kind": "play_episode", "item_id": SERIES, "episode_id": SERIES + ":s1:e1"})

    def test_series_without_any_episode_inventory(self):
        d = self.detail(NOSEASONS)
        self.assertEqual((d["type"], d["seasons"], d["playback"]), ("series", [], "trailer"))
        self.assertEqual(d["availability"], {"state": "unavailable", "reason": "no_video_source", "has_trailer": True})
        self.assertEqual([a["kind"] for a in d["actions"]], ["play_trailer"], "no episode is invented")

    def test_single_episode_series_and_announced_episode(self):
        d = self.detail(ONEEP)
        self.assertEqual([s["episode_count"] for s in d["seasons"]], [1])
        d = self.detail(UNAIRED)
        future = [e for s in d["seasons"] for e in s["episodes"] if e["air_date"] == "2099-01-01"][0]
        self.assertEqual(future["availability"]["state"], "unavailable")   # client: air_date in the future + not ready = "Yakında"
        self.assertEqual(d["actions"][0]["episode_id"], UNAIRED + ":s1:e1")

    def test_series_whose_newest_episode_is_broken(self):
        d = self.detail(BROKENLAST)
        self.assertEqual([e["availability"]["state"] for s in d["seasons"] for e in s["episodes"]], ["ready", "unavailable"])
        self.assertEqual(d["actions"][0]["episode_id"], BROKENLAST + ":s1:e1")

    def test_unknown_id_is_a_json_404(self):
        e = self.get("/api/detail/yok?profile=p1", 404)
        self.conforms(e, S.ERROR)
        self.assertEqual(e["error"]["code"], "not_found")

    def test_detail_without_or_with_unknown_profile_still_answers(self):
        self.assertEqual(self.get("/api/detail/%s" % FILM)["progress"], None)
        self.assertIsNone(self.get("/api/detail/%s?profile=nobody" % FILM)["progress"])


class TrailerCheckTests(Base):
    """availability.trailer (ok | dead | unknown): live verification of the YouTube trailer, mocked."""

    def test_detail_reports_the_trailer_verdict_and_hides_a_dead_one(self):
        from app import config
        from app.library import trailer_check as tc
        verdict = {"bbbbbbbbbbb": (tc.DEAD, "not_found"), "aaaaaaaaaaa": (tc.OK, "ok")}
        with patch.object(config, "TRAILER_CHECK", True), patch.object(tc, "after_change", None), \
                patch.object(tc, "probe", side_effect=lambda vid: verdict.get(vid, (tc.UNKNOWN, "http_500"))):
            ok = self.get("/api/detail/%s?profile=p1" % FILM)
            dead = self.get("/api/detail/%s?profile=p1" % DEADTRAILER)
            unknown = self.get("/api/detail/%s?profile=p1" % NOSEASONS)
            trailer_streams = self.get("/api/streams/%s?kind=trailer" % DEADTRAILER)
        self.assertEqual((ok["availability"]["trailer"], ok["availability"]["has_trailer"]), ("ok", True))
        self.assertIn("play_trailer", [a["kind"] for a in ok["actions"]])
        self.assertEqual((dead["availability"]["trailer"], dead["availability"]["has_trailer"]), ("dead", False))
        self.assertEqual((dead["playback"], dead["actions"]), ("unavailable", []))
        self.assertEqual(unknown["availability"]["trailer"], "unknown")
        self.assertEqual(unknown["availability"]["has_trailer"], True, "unverifiable = assumed alive")
        self.assertEqual(trailer_streams["streams"], [], "a dead trailer resolves to no streams (client shows the no-source text)")
        self.conforms(dead, S.DETAIL)


class StreamsTests(Base):
    def test_film_video_streams(self):
        s = self.get("/api/streams/%s?profile=p1&kind=video" % FILM)
        self.conforms(s, S.STREAMS)
        self.assertEqual(len(s["streams"]), 1)
        st = s["streams"][0]
        self.assertEqual((st["type"], st["kind"], st["source"]), ("hls", "movie", "yabancidizi"))
        self.assertEqual(len(st["attempt_token"]), 32, "playback-report wants a 32 char token")
        self.assertIn("·", st["label"], "label = '<provider> · <quality>', shown as is in the source menu")
        self.assertEqual(s["resume_position"], 3000)

    def test_trailer_kind_returns_only_trailers_and_video_kind_never_falls_back_to_them(self):
        t = self.get("/api/streams/%s?kind=trailer" % FILM)
        self.conforms(t, S.STREAMS)
        self.assertEqual([(x["kind"], x["type"]) for x in t["streams"]], [("trailer", "embed")])
        self.assertEqual(t["resume_position"], 0, "trailers never resume")
        self.assertEqual(self.get("/api/streams/%s?kind=video" % DEADTRAILER)["streams"], [])
        self.assertEqual(self.get("/api/streams/%s?kind=video" % METAONLY)["streams"], [])

    def test_episode_streams(self):
        s = self.get("/api/streams/%s?profile=p1&episode=%s:s1:e1&kind=video" % (SERIES, SERIES))
        self.conforms(s, S.STREAMS)
        self.assertEqual([x["kind"] for x in s["streams"]], ["episode"])
        s = self.get("/api/streams/%s?profile=p1&episode=%s:s2:e1&kind=video" % (SERIES, SERIES))
        self.assertEqual(s["resume_position"], 600, "resume position of the addressed episode")

    def test_episode_without_a_live_source_returns_an_empty_list_not_an_error(self):
        s = self.get("/api/streams/%s?episode=%s:s2:e2&kind=video" % (SERIES, SERIES))
        self.conforms(s, S.STREAMS)
        self.assertEqual(s["streams"], [])

    def test_errors(self):
        self.assertEqual(self.get("/api/streams/yok", 404)["error"]["code"], "not_found")
        self.assertEqual(self.get("/api/streams/%s?episode=%s:s9:e9" % (SERIES, SERIES), 404)["error"]["code"], "not_found")
        self.assertEqual(self.get("/api/streams/%s?episode=%s:s1:e1" % (SERIES, ONEEP), 404)["error"]["code"], "not_found")
        self.conforms(self.get("/api/streams/%s?kind=bogus" % FILM, 422), S.ERROR)


class ListEndpointTests(Base):
    def test_catalog(self):
        c = self.get("/api/catalog?profile=p1&type=series&limit=20")
        self.conforms(c, S.CATALOG)
        self.check_cards(c["items"], "catalog")
        self.assertTrue(all(i["type"] == "series" for i in c["items"]))
        self.assertEqual(c["total"], len(c["items"]))
        self.assertEqual(c["years"], sorted(c["years"], reverse=True))
        genre = c["genres"][0]["id"]
        filtered = self.get("/api/catalog?profile=p1&genre=%s&availability=ready&sort=title&limit=20" % genre)
        self.assertTrue(all(i["availability"]["state"] == "ready" for i in filtered["items"]))
        self.assertEqual(self.get("/api/catalog?profile=p1&mine=true")["total"], 1)
        self.assertEqual(self.get("/api/catalog?profile=p1&q=SESSIZ%20LIMAN")["items"][0]["id"], NOPOSTER)
        self.conforms(self.get("/api/catalog?profile=p1&limit=999", 422), S.ERROR)

    def test_catalog_query_the_tv_client_sends(self):
        # api.catalog drops '' values but sends mine=false, sort=new, offset, limit
        c = self.get("/api/catalog?profile=p1&sort=new&offset=0&mine=false&limit=20&type=movie")
        self.conforms(c, S.CATALOG)
        self.assertEqual({i["type"] for i in c["items"]}, {"movie"})
        # a filter that matches nothing is a valid empty page (client shows the empty text)
        empty = self.get("/api/catalog?profile=p1&year=1801")
        self.conforms(empty, S.CATALOG)
        self.assertEqual((empty["items"], empty["total"]), ([], 0))

    def test_search_local_path(self):
        s = self.get("/api/search?q=ka&profile=p1&limit=3")
        self.conforms(s, S.SEARCH)
        self.assertEqual((s["remote"], s["remote_error"], s["remote_sites"]), (False, None, {}))
        self.check_cards(s["items"], "search")
        self.assertLessEqual(len(s["items"]), 3)
        self.assertEqual(self.get("/api/search?q=&profile=p1")["items"], [])

    def test_search_remote_path_success_and_failure(self):
        from app.library import search_all
        from app.scraper import site_search
        search_all.reset()
        self.addCleanup(search_all.reset)
        live = (patch.object(site_search, "search_sites", return_value=["yabancidizi"], create=True),
                patch.object(site_search, "supports", return_value=True, create=True))
        for p in live:
            p.start()
            self.addCleanup(p.stop)
        with patch.object(site_search, "search", return_value=[{"title": "Sessiz Liman"}]), \
                patch.object(search_all, "ingest_discovered_items", return_value=[NOPOSTER]):
            ok = self.get("/api/search?q=sessiz&profile=p1")
        self.conforms(ok, S.SEARCH)
        self.assertEqual((ok["remote"], ok["remote_error"], ok["items"][0]["id"]), (True, None, NOPOSTER))
        self.assertEqual(list(ok["remote_sites"]), ["yabancidizi"])
        self.conforms(ok["remote_sites"]["yabancidizi"], S.REMOTE_SITE)
        self.check_cards(ok["items"], "search.remote")
        with patch.object(site_search, "search", side_effect=RuntimeError("HTTP 503")):
            down = self.get("/api/search?q=sessiz&profile=p1")
        self.conforms(down, S.SEARCH)
        self.assertEqual((down["remote"], down["remote_error"]), (True, "HTTP 503"))
        self.assertEqual(down["remote_sites"]["yabancidizi"]["error"], "HTTP 503")
        self.assertEqual(down["items"][0]["id"], NOPOSTER, "local matches still come back when the live source is down")

    def test_mylist_get(self):
        m = self.get("/api/mylist?profile=p1")
        self.conforms(m, S.MYLIST)
        self.assertEqual([i["id"] for i in m["items"]], [FILM])
        self.get("/api/mylist", 400)

    def test_health_and_profiles(self):
        self.conforms(self.get("/api/health"), S.HEALTH)
        p = self.get("/api/profiles")
        self.conforms(p, S.PROFILES)
        self.assertEqual([x["id"] for x in p["profiles"]][:2], ["p1", "p2"])
        self.assertTrue(p["profiles"][0]["avatar"].startswith("/img/avatar/"))
        a = self.get("/api/avatars")
        self.conforms(a, S.AVATARS)
        self.assertGreaterEqual(len(a["avatars"]), 16)


class SourceFinderContractTests(Base):
    """Finder-state endpoint (additive, library/sourcefinder.py) and the optional ``finder`` stream field."""

    def test_finder_state_conforms(self):
        st = self.get("/api/source-finder/%s" % FILM)
        self.conforms(st, S.SOURCE_FINDER)
        self.assertEqual((st["state"], [s["name"] for s in st["steps"]]), ("found", ["retry"]))
        self.conforms(self.get("/api/source-finder/%s?episode=%s:s1:e1" % (SERIES, SERIES)), S.SOURCE_FINDER)
        self.assertEqual(self.get("/api/source-finder/%s" % SERIES)["state"], "idle")
        self.get("/api/source-finder/yok", 404)

    def test_streams_answer_has_no_finder_field_while_something_plays(self):
        self.assertNotIn("finder", self.get("/api/streams/%s?profile=p1&kind=video" % FILM))

    def test_an_episode_without_a_source_may_carry_the_finder_field(self):
        from unittest.mock import patch
        from app.library import sourcefinder
        with patch.object(sourcefinder, "request", return_value={"state": "searching", "started": True, "reason": ""}):
            s = self.get("/api/streams/%s?profile=p1&episode=%s:s2:e2&kind=video" % (SERIES, SERIES))   # no live source
        self.assertEqual(s["streams"], [])
        self.conforms(s, S.STREAMS)
        self.conforms(s["finder"], S.FINDER_STATE)
        self.assertEqual(s["finder"], {"state": "searching"})

class ErrorEnvelopeTests(Base):
    def test_every_error_class_uses_the_envelope(self):
        for path, status in (("/api/sections", 400), ("/api/sections?profile=nobody", 400), ("/api/detail/yok", 404),
                             ("/api/row/x?profile=p1", 404), ("/api/catalog?profile=p1&limit=0", 422),
                             ("/api/sections?profile=p1&fail=1", 500), ("/img/yok/card", 404), ("/img/%s/bogus" % FILM, 404)):
            body = self.get(path, status)
            self.conforms(body, S.ERROR, path)

    def test_client_debug_delay_flag_is_accepted(self):
        self.get("/api/health?delay=1")


class ImageTests(Base):
    def image(self, path, status=200):
        r = self.c.get(path)
        self.assertEqual(r.status_code, status, path)
        if status == 200:
            self.assertEqual(r.headers["content-type"], "image/jpeg", path)
            self.assertTrue(r.content[:2] == b"\xff\xd8", path)
        return r

    def test_every_url_a_card_or_episode_carries_is_servable(self):
        boot = self.home()
        urls = set()
        for r in boot["rows"]:
            for i in r["items"]:
                urls.update((i["card"], i["portrait"], i["backdrop"]))
                if i["card_kind"] == "episode":
                    urls.add(i["still_url"])
        d = self.get("/api/detail/%s?profile=p1" % SERIES)
        for s in d["seasons"]:
            urls.add(s["poster_url"])
            urls.update(e["still_url"] for e in s["episodes"])
        self.assertGreater(len(urls), 20)
        for url in sorted(urls):
            self.image(url)

    def test_missing_artwork_serves_a_generated_placeholder(self):
        for kind in ("card", "portrait", "backdrop", "still"):
            self.image("/img/%s/%s?w=300&h=450" % (NOPOSTER, kind))

    def test_season_poster_id_and_episode_ids_with_colons(self):
        self.image("/img/%s:s1/portrait?w=300&h=450" % SERIES)
        self.image("/img/%s:s1:e1/still?w=320&h=180" % SERIES)

    def test_avatar_and_caching_headers(self):
        r = self.image("/img/avatar/a1?w=200&h=200")
        self.assertIn("max-age", r.headers["cache-control"])
        again = self.c.get("/img/avatar/a1?w=200&h=200", headers={"If-None-Match": r.headers["etag"]})
        self.assertEqual(again.status_code, 304)


class Fresh(unittest.TestCase):
    """Mutating flows: a new seeded server per test."""

    def setUp(self):
        self._ctx = seed.seeded_client()
        self.c = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    def call(self, method, path, status=200, **kw):
        r = getattr(self.c, method)(path, **kw)
        self.assertEqual(r.status_code, status, "%s %s -> %s %s" % (method, path, r.status_code, r.text[:200]))
        return r.json()

    def continue_cards(self):
        from app import cache, rows as rws
        rows = rws.tv_boot(cache.get(), "p1", rws.progress_map("p1"))["rows"]
        return next(r["items"] for r in rows if r["id"] == "continue")

    def test_progress_updates_the_continue_row_with_episode_cards(self):
        ack = self.call("post", "/api/progress", json={"profile": "p1", "item_id": ONEEP, "episode_id": ONEEP + ":s1:e5",
                                                        "position": 20, "duration": 100})
        S_ = S.problems(ack, S.PROGRESS_ACK)
        self.assertEqual(S_, [])
        self.assertEqual((ack["watched"], ack["next_episode"]), (False, None))
        first = self.continue_cards()[0]
        self.assertEqual((first["id"], first["episode_id"], first["progress"]["pct"], first["primary_action"]["kind"]),
                         (ONEEP, ONEEP + ":s1:e5", 20, "resume_episode"))

    def test_finishing_an_episode_queues_the_next_one_as_a_play_card(self):
        ack = self.call("post", "/api/progress", json={"profile": "p1", "item_id": SERIES, "episode_id": SERIES + ":s1:e1",
                                                        "position": 2650, "duration": 2700})
        self.assertEqual((ack["watched"], ack["next_episode"]), (True, SERIES + ":s1:e2"))
        card = next(c for c in self.continue_cards() if c["id"] == SERIES)
        self.assertEqual((card["episode_id"], card["primary_action"]["kind"]), (SERIES + ":s1:e2", "play_episode"))
        self.assertEqual(S.problems(card, S.ITEM), [])
        d = self.call("get", "/api/detail/%s?profile=p1" % SERIES)
        self.assertEqual(d["actions"][0]["kind"], "play_episode")
        self.assertEqual(d["resume"]["episode_id"], SERIES + ":s1:e2")

    def test_progress_validation(self):
        self.call("post", "/api/progress", 404, json={"profile": "p1", "item_id": "yok", "position": 1, "duration": 2})
        self.call("post", "/api/progress", 404, json={"profile": "p1", "item_id": SERIES, "episode_id": ONEEP + ":s1:e5",
                                                       "position": 1, "duration": 2})
        self.call("post", "/api/progress", 400, json={"profile": "nobody", "item_id": SERIES, "position": 1, "duration": 2})
        self.call("post", "/api/progress", 422, json={"profile": "p1", "item_id": SERIES, "position": -1})
        # a movie reports episode_id == item_id (player.js: episodeId || itemId)
        self.call("post", "/api/progress", json={"profile": "p1", "item_id": NOPOSTER, "episode_id": NOPOSTER,
                                                  "position": 10, "duration": 90})

    def test_mylist_round_trip(self):
        r = self.call("post", "/api/mylist", 201, json={"profile": "p1", "item_id": SERIES})
        self.assertEqual(S.problems(r, S.MYLIST_MUTATION), [])
        self.assertTrue(self.call("get", "/api/detail/%s?profile=p1" % SERIES)["in_mylist"])
        ids = [i["id"] for i in self.call("get", "/api/mylist?profile=p1")["items"]]
        self.assertEqual(ids, [SERIES, FILM], "newest first")
        r = self.call("delete", "/api/mylist/%s?profile=p1" % SERIES)
        self.assertEqual((r["ok"], r["in_mylist"]), (True, False))
        self.assertFalse(self.call("get", "/api/detail/%s?profile=p1" % SERIES)["in_mylist"])
        self.call("post", "/api/mylist", 404, json={"profile": "p1", "item_id": "yok"})
        self.call("post", "/api/mylist", 400, json={"profile": "nobody", "item_id": SERIES})

    def test_profile_crud_and_avatar_seeds(self):
        p = self.call("post", "/api/profiles", 201, json={"name": "Yeni", "is_kids": True, "avatar_seed": "a5"})
        self.assertEqual(S.problems(p, S.PROFILE), [])
        self.assertEqual((p["avatar_seed"], p["is_kids"]), ("a5", True))
        self.assertEqual(p["avatar"], "/img/avatar/a5?w=200&h=200")
        auto = self.call("post", "/api/profiles", 201, json={"name": "Otomatik"})
        self.assertTrue(auto["avatar_seed"])
        u = self.call("put", "/api/profiles/" + p["id"], json={"name": "Yeni2", "avatar_seed": "a7", "is_kids": False})
        self.assertEqual((u["name"], u["avatar_seed"], u["is_kids"]), ("Yeni2", "a7", False))
        self.call("put", "/api/profiles/" + p["id"], 400, json={"avatar_seed": "nope"})
        self.call("put", "/api/profiles/yok", 404, json={"name": "x"})
        self.assertEqual(self.call("delete", "/api/profiles/" + p["id"]), {"ok": True})
        # the client keeps the deleted id in localStorage until it notices: the answer is a readable 400
        e = self.call("get", "/api/sections?profile=%s" % p["id"], 400)
        self.assertEqual(S.problems(e, S.ERROR), [])

    def test_guest_profile_is_permanent_and_stores_nothing(self):
        ps = self.call("get", "/api/profiles")["profiles"]
        self.assertEqual((ps[-1]["id"], ps[-1]["guest"]), ("guest", True), "guest listed last")
        self.assertFalse(any(p["guest"] for p in ps[:-1]))
        self.assertEqual(S.problems(self.call("put", "/api/profiles/guest", 403, json={"name": "x"}), S.ERROR), [])
        self.call("delete", "/api/profiles/guest", 403)
        r = self.call("post", "/api/progress", json={"profile": "guest", "item_id": FILM, "position": 600, "duration": 6000})
        self.assertEqual((r["ok"], r["next_episode"]), (True, None))
        self.assertIsNone(self.call("get", "/api/detail/%s?profile=guest" % FILM)["progress"])
        m = self.call("post", "/api/mylist", 201, json={"profile": "guest", "item_id": FILM})
        self.assertEqual((m["ok"], m["in_mylist"]), (True, False))
        self.assertEqual(self.call("get", "/api/mylist?profile=guest")["items"], [])

    def test_playback_report_accepts_everything_the_tv_client_sends(self):
        s = self.call("get", "/api/streams/%s?profile=p1&kind=video" % FILM)
        token = s["streams"][0]["attempt_token"]
        for code in ("unsupported", "decode", "autoplay", "aborted", "offline"):   # device side: never counted
            fresh = self.call("get", "/api/streams/%s?kind=video" % FILM)["streams"][0]["attempt_token"]
            self.call("post", "/api/playback-report", json={"attempt_token": fresh, "event": "failure", "code": code,
                                                             "engine": "avplay"})
        self.call("post", "/api/playback-report", json={"attempt_token": token, "event": "success", "code": "", "engine": "html5"})
        self.call("post", "/api/playback-report", 400, json={"attempt_token": "0" * 32, "event": "success", "code": "", "engine": ""})
        self.call("post", "/api/playback-report", 422, json={"attempt_token": token, "event": "success", "code": "bogus"})
        self.call("post", "/api/playback-report", 422, json={"attempt_token": "short", "event": "success"})


class SectionTests(Base):
    """The new home's menu (`/api/sections`) and section pages (`/api/section/<id>`); the boot stays as it was."""

    def test_menu_order_and_soon_sections(self):
        b = self.get("/api/sections?profile=p1")
        self.conforms(b, S.SECTIONS)
        self.assertEqual([x["id"] for x in b["sections"]], ["featured", "series", "movies", "live_tv", "anime", "dvr"])
        soon = {x["id"]: x for x in b["sections"] if x["status"] == "soon"}
        self.assertEqual(sorted(soon), ["dvr"])   # live_tv is ready (channels, tests/test_live_tv.py)
        self.assertTrue(all(not x["heroes"] for x in soon.values()))
        self.get("/api/section/no_such?profile=p1", 404)

    def test_section_rows_are_filtered_by_type_and_page_through_the_row_endpoint(self):
        boot = self.home()
        for sid, kind, ids in (("series", "series", ["featured_series", "new_episodes", "trending_series", "series"]),
                               ("movies", "movie", ["featured_movies", "latest_movies", "trending_movies", "movies"])):
            sec = self.get("/api/section/%s?profile=p1" % sid)
            self.conforms(sec, S.SECTION, sid)
            self.assertEqual([r["id"] for r in sec["rows"]], ids)
            for r in sec["rows"]:
                self.assertEqual({i["type"] for i in r["items"]}, {kind}, sid + ":" + r["id"])
                self.assertEqual([i["card_key"] for i in self.get("/api/row/%s?profile=p1&limit=100" % r["id"])["items"]],
                                 [i["card_key"] for i in r["items"]])
            self.assertTrue(sec["heroes"] and all(h["type"] == kind for h in sec["heroes"]))
        feat = self.get("/api/section/featured?profile=p1")
        self.assertEqual([r["id"] for r in feat["rows"]],
                         [r["id"] for r in boot["rows"] if r["id"] not in ("continue", "series", "movies")])
        self.assertTrue({"series", "movies"} <= {r["id"] for r in boot["rows"]}, "the boot keeps Tüm Diziler/Filmler")
        menu = {x["id"]: x["heroes"] for x in self.get("/api/sections?profile=p1")["sections"]}
        self.assertEqual(feat["heroes"], menu["featured"], "a section page shows the menu's vitrin")
        self.assertEqual(len({h["id"] for h in feat["heroes"]}), len(feat["heroes"]))
        self.assertEqual(boot, self.home(), "the home is unchanged by the sections")

    def _vitrins(self, featured, n_extra):
        """Vitrins of a fake library: ``featured`` = one site's featured list (ids), plus ``n_extra`` titles per kind."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import homelayout as hl

        def it(cid, kind, rating):
            return {"id": cid, "type": "movie" if kind == "movie" else "series", "rating": rating,
                    "backdrop_url": "/img/b", "availability": {"state": "ready"}}
        kinds = {"s": "series", "m": "movie", "a": "anime"}
        items = [it(c, kinds[c[0]], 9.0 - k * 0.1) for k, c in enumerate(featured)]
        items += [it("%sx%d" % (p, k), kinds[p], 5.0 - k * 0.1) for p in kinds for k in range(n_extra)]
        items = list({i["id"]: i for i in items}.values())
        snap = SimpleNamespace(source="library", items=items, by_id={i["id"]: i for i in items})
        anime = [i for i in items if i["id"][0] == "a"]
        lists = {"featured": {"featured_x": [snap.by_id[c] for c in featured]}}
        with patch.object(hl.rows, "_role_by_list", lambda _s, role: lists.get(role, {})), \
                patch.object(hl.categories, "member_lists", lambda _s, slug: [anime] if slug == "animeler" else []), \
                patch.object(hl, "_hero_card", lambda _c, item, _p=None: {"id": item["id"], "type": item["type"]}):
            return {k: [h["id"] for h in v] for k, v in hl._vitrins(hl.Context(snap)).items()}

    def test_featured_vitrin_cycles_series_movie_anime(self):
        """Öne Çıkanlar: dizi, film, anime, ... each from the sites' featured first (site order), then by score."""
        v = self._vitrins(["s1", "s2", "s3", "a1", "m1"], 8)
        self.assertEqual(v["featured"], ["s1", "m1", "a1", "s2", "mx0", "ax0"])

    def test_section_vitrins_skip_the_featured_vitrin_and_never_run_empty(self):
        v = self._vitrins(["s1", "s2", "s3", "a1", "m1"], 8)
        for sid, p in (("series", "s"), ("movies", "m"), ("anime", "a")):
            self.assertEqual(len(v[sid]), 6, sid)
            self.assertTrue(all(c[0] == p for c in v[sid]), sid)
            self.assertFalse(set(v[sid]) & set(v["featured"]), sid)
        self.assertEqual(v["series"][0], "s3", "featured first, minus the Öne Çıkanlar ones")
        v = self._vitrins(["s1", "m1", "a1"], 1)   # too few: repeats instead of an empty vitrin
        self.assertEqual(sorted(v["series"]), ["s1", "sx0"])
        self.assertTrue(v["movies"] and v["anime"])

    def test_anime_category_row_is_the_lists_then_best_rated_library_anime(self):
        """``cat_animeler``: the sites' ``category_animeler_*`` lists first (list order), then the other anime series
        of the library best rated first up to TREND_TARGET; no repeats, unplayable left out, min_items on the filled row."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import homelayout as hl

        def it(cid, rating, state="ready", kind="series"):
            return {"id": cid, "type": kind, "rating": rating, "availability": {"state": state}}
        listed = [it("l1", 1.0), it("l2", 9.9)]
        extra = [it("x1", 5.0), it("x2", 8.0), it("x3", 7.0), it("bad", 9.5, "broken"), it("mv", 9.8, kind="movie")]
        everything = listed + extra + [it("x%d" % k, 1.0) for k in range(4, 40)]
        snap = SimpleNamespace(source="library", items=everything, by_id={i["id"]: i for i in everything})
        anime_ids = [{"canonical_id": i["id"]} for i in everything]
        with patch.object(hl.categories, "get", lambda slug: {"enabled": True, "min_items": 3}), \
                patch.object(hl.categories, "member_lists", lambda _s, slug: [listed] if slug == "animeler" else []), \
                patch.object(hl.db, "query", lambda *_a, **_k: anime_ids):
            got = [i["id"] for i in hl.Context(snap)._category("animeler")]
            self.assertEqual(got[:5], ["l1", "l2", "x2", "x3", "x1"])
            self.assertEqual(len(got), hl.TREND_TARGET)
            self.assertEqual(len(got), len(set(got)))
            self.assertFalse({"bad", "mv"} & set(got))
            self.assertEqual([i["id"] for i in hl.Context(snap)._category("drama")], [], "other categories: no fill")
            snap = SimpleNamespace(source="library", items=listed + extra[:1],
                                   by_id={i["id"]: i for i in listed + extra[:1]})
            self.assertEqual([i["id"] for i in hl.Context(snap)._category("animeler")], ["l1", "l2", "x1"],
                             "min_items (3) is met by the filled row")


class SampleFileTests(Base):
    """docs/api-samples/*.json are the censored example responses handed to client authors."""

    @staticmethod
    def paths(x, prefix=""):
        """{json path -> JSON type name} (list indexes collapsed), for a drift check that tolerates additions."""
        out = {}
        if isinstance(x, dict):
            for k, v in x.items():
                out.update(SampleFileTests.paths(v, prefix + "." + k))
        elif isinstance(x, list):
            for v in x[:1]:
                out.update(SampleFileTests.paths(v, prefix + "[]"))
            out.setdefault(prefix, "list")
        else:
            out[prefix] = type(x).__name__
        return out

    def test_samples_exist_and_conform_to_the_client_schemas(self):
        specs = {"row-new_episodes.json": S.ROW_PAGE, "detail-movie.json": S.DETAIL,
                 "detail-movie-no-source.json": S.DETAIL, "detail-series.json": S.DETAIL,
                 "detail-series-no-episodes.json": S.DETAIL, "streams-movie.json": S.STREAMS,
                 "streams-trailer.json": S.STREAMS, "streams-episode.json": S.STREAMS, "profiles.json": S.PROFILES,
                 "avatars.json": S.AVATARS, "catalog.json": S.CATALOG, "search.json": S.SEARCH,
                 "mylist.json": S.MYLIST, "error-not-found.json": S.ERROR,
                 "source-finder.json": S.SOURCE_FINDER,
                 "sections.json": S.SECTIONS, "section-series.json": S.SECTION}
        self.assertEqual(sorted(specs), sorted(seed.SAMPLES), "every sample has a schema")
        for name, spec in specs.items():
            path = os.path.join(seed.SAMPLE_DIR, name)
            self.assertTrue(os.path.exists(path), "%s missing: run `venv/bin/python -m tools.gen_api_samples`" % name)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(S.problems(json.load(fh), spec), [], name)

    def test_samples_are_censored(self):
        for name in seed.SAMPLES:
            with open(os.path.join(seed.SAMPLE_DIR, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("k3Jx9QvT", text, name + ": signed URL value leaked")
            self.assertNotIn("7f2a91", text, name)
            self.assertNotIn("1791000000", text, name)
        with open(os.path.join(seed.SAMPLE_DIR, "streams-movie.json"), encoding="utf-8") as fh:
            stream = json.load(fh)["streams"][0]
        self.assertEqual(stream["attempt_token"], "REDACTED")
        self.assertIn("t=REDACTED&e=REDACTED&s=REDACTED", stream["url"])

    def test_samples_did_not_drift_from_the_server(self):
        """A key/type in a sample that the server no longer returns = a breaking change (or stale sample:
        regenerate with `venv/bin/python -m tools.gen_api_samples`). New server fields are fine."""
        for name, body in seed.build_samples(self.c).items():
            with open(os.path.join(seed.SAMPLE_DIR, name), encoding="utf-8") as fh:
                sample = json.load(fh)
            have, want = self.paths(body), self.paths(sample)
            stale = sorted(p for p, t in want.items()
                           if p not in have or (t != have[p] and "NoneType" not in (t, have[p])))
            self.assertEqual(stale, [], "%s drifted from the server (regenerate the samples)" % name)


if __name__ == "__main__":
    unittest.main()
