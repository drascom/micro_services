import _sandbox  # noqa: F401  (must be first)

import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from app import config, db
from app.api import _finder
from app.control import jobs, notes, problems
from app.control.jobs import store as jobs_store, worker
from app.control.problems import actions, confirm, diagnose, intake, run, store
from app.core import events
from app.core.catalog import cache, notify as catalog_notify, write
from app.core.fetch.base import FetchError
from app.core.play import ctx as playctx, hooks as play_hooks, hostrules, resolve, sources, streamdiag
from app.core.play.providers import recipes, registry

from test_t04b_api import sample, shape

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fakesite")
BASE = "https://www.fakesite.test"
SITE = "fake"
NOW = 2_000_000_000


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class FakeTransport:
    def __init__(self, pages):
        self.pages = pages

    def page(self, ctx, url, timeout=None):
        if url not in self.pages:
            raise FetchError("http_404", "missing", 404, url)
        return self.pages[url]


def fake_provider():
    return registry.Provider("fakehost", lambda u: "//player.vidhost.test/" in u,
                             lambda u, referer="": {"provider": "FakeHost", "streams": [
                                 {"url": "https://cdn.vidhost.test/v/ko13.mp4", "type": "mp4", "quality": "720p"}]},
                             "test", ("player.vidhost.test",))


def referer_probe(url, headers, kind, now=None):
    """The stream host answers only with a Referer."""
    code = "reachable" if any(k.lower() == "referer" for k in headers or {}) else "forbidden"
    return {"code": code, "http": 200 if code == "reachable" else 403, "ct": "video/mp4", "ms": 1, "why": "", "body": ""}


class Crash(BaseException):
    """The process dies mid-action (not an Exception: the runner cannot turn it into a Retry)."""


class ProblemCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        cls._old = config.DB_PATH
        config.DB_PATH = os.path.join(cls.tmp, "t13.db")
        db.close_thread()
        db.init()
        events.clear()
        problems.register()

    @classmethod
    def tearDownClass(cls):
        events.clear()
        play_hooks.bind_ctx(None)
        cache.reset()
        db.close_thread()
        config.DB_PATH = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for table in ("problems", "problem_events", "problem_watchers", "jobs", "notifications", "admin_notes", "blocked_pages",
                      "stream_host_rules", "video_sources", "library_items", "source_items"):
            db.execute(f"DELETE FROM {table}")
        resolve.reset_caches()
        sources.reset_caches()
        self.pages = {}
        self.clock = [NOW]
        play_hooks.bind_ctx(lambda site: playctx.make_ctx(site, BASE, resolvers=[{"type": "iframe", "selector": ".video iframe"}],
                                                          fetch=FakeTransport(self.pages)))
        self.patches = [mock.patch.object(registry, "CODE_PROVIDERS", [fake_provider()]),
                        mock.patch.object(recipes, "load_all", lambda: []),
                        mock.patch.object(store, "now", lambda: self.clock[0]),
                        mock.patch.object(sources, "first_sight", lambda *a, **k: 0),
                        mock.patch.object(streamdiag, "probe", referer_probe)]
        for p in self.patches:
            p.start()
        self.runner = worker.Runner(threads=1, owner="t13")
        self.cid = write.upsert_title(SITE, {"key": "dizi/kara-orman", "type": "series", "title": "Kara Orman",
                                             "source_url": BASE + "/dizi/kara-orman/"})
        write.upsert_episodes(SITE, "dizi/kara-orman", [
            {"season": 1, "episode": n, "url": f"{BASE}/dizi/kara-orman/1-sezon-{n}-bolum/"} for n in (1, 2, 3)])

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def ep(self, n):
        return f"{self.cid}:s1:e{n}"

    def page(self, n, html):
        self.pages[f"{BASE}/dizi/kara-orman/1-sezon-{n}-bolum/"] = html

    def drain(self, rounds=10, later=0):
        for _ in range(rounds):
            if self.runner.run_one(now=time.time() + later) is None:
                break

    def problem(self, n):
        return store.by_fingerprint(store.episode_fp(self.cid, self.ep(n)))


class IntakeTests(ProblemCase):
    def test_stream_missing_opens_one_problem_and_adds_watchers(self):
        sources.streams(self.cid, self.ep(1), profile_id="p1")     # page missing -> stream_missing
        sources.streams(self.cid, self.ep(1), profile_id="p2")
        rows = store.list({"canonical_id": self.cid})
        self.assertEqual(len(rows), 1)
        got = store.get(rows[0]["id"])
        self.assertEqual(sorted(w["profile_id"] for w in got["watchers"]), ["p1", "p2"])
        self.assertEqual(got["deadline_at"], NOW + 900)
        self.assertEqual(_finder.client_state(self.cid, self.ep(1), "p1"), {"state": "searching"})
        self.assertEqual(_finder.status(self.cid, self.ep(1))["state"], "searching")
        self.assertEqual(len(jobs_store.list_jobs(state="queued")), 1)

    def test_device_codes_open_nothing_and_illegal_transition_raises(self):
        events.emit("playback_failed", source_id="x", canonical_id=self.cid, episode_id=self.ep(1), profile_id="p1",
                    code="decode", engine="avplay", detail="")
        self.assertEqual(store.list(), [])
        p, _ = store.open_or_get(store.episode_fp(self.cid, self.ep(1)), canonical_id=self.cid, episode_id=self.ep(1))
        with self.assertRaises(store.InvalidTransition):
            store.transition(p["id"], "resolved", actor="system", kind="x")
        self.assertEqual(store.row(p["id"])["state"], "open")


class FlowTests(ProblemCase):
    def test_host_needs_referer_learns_rule_and_resolves_without_llm(self):
        self.page(1, fixture("episode_iframe.html"))
        intake.report(self.cid, self.ep(1), "p1", origin="playback_failed", data={"code": "network", "engine": "html5"})
        self.drain()
        p = self.problem(1)
        self.assertEqual((p["class"], p["state"], p["resolution"]), ("host_needs_headers", "resolved", "heal"))
        self.assertTrue(hostrules.get("cdn.vidhost.test"))
        items = catalog_notify.unread("p1")["items"]
        self.assertEqual([(i["kind"], i["method"], i["season"], i["episode"]) for i in items], [("source_found", "heal", 1, 1)])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM llm_calls")["n"], 0)
        steps = _finder.status(self.cid, self.ep(1))["steps"]
        self.assertEqual([s["name"] for s in steps], ["retry", "heal"])
        self.assertEqual(shape(sample("source-finder"), _finder.status(self.cid, self.ep(1))), [])
        self.assertEqual(shape(sample("notifications"), catalog_notify.unread("p1")), [])

    def test_placeholder_page_closes_not_public(self):
        self.page(1, '<div class="embed"><iframe src="https://www.fakesite.test/player/telif.html"></iframe></div>')
        intake.report(self.cid, self.ep(1), "p1", origin="stream_missing")
        self.drain()
        p = self.problem(1)
        self.assertEqual((p["class"], p["state"]), ("not_public", "closed_not_public"))
        self.assertTrue(db.query_one("SELECT 1 FROM blocked_pages WHERE site=? AND url LIKE '%1-sezon-1-bolum/'", (SITE,)))
        self.assertEqual([i["kind"] for i in catalog_notify.unread("p1")["items"]], ["source_unavailable"])

    def test_player_trace_without_candidate_waits_for_user(self):
        self.page(1, '<div class="player"><iframe src="https://cdn.other.test/embed/9"></iframe></div>')
        intake.report(self.cid, self.ep(1), "p1", origin="stream_missing")
        self.drain()
        p = self.problem(1)
        self.assertEqual((p["class"], p["page_type"], p["state"]), ("player_not_found", "player", "waiting_user"))
        self.assertEqual(notes.unread()[0]["ref_id"], str(p["id"]))
        self.assertEqual(_finder.client_state(self.cid, self.ep(1), "p1"), {"state": "not_found"})

    def test_two_episodes_same_class_open_a_site_problem(self):
        for n in (1, 2):
            self.page(n, '<div class="player"><iframe src="https://cdn.other.test/embed/9"></iframe></div>')
            intake.report(self.cid, self.ep(n), "p1", origin="stream_missing")
            self.drain()
        site = store.by_fingerprint(store.site_fp(SITE, "player", "player_not_found"))
        self.assertIsNotNone(site)
        self.assertEqual(site["scope"], "site")
        self.assertEqual({self.problem(1)["parent_id"], self.problem(2)["parent_id"]}, {site["id"]})
        self.assertEqual(site["state"], "waiting_user")

    def test_children_wait_with_the_waiting_site_problem(self):
        for n in (1, 2):
            self.page(n, '<div class="player"><iframe src="https://cdn.other.test/embed/9"></iframe></div>')
            intake.report(self.cid, self.ep(n), "p1", origin="stream_missing")
            self.drain()
        site = store.by_fingerprint(store.site_fp(SITE, "player", "player_not_found"))
        self.assertEqual(site["state"], "waiting_user")
        for n in (1, 2):
            self.assertEqual(self.problem(n)["state"], "waiting_user")
            self.assertNotEqual(jobs_store.get_by_key(run.job_key(self.problem(n)["id"]))["state"], "waiting")
            self.assertEqual(_finder.client_state(self.cid, self.ep(n), "p1"), {"state": "not_found"})
        self.assertEqual(catalog_notify.unread("p1")["items"], [])
        # the parent gets its answer later: children are measured again as before
        store.transition(site["id"], "verifying", actor="admin", kind="test")
        done = store.transition(site["id"], "resolved", actor="system", kind="test")
        run.finish_children(done)
        self.assertEqual({self.problem(n)["state"] for n in (1, 2)}, {"verifying"})

    def test_resolved_only_by_measurement(self):
        """The config "changed" (problem moved to verifying) but the page still gives nothing: not resolved."""
        self.page(1, '<div class="player"><iframe src="https://cdn.other.test/embed/9"></iframe></div>')
        p, _ = store.open_or_get(store.episode_fp(self.cid, self.ep(1)), canonical_id=self.cid, episode_id=self.ep(1),
                                 deadline_at=NOW + 900)
        for st in ("diagnosing", "acting", "verifying"):
            store.transition(p["id"], st, actor="admin", kind="test")
        run.enqueue(p)
        self.drain(rounds=1)
        p = store.row(p["id"])
        self.assertNotIn(p["state"], ("resolved", "confirmed"))
        self.assertEqual((p["state"], p["attempts"]), ("open", 1))
        self.assertEqual(p["next_at"], NOW + 60)

    def test_restart_mid_acting_resumes_from_the_step(self):
        self.page(1, fixture("episode_iframe.html"))
        intake.report(self.cid, self.ep(1), "p1", origin="playback_failed", data={"code": "network", "engine": "html5"})
        before = self.problem(1)
        calls = []
        real = diagnose.diagnose
        with mock.patch.object(diagnose, "diagnose", lambda p: calls.append(1) or real(p)):
            with mock.patch.object(actions, "retry_sources", side_effect=Crash()), self.assertRaises(Crash):
                self.runner.run_one()
            job = jobs_store.get_by_key(run.job_key(before["id"]))
            self.assertEqual((job["state"], job["step"], self.problem(1)["state"]), ("running", 2, "acting"))
            self.assertEqual(jobs_store.recover(now=time.time() + 600), 1)   # lease ran out
            self.drain(later=600)
        p = self.problem(1)
        self.assertEqual(calls, [1], "diagnosis is not repeated after the restart")
        self.assertEqual(p["state"], "resolved")
        self.assertEqual(p["deadline_at"], before["deadline_at"])
        self.assertEqual([w["profile_id"] for w in store.watchers(p["id"])], ["p1"])


class DeadlineTests(ProblemCase):
    def test_long_diagnosis_yields_at_the_deadline_and_resumes(self):
        self.page(1, fixture("episode_iframe.html"))
        intake.report(self.cid, self.ep(1), "p1", origin="playback_failed", data={"code": "network", "engine": "html5"})
        pid = self.problem(1)["id"]
        clock = {"late": False}
        real = diagnose.diagnose

        def slow(p):   # the diagnosis eats the job's time
            clock["late"] = True
            return real(p)
        with mock.patch.object(diagnose, "diagnose", slow), \
                mock.patch.object(run, "time", mock.Mock(time=lambda: (10**10 if clock["late"] else time.time()))):
            self.runner.run_one()
        job = jobs_store.get_by_key(run.job_key(pid))
        self.assertEqual((job["state"], job["attempts"], self.problem(1)["state"]), ("queued", 0, "acting"))
        self.drain()
        p = self.problem(1)
        self.assertEqual((p["state"], p["attempts"]), ("resolved", 0))


class ClientTests(ProblemCase):
    def resolved(self, n=1):
        p, _ = store.open_or_get(store.episode_fp(self.cid, self.ep(n)), canonical_id=self.cid, episode_id=self.ep(n),
                                 deadline_at=NOW + 900)
        store.watch(p["id"], "p1", self.cid, self.ep(n))
        for st in ("diagnosing", "acting", "verifying", "resolved"):
            store.transition(p["id"], st, actor="system", kind="test")
        return p["id"]

    def progress(self, pos, n=1):
        events.emit("progress", profile_id="p1", canonical_id=self.cid, episode_id=self.ep(n), position=pos, duration=2400)

    def test_deadline_turns_searching_into_not_found(self):
        intake.report(self.cid, self.ep(1), "p1", origin="stream_missing")
        self.assertEqual(_finder.client_state(self.cid, self.ep(1))["state"], "searching")
        self.clock[0] = NOW + 901
        self.assertEqual(self.problem(1)["state"], "open")
        self.assertEqual(_finder.client_state(self.cid, self.ep(1))["state"], "not_found")

    def test_k9_ten_seconds_confirm_nine_do_not(self):
        pid = self.resolved()
        self.progress(100)
        self.progress(109)
        self.assertEqual(store.row(pid)["state"], "resolved")
        self.progress(110)
        self.assertEqual(store.row(pid)["state"], "confirmed")

    def test_k9_failure_in_between_reopens(self):
        pid = self.resolved()
        self.progress(100)
        events.emit("playback_failed", source_id="x", canonical_id=self.cid, episode_id=self.ep(1), profile_id="p1",
                    code="network", engine="html5", detail="")
        self.progress(130)
        p = store.row(pid)
        self.assertEqual((p["state"], p["attempts"]), ("open", 1))

    def test_unwatched_resolved_closes_after_seven_days(self):
        pid = self.resolved()
        db.execute("DELETE FROM problem_watchers")
        self.clock[0] = NOW + 7 * 86400 + 1
        self.assertEqual(confirm.sweep(), 1)
        self.assertEqual(store.row(pid)["state"], "closed")


class YoutubeTests(ProblemCase):
    def test_youtube_embed_is_a_known_provider_not_a_capability_gap(self):
        self.page(1, '<div class="video"><iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe></div>')
        p, _ = store.open_or_get(store.episode_fp(self.cid, self.ep(1)), canonical_id=self.cid, episode_id=self.ep(1),
                                 deadline_at=NOW + 900)
        verdict = diagnose.diagnose(p)
        self.assertEqual(verdict["class"], "provider_changed", verdict)
        self.assertEqual(verdict["evidence"]["provider"], "youtube")
        self.assertTrue(verdict["evidence"]["opt_in"])
        self.assertNotIn("youtube", [x.name for x in registry.providers()])   # still not in a site page's default pool
        self.assertEqual(diagnose.known_provider("https://vimeo.com/123"), None)


if __name__ == "__main__":
    unittest.main()
