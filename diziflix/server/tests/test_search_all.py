import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import _contract_seed as seed
from _contract_seed import BROKENLAST, FILM, NOPOSTER, NOSEASONS, SERIES, UNAIRED
from app import config, db
from app.library import search_all
from app.scraper import site_search
from app.scraper.site_search import yabancidizi


def raw(*ids):
    return [{"id": i, "title": i} for i in ids]


class FanOutBase(unittest.TestCase):
    """``site_search`` (S1) and the library are faked: search_all is tested alone."""
    sites = ["a", "b", "c"]

    def setUp(self):
        search_all.reset()
        self.calls = []
        self.release = threading.Event()
        self.addCleanup(self.release.set)           # never leave a blocked worker behind
        self.addCleanup(search_all.reset)
        self.hits = {}
        for target, name, value in (
                (config, "SEARCH_PARALLEL", 4), (config, "SEARCH_SITE_TIMEOUT", 5.0), (config, "SEARCH_TOTAL_TIMEOUT", 10.0),
                (config, "SEARCH_BREAKER_FAILS", 3), (config, "SEARCH_BREAKER_COOLDOWN", 300.0)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.refresh = self.patch(search_all.cache, "refresh")
        self.patch(site_search, "search_sites", side_effect=lambda: list(self.sites), create=True)
        self.patch(site_search, "supports", side_effect=lambda s: s in self.sites, create=True)
        self.patch(site_search, "search", side_effect=self.fake_search)
        self.ingest = self.patch(search_all, "ingest_discovered_items", side_effect=lambda site, items: [i["id"] for i in items])

    def patch(self, target, name, **kw):
        p = patch.object(target, name, **kw)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def fake_search(self, site, query, limit=20):
        self.calls.append((site, query, limit))
        return raw(*self.hits.get(site, ["%s-hit" % site]))


class SearchSitesTests(FanOutBase):
    def test_every_search_site_is_queried_and_ingested(self):
        self.hits = {"a": ["x", "y"], "b": ["y", "z"], "c": []}
        out = search_all.search_sites("dark")
        self.assertEqual(sorted(out), ["a", "b", "c"])
        self.assertEqual(out["a"]["ids"], ["x", "y"])
        self.assertEqual((out["a"]["ok"], out["a"]["count"], out["a"]["error"], out["a"]["skipped"]), (True, 2, "", None))
        self.assertEqual(out["c"], dict(ok=True, ids=[], count=0, ms=out["c"]["ms"], error="", skipped=None))
        self.assertIsInstance(out["a"]["ms"], int)
        self.assertEqual(sorted(c[0] for c in self.calls), ["a", "b", "c"])
        self.assertEqual(self.ingest.call_count, 2, "an empty hit list is not ingested")
        self.refresh.assert_called_once()

    def test_no_refresh_when_nothing_was_found(self):
        self.hits = {"a": [], "b": [], "c": []}
        search_all.search_sites("dark")
        self.refresh.assert_not_called()

    def test_explicit_sites_query_normalisation_and_limit_clamp(self):
        out = search_all.search_sites("  dark   knight ", sites=["b"], limit=100)
        self.assertEqual(list(out), ["b"])
        self.assertEqual(self.calls, [("b", "dark knight", 20)])
        search_all.search_sites("dark", sites=["a"], limit=0)
        self.assertEqual(self.calls[-1], ("a", "dark", 1))

    def test_short_query_searches_nothing(self):
        out = search_all.search_sites("ka")
        self.assertEqual({s: r["skipped"] for s, r in out.items()}, {"a": "short_query", "b": "short_query", "c": "short_query"})
        self.assertFalse(any(r["ok"] for r in out.values()))
        self.assertEqual(self.calls, [])

    def test_site_without_search_endpoint_is_skipped(self):
        out = search_all.search_sites("dark", sites=["a", "nope"])
        self.assertEqual((out["a"]["ok"], out["nope"]["ok"], out["nope"]["skipped"]), (True, False, "unsupported"))
        self.assertEqual([c[0] for c in self.calls], ["a"])

    def test_sites_run_in_parallel(self):
        barrier = threading.Barrier(3)

        def meet(site, query, limit=20):
            barrier.wait(timeout=3)            # only passes when all three searches are running at the same time
            return raw(site)
        self.patch(site_search, "search", side_effect=meet)
        out = search_all.search_sites("dark")
        self.assertTrue(all(r["ok"] for r in out.values()), out)

    def test_parallelism_is_capped(self):
        self.sites = ["a", "b", "c", "d", "e"]
        running, peak, lock = [0], [0], threading.Lock()

        def work(site, query, limit=20):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.08)
            with lock:
                running[0] -= 1
            return raw(site)
        self.patch(site_search, "search", side_effect=work)
        config.SEARCH_PARALLEL = 2
        out = search_all.search_sites("dark")
        self.assertEqual(sum(r["ok"] for r in out.values()), 5)
        self.assertEqual(peak[0], 2)

    def test_a_failing_search_or_cache_write_only_costs_that_site(self):
        def search(site, query, limit=20):
            if site == "a":
                raise RuntimeError("HTTP 503")
            return raw(site)

        def ingest(site, items):
            if site == "b":
                raise ValueError("db locked")
            return [i["id"] for i in items]
        self.patch(site_search, "search", side_effect=search)
        self.ingest.side_effect = ingest
        out = search_all.search_sites("dark")
        self.assertEqual((out["a"]["ok"], out["a"]["error"]), (False, "HTTP 503"))
        self.assertEqual((out["b"]["ok"], out["b"]["error"]), (False, "db locked"))
        self.assertEqual((out["c"]["ok"], out["c"]["ids"]), (True, ["c"]))

    def test_slow_site_times_out_without_holding_the_others(self):
        def search(site, query, limit=20):
            if site == "b":
                self.release.wait(5)
            return raw(site)
        self.patch(site_search, "search", side_effect=search)
        t0 = time.monotonic()
        out = search_all.search_sites("dark", timeout=0.25)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual((out["b"]["ok"], out["b"]["error"]), (False, "timeout"))
        self.assertEqual((out["a"]["ok"], out["c"]["ok"]), (True, True))

    def test_total_timeout_reports_running_and_queued_sites(self):
        config.SEARCH_PARALLEL = 1
        config.SEARCH_TOTAL_TIMEOUT = 0.3

        def search(site, query, limit=20):
            self.calls.append((site, query, limit))
            self.release.wait(5)
            return raw(site)
        self.patch(site_search, "search", side_effect=search)
        t0 = time.monotonic()
        out = search_all.search_sites("dark", sites=["a", "b"])
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual((out["a"]["error"], out["b"]["error"]), ("timeout", "timeout"))
        self.release.set()
        time.sleep(0.1)
        self.assertEqual([c[0] for c in self.calls], ["a"], "the queued site was never started")
        state = search_all.breaker_state()
        self.assertEqual(list(state), ["a"], "only the search that really ran counts as a failure")

    def test_same_site_and_query_is_searched_once_at_a_time(self):
        started = threading.Event()

        def slow(site, query, limit=20):
            self.calls.append((site, query, limit))
            started.set()
            self.release.wait(5)
            return raw("x")
        self.patch(site_search, "search", side_effect=slow)
        results = []
        first = threading.Thread(target=lambda: results.append(search_all.search_sites("dark", sites=["a"])))
        first.start()
        self.assertTrue(started.wait(2))
        second = threading.Thread(target=lambda: results.append(search_all.search_sites("Dark ", sites=["a"])))
        second.start()
        time.sleep(0.2)
        self.release.set()
        first.join(3)
        second.join(3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([r["a"]["ids"] for r in results], [["x"], ["x"]])


class BreakerTests(FanOutBase):
    def setUp(self):
        super().setUp()
        config.SEARCH_BREAKER_FAILS = 2
        config.SEARCH_BREAKER_COOLDOWN = 0.3
        self.bad = True

        def search(site, query, limit=20):
            self.calls.append((site, query, limit))
            if site == "a" and self.bad:
                raise RuntimeError("HTTP 500")
            return raw(site)
        self.patch(site_search, "search", side_effect=search)

    def calls_to(self, site):
        return sum(1 for c in self.calls if c[0] == site)

    def test_repeated_failures_park_the_site_then_one_retry_after_the_cooldown(self):
        for _ in range(2):
            out = search_all.search_sites("dark")
            self.assertEqual((out["a"]["ok"], out["a"]["skipped"]), (False, None))
        out = search_all.search_sites("dark")
        self.assertEqual((out["a"]["ok"], out["a"]["skipped"], out["a"]["error"]), (False, "breaker", ""))
        self.assertTrue(out["b"]["ok"] and out["c"]["ok"], "other sites are unaffected")
        self.assertEqual(self.calls_to("a"), 2, "a parked site is not searched")
        self.assertTrue(search_all.breaker_state()["a"]["parked"])
        time.sleep(0.35)
        out = search_all.search_sites("dark")
        self.assertEqual((out["a"]["ok"], out["a"]["error"]), (False, "HTTP 500"))
        self.assertEqual(self.calls_to("a"), 3)
        self.assertEqual(search_all.search_sites("dark")["a"]["skipped"], "breaker", "one more failure parks it again at once")

    def test_a_success_closes_the_breaker(self):
        search_all.search_sites("dark")
        self.bad = False
        self.assertTrue(search_all.search_sites("dark")["a"]["ok"])
        self.assertEqual(search_all.breaker_state(), {})
        self.bad = True
        search_all.search_sites("dark")                                    # one failure: the count started over
        before = self.calls_to("a")
        out = search_all.search_sites("dark")
        self.assertEqual((out["a"]["skipped"], self.calls_to("a")), (None, before + 1), "not parked: needs 2 in a row again")

    def test_empty_result_is_not_a_failure(self):
        self.hits = {"a": []}
        self.bad = False
        for _ in range(4):
            self.assertTrue(search_all.search_sites("dark", sites=["a"])["a"]["ok"])


class RealWiringTests(unittest.TestCase):
    """Real ``site_search`` dispatcher + yabancidizi adapter (only its HTTP call is faked) + the real library write."""

    def setUp(self):
        search_all.reset()
        self.addCleanup(search_all.reset)
        yabancidizi._results.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DB_PATH", str(Path(tmp.name) / "wiring.db")),):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(search_all.cache, "refresh")
        self.refresh = p.start()
        self.addCleanup(p.stop)
        db.init()

    def test_search_hits_become_canonical_library_items(self):
        payload = {"success": 1, "data": {"result": [
            {"s_type": "0", "s_link": "dark-izle-5", "s_name": "Dark", "s_image": "dark.jpg", "s_year": "2017"},
            {"s_type": "1", "s_link": "dark-city-izle", "s_name": "Dark City", "s_image": "city.jpg", "s_year": "1998"}]}}
        self.assertIn("yabancidizi", site_search.search_sites())
        with patch.object(yabancidizi, "_request", return_value=payload):
            out = search_all.search_sites("dark", sites=["yabancidizi"])
        self.assertEqual((out["yabancidizi"]["ok"], out["yabancidizi"]["count"]), (True, 2))
        ids = out["yabancidizi"]["ids"]
        self.assertEqual(len(ids), 2)
        stored = {r["canonical_id"] for r in db.query("SELECT canonical_id FROM source_items WHERE source='yabancidizi'")}
        self.assertEqual(stored, set(ids))
        self.refresh.assert_called_once()
        self.assertEqual(search_all.source_options({ids[0]: "series"})[ids[0]][0]["site"], "yabancidizi")

    def test_a_site_without_a_search_endpoint_is_reported_not_raised(self):
        self.assertNotIn("sinemalar", site_search.search_sites())
        out = search_all.search_sites("dark", sites=["sinemalar"])
        self.assertEqual((out["sinemalar"]["ok"], out["sinemalar"]["skipped"]), (False, "unsupported"))


class SourceOptionsAndRouteTests(unittest.TestCase):
    """Real FastAPI app + the seeded contract library (tests/_contract_seed.py); only the site search/ingest are faked."""

    @classmethod
    def setUpClass(cls):
        cls._ctx = seed.seeded_client()
        cls.c = cls._ctx.__enter__()
        with closing(db.connect()) as conn, conn:   # a second site that holds the film with a dead source
            conn.execute("INSERT INTO source_items(source,source_key,canonical_id,fetched_at) VALUES ('sinemalar','gece-key',?,1)", (FILM,))
            seed.video(conn, FILM, "movie", "https://cdn.example/other.mp4", status="broken", source="sinemalar")

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        search_all.reset()
        self.addCleanup(search_all.reset)
        self.sites = ["yabancidizi", "sinemalar"]
        self.hits = {"yabancidizi": [SERIES, FILM], "sinemalar": [FILM, NOPOSTER]}
        self.fail = {}
        self.calls = []
        for target, name, kw in (
                (site_search, "search_sites", dict(side_effect=lambda: list(self.sites), create=True)),
                (site_search, "supports", dict(side_effect=lambda s: s in self.sites, create=True)),
                (site_search, "search", dict(side_effect=self.fake_search)),
                (search_all, "ingest_discovered_items", dict(side_effect=lambda site, items: [i["id"] for i in items]))):
            p = patch.object(target, name, **kw)
            p.start()
            self.addCleanup(p.stop)

    def fake_search(self, site, query, limit=20):
        self.calls.append(site)
        if site in self.fail:
            raise self.fail[site]
        return raw(*self.hits.get(site, []))

    def get(self, path, status=200):
        r = self.c.get(path)
        self.assertEqual(r.status_code, status, r.text[:200])
        return r.json()

    # -- source_options -------------------------------------------------------------------------------------------
    def test_source_options_counts_status_and_order(self):
        types = {SERIES: "series", FILM: "movie", NOPOSTER: "movie", NOSEASONS: "series", UNAIRED: "series",
                 BROKENLAST: "series", "missing": "movie"}
        opts = search_all.source_options(types)
        self.assertEqual(opts[SERIES], [{"site": "yabancidizi", "name": "Yabancı Dizi", "kind": "series",
                                         "episodes": 7, "status": "ok"}])
        self.assertEqual([(o["site"], o["episodes"], o["status"]) for o in opts[FILM]],
                         [("yabancidizi", 1, "ok"), ("sinemalar", 1, "broken")], "best source first; trailer rows do not count")
        self.assertEqual(opts[FILM][1]["name"], "Sinemalar.com")
        self.assertEqual((opts[NOPOSTER][0]["status"], opts[NOPOSTER][0]["episodes"]), ("unknown", 1), "never tried")
        self.assertEqual((opts[NOSEASONS][0]["episodes"], opts[NOSEASONS][0]["status"]), (0, "unknown"), "no inventory yet")
        self.assertEqual((opts[UNAIRED][0]["episodes"], opts[UNAIRED][0]["status"]), (1, "unknown"), "disabled rows are left out")
        self.assertEqual(opts[BROKENLAST][0]["status"], "ok", "one tried and broken, one untried: not all broken")
        self.assertEqual(opts["missing"], [])
        self.assertEqual(search_all.source_options({}), {})

    def test_source_options_is_two_queries_for_a_whole_page(self):
        seen = []
        real = db.query

        def spy(sql, params=()):
            seen.append(sql)
            return real(sql, params)
        with patch.object(db, "query", side_effect=spy):
            search_all.source_options({SERIES: "series", FILM: "movie", NOPOSTER: "movie", NOSEASONS: "series"})
        self.assertEqual(len(seen), 2)

    # -- route ----------------------------------------------------------------------------------------------------
    def test_yabancidizi_only_keeps_the_old_shape(self):
        self.sites = ["yabancidizi"]
        body = self.get("/api/search?q=sessiz&profile=p1")
        self.assertEqual((body["remote"], body["remote_error"]), (True, None))
        self.assertEqual([i["id"] for i in body["items"]][:2], [SERIES, FILM], "remote hits first")
        self.assertEqual(body["total"], len(body["items"]))
        self.assertEqual(body["remote_sites"].keys(), {"yabancidizi"})
        self.assertEqual(body["remote_sites"]["yabancidizi"]["ok"], True)
        self.assertEqual(body["remote_sites"]["yabancidizi"]["count"], 2)
        self.assertNotIn("error", body["remote_sites"]["yabancidizi"])
        self.assertEqual(self.calls, ["yabancidizi"])
        self.fail = {"yabancidizi": RuntimeError("HTTP 503")}
        down = self.get("/api/search?q=sessiz&profile=p1")
        self.assertEqual((down["remote"], down["remote_error"]), (True, "HTTP 503"), "one site: its bare message")
        self.assertEqual(down["items"][0]["id"], NOPOSTER, "local matches still come back")
        self.assertEqual(down["remote_sites"]["yabancidizi"]["error"], "HTTP 503")

    def test_all_sites_are_merged_without_duplicates(self):
        body = self.get("/api/search?q=sessiz&profile=p1&limit=10")
        ids = [i["id"] for i in body["items"]]
        self.assertEqual(ids[:3], [SERIES, FILM, NOPOSTER], "round-robin over sites, the film found twice is one item")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(self.calls), ["sinemalar", "yabancidizi"])
        self.assertEqual(sorted(body["remote_sites"]), ["sinemalar", "yabancidizi"])
        self.assertEqual(body["remote_sites"]["sinemalar"]["count"], 2)
        self.assertIsNone(body["remote_error"])

    def test_limit_applies_to_the_merged_list(self):
        body = self.get("/api/search?q=sessiz&profile=p1&limit=2")
        self.assertEqual([i["id"] for i in body["items"]], [SERIES, FILM])
        self.assertEqual(body["total"], 2)

    def test_failing_site_is_named_when_several_were_searched(self):
        self.fail = {"sinemalar": RuntimeError("HTTP 500")}
        body = self.get("/api/search?q=sessiz&profile=p1")
        self.assertEqual(body["remote_error"], "sinemalar: HTTP 500")
        self.assertEqual(body["remote_sites"]["sinemalar"]["ok"], False)
        self.assertEqual(body["remote_sites"]["yabancidizi"]["ok"], True)
        self.assertEqual([i["id"] for i in body["items"]][:2], [SERIES, FILM], "the other site's hits are not lost")

    def test_parked_site_is_reported_as_skipped(self):
        config_patch = patch.object(config, "SEARCH_BREAKER_FAILS", 1)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.fail = {"sinemalar": RuntimeError("HTTP 500")}
        self.get("/api/search?q=sessiz&profile=p1")
        body = self.get("/api/search?q=sessiz&profile=p1")
        self.assertEqual(body["remote_sites"]["sinemalar"]["skipped"], "breaker")
        self.assertEqual(body["remote_error"], "sinemalar: temporarily skipped")

    def test_source_parameter_limits_the_remote_search_to_that_site(self):
        self.get("/api/search?q=sessiz&profile=p1&source=sinemalar")
        self.assertEqual(self.calls, ["sinemalar"])
        self.calls.clear()
        self.get("/api/search?q=sessiz&profile=p1&source=yabancidizi")
        self.assertEqual(self.calls, ["yabancidizi"])

    def test_source_without_a_search_endpoint_or_unknown_stays_local(self):
        self.sites = ["yabancidizi"]
        body = self.get("/api/search?q=sessiz&profile=p1&source=sinemalar")      # a real site, but it cannot search
        self.assertEqual((body["remote"], body["remote_error"], body["remote_sites"]), (False, None, {}))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.get("/api/search?q=sessiz&profile=p1&source=zzz", 400)["error"]["code"], "bad_request")

    def test_short_query_is_local_only(self):
        body = self.get("/api/search?q=ka&profile=p1&limit=3")
        self.assertEqual((body["remote"], body["remote_error"], body["remote_sites"]), (False, None, {}))
        self.assertEqual(self.calls, [])

    def test_every_item_carries_its_source_options(self):
        body = self.get("/api/search?q=sessiz&profile=p1&limit=10")
        by_id = {i["id"]: i for i in body["items"]}
        self.assertEqual([o["site"] for o in by_id[FILM]["source_options"]], ["yabancidizi", "sinemalar"])
        self.assertEqual(by_id[SERIES]["source_options"][0]["episodes"], 7)
        self.assertEqual(by_id[SERIES]["sources"], ["yabancidizi"], "the old `sources` (site ids) is untouched")
        for item in body["items"]:
            for o in item["source_options"]:
                self.assertEqual(sorted(o), ["episodes", "kind", "name", "site", "status"])
        local = self.get("/api/search?q=ka&profile=p1&limit=3")
        self.assertTrue(all("source_options" in i for i in local["items"]))

    def test_a_crash_in_the_remote_step_degrades_to_local_results(self):
        with patch.object(search_all, "search_sites", side_effect=RuntimeError("boom")):
            body = self.get("/api/search?q=sessiz&profile=p1")
        self.assertEqual((body["remote"], body["remote_error"]), (True, "boom"))
        self.assertEqual(body["items"][0]["id"], NOPOSTER)


if __name__ == "__main__":
    unittest.main()
