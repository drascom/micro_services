import _sandbox  # noqa: F401  (must be first)

"""T10 <-> T08 end to end: the real admin routes over the real stage engine (fake fetcher, fake LLM, jobs run inline)."""
import ipaddress
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main
from app.control.jobs import store as job_store, worker
from app.control.stages import engine
from app.core.fetch import FetchError, Page, netguard
from app.core.llm import fake

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fakesite")
A = "https://fakesite.test"
_real_addresses = netguard._addresses
ROUTES = [(r"^/$", "home.html"), (r"^/tur/aksiyon/\?syf=2$", "listing_aksiyon_2.html"),
          (r"^/tur/aksiyon/$", "listing_aksiyon.html"), (r"^/tur/dram/$", "listing_dram.html"),
          (r"^/dizi/[^/]+/$", "series_detail.html"), (r"^/film/[^/]+/$", "film_detail.html")]


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]
    return _real_addresses(host, port)


def _fetcher(url, *, site, page_type="page", timeout=None):
    path = re.match(r"^https?://[^/]+(.*)$", url).group(1) or "/"
    for rx, name in ROUTES:
        if re.search(rx, path):
            with open(os.path.join(FIX, name), encoding="utf-8") as fh:
                return Page(url, url, 200, fh.read(), "http")
    raise FetchError("http_404", "not found", status=404, url=url)


class AdminStagesE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        for obj, name, value in ((netguard, "_addresses", _fake_addresses), (engine, "FETCHER", _fetcher),
                                 (engine, "A1_REVIEW", False)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        engine.register()
        p = mock.patch.dict(engine.STAGES, {k: engine.STAGES[k] for k in ("A1", "A2", "A3")}, clear=True)   # A4-A7: T09 tests
        p.start()
        self.addCleanup(p.stop)
        fake.script([])
        self.runner = worker.Runner(threads=1, owner="test")
        self.client = TestClient(main.app)

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drain(self, limit=20):
        n = 0
        while n < limit and self.runner.run_one() is not None:
            n += 1
        return n

    def ok(self, method, path, **kw):
        r = getattr(self.client, method)(path, **kw)
        self.assertEqual(r.status_code, 200, (path, r.text))
        return r.json()

    def add_site(self):
        body = self.ok("post", "/api/control/sites", json={"url": A + "/"})
        self.assertTrue(body["started"])
        return body["site_id"]

    def stages(self, sid):
        return {st["id"]: st for st in self.ok("get", f"/api/control/sites/{sid}/stages")["stages"]}

    def test_add_site_runs_a1_a3_and_activates(self):
        sid = self.add_site()
        jobs = self.ok("get", f"/api/control/sites/{sid}")["jobs"]
        self.assertEqual([j["key"] for j in jobs], [engine.job_key(sid, "A1")])
        self.assertTrue(self.ok("get", f"/api/control/sites/{sid}/stages")["busy"])
        self.drain()
        body = self.ok("get", f"/api/control/sites/{sid}")
        self.assertEqual((body["site"]["status"], body["site"]["active_version"]), ("active", 1))
        self.assertEqual((body["map"]["passed"], body["map"]["stuck"]), (3, None))
        self.assertEqual([j for j in body["jobs"] if j["kind"] == "stage"], [])   # T11: the crawl job may wait for its slot
        data = self.ok("get", f"/api/control/sites/{sid}/stages")
        self.assertFalse(data["busy"])
        st = {s["id"]: s for s in data["stages"]}
        for sid_ in ("A1", "A2", "A3"):
            self.assertIn(st[sid_]["state"], ("passed", "passed_warn"), (sid_, st[sid_]["checks"]))
        self.assertEqual(st["A1"]["skippable"], ["poster"])
        self.assertIn("genres", st["A3"]["skippable"])
        self.assertNotIn("key", st["A3"]["skippable"])
        self.assertIn("rerun", st["A1"]["actions"])

    def test_hint_skip_rerun_show_in_overview(self):
        sid = self.add_site()
        self.drain()
        self.ok("post", f"/api/control/sites/{sid}/stages/A1/hint", json={"text": "kartlar div.cards altında"})
        self.assertTrue(any(m["role"] == "user" and "div.cards" in m["text"] for m in self.stages(sid)["A1"]["messages"]))
        self.drain()
        self.ok("post", f"/api/control/sites/{sid}/stages/A1/skip", json={"field": "poster"})
        self.drain()
        a1 = self.stages(sid)["A1"]
        self.assertNotIn("poster_fill", [c["name"] for c in a1["checks"]])
        self.assertTrue(any("poster" in m["text"] for m in a1["messages"]))
        self.ok("post", f"/api/control/sites/{sid}/stages/A2/rerun")
        self.assertTrue(self.ok("get", f"/api/control/sites/{sid}/stages")["busy"])
        self.drain()
        self.assertIn(self.stages(sid)["A2"]["state"], ("passed", "passed_warn"))
        # error mapping: engine ValueError -> 400, unknown stage (engine LookupError / route) -> 404
        bad = self.client.post(f"/api/control/sites/{sid}/stages/A1/skip", json={"field": "url"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/stages/A1/skip", json={}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/stages/A4/rerun").status_code, 404)
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/stages/A9/rerun").status_code, 404)
        self.assertEqual(self.client.post("/api/control/sites/nope/stages/A1/rerun").status_code, 404)

    def test_paused_site_jobs_wait_until_resume(self):
        sid = self.add_site()
        self.ok("post", f"/api/control/sites/{sid}/pause")
        self.drain()
        job = job_store.get_by_key(engine.job_key(sid, "A1"))
        self.assertEqual((job["state"], job["wait_reason"]), ("waiting", engine.PAUSED))
        self.assertEqual(self.stages(sid)["A1"]["state"], "pending")
        self.assertEqual(self.ok("post", f"/api/control/sites/{sid}/resume")["woken_jobs"], 1)
        self.drain()
        self.assertEqual(self.ok("get", f"/api/control/sites/{sid}")["site"]["status"], "active")
        self.assertEqual(job_store.get_by_key("refresh:" + sid)["state"], "done")   # T11: the crawl plan replaced K0

    def test_delete_cancels_jobs_and_drops_stage_rows(self):
        sid = self.add_site()
        self.runner.run_one()   # A1 done, A2 queued
        key = engine.job_key(sid, "A2")
        db.execute("UPDATE jobs SET state='running', lease_owner='other', lease_until=? WHERE key=?", (9e12, key))
        self.assertTrue(db.query_one("SELECT COUNT(*) FROM stage_messages WHERE site_id=?", (sid,))[0])
        body = self.ok("delete", f"/api/control/sites/{sid}")
        self.assertGreaterEqual(body["cancelled_jobs"], 1)
        job = job_store.get_by_key(key)
        self.assertEqual((job["state"], job["error"]), ("failed", "cancelled"))
        self.assertEqual(job_store.active(sid), [])
        for table in ("site_stages", "stage_messages"):
            self.assertEqual(db.query_one(f"SELECT COUNT(*) FROM {table} WHERE site_id=?", (sid,))[0], 0, table)
        self.assertEqual(self.client.get(f"/api/control/sites/{sid}").status_code, 404)
        self.assertEqual(self.drain(), 0)


if __name__ == "__main__":
    unittest.main()
