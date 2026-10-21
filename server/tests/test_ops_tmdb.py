"""Admin "TMDB zenginleştirme": ayarlar, durum, backfill işi, önizleme, olay kaydı, diziler.

Ağsız: TMDB tamamen sahte (`tmdb._get`), `httpx.get` gerçek ağa çıkarsa test düşer; geçici DB / ayar /
durum / önizleme dosyaları. Anahtar sahte ve asla yazdırılmaz.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cache, config, db, images, settings
from app.library import enrich, ingest as ingest_module, tmdb, tmdb_admin
from app.routers import ops, ops_library, ops_settings, ops_tmdb
from app.scraper import state as sstate
from app.scraper.runner import RunResult
from app.sources.library import LibrarySource

KEY = "k" * 32  # fake v3 key; never a real credential
NOW = 1_800_000_000


def jload(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def jdump(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh)


# --- a tiny fake TMDB ---------------------------------------------------------------------

def movie_hit(tid, title, year, original=None):
    return {"id": tid, "title": title, "original_title": original or title,
            "release_date": "%s-03-01" % year, "poster_path": "/h%d.jpg" % tid}


def tv_hit(tid, name, year, original=None):
    return {"id": tid, "name": name, "original_name": original or name,
            "first_air_date": "%s-09-01" % year, "poster_path": "/h%d.jpg" % tid}


def detail(tid, kind="movie"):
    d = {"id": tid, "imdb_id": "tt%07d" % tid if kind == "movie" else None,
         "overview": "TMDB özeti %d" % tid, "original_title": "Orig %d" % tid,
         "genres": [{"name": "Aksiyon"}], "vote_average": 7.5, "vote_count": 100,
         "poster_path": "/p%d.jpg" % tid, "backdrop_path": "/b%d.jpg" % tid,
         "images": {"posters": [{"file_path": "/tr%d.jpg" % tid, "iso_639_1": "tr", "vote_average": 5, "width": 800}],
                    "backdrops": [{"file_path": "/bk%d.jpg" % tid, "iso_639_1": None, "vote_average": 5, "width": 1920}]},
         "external_ids": {"imdb_id": "tt%07d" % tid}}
    if kind == "movie":
        d["runtime"] = 101
    else:
        d["episode_run_time"] = [45]
        d["imdb_id"] = None
    return d


class FakeTmdb:
    """``search`` maps a lower-cased query to hits; details are generated per id."""

    def __init__(self, movies=None, tv=None, alt=None):
        self.movies, self.tv, self.alt = movies or {}, tv or {}, alt or {}
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, path, **params):
        with self.lock:
            self.calls.append((path, params.get("query"), params.get("append_to_response")))
        kind = "tv" if "/tv" in path else "movie"
        if path.startswith("/search/"):
            table = self.tv if kind == "tv" else self.movies
            return {"results": table.get((params.get("query") or "").lower(), [])}
        if path.startswith("/movie/") or path.startswith("/tv/"):
            tid = int(path.rsplit("/", 1)[-1])
            if "alternative_titles" in (params.get("append_to_response") or ""):
                return self.alt.get(tid, {})
            return detail(tid, kind)
        raise AssertionError(path)

    def searches(self):
        return [c for c in self.calls if c[0].startswith("/search/")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        for p in (patch.object(config, "DB_PATH", os.path.join(t, "t.db")),
                  patch.object(config, "IMG_CACHE_DIR", os.path.join(t, "imgcache")),
                  patch.object(settings, "SETTINGS_PATH", os.path.join(t, "data", "ops_settings.json")),
                  patch.object(sstate, "STATE_DIR", os.path.join(t, "state")),
                  patch.object(tmdb_admin, "PREVIEW_PATH", os.path.join(t, "data", "tmdb_preview.json")),
                  patch.dict(os.environ, {"TMDB_ACCESS_KEY": KEY, "TMDB_TOKEN": "", "TMDB_API_KEY": ""})):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(config.IMG_CACHE_DIR, exist_ok=True)
        sstate._active.clear()
        self.addCleanup(sstate._active.clear)
        # any real HTTP attempt is recorded and fails the test in tearDown
        self.network = []

        def no_net(*a, **k):
            self.network.append(a)
            raise tmdb.httpx.ConnectError("blocked in tests")
        p = patch.object(tmdb.httpx, "get", no_net)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: self.assertEqual(self.network, []))
        db.init()
        self.n = 0
        app = FastAPI()
        for r in (ops, ops_settings, ops_tmdb, ops_library):
            app.include_router(r.router)
        self.c = TestClient(app)

    def use(self, fake):
        p = patch.object(tmdb, "_get", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def add(self, cid, title, year=2020, type="movie", **cols):
        """One canonical row + its source row (so merge_canonical can rebuild it)."""
        self.n += 1
        norm = {"source_key": cid + "-k", "type": type, "title": title, "year": year,
                "poster_url": cols.get("poster_url"), "backdrop_url": cols.get("backdrop_url"),
                "_added_at": NOW + self.n}
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO library_items(id,tmdb_id,type,title,year,poster_url,backdrop_url,added_at,updated_at,"
                "tmdb_poster_url,tmdb_backdrop_url,tmdb_enrich_status,tmdb_checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, cols.get("tmdb_id"), type, title, year, cols.get("poster_url"), cols.get("backdrop_url"),
                 NOW + self.n, NOW + self.n, cols.get("tmdb_poster_url"), cols.get("tmdb_backdrop_url"),
                 cols.get("status"), cols.get("checked_at")))
            conn.execute("INSERT INTO source_items(source,source_key,canonical_id,normalized,fetched_at) VALUES (?,?,?,?,?)",
                         ("yabancidizi", cid + "-k", cid, json.dumps(norm), NOW))
        return cid

    def row(self, cid):
        return db.query_one("SELECT * FROM library_items WHERE id=?", (cid,))

    def rows(self):
        return {r["title"]: r for r in db.query("SELECT * FROM library_items")}

    def events(self):
        return sstate.list_ops("tmdb", None, 50)

    def trio(self):
        """One auto match, one ambiguous (review), one unknown (unmatched) movie."""
        self.add("m-auto", "Mayday", 2026)
        self.add("m-review", "Belirsiz", 2020)
        self.add("m-none", "Hic Yok", 2019)
        return self.use(FakeTmdb(movies={
            "mayday": [movie_hit(42, "Mayday", 2026)],
            "belirsiz": [movie_hit(50, "Belirsiz", 2020), movie_hit(51, "Belirsiz", 2020)],
        }))


# --- settings ------------------------------------------------------------------------------

class SettingsTests(Base):
    def test_defaults_and_env_default_for_types(self):
        self.assertEqual(settings.tmdb_settings(), {"auto": True, "types": ["movie"]})
        with patch.object(config, "TMDB_ENRICH_TYPES", {"movie", "series"}):
            self.assertEqual(settings.tmdb_types(), ["movie", "series"])
        with patch.object(config, "TMDB_ENRICH_TYPES", {"series"}):
            self.assertEqual(settings.tmdb_types(), ["series"])
        with patch.object(config, "TMDB_ENRICH_TYPES", {"junk"}):
            self.assertEqual(settings.tmdb_types(), ["movie"])
        self.assertFalse(os.path.exists(settings.SETTINGS_PATH))  # reading never writes

    def test_put_persists_and_reads_dynamically(self):
        r = self.c.put("/api/ops/settings", json={"tmdb": {"auto": False, "types": ["series", "movie"]}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tmdb"], {"configured": True, "auto": False, "types": ["movie", "series"]})
        saved = jload(settings.SETTINGS_PATH)
        self.assertEqual((saved["tmdb_auto"], saved["tmdb_types"]), (False, ["movie", "series"]))
        # no restart / cache: the very next reads see it, and the env default no longer wins
        self.assertFalse(settings.tmdb_auto())
        with patch.object(config, "TMDB_ENRICH_TYPES", {"movie"}):
            self.assertEqual(settings.tmdb_types(), ["movie", "series"])
        # partial update keeps the other value; empty list = no type at all
        self.c.put("/api/ops/settings", json={"tmdb": {"auto": True}})
        d = self.c.get("/api/ops/settings").json()["tmdb"]
        self.assertEqual((d["auto"], d["types"]), (True, ["movie", "series"]))
        self.assertEqual(self.c.put("/api/ops/settings", json={"tmdb": {"types": []}}).json()["tmdb"]["types"], [])
        self.assertFalse(enrich.type_enabled("movie"))

    def test_view_reports_key_presence_never_the_key(self):
        d = self.c.get("/api/ops/settings")
        self.assertTrue(d.json()["tmdb"]["configured"])
        self.assertNotIn(KEY, d.text)
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "", "TMDB_TOKEN": "", "TMDB_API_KEY": ""}):
            self.assertFalse(self.c.get("/api/ops/settings").json()["tmdb"]["configured"])
            self.assertFalse(enrich.auto_enabled())  # switch on but no key: inert
        self.assertNotIn(KEY, json.dumps(self.c.get("/api/ops/tmdb/status").json()))

    def test_validation_422_and_nothing_saved(self):
        for body in ({"tmdb": []}, {"tmdb": {"auto": "yes"}}, {"tmdb": {"types": "movie"}},
                     {"tmdb": {"types": ["movie", "anime"]}}, {"tmdb": {"types": [1]}},
                     {"tmdb": {"configured": True}}, {"tmdb": {"key": "x"}}, {"tmdb_auto": False}):
            self.assertEqual(self.c.put("/api/ops/settings", json=body).status_code, 422, body)
        self.assertFalse(os.path.exists(settings.SETTINGS_PATH))

    def test_corrupt_values_fall_back_to_defaults(self):
        os.makedirs(os.path.dirname(settings.SETTINGS_PATH), exist_ok=True)
        jdump(settings.SETTINGS_PATH, {"tmdb_auto": "no", "tmdb_types": ["movie", "bogus"]})
        self.assertEqual(settings.tmdb_settings(), {"auto": True, "types": ["movie"]})


class IngestDynamicTests(Base):
    """`tmdb_auto` / `tmdb_types` are read on every ingest (no restart)."""

    def run_ingest(self, items):
        result = RunResult("yabancidizi", items=items, drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module, "_enrich_series_catalogs"), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source("yabancidizi")

    def film(self, title="Mayday", year=2026, series=False):
        return {"title": title, "year": year, "detail_url": ("dizi/" if series else "film/") + title.lower().replace(" ", "-"),
                "poster_url": "/uploads/series/x.jpg", "genres": ["Aksiyon"]}

    def test_auto_off_never_enriches_and_on_again_does(self):
        fake = self.use(FakeTmdb(movies={"mayday": [movie_hit(42, "Mayday", 2026)]}))
        settings.update({"tmdb": {"auto": False}})
        res = self.run_ingest([self.film()])
        self.assertEqual(fake.calls, [])
        self.assertEqual((res["tmdb_enrich"]["matched"], res["tmdb_enrich"]["skipped"]), (0, 0))
        self.assertIsNone(db.query_one("SELECT tmdb_poster_url p FROM library_items")["p"])
        self.assertIsNone(db.query_one("SELECT tmdb_enrich_status s FROM library_items")["s"])
        settings.update({"tmdb": {"auto": True}})
        res = self.run_ingest([self.film()])
        self.assertEqual(res["tmdb_enrich"]["matched"], 1)
        self.assertTrue(db.query_one("SELECT tmdb_poster_url p FROM library_items")["p"])

    def test_series_only_enriched_when_selected(self):
        fake = self.use(FakeTmdb(tv={"mayday": [tv_hit(7, "Mayday", 2026)]}))
        res = self.run_ingest([self.film(series=True)])
        self.assertEqual(fake.calls, [])  # default types = movie
        self.assertEqual(res["tmdb_enrich"]["skipped"], 1)
        settings.update({"tmdb": {"types": ["movie", "series"]}})
        res = self.run_ingest([self.film(series=True)])
        self.assertEqual(res["tmdb_enrich"]["matched"], 1)
        row = db.query_one("SELECT * FROM library_items")
        self.assertEqual((row["type"], row["tmdb_id"]), ("series", 7))
        self.assertIn("/t/p/w500/", row["tmdb_poster_url"])


# --- status / preview endpoints ----------------------------------------------------------------

class StatusTests(Base):
    def test_coverage_counts(self):
        self.add("a", "A", tmdb_id=1, status="matched", poster_url="https://x/p.jpg", tmdb_poster_url="https://image.tmdb.org/t/p/w500/a.jpg",
                 tmdb_backdrop_url="https://image.tmdb.org/t/p/w1280/a.jpg")
        self.add("b", "B", status="review", poster_url="https://cdn/no-poster.svg")
        self.add("c", "C", poster_url="https://cdn/real.jpg", backdrop_url="https://cdn/bd.jpg")
        self.add("d", "D")  # nothing at all
        self.add("s1", "S1", type="series", tmdb_id=9, poster_url="https://cdn/x.svg")
        self.add("s2", "S2", type="series")
        d = self.c.get("/api/ops/tmdb/status").json()
        self.assertTrue(d["configured"])
        self.assertEqual((d["auto"], d["types"]), (True, ["movie"]))
        self.assertEqual(d["coverage"]["movie"], {"total": 4, "matched": 1, "review": 1, "unmatched": 2,
                                                  "tmdb_poster": 1, "no_poster": 2, "no_backdrop": 2})
        self.assertEqual(d["coverage"]["series"], {"total": 2, "matched": 1, "review": 0, "unmatched": 1,
                                                   "tmdb_poster": 0, "no_poster": 2, "no_backdrop": 2})
        self.assertIsNone(d["job"])
        self.assertIsNone(d["preview"])
        self.assertIsNone(d["last_job"])
        self.assertEqual(d["retry_days"], config.TMDB_RETRY_DAYS)
        with patch.object(images, "prewarm_running", return_value=True):
            self.assertTrue(self.c.get("/api/ops/tmdb/status").json()["prewarm"])

    def test_empty_library(self):
        d = self.c.get("/api/ops/tmdb/status").json()
        self.assertEqual(d["coverage"]["movie"]["total"], 0)
        self.assertEqual(self.c.get("/api/ops/tmdb/preview").json(),
                         {"available": False, "summary": None, "total": 0, "limit": 50, "offset": 0,
                          "has_more": False, "items": []})

    def test_preview_paging_filter_and_validation(self):
        fake = self.trio()
        for i in range(4):
            self.add("x%d" % i, "Ekstra %d" % i, 2010 + i)
        self.use(FakeTmdb(movies={**fake.movies, **{"ekstra %d" % i: [movie_hit(100 + i, "Ekstra %d" % i, 2010 + i)]
                                                    for i in range(4)}}))
        self.assertEqual(self.c.post("/api/ops/tmdb/backfill", json={"kind": "movie", "dry_run": True}).status_code, 200)
        p = self.c.get("/api/ops/tmdb/preview?limit=3&offset=0").json()
        self.assertTrue(p["available"])
        self.assertEqual((p["total"], len(p["items"]), p["has_more"]), (7, 3, True))
        self.assertNotIn("data", p["items"][0])  # apply payload never sent to the UI
        page2 = self.c.get("/api/ops/tmdb/preview?limit=3&offset=3").json()
        page3 = self.c.get("/api/ops/tmdb/preview?limit=3&offset=6").json()
        self.assertEqual((len(page2["items"]), page2["has_more"], len(page3["items"]), page3["has_more"]), (3, True, 1, False))
        ids = [i["id"] for i in p["items"] + page2["items"] + page3["items"]]
        self.assertEqual(len(set(ids)), 7)
        for dec, n in (("auto", 5), ("review", 1), ("unmatched", 1)):
            got = self.c.get("/api/ops/tmdb/preview?decision=" + dec).json()
            self.assertEqual((got["total"], {i["decision"] for i in got["items"]}), (n, {dec}), dec)
        self.assertEqual(p["summary"]["counts"]["auto"], 5)
        self.assertEqual(self.c.get("/api/ops/tmdb/preview?decision=bogus").status_code, 422)
        self.assertEqual(self.c.get("/api/ops/tmdb/preview?limit=0").status_code, 422)
        item = next(i for i in self.c.get("/api/ops/tmdb/preview?decision=auto").json()["items"] if i["title"] == "Mayday")
        self.assertEqual((item["candidate"]["tmdb_id"], item["candidate"]["year"]), (42, 2026))
        self.assertTrue(item["candidate"]["poster_url"].startswith("https://image.tmdb.org/t/p/w92/"))
        self.assertGreaterEqual(item["score"], 0.85)
        rev = self.c.get("/api/ops/tmdb/preview?decision=review").json()["items"][0]
        self.assertIn("ambiguous", rev["reason"])
        self.assertEqual(rev["candidate"]["poster_url"], "https://image.tmdb.org/t/p/w92/h50.jpg")

    def test_corrupt_preview_is_ignored(self):
        os.makedirs(os.path.dirname(tmdb_admin.PREVIEW_PATH), exist_ok=True)
        with open(tmdb_admin.PREVIEW_PATH, "w") as fh:
            fh.write("{oops")
        self.assertFalse(self.c.get("/api/ops/tmdb/preview").json()["available"])
        self.assertIsNone(self.c.get("/api/ops/tmdb/status").json()["preview"])


# --- backfill job -----------------------------------------------------------------------------

class BackfillTests(Base):
    def post(self, **body):
        return self.c.post("/api/ops/tmdb/backfill", json={"kind": "movie", **body})

    def test_dry_run_writes_nothing_but_preview_and_event(self):
        self.trio()
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["started"] and r.json()["dry_run"])
        for row in self.rows().values():
            self.assertIsNone(row["tmdb_poster_url"])
            self.assertIsNone(row["tmdb_enrich_status"])
            self.assertIsNone(row["tmdb_id"])
        refresh.assert_not_called()
        self.assertEqual(db.query("SELECT * FROM identity_reviews"), [])
        pv = jload(tmdb_admin.PREVIEW_PATH)
        self.assertEqual((pv["kind"], len(pv["items"])), ("movie", 3))
        self.assertEqual(sorted(os.listdir(os.path.dirname(tmdb_admin.PREVIEW_PATH))), ["tmdb_preview.json"])  # atomic: no tmp left
        ev = self.events()[0]
        self.assertEqual((ev["dry_run"], ev["status"], ev["media_type"], ev["written"]), (True, "success", "movie", 0))
        self.assertEqual((ev["matched"], ev["review"], ev["unmatched"]), (1, 1, 1))
        self.assertIn("seconds", ev)
        self.assertIsNone(sstate.activity_list() or None)  # slot released

    def test_apply_writes_only_auto_and_refreshes_catalogue(self):
        self.trio()
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=False)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["from_preview"])  # no preview existed: live
        rows = self.rows()
        auto = rows["Mayday"]
        self.assertEqual((auto["tmdb_id"], auto["tmdb_enrich_status"]), (42, "matched"))
        self.assertEqual(auto["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/tr42.jpg")
        self.assertEqual(auto["tmdb_backdrop_url"], "https://image.tmdb.org/t/p/w1280/bk42.jpg")
        self.assertEqual(auto["overview"], "TMDB özeti 42")  # gap filled
        # review / unmatched: no artwork, no ids; existing bookkeeping (status stamp, review queue) only
        for title, status in (("Belirsiz", "review"), ("Hic Yok", "unmatched")):
            self.assertEqual(rows[title]["tmdb_enrich_status"], status)
            self.assertIsNone(rows[title]["tmdb_poster_url"])
            self.assertIsNone(rows[title]["tmdb_id"])
        self.assertEqual([r["reason"] for r in db.query("SELECT reason FROM identity_reviews")], ["tmdb_low_confidence"])
        refresh.assert_called_once_with()
        ev = self.events()[0]
        self.assertEqual((ev["dry_run"], ev["written"], ev["matched"], ev["review"], ev["unmatched"]), (False, 1, 1, 1, 1))
        self.assertFalse(ev["from_preview"])

    def test_source_fields_are_not_overwritten(self):
        self.add("m", "Mayday", 2026, poster_url="https://cdn.sinemalar.com/p.jpg")
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET overview='Kaynak özeti' WHERE id='m'")
            conn.execute("UPDATE source_items SET normalized=json_set(normalized,'$.overview','Kaynak özeti')")
        self.use(FakeTmdb(movies={"mayday": [movie_hit(42, "Mayday", 2026)]}))
        with patch.object(cache, "refresh_and_prewarm"):
            self.post(dry_run=False)
        row = self.row("m")
        self.assertEqual(row["overview"], "Kaynak özeti")
        self.assertEqual(row["poster_url"], "https://cdn.sinemalar.com/p.jpg")
        self.assertEqual(row["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/tr42.jpg")

    def test_apply_from_fresh_preview_makes_no_tmdb_queries(self):
        fake = self.trio()
        self.post(dry_run=True)
        first_calls = len(fake.calls)
        self.assertGreater(first_calls, 0)
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=False)
        self.assertTrue(r.json()["from_preview"])
        self.assertEqual(len(fake.calls), first_calls)  # not a single new request
        self.assertEqual(self.row("m-auto")["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/tr42.jpg")
        self.assertIsNone(self.row("m-review")["tmdb_poster_url"])
        refresh.assert_called_once_with()
        ev = self.events()[0]
        self.assertTrue(ev["from_preview"] and not ev["dry_run"])
        self.assertEqual((ev["written"], ev["matched"], ev["review"], ev["unmatched"]), (1, 1, 1, 1))
        # the preview is now consumed: an immediate second apply is live (and finds nothing left to do)
        summary = self.c.get("/api/ops/tmdb/status").json()["preview"]
        self.assertTrue(summary["applied"] and not summary["fresh"])
        r2 = self.post(dry_run=False)
        self.assertFalse(r2.json()["from_preview"])

    def test_stale_or_other_kind_preview_runs_live(self):
        fake = self.trio()
        self.post(dry_run=True)
        n = len(fake.calls)
        pv = jload(tmdb_admin.PREVIEW_PATH)
        pv["created_at"] -= tmdb_admin.PREVIEW_TTL + 5  # 30+ minutes old
        jdump(tmdb_admin.PREVIEW_PATH, pv)
        with patch.object(cache, "refresh_and_prewarm"):
            r = self.post(dry_run=False)
        self.assertFalse(r.json()["from_preview"])
        self.assertGreater(len(fake.calls), n)
        self.assertEqual(self.row("m-auto")["tmdb_enrich_status"], "matched")
        # other media type: a movie preview is never applied as series
        self.post(dry_run=True, force=True)
        settings.update({"tmdb": {"types": ["movie", "series"]}})
        r = self.post(kind="series", dry_run=False)
        self.assertFalse(r.json()["from_preview"])

    def test_preview_apply_skips_rows_changed_meanwhile(self):
        self.trio()
        self.post(dry_run=True)
        with closing(db.connect()) as conn, conn:  # an ingest matched it after the preview
            conn.execute("UPDATE library_items SET tmdb_enrich_status='matched', tmdb_poster_url='https://image.tmdb.org/t/p/w500/new.jpg' WHERE id='m-auto'")
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            self.post(dry_run=False)
        self.assertEqual(self.row("m-auto")["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/new.jpg")
        ev = self.events()[0]
        self.assertEqual((ev["written"], ev["matched"]), (0, 0))
        refresh.assert_not_called()  # nothing written: nothing to re-cache

    def test_limit_and_retry_window(self):
        fake = self.trio()
        self.post(dry_run=True, limit=1)
        self.assertEqual(len(jload(tmdb_admin.PREVIEW_PATH)["items"]), 1)
        with patch.object(cache, "refresh_and_prewarm"):
            self.post(dry_run=False, use_preview=False)
        n = len(fake.calls)
        self.post(dry_run=True)  # matched + freshly stamped titles are not queried again
        self.assertEqual(len(fake.calls), n)
        self.assertEqual(jload(tmdb_admin.PREVIEW_PATH)["items"], [])
        self.post(dry_run=True, force=True)
        self.assertEqual(len(jload(tmdb_admin.PREVIEW_PATH)["items"]), 3)

    def test_errors_409_and_400s(self):
        self.trio()
        sstate.activity_start("tmdb", "tmdb", "manual")
        r = self.post(dry_run=True)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "already_running")
        sstate.activity_end("tmdb", "tmdb")
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "", "TMDB_TOKEN": "", "TMDB_API_KEY": ""}):
            r = self.post(dry_run=True)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"]["message"], "TMDB anahtarı tanımlı değil")
        r = self.post(kind="series", dry_run=True)  # series not selected
        self.assertEqual(r.status_code, 400)
        self.assertIn("Dizi", r.json()["detail"]["message"])
        self.assertEqual(r.json()["detail"]["code"], "tmdb_type_disabled")
        for body in ({"kind": "anime"}, {}, {"kind": "movie", "limit": 0}, {"kind": "movie", "dry_run": "maybe"}):
            self.assertEqual(self.c.post("/api/ops/tmdb/backfill", json=body).status_code, 422, body)
        self.assertEqual(self.events(), [])  # rejected requests leave no event and no job behind
        self.assertEqual(sstate.activity_list(), [])

    def test_progress_is_reported_to_the_job_bar(self):
        for i in range(30):
            self.add("p%d" % i, "Film %d" % i, 2000 + i % 20)
        self.use(FakeTmdb(movies={"film %d" % i: [movie_hit(200 + i, "Film %d" % i, 2000 + i % 20)] for i in range(30)}))
        seen = []
        real = sstate.activity_update

        def spy(site, kind, **fields):
            seen.append((site, kind, dict(fields), [dict(a) for a in sstate.activity_list()]))
            return real(site, kind, **fields)
        job = tmdb_admin.begin("movie", True)
        live = sstate.activity_list()
        self.assertEqual((live[0]["site"], live[0]["kind"], live[0]["label"]), ("tmdb", "tmdb", "TMDB zenginleştirme (film)"))
        with patch.object(sstate, "activity_update", spy):
            tmdb_admin.execute(job)
        labels = [f["label"] for _, _, f, _ in seen if "label" in f]
        self.assertEqual(labels[0], "TMDB zenginleştirme (film) 0/30")
        self.assertIn("TMDB zenginleştirme (film) 25/30", labels)
        self.assertEqual(labels[-1], "TMDB zenginleştirme (film) 30/30")
        progress = [(f["done"], f["total"]) for _, _, f, _ in seen if "done" in f and f.get("total")]
        self.assertEqual(progress[-1], (30, 30))
        self.assertTrue(all(a["kind"] == "tmdb" for _, _, _, act in seen for a in act))
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual(self.events()[0]["lookup"], 30)

    def test_status_shows_running_job(self):
        sstate.activity_start("tmdb", "tmdb", "manual")
        sstate.activity_update("tmdb", "tmdb", label="TMDB zenginleştirme (film) 3/10", media_type="movie",
                               dry_run=True, done=3, total=10)
        d = self.c.get("/api/ops/tmdb/status").json()
        self.assertEqual((d["job"]["label"], d["job"]["done"], d["job"]["total"]), ("TMDB zenginleştirme (film) 3/10", 3, 10))
        act = self.c.get("/api/ops/active").json()["active"]
        self.assertEqual((act[0]["kind"], act[0]["done"]), ("tmdb", 3))

    def test_write_waits_for_a_running_ingest(self):
        """The write phase takes the ingest lock: it cannot interleave with a scan."""
        self.trio()
        job = tmdb_admin.begin("movie", False, use_preview=False)
        done = threading.Event()
        out = {}

        def work():
            with patch.object(cache, "refresh_and_prewarm"):
                out["ev"] = tmdb_admin.execute(job)
            done.set()
        with enrich.ingest_lock():
            t = threading.Thread(target=work, daemon=True)
            t.start()
            self.assertFalse(done.wait(1.0))  # blocked on the lock while "ingest" holds it
            self.assertIsNone(self.row("m-auto")["tmdb_poster_url"])
            self.assertEqual(sstate.activity_list()[0]["kind"], "tmdb")
        self.assertTrue(done.wait(10))
        t.join(5)
        self.assertEqual(out["ev"]["written"], 1)
        self.assertTrue(self.row("m-auto")["tmdb_poster_url"])

    def test_tmdb_outage_aborts_and_is_marked_partial(self):
        for i in range(3):
            self.add("e%d" % i, "Film %d" % i)

        def down(path, **p):
            raise tmdb.TmdbError("timeout")
        self.use(down)
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            self.post(dry_run=False)
        ev = self.events()[0]
        self.assertEqual((ev["status"], ev["aborted"], ev["written"]), ("partial", "tmdb_unreachable", 0))
        self.assertEqual(ev["errors"], 3)
        refresh.assert_not_called()
        for row in self.rows().values():
            self.assertIsNone(row["tmdb_checked_at"])  # errors are never stamped: retried next time

    def test_events_feed_and_active_expose_tmdb_runs(self):
        self.trio()
        self.post(dry_run=True)
        self.assertEqual(self.c.get("/api/ops/active").json()["last_tmdb"]["id"], self.events()[0]["id"])
        feed = self.c.get("/api/ops/events").json()["events"]
        ev = next(e for e in feed if e["kind"] == "tmdb")
        self.assertEqual((ev["site"], ev["dry_run"], ev["matched"], ev["review"], ev["unmatched"]), ("tmdb", True, 1, 1, 1))
        self.assertTrue(ev["at"])
        self.assertEqual([e["kind"] for e in self.c.get("/api/ops/events?site=tmdb").json()["events"]], ["tmdb"])
        self.assertFalse([e for e in self.c.get("/api/ops/events?site=yabancidizi").json()["events"] if e["kind"] == "tmdb"])
        self.assertEqual(self.c.get("/api/ops/tmdb/status").json()["last_job"]["id"], ev["id"])
        self.assertTrue(self.c.get("/api/ops/tmdb/status").json()["preview"]["fresh"])


# --- CLI shares the job's logic --------------------------------------------------------------

class CliTests(Base):
    def run_cli(self, argv):
        from tools import tmdb_enrich
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tmdb_enrich.main(["x"] + argv)
        return code, out.getvalue()

    def test_cli_dry_run_apply_and_output_shape(self):
        self.trio()
        code, out = self.run_cli(["--dry-run", "--verbose"])
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "movie: 3 to look up of 3 in library (dry-run)")
        self.assertTrue(any("[auto] Mayday (2026) -> Mayday (2026) tmdb=42" in l for l in lines))
        self.assertTrue(any(l.startswith("  [review] Belirsiz") for l in lines))
        self.assertTrue(any(l.startswith("  [unmatched] Hic Yok") for l in lines))
        self.assertIn("  3/3 matched=1 review=1 unmatched=1 errors=0", lines)
        self.assertEqual(json.loads(lines[-1])["matched"], 1)
        self.assertIsNone(self.row("m-auto")["tmdb_poster_url"])
        code, out = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(self.row("m-auto")["tmdb_id"], 42)
        self.assertEqual(self.row("m-review")["tmdb_enrich_status"], "review")

    def test_cli_respects_selected_types_and_key(self):
        self.trio()
        code, out = self.run_cli(["--type", "series"])
        self.assertEqual(code, 1)
        self.assertIn("disabled", out)
        settings.update({"tmdb": {"types": ["series"]}})
        code, out = self.run_cli([])  # movie no longer selected
        self.assertEqual(code, 1)
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "", "TMDB_TOKEN": "", "TMDB_API_KEY": ""}):
            code, out = self.run_cli([])
        self.assertEqual(code, 1)
        self.assertIn("not configured", out)
        self.assertNotIn(KEY, out)


# --- series --------------------------------------------------------------------------------

class SeriesMatchingTests(Base):
    def find(self, title, year, fake, original=None):
        self.use(fake)
        return tmdb.find(title, year, original, media_type="series")

    def test_search_and_details_use_tv_endpoints_with_first_air_date(self):
        fake = FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]})
        res = self.find("Dark", 2017, fake)
        self.assertEqual(res["status"], "matched")
        self.assertEqual(res["candidate"]["year"], 2017)  # first_air_date, not release_date
        self.assertTrue(all(c[0].startswith(("/search/tv", "/tv/")) for c in fake.calls))
        self.assertTrue(any(c[0] == "/tv/70" and "external_ids" in (c[2] or "") and "images" in (c[2] or "") for c in fake.calls))
        d = res["data"]
        self.assertEqual((d["tmdb_id"], d["runtime"]), (70, 45))  # episode_run_time
        self.assertEqual(d["poster_url"], "https://image.tmdb.org/t/p/w500/tr70.jpg")  # tr first
        self.assertEqual(d["backdrop_url"], "https://image.tmdb.org/t/p/w1280/bk70.jpg")

    def test_series_runtime_falls_back_to_last_episode(self):
        def get(path, **params):
            if path.startswith('/search/'):
                return {'results': [tv_hit(70, 'Dark', 2017)]}
            d = detail(70, 'tv')
            d['episode_run_time'] = []
            d['last_episode_to_air'] = {'runtime': 53}
            return d
        self.use(get)
        self.assertEqual(tmdb.find('Dark', 2017, None, media_type='series')['data']['runtime'], 53)

    def test_season_and_dizi_suffixes_are_stripped_for_matching_only(self):
        for title in ("Dark 3. Sezon", "Dark Sezon 3", "Dark Season 3", "Dark (Dizi)", "Dark - 2. Sezon (Dizi)"):
            fake = FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]})
            res = self.find(title, 2017, fake)
            self.assertEqual(res["status"], "matched", title)
            self.assertEqual({c[1] for c in fake.searches()}, {"Dark"}, title)
        self.assertEqual(tmdb.series_query_title("24 Sezon 2"), "24")
        self.assertEqual(tmdb.series_query_title("Sezon 2"), "Sezon 2")  # never empties the title
        self.assertEqual(tmdb.series_query_title("Breaking Bad"), "Breaking Bad")
        # movies keep their title untouched
        self.use(FakeTmdb(movies={"dark 3. sezon": [movie_hit(1, "Dark 3. Sezon", 2017)]}))
        self.assertEqual(tmdb.find("Dark 3. Sezon", 2017, None, media_type="movie")["status"], "matched")

    def test_source_title_is_never_rewritten_in_the_library(self):
        self.add("s", "Dark 3. Sezon", 2017, type="series")
        settings.update({"tmdb": {"types": ["series"]}})
        self.use(FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]}))
        with patch.object(cache, "refresh_and_prewarm"):
            tmdb_admin.run_now("series", False, use_preview=False)
        self.assertEqual(self.row("s")["title"], "Dark 3. Sezon")
        self.assertEqual(self.row("s")["tmdb_id"], 70)

    def test_score_rules_apply_to_series(self):
        # year far apart (source year of a later season) -> review, not auto
        fake = FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]})
        self.assertEqual(self.find("Dark", 2021, fake)["status"], "review")
        # no source year: exact title with a clear lead is fine; a merely similar title is not
        self.assertEqual(self.find("Dark", None, FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]}))["status"], "matched")
        self.assertEqual(self.find("Darkk Web", None, FakeTmdb(tv={"darkk web": [tv_hit(70, "Dark Web", 2017)]}))["status"], "review")
        # weak title: the year cannot rescue it
        weak = self.find("Karanlik", 2017, FakeTmdb(tv={"karanlik": [tv_hit(70, "Karanlik Dunya Savaslari", 2017)]}))
        self.assertNotEqual(weak["status"], "matched")
        # ambiguous: two equally good candidates -> lead rule -> review
        amb = self.find("Dark", 2017, FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017), tv_hit(71, "Dark", 2017)]}))
        self.assertEqual(amb["status"], "review")
        self.assertIn("ambiguous", amb["reason"])
        # roman numeral / article normalisation
        self.assertEqual(self.find("Kingdom II", 2020, FakeTmdb(tv={"kingdom ii": [tv_hit(72, "Kingdom 2", 2020)]}))["status"], "matched")
        self.assertEqual(self.find("The Wire", 2002, FakeTmdb(tv={"the wire": [tv_hit(73, "Wire", 2002)]}))["status"], "matched")
        # nothing plausible
        self.assertEqual(self.find("Hic Yok", 2020, FakeTmdb())["status"], "unmatched")

    def test_alternative_and_translated_titles_rescue_a_tv_match(self):
        fake = FakeTmdb(tv={"la casa de papel": [tv_hit(80, "Kâğıt Ev Dizisi", 2017, original="Papel")]},
                        alt={80: {"alternative_titles": {"results": [{"title": "La Casa de Papel"}]},
                                  "translations": {"translations": [{"data": {"name": "Money Heist"}}]}}})
        res = self.find("La Casa de Papel", 2017, fake)
        self.assertEqual(res["status"], "matched")
        self.assertTrue(res["candidate"].get("via"))
        self.assertTrue(any("alternative_titles" in (c[2] or "") and c[0].startswith("/tv/") for c in fake.calls))

    def test_known_imdb_id_resolves_through_tv_results(self):
        fake = FakeTmdb()

        def get(path, **params):
            if path.startswith("/find/"):
                return {"tv_results": [{"id": 90}], "movie_results": [{"id": 1}]}
            return fake(path, **params)
        self.use(get)
        res = tmdb.find("x", None, None, media_type="series", imdb_id="tt0000090")
        self.assertEqual((res["status"], res["data"]["tmdb_id"]), ("matched", 90))


class SeriesBackfillTests(Base):
    def series_with_episodes(self, cid="series-dark-2017", title="Dark 3. Sezon"):
        self.add(cid, title, 2017, type="series", poster_url="https://yabancidizi.news/p.jpg")
        eps = []
        with closing(db.connect()) as conn, conn:
            for s in (1, 2):
                for e in (1, 2, 3):
                    eid = "%s:s%d:e%d" % (cid, s, e)
                    eps.append({"kind": "episode", "season": s, "episode": e, "locator": "https://x/%s" % eid})
                    conn.execute(
                        "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,"
                        "resolver,status,failures,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("vs_" + eid, cid, "yabancidizi", cid + "-k", eid, s, e, "episode", "https://x/" + eid, "page", "unknown", 0, NOW))
            norm = json.loads(conn.execute("SELECT normalized FROM source_items WHERE canonical_id=?", (cid,)).fetchone()[0])
            norm["video_sources"] = eps
            conn.execute("UPDATE source_items SET normalized=? WHERE canonical_id=?", (json.dumps(norm), cid))
        return cid

    def snapshot_video_state(self, cid):
        vs = [tuple(r) for r in db.query(
            "SELECT id,episode_id,season,episode,kind,status,locator FROM video_sources WHERE canonical_id=? ORDER BY id", (cid,))]
        norm = json.loads(db.query_one("SELECT normalized FROM source_items WHERE canonical_id=?", (cid,))["normalized"])
        return vs, norm["video_sources"], norm["title"]

    def test_series_preview_then_apply_touches_only_the_main_record(self):
        cid = self.series_with_episodes()
        settings.update({"tmdb": {"types": ["movie", "series"]}})
        fake = self.use(FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]}))
        before = self.snapshot_video_state(cid)
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "dry_run": True})
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(self.row(cid)["tmdb_poster_url"])
            pv = self.c.get("/api/ops/tmdb/preview").json()
            self.assertEqual((pv["summary"]["kind"], pv["items"][0]["decision"], pv["items"][0]["candidate"]["year"]), ("series", "auto", 2017))
            self.assertEqual(pv["items"][0]["title"], "Dark 3. Sezon")  # source title shown as-is
            n = len(fake.calls)
            r = self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "dry_run": False})
            self.assertTrue(r.json()["from_preview"])
            self.assertEqual(len(fake.calls), n)
            refresh.assert_called_once_with()
        row = self.row(cid)
        self.assertEqual((row["type"], row["tmdb_id"], row["tmdb_enrich_status"]), ("series", 70, "matched"))
        self.assertEqual(row["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/tr70.jpg")
        self.assertEqual(row["tmdb_backdrop_url"], "https://image.tmdb.org/t/p/w1280/bk70.jpg")
        self.assertEqual(row["title"], "Dark 3. Sezon")
        self.assertEqual(row["poster_url"], "https://yabancidizi.news/p.jpg")  # source image kept as fallback
        # seasons / episodes untouched
        self.assertEqual(self.snapshot_video_state(cid), before)
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM video_sources WHERE canonical_id=?", (cid,))["n"], 6)
        self.assertEqual(db.query_one("SELECT media_type m FROM external_ids WHERE provider='tmdb'")["m"], "series")
        # catalogue: TMDB artwork first, episodes still there
        item = cache.Snapshot(LibrarySource()).by_id[cid]
        self.assertEqual(item["poster_url"], "https://image.tmdb.org/t/p/w500/tr70.jpg")
        self.assertEqual(item["backdrop_url"], "https://image.tmdb.org/t/p/w1280/bk70.jpg")
        self.assertEqual([len(s["episodes"]) for s in item["seasons"]], [3, 3])
        # admin library tab prefers TMDB artwork for series too
        lib = self.c.get("/api/ops/library?kind=series").json()["items"][0]
        self.assertEqual((lib["poster_origin"], lib["backdrop_origin"], lib["tmdb"]), ("tmdb", "tmdb", "matched"))

    def test_series_off_is_skipped_with_clear_error_and_untouched(self):
        cid = self.series_with_episodes()
        fake = self.use(FakeTmdb(tv={"dark": [tv_hit(70, "Dark", 2017)]}))
        r = self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "dry_run": True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(fake.calls, [])
        self.assertIsNone(self.row(cid)["tmdb_enrich_status"])
        self.assertNotIn("series", self.c.get("/api/ops/settings").json()["tmdb"]["types"])
        self.assertEqual(self.c.get("/api/ops/tmdb/status").json()["coverage"]["series"]["total"], 1)

    def test_series_review_and_unmatched_do_not_touch_episodes(self):
        a = self.series_with_episodes("series-a-2010", "Belirsiz Dizi")
        b = self.series_with_episodes("series-b-2011", "Bilinmeyen")
        settings.update({"tmdb": {"types": ["series"]}})
        self.use(FakeTmdb(tv={"belirsiz dizi": [tv_hit(60, "Belirsiz Dizi", 2010), tv_hit(61, "Belirsiz Dizi", 2010)]}))
        before = (self.snapshot_video_state(a), self.snapshot_video_state(b))
        with patch.object(cache, "refresh_and_prewarm"):
            tmdb_admin.run_now("series", False, use_preview=False)
        self.assertEqual((self.row(a)["tmdb_enrich_status"], self.row(b)["tmdb_enrich_status"]), ("review", "unmatched"))
        self.assertIsNone(self.row(a)["tmdb_id"])
        self.assertEqual((self.snapshot_video_state(a), self.snapshot_video_state(b)), before)
        ev = self.events()[0]
        self.assertEqual((ev["media_type"], ev["review"], ev["unmatched"], ev["written"]), ("series", 1, 1, 0))


if __name__ == "__main__":
    unittest.main()
