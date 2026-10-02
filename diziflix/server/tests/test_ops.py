"""Ops panel: sahte state, ağsız."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import ops
from app.scraper import heal as sheal, state as sstate


class OpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = sstate.STATE_DIR
        sstate.STATE_DIR = self.tmp.name
        sstate._active.clear()
        app = FastAPI()
        app.include_router(ops.router)
        self.c = TestClient(app)
        self._bg = (ops._bg_scan, ops._bg_heal)
        ops._bg_scan = ops._bg_heal = lambda *a, **k: None  # arka plan işi ağa çıkmasın

    def tearDown(self):
        sstate.STATE_DIR = self._old
        ops._bg_scan, ops._bg_heal = self._bg
        self.tmp.cleanup()

    def test_pages(self):
        r = self.c.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("diziflix ops", r.text)
        self.assertEqual(self.c.get("/admin/app.js").status_code, 200)
        self.assertEqual(self.c.get("/admin/nope.js").status_code, 404)

    def test_onboard_tab_assets(self):
        # "Site ekle" sekmesi: onboard.js beyaz listede, sayfa onu ve sekme düğmesini taşıyor
        r = self.c.get("/admin/onboard.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])
        self.assertIn("dzTabHooks.onboard", r.text)
        page = self.c.get("/admin").text
        for needle in ('data-tab="onboard"', 'id="tab-onboard"', '/admin/onboard.js'):
            self.assertIn(needle, page)
        for name in ("app.js", "library.js", "settings.js", "style.css"):
            self.assertEqual(self.c.get("/admin/" + name).status_code, 200)

    def test_overview_and_history(self):
        sstate.record_ops_run({"site": "sinemalar", "started_at": "2026-01-01T00:00:00Z", "duration": 4.0,
                               "status": "success", "scraped": 20, "ingested": 20, "pages": 2, "rate": 5.0})
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:01:00Z", "outcome": "fixed",
                                "diff": [{"path": "row_selector", "before": ".a", "after": ".b"}]})
        d = self.c.get("/api/ops/overview").json()
        s = {x["site"]: x for x in d["sites"]}["sinemalar"]
        self.assertEqual(s["scraped"], 20)
        self.assertEqual(s["last_heal"]["outcome"], "fixed")
        self.assertEqual(len(self.c.get("/api/ops/runs?site=sinemalar").json()["runs"]), 1)
        self.assertEqual(self.c.get("/api/ops/runs?site=yok").json()["runs"], [])
        self.assertEqual(self.c.get("/api/ops/heals").json()["heals"][0]["diff"][0]["after"], ".b")

    def test_history_capped(self):
        for i in range(sstate.OPS_LIMIT + 5):
            sstate.record_ops_run({"site": "x", "n": i})
        rows = sstate.list_ops("runs", None, 1000)
        self.assertEqual(len(rows), sstate.OPS_LIMIT)
        self.assertEqual(rows[0]["n"], sstate.OPS_LIMIT + 4)

    def test_trigger_blocks_duplicate(self):
        self.assertEqual(self.c.post("/api/ops/sites/sinemalar/scan").json(), {"started": True})
        self.assertEqual(self.c.post("/api/ops/sites/sinemalar/scan").json()["started"], False)
        act = self.c.get("/api/ops/active").json()["active"]
        self.assertEqual([(a["site"], a["kind"]) for a in act], [("sinemalar", "scan")])
        self.assertEqual(self.c.post("/api/ops/sites/sinemalar/heal").json(), {"started": False, "reason": "no_drift"})
        self.assertEqual(self.c.post("/api/ops/sites/sinemalar/heal?force=true").json(), {"started": True})
        self.assertEqual(self.c.post("/api/ops/sites/nope/scan").status_code, 404)

    def test_overview_recent_and_catalog(self):
        for i in range(30):
            sstate.record_ops_run({"site": "sinemalar", "started_at": "2026-01-01T00:%02d:00Z" % i,
                                   "duration": 1.0, "status": "success", "scraped": i})
        s = {x["site"]: x for x in self.c.get("/api/ops/overview").json()["sites"]}["sinemalar"]
        self.assertEqual(len(s["recent"]), ops.RECENT_N)
        self.assertEqual(s["recent"][-1]["scraped"], 29)  # oldest first, newest last
        self.assertIn("item_count", s["catalog"])
        empty = {x["site"]: x for x in self.c.get("/api/ops/overview").json()["sites"]}
        for x in empty.values():
            self.assertIsInstance(x["recent"], list)

    def test_events_merged_and_paged(self):
        sstate.record_ops_run({"site": "sinemalar", "started_at": "2026-01-01T00:00:00Z", "status": "success"})
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:01:00Z", "outcome": "fixed", "applied": True})
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:02:00Z", "outcome": "skipped_cooldown"})
        sstate.record_ops_run({"site": "yabancidizi", "started_at": "2026-01-01T00:03:00Z", "status": "error"})
        ev = self.c.get("/api/ops/events").json()["events"]
        self.assertEqual([e["kind"] for e in ev], ["scan", "cooldown", "heal", "scan"])
        self.assertTrue(ev[2]["can_rollback"])
        self.assertEqual([e["kind"] for e in self.c.get("/api/ops/events?site=sinemalar").json()["events"]],
                         ["cooldown", "heal", "scan"])
        d = self.c.get("/api/ops/events?limit=2").json()
        self.assertEqual(len(d["events"]), 2)
        self.assertEqual(d["next_before"], "2026-01-01T00:02:00Z")
        rest = self.c.get("/api/ops/events?limit=2&before=" + d["next_before"]).json()
        self.assertEqual([e["kind"] for e in rest["events"]], ["heal", "scan"])
        self.assertIsNone(rest["next_before"])
        self.assertEqual(self.c.get("/api/ops/events?site=yok").json()["events"], [])

    def test_rollback_records_event(self):
        from app.scraper import config as scfg
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:01:00Z", "outcome": "fixed", "applied": True})
        old = scfg.rollback_config
        scfg.rollback_config = lambda site: 1
        try:
            self.assertEqual(self.c.post("/api/ops/sites/sinemalar/rollback").json(), {"ok": True, "version": 1})
        finally:
            scfg.rollback_config = old
        ev = self.c.get("/api/ops/events").json()["events"]
        self.assertEqual(ev[0]["kind"], "rollback")
        self.assertFalse([e for e in ev if e["kind"] == "heal"][0]["can_rollback"])

    def test_selector_diff(self):
        d = sheal.selector_diff({"row_selector": ".a", "fields": {"t": {"selector": "h1"}}},
                                {"row_selector": ".b", "fields": {"t": {"selector": "h1"}}})
        self.assertEqual(d, [{"path": "row_selector", "before": ".a", "after": ".b"}])


if __name__ == "__main__":
    unittest.main()
