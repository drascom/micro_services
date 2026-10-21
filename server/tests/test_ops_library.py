"""Admin Kütüphane: K/T hesabı, süzgeçler, sayfalama, facet, boş durum. Ağsız, geçici DB."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from urllib.parse import quote
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, db
from app.routers import ops, ops_library

NOW = 1_800_000_000


class LibraryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(config, "DB_PATH", os.path.join(self.tmp.name, "t.db"))
        p.start()
        self.addCleanup(p.stop)
        db.init()
        app = FastAPI()
        app.include_router(ops.router)
        app.include_router(ops_library.router)
        self.c = TestClient(app)
        self.n = 0

    def add(self, cid, title, year=2000, type="movie", sources=("sinemalar",), videos=(), tmdb_id=None,
            enrich=None, imdb=None, poster=None, tmdb_poster=None, backdrop=None, added=None, original=None):
        """videos: (site, kind, status) veya (site, kind, status, extra dict)."""
        added = added if added is not None else NOW + self.n
        self.n += 1
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO library_items(id,tmdb_id,type,title,original_title,year,poster_url,backdrop_url,"
                "added_at,updated_at,imdb_id,tmdb_poster_url,tmdb_enrich_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, tmdb_id, type, title, original, year, poster, backdrop, added, added, imdb, tmdb_poster, enrich))
            for site in sources:
                conn.execute("INSERT INTO source_items(source,source_key,canonical_id,fetched_at) VALUES (?,?,?,?)",
                             (site, cid + "-" + site, cid, NOW))
            for i, spec in enumerate(videos):
                site, kind, status = spec[:3]
                extra = spec[3] if len(spec) > 3 else {}
                conn.execute(
                    "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,"
                    "resolver,status,failures,last_error,last_checked_at,last_success_at,resolved_payload,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"vs_{cid}_{i}", cid, site, cid + "-" + site, extra.get("episode_id", ""), extra.get("season"),
                     extra.get("episode"), kind, extra.get("locator", f"https://{site}.example/{cid}/{i}"), "page",
                     status, extra.get("failures", 3 if status == "broken" else 1 if status == "suspect" else 0),
                     extra.get("error"), extra.get("checked"), extra.get("success"), extra.get("payload"), NOW))

    def get(self, qs="", **kw):
        r = self.c.get("/api/ops/library" + ("?" + qs if qs else ""), **kw)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ids(self, qs=""):
        return [i["id"] for i in self.get(qs)["items"]]

    def seed(self):
        """Altı öğe: her K/T durumundan biri + yetim kayıt."""
        self.add("alfa", "Alfa", 2001, sources=("sinemalar",), enrich="matched", tmdb_id=11, imdb="tt1111111",
                 tmdb_poster="https://image.tmdb.org/t/p/w500/a.jpg", poster="https://cdn.sinemalar.com/a.jpg",
                 videos=[("sinemalar", "movie", "healthy"), ("yabancidizi", "movie", "broken"),
                         ("sinemalar", "trailer", "unknown")])           # 1/3 kısmen kırık
        self.add("beta", "Beta", 2010, type="series", sources=("yabancidizi",), enrich="review",
                 videos=[("yabancidizi", "episode", "broken", {"season": 1, "episode": 1}),
                         ("yabancidizi", "episode", "broken", {"season": 1, "episode": 2})])  # 2/2 tamamen kırık
        self.add("celik", "Çelik Gemi", 1999, sources=("sinemalar", "yabancidizi"), enrich="unmatched",
                 original="Steel Ship", poster="https://cdn.sinemalar.com/ng/img/no-poster.svg",
                 videos=[("sinemalar", "movie", "healthy"), ("yabancidizi", "movie", "unknown"),
                         ("sinemalar", "trailer", "suspect")])           # 0/3, 1 şüpheli
        self.add("delta", "Delta", 2020, sources=("sinemalar",))         # 0/0 kaynak yok
        self.add("epsilon", "Epsilon", 2015, type="series", sources=("yabancidizi",), tmdb_id=55,
                 videos=[("yabancidizi", "episode", "broken", {"season": 1, "episode": 1}),
                         ("yabancidizi", "episode", "suspect", {"season": 1, "episode": 2}),
                         ("yabancidizi", "episode", "disabled", {"season": 1, "episode": 3})])  # 1/2 (+1 devre dışı)
        self.add("yetim", "Yetim Film", 2005, sources=())               # kaynak sitesi yok


class KtTests(LibraryBase):
    def test_kt_counts_and_state(self):
        self.seed()
        v = {i["id"]: i["videos"] for i in self.get()["items"]}
        self.assertEqual((v["alfa"]["broken"], v["alfa"]["total"], v["alfa"]["state"], v["alfa"]["label"]),
                         (1, 3, "partial", "1/3"))
        self.assertEqual((v["beta"]["label"], v["beta"]["state"]), ("2/2", "dead"))
        self.assertEqual((v["celik"]["label"], v["celik"]["state"], v["celik"]["suspect"]), ("0/3", "ok", 1))
        self.assertEqual((v["delta"]["label"], v["delta"]["state"]), ("0/0", "none"))
        # devre dışı T'ye dahil değil, şüpheli K'ya dahil değil
        self.assertEqual((v["epsilon"]["label"], v["epsilon"]["state"], v["epsilon"]["suspect"],
                          v["epsilon"]["disabled"]), ("1/2", "partial", 1, 1))
        self.assertEqual(v["alfa"]["trailers"], 1)
        self.assertEqual(v["yetim"]["label"], "0/0")

    def test_item_fields(self):
        self.seed()
        it = {i["id"]: i for i in self.get()["items"]}
        a = it["alfa"]
        self.assertEqual((a["title"], a["year"], a["type"], a["tmdb"], a["imdb_id"], a["tmdb_id"]),
                         ("Alfa", 2001, "movie", "matched", "tt1111111", 11))
        self.assertEqual(a["sources"], ["sinemalar"])
        self.assertEqual(it["celik"]["sources"], ["sinemalar", "yabancidizi"])
        self.assertEqual(it["yetim"]["sources"], [])
        self.assertEqual(it["beta"]["tmdb"], "review")
        self.assertEqual(it["celik"]["tmdb"], "unmatched")
        self.assertEqual(it["delta"]["tmdb"], "unmatched")   # hiç denenmemiş = eşleşme yok
        self.assertEqual(it["epsilon"]["tmdb"], "matched")   # tmdb_id varsa eşleşti
        self.assertTrue(a["added_at"].endswith("Z"))

    def test_artwork_urls(self):
        self.seed()
        it = {i["id"]: i for i in self.get()["items"]}
        self.assertEqual(it["alfa"]["poster"], "/img/alfa/portrait?w=200")
        self.assertEqual(it["alfa"]["poster_origin"], "tmdb")        # TMDB varsa o
        self.assertEqual(it["alfa"]["backdrop"], "/img/alfa/card?w=500")
        self.assertIsNone(it["celik"]["poster"])                     # "no-poster.svg" resim sayılmaz
        self.assertIsNone(it["celik"]["poster_origin"])
        self.assertIsNone(it["delta"]["backdrop"])
        self.add("kaynak", "Kaynak Poster", poster="https://cdn.sinemalar.com/x.jpg")
        got = {i["id"]: i for i in self.get()["items"]}["kaynak"]
        self.assertEqual(got["poster_origin"], "source")             # TMDB yoksa kaynak posteri


class FilterTests(LibraryBase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_broken_filters(self):
        self.assertEqual(sorted(self.ids("broken=ok")), ["celik"])
        self.assertEqual(sorted(self.ids("broken=partial")), ["alfa", "epsilon"])
        self.assertEqual(self.ids("broken=dead"), ["beta"])
        self.assertEqual(sorted(self.ids("broken=suspect")), ["celik", "epsilon"])
        self.assertEqual(sorted(self.ids("broken=none")), ["delta", "yetim"])
        self.assertEqual(len(self.ids("broken=all")), 6)
        self.assertEqual(len(self.ids("broken=")), 6)

    def test_site_filter_single_multi_and_comma(self):
        self.assertEqual(sorted(self.ids("site=sinemalar")), ["alfa", "celik", "delta"])
        self.assertEqual(sorted(self.ids("site=yabancidizi")), ["beta", "celik", "epsilon"])
        both = sorted(self.ids("site=sinemalar&site=yabancidizi"))
        self.assertEqual(both, ["alfa", "beta", "celik", "delta", "epsilon"])  # yetim hiçbir sitede yok
        self.assertEqual(sorted(self.ids("site=sinemalar,yabancidizi")), both)
        self.assertEqual(self.ids("site=yok"), [])

    def test_kind_and_tmdb(self):
        self.assertEqual(sorted(self.ids("kind=series")), ["beta", "epsilon"])
        self.assertEqual(len(self.ids("kind=movie")), 4)
        self.assertEqual(sorted(self.ids("tmdb=matched")), ["alfa", "epsilon"])
        self.assertEqual(self.ids("tmdb=review"), ["beta"])
        self.assertEqual(sorted(self.ids("tmdb=unmatched")), ["celik", "delta", "yetim"])

    def test_filters_combine(self):
        self.assertEqual(self.ids("kind=series&broken=partial"), ["epsilon"])
        self.assertEqual(self.ids("site=sinemalar&broken=partial"), ["alfa"])
        self.assertEqual(self.ids("site=yabancidizi&kind=series&tmdb=review"), ["beta"])
        self.assertEqual(self.ids("broken=dead&kind=movie"), [])

    def test_search_is_diacritic_and_case_insensitive(self):
        self.assertEqual(self.ids("q=celik"), ["celik"])
        self.assertEqual(self.ids("q=" + quote("ÇELİK")), ["celik"])
        self.assertEqual(self.ids("q=steel"), ["celik"])            # özgün başlık
        self.assertEqual(self.ids("q=" + quote("gemi çelik")), ["celik"])     # sözcük sırası önemsiz, hepsi geçmeli
        self.assertEqual(self.ids("q=2015"), ["epsilon"])           # yıl
        self.assertEqual(self.ids("q=yok"), [])
        self.assertEqual(self.ids("q=%25"), [])                      # '%' joker değil
        self.assertEqual(self.ids("q=_lfa"), [])                     # '_' joker değil
        self.assertEqual(self.ids("q=alfa&kind=movie&site=sinemalar"), ["alfa"])

    def test_sorting(self):
        self.assertEqual(self.ids("sort=recent"), ["yetim", "epsilon", "delta", "celik", "beta", "alfa"])
        self.assertEqual(self.ids("sort=title"), ["alfa", "beta", "celik", "delta", "epsilon", "yetim"])
        broken = self.ids("sort=broken")
        self.assertEqual(broken[:3], ["beta", "epsilon", "alfa"])   # 2 kırık; sonra oran 1/2 > 1/3

    def test_invalid_params_rejected(self):
        for qs in ("broken=zzz", "kind=film", "tmdb=x", "sort=zzz", "limit=0", "limit=999", "offset=-1"):
            self.assertEqual(self.c.get("/api/ops/library?" + qs).status_code, 422, qs)


class PagingFacetTests(LibraryBase):
    def test_pagination(self):
        for i in range(120):
            self.add(f"m{i:03d}", f"Film {i:03d}", videos=[("sinemalar", "movie", "healthy")])
        first = self.get("limit=50")
        self.assertEqual((first["total"], first["library_total"], len(first["items"]), first["has_more"]),
                         (120, 120, 50, True))
        second = self.get("limit=50&offset=50&facets=0")
        third = self.get("limit=50&offset=100&facets=0")
        self.assertEqual((len(second["items"]), second["has_more"]), (50, True))
        self.assertEqual((len(third["items"]), third["has_more"], third["total"]), (20, False, 120))
        ids = [i["id"] for p in (first, second, third) for i in p["items"]]
        self.assertEqual(len(set(ids)), 120)                          # sayfalar çakışmaz
        self.assertNotIn("facets", second)
        self.assertIn("facets", first)
        self.assertEqual(self.get("offset=500")["items"], [])
        self.assertEqual(len(self.get()["items"]), 50)                # varsayılan sayfa boyutu 50

    def test_facets_respect_other_filters_but_not_own(self):
        self.seed_small()
        f = self.get()["facets"]
        self.assertEqual(f["broken"], {"all": 4, "ok": 1, "partial": 1, "dead": 1, "suspect": 1, "none": 1})
        self.assertEqual(f["kind"], {"movie": 3, "series": 1})
        self.assertEqual(f["tmdb"], {"matched": 1, "review": 1, "unmatched": 2})
        self.assertEqual({s["site"]: s["count"] for s in f["sites"]}, {"sinemalar": 3, "yabancidizi": 2})
        # tür süzgeci: kırık facet'i yalnız dizilere bakar; tür facet'i kendi süzgecini yok sayar
        g = self.get("kind=series")["facets"]
        self.assertEqual(g["broken"]["all"], 1)
        self.assertEqual(g["kind"], {"movie": 3, "series": 1})
        # site süzgeci çoklu seçimde diğer sitelerin sayısını korur
        h = self.get("site=sinemalar")["facets"]
        self.assertEqual({s["site"]: s["count"] for s in h["sites"]}, {"sinemalar": 3, "yabancidizi": 2})
        self.assertEqual(h["broken"]["all"], 3)
        # sonuç vermeyen seçili site 0 sayısıyla listede kalır
        z = self.get("site=yok")["facets"]
        self.assertIn({"site": "yok", "count": 0}, z["sites"])

    def seed_small(self):
        self.add("a", "A", sources=("sinemalar",), enrich="matched", videos=[("sinemalar", "movie", "healthy"),
                                                                             ("sinemalar", "trailer", "broken")])
        self.add("b", "B", type="series", sources=("yabancidizi",), enrich="review",
                 videos=[("yabancidizi", "episode", "broken")])
        self.add("c", "C", sources=("sinemalar", "yabancidizi"), videos=[("sinemalar", "movie", "suspect")])
        self.add("d", "D", sources=("sinemalar",))

    def test_speed_with_many_items(self):
        with closing(db.connect()) as conn, conn:
            for i in range(600):
                cid = f"x{i}"
                conn.execute("INSERT INTO library_items(id,type,title,year,added_at,updated_at) VALUES (?,?,?,?,?,?)",
                             (cid, "series" if i % 3 == 0 else "movie", f"Başlık {i}", 1990 + i % 30, NOW + i, NOW))
                conn.execute("INSERT INTO source_items(source,source_key,canonical_id,fetched_at) VALUES (?,?,?,?)",
                             ("sinemalar" if i % 2 else "yabancidizi", cid, cid, NOW))
                for j in range(3):
                    conn.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,status,"
                                 "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                 (f"v{i}_{j}", cid, "sinemalar", cid, "movie", "https://a.example/x",
                                  ("broken", "suspect", "healthy")[(i + j) % 3], NOW))
        started = time.monotonic()
        for qs in ("", "broken=partial&q=" + quote("başlık"), "sort=broken&site=sinemalar", "sort=title&kind=series"):
            self.assertLess(len(self.get(qs)["items"]), 51)
        self.assertLess((time.monotonic() - started) / 4, 0.5)        # istek başına ortalama


class EmptyStateTests(LibraryBase):
    def test_empty_library(self):
        d = self.get()
        self.assertEqual((d["total"], d["library_total"], d["items"], d["has_more"]), (0, 0, [], False))
        self.assertEqual(d["facets"]["broken"], {"all": 0, "ok": 0, "partial": 0, "dead": 0, "suspect": 0, "none": 0})
        self.assertEqual(d["facets"]["sites"], [])
        self.assertEqual(self.get("q=x&broken=dead&site=a&kind=series&tmdb=review")["items"], [])
        self.assertIn("source_mode", d)

    def test_no_tables_at_all(self):
        """Şeması hiç kurulmamış DB (örn. mock modu, yeni kurulum): hata değil boş durum."""
        raw = os.path.join(self.tmp.name, "raw.db")
        sqlite3.connect(raw).close()
        with patch.object(config, "DB_PATH", raw):
            r = self.c.get("/api/ops/library")
            self.assertEqual(r.status_code, 200)
            self.assertEqual((r.json()["total"], r.json()["items"]), (0, []))
            self.assertEqual(self.c.get("/api/ops/library/x/videos").status_code, 404)

    def test_filter_without_matches_is_distinguishable_from_empty_library(self):
        self.add("a", "A")
        d = self.get("q=zzz")
        self.assertEqual((d["total"], d["library_total"]), (0, 1))


class VideosEndpointTests(LibraryBase):
    def test_videos_list(self):
        payload = json.dumps({"streams": [{"url": "https://cdn.vidmolly.to/x/master.m3u8?token=SECRET",
                                           "provider": "Vidmolly"}]})
        self.add("f", "Film", videos=[
            ("sinemalar", "trailer", "healthy", {"checked": NOW - 100, "success": NOW - 100}),
            ("yabancidizi", "movie", "suspect", {"error": "timeout", "failures": 2, "checked": NOW - 50,
                                                 "locator": "https://www.yabancidizi.news/film/x"}),
            ("yabancidizi", "movie", "broken", {"error": "decode_failed", "failures": 3, "checked": NOW - 10,
                                                "payload": payload}),
            ("sinemalar", "movie", "disabled"),
        ])
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO playback_attempts(token,source_id,created_at,failure_at,error_code) VALUES (?,?,?,?,?)",
                         ("t1", "vs_f_2", NOW - 20, NOW - 19, "decode_failed"))
            conn.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)",
                         ("t2", "vs_f_2", NOW - 5))
        d = self.c.get("/api/ops/library/f/videos").json()
        self.assertEqual(d["item"]["title"], "Film")
        self.assertEqual(d["summary"], {"total": 3, "broken": 1, "suspect": 1, "healthy": 1, "unknown": 0,
                                        "disabled": 1, "blocked": 0, "trailers": 1, "trailers_dead": 0, "state": "partial", "label": "1/3"})
        vids = d["videos"]
        self.assertEqual([v["status"] for v in vids], ["broken", "suspect", "healthy", "disabled"])  # kırık önce
        broken = vids[0]
        self.assertEqual((broken["site"], broken["host"], broken["last_error"], broken["failures"]),
                         ("yabancidizi", "yabancidizi.example", "decode_failed", 3))
        self.assertEqual(broken["providers"], ["Vidmolly"])
        self.assertEqual(broken["stream_hosts"], ["cdn.vidmolly.to"])
        self.assertNotIn("SECRET", json.dumps(d))                     # akış URL'si/token sızmaz
        self.assertTrue(broken["last_checked_at"].endswith("Z"))
        self.assertTrue(broken["last_attempt_at"].endswith("Z"))       # en son deneme t2
        self.assertIsNotNone(broken["last_failure_at"])
        self.assertEqual(vids[1]["host"], "www.yabancidizi.news")
        self.assertIsNone(vids[2]["last_attempt_at"])
        self.assertFalse(d["truncated"])
        # list ile detay aynı K/T'yi verir
        item = self.get("q=film")["items"][0]
        self.assertEqual(item["videos"]["label"], d["summary"]["label"])

    def test_episode_order_and_limit(self):
        vids = [("yabancidizi", "episode", "healthy", {"season": s, "episode": e}) for s in (1, 2) for e in (1, 2, 3)]
        self.add("dizi", "Dizi", type="series", sources=("yabancidizi",), videos=vids)
        d = self.c.get("/api/ops/library/dizi/videos").json()
        self.assertEqual([(v["season"], v["episode"]) for v in d["videos"]],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])
        short = self.c.get("/api/ops/library/dizi/videos?limit=2").json()
        self.assertEqual((len(short["videos"]), short["truncated"], short["summary"]["total"]), (2, True, 6))

    def test_no_videos_and_unknown_item(self):
        self.add("bos", "Boş")
        d = self.c.get("/api/ops/library/bos/videos").json()
        self.assertEqual((d["videos"], d["summary"]["label"], d["summary"]["state"]), ([], "0/0", "none"))
        r = self.c.get("/api/ops/library/yok/videos")
        self.assertEqual(r.status_code, 404)


class AdminPageTests(LibraryBase):
    def test_page_and_assets(self):
        page = self.c.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Kütüphane", page.text)
        self.assertIn("/admin/library.js", page.text)
        js = self.c.get("/admin/library.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("/api/ops/library", js.text)
        self.assertEqual(self.c.get("/admin/app.js").status_code, 200)   # mevcut panel bozulmadı
        self.assertEqual(self.c.get("/admin/style.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
