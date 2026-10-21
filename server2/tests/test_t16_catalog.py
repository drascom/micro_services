import _sandbox  # noqa: F401  (must be first)

import os
import re
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main
from app.admin import routes_catalog
from app.core import events
from app.core.catalog import cache, write
from app.core.fetch.base import FetchError
from app.core.play import ctx as playctx, hooks as play_hooks, resolve, sources, streamprobe, streamproxy
from app.core.play.providers import recipes, registry, youtube

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "app", "admin", "static")
BASE = "https://www.fakesite.test"
P = "/api/control/catalog"
EP_PAGE = BASE + "/dizi/sinyal/1-sezon-1-bolum/"
NO_PLAYER = BASE + "/dizi/sinyal/1-sezon-2-bolum/"
FILM_PAGE = BASE + "/film/gece/"
HLS = [{"url": "https://cdn.vidhost.test/m/720/index.m3u8", "type": "hls", "quality": "720p", "label": "720p",
        "width": 1280, "height": 720, "bitrate": 2800000},
       {"url": "https://cdn.vidhost.test/m/360/index.m3u8", "type": "hls", "quality": "360p", "label": "360p",
        "width": 640, "height": 360, "bitrate": 800000}]
SUBS = [{"url": "https://cdn.vidhost.test/s/tr.vtt", "lang": "tr", "label": "Türkçe", "format": "vtt"}]


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def page(self, ctx, url, timeout=None):
        if url not in self.pages:
            raise FetchError("http_404", "missing", 404, url)
        return self.pages[url]


class NoNet:
    def fetch_url(self, url, **kw):
        raise FetchError("network", "offline in tests", url=url)


def provider(streams, proxy=False):
    def res(url, referer=""):
        return {"provider": "FakeHost", "streams": [{**s, **({"stream_proxy": True} if proxy else {})} for s in streams],
                "subtitles": [dict(t) for t in SUBS], "audio": [{"lang": "tr", "label": "Türkçe", "default": True}]}
    return registry.Provider("fakehost", lambda u: "player.vidhost.test" in u, res, "test", ("player.vidhost.test",))


IFRAME = '<div class="video"><iframe src="https://player.vidhost.test/embed/1"></iframe></div>'
CTX = lambda site: playctx.make_ctx(site, BASE, resolvers=[{"type": "iframe", "selector": ".video iframe"}],  # noqa: E731
                                    fetch=Pages({EP_PAGE: IFRAME, FILM_PAGE: IFRAME, NO_PLAYER: "<p>yok</p>"}))
PROBE = {"ok": True, "http": 200, "kind": "hls", "duration_s": 2700, "variants": [{}, {}], "encrypted": False, "ms": 12}


def title(site, key, name, kind="movie", origins=(), **kw):
    return write.upsert_title(site, {"key": key, "type": kind, "title": name, "source_url": f"https://{site}.test/{key}", **kw},
                              origins=origins)


class CatalogCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        cls._old = config.DB_PATH
        config.DB_PATH = os.path.join(cls.tmp, "t16.db")
        cache.reset()
        cls._client = TestClient(main.app)
        cls.c = cls._client.__enter__()
        now = int(time.time())
        db.execute("INSERT INTO sites(site_id,display_name,base_url,status,created_at,updated_at) VALUES "
                   "('alpha','A',?,'active',?,?)", (BASE, now, now))
        cls.series = write.upsert_title("alpha", {"key": "dizi/sinyal", "type": "series", "title": "Kayıp Sinyal",
                                                   "source_url": BASE + "/dizi/sinyal/", "year": 2021, "overview": "Bir sinyal."},
                                        origins=[("section", "Yeni Bölümler", 1)])
        write.upsert_episodes("alpha", "dizi/sinyal", [
            {"season": 1, "episode": 1, "url": EP_PAGE, "title": "Pilot"}, {"season": 1, "episode": 2, "url": NO_PLAYER},
            {"season": 2, "episode": 1, "url": BASE + "/dizi/sinyal/2-sezon-1-bolum/"}])
        cls.movie = title("alpha", "film/gece", "Gece Treni", year=2024)
        write.set_movie_source("alpha", "film/gece", FILM_PAGE)
        cls.nosource = title("alpha", "film/defter", "Eski Defter")
        cls.blocked = title("alpha", "film/kapali", "Kapalı Film")
        write.set_movie_source("alpha", "film/kapali", BASE + "/film/kapali/")
        write.mark_blocked("alpha", BASE + "/film/kapali/", kind="movie", reason="telif", via="rule")
        cls.broken = title("alpha", "film/kirik", "Kırık Film")
        write.set_movie_source("alpha", "film/kirik", BASE + "/film/kirik/")
        db.execute("UPDATE video_sources SET status='broken', failures=3 WHERE canonical_id=?", (cls.broken,))
        cls.off = title("alpha", "film/kapat", "Kapatılmış Film")
        write.set_movie_source("alpha", "film/kapat", BASE + "/film/kapat/")
        db.execute("UPDATE video_sources SET status='disabled' WHERE canonical_id=?", (cls.off,))
        for n in range(55):
            key = f"film/dolgu-{n:02d}"
            title("beta", key, f"Dolgu {n:02d}")
            write.set_movie_source("beta", key, f"https://beta.test/{key}")

    @classmethod
    def tearDownClass(cls):
        cls._client.__exit__(None, None, None)
        cache.reset()
        config.DB_PATH = cls._old

    def setUp(self):
        resolve.reset_caches()
        sources.reset_caches()
        self.patches = [mock.patch.object(registry, "CODE_PROVIDERS", [provider(HLS)]),
                        mock.patch.object(registry, "fetch", NoNet()), mock.patch.object(recipes, "load_all", lambda: []),
                        mock.patch.object(play_hooks, "ctx_for", CTX),
                        mock.patch.object(streamprobe, "probe", lambda *a, **k: dict(PROBE))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def sid(self, cid, episode_id=""):
        return db.query_one("SELECT id FROM video_sources WHERE canonical_id=? AND episode_id=?", (cid, episode_id))["id"]

    def health(self):
        return [tuple(r) for r in db.query("SELECT id, status, failures FROM video_sources ORDER BY id")]


class ListTests(CatalogCase):
    def test_filters_and_pagination(self):
        d = self.c.get(P, params={"site": "beta", "sort": "name"}).json()
        self.assertEqual((d["total"], d["pages"], len(d["items"])), (55, 2, 50))
        self.assertEqual(d["items"][0]["title"], "Dolgu 00")
        self.assertEqual(len(self.c.get(P, params={"site": "beta", "page": 2}).json()["items"]), 5)
        d = self.c.get(P, params={"type": "series"}).json()
        self.assertEqual([i["id"] for i in d["items"]], [self.series])
        it = d["items"][0]
        self.assertEqual(it["summary"]["text"], "3 bölüm · 3 bilinmiyor")
        self.assertEqual(it["origins"], [{"site": "alpha", "kind": "section", "value": "Yeni Bölümler"}])
        self.assertFalse(it["tmdb_matched"])
        self.assertEqual([i["title"] for i in self.c.get(P, params={"q": "kirik"}).json()["items"]], ["Kırık Film"])

    def test_invisible_titles_are_listed_with_their_reason(self):
        d = self.c.get(P, params={"site": "alpha", "status": "unplayable"}).json()
        got = {i["id"]: i["status"] for i in d["items"]}
        self.assertEqual(got, {self.nosource: "no_source", self.blocked: "not_public", self.broken: "broken",
                               self.off: "disabled"})
        reasons = {i["id"]: i["reason"] for i in d["items"]}
        self.assertIn("telif", reasons[self.blocked])
        self.assertIn("bozuk", reasons[self.broken])
        self.assertEqual(d["counts"]["unplayable"], 4)
        self.assertEqual(self.c.get(P, params={"status": "visible", "site": "alpha"}).json()["total"], 2)


class DetailTests(CatalogCase):
    def test_series_tree_with_origins_and_provenance(self):
        d = self.c.get(f"{P}/{self.series}").json()
        self.assertEqual([s["season"] for s in d["seasons"]], [1, 2])
        ep = d["seasons"][0]["episodes"][0]
        self.assertEqual((ep["episode"], ep["title"], ep["source_count"], ep["health"]["status"]), (1, "Pilot", 1, "visible"))
        src = ep["sources"][0]
        self.assertEqual((src["site"], src["url"], src["status"]), ("alpha", EP_PAGE, "unknown"))
        for key in ("code", "proxied", "proxy_required"):
            self.assertIn(key, src["diagnosis"])
        self.assertEqual(d["records"][0]["origins"][0]["value"], "Yeni Bölümler")
        self.assertEqual(d["field_provenance"].get("title"), "alpha")
        self.assertEqual(self.c.get(f"{P}/nope").status_code, 404)


class ResolveTests(CatalogCase):
    def test_trace_steps_stream_fields_and_no_side_effects(self):
        sid = self.sid(self.series, f"{self.series}:s1:e1")
        before = self.health()
        with mock.patch.object(events, "emit") as emit:
            t = self.c.post(f"{P}/sources/{sid}/resolve", json={"force": True}).json()
        self.assertTrue(t["ok"])
        self.assertEqual([s["step"] for s in t["steps"]], ["page", "discover", "provider", "stream"])
        self.assertEqual(t["steps"][1]["candidates"][0]["host"], "player.vidhost.test")
        self.assertEqual(t["steps"][2]["providers"], ["FakeHost"])
        s = t["streams"][0]
        self.assertEqual((s["type"], s["quality"], s["height"], s["bitrate"]), ("hls", "720p", 720, 2800000))
        self.assertEqual((s["probe"]["http"], s["probe"]["duration_min"], s["probe"]["variants"], s["probe"]["ms"]), (200, 45.0, 2, 12))
        self.assertFalse(s["probe"]["encrypted"])
        self.assertEqual([(x["lang"], x["label"]) for x in t["subtitles"]], [("tr", "Türkçe")])
        self.assertEqual(t["audio"][0]["lang"], "tr")
        self.assertEqual(self.health(), before)
        emit.assert_not_called()
        if db.query_one("SELECT 1 FROM sqlite_master WHERE name='problems'"):
            self.assertEqual(db.query_one("SELECT COUNT(*) n FROM problems")["n"], 0)

    def test_page_without_candidates_stops_at_discover(self):
        sid = self.sid(self.series, f"{self.series}:s1:e2")
        before = self.health()
        t = self.c.post(f"{P}/sources/{sid}/resolve", json={}).json()
        self.assertEqual((t["ok"], t["failed_stage"]), (False, "discover"))
        self.assertEqual([s["ok"] for s in t["steps"]][:2], [True, False])
        self.assertEqual(self.health(), before)


class StreamsTests(CatalogCase):
    def test_admin_streams_leave_no_trace(self):
        with mock.patch.object(events, "emit") as emit:
            d = self.c.get(f"{P}/streams/{self.series}", params={"episode": f"{self.series}:s1:e1"}).json()
        self.assertEqual(len(d["streams"]), 2)
        self.assertNotIn("attempt_token", d["streams"][0])
        emit.assert_not_called()
        self.assertIsNone(db.query_one("SELECT 1 FROM playback_attempts"))
        self.assertIsNone(db.query_one("SELECT 1 FROM progress"))

    def test_proxy_stream_comes_back_signed(self):
        mp4 = [{"url": "https://cdn.vidhost.test/v/1.mp4", "type": "mp4", "quality": "720p"}]
        with mock.patch.object(registry, "CODE_PROVIDERS", [provider(mp4, proxy=True)]):
            d = self.c.get(f"{P}/streams/{self.movie}").json()
        s = d["streams"][0]
        self.assertTrue(s["proxied"])
        self.assertEqual(s["proxy_reason"], "recipe")
        token = re.search(r"/api/stream-proxy/([^/?]+)", s["url"]).group(1)
        self.assertEqual(streamproxy.parse_token(token)[0], "https://cdn.vidhost.test/v/1.mp4")


class ActionTests(CatalogCase):
    def test_disable_enable(self):
        sid = self.sid(self.movie)
        self.assertEqual(self.c.post(f"{P}/sources/{sid}/disable").json()["status"], "disabled")
        self.assertEqual(db.query_one("SELECT status FROM video_sources WHERE id=?", (sid,))["status"], "disabled")
        self.assertEqual(self.c.post(f"{P}/sources/{sid}/enable").json()["status"], "unknown")
        self.assertEqual(self.c.post(f"{P}/sources/{sid}/enable").status_code, 409)

    def test_unlist_hides_from_list_and_clients_relist_brings_back(self):
        self.assertEqual(self.c.post(f"{P}/{self.movie}/unlist").status_code, 200)
        ids = [i["id"] for i in self.c.get(P, params={"site": "alpha"}).json()["items"]]
        self.assertNotIn(self.movie, ids)
        self.assertEqual([i["id"] for i in self.c.get(P, params={"status": "unlisted"}).json()["items"]], [self.movie])
        self.assertNotIn(self.movie, [i["id"] for i in cache.refresh().visible])
        title("alpha", "film/gece", "Gece Treni", year=2024)   # re-import does not bring it back
        self.assertNotIn(self.movie, [i["id"] for i in cache.refresh().visible])
        self.c.post(f"{P}/{self.movie}/relist")
        self.assertIn(self.movie, [i["id"] for i in cache.refresh().visible])

    def test_delete_site_record_and_bulk_keep_user_data(self):
        a = title("gamma", "film/silinecek", "Silinecek")
        b = title("gamma", "film/toplu", "Toplu")
        db.execute("INSERT INTO progress(profile_id,item_id,episode_id,position,duration,updated_at) VALUES ('p1',?,?,10,100,1)", (a, a))
        self.assertEqual(self.c.delete(f"{P}/{a}", params={"site": "nope"}).status_code, 404)
        self.assertTrue(self.c.delete(f"{P}/{a}", params={"site": "gamma"}).json()["title_gone"])
        self.assertIsNotNone(db.query_one("SELECT 1 FROM progress WHERE item_id=?", (a,)))
        self.assertEqual(self.c.post(f"{P}/bulk", json={"ids": [b], "action": "delete"}).json()["done"], 1)
        self.assertIsNone(db.query_one("SELECT 1 FROM library_items WHERE id=?", (b,)))

    def test_add_source_youtube_makes_title_visible(self):
        cid = title("delta", "film/yeni", "Yeni Film")
        self.assertEqual(self.c.post(f"{P}/{cid}/sources", json={"url": "http://127.0.0.1/x.mp4"}).status_code, 400)
        with mock.patch.object(streamprobe, "probe", lambda *a, **k: {"ok": False, "http": 404}):
            self.assertEqual(self.c.post(f"{P}/{cid}/sources", json={"url": "https://media.test/x.mp4"}).status_code, 422)
        r = self.c.post(f"{P}/{cid}/sources", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()
        self.assertEqual((r["resolver"], r["media_type"]), ("embed", "embed"))
        row = db.query_one("SELECT * FROM video_sources WHERE id=?", (r["source_id"],))
        self.assertEqual((row["source"], row["status"], row["locator"]),
                         ("admin", "unknown", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"))
        self.assertIn(cid, [i["id"] for i in cache.refresh().visible])
        self.assertEqual(self.c.post(f"{P}/{self.series}/sources", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).status_code, 400)
        ep = f"{self.series}:s2:e1"
        r = self.c.post(f"{P}/{self.series}/sources", json={"url": "https://youtu.be/dQw4w9WgXcQ", "episode": ep}).json()
        self.assertEqual(db.query_one("SELECT episode_id, season, episode FROM video_sources WHERE id=?", (r["source_id"],))[0], ep)


    def test_add_source_known_provider_is_resolved_on_every_play(self):
        calls = []

        def res(url, referer=""):
            calls.append(url)
            return {"provider": "SignHost", "cache_ttl": 7200,
                    "streams": [{"url": f"https://cdn.signhost.test/v.mp4?sig={len(calls)}", "type": "mp4", "quality": "720p"}]}
        signed = registry.Provider("signhost", lambda u: "player.signhost.test" in u, res, "test", ("player.signhost.test",))
        cid = title("delta", "film/imzali", "İmzalı Film")
        link = "https://player.signhost.test/e/abc"
        with mock.patch.object(registry, "CODE_PROVIDERS", [signed]):
            r = self.c.post(f"{P}/{cid}/sources", json={"url": link}).json()
            self.assertEqual((r["resolver"], r["expires"]), ("provider", False))
            row = db.query_one("SELECT * FROM video_sources WHERE id=?", (r["source_id"],))
            self.assertEqual((row["locator"], row["resolver"]), (link, "provider"))
            first = sources.streams(cid)["streams"]
            self.assertIn("sig=2", first[0]["url"])            # 1 = the add check, 2 = the first play
            self.assertIn("sig=2", sources.streams(cid)["streams"][0]["url"])   # still valid: cached, no new resolve
            self.assertEqual(len(calls), 2)
            db.execute("UPDATE video_sources SET resolved_at=resolved_at-100000 WHERE id=?", (r["source_id"],))   # expired
            self.assertIn("sig=3", sources.streams(cid)["streams"][0]["url"])
            trace = self.c.post(f"{P}/sources/{r['source_id']}/resolve", json={"force": True}).json()
            self.assertTrue(trace["ok"], trace)
            self.assertIn("sig=4", trace["streams"][0]["url"])

    def test_report_opens_an_admin_problem(self):
        if routes_catalog._problems() is None:
            self.skipTest("T13 problem store not installed")
        sid = self.sid(self.movie)
        r = self.c.post(f"{P}/sources/{sid}/report", json={})
        self.assertEqual(r.status_code, 200)
        ev = db.query_one("SELECT actor, kind FROM problem_events WHERE problem_id=? ORDER BY id DESC", (r.json()["problem_id"],))
        self.assertEqual(tuple(ev), ("admin", "admin_report"))
        self.assertTrue(self.c.get(f"{P}/{self.movie}").json()["problems_available"])


    def test_report_reopens_waiting_and_not_public_problems(self):
        if routes_catalog._problems() is None:
            self.skipTest("T13 problem store not installed")
        from app.control.jobs import store as jobs_store
        from app.control.problems import run, store
        sid = self.sid(self.movie)
        pid = self.c.post(f"{P}/sources/{sid}/report", json={}).json()["problem_id"]
        for stuck in (("diagnosing", "waiting_user"), ("diagnosing", "closed_not_public")):
            db.execute("DELETE FROM jobs")
            db.execute("UPDATE problems SET state='open', deadline_at=NULL WHERE id=?", (pid,))
            for st in stuck:
                store.transition(pid, st, actor="system", kind="test")
            r = self.c.post(f"{P}/sources/{sid}/report", json={}).json()
            self.assertEqual(r["state"], "open", stuck)
            self.assertGreater(r["deadline_at"] or 0, store.now())
            self.assertEqual(store.row(pid)["state"], "open")
            self.assertEqual(jobs_store.get_by_key(run.job_key(pid))["state"], "queued")


class StaticTests(unittest.TestCase):
    def test_youtube_provider_is_generic_and_opt_in(self):
        self.assertEqual(youtube.video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube.video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertFalse(youtube.matches("https://www.youtube.com/channel/x"))
        self.assertNotIn("youtube", [p.name for p in registry.providers()])

    def test_vendor_served_locally_and_no_foreign_urls(self):
        with TestClient(main.app) as c:
            r = c.get("/admin/vendor/hls.min.js")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Hls", r.text[:5000] + r.text[-5000:])
            self.assertEqual(c.get("/admin/vendor/hls.js.LICENSE").status_code, 200)
            self.assertEqual(c.get("/admin/js/catalog.js").status_code, 200)
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            self.assertFalse(re.search(r"""(?:src|href)\s*=\s*["']?https?://""", fh.read(), re.I))
        for name in ("catalog.js", "catalog_player.js"):
            with open(os.path.join(STATIC, "js", name), encoding="utf-8") as fh:
                self.assertNotRegex(fh.read(), r"https?://", name)
        with open(os.path.join(STATIC, "js", "catalog_player.js"), encoding="utf-8") as fh:
            self.assertIn("setAttribute('sandbox'", fh.read())


if __name__ == "__main__":
    unittest.main()
