import _sandbox  # noqa: F401  (must be first)

import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.library import enginehint, playissues, streamdiag
from app.main import app
from app.routers.streams import public_streams

SID = "src_engine_hint_test"
TIZEN_UA = "Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36"


class EngineHintTests(unittest.TestCase):
    """streams[].engine_hint: seed table per platform, learned engine_stats override it; playback reports feed the statistics."""

    def setUp(self):
        db.init()
        db.execute("DELETE FROM engine_stats")
        db.execute("DELETE FROM playback_attempts WHERE source_id=?", (SID,))
        db.execute("DELETE FROM video_sources WHERE id=?", (SID,))
        payload = {"streams": [{"url": "https://x.uns.test/master.m3u8", "type": "hls", "variant_id": "v_u", "provider": "UNS"}],
                   "valid_until": int(time.time()) + 3600}
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,resolved_payload,resolved_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (SID, "c_x", "diziasya", "k", "episode", "https://site.test/e1",
                                                  json.dumps(payload), int(time.time()), int(time.time())))
        self.token = "e" * 32
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (self.token, SID, int(time.time())))
        self.p = [patch.object(streamdiag, "schedule", lambda job: None), patch.object(playissues, "check", lambda site: "")]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def shaped(self, platform):
        streams = [{"url": "https://x.uns.test/master.m3u8", "type": "hls", "provider": "UNS"},
                   {"url": "https://ok.test/v.mp4", "type": "mp4", "provider": "OK.ru"},
                   {"url": "https://p2p.test/m.m3u8", "type": "hls", "provider": "P2PPLAY"},
                   {"url": "https://yt.test/embed", "type": "embed", "provider": "UNS"}]
        return public_streams(streams, "", hint=enginehint.hinter(platform))

    def test_platform_and_seed(self):
        self.assertEqual(enginehint.platform_of("", TIZEN_UA), "tizen")
        self.assertEqual(enginehint.platform_of("android", TIZEN_UA), "android")
        self.assertEqual(enginehint.platform_of(None, "Mozilla/5.0 (Linux; Android 11; wv)"), "android")
        out = self.shaped("tizen")
        self.assertEqual([(s.get("engine_hint"), s.get("engine_hint_source")) for s in out],
                         [("hlsjs", "seed"), ("native", "seed"), (None, None), (None, None)])
        self.assertFalse(any("engine_hint" in s for s in self.shaped("android")), "android keeps the client's own choice")

    def test_learned_overrides_seed(self):
        for _ in range(3): enginehint.record("tizen", "UNS", "hls", "native", True)
        enginehint.record("tizen", "UNS", "hls", "hlsjs", False)
        self.assertEqual(self.shaped("tizen")[0].get("engine_hint_source"), "learned")
        self.assertEqual(self.shaped("tizen")[0].get("engine_hint"), "native")
        self.assertFalse(enginehint.record("tizen", "UNS", "hls", "native", False, "aborted"), "device-side code not counted")

    def test_report_counts_engine_with_provider_from_variant(self):
        c = TestClient(app)
        r = c.post("/api/playback-report", headers={"user-agent": TIZEN_UA},
                   json={"attempt_token": self.token, "event": "failure", "code": "timeout", "engine": "native",
                         "variant_id": "v_u", "stream_type": "hls"})
        self.assertEqual(r.status_code, 200, r.text)
        row = db.query_one("SELECT * FROM engine_stats")
        self.assertEqual((row["platform"], row["provider"], row["stream_type"], row["engine"], row["fail"]),
                         ("tizen", "uns", "hls", "native", 1))
        self.assertEqual(db.query_one("SELECT engine FROM playback_attempts WHERE token=?", (self.token,))["engine"], "html5",
                         "stored engine stays html5")


if __name__ == "__main__":
    unittest.main()
