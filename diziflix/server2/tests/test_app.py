import _sandbox  # noqa: F401  (must be first)

import unittest

from fastapi.testclient import TestClient

from app import main
from app.core import events


class AppTests(unittest.TestCase):
    def test_health(self):
        with TestClient(main.app) as client:
            res = client.get("/api/health")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["db"], "ok")
            self.assertIn("version", body)
            missing = client.get("/api/nope")
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["error"]["code"], "http_404")

    def test_events_subscriber_error_does_not_reach_publisher(self):
        events.clear()
        got = []
        events.subscribe("playback_ok", lambda **p: 1 / 0)
        events.subscribe("playback_ok", lambda **p: got.append(p))
        with self.assertLogs("diziflix.events", "ERROR"):
            events.emit("playback_ok", source_id="s1")
        self.assertEqual(got, [{"source_id": "s1"}])
        events.clear()


if __name__ == "__main__":
    unittest.main()
