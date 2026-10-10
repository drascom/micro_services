import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app import config, db
from app.control import jobs, wiring
from app.control.jobs import schedule, store, worker
from app.control.sitemap import store as sitemap_store
from app.core.catalog import hooks
from app.core.fetch import netguard
from app.core.play import hooks as play_hooks
from app.core.play.ctx import PlayCtx
from test_t07_sitemap import _fake_addresses, fake_map


def _reset():
    db.init()
    for t in ("jobs", "settings", "site_versions", "sites"):
        db.execute(f"DELETE FROM {t}")
    worker._kinds.clear()
    schedule.clear()
    jobs.gc_hooks.clear()
    hooks.bind_tmdb(None)
    play_hooks.bind_ctx(None)


class WiringTests(unittest.TestCase):
    def setUp(self):
        _reset()
        wiring.register()

    def tearDown(self):
        _reset()

    def test_handlers_registered(self):
        self.assertIn("tmdb", worker.kinds())
        self.assertIn("cache_refresh", worker.kinds())
        self.assertIs(hooks.enqueue_tmdb, wiring._enqueue_tmdb)

    def test_hook_with_key_enqueues_tmdb_job(self):
        with mock.patch.object(config, "TMDB_ACCESS_KEY", "0123456789abcdef0123456789abcdef"):
            hooks.enqueue_tmdb("c1")
            hooks.enqueue_tmdb("c1")  # same key while queued: no-op
        rows = db.query("SELECT kind, key, payload FROM jobs")
        self.assertEqual([(r["kind"], r["key"]) for r in rows], [("tmdb", "tmdb:c1")])
        self.assertEqual(store.get(db.query_one("SELECT id FROM jobs")["id"])["payload"], {"canonical_id": "c1"})

    def test_hook_without_key_enqueues_nothing(self):
        with mock.patch.object(config, "TMDB_ACCESS_KEY", ""):
            self.assertIsNone(hooks.enqueue_tmdb("c1"))
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM jobs")["n"], 0)

    def test_jobs_gc_calls_llm_gc_sessions(self):
        jobs.gc_hooks.clear()
        with mock.patch("app.core.llm.client.gc_sessions", return_value=2) as gc:
            wiring.register()
            res = jobs._jobs_gc({"id": 0}, mock.Mock())
        gc.assert_called_once_with()
        self.assertIn("deleted", res.result)

    def test_cache_refresh_handler_rebuilds_snapshot(self):
        with mock.patch("app.core.catalog.cache.refresh", return_value=mock.Mock(items=[1, 2])) as rf:
            res = wiring._cache_refresh_job({"id": 0}, mock.Mock())
        rf.assert_called_once_with()
        self.assertEqual(res.result, {"items": 2})


class PlayCtxWiringTests(unittest.TestCase):
    def setUp(self):
        _reset()
        patcher = mock.patch.object(netguard, "_addresses", _fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)
        wiring.register()

    def tearDown(self):
        _reset()

    def _site(self, *, commit: bool):
        sid = sitemap_store.create_site("https://fakesite.test/", "Fake")
        data = fake_map()
        data["fetch"] = {"mode": "browser"}
        sitemap_store.save_draft(sid, data)
        if commit:
            sitemap_store.commit(sid, created_by="admin")
        return sid

    def test_ctx_from_active_site_map(self):
        sid = self._site(commit=True)
        ctx = play_hooks.ctx_for(sid)
        self.assertIsInstance(ctx, PlayCtx)
        self.assertEqual(ctx.base_url, "https://fakesite.test")
        self.assertEqual(ctx.resolvers, [{"type": "iframe", "selector": "div.video iframe"}])
        self.assertEqual(ctx.gate.get("require"), "player")
        self.assertTrue(ctx.gate.get("blocked"))
        self.assertEqual(ctx.mode, "browser")

    def test_draft_only_site_still_has_resolvers(self):
        sid = self._site(commit=False)
        self.assertTrue(wiring._play_ctx(sid).resolvers)

    def test_unknown_site_gives_empty_ctx(self):
        self.assertIsNone(wiring._play_ctx("nosuchsite"))
        ctx = play_hooks.ctx_for("nosuchsite")
        self.assertEqual((ctx.resolvers, ctx.base_url), ([], ""))


if __name__ == "__main__":
    unittest.main()
