"""Detail `hydrating` flag + `?poll=1` (detail polling of the clients). No network, no real hydrate work."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import _contract_schema as S
import _contract_seed as seed
from app.routers import detail as dmod


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class StubBase(unittest.TestCase):
    """The real detail router over a stubbed catalogue (`state` = what rows.detail returns; library source)."""
    source = "library"

    def setUp(self):
        self.state = {"overview": ""}
        self.checked, self.prefetched = [], []
        snap = type("S", (), {"source": self.source})()
        for p in (patch.object(dmod.rows, "detail", side_effect=self.rows_detail),
                  patch.object(dmod.rows, "detail_actions", return_value=[{"kind": "play_movie", "item_id": "m1"}]),
                  patch.object(dmod.cache, "get", return_value=snap),
                  patch.object(dmod, "_check_trailer", side_effect=self.checked.append),
                  patch.object(dmod, "_prefetch", side_effect=lambda d, p: self.prefetched.append(d["id"])),
                  patch.object(dmod, "_inventory_due", return_value=False),
                  patch.object(dmod.config, "RESOLVE_PREFETCH", True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(dmod._inflight.clear)
        self.addCleanup(dmod._seasons_checked.clear)
        app = FastAPI()
        app.include_router(dmod.router)
        self.client = TestClient(app)

    def rows_detail(self, item_id, profile):
        if item_id != "m1":
            return None
        return {"id": "m1", "type": "movie", "title": "Film", "overview": self.state["overview"],
                "seasons": [], "tmdb_id": None, "actions": []}

    def get(self, path="/api/detail/m1"):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class HydratingFlagTests(StubBase):
    def test_true_when_this_request_started_the_hydrate(self):
        with patch.object(dmod, "schedule_hydrate", return_value=True) as sched:
            d = self.get()
        sched.assert_called_once_with("m1", False)
        self.assertIs(d["hydrating"], True)

    def test_true_when_a_hydrate_is_already_running(self):
        dmod._inflight.add("m1")
        with patch.object(dmod, "schedule_hydrate", return_value=False):
            self.assertIs(self.get()["hydrating"], True)

    def test_false_when_nothing_to_hydrate(self):
        self.state["overview"] = "Var"
        with patch.object(dmod, "schedule_hydrate") as sched:
            d = self.get()
        sched.assert_not_called()
        self.assertIs(d["hydrating"], False)
        self.assertEqual((d["id"], d["title"], d["overview"], d["seasons"]), ("m1", "Film", "Var", []))
        self.assertEqual(d["actions"], [{"kind": "play_movie", "item_id": "m1"}])

    def test_an_unrelated_running_hydrate_is_not_reported(self):
        self.state["overview"] = "Var"
        dmod._inflight.add("other")
        self.assertIs(self.get()["hydrating"], False)

    def test_unknown_item_is_still_404(self):
        self.assertEqual(self.client.get("/api/detail/yok").status_code, 404)
        self.assertEqual(self.client.get("/api/detail/yok?poll=1").status_code, 404)

    def test_the_flag_is_true_for_the_series_hydrate_too(self):
        with patch.object(dmod.rows, "detail", return_value={"id": "s1", "type": "series", "overview": "", "seasons": []}), \
                patch.object(dmod, "schedule_hydrate", return_value=True) as sched:
            d = self.get("/api/detail/s1")
        sched.assert_called_once_with("s1", True)
        self.assertIs(d["hydrating"], True)


class MockSourceTests(StubBase):
    source = "mock"

    def test_mock_source_answers_false_and_never_hydrates(self):
        with patch.object(dmod, "schedule_hydrate") as sched, patch.object(dmod, "schedule_seasons") as seas:
            d = self.get()
            p = self.get("/api/detail/m1?poll=1")
        sched.assert_not_called()
        seas.assert_not_called()
        self.assertEqual(self.checked, [])
        self.assertIs(d["hydrating"], False)
        self.assertIs(p["hydrating"], False)


class PollTests(StubBase):
    def test_poll_starts_no_work_at_all(self):
        # a series holding seasons + tmdb_id would normally schedule the TMDB season pass; an empty movie the hydrate
        with patch.object(dmod, "schedule_hydrate") as hyd, patch.object(dmod, "schedule_seasons") as seas, \
                patch.object(dmod.rows, "detail", side_effect=[
                    {"id": "m1", "type": "movie", "overview": "", "seasons": []},
                    {"id": "s1", "type": "series", "overview": "x", "tmdb_id": 5, "seasons": [{"season": 1}]}]):
            a = self.get("/api/detail/m1?poll=1")
            b = self.get("/api/detail/s1?poll=1")
        hyd.assert_not_called()
        seas.assert_not_called()
        self.assertEqual(self.checked, [], "no trailer check on a poll")
        self.assertEqual(self.prefetched, [], "no prefetch on a poll")
        self.assertIs(a["hydrating"], False)
        self.assertIs(b["hydrating"], False)
        self.assertEqual(a["actions"], [{"kind": "play_movie", "item_id": "m1"}], "actions are still computed")

    def test_the_normal_request_does_start_them(self):
        """Control for the test above: the very same state without ?poll=1 starts the work."""
        with patch.object(dmod, "schedule_hydrate", return_value=True) as hyd:
            self.get()
        hyd.assert_called_once()
        self.assertEqual(self.checked[0]["id"], "m1")
        self.assertEqual(self.prefetched, ["m1"])

    def test_poll_reflects_a_running_hydrate(self):
        dmod._inflight.add("m1")
        with patch.object(dmod, "schedule_hydrate") as hyd:
            self.assertIs(self.get("/api/detail/m1?poll=1")["hydrating"], True)
        hyd.assert_not_called()

    def test_poll_body_equals_the_normal_body_but_for_the_work_it_skips(self):
        self.state["overview"] = "Var"
        self.assertEqual(self.get("/api/detail/m1?poll=1"), self.get("/api/detail/m1"))
        self.assertEqual(self.get("/api/detail/m1?poll=0"), self.get("/api/detail/m1"))

    def test_polling_a_failing_hydrate_never_restarts_it(self):
        calls = []

        def boom(item_id):
            calls.append(item_id)
            raise RuntimeError("site down")

        with patch("app.library.hydrate_item_metadata", boom):
            self.assertIs(self.get()["hydrating"], True)          # the opening request starts it
            self.assertTrue(wait_until(lambda: not dmod._inflight))
            for _ in range(5):                                     # the client polls: no restart, no loop
                self.assertIs(self.get("/api/detail/m1?poll=1")["hydrating"], False)
        self.assertEqual(calls, ["m1"])


class LifecycleTests(StubBase):
    """Real hydrate thread (the library function is faked)."""

    def test_true_while_running_then_false_with_the_new_data(self):
        release = threading.Event()
        order = []

        def slow(item_id):
            release.wait(5)
            self.state["overview"] = "Yeni özet"      # what the hydrate commits to the db
            return True

        def refresh():
            order.append(("refresh", dmod.is_hydrating("m1")))   # still flagged while the snapshot is rebuilt

        with patch("app.library.hydrate_item_metadata", slow), patch.object(dmod.cache, "refresh", refresh):
            first = self.get()
            self.assertIs(first["hydrating"], True)
            self.assertEqual(first["overview"], "")
            self.assertIs(self.get("/api/detail/m1?poll=1")["hydrating"], True)
            self.assertIs(self.get()["hydrating"], True)           # a second open: deduplicated, still true
            release.set()
            self.assertTrue(wait_until(lambda: not dmod._inflight))
            done = self.get("/api/detail/m1?poll=1")
        self.assertIs(done["hydrating"], False)
        self.assertEqual(done["overview"], "Yeni özet")
        self.assertEqual(order, [("refresh", True)], "the id leaves _inflight only AFTER the snapshot refresh")

    def test_series_hydrate_refreshes_before_clearing(self):
        seen = []
        data = {"id": "s1", "type": "series", "overview": "", "seasons": []}
        with patch.object(dmod.rows, "detail", return_value=data), \
                patch("app.library.hydrate_series_item", return_value=True), \
                patch("app.library.seasons.auto_enrich", return_value={"seasons": 2}), \
                patch.object(dmod.cache, "refresh", side_effect=lambda: seen.append(dmod.is_hydrating("s1"))):
            self.assertIs(self.get("/api/detail/s1")["hydrating"], True)
            self.assertTrue(wait_until(lambda: not dmod._inflight))
        self.assertEqual(seen, [True])

    def test_false_again_after_a_failed_hydrate(self):
        with patch("app.library.hydrate_item_metadata", side_effect=RuntimeError("boom")):
            self.assertIs(self.get()["hydrating"], True)
            self.assertTrue(wait_until(lambda: not dmod._inflight))
            d = self.get("/api/detail/m1?poll=1")
        self.assertIs(d["hydrating"], False)
        self.assertEqual(d["overview"], "", "still empty: the client shows 'could not fetch'")

    def test_false_again_when_the_refresh_itself_fails(self):
        with patch("app.library.hydrate_item_metadata", return_value=True), \
                patch.object(dmod.cache, "refresh", side_effect=RuntimeError("db locked")):
            self.assertIs(self.get()["hydrating"], True)
            self.assertTrue(wait_until(lambda: not dmod._inflight))
        self.assertFalse(dmod.is_hydrating("m1"))

    def test_false_again_when_nothing_was_fetched(self):
        refresh = MagicMock()
        with patch("app.library.hydrate_item_metadata", return_value=False), patch.object(dmod.cache, "refresh", refresh):
            self.get()
            self.assertTrue(wait_until(lambda: not dmod._inflight))
        refresh.assert_not_called()
        self.assertIs(self.get("/api/detail/m1?poll=1")["hydrating"], False)


class SeededServerTests(unittest.TestCase):
    """Real app + seeded library (contract seed): the flag is on every detail response, old fields unchanged."""

    @classmethod
    def setUpClass(cls):
        cls._ctx = seed.seeded_client()
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def test_every_seeded_detail_has_a_boolean_false_and_conforms(self):
        for iid in (seed.FILM, seed.SERIES, seed.NOSEASONS, seed.METAONLY, seed.NOPOSTER, seed.DEADTRAILER):
            d = self.c.get("/api/detail/%s?profile=p1" % iid).json()
            self.assertEqual(S.problems(d, S.DETAIL), [], iid)
            self.assertIs(d["hydrating"], False, iid)

    def test_poll_returns_the_full_detail_body(self):
        for iid in (seed.FILM, seed.SERIES, seed.NOSEASONS):
            plain = self.c.get("/api/detail/%s?profile=p1" % iid).json()
            polled = self.c.get("/api/detail/%s?profile=p1&poll=1" % iid).json()
            self.assertEqual(S.problems(polled, S.DETAIL), [], iid)
            self.assertEqual(set(polled), set(plain), iid)
            for k in ("id", "type", "title", "seasons", "similar", "resume", "actions", "in_mylist", "cast", "overview"):
                self.assertEqual(polled[k], plain[k], "%s.%s" % (iid, k))

    def test_running_hydrate_is_reported_by_the_real_endpoint(self):
        dmod._inflight.add(seed.NOSEASONS)
        try:
            for suffix in ("", "&poll=1"):
                d = self.c.get("/api/detail/%s?profile=p1%s" % (seed.NOSEASONS, suffix)).json()
                self.assertIs(d["hydrating"], True)
            self.assertIs(self.c.get("/api/detail/%s?profile=p1" % seed.FILM).json()["hydrating"], False)
        finally:
            dmod._inflight.discard(seed.NOSEASONS)


if __name__ == "__main__":
    unittest.main()
