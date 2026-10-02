"""Content that is not public never enters the library (``library/gate.py``, yaml ``blocked:`` / ``availability_gate:``):
the series verdicts, the inventory stage (blocked series not written / removed, partial, transient), the scan's pre-write
screening of films, the search path, play-time detection (``status='blocked'``: not suspect, outside the playheal window, no
repair from the source finder), the recheck and ``purge_source_items``. Network-free: ``fetch.page`` (series pages) and
``fetch.page_bundle`` (playback pages) are fakes, sleeps are recorders, temp DB + temp config dir."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import test_series_generic as tsg
from app import config, db
from app.library import gate, ingest as ingest_module, purge, series_crawl, sourcefinder, videos
from app.scraper import config as scfg, fetch, playheal, state
from app.scraper.runner import RunResult

SITE, BASE = tsg.SITE, tsg.BASE
FIXTURES = Path(__file__).parent / "fixtures"
TELIF = (FIXTURES / "trdiziizle_episode_telif.html").read_text(encoding="utf-8")
PLAYER = (FIXTURES / "trdiziizle_episode_player.html").read_text(encoding="utf-8")
RULE = {"on": "episode_page", "iframe_src_regex": "/player/telif", "reason": "telif engeli"}
RESOLVERS = [{"type": "iframe", "selector": "iframe#player", "attr": "src"}]
SERIES_CARD = {"title": "Kara Mavi", "detail_url": "/diziler/kara-mavi-izle/", "poster_url": f"{BASE}/p.jpg"}
NEWEST = BASE + tsg.ep_url("kara-mavi", 2, 2)    # MAIN lists s1e1 s1e2 s2e1 s2e2
OLDEST = BASE + tsg.ep_url("kara-mavi", 1, 1)
MIDDLE = BASE + tsg.ep_url("kara-mavi", 2, 1)
UNAVAILABLE = fetch.FetchError("crawlee-http http: http_503 for page", 503)
NO_PLAYER = "<html><body><p>video yok</p></body></html>"
REAL_RESOLVE = videos.resolve_source   # tsg.Base replaces it by a tripwire (an inventory never resolves a video host)


class GateBase(tsg.Base):
    """The generic site ``trgen`` with a ``blocked:`` rule (and an optional gate); ``self.ep`` serves the playback pages."""

    gate_cfg = None

    def setUp(self):
        self.ep = {}
        self.bundle_calls = []
        self.gate_sleeps = []
        super().setUp()
        for p in (patch.object(fetch, "page_bundle", self.fake_bundle), patch.object(gate, "_sleep", self.gate_sleeps.append)):
            p.start()
            self.addCleanup(p.stop)

    def write_config(self, **overrides):
        data = {"blocked": [RULE], "resolvers": RESOLVERS, "availability_gate": self.gate_cfg}
        data.update(overrides)
        super().write_config(**data)

    def fake_bundle(self, cfg, url, wait_for=""):
        self.bundle_calls.append(url)
        value = self.ep.get(url, PLAYER)
        if isinstance(value, Exception):
            raise value
        return {"html": value, "initial_html": value}

    def blocked_rows(self, **where):
        rows = [dict(r) for r in db.query("SELECT * FROM blocked_pages ORDER BY kind, url")]
        return [r for r in rows if all(r[k] == v for k, v in where.items())]

    def source_rows(self):
        return db.query("SELECT * FROM source_items")

    def norm(self):
        row = db.query_one("SELECT normalized FROM source_items WHERE source=?", (SITE,))
        return json.loads(row["normalized"]) if row else None

    def library_ids(self):
        return [r["id"] for r in db.query("SELECT id FROM library_items")]


class SeriesVerdictTests(GateBase):
    def episodes(self, *numbers):
        return [{"season": 1, "episode": n, "url": f"{BASE}/e{n}", "kind": "episode"} for n in numbers]

    def verdict(self, pages, **kw):
        self.ep.update({f"{BASE}/e{n}": html for n, html in pages.items()})
        return gate.series_verdict(self.cfg(), self.episodes(*pages), **kw)

    def test_every_probed_page_blocked_blocks_the_series(self):
        v = self.verdict({1: TELIF, 2: TELIF, 3: TELIF})
        self.assertEqual((v["state"], v["reason"], v["via"], v["probed"]), ("blocked", "telif engeli", "rule", 2))
        self.assertEqual([p["url"] for p in v["pages"]], [f"{BASE}/e3", f"{BASE}/e1"])   # the newest + the oldest
        self.assertEqual(self.gate_sleeps, [config.SERIES_CRAWL_DELAY])   # polite between the two fetches

    def test_a_partly_blocked_series_is_taken_and_only_the_blocked_pages_are_listed(self):
        v = self.verdict({1: TELIF, 2: PLAYER})
        self.assertEqual((v["state"], v["blocked"], v["retry"]), ("ok", [f"{BASE}/e1"], []))

    def test_a_transient_error_is_not_no_player(self):
        v = self.verdict({1: UNAVAILABLE, 2: UNAVAILABLE})
        self.assertEqual((v["state"], v["blocked"]), ("retry", []))
        self.assertIn("geçici hata", v["reason"])
        v = self.verdict({1: UNAVAILABLE, 2: PLAYER})   # one page is fine: taken
        self.assertEqual((v["state"], v["retry"]), ("ok", [f"{BASE}/e1"]))
        v = self.verdict({1: UNAVAILABLE, 2: TELIF})   # blocked + transient and nothing fine: not decided
        self.assertEqual(v["state"], "retry")
        for status in (403, 429, 500):   # every status except gone-for-good is transient
            self.assertEqual(gate.error_verdict(fetch.FetchError(f"http_{status}", status), "player")["state"], "retry")

    def test_the_gate_wants_a_player_a_rule_only_site_does_not(self):
        no_player = NO_PLAYER
        self.assertEqual(self.verdict({1: no_player, 2: no_player})["state"], "ok")   # rules only: no rule matched, nothing blocked
        self.gate_cfg = {"probe": 2, "require": "player"}
        self.write_config()
        v = self.verdict({1: no_player, 2: no_player})
        self.assertEqual((v["state"], v["via"], v["reason"]), ("blocked", "gate", "sayfada oynatıcı bulunamadı"))
        self.assertEqual(self.verdict({1: PLAYER, 2: no_player})["state"], "ok")

    def test_a_page_that_is_gone_is_no_player_for_the_gate_and_nothing_for_a_rule_only_site(self):
        gone = fetch.FetchError("crawlee-http http: http_404 for page", 404)
        self.assertEqual(self.verdict({1: gone, 2: gone})["state"], "ok")
        self.gate_cfg = {"probe": 2, "require": "player"}
        self.write_config()
        v = self.verdict({1: gone, 2: gone})
        self.assertEqual((v["state"], v["via"]), ("blocked", "gate"))
        self.assertIn("HTTP 404", v["reason"])

    def test_pages_with_a_fresh_blocked_row_are_not_fetched_again_and_nothing_is_probed_without_rules(self):
        self.ep[f"{BASE}/e2"] = PLAYER
        v = gate.series_verdict(self.cfg(), self.episodes(1, 2), known={f"{BASE}/e1": "telif engeli"})
        self.assertEqual((v["state"], v["blocked"], v["probed"]), ("ok", [f"{BASE}/e1"], 1))
        self.assertEqual(self.bundle_calls, [f"{BASE}/e2"])
        self.write_config(blocked=None)
        v = gate.series_verdict(self.cfg(), self.episodes(1, 2))
        self.assertEqual((v["state"], v["probed"]), ("ok", 0))

    def test_pick_pages(self):
        eps = self.episodes(*range(1, 8))
        self.assertEqual([p["episode"] for p in gate.pick_pages(eps, 2)], [7, 1])
        self.assertEqual([p["episode"] for p in gate.pick_pages(eps, 3)], [7, 1, 4])
        self.assertEqual([p["episode"] for p in gate.pick_pages(eps[:1], 3)], [1])
        self.assertEqual(gate.pick_pages([], 2), [])
        self.assertEqual(gate.pick_pages(eps, 0), [])


class InventoryStageTests(GateBase):
    def crawl(self, **kw):
        return series_crawl.run_stage(self.cfg(), SITE, **kw)

    def test_a_blocked_series_is_not_written_and_leaves_the_library(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        self.seed()
        self.assertEqual(len(self.library_ids()), 1)   # the card is in the library before the inventory is read
        stats = self.crawl()
        self.assertEqual((stats["series"], stats["errors"]), (0, 0))
        self.assertEqual({k: stats["blocked"][k] for k in ("series", "episodes", "probed", "removed", "retry")},
                         {"series": 1, "episodes": 2, "probed": 2, "removed": 1, "retry": 0})
        self.assertEqual((self.episode_rows(), self.source_rows(), self.library_ids()), ([], [], []))
        series = self.blocked_rows(kind="series")
        self.assertEqual([(r["source_key"], r["status"], r["via"], r["reason"]) for r in series],
                         [("kara-mavi", "blocked", "rule", "telif engeli")])   # the generic normalizer's key lost its "-izle" tail (D1)
        self.assertEqual(len(self.blocked_rows(kind="episode", status="blocked")), 2)

    def test_the_viewers_data_stays_when_a_series_is_removed(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        cid = self.seed()[0]
        db.execute("INSERT INTO mylist(profile_id,item_id,added_at) VALUES ('p1',?,?)", (cid, 1))
        db.execute("INSERT INTO progress(profile_id,item_id,episode_id,position,duration,watched,updated_at) VALUES ('p1',?,?,10,100,0,1)",
                   (cid, cid + ":s1:e1"))
        self.crawl()
        self.assertEqual(self.library_ids(), [])
        self.assertEqual(len(db.query("SELECT 1 FROM mylist WHERE item_id=?", (cid,))), 1)
        self.assertEqual(len(db.query("SELECT 1 FROM progress WHERE item_id=?", (cid,))), 1)

    def test_a_partly_blocked_series_is_taken_without_its_blocked_episodes(self):
        self.ep.update({NEWEST: PLAYER, OLDEST: TELIF})
        self.seed()
        stats = self.crawl()
        self.assertEqual((stats["series"], stats["episodes"], stats["blocked"]["series"], stats["blocked"]["episodes"]), (1, 3, 0, 1))
        self.assertEqual([(r["season"], r["episode"]) for r in self.episode_rows()], [(1, 2), (2, 1), (2, 2)])   # s1e1 is not written
        self.assertEqual([r["url"] for r in self.blocked_rows(status="blocked")], [OLDEST])
        marker = self.norm()["_gate"]
        self.assertEqual((marker["state"], marker["blocked_episodes"]), ("ok", 1))
        self.assertNotIn(OLDEST, [e["url"] for e in self.norm()["video_sources"]])   # nor in the stored inventory

    def test_a_verified_series_is_not_probed_again_inside_the_window(self):
        self.ep.update({NEWEST: PLAYER, OLDEST: PLAYER})
        self.seed()
        self.crawl()
        self.assertEqual(len(self.bundle_calls), 2)
        self.crawl(force=True)
        self.assertEqual(len(self.bundle_calls), 2)   # the _gate marker is fresh
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$._gate.at',1)")
        self.crawl(force=True)
        self.assertEqual(len(self.bundle_calls), 4)   # a stale marker: judged again

    def test_a_transient_failure_does_not_take_an_unverified_series_and_it_is_judged_again(self):
        self.ep.update({NEWEST: UNAVAILABLE, OLDEST: UNAVAILABLE})
        self.seed()
        stats = self.crawl()
        self.assertEqual((stats["series"], stats["blocked"]["retry"], stats["blocked"]["series"], stats["blocked"]["removed"]), (0, 1, 0, 1))
        self.assertEqual((self.episode_rows(), self.library_ids()), ([], []))
        self.assertEqual([(r["kind"], r["status"]) for r in self.blocked_rows() if r["kind"] == "series"], [("series", "retry")])
        # the next scan: the card comes again, the pages answer now -> taken
        self.ep.update({NEWEST: PLAYER, OLDEST: PLAYER})
        result = self.ingest([SERIES_CARD])
        self.assertEqual(len(self.episode_rows()), 4)
        self.assertEqual(self.blocked_rows(kind="series"), [])   # an ok series clears its old row
        self.assertEqual(result["series_crawl"]["series"], 1)

    def test_a_transient_failure_never_takes_a_verified_series_away(self):
        self.ep.update({NEWEST: PLAYER, OLDEST: PLAYER})
        self.seed()
        self.crawl()
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$._gate.at',1)")
        self.ep.update({NEWEST: UNAVAILABLE, OLDEST: UNAVAILABLE})
        stats = self.crawl(force=True)
        self.assertEqual((stats["blocked"]["retry"], stats["blocked"]["removed"]), (1, 0))
        self.assertEqual(len(self.episode_rows()), 4)
        self.assertEqual(self.norm()["_series_inventory"]["fails"], 1)   # a failed attempt: backoff, nothing else

    def test_a_series_page_rule_blocks_before_any_episode_is_read(self):
        self.write_config(blocked=[{"on": "series_page", "selector": "h1.baslik", "reason": "dizi kaldırıldı"}])
        self.pages[tsg.SERIES_URL] = '<html><body><h1 class="baslik">Kara Mavi</h1></body></html>'
        self.seed()
        stats = self.crawl()
        self.assertEqual((stats["blocked"]["series"], stats["blocked"]["removed"], self.bundle_calls), (1, 1, []))
        self.assertEqual(self.blocked_rows(kind="series")[0]["reason"], "dizi kaldırıldı")
        self.assertEqual(self.library_ids(), [])

    def test_ingest_does_not_write_a_series_with_a_fresh_blocked_row_until_the_recheck_window_is_over(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        first = self.ingest([SERIES_CARD])
        self.assertEqual(first["blocked"]["series"], 1)
        self.assertEqual((self.source_rows(), self.library_ids()), ([], []))
        calls = len(self.bundle_calls)
        second = self.ingest([SERIES_CARD])
        self.assertEqual((self.source_rows(), second["blocked"]["items"]), ([], 1))   # skipped, nothing fetched
        self.assertEqual(len(self.bundle_calls), calls)
        self.assertEqual(second["status"], "success")
        db.execute("UPDATE blocked_pages SET checked_at=1")   # the verdict is old now
        self.ep.update({NEWEST: PLAYER, OLDEST: PLAYER})      # and the site lifted the block
        third = self.ingest([SERIES_CARD])
        self.assertEqual(len(self.episode_rows()), 4)
        self.assertEqual((third["series_crawl"]["series"], self.blocked_rows(kind="series")), (1, []))

    def test_opening_a_series_that_was_never_judged_hides_it(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        cid = self.seed()[0]   # a live-search card: not judged
        self.assertEqual(self.library_ids(), [cid])
        self.assertTrue(ingest_module.hydrate_series_item(cid))
        self.assertEqual((self.library_ids(), self.source_rows()), ([], []))
        self.assertEqual(len(self.blocked_rows(kind="series")), 1)

    def test_a_search_card_of_a_blocked_series_is_not_cached_again(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        self.seed()
        self.crawl()
        self.assertEqual(self.seed(), [])
        self.assertEqual(self.source_rows(), [])
        other = ingest_module.ingest_discovered_items(SITE, [{"title": "Başka", "detail_url": "/diziler/baska-izle/", "poster_url": "x"}])
        self.assertEqual(len(other), 1)   # a series nobody judged yet stays on the card

    def test_a_site_without_blocks_is_untouched(self):
        self.write_config(blocked=None, availability_gate=None, resolvers=None)
        self.seed()
        stats = self.crawl()
        self.assertEqual((stats["series"], stats["episodes"], self.bundle_calls), (1, 4, []))
        self.assertEqual((stats["blocked"], self.blocked_rows()), (gate.new_counters(), []))
        self.assertNotIn("_gate", self.norm())

    def test_removed_when_the_site_is_deleted(self):
        self.ep.update({NEWEST: TELIF, OLDEST: TELIF})
        self.seed()
        self.crawl()
        self.assertTrue(self.blocked_rows())
        exact, details = purge.list_ids(SITE)
        self.assertEqual(purge._purge_db(SITE, exact, details)["blocked_pages"], 3)
        self.assertEqual(self.blocked_rows(), [])


class MovieGateTests(GateBase):
    gate_cfg = {"probe": 1, "require": "player"}

    def write_config(self, **overrides):
        film = {"series_page": None, "normalize": {
            "host": "www.trgen.test", "type": "movie",
            "key": {"from": ["detail_url"], "regex": r"^/film/(?P<id>[^/]+)", "template": "{id}"},
            "source_url": BASE + "/film/{id}/"}}
        super().write_config(**{**film, **overrides})

    def film(self, slug):
        return {"title": f"Film {slug}", "detail_url": f"/film/{slug}/", "poster_url": f"{BASE}/{slug}.jpg"}

    def url(self, slug):
        return f"{BASE}/film/{slug}/"

    def test_films_are_judged_before_they_are_written(self):
        self.ep.update({self.url("a"): PLAYER, self.url("b"): NO_PLAYER, self.url("c"): UNAVAILABLE})
        result = self.ingest([self.film("a"), self.film("b"), self.film("c")])
        self.assertEqual([r["source_key"] for r in self.source_rows()], ["a"])   # b has no player, c could not be asked
        counters = result["blocked"]
        self.assertEqual((counters["items"], counters["retry"], counters["probed"], counters["deferred"]), (1, 1, 3, 0))
        self.assertEqual([(r["source_key"], r["kind"], r["status"]) for r in self.blocked_rows()],
                         [("b", "movie", "blocked"), ("c", "movie", "retry")])
        self.assertEqual(self.blocked_rows(source_key="b")[0]["via"], "gate")
        self.assertEqual(len(db.query("SELECT 1 FROM video_sources WHERE kind='movie'")), 1)
        # the next scan: a is verified (no probe), b has a fresh verdict (no probe), c is asked again and passes
        self.bundle_calls.clear()
        self.ep[self.url("c")] = PLAYER
        again = self.ingest([self.film("a"), self.film("b"), self.film("c")])
        self.assertEqual(self.bundle_calls, [self.url("c")])
        self.assertEqual(sorted(r["source_key"] for r in self.source_rows()), ["a", "c"])
        self.assertEqual(again["blocked"]["items"], 1)

    def test_a_film_that_was_written_and_turns_out_blocked_leaves_the_library(self):
        self.ep[self.url("a")] = PLAYER
        self.ingest([self.film("a")])
        self.assertEqual(len(self.library_ids()), 1)
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$._gate.at',1)")   # the verdict is old
        self.ep[self.url("a")] = TELIF
        self.ingest([self.film("a")])
        self.assertEqual((self.source_rows(), self.library_ids()), ([], []))

    def test_a_transient_failure_never_takes_a_verified_film_away(self):
        self.ep[self.url("a")] = PLAYER
        self.ingest([self.film("a")])
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$._gate.at',1)")
        self.ep[self.url("a")] = UNAVAILABLE
        self.ingest([self.film("a")])
        self.assertEqual(len(self.library_ids()), 1)

    def test_the_budget_defers_instead_of_writing_unverified_films(self):
        self.ep.update({self.url(s): PLAYER for s in "abc"})
        with patch.object(config, "GATE_BUDGET", 2):
            result = self.ingest([self.film(s) for s in "abc"])
        self.assertEqual(sorted(r["source_key"] for r in self.source_rows()), ["a", "b"])
        self.assertEqual((result["blocked"]["deferred"], result["blocked"]["probed"]), (1, 2))
        result = self.ingest([self.film(s) for s in "abc"])   # the rest of the next scan: only c is asked
        self.assertEqual(sorted(r["source_key"] for r in self.source_rows()), ["a", "b", "c"])

    def test_rule_only_sites_do_not_probe_films_at_scan_time(self):
        self.gate_cfg = None
        self.write_config()
        self.ep[self.url("a")] = TELIF
        self.ingest([self.film("a")])
        self.assertEqual((len(self.source_rows()), self.bundle_calls), (1, []))   # detected when it is played


class PlaybackTests(GateBase):
    """Play-time detection: a ``blocked:`` rule that matches the page of a stored source."""

    def setUp(self):
        super().setUp()
        resolve = patch.object(videos, "resolve_source", REAL_RESOLVE)
        resolve.start()
        self.addCleanup(resolve.stop)
        self.ep.update({NEWEST: PLAYER, OLDEST: PLAYER})
        self.seed()
        series_crawl.run_stage(self.cfg(), SITE)
        self.cid = self.library_ids()[0]
        self.bundle_calls.clear()
        playheal.reset()

    def source(self, season, episode):
        return db.query_one("SELECT * FROM video_sources WHERE season=? AND episode=? AND kind='episode'", (season, episode))

    def test_a_blocked_page_marks_the_source_blocked_not_suspect(self):
        self.ep[MIDDLE] = TELIF
        row = self.source(2, 1)
        with self.assertRaises(videos.Blocked) as caught:
            videos.resolve_source(row, force=True)
        self.assertEqual(str(caught.exception), "engelli: telif engeli")
        now = self.source(2, 1)
        self.assertEqual((now["status"], now["failures"], now["last_error"]), ("blocked", 0, "engelli: telif engeli"))
        self.assertEqual([(r["url"], r["status"], r["via"]) for r in self.blocked_rows(kind="episode")], [(MIDDLE, "blocked", "rule")])
        event = state.get_site_state(SITE)["last_resolver"]
        self.assertEqual((event["blocked"], event["ok"]), (True, False))
        self.assertEqual(playheal._snapshot(SITE), [])   # the repair trigger's sliding window never sees it
        self.assertIsNone(playheal.evaluate(SITE, force=True))

    def test_streams_marks_blocked_and_never_offers_it_again(self):
        self.ep[MIDDLE] = TELIF
        episode_id = self.source(2, 1)["episode_id"]
        self.assertEqual(videos.streams(self.cid, episode_id)["streams"], [])
        self.assertEqual(self.source(2, 1)["status"], "blocked")   # not 'suspect'
        calls = len(self.bundle_calls)
        self.assertEqual(videos.streams(self.cid, episode_id)["streams"], [])
        self.assertEqual(len(self.bundle_calls), calls)            # a blocked source is not asked again
        self.assertEqual(self.source(1, 2)["status"], "unknown")   # the others are untouched

    def test_the_admin_counters_leave_it_out(self):
        self.ep[MIDDLE] = TELIF
        with self.assertRaises(videos.Blocked):
            videos.resolve_source(self.source(2, 1), force=True)
        from app.routers import ops_library
        detail = ops_library.library_videos(self.cid, limit=200)
        self.assertEqual((detail["summary"]["blocked"], detail["summary"]["total"], detail["summary"]["broken"]), (1, 3, 0))
        self.assertEqual(detail["summary"]["label"], "0/3")

    def test_the_source_finder_never_runs_a_repair_for_it(self):
        row = self.source(2, 1)
        job = {"job_id": 1, "key": (self.cid, row["episode_id"]), "cid": self.cid, "ep": row["episode_id"]}
        with patch.object(videos, "resolve_source", side_effect=videos.Blocked("engelli: telif engeli")):
            out = sourcefinder._resolve_found(job, [row], "retry")
        self.assertNotIn("found", out)
        self.assertFalse(job.get("failed"))   # no evidence for the heal step: a block is not a broken player
        videos._mark_blocked(row, "telif engeli")
        self.assertEqual(sourcefinder._episode_rows(self.cid, row["episode_id"]), [])   # and the retry step has nothing to ask

    def test_a_cached_blocked_answer_stays_a_blocked_error(self):
        self.ep[MIDDLE] = TELIF
        row = self.source(2, 1)
        with self.assertRaises(videos.Blocked):
            videos.resolve_source(row, force=True)
        with self.assertRaises(videos.Blocked):   # the negative cache answers the next call
            videos.resolve_source(self.source(2, 1))
        self.assertTrue(videos.is_blocked(ValueError("engelli: x")))
        self.assertFalse(videos.is_blocked(ValueError("sağlayıcı akış vermedi")))

    def test_a_blocked_episode_url_is_not_written_again(self):
        gate.record(SITE, MIDDLE, "blocked", "telif engeli", "rule", kind="episode", key="kara-mavi-izle")
        db.execute("DELETE FROM video_sources WHERE season=2 AND episode=1")
        norm = self.norm()
        conn = db.connect()
        try:
            videos.sync_source(conn, SITE, "kara-mavi-izle", self.cid, norm, self.cfg())
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(self.source(2, 1))
        self.assertIsNotNone(self.source(1, 2))

    def test_the_recheck_reopens_a_page_the_site_unblocked(self):
        self.ep[MIDDLE] = TELIF
        with self.assertRaises(videos.Blocked):
            videos.resolve_source(self.source(2, 1), force=True)
        self.assertEqual(gate.recheck(self.cfg(), SITE), {"checked": 0, "reopened": 0, "still_blocked": 0, "retry": 0})   # not old enough
        db.execute("UPDATE blocked_pages SET checked_at=1")
        self.assertEqual(gate.recheck(self.cfg(), SITE)["still_blocked"], 1)   # still telif: asked, stays blocked
        self.assertEqual(self.source(2, 1)["status"], "blocked")
        db.execute("UPDATE blocked_pages SET checked_at=1")
        self.ep[MIDDLE] = PLAYER
        out = gate.recheck(self.cfg(), SITE)
        self.assertEqual((out["checked"], out["reopened"]), (1, 1))
        self.assertEqual((self.source(2, 1)["status"], self.blocked_rows()), ("unknown", []))
        # a transient error changes nothing and is asked again next time
        gate.record(SITE, MIDDLE, "blocked", "telif engeli", "rule", kind="episode", key="k", now=1)
        self.ep[MIDDLE] = UNAVAILABLE
        self.assertEqual(gate.recheck(self.cfg(), SITE)["retry"], 1)
        self.assertEqual(len(self.blocked_rows(status="blocked")), 1)


class PurgeItemsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "t.db"))
        p.start()
        self.addCleanup(p.stop)
        db.init()
        now = int(time.time())
        for cid in ("shared", "alone", "other"):
            db.execute("INSERT INTO library_items(id,type,title,updated_at) VALUES (?, 'series', ?, ?)", (cid, cid, now))
        for source, key, cid in (("a", "shared", "shared"), ("b", "shared", "shared"), ("a", "alone", "alone"), ("a", "other", "other")):
            db.execute("INSERT INTO source_items(source,source_key,canonical_id,raw,normalized,fetched_at) VALUES (?,?,?,?,?,?)",
                       (source, key, cid, "{}", json.dumps({"title": cid, "type": "series"}), now))
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,updated_at) VALUES (?,?,?,?,'movie','https://x/1',?)",
                       (f"vs_{source}_{key}", cid, source, key, now))
        db.execute("INSERT INTO library_lists(list_id,canonical_id,position) VALUES ('l','alone',0),('l','shared',1)")
        db.execute("INSERT INTO mylist(profile_id,item_id,added_at) VALUES ('p1','alone',1)")

    def test_only_the_named_items_go_and_a_shared_title_stays(self):
        out = purge.purge_source_items("a", ["shared", "alone", "missing"])
        self.assertEqual((out["source_items"], out["video_sources"], out["library_items"], out["shared_kept"]), (2, 2, 1, 1))
        self.assertEqual(sorted(r["id"] for r in db.query("SELECT id FROM library_items")), ["other", "shared"])
        self.assertEqual(sorted((r["source"], r["source_key"]) for r in db.query("SELECT * FROM source_items")),
                         [("a", "other"), ("b", "shared")])
        self.assertEqual([r["canonical_id"] for r in db.query("SELECT canonical_id FROM library_lists")], ["shared"])
        self.assertEqual(len(db.query("SELECT 1 FROM mylist WHERE item_id='alone'")), 1)   # the viewer's data waits for the title
        self.assertEqual(purge.purge_source_items("a", []), {k: 0 for k in purge._ITEM_COUNTERS})


if __name__ == "__main__":
    unittest.main()
