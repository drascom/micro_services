"""TMDB season posters + episode metadata/stills: parsing, storage, API merge, /img, backfill job, ingest hook.

Network-free: TMDB is a fake (`tmdb._get`), `httpx.get` fails the test if anything reaches it, temp DB /
settings / preview / state / image cache. The TMDB key is fake and never printed.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import io
import json
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import cache, config, db, images, rows, settings
from app.library import identity, ingest as ingest_module, seasons, tmdb, tmdb_admin
from app.routers import detail as detail_router, images as images_router
from app.scraper import state as sstate
from app.scraper.runner import RunResult
from app.sources.library import LibrarySource

from test_ops_tmdb import Base, FakeTmdb, NOW, jdump, jload, tv_hit

CID = "tmdb_tv_70"


def png():
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def ep_body(tid, season, n, lang="tr-TR", title=True, overview=True):
    return {"episode_number": n, "season_number": season,
            "name": ("Bölüm %d" % n if lang == "tr-TR" else "Ep %d-%d en" % (season, n)) if not title else "S%dE%d %s" % (season, n, lang[:2]),
            "overview": ("Özet %d-%d" % (season, n)) if overview else "",
            "air_date": "2017-06-%02d" % n, "runtime": 40 + n, "still_path": "/st%d_%d_%d.jpg" % (tid, season, n)}


def season_body(tid, season, eps=3, lang="tr-TR", title=True, overview=True, poster=True, name="Sezon %d"):
    return {"id": 9000 + season, "name": name % season, "overview": "Sezon %d özeti" % season if overview else "",
            "air_date": "2017-06-01", "poster_path": ("/sp%d_%d.jpg" % (tid, season)) if poster else None,
            "episodes": [ep_body(tid, season, n, lang, title, overview) for n in range(1, eps + 1)]}


class FakeSeasons(FakeTmdb):
    """`/tv/{id}/season/{n}` answers from ``table[(id, n)] = {'tr': body, 'en': body}``; unknown -> HTTP 404."""

    def __init__(self, table=None, **kw):
        super().__init__(**kw)
        self.table = table or {}
        self.season_calls = []

    def __call__(self, path, **params):
        if "/season/" in path:
            tid, n = (int(x) for x in path.replace("/tv/", "").split("/season/"))
            lang = params.get("language")
            with self.lock:
                self.calls.append((path, lang, None))
                self.season_calls.append((tid, n, lang))
            entry = self.table.get((tid, n))
            if entry is None:
                raise tmdb.TmdbError("HTTP 404", 404)
            body = entry.get("tr" if lang == "tr-TR" else "en")
            if isinstance(body, Exception):
                raise body
            return body if body is not None else entry["tr"]
        return super().__call__(path, **params)

    def paths(self):
        return sorted({(t, n) for t, n, _ in self.season_calls})


def full_table(tid=70, seasons_=(1, 2, 3, 4, 5), eps=3):
    """TMDB knows every season with full tr-TR data (no en-US fallback needed)."""
    return {(tid, s): {"tr": season_body(tid, s, eps)} for s in seasons_}


class SBase(Base):
    def setUp(self):
        super().setUp()
        p = patch.object(tmdb_admin, "SEASONS_PREVIEW_PATH", _os.path.join(self.tmp.name, "data", "tmdb_seasons_preview.json"))
        p.start()
        self.addCleanup(p.stop)
        settings.update({"tmdb": {"types": ["movie", "series"]}})

    def series(self, cid=CID, title="Dark", year=2017, tmdb_id=70, eps=None, matched=True, **cols):
        """Library series (+ optional provider rows: ``eps = {season: [episode, ...]}``)."""
        self.add(cid, title, year, type="series", tmdb_id=tmdb_id if matched else None,
                 status="matched" if matched else None, checked_at=NOW, **cols)
        if matched and tmdb_id:
            with closing(db.connect()) as conn, conn:
                conn.execute("INSERT OR IGNORE INTO external_ids VALUES ('tmdb','series',?,?)", (str(tmdb_id), cid))
        if eps:
            self.episodes(cid, eps)
        return cid

    def episodes(self, cid, eps, **fields):
        """Provider rows for ``{season: [episode, ...]}``; ``fields`` maps ``(season, episode)`` -> column overrides."""
        with closing(db.connect()) as conn, conn:
            for s, nums in eps.items():
                for e in nums:
                    eid = "%s:s%d:e%d" % (cid, s, e)
                    extra = fields.get((s, e), {})
                    conn.execute(
                        "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,"
                        "resolver,status,failures,updated_at,episode_title,episode_overview,episode_still_url,episode_runtime) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("vs_" + eid, cid, "yabancidizi", cid + "-k", eid, s, e, "episode", "https://x/" + eid, "page",
                         "unknown", 0, NOW, extra.get("title", "%d. Bölüm" % e), extra.get("overview"), extra.get("still"),
                         extra.get("runtime")))

    def seasons_rows(self):
        return {r["season"]: r for r in db.query("SELECT * FROM library_seasons ORDER BY season")}

    def episode_rows(self):
        return {(r["season"], r["episode"]): r for r in db.query("SELECT * FROM library_episodes ORDER BY season,episode")}

    def snap(self):
        return cache.Snapshot(LibrarySource())

    def provider_state(self):
        return [tuple(r) for r in db.query(
            "SELECT id,episode_id,season,episode,episode_title,episode_overview,episode_still_url,episode_runtime,status "
            "FROM video_sources ORDER BY id")]


# --- parsing / TMDB layer ---------------------------------------------------------------------

class ParsingTests(SBase):
    def test_full_season_parsed_with_urls_and_one_request(self):
        fake = self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 3)}}))
        res = tmdb.season(70, 1)
        self.assertEqual(res["status"], "ok")
        d = res["data"]
        self.assertEqual((d["season"], d["name"], d["air_date"]), (1, None, "2017-06-01"))  # "Sezon 1" is generic -> blank
        self.assertEqual(d["overview"], "Sezon 1 özeti")
        self.assertEqual(d["poster_url"], "https://image.tmdb.org/t/p/w500/sp70_1.jpg")
        self.assertEqual([e["episode"] for e in d["episodes"]], [1, 2, 3])
        e2 = d["episodes"][1]
        self.assertEqual((e2["title"], e2["overview"], e2["air_date"], e2["runtime"]), ("S1E2 tr", "Özet 1-2", "2017-06-02", 42))
        self.assertEqual(e2["still_url"], "https://image.tmdb.org/t/p/original/st70_1_2.jpg")
        self.assertEqual(fake.season_calls, [(70, 1, "tr-TR")])  # nothing blank: no en-US request

    def test_blank_and_generic_fields_fall_back_to_en_us(self):
        tr = season_body(70, 1, 3, title=False, overview=False, name="Sezon %d")
        en = season_body(70, 1, 3, lang="en-US", title=True, overview=True, name="Season %d")
        en["episodes"][2]["name"] = "Episode 3"       # generic in en too -> stays blank
        en["episodes"][1]["overview"] = ""            # blank in both -> None
        en["overview"] = "Season overview"
        en["name"] = "The Beginning"
        fake = self.use(FakeSeasons({(70, 1): {"tr": tr, "en": en}}))
        d = tmdb.season(70, 1)["data"]
        self.assertEqual([c[2] for c in fake.season_calls], ["tr-TR", "en-US"])
        self.assertEqual((d["name"], d["overview"]), ("The Beginning", "Season overview"))
        titles = [e["title"] for e in d["episodes"]]
        self.assertEqual(titles, ["S1E1 en", "S1E2 en", None])
        self.assertEqual([e["overview"] for e in d["episodes"]], ["Özet 1-1", None, "Özet 1-3"])
        # ... the tr-TR value always wins when it is real
        tr2 = season_body(70, 2, 2)
        tr2["episodes"][0]["overview"] = ""
        en2 = season_body(70, 2, 2, lang="en-US")
        self.use(FakeSeasons({(70, 2): {"tr": tr2, "en": en2}}))
        e = tmdb.season(70, 2)["data"]["episodes"]
        self.assertEqual((e[0]["title"], e[0]["overview"]), ("S2E1 tr", "Özet 2-1"))

    def test_404_is_empty_and_errors_are_errors_never_raised(self):
        self.use(FakeSeasons({}))
        self.assertEqual(tmdb.season(70, 9)["status"], "empty")
        self.use(FakeSeasons({(70, 1): {"tr": tmdb.TmdbError("HTTP 500", 500)}}))
        self.assertEqual(tmdb.season(70, 1)["status"], "error")
        self.use(FakeSeasons({(70, 1): {"tr": tmdb.TmdbError("timeout")}}))
        self.assertEqual(tmdb.season(70, 1)["status"], "error")
        # a transport failure of the en-US fallback must not store half a season
        tr = season_body(70, 1, 2, overview=False)
        self.use(FakeSeasons({(70, 1): {"tr": tr, "en": tmdb.TmdbError("timeout")}}))
        self.assertEqual(tmdb.season(70, 1)["status"], "error")
        # an HTTP error on the fallback keeps the tr-TR data
        self.use(FakeSeasons({(70, 1): {"tr": tr, "en": tmdb.TmdbError("HTTP 500", 500)}}))
        self.assertEqual(tmdb.season(70, 1)["status"], "ok")
        # a season with no data at all is "empty"
        self.use(FakeSeasons({(70, 1): {"tr": {"name": "", "overview": "", "episodes": []}, "en": {}}}))
        self.assertEqual(tmdb.season(70, 1)["status"], "empty")
        with patch.dict(_os.environ, {"TMDB_ACCESS_KEY": ""}):
            self.assertEqual(tmdb.season(70, 1), {"status": "disabled"})

    def test_429_is_retried_by_the_shared_layer_and_gives_up_as_error(self):
        class Resp:
            def __init__(self, code, body=None, headers=None):
                self.status_code, self._b, self.headers = code, body, headers or {}

            def json(self):
                return self._b

        seq = [Resp(429, headers={"Retry-After": "1"}), Resp(200, season_body(70, 1, 2))]
        calls = []

        def get(url, **kw):
            calls.append(url)
            return seq.pop(0)
        with patch.object(tmdb.httpx, "get", get), patch.object(tmdb.time, "sleep") as sleep:
            res = tmdb.season(70, 1)
        self.assertEqual((res["status"], len(calls)), ("ok", 2))
        sleep.assert_called_once()
        self.assertNotIn(KEY_TEXT, " ".join(calls))  # the key is never part of the URL
        # 429 forever: 3 attempts, then a plain error (not stamped by callers)
        calls.clear()
        with patch.object(tmdb.httpx, "get", lambda url, **kw: calls.append(url) or Resp(429)), patch.object(tmdb.time, "sleep"):
            res = tmdb.season(70, 1)
        self.assertEqual((res["status"], len(calls)), ("error", 3))
        self.assertNotIn(KEY_TEXT, json.dumps(res))


KEY_TEXT = "k" * 32


# --- which seasons are requested ----------------------------------------------------------------

class SelectionTests(SBase):
    def test_only_seasons_the_library_has_are_requested(self):
        cid = self.series(eps={1: [1, 2], 3: [1]})
        fake = self.use(FakeSeasons(full_table()))  # TMDB knows five seasons
        c = seasons.enrich_series([cid])
        self.assertEqual(fake.paths(), [(70, 1), (70, 3)])
        self.assertEqual((c["series"], c["seasons"], c["episodes"], c["errors"]), (1, 2, 6, 0))
        self.assertEqual(sorted(self.seasons_rows()), [1, 3])

    def test_episodes_only_listed_in_the_normalized_source_count_too(self):
        cid = self.series()
        with closing(db.connect()) as conn, conn:
            norm = json.loads(conn.execute("SELECT normalized FROM source_items WHERE canonical_id=?", (cid,)).fetchone()[0])
            norm["video_sources"] = [{"kind": "episode", "season": 2, "episode": 1}, {"kind": "episode", "season": 2, "episode": 2},
                                     {"kind": "trailer", "season": 7, "episode": 1}, {"kind": "episode", "season": "x", "episode": 1}]
            conn.execute("UPDATE source_items SET normalized=? WHERE canonical_id=?", (json.dumps(norm), cid))
        fake = self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        self.assertEqual(fake.paths(), [(70, 2)])

    def test_series_without_tmdb_id_or_seasons_are_left_alone(self):
        a = self.series("s-none", "A", tmdb_id=None, matched=False, eps={1: [1]})
        b = self.series("s-noeps", "B", tmdb_id=5)
        m = self.add("m1", "Film", type="movie", tmdb_id=9)
        fake = self.use(FakeSeasons(full_table(5)))
        c = seasons.enrich_series([a, b, m])
        self.assertEqual(fake.calls, [])
        self.assertEqual((c["series"], c["no_tmdb"]), (0, 1))
        self.assertEqual(self.seasons_rows(), {})

    def test_stored_seasons_are_not_asked_again_until_episodes_are_missing_and_stale(self):
        cid = self.series(eps={1: [1, 2, 3]})
        fake = self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        n = len(fake.calls)
        seasons.enrich_series([cid])
        self.assertEqual(len(fake.calls), n)  # complete: never re-queried
        self.episodes(cid, {1: [4, 5]})  # the source got two episodes TMDB had not listed at the time
        seasons.enrich_series([cid])
        self.assertEqual(len(fake.calls), n)  # inside the retry window
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_seasons SET checked_at=checked_at-?", (int(config.TMDB_RETRY_DAYS * 86400) + 10,))
        seasons.enrich_series([cid])
        self.assertEqual(len(fake.calls), n + 1)

    def test_force_asks_again(self):
        cid = self.series(eps={1: [1]})
        fake = self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        seasons.enrich_series([cid], force=True)
        self.assertEqual(len(fake.season_calls), 2)


# --- storage / never overwriting the source -----------------------------------------------------------

class StorageTests(SBase):
    def stored(self, cid=CID, eps=None):
        cid = self.series(cid, eps=eps or {1: [1, 2, 3]})
        self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        return cid

    def test_rows_written_with_tmdb_values(self):
        self.stored()
        s = self.seasons_rows()[1]
        self.assertEqual((s["status"], s["tmdb_poster_url"], s["overview"], s["air_date"]),
                         ("ok", "https://image.tmdb.org/t/p/w500/sp70_1.jpg", "Sezon 1 özeti", "2017-06-01"))
        self.assertIsNone(s["name"])  # generic "Sezon 1" is not stored as a name
        self.assertTrue(s["checked_at"])
        e = self.episode_rows()[(1, 2)]
        self.assertEqual((e["title"], e["overview"], e["air_date"], e["runtime_minutes"], e["tmdb_still_url"]),
                         ("S1E2 tr", "Özet 1-2", "2017-06-02", 42, "https://image.tmdb.org/t/p/original/st70_1_2.jpg"))

    def test_video_sources_and_source_items_are_never_touched(self):
        cid = self.series(eps={1: [1, 2, 3]})
        before = (self.provider_state(), db.query_one("SELECT normalized FROM source_items")["normalized"],
                  dict(self.row(cid)))
        self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        after = (self.provider_state(), db.query_one("SELECT normalized FROM source_items")["normalized"], dict(self.row(cid)))
        self.assertEqual(before, after)

    def test_a_blank_never_wipes_a_stored_value_and_empty_keeps_old_data(self):
        cid = self.stored()
        blank = season_body(70, 1, 3, overview=False, poster=False)
        for e in blank["episodes"]:
            e["still_path"] = None
        self.use(FakeSeasons({(70, 1): {"tr": blank, "en": blank}}))
        seasons.enrich_series([cid], force=True)
        s = self.seasons_rows()[1]
        self.assertEqual((s["overview"], s["tmdb_poster_url"]), ("Sezon 1 özeti", "https://image.tmdb.org/t/p/w500/sp70_1.jpg"))
        self.assertEqual(self.episode_rows()[(1, 1)]["tmdb_still_url"], "https://image.tmdb.org/t/p/original/st70_1_1.jpg")
        self.use(FakeSeasons({}))  # TMDB now answers 404 for the season
        seasons.enrich_series([cid], force=True)
        s = self.seasons_rows()[1]
        self.assertEqual((s["status"], s["tmdb_poster_url"]), ("empty", "https://image.tmdb.org/t/p/w500/sp70_1.jpg"))

    def test_result_for_a_vanished_series_or_another_tmdb_id_is_not_written(self):
        cid = self.series(eps={1: [1]})
        self.use(FakeSeasons(full_table()))
        data = tmdb.season(70, 1)
        with closing(db.connect()) as conn, conn:
            self.assertFalse(seasons.persist(conn, "gone", 1, data))
            self.assertFalse(seasons.persist(conn, cid, 1, data, expect_tmdb_id=999))
            self.assertTrue(seasons.persist(conn, cid, 1, data, expect_tmdb_id=70))
        self.assertEqual(list(self.seasons_rows()), [1])

    def test_identity_merge_moves_the_season_data(self):
        old = self.series("series-dark-2017", eps={1: [1]}, tmdb_id=None, matched=False)
        self.use(FakeSeasons(full_table()))
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET tmdb_id=70 WHERE id=?", (old,))
        seasons.enrich_series([old])
        self.assertEqual(list(self.seasons_rows()), [1])
        target = self.series("tmdb_tv_70", eps={2: [1]})
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET tmdb_id=NULL WHERE id=?", (old,))
            identity.merge(conn, old, target)
        rows_ = db.query("SELECT canonical_id, season FROM library_seasons")
        self.assertEqual({(r["canonical_id"], r["season"]) for r in rows_}, {(target, 1)})
        self.assertEqual({r["canonical_id"] for r in db.query("SELECT canonical_id FROM library_episodes")}, {target})
        self.assertIn(("series-dark-2017:s1", target + ":s1"),
                      [(r["alias"], r["canonical_id"]) for r in db.query("SELECT * FROM catalogue_aliases")])

    def test_old_database_gets_the_tables_on_init(self):
        with closing(db.connect()) as conn, conn:
            conn.execute("DROP TABLE library_seasons")
            conn.execute("DROP TABLE library_episodes")
        db.init()
        self.assertEqual(db.query("SELECT * FROM library_seasons"), [])
        self.assertEqual(seasons.load_tmdb(), ({}, {}))
        with closing(db.connect()) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(library_episodes)")}
        self.assertTrue({"canonical_id", "season", "episode", "title", "overview", "air_date", "runtime_minutes",
                         "tmdb_still_url", "checked_at"} <= cols)


# --- merge: source wins, TMDB fills, artwork TMDB first ------------------------------------------------

class MergeTests(SBase):
    def build(self):
        cid = self.series(eps={1: [1, 2, 3]})
        self.episodes(cid, {2: [1]})
        with closing(db.connect()) as conn, conn:  # source data on episode 1 and 2 of season 1
            conn.execute("UPDATE video_sources SET episode_title='Yankı', episode_overview='Kaynak özeti', episode_runtime=55, "
                         "episode_still_url='https://yabancidizi.news/still1.jpg' WHERE episode_id=?", (cid + ":s1:e1",))
            conn.execute("UPDATE video_sources SET episode_still_url='https://yabancidizi.news/still2.jpg' WHERE episode_id=?", (cid + ":s1:e2",))
        self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 3)}}))  # TMDB has no season 2 -> nothing stored for it
        seasons.enrich_series([cid])
        return cid

    def eps(self, cid):
        item = self.snap().by_id[cid]
        return item, {(s["season"], e["episode"]): e for s in item["seasons"] for e in s["episodes"]}

    def test_source_values_win_and_tmdb_fills_blanks(self):
        item, e = self.eps(self.build())
        self.assertEqual((e[(1, 1)]["title"], e[(1, 1)]["overview"], e[(1, 1)]["runtime"]), ("Yankı", "Kaynak özeti", 55))
        # placeholder title / empty overview / no runtime -> TMDB
        self.assertEqual((e[(1, 2)]["title"], e[(1, 2)]["overview"], e[(1, 2)]["runtime"]), ("S1E2 tr", "Özet 1-2", 42))
        self.assertEqual(e[(1, 3)]["air_date"], "2017-06-03")
        # season 2 exists in the library but TMDB was never asked (no data): source defaults stay
        self.assertEqual((e[(2, 1)]["title"], e[(2, 1)]["overview"], e[(2, 1)]["runtime"], e[(2, 1)]["air_date"]),
                         ("1. Bölüm", "", 0, None))

    def test_artwork_prefers_tmdb_and_falls_back_to_the_source(self):
        cid = self.build()
        item, e = self.eps(cid)
        self.assertEqual(e[(1, 1)]["still_remote"], "https://image.tmdb.org/t/p/original/st70_1_1.jpg")  # TMDB beats source
        self.assertEqual(e[(1, 1)]["still"], "https://yabancidizi.news/still1.jpg")                        # source value kept
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_episodes SET tmdb_still_url=NULL WHERE season=1 AND episode=2")
        _, e = self.eps(cid)
        self.assertEqual(e[(1, 2)]["still_remote"], "https://yabancidizi.news/still2.jpg")                # source fallback
        self.assertIsNone(e[(2, 1)]["still_remote"])                                                      # neither
        s1 = item["seasons"][0]
        self.assertEqual((s1["poster_url"], s1["name"], s1["overview"]), ("https://image.tmdb.org/t/p/w500/sp70_1.jpg", "1. Sezon", "Sezon 1 özeti"))
        s2 = item["seasons"][1]
        self.assertEqual((s2["poster_url"], s2["name"], s2["overview"], s2["air_date"]), (None, "2. Sezon", "", None))

    def test_a_real_tmdb_season_name_is_used(self):
        cid = self.series(eps={1: [1]})
        self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 1, name="Volume %d")}}))
        seasons.enrich_series([cid])
        self.assertEqual(self.snap().by_id[cid]["seasons"][0]["name"], "Volume 1")

    def test_no_tables_no_crash(self):
        cid = self.series(eps={1: [1]})
        with closing(db.connect()) as conn, conn:
            conn.execute("DROP TABLE library_seasons")
            conn.execute("DROP TABLE library_episodes")
        item = self.snap().by_id[cid]
        self.assertEqual(item["seasons"][0]["episodes"][0]["title"], "1. Bölüm")


# --- public API + /img ----------------------------------------------------------------------------------

class ApiTests(SBase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(images_router.router)
        self.img = TestClient(app)

    def detail(self, cid):
        snap = self.snap()
        with patch.object(rows, "get_cache", return_value=snap):
            return rows.detail(cid, ""), snap

    def prepare(self):
        cid = self.series(eps={1: [1, 2], 2: [1]})
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE video_sources SET episode_title='Yankı', episode_still_url='https://yabancidizi.news/s.jpg' WHERE episode_id=?", (cid + ":s1:e1",))
        self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 2)}, (70, 2): {"tr": season_body(70, 2, 1, poster=False)}}))
        seasons.enrich_series([cid])
        return cid

    def test_detail_has_seasons_and_episode_fields_merged(self):
        cid = self.prepare()
        d, _ = self.detail(cid)
        s1, s2 = d["seasons"]
        self.assertEqual((s1["season"], s1["title"], s1["name"], s1["episode_count"], s1["has_poster"]), (1, "1. Sezon", "1. Sezon", 2, True))
        self.assertEqual((s1["overview"], s1["air_date"]), ("Sezon 1 özeti", "2017-06-01"))
        self.assertEqual(s1["poster_url"], "/img/%s:s1/portrait?w=300&h=450" % cid)
        # season without a TMDB poster: the series portrait, flagged
        self.assertEqual((s2["has_poster"], s2["poster_url"], s2["episode_count"]), (False, "/img/%s/portrait?w=300&h=450" % cid, 1))
        e1, e2 = s1["episodes"]
        self.assertEqual((e1["title"], e2["title"]), ("Yankı", "S1E2 tr"))
        self.assertEqual((e2["overview"], e2["air_date"], e2["runtime"]), ("Özet 1-2", "2017-06-02", 42))
        self.assertEqual((e1["still_url"], e1["has_still"]), ("/img/%s:s1:e1/still?w=320&h=180" % cid, True))
        with closing(db.connect()) as conn, conn:  # a still on a host outside the /img allow-list is not a usable still
            conn.execute("UPDATE library_episodes SET tmdb_still_url='https://evil.example/x.jpg' WHERE season=1 AND episode=2")
        d2, _ = self.detail(cid)
        self.assertFalse(d2["seasons"][0]["episodes"][1]["has_still"])
        self.assertTrue(s2["episodes"][0]["has_still"])

    def test_legacy_fields_are_unchanged(self):
        cid = self.series(eps={1: [1, 2]})
        legacy, _ = self.detail(cid)
        self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 2)}}))
        seasons.enrich_series([cid])
        new, _ = self.detail(cid)
        for key in legacy:  # every key the client knew is still there with the same meaning
            if key == "seasons":
                continue
            self.assertEqual(new[key], legacy[key], key)
        s_old, s_new = legacy["seasons"][0], new["seasons"][0]
        for key in ("season", "title"):
            self.assertEqual(s_new[key], s_old[key])
        self.assertEqual(set(s_old) - {"episodes"}, {"season", "title", "name", "overview", "air_date", "poster_url", "has_poster", "episode_count"})
        for old, cur in zip(s_old["episodes"], s_new["episodes"]):
            for key in ("id", "season", "episode", "runtime", "still", "availability", "progress", "overview"):
                if key not in ("runtime", "overview"):  # (filled from TMDB when the source had none)
                    self.assertEqual(cur[key], old[key], key)
            self.assertTrue({"id", "season", "episode", "title", "overview", "runtime", "still", "progress", "availability"} <= set(cur))
            self.assertEqual(cur["still"], "/img/%s/still?w=320&h=180" % cur["id"])

    def test_mock_source_detail_is_unchanged(self):
        snap = cache.Snapshot(__import__("app.sources.mock", fromlist=["MockSource"]).MockSource())
        item = next(i for i in snap.items if i.get("seasons"))
        with patch.object(rows, "get_cache", return_value=snap):
            d = rows.detail(item["id"], "")
        self.assertEqual(d["seasons"][0]["title"], item["seasons"][0]["title"])
        self.assertEqual(d["seasons"][0]["episode_count"], len(item["seasons"][0]["episodes"]))
        self.assertFalse(d["seasons"][0]["has_poster"])
        self.assertFalse(d["seasons"][0]["episodes"][0]["has_still"])

    def fetcher(self, log, ok=True):
        def fake(url):
            log.append(url)
            return png() if ok else None
        return fake

    def test_img_still_is_proxied_tmdb_first_then_source_then_placeholder(self):
        cid = self.prepare()
        snap = self.snap()
        got = []
        images._neg_cache.clear()
        with patch.object(cache, "get", return_value=snap), patch.object(images, "_http_fetch", self.fetcher(got)):
            r = self.img.get("/img/%s:s1:e1/still?w=320&h=180" % cid)  # TMDB and source both exist -> TMDB
            self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
            self.assertEqual(got, ["https://image.tmdb.org/t/p/original/st70_1_1.jpg"])
            self.assertEqual(Image.open(io.BytesIO(r.content)).size, (320, 180))
            tag = r.headers["etag"]
            self.assertEqual(self.img.get("/img/%s:s1:e1/still?w=320&h=180" % cid, headers={"If-None-Match": tag}).status_code, 304)
            self.img.get("/img/%s:s1:e1/still?w=320&h=180" % cid)
            self.assertEqual(len(got), 1)  # cached on disk after the first request
        # TMDB has nothing for the episode -> the source still
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_episodes SET tmdb_still_url=NULL WHERE season=1 AND episode=1")
        snap = self.snap()
        got.clear()
        with patch.object(cache, "get", return_value=snap), patch.object(images, "_http_fetch", self.fetcher(got)):
            self.assertEqual(self.img.get("/img/%s:s1:e1/still" % cid).status_code, 200)
        self.assertEqual(got, ["https://yabancidizi.news/s.jpg"])
        # remote download fails -> generated placeholder (still a 200 JPEG)
        got.clear()
        images._neg_cache.clear()
        with patch.object(cache, "get", return_value=snap), patch.object(images, "_http_fetch", self.fetcher(got, ok=False)):
            r = self.img.get("/img/%s:s1:e2/still" % cid)
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))

    def test_img_season_poster_with_fallback_to_the_series_poster(self):
        cid = self.prepare()
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET tmdb_poster_url='https://image.tmdb.org/t/p/w500/series.jpg' WHERE id=?", (cid,))
        snap = self.snap()
        got = []
        images._neg_cache.clear()
        with patch.object(cache, "get", return_value=snap), patch.object(images, "_http_fetch", self.fetcher(got)):
            r1 = self.img.get("/img/%s:s1/portrait?w=300&h=450" % cid)
            r2 = self.img.get("/img/%s:s2/portrait?w=300&h=450" % cid)  # no season poster -> series poster
            r3 = self.img.get("/img/%s:s9/portrait" % cid)
            r4 = self.img.get("/img/%s:s1/card" % cid)  # only portrait is real artwork for a season
        self.assertEqual([r.status_code for r in (r1, r2, r3, r4)], [200, 200, 404, 200])
        self.assertEqual(got, ["https://image.tmdb.org/t/p/w500/sp70_1.jpg", "https://image.tmdb.org/t/p/w500/series.jpg"])
        self.assertEqual(Image.open(io.BytesIO(r1.content)).size, (300, 450))

    def test_img_only_allow_listed_hosts(self):
        cid = self.series(eps={1: [1]})
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE video_sources SET episode_still_url='https://evil.example/x.jpg'")
        got = []
        with patch.object(cache, "get", return_value=self.snap()), patch.object(images, "_http_fetch", self.fetcher(got)):
            r = self.img.get("/img/%s:s1:e1/still" % cid)
        self.assertEqual((r.status_code, got), (200, []))  # SSRF guard: placeholder, no fetch

    def test_snapshot_indexes_seasons_and_their_aliases(self):
        cid = self.prepare()
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO catalogue_aliases VALUES (?,?)", ("old-id:s1", cid + ":s1"))
        snap = self.snap()
        self.assertIn(cid + ":s1", snap.seasons)
        self.assertIs(snap.seasons["old-id:s1"], snap.seasons[cid + ":s1"])


# --- prewarm ------------------------------------------------------------------------------------------------

class PrewarmTests(SBase):
    def items(self):
        cid = self.series(eps={1: [1, 2]})
        self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 2)}}))
        seasons.enrich_series([cid])
        return self.snap().items

    def test_season_posters_are_prewarmed_stills_are_not_by_default(self):
        items = self.items()
        urls = {u for u, _ in images.prewarm_targets(items)}
        self.assertIn("https://image.tmdb.org/t/p/w500/sp70_1.jpg", urls)
        self.assertFalse([u for u in urls if "/st70_" in u])
        sizes = [sz for u, sz in images.prewarm_targets(items) if "sp70_1" in u]
        self.assertEqual(sizes, [images.DEFAULT_SIZE["portrait"]])  # the size the API's season poster_url asks for

    def test_env_switch_prewarms_stills_too(self):
        items = self.items()
        with patch.object(config, "IMG_PREWARM_STILLS", True):
            targets = images.prewarm_targets(items)
        stills = [(u, sz) for u, sz in targets if "/st70_" in u]
        self.assertEqual(len(stills), 2)
        self.assertEqual({sz for _, sz in stills}, {images.DEFAULT_SIZE["still"]})

    def test_prewarm_downloads_the_season_poster_only(self):
        items = self.items()
        got = []
        with patch.object(images, "_http_fetch", lambda url: got.append(url) or png()), \
                patch.object(config, "REMOTE_IMG_HOSTS", ["image.tmdb.org"]):
            images.prewarm(items)
        self.assertEqual(got, ["https://image.tmdb.org/t/p/w500/sp70_1.jpg"])

    def test_default_still_prewarm_flag_is_off(self):
        self.assertFalse(config.IMG_PREWARM_STILLS)


# --- retry stamps, budget, breaker ---------------------------------------------------------------------------

class RetryBudgetTests(SBase):
    def test_empty_is_stamped_and_retried_after_the_retry_window(self):
        cid = self.series(eps={1: [1], 2: [1]})
        fake = self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 1)}}))  # season 2 is unknown to TMDB
        c = seasons.enrich_series([cid])
        self.assertEqual((c["seasons"], c["empty"], c["errors"]), (1, 1, 0))
        rows_ = self.seasons_rows()
        self.assertEqual((rows_[1]["status"], rows_[2]["status"]), ("ok", "empty"))
        self.assertTrue(rows_[2]["checked_at"])
        self.assertIsNone(rows_[2]["tmdb_poster_url"])
        n = len(fake.calls)
        seasons.enrich_series([cid])
        self.assertEqual(len(fake.calls), n)  # empty season not asked again inside the window
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_seasons SET checked_at=? WHERE season=2", (NOW,))
        with patch.object(seasons.time, "time", return_value=NOW + config.TMDB_RETRY_DAYS * 86400 - 60):
            seasons.enrich_series([cid])
        self.assertEqual(len(fake.calls), n)
        with patch.object(seasons.time, "time", return_value=NOW + config.TMDB_RETRY_DAYS * 86400 + 60):
            seasons.enrich_series([cid])
        self.assertEqual(fake.paths()[-1], (70, 2))
        self.assertEqual(len(fake.calls), n + 1)

    def test_errors_are_not_stamped_and_retried_next_run(self):
        cid = self.series(eps={1: [1]})
        self.use(FakeSeasons({(70, 1): {"tr": tmdb.TmdbError("timeout")}}))
        c = seasons.enrich_series([cid])
        self.assertEqual((c["errors"], c["seasons"]), (1, 0))
        self.assertEqual(self.seasons_rows(), {})
        fake = self.use(FakeSeasons(full_table()))
        seasons.enrich_series([cid])
        self.assertEqual(fake.paths(), [(70, 1)])
        self.assertEqual(list(self.seasons_rows()), [1])

    def test_budget_exhausted_defers_everything_and_stamps_nothing(self):
        cid = self.series(eps={1: [1], 2: [1]})
        fake = self.use(FakeSeasons(full_table()))
        c = seasons.enrich_series([cid], budget=0)
        self.assertEqual((c["deferred"], c["seasons"], fake.calls), (2, 0, []))
        self.assertEqual(self.seasons_rows(), {})
        seasons.enrich_series([cid])  # next run has budget
        self.assertEqual(len(self.seasons_rows()), 2)

    def test_error_breaker_stops_the_batch(self):
        cid = self.series(eps={n: [1] for n in range(1, 9)})
        fake = self.use(FakeSeasons({(70, n): {"tr": tmdb.TmdbError("timeout")} for n in range(1, 9)}))
        jobs = {(cid, n): 70 for n in range(1, 9)}
        results, c = seasons.run_batch(jobs, budget=30, workers=1)
        self.assertEqual(len(fake.season_calls), enrich_breaker())
        self.assertEqual((c["errors"], c["deferred"]), (enrich_breaker(), 8 - enrich_breaker()))
        self.assertEqual(len(results), enrich_breaker())

    def test_auto_enrich_obeys_the_admin_switches(self):
        cid = self.series(eps={1: [1]})
        fake = self.use(FakeSeasons(full_table()))
        settings.update({"tmdb": {"auto": False}})
        self.assertFalse(seasons.auto_ok())
        seasons.auto_enrich([cid])
        self.assertEqual(fake.calls, [])
        settings.update({"tmdb": {"auto": True, "types": ["movie"]}})
        seasons.auto_enrich([cid])
        self.assertEqual(fake.calls, [])
        settings.update({"tmdb": {"types": ["series"]}})
        seasons.auto_enrich([cid])
        self.assertEqual(len(fake.season_calls), 1)
        with patch.dict(_os.environ, {"TMDB_ACCESS_KEY": ""}):
            self.assertFalse(seasons.auto_ok())


def enrich_breaker():
    from app.library import enrich
    return enrich.ERROR_BREAKER


# --- ingest hook + opening a series ------------------------------------------------------------------------------

class IngestHookTests(SBase):
    def run_ingest(self):
        item = {"title": "Dark", "year": 2017, "detail_url": "dizi/dark", "poster_url": "/uploads/series/x.jpg", "genres": ["Dram"]}
        result = RunResult("yabancidizi", items=[item], drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module, "_enrich_series_catalogs"), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source("yabancidizi")

    def setup_series(self):
        fake = self.use(FakeSeasons(full_table(), tv={"dark": [tv_hit(70, "Dark", 2017)]}))
        res = self.run_ingest()  # auto-matches the series
        self.assertEqual(res["tmdb_enrich"]["matched"], 1)
        cid = db.query_one("SELECT id FROM library_items WHERE type='series'")["id"]
        self.assertEqual(cid, CID)
        self.assertEqual(res["tmdb_seasons"]["series"], 0)  # no episodes in the library yet -> nothing to ask
        self.assertEqual(fake.season_calls, [])
        return fake, cid

    def test_ingest_fetches_seasons_the_library_has_for_matched_series(self):
        fake, cid = self.setup_series()
        self.episodes(cid, {1: [1, 2]})
        res = self.run_ingest()
        self.assertEqual((res["tmdb_seasons"]["series"], res["tmdb_seasons"]["seasons"], res["tmdb_seasons"]["episodes"]), (1, 1, 3))
        self.assertEqual(fake.paths(), [(70, 1)])
        self.assertEqual(list(self.seasons_rows()), [1])
        n = len(fake.calls)
        res = self.run_ingest()  # complete now: a rescan costs no TMDB request
        self.assertEqual(len(fake.calls), n)
        self.assertEqual(res["tmdb_seasons"]["seasons"], 0)
        self.assertEqual(res["status"], "success")

    def test_series_matched_in_this_very_run_gets_its_seasons_too(self):
        """A scraped card that already carries episodes (e.g. latest-episode cards) + an auto-match in the same scan."""
        fake = self.use(FakeSeasons(full_table(), tv={"dark": [tv_hit(70, "Dark", 2017)]}))
        item = {"title": "Dark", "year": 2017, "detail_url": "dizi/dark", "poster_url": "/uploads/series/x.jpg", "genres": ["Dram"],
                "video_sources": [{"kind": "episode", "season": 1, "episode": e, "url": "https://x/e%d" % e, "resolver": "page"}
                                  for e in (1, 2)]}
        result = RunResult("yabancidizi", items=[item], drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module, "_enrich_series_catalogs"), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            res = ingest_module.ingest_source("yabancidizi")
        self.assertEqual((res["tmdb_enrich"]["matched"], res["tmdb_seasons"]["seasons"], res["tmdb_seasons"]["episodes"]), (1, 1, 3))
        self.assertEqual(fake.paths(), [(70, 1)])
        cid = db.query_one("SELECT id FROM library_items")["id"]
        self.assertEqual(cid, CID)
        self.assertEqual(list(self.seasons_rows()), [1])
        item = self.snap().by_id[cid]
        self.assertEqual([e["title"] for e in item["seasons"][0]["episodes"]], ["S1E1 tr", "S1E2 tr"])

    def test_ingest_obeys_auto_switch_type_and_budget(self):
        fake, cid = self.setup_series()
        self.episodes(cid, {1: [1]})
        settings.update({"tmdb": {"auto": False}})
        self.run_ingest()
        settings.update({"tmdb": {"auto": True, "types": ["movie"]}})
        self.run_ingest()
        self.assertEqual(fake.season_calls, [])
        settings.update({"tmdb": {"types": ["movie", "series"]}})
        with patch.object(config, "TMDB_BUDGET_SECONDS", 0):
            res = self.run_ingest()
        self.assertEqual((fake.season_calls, res["tmdb_seasons"]["deferred"], res["status"]), ([], 1, "success"))
        self.assertEqual(self.seasons_rows(), {})
        self.run_ingest()
        self.assertEqual(list(self.seasons_rows()), [1])

    def test_a_tmdb_failure_never_fails_the_ingest(self):
        fake, cid = self.setup_series()
        self.episodes(cid, {1: [1]})
        self.use(FakeSeasons({(70, 1): {"tr": tmdb.TmdbError("timeout")}}))
        res = self.run_ingest()
        self.assertEqual((res["status"], res["tmdb_seasons"]["errors"]), ("success", 1))
        with patch.object(seasons, "auto_enrich", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_ingest()["status"], "success")

    def test_opening_a_series_enriches_it_in_the_background(self):
        cid = self.series(eps={1: [1, 2]})
        self.use(FakeSeasons(full_table()))
        detail_router._seasons_checked.clear()
        detail_router._seasons_inflight.clear()
        with patch.object(detail_router, "threading") as thr, patch.object(cache, "refresh") as refresh:
            thread = thr.Thread  # only the router's own thread is faked (the batch's pool keeps real threads)
            self.assertTrue(detail_router.schedule_seasons(cid))
            self.assertFalse(detail_router.schedule_seasons(cid))  # de-duplicated / rate limited
            thread.assert_called_once()
            target = thread.call_args.kwargs["target"]
            thread.return_value.start.assert_called_once()
            target(*thread.call_args.kwargs["args"])  # what the thread would do
            refresh.assert_called_once_with()
        self.assertEqual(list(self.seasons_rows()), [1])
        self.assertEqual(detail_router._seasons_inflight, set())
        settings.update({"tmdb": {"auto": False}})
        detail_router._seasons_checked.clear()
        self.assertFalse(detail_router.schedule_seasons(cid))

    def test_detail_route_schedules_seasons_only_for_matched_library_series(self):
        cid = self.series(eps={1: [1]})
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET overview='Özet' WHERE id=?", (cid,))
        snap = self.snap()
        with patch.object(cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(detail_router, "schedule_seasons") as sched, patch.object(detail_router, "schedule_hydrate") as hyd, \
                patch.object(detail_router, "_inventory_due", return_value=False):  # inventory already read
            out = detail_router.detail(cid, "")
        self.assertEqual(out["id"], cid)
        sched.assert_called_once_with(cid)
        hyd.assert_not_called()


# --- admin job: preview / apply / status ------------------------------------------------------------------------------

class BackfillTests(SBase):
    def post(self, **body):
        return self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "scope": "seasons", **body})

    def library(self):
        a = self.series("tmdb_tv_70", "Dark", eps={1: [1, 2], 2: [1]})
        b = self.series("tmdb_tv_71", "Ozark", 2017, tmdb_id=71, eps={1: [1]})
        self.series("series-x-2010", "Eşleşmemiş", 2010, tmdb_id=None, matched=False, eps={1: [1]})
        self.series("tmdb_tv_72", "Bölümsüz", 2015, tmdb_id=72)
        table = {**full_table(70, (1, 2)), (71, 1): {"tr": season_body(71, 1, 2)}}
        return a, b, self.use(FakeSeasons(table))

    def test_dry_run_writes_nothing_but_the_preview_and_event(self):
        a, b, fake = self.library()
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=True)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["started"] and body["dry_run"])
        self.assertEqual((body["kind"], body["scope"], body["label"]), ("series", "seasons", "TMDB sezon ve bölüm görselleri (dizi)"))
        self.assertEqual(self.seasons_rows(), {})
        self.assertEqual(self.episode_rows(), {})
        refresh.assert_not_called()
        self.assertEqual(fake.paths(), [(70, 1), (70, 2), (71, 1)])  # only TMDB-matched series with library seasons
        pv = jload(tmdb_admin.SEASONS_PREVIEW_PATH)
        self.assertEqual((pv["kind"], pv["scope"], len(pv["items"])), ("series", "seasons", 2))
        self.assertFalse(_os.path.exists(tmdb_admin.PREVIEW_PATH))  # the title-match preview is a different file
        dark = next(i for i in pv["items"] if i["id"] == "tmdb_tv_70")
        self.assertEqual((dark["decision"], len(dark["seasons"]), dark["episodes_found"], dark["posters"], dark["stills"]), ("auto", 2, 6, 2, 6))
        self.assertEqual(len(dark["samples"]), 3)
        self.assertTrue(dark["samples"][0]["still_url"].startswith("https://image.tmdb.org/t/p/w185/"))
        self.assertTrue(dark["seasons"][0]["poster_url"].startswith("https://image.tmdb.org/t/p/w185/"))
        ev = self.events()[0]
        self.assertEqual((ev["scope"], ev["dry_run"], ev["status"], ev["series"], ev["seasons"], ev["episodes"], ev["written"]),
                         ("seasons", True, "success", 2, 3, 8, 0))
        self.assertEqual((ev["posters"], ev["stills"], ev["lookup"], ev["matched"]), (3, 8, 2, 0))
        self.assertEqual(sstate.activity_list(), [])

    def test_apply_from_a_fresh_preview_writes_without_new_tmdb_queries(self):
        a, b, fake = self.library()
        self.post(dry_run=True)
        n = len(fake.calls)
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=False)
        self.assertTrue(r.json()["from_preview"])
        self.assertEqual(len(fake.calls), n)
        refresh.assert_called_once_with()
        self.assertEqual(sorted(self.seasons_rows()), [1, 2])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM library_seasons")["n"], 3)
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM library_episodes")["n"], 8)
        ev = self.events()[0]
        self.assertEqual((ev["dry_run"], ev["from_preview"], ev["written"], ev["seasons"], ev["episodes"]), (False, True, 3, 3, 8))
        summary = self.c.get("/api/ops/tmdb/status").json()["season_preview"]
        self.assertTrue(summary["applied"] and not summary["fresh"])
        self.assertEqual(summary["scope"], "seasons")
        # nothing left to do: a live run finds no due seasons
        r2 = self.post(dry_run=False)
        self.assertFalse(r2.json()["from_preview"])
        self.assertEqual(len(fake.calls), n)

    def test_live_apply_without_preview_and_provider_rows_stay_untouched(self):
        a, b, fake = self.library()
        before = self.provider_state()
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            r = self.post(dry_run=False)
        self.assertFalse(r.json()["from_preview"])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM library_seasons")["n"], 3)
        self.assertEqual(self.provider_state(), before)
        refresh.assert_called_once_with()
        self.assertEqual(self.events()[0]["written"], 3)
        # ... and the catalogue now carries the artwork
        item = self.snap().by_id[a]
        self.assertTrue(item["seasons"][0]["poster_url"].endswith("sp70_1.jpg"))

    def test_stale_preview_and_title_preview_do_not_apply(self):
        a, b, fake = self.library()
        self.post(dry_run=True)
        pv = jload(tmdb_admin.SEASONS_PREVIEW_PATH)
        pv["created_at"] -= tmdb_admin.PREVIEW_TTL + 5
        jdump(tmdb_admin.SEASONS_PREVIEW_PATH, pv)
        n = len(fake.calls)
        with patch.object(cache, "refresh_and_prewarm"):
            r = self.post(dry_run=False)
        self.assertFalse(r.json()["from_preview"])
        self.assertGreater(len(fake.calls), n)

    def test_preview_apply_skips_series_rebound_or_removed_meanwhile(self):
        a, b, fake = self.library()
        self.post(dry_run=True)
        with closing(db.connect()) as conn, conn:
            conn.execute("UPDATE library_items SET tmdb_id=999 WHERE id=?", (a,))
            conn.execute("DELETE FROM library_items WHERE id=?", (b,))
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            self.post(dry_run=False)
        self.assertEqual(self.seasons_rows(), {})
        self.assertEqual(self.events()[0]["written"], 0)
        refresh.assert_not_called()

    def test_preview_endpoint_scope_paging_and_filters(self):
        a, b, fake = self.library()
        self.assertFalse(self.c.get("/api/ops/tmdb/preview?scope=seasons").json()["available"])
        self.post(dry_run=True)
        p = self.c.get("/api/ops/tmdb/preview?scope=seasons&limit=1").json()
        self.assertEqual((p["available"], p["total"], len(p["items"]), p["has_more"]), (True, 2, 1, True))
        self.assertNotIn("data", p["items"][0])
        self.assertEqual(p["summary"]["scope"], "seasons")
        self.assertEqual(p["summary"]["counts"], {"auto": 2, "empty": 0, "error": 0, "deferred": 0})
        self.assertEqual(p["summary"]["totals"]["seasons"], 3)
        self.assertEqual(self.c.get("/api/ops/tmdb/preview?scope=seasons&decision=empty").json()["total"], 0)
        self.assertFalse(self.c.get("/api/ops/tmdb/preview").json()["available"])  # title-match preview untouched
        self.assertEqual(self.c.get("/api/ops/tmdb/preview?scope=bogus").status_code, 422)

    def test_empty_and_error_decisions_in_the_preview(self):
        a = self.series("tmdb_tv_70", "Dark", eps={1: [1], 2: [1]})
        self.series("tmdb_tv_73", "Yok", 2019, tmdb_id=73, eps={1: [1]})
        self.series("tmdb_tv_74", "Hata", 2019, tmdb_id=74, eps={1: [1]})
        fake = self.use(FakeSeasons({(70, 1): {"tr": season_body(70, 1, 1)}, (74, 1): {"tr": tmdb.TmdbError("HTTP 500", 500)}}))
        self.post(dry_run=True)
        got = {i["id"]: i for i in self.c.get("/api/ops/tmdb/preview?scope=seasons&limit=10").json()["items"]}
        self.assertEqual((got[a]["decision"], [s["status"] for s in got[a]["seasons"]]), ("auto", ["ok", "empty"]))
        self.assertEqual(got["tmdb_tv_73"]["decision"], "empty")
        self.assertEqual(got["tmdb_tv_74"]["decision"], "error")
        with patch.object(cache, "refresh_and_prewarm"):
            self.post(dry_run=False)
        # empty stamped (from the preview), error not
        self.assertEqual({(r["canonical_id"], r["status"]) for r in db.query("SELECT * FROM library_seasons")},
                         {(a, "ok"), (a, "empty"), ("tmdb_tv_73", "empty")})
        self.assertEqual(fake.paths().count((74, 1)), 1)

    def test_tmdb_outage_aborts_partial_and_stamps_nothing(self):
        for i in range(3):
            self.series("tmdb_tv_%d" % (80 + i), "S%d" % i, 2018, tmdb_id=80 + i, eps={1: [1]})
        self.use(FakeSeasons({(80 + i, 1): {"tr": tmdb.TmdbError("timeout")} for i in range(3)}))
        with patch.object(cache, "refresh_and_prewarm") as refresh:
            self.post(dry_run=False)
        ev = self.events()[0]
        self.assertEqual((ev["status"], ev["aborted"], ev["written"], ev["errors"]), ("partial", "tmdb_unreachable", 0, 3))
        self.assertEqual(self.seasons_rows(), {})
        refresh.assert_not_called()

    def test_progress_reaches_the_job_bar(self):
        for i in range(12):
            self.series("tmdb_tv_%d" % (100 + i), "Dizi %d" % i, 2000 + i, tmdb_id=100 + i, eps={1: [1]})
        self.use(FakeSeasons({(100 + i, 1): {"tr": season_body(100 + i, 1, 1)} for i in range(12)}))
        seen = []
        real = sstate.activity_update

        def spy(site, kind, **fields):
            seen.append(dict(fields))
            return real(site, kind, **fields)
        job = tmdb_admin.begin("series", True, scope="seasons")
        live = sstate.activity_list()[0]
        self.assertEqual((live["site"], live["kind"], live["scope"], live["label"]),
                         ("tmdb", "tmdb", "seasons", "TMDB sezon ve bölüm görselleri (dizi)"))
        with patch.object(sstate, "activity_update", spy):
            tmdb_admin.execute(job)
        labels = [f["label"] for f in seen if "label" in f]
        self.assertEqual(labels[0], "TMDB sezon ve bölüm görselleri (dizi) 0/12")
        self.assertIn("TMDB sezon ve bölüm görselleri (dizi) 10/12", labels)
        self.assertEqual(labels[-1], "TMDB sezon ve bölüm görselleri (dizi) 12/12")
        self.assertEqual(sstate.activity_list(), [])

    def test_validation_and_gates(self):
        self.library()
        self.assertEqual(self.c.post("/api/ops/tmdb/backfill", json={"kind": "movie", "scope": "seasons"}).status_code, 422)
        self.assertEqual(self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "scope": "bogus"}).status_code, 422)
        settings.update({"tmdb": {"types": ["movie"]}})
        r = self.post(dry_run=True)
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (400, "tmdb_type_disabled"))
        settings.update({"tmdb": {"types": ["series"]}})
        with patch.dict(_os.environ, {"TMDB_ACCESS_KEY": ""}):
            self.assertEqual(self.post(dry_run=True).status_code, 400)
        sstate.activity_start("tmdb", "tmdb", "manual")
        self.assertEqual(self.post(dry_run=True).status_code, 409)
        sstate.activity_end("tmdb", "tmdb")
        self.assertEqual(self.events(), [])

    def test_default_scope_is_still_the_title_backfill(self):
        self.series("tmdb_tv_70", "Dark", eps={1: [1]})
        fake = self.use(FakeSeasons(full_table()))
        r = self.c.post("/api/ops/tmdb/backfill", json={"kind": "series", "dry_run": True})
        self.assertEqual((r.status_code, r.json()["scope"]), (200, "items"))
        self.assertEqual(fake.season_calls, [])
        self.assertEqual(self.events()[0]["scope"], "items")

    def test_status_coverage_for_seasons(self):
        a, b, fake = self.library()
        cov = self.c.get("/api/ops/tmdb/status").json()["coverage"]["seasons"]
        self.assertEqual(cov, {"series": 3, "series_tmdb": 2, "seasons": 4, "seasons_checked": 0, "season_posters": 0,
                               "episodes": 5, "tmdb_stills": 0, "episode_stills": 0})
        with patch.object(cache, "refresh_and_prewarm"):
            self.post(dry_run=False, use_preview=False)
        with closing(db.connect()) as conn, conn:  # a source still on the unmatched series' episode
            conn.execute("UPDATE video_sources SET episode_still_url='https://yabancidizi.news/a.jpg' WHERE canonical_id='series-x-2010'")
        d = self.c.get("/api/ops/tmdb/status").json()
        cov = d["coverage"]["seasons"]
        self.assertEqual((cov["seasons_checked"], cov["season_posters"], cov["tmdb_stills"], cov["episode_stills"]), (3, 3, 4, 5))
        self.assertEqual((d["coverage"]["series"]["total"], d["coverage"]["movie"]["total"]), (4, 0))
        self.assertNotIn(KEY_TEXT, json.dumps(d))
        self.assertIsNone(d["job"])

    def test_events_feed_and_library_strip(self):
        a, b, fake = self.library()
        self.post(dry_run=True)
        feed = self.c.get("/api/ops/events").json()["events"]
        ev = next(e for e in feed if e["kind"] == "tmdb")
        self.assertEqual((ev["scope"], ev["seasons"], ev["episodes"]), ("seasons", 3, 8))
        with patch.object(cache, "refresh_and_prewarm"):
            self.post(dry_run=False)
        d = self.c.get("/api/ops/library/%s/videos" % a).json()
        self.assertEqual([(s["season"], s["has_poster"], s["episodes"], s["stills"], s["checked"]) for s in d["seasons"]],
                         [(1, True, 2, 2, True), (2, True, 1, 1, True)])
        self.assertEqual(d["seasons"][0]["id"], "tmdb_tv_70:s1")
        movie = self.add("m1", "Film")
        self.assertEqual(self.c.get("/api/ops/library/%s/videos" % movie).json()["seasons"], [])

    def test_cli_seasons_dry_run_and_apply(self):
        import contextlib
        from tools import tmdb_enrich
        self.library()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tmdb_enrich.main(["x", "--seasons", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("series: 2 with seasons to look up of 4 in library (dry-run)", out.getvalue())
        self.assertEqual(self.seasons_rows(), {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tmdb_enrich.main(["x", "--seasons"])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM library_seasons")["n"], 3)
        self.assertEqual(json.loads(out.getvalue().strip().splitlines()[-1])["seasons"], 3)
        self.assertNotIn(KEY_TEXT, out.getvalue())


class ThreadSafetyTests(SBase):
    def test_write_takes_the_ingest_lock_unless_the_caller_holds_it(self):
        from app.library import enrich
        cid = self.series(eps={1: [1]})
        self.use(FakeSeasons(full_table()))
        done = threading.Event()

        def work():
            seasons.enrich_series([cid])
            done.set()
        with enrich.ingest_lock():
            t = threading.Thread(target=work, daemon=True)
            t.start()
            self.assertFalse(done.wait(1.0))  # blocked on the lock while "ingest" holds it
            self.assertEqual(self.seasons_rows(), {})
        self.assertTrue(done.wait(10))
        t.join(5)
        self.assertEqual(list(self.seasons_rows()), [1])
        with enrich.ingest_lock():  # locked=True: the caller (ingest) holds it, no deadlock
            seasons.enrich_series([cid], force=True, locked=True)


if __name__ == "__main__":
    unittest.main()
