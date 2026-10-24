import _sandbox  # noqa: F401  (must be first)

import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.library import playissues, streamdiag, videos
from app.main import app
from app.routers import debuglog

SID = "src_variant_test"


class PlaybackVariantTests(unittest.TestCase):
    """POST /api/playback-report variant_id: one variant's failure only demotes it; the last one is the whole-source failure."""

    def setUp(self):
        db.init()
        db.execute("DELETE FROM playback_attempts WHERE source_id=?", (SID,))
        db.execute("DELETE FROM video_sources WHERE id=?", (SID,))
        payload = {"streams": [{"url": "https://a.cdn.test/one.mp4", "type": "mp4", "variant_id": "v_a", "provider": "VidHide"},
                               {"url": "https://b.cdn.test/two.mp4", "type": "mp4", "variant_id": "v_b", "provider": "OK.ru"}],
                   "valid_until": int(time.time()) + 3600}
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,resolved_payload,resolved_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (SID, "c_x", "yabancidizi", "k", "episode", "https://site.test/e1",
                                                  json.dumps(payload), int(time.time()), int(time.time())))
        self.token = "a" * 32
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (self.token, SID, int(time.time())))
        self.p = [patch.object(streamdiag, "schedule", lambda job: None), patch.object(playissues, "check", lambda site: "")]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def src(self):
        return db.query_one("SELECT * FROM video_sources WHERE id=?", (SID,))

    def test_one_variant_failure_keeps_source_health_and_demotes_it(self):
        r = videos.feedback(self.token, "failure", "stall", "html5", detail="stall: bekleme 18 sn", variant_id="v_a")
        self.assertTrue(r.get("variant"))
        self.assertNotIn("finder", r)
        s = self.src()
        self.assertEqual((s["status"], s["failures"]), ("unknown", 0), "health untouched")
        self.assertIsNotNone(s["resolved_payload"], "stored resolution kept")
        self.assertEqual([x["variant_id"] for x in json.loads(s["resolved_payload"])["streams"]], ["v_b", "v_a"], "failed variant moved last")
        issue = db.query_one("SELECT * FROM playback_issues WHERE source_id=?", (SID,))
        self.assertEqual((issue["issue_class"], issue["host"]), ("stall", "a.cdn.test"), "issue noted for that variant, class stall")
        self.assertNotIn("stall", playissues.HEAL_CLASSES)
        self.assertTrue(videos.feedback(self.token, "failure", "stall", "html5", variant_id="v_a").get("duplicate"), "once per attempt")
        # the last remaining variant fails -> whole-source failure (old behaviour)
        r2 = videos.feedback(self.token, "failure", "playback_failed", "html5", variant_id="v_b")
        self.assertFalse(r2.get("variant"))
        s = self.src()
        self.assertEqual((s["status"], s["failures"]), ("suspect", 1))
        self.assertIsNone(s["resolved_payload"])

    def test_report_without_variant_is_whole_source(self):
        videos.feedback(self.token, "failure", "playback_failed", "html5")
        self.assertEqual(self.src()["status"], "suspect")

    def test_timeout_with_reachable_diag_is_not_a_heal_class(self):
        self.assertEqual(playissues.issue_class("timeout", "reachable"), "timeout_reachable")
        self.assertNotIn("timeout_reachable", playissues.HEAL_CLASSES)
        self.assertEqual(playissues.issue_class("timeout", ""), "timeout")
        self.assertEqual(playissues.issue_class("stall", "forbidden"), "stall")

    def test_endpoint_accepts_variant_fields_and_stall(self):
        c = TestClient(app)
        r = c.post("/api/playback-report", json={"attempt_token": self.token, "event": "failure", "code": "stall", "engine": "html5",
                                                 "variant_id": "v_a", "stream_url_host": "a.cdn.test", "stream_type": "mp4"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("variant"))


class DebugLogTests(unittest.TestCase):
    def setUp(self):
        debuglog.reset()
        self.c = TestClient(app)

    def test_post_ring_buffer_and_read(self):
        r = self.c.post("/api/debug-log", json={"device": "UE55TEST-ab12", "ua": "Tizen", "app_ver": "1", "mode": "summary",
                                                 "lines": [{"t": 1, "area": "player", "level": "error", "text": "✗ VidHide · takılma " + "x" * 900}]})
        self.assertEqual(r.json(), {"ok": True, "accepted": 1})
        devs = self.c.get("/api/ops/debug-log").json()["devices"]
        self.assertEqual((devs[0]["device"], devs[0]["lines"]), ("UE55TEST-ab12", 1))
        got = self.c.get("/api/ops/debug-log?device=UE55TEST-ab12").json()
        self.assertEqual(len(got["lines"][0]["text"]), debuglog.MAX_TEXT, "line cut")
        for k in range(12):
            self.c.post("/api/debug-log", json={"device": "UE55TEST-ab12", "lines": [{"text": "l%d-%d" % (k, i)} for i in range(100)]})
        got = self.c.get("/api/ops/debug-log?device=UE55TEST-ab12&limit=1000").json()
        self.assertEqual(len(got["lines"]), 1000, "ring buffer 1000")
        self.assertEqual(got["lines"][-1]["text"], "l11-99")
        since = self.c.get("/api/ops/debug-log?device=UE55TEST-ab12&since=%d" % (got["seq"] - 3)).json()
        self.assertEqual(len(since["lines"]), 3)
        self.assertEqual(self.c.post("/api/debug-log", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.c.post("/api/debug-log", json={"lines": []}).status_code, 400)
        self.assertEqual(self.c.get("/api/ops/debug-log?device=nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
