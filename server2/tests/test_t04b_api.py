import _sandbox  # noqa: F401  (must be first)

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main
from app.core import events
from app.core.catalog import cache, lists, notify, write
from app.core.play import sources, streamproxy

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "api-samples")
API_DIR = os.path.join(os.path.dirname(db.MIGRATIONS_DIR), "api")


def sample(name):
    with open(os.path.join(SAMPLES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def shape(ref, got, path="$"):
    """Every field of the sample ``ref`` is present in ``got`` with the same JSON type (null in either = unknown, accepted;
    int/float interchangeable). Lists of objects: every sample variant (same ``card_kind``/``kind``) must be present in the response
and is compared with its element of the same variant; rows by ``id`` (else the first row)."""
    if ref is None or got is None:
        return []
    if isinstance(ref, dict):
        if not isinstance(got, dict):
            return [f"{path}: object expected, got {type(got).__name__}"]
        out = []
        for key, value in ref.items():
            if key not in got:
                out.append(f"{path}.{key}: missing")
            else:
                out += shape(value, got[key], f"{path}.{key}")
        return out
    if isinstance(ref, list):
        if not isinstance(got, list):
            return [f"{path}: list expected"]
        if not ref or not got:
            return []
        if not isinstance(ref[0], dict):
            return shape(ref[0], got[0], path + "[0]")
        out, done = [], set()
        for n, item in enumerate(ref):   # rows by id; cards/actions by card_kind/kind; each variant once
            tag = next((t for t in ("card_kind", "kind") if t in item), None)
            mark = (tag, item.get(tag)) if tag else ("id", item.get("id"))
            if mark in done:
                continue
            done.add(mark)
            same = [g for g in got if isinstance(g, dict) and g.get(mark[0]) == mark[1]]
            if not same and tag is None:
                same = got[:1]
            if not same:   # every card/action variant of the sample must be exercised by the response
                out.append(f"{path}: no {tag}={item.get(tag)!r} element in the response")
                continue
            out += shape(item, same[0], f"{path}[{n}]")
        return out
    if isinstance(ref, bool) or isinstance(got, bool):
        return [] if isinstance(ref, bool) and isinstance(got, bool) else [f"{path}: bool mismatch"]
    if isinstance(ref, (int, float)):
        return [] if isinstance(got, (int, float)) else [f"{path}: number expected"]
    return [] if isinstance(got, type(ref)) else [f"{path}: {type(ref).__name__} expected, got {type(got).__name__}"]


def title(site, key, name, kind="movie", **kw):
    return write.upsert_title(site, {"key": key, "type": kind, "title": name, "source_url": f"https://{site}.test/{key}",
                                     **kw})


class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        cls._old = config.DB_PATH
        config.DB_PATH = os.path.join(cls.tmp, "api.db")
        cache.reset()
        events.clear()
        cls._client = TestClient(main.app)
        cls.c = cls._client.__enter__()
        now = int(time.time())
        for sid, name in (("alpha", "Alfa Sitesi"), ("beta", "Beta Sitesi")):
            db.execute("INSERT INTO sites(site_id,display_name,base_url,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (sid, name, f"https://{sid}.test", "active", now, now))
        cls.series = title("alpha", "dizi/sinyal", "Kayıp Sinyal", "series", year=2021, genres=["Dram"], rating=8.3,
                           overview="Bir sinyal.", backdrop_url="https://alpha.test/b.jpg")
        write.upsert_episodes("alpha", "dizi/sinyal", [
            {"season": 1, "episode": 1, "url": "https://alpha.test/sinyal-1-1", "title": "Pilot"},
            {"season": 1, "episode": 2, "url": "https://alpha.test/sinyal-1-2"}])
        cls.movie = title("alpha", "film/gece", "Gece Treni", year=2024, genres=["Dram"], rating=7.4, overview="Tren.")
        write.set_movie_source("alpha", "film/gece", "https://alpha.test/gece")
        cls.nosource = title("alpha", "film/defter", "Eski Defter", year=1999, genres=["Dram"], overview="Yok.")
        # contract variants: trailers (play_trailer, streams-trailer), a trailer-only series without episodes,
        # a "latest episodes" list (episode cards in boot / new_episodes) and a movie + series for "kayıp" (search kinds)
        cls.coming = title("alpha", "dizi/umut", "Yeni Umut", "series", year=2026, genres=["Dram"])
        for cid in (cls.series, cls.movie, cls.coming):
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                       "media_type,label,updated_at) VALUES (?,?,?,?,'','trailer',?,'direct','embed','Fragman',?)",
                       (f"tr-{cid}", cid, "alpha", cid, "https://www.youtube.com/embed/aaaaaaaaaaa", now))
        cls.shore = title("alpha", "film/sahil", "Kayıp Sahil", year=2023, genres=["Macera"])
        write.set_movie_source("alpha", "film/sahil", "https://alpha.test/sahil")
        lists.set_list("latest_episodes_alpha", [cls.series])
        cls.broken = title("beta", "film/kirik", "Kırık Film", year=2020, genres=["Dram"])
        write.set_movie_source("beta", "film/kirik", "https://beta.test/kirik")
        db.execute("UPDATE video_sources SET status='broken' WHERE canonical_id=?", (cls.broken,))
        # trend fill: alpha's titles all score higher than beta's (rating 7 < classic bar 7.5)
        for site, rating in (("alpha", 7.0), ("beta", 6.0)):
            for n in range(12):
                key = f"film/{site}-{n}"
                title(site, key, f"{site.title()} Trend {n}", year=2000 + n, rating=rating)
                write.set_movie_source(site, key, f"https://{site}.test/{key}")
        cache.refresh()

    @classmethod
    def tearDownClass(cls):
        cls._client.__exit__(None, None, None)
        cache.reset()
        events.clear()
        db.close_thread()
        config.DB_PATH = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, status=200):
        r = self.c.get(path)
        self.assertEqual(r.status_code, status, f"{path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def conforms(self, name, body):
        self.assertEqual(shape(sample(name), body), [], name)


def fake_resolve(row, force=False):
    return {"streams": [{"url": f"https://cdn.test/{row['id']}/master.m3u8", "type": "hls", "quality": "auto"}],
            "subtitles": [], "duration": 0}


class ContractTests(ApiCase):
    def test_reads_match_the_samples(self):
        self.c.post("/api/progress", json={"profile": "p1", "item_id": self.series, "episode_id": f"{self.series}:s1:e1",
                                           "position": 600, "duration": 2700})
        self.c.post("/api/progress", json={"profile": "p1", "item_id": self.movie, "position": 300, "duration": 6000})
        self.assertEqual(self.c.post("/api/mylist", json={"profile": "p1", "item_id": self.movie}).status_code, 201)
        notify.add("p1", "source_found", self.movie, "", {"title": "Gece Treni", "site": "alpha", "method": "retry"})
        boot = self.get("/api/boot?profile=p1&layout=tv-v1")
        self.conforms("boot", boot)
        self.conforms("row-new_episodes", self.get("/api/row/new_episodes?profile=p1"))
        self.conforms("detail-movie", self.get(f"/api/detail/{self.movie}?profile=p1"))
        self.conforms("detail-series", self.get(f"/api/detail/{self.series}?profile=p1"))
        self.conforms("detail-movie-no-source", self.get(f"/api/detail/{self.nosource}?profile=p1"))
        self.conforms("detail-series-no-episodes", self.get(f"/api/detail/{self.coming}?profile=p1"))
        self.conforms("mylist", self.get("/api/mylist?profile=p1"))
        self.conforms("search", self.get("/api/search?q=kayip&profile=p1"))
        self.conforms("catalog", self.get("/api/catalog?profile=p1"))
        self.conforms("notifications", self.get("/api/notifications?profile=p1"))
        self.conforms("source-finder", self.get(f"/api/source-finder/{self.movie}"))
        self.conforms("profiles", self.get("/api/profiles"))
        self.conforms("avatars", self.get("/api/avatars"))
        self.assertEqual(self.get(f"/api/detail/{self.series}?profile=p1")["source_names"], [{"id": "alpha", "name": "Alfa Sitesi"}])
        with mock.patch.object(sources, "resolve_source", fake_resolve):
            movie = self.get(f"/api/streams/{self.movie}?profile=p1")
            self.conforms("streams-movie", movie)
            self.conforms("streams-episode", self.get(f"/api/streams/{self.series}?episode={self.series}:s1:e1"))
            self.conforms("streams-trailer", self.get(f"/api/streams/{self.coming}?kind=trailer"))
        self.assertEqual(movie["resume_position"], 300)

    def test_error_envelope_and_dev_helpers(self):
        body = self.get("/api/detail/nope", 404)
        self.assertEqual(body, {"error": {"code": "not_found", "message": "item not found"}})
        self.conforms("error-not-found", body)
        self.assertEqual(self.get("/api/streams/nope", 404)["error"]["code"], "not_found")
        self.assertEqual(self.get("/api/row/no_such_row?profile=p1", 404)["error"]["code"], "not_found")
        self.assertEqual(self.get("/api/boot?profile=p1&fail=1", 500)["error"]["code"], "forced_failure")
        self.assertEqual(self.get("/api/boot", 400)["error"]["code"], "bad_request")

    def test_no_old_management_names_in_api(self):
        bad = re.compile(r"sourcefinder|playheal|playissues|heal_agent|onboard|search_all|catalog_view\.select")
        for name in os.listdir(API_DIR):
            if name.endswith(".py"):
                with open(os.path.join(API_DIR, name), encoding="utf-8") as fh:
                    self.assertIsNone(bad.search(fh.read()), name)


class VisibilityTests(ApiCase):
    def test_home_shows_unknown_sources_hides_sourceless_and_broken(self):
        boot = self.get("/api/boot?profile=p2&layout=tv-v1")
        shown = {i["id"] for r in boot["rows"] for i in r["items"]} | {h["id"] for h in boot["heroes"]}
        self.assertIn(self.movie, shown)          # its only source is 'unknown'
        self.assertIn(self.series, shown)
        self.assertNotIn(self.nosource, shown)    # no source at all
        self.assertNotIn(self.broken, shown)      # only a broken source
        movies = {i["id"] for i in self.get("/api/row/movies?profile=p2&limit=100")["items"]}
        self.assertIn(self.movie, movies)
        self.assertFalse({self.nosource, self.broken} & movies)
        self.assertFalse({self.nosource, self.broken} & {i["id"] for i in self.get("/api/catalog?profile=p2&limit=50")["items"]})

    def test_continue_and_mylist_lists_hide_invisible_titles(self):
        self.addCleanup(lambda: [db.execute(f"DELETE FROM {t} WHERE profile_id='p2'") for t in ("mylist", "progress")])
        for cid in (self.movie, self.nosource, self.broken):
            self.assertEqual(self.c.post("/api/mylist", json={"profile": "p2", "item_id": cid}).status_code, 201)
            self.c.post("/api/progress", json={"profile": "p2", "item_id": cid, "position": 300, "duration": 6000})
        self.assertEqual([i["id"] for i in self.get("/api/mylist?profile=p2")["items"]], [self.movie])
        self.assertEqual([i["id"] for i in self.get("/api/row/mylist?profile=p2")["items"]], [self.movie])
        self.assertEqual([i["id"] for i in self.get("/api/row/continue?profile=p2")["items"]], [self.movie])
        self.assertEqual(self.get(f"/api/detail/{self.broken}?profile=p2")["id"], self.broken)   # deep link answers

    def test_unplayable_episodes_and_empty_seasons_leave_the_client_detail(self):
        sid = title("alpha", "dizi/kapali", "Kapalı Kapı", "series", year=2022)
        urls = {(1, 1): "https://alpha.test/kapali-1-1", (1, 2): "https://alpha.test/kapali-1-2",
                (2, 1): "https://alpha.test/kapali-2-1"}
        write.upsert_episodes("alpha", "dizi/kapali", [{"season": s, "episode": e, "url": u} for (s, e), u in urls.items()])
        self.addCleanup(lambda: (write.remove_title("alpha", "dizi/kapali"), cache.refresh()))
        for key in ((1, 2), (2, 1)):
            write.mark_blocked("alpha", urls[key], kind="episode", reason="telif", via="rule")
        cache.refresh()
        seasons = self.get(f"/api/detail/{sid}?profile=p2")["seasons"]
        self.assertEqual([(s["season"], [e["episode"] for e in s["episodes"]], s["episode_count"]) for s in seasons],
                         [(1, [1], 1)])
        # the admin snapshot still has everything
        self.assertEqual(sum(len(s["episodes"]) for s in cache.get().by_id[sid]["seasons"]), 3)

    def test_trending_fill_gives_every_site_a_share(self):
        first = self.get("/api/row/trending_movies?profile=p2&limit=10")["items"]
        per_site = [i["sources"][0] for i in first]
        self.assertEqual(len(first), 10)
        self.assertEqual((per_site.count("alpha"), per_site.count("beta")), (5, 5), [i["title"] for i in first])
        alpha = [i["title"] for i in first if i["sources"] == ["alpha"]]
        self.assertEqual(alpha[0], "Gece Treni", "inside a site trend_score orders (7.4 beats 7.0)")


class PlaybackTests(ApiCase):
    def setUp(self):
        self.got = []
        for name in ("playback_ok", "playback_failed", "progress", "stream_missing"):
            events.subscribe(name, lambda _n=name, **p: self.got.append((_n, p)))

    def tearDown(self):
        events.clear()

    def test_report_emits_events_and_bad_token_is_400(self):
        with mock.patch.object(sources, "resolve_source", fake_resolve):
            stream = self.get(f"/api/streams/{self.series}?episode={self.series}:s1:e2&profile=p2")["streams"][0]
        token = stream["attempt_token"]
        r = self.c.post("/api/playback-report?profile=p2", json={"attempt_token": token, "event": "failure", "code": "network",
                                                                  "engine": "html5"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post("/api/playback-report?profile=p2", json={"attempt_token": token, "event": "success", "engine": "html5"})
        self.assertEqual(r.status_code, 200, r.text)
        kinds = [k for k, _ in self.got]
        self.assertEqual(kinds, ["playback_failed", "playback_ok"])
        failed = self.got[0][1]
        self.assertEqual((failed["canonical_id"], failed["episode_id"], failed["source_id"], failed["code"], failed["profile_id"]),
                         (self.series, f"{self.series}:s1:e2", stream["source_id"], "network", "p2"))
        bad = self.c.post("/api/playback-report", json={"attempt_token": "0" * 32, "event": "success"})
        self.assertEqual(bad.status_code, 400)
        db.execute("UPDATE playback_attempts SET created_at=? WHERE token=?", (int(time.time()) - 2 * 86400, token))
        expired = self.c.post("/api/playback-report", json={"attempt_token": token, "event": "failure", "code": "network"})
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(len(self.got), 2, "a refused report emits nothing")

    def test_progress_event_carries_the_position(self):
        r = self.c.post("/api/progress", json={"profile": "p2", "item_id": self.series, "episode_id": f"{self.series}:s1:e1",
                                               "position": 120, "duration": 2400})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(("progress", {"profile_id": "p2", "canonical_id": self.series, "episode_id": f"{self.series}:s1:e1",
                                    "position": 120, "duration": 2400}), self.got)

    def test_empty_streams_carry_a_finder_state(self):
        with mock.patch.object(sources, "resolve_source", side_effect=RuntimeError("no player")):
            body = self.get(f"/api/streams/{self.movie}?profile=p2")
        self.assertEqual(body["streams"], [])
        self.assertEqual(body["finder"], {"state": "searching"})   # T13: stream_missing opened a problem
        self.assertEqual([k for k, _ in self.got], ["stream_missing"])


class StreamProxyTests(ApiCase):
    def test_token_to_an_internal_address_is_refused(self):
        for url in ("http://127.0.0.1:8091/api/health", "http://10.0.0.5/v.mp4", "http://[::1]/v.mp4"):
            token = streamproxy.make_token(url, {})
            r = self.c.get(f"/api/stream-proxy/{token}")
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "forbidden_target"), url)
            hls = streamproxy.make_token(url, {}, kind=streamproxy.PLAYLIST)
            r = self.c.get(f"/api/stream-proxy/{hls}/index.m3u8")
            self.assertEqual(r.status_code, 403, url)

    def test_redirect_to_an_internal_address_is_refused(self):
        import contextlib
        import ipaddress
        from app.core.fetch import netguard, playback
        real = netguard._addresses
        fake_dns = lambda host, port: [ipaddress.ip_address("93.184.216.34")] if host.endswith(".test") else real(host, port)
        hops = []

        class Resp:
            status_code, headers = 302, {"location": "http://10.1.2.3/v.mp4"}

        @contextlib.contextmanager
        def stream(method, pinned, **kw):
            hops.append(pinned[0])
            yield Resp()
        token = streamproxy.make_token("https://cdn.test/v.mp4", {})
        with mock.patch.object(netguard, "_addresses", fake_dns), mock.patch.object(playback, "_stream", stream):
            r = self.c.get(f"/api/stream-proxy/{token}")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "forbidden_target"))
        self.assertEqual(hops, ["https://cdn.test/v.mp4"])   # the 10.x hop was never opened

    def test_bad_and_expired_tokens(self):
        self.assertEqual(self.c.get("/api/stream-proxy/abc.def").status_code, 403)
        old = streamproxy.make_token("https://cdn.test/v.mp4", {}, now=time.time() - 10 * 86400)
        self.assertEqual(self.c.get(f"/api/stream-proxy/{old}").status_code, 410)

    def test_playlist_is_rewritten_through_the_guarded_transport(self):
        class Reply:
            status, headers, url = 200, {"content-type": "application/vnd.apple.mpegurl"}, "https://cdn.test/hls/master.m3u8"

            def read(self, cap):
                return b"#EXTM3U\n#EXTINF:4,\nseg-1.ts\n#EXTINF:4,\nhttp://10.0.0.9/seg-2.ts\n"

            def close(self):
                pass
        token = streamproxy.make_token("https://cdn.test/hls/master.m3u8", {}, kind=streamproxy.PLAYLIST)
        from app.core.fetch import playback
        with mock.patch.object(playback, "open_stream", return_value=Reply()) as opened:
            r = self.c.get(f"/api/stream-proxy/{token}/index.m3u8")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(opened.call_args[0][1], "https://cdn.test/hls/master.m3u8")
        lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("/api/stream-proxy/" in ln for ln in lines))
        # the internal segment got a token, but serving it hits netguard again
        seg = lines[1].split("/api/stream-proxy/", 1)[1].split("/", 1)[0]
        self.assertEqual(self.c.get(f"/api/stream-proxy/{seg}").status_code, 403)


if __name__ == "__main__":
    unittest.main()
