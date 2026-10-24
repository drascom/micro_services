"""/api/streams?debug=1 + /api/source-finder?debug=1: ``debug_trace`` (library/debugtrace.py) only on request, shape, thread-safe."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402
import time
import unittest

from app import db
from app.library import debugtrace, live, videos
from test_live_tv import ch, files, FakeClient, CFG

KEYS = {"t_ms", "stage", "result", "site", "provider", "host", "detail", "ms"}


def check_shape(case, steps):
    case.assertIsInstance(steps, list)
    case.assertTrue(steps)
    for s in steps:
        case.assertLessEqual(set(s), KEYS, s)
        case.assertIsInstance(s["t_ms"], int)
        case.assertIn(s["result"], debugtrace.RESULTS)


class DebugTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def test_parallel_candidates_share_the_trace(self):
        def one(c):
            time.sleep(0.01)
            debugtrace.add("provider", "found", provider=c["label"])
            return {"label": c["label"], "provider": "", "streams": [{"url": "x"}], "duration": 0, "error": "", "events": [], "ms": 1}
        trace = debugtrace.start()
        try:
            videos._run_candidates([{"label": str(i)} for i in range(6)], one)
        finally:
            debugtrace.stop()
        self.assertEqual(sorted(s["provider"] for s in trace.take()), [str(i) for i in range(6)])
        debugtrace.add("x", "info")   # no trace bound: a no-op
        self.assertIsNone(debugtrace.active())

    def test_library_sources_trace(self):
        now = int(time.time())
        db.execute("DELETE FROM video_sources WHERE canonical_id='dbg1'")
        for sid, site, status in (("vs_dbg_a", "sitea", "unknown"), ("vs_dbg_b", "siteb", "broken")):
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,status,updated_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid, "dbg1", site, "k", "", "movie", "https://cdn.example/v.mp4", "direct", "mp4", status, now))
        trace = debugtrace.start()
        try:
            out = videos.streams("dbg1")
        finally:
            debugtrace.stop()
        self.assertEqual(len(out["streams"]), 1)
        steps = trace.take()
        check_shape(self, steps)
        got = {(s["stage"], s["result"], s.get("site")) for s in steps}
        self.assertIn(("source", "skipped", "siteb"), got)   # broken source left out, with the reason
        self.assertIn(("source", "found", "sitea"), got)
        self.assertIn("sources", {s["stage"] for s in steps})

    def test_streams_endpoint_debug_flag(self):
        from fastapi.testclient import TestClient
        from app.main import app
        db.execute("DELETE FROM channels")
        live.invalidate()
        live.run(client=FakeClient(files([ch("A", "Haber 1", streams=["https://h1/a.m3u8"])])), cfg=CFG, probe=False)
        with TestClient(app) as c:
            plain = c.get("/api/streams/ch_A").json()
            self.assertNotIn("debug_trace", plain)
            dbg = c.get("/api/streams/ch_A?debug=1").json()
            check_shape(self, dbg["debug_trace"])
            step = dbg["debug_trace"][-1]
            self.assertEqual((step["stage"], step["host"]), ("stream", "h1"))
            self.assertIn("doğrudan", step["detail"])
            self.assertEqual(len(dbg["streams"]), len(plain["streams"]))

    def test_finder_trace_shape(self):
        from app.routers.notifications import _finder_trace
        steps = _finder_trace({"state": "not_found", "steps": [{"name": "retry", "ok": False, "ms": 40, "note": "akış yok"},
                                                                {"name": "search", "ok": True, "ms": 60, "note": "bulundu"}]})
        check_shape(self, steps)
        self.assertEqual([(s["result"], s["t_ms"]) for s in steps], [("none", 40), ("found", 100), ("info", 100)])


if __name__ == "__main__":
    unittest.main()
