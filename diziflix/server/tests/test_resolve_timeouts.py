"""Live playback time limits for browser-carried candidates: ``player_page`` with ``fetch: browser`` stamps ``timeout``
(``RESOLVE_BROWSER_TIMEOUT``) on its candidates and ``videos._run_candidates`` gives exactly those candidates the longer
budget (per-candidate ``max(RESOLVE_CANDIDATE_TIMEOUT, timeout)``, whole source ``max(RESOLVE_TOTAL_TIMEOUT, longest + 2)``),
while ``RESOLVE_GRACE`` and the limits of candidates without a ``timeout`` stay as they were. Network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import config as app_config
from app.library import videos
from app.scraper import site_extractors
from app.scraper.resolvers import player_page

STREAM = [{"url": "https://cdn.example/a.m3u8", "type": "hls", "quality": "auto", "label": "auto"}]
DETAIL = '<div><iframe src="/player/oynat/abc"></iframe><iframe src="/player/oynat/abc"></iframe><iframe src="/player/oynat/def"></iframe></div>'
PAGE_URL = "https://site.example/film/x"
RULES = [{"regex": r'file:"([^"]+)"'}]


def outcome(streams=True):
    return {"label": "", "provider": "P" if streams else "", "streams": list(STREAM) if streams else [], "duration": 0,
            "error": "" if streams else "yok", "events": [], "ms": 0}


def sleeper(delays, streams=True):
    """``run_one`` double: sleeps ``delays[candidate['url']]`` seconds, then answers."""
    def run_one(raw):
        time.sleep(delays.get(raw["url"], 0))
        return outcome(streams)
    return run_one


def cfg_patch(**values):
    return [patch.object(app_config, key, value) for key, value in values.items()]


class Limits(unittest.TestCase):
    def run_candidates(self, candidates, run_one, **cfg):
        patches = cfg_patch(**cfg)
        for p in patches:
            p.start()
        try:
            started = time.monotonic()
            result = videos._run_candidates(candidates, run_one)
            return result, time.monotonic() - started
        finally:
            for p in reversed(patches):
                p.stop()


class LiveLimitsTest(Limits):
    def limits(self, candidates, **cfg):
        patches = cfg_patch(**cfg)
        for p in patches:
            p.start()
        try:
            return videos.live_limits(candidates)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_candidate_limit_is_the_larger_of_the_live_limit_and_its_own_budget(self):
        pers, total = self.limits([{"url": "a"}, {"url": "b", "timeout": 40.0}], RESOLVE_CANDIDATE_TIMEOUT=12.0, RESOLVE_TOTAL_TIMEOUT=20.0)
        self.assertEqual(pers, [12.0, 40.0])
        self.assertEqual(total, 42.0)                      # longest candidate + 2 s

    def test_total_keeps_the_live_limit_when_it_is_already_long_enough(self):
        pers, total = self.limits([{"url": "b", "timeout": 25}], RESOLVE_CANDIDATE_TIMEOUT=12.0, RESOLVE_TOTAL_TIMEOUT=60.0)
        self.assertEqual((pers, total), ([25.0], 60.0))

    def test_candidates_without_a_budget_or_with_a_smaller_one_change_nothing(self):
        cfg = dict(RESOLVE_CANDIDATE_TIMEOUT=30.0, RESOLVE_TOTAL_TIMEOUT=0.4)
        self.assertEqual(self.limits([{"url": "a"}, {"url": "b"}], **cfg), ([30.0, 30.0], 0.4))
        self.assertEqual(self.limits([{"url": "a", "timeout": 5.0}], **cfg), ([30.0], 0.4))

    def test_garbage_budgets_are_ignored(self):
        cands = [{"timeout": v} for v in (True, "40", -1, 0, None, [40], {})]
        pers, total = self.limits(cands, RESOLVE_CANDIDATE_TIMEOUT=12.0, RESOLVE_TOTAL_TIMEOUT=20.0)
        self.assertEqual((set(pers), total), ({12.0}, 20.0))


class RunCandidatesTest(Limits):
    def test_a_slow_candidate_with_a_budget_finishes(self):
        cands = [{"url": "a", "timeout": 3.0}]
        result, took = self.run_candidates(cands, sleeper({"a": 0.5}), RESOLVE_CANDIDATE_TIMEOUT=0.2, RESOLVE_TOTAL_TIMEOUT=0.3,
                                           RESOLVE_GRACE=5.0)
        self.assertTrue(result[0]["streams"], result)
        self.assertFalse(result[0].get("timed_out"))
        self.assertGreaterEqual(took, 0.45)

    def test_the_same_slow_candidate_without_a_budget_is_cut_at_the_live_limit(self):
        result, took = self.run_candidates([{"url": "a"}], sleeper({"a": 0.6}), RESOLVE_CANDIDATE_TIMEOUT=0.2, RESOLVE_TOTAL_TIMEOUT=5.0,
                                           RESOLVE_GRACE=5.0)
        self.assertTrue(result[0]["timed_out"])
        self.assertEqual(result[0]["streams"], [])
        self.assertIn("aday süresi (0.2 sn) doldu", result[0]["error"])
        self.assertLess(took, 0.5)

    def test_a_budget_below_the_live_limit_does_not_shorten_it(self):
        result, _ = self.run_candidates([{"url": "a", "timeout": 0.05}], sleeper({"a": 0.3}), RESOLVE_CANDIDATE_TIMEOUT=2.0,
                                        RESOLVE_TOTAL_TIMEOUT=5.0, RESOLVE_GRACE=5.0)
        self.assertTrue(result[0]["streams"])

    def test_timed_out_message_shows_the_real_candidate_limit(self):
        cands = [{"url": "slow-browser", "timeout": 0.4}, {"url": "slow-http"}]
        result, took = self.run_candidates(cands, sleeper({"slow-browser": 1.2, "slow-http": 1.2}), RESOLVE_CANDIDATE_TIMEOUT=0.1,
                                           RESOLVE_TOTAL_TIMEOUT=0.2, RESOLVE_GRACE=5.0)
        self.assertIn("aday süresi (0.1 sn) doldu", result[1]["error"])
        self.assertIn("aday süresi (0.4 sn) doldu", result[0]["error"])
        self.assertTrue(0.35 < took < 1.0, took)           # whole source waited for the budgeted one (0.4 + 2 s limit not reached)

    def test_total_limit_grows_with_the_longest_budget(self):
        # live total 0.2 s would end the run before the 0.5 s candidate answers; its budget (3 s) makes the total 5 s
        result, _ = self.run_candidates([{"url": "a", "timeout": 3.0}, {"url": "b"}], sleeper({"a": 0.5}),
                                        RESOLVE_CANDIDATE_TIMEOUT=0.1, RESOLVE_TOTAL_TIMEOUT=0.2, RESOLVE_GRACE=5.0)
        self.assertTrue(result[0]["streams"])
        self.assertTrue(result[1]["streams"])              # b answered at once

    def test_a_quick_candidate_with_streams_cuts_the_slow_browser_candidate_after_the_grace(self):
        cands = [{"url": "fast"}, {"url": "browser", "timeout": 10.0}]
        result, took = self.run_candidates(cands, sleeper({"browser": 1.5}), RESOLVE_CANDIDATE_TIMEOUT=1.0, RESOLVE_TOTAL_TIMEOUT=2.0,
                                           RESOLVE_GRACE=0.2)
        self.assertTrue(result[0]["streams"])
        self.assertTrue(result[1]["timed_out"])
        self.assertIn("ilk akıştan sonra 0.2 sn bekleme süresi doldu", result[1]["error"])
        self.assertLess(took, 1.0)

    def test_a_browser_candidate_that_answers_first_is_kept_and_others_get_the_grace(self):
        cands = [{"url": "browser", "timeout": 3.0}, {"url": "slow"}]
        result, _ = self.run_candidates(cands, sleeper({"browser": 0.1, "slow": 1.5}), RESOLVE_CANDIDATE_TIMEOUT=1.0,
                                        RESOLVE_TOTAL_TIMEOUT=2.0, RESOLVE_GRACE=0.2)
        self.assertTrue(result[0]["streams"])
        self.assertTrue(result[1]["timed_out"])

    def test_candidates_without_a_budget_keep_the_old_total_limit(self):
        # per 30 s, total 0.4 s: the total still wins (nothing was lengthened)
        result, took = self.run_candidates([{"url": "a"}, {"url": "b"}], sleeper({"a": 1.5, "b": 1.5}), RESOLVE_CANDIDATE_TIMEOUT=30.0,
                                           RESOLVE_TOTAL_TIMEOUT=0.4, RESOLVE_GRACE=5.0)
        self.assertTrue(all(o["timed_out"] for o in result))
        self.assertLess(took, 1.0)
        self.assertIn("toplam süre (0.4 sn) doldu", result[0]["error"])


class DiscoverStampTest(unittest.TestCase):
    PARAMS = {"selector": "iframe", "extract": RULES}

    def discover(self, **extra):
        return player_page.discover(None, DETAIL, PAGE_URL, {**self.PARAMS, **extra})

    def test_browser_candidates_carry_the_browser_budget(self):
        with patch.object(app_config, "RESOLVE_BROWSER_TIMEOUT", 7.5):
            found = self.discover(fetch="browser")
        self.assertEqual([c["timeout"] for c in found], [7.5, 7.5])
        self.assertEqual([c["url"] for c in found], ["https://site.example/player/oynat/abc", "https://site.example/player/oynat/def"])

    def test_http_candidates_carry_no_timeout(self):
        for extra in ({}, {"fetch": "http"}):
            found = self.discover(**extra)
            self.assertTrue(found)
            self.assertTrue(all("timeout" not in c for c in found), found)

    def test_default_budget_is_40_seconds_and_at_least_one(self):
        self.assertEqual(app_config.RESOLVE_BROWSER_TIMEOUT, 40.0)
        self.assertGreaterEqual(app_config.RESOLVE_BROWSER_TIMEOUT, 1.0)


class CandidateKeyAndDispatcherTest(unittest.TestCase):
    def cfg(self, **item):
        return SimpleNamespace(site_id="site", base_url="https://site.example", providers=None, use_site_module=False,
                               fetch_mode="http", data={},
                               resolvers=[{"type": "player_page", "selector": "iframe", "extract": RULES, **item}])

    def test_candidate_key_ignores_the_timeout(self):
        base = {"url": "https://site.example/player/oynat/abc", "label": "Player"}
        self.assertEqual(videos._candidate_key(base), videos._candidate_key({**base, "timeout": 40.0}))

    def test_dispatcher_still_dedupes_and_stamps_with_a_timeout_on_the_candidates(self):
        found = site_extractors.discover("site", DETAIL, PAGE_URL, cfg=self.cfg(fetch="browser"))
        self.assertEqual(len(found), 2)                    # the duplicate iframe is dropped
        for cand in found:
            self.assertEqual((cand["resolver"], cand["resolver_type"], cand["timeout"]), (0, "player_page", app_config.RESOLVE_BROWSER_TIMEOUT))
        plain = site_extractors.discover("site", DETAIL, PAGE_URL, cfg=self.cfg())
        self.assertEqual(len(plain), 2)
        self.assertTrue(all("timeout" not in c for c in plain))


if __name__ == "__main__":
    unittest.main()
