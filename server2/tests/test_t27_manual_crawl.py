import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import db, settings
from app.control.crawl import frontier, manual, process, run, state, status
from app.control.jobs import store as job_store

from test_t11_crawl import BASE, Case


class ManualCrawlTests(Case):
    """T27: admin-triggered crawls outside the K1 / gate quotas."""

    def _quota_full(self):
        settings.set("crawl_gate_daily_per_site", 10)
        run.activate(self.sid)
        self.drain()
        st = frontier.stats(self.sid)
        self.assertGreater(st["gate_wait"], 0)                    # gate budget full
        return st

    def test_crawl_all_processes_waiting_pages_without_counting_quota(self):
        self._quota_full()
        gate_before = process.gate_used(self.sid)
        quota_before = int(state.get(self.sid).get("quota_used") or 0)
        self.assertEqual(gate_before, 10)                         # the setting is read: the system crawl stops at it
        out = manual.start_all(self.sid)
        self.assertGreater(out["total"], 0)
        self.drain()
        left = db.query("SELECT url, mark FROM crawl_frontier WHERE site_id=? AND state IN ('new','failed') "
                        "AND mark NOT IN ('wait_rules')", (self.sid,))
        self.assertEqual(left, [])
        self.assertEqual(frontier.stats(self.sid)["gate_wait"], 0)
        self.assertEqual(process.gate_used(self.sid), gate_before)    # not counted
        self.assertEqual(int(state.get(self.sid).get("quota_used") or 0), quota_before)   # K1 quota not counted
        self.assertTrue(state.get(self.sid).get("k1_done_at"))
        m = status.view(self.sid)["manual"]
        self.assertEqual((m["state"], m["running"]), ("done", False))
        self.assertTrue(db.query_one("SELECT 1 FROM admin_notes WHERE text LIKE '%elle tarama bitti%'"))
        self.assertIn("gate_limit", status.view(self.sid)["plan"])

    def test_stop_cancels_and_remaining_pages_stay(self):
        self._quota_full()
        waiting = frontier.stats(self.sid)["gate_wait"]
        manual.start_all(self.sid)
        self.assertTrue(manual.stop_all(self.sid)["stopped"])
        self.assertEqual(job_store.get_by_key(manual.all_key(self.sid))["state"], "failed")
        self.drain()
        self.assertEqual(frontier.stats(self.sid)["gate_wait"], waiting)    # nothing read, the plan keeps them
        self.assertEqual(state.get(self.sid)["manual"]["state"], "cancelled")

    def test_title_now_writes_episodes_and_adds_a_missing_frontier_row(self):
        from fastapi.testclient import TestClient
        from app import main
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        run.activate(self.sid)
        self.drain()
        url = BASE + "/dizi/s0/"
        cid = db.query_one("SELECT canonical_id FROM source_items WHERE site_id=? AND key='s0'", (self.sid,))["canonical_id"]
        db.execute("DELETE FROM video_sources WHERE source=? AND source_key='s0'", (self.sid,))
        db.execute("DELETE FROM crawl_frontier WHERE site_id=? AND url=?", (self.sid, url))
        state.update(self.sid, lambda d: d.update(gate_date=state.today(), gate_used=10 ** 6))   # gate budget gone
        client = TestClient(main.app)
        self.assertTrue(client.post(f"/api/control/catalog/{cid}/crawl").json()["job_id"])
        self.drain()
        job = client.get(f"/api/control/catalog/{cid}/crawl").json()
        self.assertEqual(job["state"], "done", job)
        self.assertTrue(job["result"]["ok"], job)
        self.assertEqual(job["result"]["text"], "1 sezon, 2 bölüm, 2 kaynak yazıldı")
        self.assertEqual(frontier.get(self.sid, url)["mark"], "ok")
        self.assertEqual(state.get(self.sid)["gate_used"], 10 ** 6)       # not counted

    def test_gate_setting_limits_the_system_crawl(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        settings.set("crawl_gate_daily_per_site", 10)
        with mock.patch.object(process, "GATE_DAILY", 999):
            run.activate(self.sid)
            self.drain()
        self.assertLessEqual(process.gate_used(self.sid), 10)
        self.assertGreater(frontier.stats(self.sid)["gate_wait"], 0)
        self.assertEqual(status.view(self.sid)["plan"]["gate_limit"], 10)


if __name__ == "__main__":
    unittest.main()
