"""Archive pass (library/archive.py): index/page parsing, batch limits, cursor persistence, end of the film pages, the OFF switch,
the admin batch endpoint (+409), drift -> no cursor move + heal. No network: ``fetch.page`` and the series read are fakes."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import autoscan, config, db
from app.errors import ApiError
from app.library import archive, ingest, series_crawl
from app.routers import ops_sites
from app.scraper import config as scfg, fetch, heal_agent, state as sstate

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BASE = "https://yabancidizi.news"
NOW = 1_800_000_000.0
LOOSE = {"min_fill_ratio": 0.5, "critical_field_fill": {"title": 0.8, "detail_url": 0.8}}


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


INDEX = read("yabancidizi_series_index.html")
MOVIES1 = read("yabancidizi_movies_p1.html")
MOVIES2 = MOVIES1.replace('href="film/', 'href="film/p2-')


class Base(unittest.TestCase):
    def setUp(self):
        db.init()
        conn = db.connect()
        for table in ("source_items", "library_items", "video_sources", "blocked_pages"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        try:
            os.remove(archive._path("yabancidizi"))
        except OSError:
            pass
        real = scfg.load_site("yabancidizi")
        self.cfg = real
        self.data = copy.deepcopy(real.data)
        self.sched = {"enabled": True, "interval_hours": 10 / 60.0}   # the site's admin auto scan switch + interval
        self.data.pop("availability_gate", None)
        self.data.pop("blocked", None)
        real.data = self.data
        self.pages = {BASE + "/dizi-izle-hd": INDEX, BASE + "/film-izle-hd": MOVIES1, BASE + "/film-izle-hd/2": MOVIES2}
        self.fetched = []
        self.crawled = []
        for p in (patch.object(scfg, "load_site", lambda site: self.cfg),
                  patch.object(scfg, "list_sites", lambda: ["yabancidizi"]),
                  patch.object(archive.settings, "site_settings", lambda site: {**self.sched, "source": "admin"}),
                  patch.object(fetch, "page", self.fake_page),
                  patch.object(ingest, "_enrich_series_catalogs", self.fake_crawl),
                  patch.object(archive, "rules_of", lambda cfg, part: LOOSE),
                  patch.object(archive.seasons, "auto_enrich", lambda *a, **k: {}),
                  patch.object(config, "SERIES_CRAWL_DELAY", 0),
                  patch.object(archive, "start_heal", self.fake_heal),
                  patch.object(archive, "_series_drift_heal", lambda site, lines: self.heals.append(("series", lines)) or "started")):
            p.start()
            self.addCleanup(p.stop)
        self.heals = []
        self.crawl_status = "success"
        self.crawl_error = ""

    def fake_heal(self, site, evidence, trigger="archive"):
        self.heals.append(("archive", evidence))
        return "started"

    def fake_page(self, cfg, url, **kw):
        self.fetched.append(url)
        if url not in self.pages:
            raise fetch.FetchError("http_404", status=404)
        return self.pages[url]

    def fake_crawl(self, cfg, by_key, raw_by_key, reports, errors, *, force=False, cards=None):
        for key, norm in by_key.items():
            self.crawled.append(key)
            if self.crawl_status == "success":
                norm["video_sources"] = [{"kind": "episode", "season": 1, "episode": 1, "url": f"{BASE}/{key}/sezon-1/bolum-1",
                                          "provider": "page"}]
                norm[series_crawl.MARKER] = {"at": int(NOW), "ok_at": int(NOW), "fails": 0, "complete": True}
                reports.append({"status": "success", "count": 1, "url": f"{BASE}/{key}"})
            else:
                errors.append({"list": key, "error": self.crawl_error})
                reports.append({"status": "error", "error": self.crawl_error, "url": f"{BASE}/{key}"})

    def keys(self):
        return {r["source_key"] for r in db.query("SELECT source_key FROM source_items WHERE source='yabancidizi'")}


class ParseTests(Base):
    def test_fixture_rows(self):
        spec = archive.spec_of(self.cfg)
        rows, _m, verdict = archive.parse_part(self.cfg, "series_index", spec["series_index"], INDEX)
        self.assertEqual((len(rows), verdict["drift"]), (30, False))
        self.assertEqual((rows[0]["title"], rows[0]["detail_url"]), ("#1 Happy Family USA", "dizi/1-happy-family-usa-izle-6"))
        self.assertTrue(rows[0]["poster_url"].startswith("/uploads/series/cover/"))
        films, _m, verdict = archive.parse_part(self.cfg, "movie_pages", spec["movie_pages"], MOVIES1)
        self.assertEqual((len(films), verdict["drift"]), (20, False))
        self.assertEqual(films[0]["detail_url"], "film/super-scam-me")
        self.assertEqual(archive.page_url(self.cfg, spec["movie_pages"], 3), BASE + "/film-izle-hd/3")

    def test_real_baseline_thresholds(self):
        patch.stopall()   # the site's own baseline section: a cut-down index (30 rows) is a drift, a film page is not
        cfg = scfg.load_site("yabancidizi")
        spec = archive.spec_of(cfg)
        self.assertTrue(archive.parse_part(cfg, "series_index", spec["series_index"], INDEX)[2]["drift"])
        self.assertFalse(archive.parse_part(cfg, "movie_pages", spec["movie_pages"], MOVIES1)[2]["drift"])
        self.assertNotIn("enabled", spec)   # yaml archive.enabled / interval_minutes: old keys, ignored


class BatchTests(Base):
    def test_batch_limits_cursor_and_resume(self):
        out = archive.run_batch("yabancidizi", now=NOW)
        self.assertIsNone(out["error"], out)
        self.assertEqual((out["series"]["index"], out["series"]["queued"], out["series"]["taken"]), (30, 30, 10))
        self.assertEqual((out["movies"]["pages"], out["movies"]["queued"], out["movies"]["taken"]), (2, 40, 40))
        self.assertEqual(len(self.keys()), 50)
        self.assertFalse(archive.running("yabancidizi"))
        st = archive.load_state("yabancidizi")   # persisted on disk
        self.assertEqual(st["movies"]["next_page"], 3)
        first = self.crawled[:]
        # resume: no index fetch (refresh_hours), the next 10 series, page 3 = 404 -> film archive done
        self.fetched.clear()
        out = archive.run_batch("yabancidizi", now=NOW + 600)
        self.assertNotIn(BASE + "/dizi-izle-hd", self.fetched)
        self.assertEqual(out["series"]["taken"], 10)
        self.assertFalse(set(first) & set(self.crawled[10:]))
        st = archive.load_state("yabancidizi")
        self.assertTrue(st["movies"]["done"])
        status = archive.status("yabancidizi")
        self.assertEqual((status["series"]["processed"], status["series"]["total"]), (20, 30))
        self.assertTrue(status["movies"]["done"])
        # done: film pages only once per refresh_hours, no page is read again before
        self.fetched.clear()
        archive.run_batch("yabancidizi", now=NOW + 1200)
        self.assertEqual(self.fetched, [])

    def test_repeated_or_empty_page_ends(self):
        self.pages[BASE + "/film-izle-hd/2"] = MOVIES1   # the site repeats its last page
        archive.run_batch("yabancidizi", now=NOW)
        self.assertTrue(archive.load_state("yabancidizi")["movies"]["done"])
        os.remove(archive._path("yabancidizi"))
        self.pages[BASE + "/film-izle-hd/2"] = "<html><div id='router-view'></div></html>"
        archive.run_batch("yabancidizi", now=NOW)
        self.assertTrue(archive.load_state("yabancidizi")["movies"]["done"])

    def test_known_title_needs_no_request(self):
        db.execute("INSERT INTO source_items (source,source_key,canonical_id,raw,normalized,fetched_at) VALUES "
                   "('yabancidizi','dizi/1-happy-family-usa-izle-6','x','{}','{}',1)")
        archive.run_batch("yabancidizi", now=NOW)
        self.assertNotIn("dizi/1-happy-family-usa-izle-6", self.crawled)
        self.assertEqual(archive.load_state("yabancidizi")["series"]["items"]["dizi/1-happy-family-usa-izle-6"]["st"], "exists")

    def test_failed_read_retries_at_most_3(self):
        self.crawl_status, self.crawl_error = "error", "http_503"   # infra: not a drift
        archive.run_batch("yabancidizi", now=NOW)
        entry = archive.load_state("yabancidizi")["series"]["items"]["dizi/1-happy-family-usa-izle-6"]
        self.assertEqual((entry["st"], entry["n"]), ("retry", 1))
        for i, hours in enumerate((1, 6)):
            archive.run_batch("yabancidizi", now=NOW + 3600 * (hours + 1) * (i + 1) + 3600 * 30)
        entry = archive.load_state("yabancidizi")["series"]["items"]["dizi/1-happy-family-usa-izle-6"]
        self.assertEqual(entry["st"], "failed")
        self.assertEqual(self.heals, [])


class DriftTests(Base):
    def test_index_drift_moves_nothing_and_heals(self):
        self.pages[BASE + "/dizi-izle-hd"] = INDEX.replace("segment-poster-sm", "poster-card")
        out = archive.run_batch("yabancidizi", now=NOW)
        self.assertEqual(out["drift"]["part"], "series_index")
        st = archive.load_state("yabancidizi")
        self.assertEqual((st["series"]["index_at"], st["series"]["items"], st["movies"]["next_page"]), (None, {}, 1))
        self.assertEqual(st["last_drift"]["part"], "series_index")
        kind, evidence = self.heals[0]
        self.assertEqual((evidence["kind"], evidence["failing"][0]["stage"]), ("archive", "archive.series_index"))
        self.assertEqual(heal_agent.layers_of(evidence), ["archive"])
        self.assertIn("archive.series_index", heal_agent.repair_message(self.cfg, evidence, "archive"))
        self.assertEqual(self.keys(), set())

    def test_movie_page_drift_keeps_cursor(self):
        self.data["archive"]["batch"]["series"] = 0
        self.pages[BASE + "/film-izle-hd/2"] = MOVIES2.replace('href="film/', 'data-x="')   # rows without a detail link
        out = archive.run_batch("yabancidizi", now=NOW)
        self.assertEqual(out["drift"]["part"], "movie_pages")
        self.assertEqual(archive.load_state("yabancidizi")["movies"]["next_page"], 2)   # page 1 was fine, page 2 is read again

    def test_series_pages_all_unparsable_revert(self):
        self.crawl_status, self.crawl_error = "error", "sayfa bir dizi sayfası gibi görünmüyor"
        out = archive.run_batch("yabancidizi", now=NOW)
        self.assertEqual(out["drift"]["part"], "series_page")
        items = archive.load_state("yabancidizi")["series"]["items"]
        self.assertTrue(all(e["st"] == "pending" and not e.get("n") for e in items.values()))
        self.assertEqual(self.heals[0][0], "series")
        self.assertEqual(self.keys(), set())   # a drifted batch writes nothing, not even its films


class SwitchTests(Base):
    def test_off_means_no_request(self):
        self.sched["enabled"] = False   # the site's auto scan switch is off
        self.data["archive"]["enabled"] = True   # old yaml key: ignored
        self.assertEqual(archive.tick(NOW), [])
        with patch.object(autoscan, "due_sites", lambda now: []):
            self.assertEqual(autoscan.tick(NOW), [])
        self.assertEqual(self.fetched, [])

    def test_on_runs_when_due(self):
        calls = []
        self.assertEqual(archive.tick(NOW, run=lambda site, **kw: calls.append(site)), ["yabancidizi"])
        archive.run_batch("yabancidizi", now=NOW)
        self.assertEqual(archive.tick(NOW + 60, run=lambda site, **kw: calls.append(site)), [])   # scan interval: 10 min

    def test_interval_is_the_scan_interval(self):
        self.sched["interval_hours"] = 2.0
        self.data["archive"]["interval_minutes"] = 1   # old yaml key: ignored
        archive.run_batch("yabancidizi", now=NOW)
        self.assertFalse(archive.due("yabancidizi", self.cfg, NOW + 3600))
        self.assertTrue(archive.due("yabancidizi", self.cfg, NOW + 7200))
        st = archive.status("yabancidizi", self.cfg, now=NOW)
        self.assertEqual((st["enabled"], st["interval_minutes"]), (True, 120.0))
        self.assertEqual(st["next_batch_at"], archive._iso(NOW + 7200))

    def test_yaml_off_but_switch_on_runs(self):
        self.data["archive"]["enabled"] = False   # old yaml key: ignored, the switch decides
        self.assertTrue(archive.due("yabancidizi", self.cfg, NOW))
        self.assertEqual(archive.tick(NOW, run=lambda site, **kw: None), ["yabancidizi"])

    def test_scan_first_batch_next_tick(self):
        with patch.object(autoscan, "due_sites", lambda now: ["yabancidizi"]):
            ran = autoscan.tick(NOW, ingest=lambda site, **kw: {}, archive=lambda site, **kw: None, gap=0)
        self.assertEqual(ran, ["yabancidizi"])   # scanned in this tick: the archive batch waits
        with patch.object(autoscan, "due_sites", lambda now: []):
            self.assertEqual(autoscan.tick(NOW + 60, archive=lambda site, **kw: None), ["yabancidizi#archive"])

    def test_archive_series_not_reread_unless_home(self):
        norm = {"type": "series", "_archive": {"at": 1}, series_crawl.MARKER: {"fails": 0, "ok_at": 1, "complete": True}}
        self.assertTrue(series_crawl.archive_frozen(norm, "stale"))
        self.assertTrue(series_crawl.archive_frozen(norm, "aired"))
        self.assertFalse(series_crawl.archive_frozen(norm, "new_episode"))
        norm[series_crawl.MARKER]["fails"] = 1
        self.assertFalse(series_crawl.archive_frozen(norm, "retry"))
        norm[series_crawl.MARKER]["fails"] = 3
        self.assertTrue(series_crawl.archive_frozen(norm, "retry"))
        self.assertFalse(series_crawl.archive_frozen({"type": "series"}, "stale"))


class EndpointTests(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ops_sites.router)

        @app.exception_handler(ApiError)
        def _err(request, exc):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        self.c = TestClient(app)
        self.started = []
        p = patch.object(ops_sites, "_bg_archive", lambda site: self.started.append(site))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: [sstate.activity_end("yabancidizi", k) for k in ("archive", "scan")])

    def test_batch_button_and_409(self):
        r = self.c.post("/api/ops/sites/yabancidizi/archive-batch")
        self.assertEqual((r.status_code, r.json()), (200, {"started": True}))
        self.assertEqual(self.started, ["yabancidizi"])
        self.assertEqual(self.c.post("/api/ops/sites/yabancidizi/archive-batch").status_code, 409)   # the batch still runs
        sstate.activity_end("yabancidizi", "archive")
        sstate.activity_start("yabancidizi", "scan", "manual")
        r = self.c.post("/api/ops/sites/yabancidizi/archive-batch")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "busy"))
        row = self.c.get("/api/ops/sites/manage").json()["sites"][0]
        self.assertEqual((row["archive"]["enabled"], row["archive"]["series"]["total"]), (True, 0))


if __name__ == "__main__":
    unittest.main()
