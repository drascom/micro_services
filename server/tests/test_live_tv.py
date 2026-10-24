"""Canlı TV (library/live.py): famelack gzip lists -> language filter, upsert, deactivation, rows, streams, playback report.
No network: the HTTP client is a fake that serves gzipped fixture bodies (and 304 on a matching ETag)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402
import gzip
import json
import unittest

from app import db
from app.library import live

CFG = live.load_config("/nonexistent")
CFG.update(countries=["tr"], languages=["tur"], categories=["news", "kids"])
CFG["urls"] = {"country": "https://x/countries/{cc}.json", "category": "https://x/categories/{cat}.json"}


def ch(nid, name, langs=("tur",), streams=(), youtube=(), country="tr", geo=False):
    src = {"streams": list(streams)}
    if youtube:
        src["youtube"] = list(youtube)
    return {"nanoid": nid, "name": name, "sources": src, "languages": list(langs), "country": country, "isGeoBlocked": geo}


class Resp:
    def __init__(self, status, body=b"", etag=None):
        self.status_code, self.content, self.headers = status, body, {"etag": etag} if etag else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, headers=None):
        self.calls.append(url)
        body = gzip.compress(json.dumps(self.files.get(url, [])).encode())
        etag = '"%x"' % hash(body)
        if (headers or {}).get("If-None-Match") == etag:
            return Resp(304)
        return Resp(200, body, etag)

    def close(self):
        pass


def files(country_list, news=(), kids=()):
    return {"https://x/countries/tr.json": country_list, "https://x/categories/news.json": list(news),
            "https://x/categories/kids.json": list(kids)}


class LiveTvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def setUp(self):
        db.execute("DELETE FROM channels")
        try:
            _os.remove(live._state_path())
        except OSError:
            pass
        live.invalidate()

    def ingest(self, data, **kw):
        return live.run(client=FakeClient(data), cfg=CFG, probe=False, **kw)

    def test_filter_upsert_deactivate_and_etag(self):
        a = ch("A", "Zeta Haber", streams=["https://h1/a.m3u8"], youtube=["https://www.youtube-nocookie.com/embed/x"])
        b = ch("B", "alfa TV", streams=["https://h2/b.m3u8"])
        y = ch("Y", "Yalnız YT", youtube=["https://www.youtube-nocookie.com/embed/y"])
        f = ch("F", "Farsça", langs=("fas",), streams=["https://h3/f.m3u8"])
        n = ch("N", "Boş", streams=[])
        other = ch("O", "Other", country="de", streams=["https://h4/o.m3u8"])
        data = files([a, b, y, f, n], news=[a, other], kids=[b])
        s = self.ingest(data)
        self.assertEqual((s["total"], s["kept"], s["youtube_only"]), (5, 3, 1))
        self.assertEqual(s["upsert"]["new"], 3)
        vis = live.visible_channels(fresh=True)
        self.assertEqual([c["title"] for c in vis], ["alfa TV", "Yalnız YT", "Zeta Haber"], "by name, case-folded")
        self.assertEqual([c["channel_no"] for c in vis], [1, 2, 3])
        self.assertEqual(live.by_id("ch_A")["categories"], ["news"])
        self.assertEqual(live.row_ids(), ["live_all", "live_news", "live_kids"])
        # unchanged source: 304 everywhere, no upsert (quick exit)
        s = self.ingest(data)
        self.assertFalse(s["changed"])
        self.assertNotIn("upsert", s)
        # B disappears -> inactive, out of the rows
        s = self.ingest(files([a, y], news=[a]))
        self.assertEqual(s["upsert"]["deactivated"], 1)
        self.assertEqual([c["id"] for c in live.visible_channels(fresh=True)], ["ch_Y", "ch_A"])
        self.assertIsNone(live.by_id("ch_B"))

    def test_scan_events_in_ops_feed(self):
        # events are picked by a per-run unique trigger: another writer of the shared feed (a scheduler tick, an
        # earlier test) can never shift the order this test reads
        import uuid
        from app.scraper import state as sstate
        t1, t2 = "t-" + uuid.uuid4().hex, "t-" + uuid.uuid4().hex
        data = files([ch("A", "Haber", streams=["https://h1/a.m3u8"])])
        s1 = self.ingest(data, trigger=t1)
        s2 = self.ingest(data, trigger=t2)   # unchanged: 304 everywhere -> "no change" event
        self.assertTrue(s1["changed"])
        self.assertFalse(s2["changed"], s2)
        evs = {e.get("trigger"): e for e in sstate.list_ops("live", "famelack", 1000)}
        self.assertEqual((evs[t1]["status"], evs[t1]["new"], evs[t1]["site"]), ("success", 1, "famelack"))
        self.assertEqual(evs[t2]["status"], "no_change", s2)

    def test_dead_channel_hidden_rows_and_streams_shape(self):
        from fastapi.testclient import TestClient
        from app.main import app
        a = ch("A", "Haber 1", streams=["https://h1/a.m3u8", "https://h1/b.m3u8"], youtube=["https://www.youtube-nocookie.com/embed/x"])
        d = ch("D", "Ölü", streams=["https://dead/d.m3u8"])
        self.ingest(files([a, d], news=[a, d]))
        live.apply_probe({"https://h1/a.m3u8": {"probe": "fail", "cors": False, "error": "HTTP 404"},
                          "https://h1/b.m3u8": {"probe": "ok", "cors": True, "error": None},
                          "https://dead/d.m3u8": {"probe": "fail", "cors": False, "error": "x"}})
        self.assertEqual([c["id"] for c in live.visible_channels()], ["ch_A"], "no stream + no YouTube = hidden")
        with TestClient(app) as c:
            secs = {x["id"]: x for x in c.get("/api/sections?profile=p1").json()["sections"]}
            self.assertEqual(secs["live_tv"]["status"], "ready")
            self.assertEqual(secs["dvr"]["status"], "soon")
            sec = c.get("/api/section/live_tv?profile=p1").json()
            self.assertEqual([r["id"] for r in sec["rows"]], ["live_all", "live_news"])
            card = sec["rows"][0]["items"][0]
            self.assertEqual((card["id"], card["type"], card["card_kind"], card["channel_no"], card["has_youtube"]),
                             ("ch_A", "channel", "channel", 1, True))
            self.assertEqual(sec["heroes"][0]["id"], "ch_A")
            page = c.get("/api/row/live_news?profile=p1&offset=0&limit=10").json()
            self.assertEqual((page["title"], page["total"]), ("Haber", 1))
            st = c.get("/api/streams/ch_A?profile=p1").json()
            self.assertTrue(st["live"])
            self.assertEqual(st["channel"], {"id": "ch_A", "title": "Haber 1", "channel_no": 1})
            self.assertEqual((st["resume_position"], st["duration"]), (0, None))
            self.assertEqual([(s["type"], s["url"].split("?")[0]) for s in st["streams"]],
                             [("hls", "https://h1/b.m3u8"), ("embed", "https://www.youtube-nocookie.com/embed/x"),
                              ("hls", "https://h1/a.m3u8")], "usable m3u8, YouTube, failing m3u8 last")
            self.assertTrue(st["streams"][1]["url"].endswith("?autoplay=1"))
            self.assertEqual(st["streams"][0]["label"], "h1 · canlı")
            self.assertTrue(all(len(s["attempt_token"]) == 32 and s["proxied"] is False for s in st["streams"]))
            self.assertEqual(c.get("/api/streams/ch_nope").status_code, 404)
            # playback reports: 3 failures -> broken -> the stream goes last; a success brings it back
            tok = st["streams"][0]["attempt_token"]
            for _ in range(3):
                st2 = c.get("/api/streams/ch_A").json()
                t = next(s["attempt_token"] for s in st2["streams"] if s["url"] == "https://h1/b.m3u8")
                self.assertEqual(c.post("/api/playback-report", json={"attempt_token": t, "event": "failure",
                                                                      "code": "network"}).json(), {"ok": True})
            h = live.by_id("ch_A")["stream_health"]["https://h1/b.m3u8"]
            self.assertEqual((h["status"], h["failures"]), ("broken", 3))
            self.assertEqual(c.post("/api/playback-report", json={"attempt_token": tok, "event": "success"}).json(), {"ok": True})
            self.assertEqual(live.by_id("ch_A")["stream_health"]["https://h1/b.m3u8"]["status"], "healthy")
            # progress for a channel is ignored
            r = c.post("/api/progress", json={"profile": "p1", "item_id": "ch_A", "position": 5, "duration": 0})
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
