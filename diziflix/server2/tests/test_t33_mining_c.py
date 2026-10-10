import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import config, db
from app.control.problems import store, system
from app.control.sitemap import store as map_store
from app.core import events
from app.core.catalog import cache, seasons, write
from app.core.fetch import netguard
from app.core.fetch.base import FetchError
from app.core.fetch.playback import ProbeReply
from app.core.play import trailer_check

YT = "https://www.youtube.com/embed/abcdefghijk"


def reply(code):
    return lambda url, **kw: ProbeReply(code, {}, b"", url)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        cache.reset()
        events.clear()
        with mock.patch.object(netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")]):
            self.sid = map_store.create_site("https://fakesite.test/")
        map_store.set_status(self.sid, "active")
        p = mock.patch.object(trailer_check, "after_change", None)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def series(self, n, episodes=()):
        url = f"https://fakesite.test/dizi/d{n}/"
        write.upsert_title(self.sid, {"key": f"dizi/d{n}", "type": "series", "title": f"Dizi {n}", "source_url": url})
        if episodes:
            write.upsert_episodes(self.sid, f"dizi/d{n}", [{"season": s, "episode": e, "url": f"{url}{s}-{e}/"}
                                                           for s, e in episodes])
        return db.query_one("SELECT canonical_id FROM source_items WHERE site_id=? AND key=?", (self.sid, f"dizi/d{n}"))[0]


class TrailerCheckTests(Case):
    def test_youtube_id(self):
        self.assertEqual(trailer_check.youtube_id(YT), "abcdefghijk")
        self.assertEqual(trailer_check.youtube_id("https://youtu.be/abcdefghijk?t=3"), "abcdefghijk")
        self.assertEqual(trailer_check.youtube_id("https://www.youtube.com/watch?v=abcdefghijk"), "abcdefghijk")
        self.assertIsNone(trailer_check.youtube_id("https://player.vidhost.test/embed/abcdefghijk"))

    def test_probe_verdicts(self):
        for code, want in ((200, "ok"), (404, "dead"), (403, "dead"), (401, "dead"), (500, "unknown"), (429, "unknown")):
            with mock.patch.object(trailer_check.playback, "probe_get", reply(code)):
                self.assertEqual(trailer_check.probe("abcdefghijk")[0], want, code)

        def boom(url, **kw):
            raise FetchError("timeout", "x", url=url)
        with mock.patch.object(trailer_check.playback, "probe_get", boom):
            self.assertEqual(trailer_check.probe("abcdefghijk"), ("unknown", "timeout"))

    def test_apply_detail_dead_trailer_is_cached_and_mirrored(self):
        cid = self.series(1)
        now = 1_900_000_000
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,updated_at) "
                   "VALUES ('tr1',?,?,'dizi/d1','trailer',?,?)", (cid, self.sid, YT, now))
        calls = []

        def dead(url, **kw):
            calls.append(url)
            return ProbeReply(404, {}, b"", url)
        with mock.patch.object(trailer_check.playback, "probe_get", dead):
            data = {"id": cid, "type": "series", "availability": {"state": "unavailable", "has_trailer": True},
                    "playback": "trailer"}
            trailer_check.apply_detail(data, budget=2.0)
            self.assertEqual((data["availability"]["has_trailer"], data["availability"]["trailer"], data["playback"]),
                             (False, "dead", "unavailable"))
            self.assertEqual(db.query_one("SELECT trailer_dead FROM video_sources WHERE id='tr1'")[0], 1)
            again = {"id": cid, "type": "series", "availability": {"has_trailer": True}, "playback": "trailer"}
            trailer_check.apply_detail(again, budget=2.0)          # from the cache: no second probe
        self.assertEqual(len(calls), 1)
        self.assertFalse(again["availability"]["has_trailer"])
        self.assertIn("watch%3Fv%3Dabcdefghijk", calls[0])


class CoverageTests(Case):
    def test_episode_gap_counts_aired_tmdb_episodes_of_answered_seasons(self):
        cid = self.series(1, [(1, 1), (1, 2)])
        db.execute("INSERT INTO library_seasons(canonical_id,season,status,checked_at) VALUES (?,1,'ok',1)", (cid,))
        db.execute("INSERT INTO library_seasons(canonical_id,season,status,checked_at) VALUES (?,2,'ok',1)", (cid,))
        for e, air in ((1, "2020-01-01"), (2, None), (3, "2020-01-15"), (4, "2020-01-22"), (5, "2999-01-01")):
            db.execute("INSERT INTO library_episodes(canonical_id,season,episode,air_date) VALUES (?,1,?,?)", (cid, e, air))
        db.execute("INSERT INTO library_episodes(canonical_id,season,episode) VALUES (?,2,1)", (cid,))  # season 2 not in library
        with db.read_transaction() as conn:
            self.assertEqual(seasons.episode_gap(conn, cid, self.sid, today="2026-10-04"),
                             {"seasons": 1, "expected": 4, "missing": 2})
            self.assertEqual(seasons.episode_gap(conn, cid, "other-site", today="2026-10-04")["expected"], 0)

    def test_scan_coverage_opens_episodes_problem_and_stays_quiet_when_covered(self):
        for n in range(6):
            self.series(n, [(1, 1), (1, 2)] if n < 4 else ())
        out = system.scan_coverage(self.sid)
        self.assertEqual((out["series"], out["no_episodes"], out.get("episodes_problem")), (6, 2, None))   # 2/6: fine
        for n in range(6, 10):
            self.series(n)
        out = system.scan_coverage(self.sid)                         # 6/10 without episodes
        p = out["episodes_problem"]
        self.assertEqual((p["class"], p["page_type"], p["scope"]), ("missing_field", "episodes", "site"))
        self.assertEqual(p["evidence"]["field"], "episodes")
        self.assertIsNone(out.get("player_problem"))
        db.execute("UPDATE video_sources SET status='broken' WHERE source=?", (self.sid,))
        db.execute("DELETE FROM problems")
        for n in range(4, 10):
            self.series(n, [(1, 1)])
        db.execute("UPDATE video_sources SET status='broken' WHERE source=?", (self.sid,))
        out = system.scan_coverage(self.sid)                         # every series: episodes, no usable source
        self.assertEqual((out["no_episodes"], out["no_sources"]), (0, 10))
        self.assertEqual([x["page_type"] for x in store.list()], ["player"])


if __name__ == "__main__":
    unittest.main()
