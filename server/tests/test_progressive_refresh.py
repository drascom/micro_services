"""Mid-scan catalogue refresh: the ingest progress hook, the debounced/single-flight cache refresh, consistency.

Network-free: same fakes as test_series_crawl (recorded pages, fake ``fetch.page``, temp DB / state, no TMDB), a fake
clock for the debounce, ``images.start_prewarm`` is always a stub (a real one would download artwork).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import contextlib
import io
import json
import threading
import time
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import autoscan, cache, config, db, images
from app.library import ingest as ingest_module, series_crawl
from app.routers import boot as boot_router, detail as detail_router, ops
from app.scraper import config as scfg
from app.scraper.fetch import FetchError
from app.scraper.runner import RunResult
from app.sources.library import LibrarySource

from test_series_crawl import Base, snw_card


def cards(n):
    """``n`` home cards of different series (each names only its latest episode, like the real feed)."""
    return [dict(snw_card(), title=f"Dizi {i}", detail_url=f"dizi/dizi-{i}-izle/sezon-1/bolum-2", season=1, episode=2,
                 poster_url=f"/uploads/series/dizi-{i}.jpg", year=2020 + i) for i in range(n)]


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class HookBase(Base):
    """test_series_crawl.Base (fake site, temp DB) + a recording progress hook installed for the test."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.addCleanup(ingest_module.set_progress_hook, ingest_module.get_progress_hook())
        ingest_module.set_progress_hook(self.hook)

    def hook(self, stage):
        with closing(db.connect()) as other:  # a NEW connection: only what is committed is visible
            titles = other.execute("SELECT COUNT(*) FROM library_items").fetchone()[0]
        self.calls.append((stage, {"titles": titles, "episodes": len(self.series_rows())}))

    def stages(self):
        return [stage for stage, _ in self.calls]


# --- the ingest side: when the hook is called --------------------------------------------------------------------------

class IngestHookTests(HookBase):
    def test_hook_runs_after_the_title_commit_then_per_series_then_after_the_season_pass(self):
        result = self.ingest([snw_card()])
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.stages(), ["titles", "inventory", "seasons"])
        titles, inventory, seasons = (state for _, state in self.calls)
        self.assertEqual(titles["titles"], 1)          # committed: visible to a separate connection...
        self.assertEqual(titles["episodes"], 1)        # ...but only the card's episode: the inventory is not read yet
        self.assertGreater(inventory["episodes"], 10)  # the series' whole inventory is in the library
        self.assertEqual(seasons["episodes"], inventory["episodes"])

    def test_one_inventory_call_per_series_that_was_written(self):
        self.site.fail["dizi-1-izle"] = FetchError("HTTP 403")  # a failed read changes nothing readable: no call
        self.ingest(cards(4))
        self.assertEqual(self.stages(), ["titles"] + ["inventory"] * 3 + ["seasons"])
        counts = [state["episodes"] for stage, state in self.calls if stage == "inventory"]
        self.assertEqual(counts, sorted(set(counts)))  # every call sees one more series' episodes than the previous

    def test_no_inventory_call_when_the_stage_is_off(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest(cards(2))
        self.assertEqual(self.stages(), ["titles", "seasons"])

    def test_a_failing_hook_never_breaks_the_ingest(self):
        def boom(stage):
            raise RuntimeError("snapshot exploded during " + stage)

        ingest_module.set_progress_hook(boom)
        with self.assertLogs("library.ingest", "WARNING") as logs:
            result = self.ingest([snw_card()])
        self.assertEqual((result["status"], result["error"]), ("success", None))
        self.assertEqual(sum("progress hook" in line for line in logs.output), 3)  # titles, inventory, seasons
        self.assertGreater(len(self.series_rows()), 10)                            # the inventory was still written
        self.assertEqual(result["series_crawl"]["series"], 1)

    def test_a_failing_run_stage_callback_never_stops_the_inventory(self):
        self.seed_series(3)
        seen = []

        def callback():
            seen.append(len(self.series_rows()))
            raise RuntimeError("consumer bug")

        with self.assertLogs("library.series_crawl", "WARNING"):
            stats = series_crawl.run_stage(scfg.load_site("yabancidizi"), "yabancidizi", None, on_progress=callback)
        self.assertEqual((stats["series"], stats["errors"]), (3, 0))
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen, sorted(set(seen)))      # called AFTER each series was written, not before

    def test_no_hook_the_ingest_just_writes(self):
        ingest_module.set_progress_hook(None)
        result = self.ingest([snw_card()])
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.calls, [])
        self.assertGreater(len(self.series_rows()), 10)


class CliTests(Base):
    def test_cli_has_no_hook_and_never_touches_the_catalogue_cache(self):
        from tools import ingest as cli
        self.assertIsNone(ingest_module.get_progress_hook())  # nothing but cache.start() installs one
        result = RunResult("yabancidizi", items=[snw_card()], drift={"drift": False})
        out = io.StringIO()
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]), \
                patch.object(cache, "refresh") as refresh, patch.object(cache, "refresh_progress") as progress, \
                contextlib.redirect_stdout(out):
            code = cli.main(["ingest", "yabancidizi"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())[0]["status"], "success")
        refresh.assert_not_called()
        progress.assert_not_called()
        self.assertGreater(len(self.series_rows()), 10)


# --- the cache side: debounce, single flight, publish order ----------------------------------------------------------------

class RefreshProgressTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.builds = []
        self.prewarms = []
        self.gate = None

        def fake_refresh():
            self.builds.append(self.clock.t)
            if self.gate:
                self.gate()
            snap = MagicMock()
            snap.items = ["item"]
            return snap

        for p in (patch.object(cache, "_now", self.clock), patch.object(cache, "refresh", fake_refresh),
                  patch.object(cache, "_progress_last", None), patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 20.0),
                  patch.object(images, "start_prewarm", side_effect=self.prewarms.append)):
            p.start()
            self.addCleanup(p.stop)

    def at(self, seconds, stage="inventory"):
        self.clock.t = 1000.0 + seconds
        return cache.refresh_progress(stage)

    def test_titles_refresh_at_once_the_rest_is_debounced_to_the_minimum_interval(self):
        self.assertIsNotNone(self.at(0, "titles"))
        for t in (1, 5, 19.9):
            self.assertIsNone(self.at(t), t)          # skipped: returns None, builds nothing
        self.assertEqual(len(self.builds), 1)
        self.assertIsNotNone(self.at(20))             # exactly the interval after the previous one
        self.assertIsNone(self.at(39.9))
        self.assertIsNone(self.at(39.9, "seasons"))   # every non-titles stage obeys the same debounce
        self.assertIsNotNone(self.at(40.0, "seasons"))
        self.assertEqual(self.builds, [1000.0, 1020.0, 1040.0])

    def test_titles_always_refresh_even_right_after_another_refresh(self):
        self.at(0, "titles")
        self.assertIsNotNone(self.at(1, "titles"))    # first sight of a NEW scan's titles is never held back
        self.assertEqual(len(self.builds), 2)

    def test_interval_is_configurable_and_zero_means_no_debounce(self):
        with patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 0.0):
            for t in (0, 0, 0.001):
                self.assertIsNotNone(self.at(t))
        self.assertEqual(len(self.builds), 3)
        self.builds.clear()
        with patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 60.0):
            self.at(100)
            self.assertIsNone(self.at(159))
            self.assertIsNotNone(self.at(160))
        self.assertEqual(len(self.builds), 2)

    def test_artwork_prewarm_only_after_the_first_title_write(self):
        self.at(0, "titles")
        self.at(25)
        self.at(50, "seasons")
        self.assertEqual(self.prewarms, [["item"]])    # later stages leave it to the scan's final refresh_and_prewarm

    def test_one_refresh_at_a_time_a_second_caller_does_not_queue_up(self):
        entered, release = threading.Event(), threading.Event()

        def gate():
            entered.set()
            self.assertTrue(release.wait(5))

        self.gate = gate
        first = threading.Thread(target=cache.refresh_progress, args=("titles",))
        first.start()
        self.assertTrue(entered.wait(5))
        self.assertIsNone(cache.refresh_progress("titles"))   # even a forced stage: returns at once, no 2nd build
        self.assertEqual(len(self.builds), 1)
        release.set()
        first.join(5)
        self.assertFalse(first.is_alive())
        self.gate = None
        self.assertIsNotNone(cache.refresh_progress("titles"))  # the lock was released
        self.assertEqual(len(self.builds), 2)

    def test_a_failed_refresh_raises_and_still_starts_the_debounce_window(self):
        def broken():
            raise RuntimeError("db went away")

        self.gate = broken
        with self.assertRaises(RuntimeError):
            self.at(0, "titles")
        self.gate = None
        self.assertIsNone(self.at(5))                   # not hammered again right away
        self.assertIsNotNone(self.at(20))
        self.assertEqual(self.prewarms, [])             # no snapshot, no prewarm

    def test_start_binds_the_hook_and_stop_unbinds_it(self):
        old_sched, old_ttl = cache._scheduler, config.CACHE_TTL
        cache._scheduler, config.CACHE_TTL = None, 0
        self.addCleanup(ingest_module.set_progress_hook, ingest_module.get_progress_hook())
        ingest_module.set_progress_hook(None)
        try:
            with patch.object(cache, "refresh_and_prewarm"):
                cache.start()
            self.assertIs(ingest_module.get_progress_hook(), cache.refresh_progress)
        finally:
            cache.stop()
            cache._scheduler, config.CACHE_TTL = old_sched, old_ttl
        self.assertIsNone(ingest_module.get_progress_hook())

    def test_stop_leaves_somebody_elses_hook_alone(self):
        self.addCleanup(ingest_module.set_progress_hook, ingest_module.get_progress_hook())
        mine = lambda stage: None  # noqa: E731
        ingest_module.set_progress_hook(mine)
        cache.stop()
        self.assertIs(ingest_module.get_progress_hook(), mine)


class FinalRefreshTests(unittest.TestCase):
    """The end-of-scan refresh of every path still happens, after whatever the progress hook did."""

    def setUp(self):
        self.order = []
        self.addCleanup(ingest_module.set_progress_hook, ingest_module.get_progress_hook())

    def test_admin_scan_ends_with_a_full_refresh(self):
        def fake_ingest(site, trigger="cli"):
            self.order.append(("ingest", trigger))
            ingest_module._progress("titles", site)

        ingest_module.set_progress_hook(lambda stage: self.order.append(("progress", stage)))
        with patch("app.library.ingest_source", fake_ingest), \
                patch.object(ops.cache, "refresh", side_effect=lambda: self.order.append(("final",))), \
                patch.object(ops.sstate, "activity_end") as end:
            ops._bg_scan("yabancidizi")
        self.assertEqual(self.order, [("ingest", "manual"), ("progress", "titles"), ("final",)])
        end.assert_called_once_with("yabancidizi", "scan")

    def test_admin_scan_still_ends_the_activity_when_the_refresh_fails(self):
        with patch("app.library.ingest_source"), patch.object(ops.cache, "refresh", side_effect=RuntimeError("x")), \
                patch.object(ops.sstate, "activity_end") as end:
            ops._bg_scan("yabancidizi")
        end.assert_called_once()

    def test_scheduled_tick_ends_with_prewarming_refresh_after_the_progress_refreshes(self):
        def fake_tick():
            self.order.append("tick")
            cache.refresh_progress("titles")
            return ["yabancidizi"]

        with patch.object(autoscan, "tick", fake_tick), patch.object(cache, "_progress_last", None), \
                patch.object(cache, "refresh", side_effect=lambda: self.order.append("progress") or MagicMock(items=[])), \
                patch.object(images, "start_prewarm"), \
                patch.object(cache, "refresh_and_prewarm", side_effect=lambda: self.order.append("final")):
            cache.ingest_tick()
        self.assertEqual(self.order, ["tick", "progress", "final"])


class SnapshotPublishTests(unittest.TestCase):
    def test_get_never_waits_for_a_build_and_an_older_build_never_replaces_a_newer_one(self):
        started, gate, built = threading.Event(), threading.Event(), []
        old = object()

        class SlowFirst:
            def __init__(self, adapter):
                built.append(self)
                self.no = len(built)
                self.items = []
                if self.no == 1:
                    started.set()
                    assert gate.wait(5)

        for p in (patch.object(cache, "Snapshot", SlowFirst), patch.object(cache, "_snapshot", old),
                  patch.object(cache, "adapter", return_value=SimpleNamespace(name="mock"))):
            p.start()
            self.addCleanup(p.stop)
        slow = threading.Thread(target=cache.refresh)
        slow.start()
        self.assertTrue(started.wait(5))
        got = []
        reader = threading.Thread(target=lambda: got.append(cache.get()))  # a request during the slow build
        reader.start()
        reader.join(2)
        self.assertFalse(reader.is_alive(), "cache.get() blocked behind a running refresh")
        self.assertIs(got[0], old)                     # served from the previous snapshot
        newer = cache.refresh()                        # started later, finishes first
        self.assertIs(cache._snapshot, newer)
        gate.set()
        slow.join(5)
        self.assertFalse(slow.is_alive())
        self.assertEqual([b.no for b in built], [1, 2])
        self.assertIs(cache._snapshot, newer)          # the slow, older build did not overwrite it

    def test_library_snapshot_is_one_consistent_read_view(self):
        """An ingest committing between the queries of a snapshot build is not half-seen, and never blocked."""
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(config, "DB_PATH", _os.path.join(tmp.name, "t.db"))
        p.start()
        self.addCleanup(p.stop)
        db.init()
        seen = []

        class TwoReads:
            def __init__(self, adapter):
                self.items = []
                seen.append(db.query_one("SELECT COUNT(*) n FROM profiles")["n"])
                with closing(db.connect()) as writer, writer:   # a scan commits in the middle of the load
                    writer.execute("INSERT INTO profiles(id,name,is_kids,avatar_seed,created_at) VALUES ('w','W',0,'s',1)")
                seen.append(db.query_one("SELECT COUNT(*) n FROM profiles")["n"])

        fake = SimpleNamespace(name="library")
        with patch.object(cache, "Snapshot", TwoReads), patch.object(cache, "adapter", return_value=fake), \
                patch.object(cache, "_snapshot", None):
            cache.refresh()
        self.assertEqual(seen[0], seen[1])                                       # one view for the whole load
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM profiles")["n"], seen[0] + 1)  # the next read sees it
        self.assertFalse(db._shared().in_transaction)                            # and the view was closed


# --- end to end: real snapshots while a scan runs ------------------------------------------------------------------------------

class ScanWithLiveSnapshotTests(Base):
    def setUp(self):
        super().setUp()
        self.prewarms = []
        self.clock = FakeClock()
        for p in (patch.object(cache, "_adapter", LibrarySource()), patch.object(cache, "_snapshot", None),
                  patch.object(cache, "_progress_last", None), patch.object(cache, "_now", self.clock),
                  patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 20.0),
                  patch.object(images, "start_prewarm", side_effect=self.prewarms.append),
                  patch.object(detail_router, "schedule_hydrate"), patch.object(detail_router, "schedule_seasons")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(ingest_module.set_progress_hook, ingest_module.get_progress_hook())
        ingest_module.set_progress_hook(cache.refresh_progress)
        self.pid = db.query_one("SELECT id FROM profiles LIMIT 1")["id"]  # db.init() seeds default profiles
        self.app = FastAPI()
        self.app.include_router(boot_router.router)
        self.app.include_router(detail_router.router)

    def snapshot_state(self, stage):
        snap = cache.get()
        return stage, len(snap.items), len(snap.episodes)

    def test_the_client_catalogue_grows_during_the_scan_not_only_at_its_end(self):
        states = []
        real = cache.refresh_progress

        def hook(stage):
            real(stage)
            states.append(self.snapshot_state(stage))

        ingest_module.set_progress_hook(hook)
        with patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 0.0):
            self.ingest(cards(4))
        self.assertEqual([s[0] for s in states], ["titles"] + ["inventory"] * 4 + ["seasons"])
        self.assertEqual(states[0][1:], (4, 4))       # every title is served after the write, one card episode each
        episodes = [s[2] for s in states]
        self.assertEqual(episodes, sorted(set(episodes)) + [episodes[-1]])  # each series' inventory shows up in turn
        self.assertEqual(episodes[-1], len(self.series_rows()))  # the last refresh has everything the scan wrote
        self.assertGreater(episodes[-1], episodes[0])
        self.assertEqual(len(self.prewarms), 1)        # artwork prewarm: once, after the title write

    def test_debounce_with_a_fake_clock_while_series_are_read(self):
        calls, builds = [], []
        real_progress, real_snapshot = cache.refresh_progress, cache.Snapshot

        def hook(stage):
            calls.append(stage)
            real_progress(stage)

        def counting(adapter):
            builds.append(self.clock.t)
            return real_snapshot(adapter)

        ingest_module.set_progress_hook(hook)
        self.site.clock = lambda _seconds: self.clock.advance(6)   # every series read takes 6 fake seconds
        with patch.object(cache, "Snapshot", counting):
            self.ingest(cards(8))
        self.assertEqual(len(calls), 10)               # titles + 8 series + seasons
        self.assertGreaterEqual(len(builds), 2)
        self.assertLess(len(builds), len(calls))        # debounced
        self.assertTrue(all(b - a >= 20 for a, b in zip(builds[1:], builds[2:])), builds)  # inventory: >= 20 s apart
        self.assertEqual(builds[0], 1000.0)             # the title refresh came first, at once

    def test_client_requests_never_fail_while_the_snapshot_is_rebuilt_under_them(self):
        self.ingest(cards(1))
        cache.refresh()
        stop, bad, done = threading.Event(), [], {"boot": 0, "detail": 0, "detail_ok": 0}

        def close_thread_connection():                    # tidy: this thread's thread-local db.query() connection
            db._close_quiet(getattr(db._local, "conn", None))
            db._local.conn = None

        def hammer():
            try:
                client = TestClient(self.app, raise_server_exceptions=False)
                while not stop.is_set():
                    r = client.get(f"/api/boot?layout=tv-v1&profile={self.pid}")
                    done["boot"] += 1
                    if r.status_code != 200:
                        bad.append(("boot", r.status_code, r.text[:200]))
                    for row in db.query("SELECT id FROM library_items"):
                        r = client.get(f"/api/detail/{row['id']}?profile={self.pid}")
                        done["detail"] += 1
                        # 404 is honest for a title committed a moment ago whose refresh is still to come; 5xx never is
                        if r.status_code not in (200, 404):
                            bad.append(("detail", row["id"], r.status_code, r.text[:200]))
                        done["detail_ok"] += r.status_code == 200
            finally:
                close_thread_connection()

        def rebuild():                                    # what the periodic job / admin actions do, all the time
            try:
                while not stop.is_set():
                    cache.refresh()
            finally:
                close_thread_connection()

        self.site.clock = lambda _seconds: time.sleep(0.05)   # a slow site: the scan lasts long enough to be hammered
        threads = [threading.Thread(target=hammer), threading.Thread(target=rebuild)]
        for t in threads:
            t.start()
        try:
            with patch.object(config, "INGEST_REFRESH_MIN_INTERVAL", 0.0):
                self.ingest(cards(6))                     # ...while a scan commits and refreshes mid-way
        finally:
            stop.set()
            for t in threads:
                t.join(30)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(bad, [])
        self.assertGreater(done["boot"], 5)
        self.assertGreater(done["detail_ok"], 5)
        self.assertEqual(len(cache.get().items), 6)


if __name__ == "__main__":
    unittest.main()
