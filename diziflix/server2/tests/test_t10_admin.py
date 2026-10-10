import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import re
import subprocess
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main
from app.admin import assets, routes_sites
from app.control.jobs import store as jobs_store
from app.control.sitemap import store as sitemap_store
from app.core.fetch import netguard

BASE = "https://fakesite.test"
_real_addresses = netguard._addresses
STATIC = os.path.join(os.path.dirname(assets.__file__), "static")


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]
    return _real_addresses(host, port)


class FakeActions:
    def __init__(self):
        self.calls = []

    def _rec(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return {"queued": True}

    def start(self, *a, **k): return self._rec("start", *a, **k)
    def hint(self, *a, **k): return self._rec("hint", *a, **k)
    def sample(self, *a, **k): return self._rec("sample", *a, **k)
    def skip(self, *a, **k): return self._rec("skip", *a, **k)
    def rerun(self, *a, **k): return self._rec("rerun", *a, **k)
    def mark_gap(self, *a, **k): return self._rec("mark_gap", *a, **k)


class FakeStatus:
    @staticmethod
    def overview(site_id):
        return {"site": {"site_id": site_id}, "stages": [
            {"id": "A1", "title": "Ana sayfa", "state": "passed", "summary": "3 bölüm", "checks": [], "samples": [],
             "evidence": {}, "messages": [], "actions": ["rerun", "hint"]},
            {"id": "A3", "title": "Başlık", "state": "needs_human", "summary": "afiş yok", "checks": [], "samples": [],
             "evidence": {}, "messages": [], "actions": ["hint", "sample", "skip", "rerun", "gap"]}]}


def _site_stages_table():
    db.execute("CREATE TABLE IF NOT EXISTS site_stages(site_id TEXT, stage TEXT, state TEXT, round INTEGER, patch TEXT,"
               " measurement TEXT, evidence TEXT, started_at INTEGER, updated_at INTEGER, PRIMARY KEY(site_id, stage))")


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        _site_stages_table()
        cls.client = TestClient(main.app)

    def setUp(self):
        for t in ("site_versions", "sites", "jobs", "admin_notes", "site_stages"):
            db.execute(f"DELETE FROM {t}")
        p = mock.patch.object(netguard, "_addresses", _fake_addresses)
        p.start()
        self.addCleanup(p.stop)
        self.actions = FakeActions()
        s = mock.patch.object(routes_sites, "_stages", lambda: (self.actions, FakeStatus))
        s.start()
        self.addCleanup(s.stop)

    def _site(self):
        r = self.client.post("/api/control/sites", json={"url": BASE + "/"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["site_id"]

    # -- assets
    def test_admin_assets(self):
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers["cache-control"])
        for name in assets._ASSETS:
            self.assertEqual(self.client.get("/admin/" + name).status_code, 200, name)
        for bad in ("nope.js", "../main.py", "js/../index.html", "index.html"):
            self.assertEqual(self.client.get("/admin/" + bad).status_code, 404, bad)

    def test_js_files_pass_node_check_and_are_registered(self):
        files = sorted(os.listdir(os.path.join(STATIC, "js")))
        self.assertEqual({"js/" + f for f in files} - set(assets._ASSETS), set())
        for f in files:
            subprocess.run(["node", "--check", os.path.join(STATIC, "js", f)], check=True)

    def test_no_raw_yaml_editor_in_ui(self):
        text = ""
        for f in os.listdir(os.path.join(STATIC, "js")):
            with open(os.path.join(STATIC, "js", f), encoding="utf-8") as fh:
                text += fh.read()
        self.assertNotIn("contenteditable", text)
        self.assertEqual(text.count("<textarea"), 3)   # the hint box, the site note editor, the "Site ekle" note
        self.assertIsNone(re.search(r"textarea[^>]*yaml", text))
        self.assertIn('pre class="yaml"', text)
        # technical metrics never in the closed card (summary row = id, title, badge, one-line summary)
        self.assertIsNone(re.search(r"<summary>.*(token|ms|selector)", text))

    # -- sites
    def test_create_site_and_reject_bad_or_private(self):
        sid = self._site()
        self.assertEqual(self.actions.calls[0], ("start", (sid,), {}))
        for url in ("http://127.0.0.1/", "http://192.168.0.61/", "ftp://fakesite.test/", "", "x" * 3000):
            r = self.client.post("/api/control/sites", json={"url": url})
            self.assertEqual(r.status_code, 400, url)
            self.assertIn("message", r.json()["error"])
        listed = self.client.get("/api/control/sites").json()["sites"]
        self.assertEqual([s["site_id"] for s in listed], [sid])
        self.assertEqual(listed[0]["map"]["total"], 7)

    def test_control_writes_refuse_foreign_origins_and_get_no_cors(self):
        evil = {"Origin": "https://evil.example"}
        r = self.client.post("/api/control/sites", json={"url": BASE + "/"}, headers=evil)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "forbidden_origin"))
        self.assertEqual(self.client.delete("/api/control/sites/x", headers={"Origin": "null"}).status_code, 403)
        self.assertEqual(sitemap_store.list_sites(), [])
        same = self.client.post("/api/control/sites", json={"url": BASE + "/"}, headers={"Origin": "http://testserver"})
        self.assertEqual(same.status_code, 200, same.text)
        got = self.client.get("/api/control/sites", headers=evil)
        self.assertNotIn("access-control-allow-origin", got.headers)
        pre = self.client.options("/api/control/sites", headers={**evil, "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", pre.headers)
        # the client API keeps CORS *
        self.assertEqual(self.client.get("/api/health", headers=evil).headers.get("access-control-allow-origin"), "*")

    def test_hint_on_a_running_stage_is_409(self):
        from app.control.stages.actions import StageBusy
        sid = self._site()
        def busy(*a, **k):
            raise StageBusy("Aşama çalışıyor; bitince ipucunu tekrar gönder")
        self.actions.hint = busy
        r = self.client.post(f"/api/control/sites/{sid}/stages/A1/hint", json={"text": "x"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "stage_busy"))
        self.assertIn("bitince", r.json()["error"]["message"])

    def test_pause_resume_delete_unknown(self):
        sid = self._site()
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/pause").json()["site"]["status"], "paused")
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/resume").json()["site"]["status"], "draft")
        self.assertEqual(self.client.get("/api/control/sites/nope").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/control/sites/{sid}?purge=1").status_code, 200)
        self.assertIsNone(sitemap_store.get(sid))

    def test_stage_actions_route_to_engine(self):
        sid = self._site()
        self.actions.calls.clear()
        u = f"/api/control/sites/{sid}/stages/A3"
        self.assertEqual(self.client.post(u + "/hint", json={"text": "afiş data-src'de"}).status_code, 200)
        self.assertEqual(self.client.post(u + "/sample", json={"url": BASE + "/dizi/x/"}).status_code, 200)
        self.assertEqual(self.client.post(u + "/skip", json={"field": "year"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/stages/A7/skip", json={}).status_code, 200)
        self.assertEqual(self.client.post(u + "/rerun").status_code, 200)
        self.assertEqual(self.client.post(u + "/gap", json={"title": "afiş tuvalde"}).status_code, 200)
        self.assertEqual(self.actions.calls, [
            ("hint", (sid, "A3", "afiş data-src'de"), {}), ("sample", (sid, "A3", BASE + "/dizi/x/"), {}),
            ("skip", (sid, "A3"), {"field": "year"}), ("skip", (sid, "A7"), {"field": None}),
            ("rerun", (sid, "A3"), {}), ("mark_gap", (sid, "A3", "afiş tuvalde"), {})])
        ov = self.client.get(f"/api/control/sites/{sid}/stages").json()
        self.assertEqual([s["state"] for s in ov["stages"]], ["passed", "needs_human"])
        self.assertIn("year", ov["stages"][1]["skippable"])

    def test_stage_errors(self):
        sid = self._site()
        self.actions.calls.clear()
        u = f"/api/control/sites/{sid}/stages/A3"
        self.assertEqual(self.client.post("/api/control/sites/nope/stages/A3/rerun").status_code, 404)
        self.assertEqual(self.client.post(f"/api/control/sites/{sid}/stages/A9/rerun").status_code, 404)
        for field in ("title", "url", "key"):
            r = self.client.post(u + "/skip", json={"field": field})
            self.assertEqual(r.status_code, 400, field)
        self.assertEqual(self.client.post(u + "/hint", json={"text": "x" * 2001}).status_code, 400)
        self.assertEqual(self.client.post(u + "/hint", json={"text": "  "}).status_code, 400)
        self.assertEqual(self.client.post(u + "/sample", json={"url": "https://other.test/a"}).status_code, 400)
        self.assertEqual(self.client.post(u + "/sample", json={"url": "http://10.0.0.1/a"}).status_code, 400)
        self.assertEqual(self.actions.calls, [])
        # an engine refusal (ValueError) becomes 400, unknown (LookupError) 404
        self.actions.skip = mock.Mock(side_effect=ValueError("A3 is not optional"))
        self.assertEqual(self.client.post(u + "/skip", json={}).status_code, 400)
        self.actions.rerun = mock.Mock(side_effect=KeyError("A3"))
        self.assertEqual(self.client.post(u + "/rerun").status_code, 404)

    def test_summary_counts(self):
        sid = self._site()
        db.execute("INSERT INTO site_stages(site_id, stage, state) VALUES (?, 'A1', 'passed'), (?, 'A3', 'needs_human')",
                   (sid, sid))
        jobs_store.enqueue("stage", f"stage:{sid}:A2", site_id=sid)
        jobs_store.lease("t", ["stage"])
        jobs_store.enqueue("stage", f"stage:{sid}:A4", site_id=sid)   # queued: not "running"
        s = self.client.get("/api/control/summary").json()
        self.assertEqual((s["running_jobs"], s["waiting_for_you"], s["open_problems"]), (1, 1, 0))
        site = self.client.get("/api/control/sites").json()["sites"][0]
        self.assertEqual(site["map"]["passed"], 1)
        self.assertEqual(site["map"]["stuck"], {"stage": "A3", "state": "needs_human"})

    def test_versions_diff_rollback_export_import(self):
        sid = self._site()
        d = sitemap_store.draft(sid)
        d["fetch"]["delay_ms"] = 1500
        sitemap_store.save_draft(sid, d)
        v1 = sitemap_store.commit(sid, created_by="onboard")
        d["fetch"]["delay_ms"] = 2000
        sitemap_store.save_draft(sid, d)
        v2 = sitemap_store.commit(sid, created_by="edit")
        base = f"/api/control/sites/{sid}"
        vs = self.client.get(base + "/versions").json()
        self.assertEqual(([v["version"] for v in vs["versions"]], vs["active_version"]), ([v1, v2], v2))
        ch = self.client.get(base + f"/versions/diff?a={v1}&b={v2}").json()["changes"]
        self.assertEqual(ch, [{"path": "fetch.delay_ms", "before": 1500, "after": 2000}])
        self.assertEqual(self.client.get(base + "/versions/diff?a=99&b=1").status_code, 404)
        r = self.client.post(base + f"/versions/{v1}/rollback")
        self.assertEqual(r.json()["active_version"], v2 + 1)
        self.assertEqual(sitemap_store.active(sid)["fetch"]["delay_ms"], 1500)
        self.assertEqual(sitemap_store.versions(sid)[-1]["created_by"], "admin")
        self.assertEqual(self.client.post(base + "/versions/42/rollback").status_code, 404)
        exp = self.client.get(base + "/export")
        self.assertIn("attachment", exp.headers["content-disposition"])
        text = exp.text.replace("delay_ms: 1500", "delay_ms: 3000")
        self.assertEqual(self.client.post("/api/control/sites/import", json={"yaml": text}).status_code, 409)
        r = self.client.post("/api/control/sites/import", json={"yaml": text, "overwrite": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sitemap_store.draft(sid)["fetch"]["delay_ms"], 3000)          # draft only
        self.assertEqual(sitemap_store.active(sid)["fetch"]["delay_ms"], 1500)
        self.assertEqual(self.client.post("/api/control/sites/import", json={"yaml": "a: [1"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
