"""``scraper/sitequality.py``: the site quality grade (standart / karışık / zayıf / bilinmiyor) of an onboarding report, the per-candidate
``source_rows`` the sandbox records, the pipeline view and the sandbox's report block. Pure / network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import time
import unittest
from unittest.mock import patch

from app.routers import onboard_sandbox as sb
from app.scraper import onboard_pipeline, sitequality as sq

from test_onboard_sandbox import DRAFT_YAML   # noqa: E402  (fixture text only)


def src(host, ok=True, known=True, rtype="iframe", challenge=False, provider=""):
    return {"host": host, "resolver_type": rtype, "provider": provider or (host.split(".")[0] if ok and known else ""),
            "known": known, "ok": ok, "challenge": challenge}


def sample(sources, ok=None, error="", **extra):
    ok = any(s["ok"] for s in sources) if ok is None else ok
    return {"key": "k", "locator": "https://s.example/b", "kind": "episode", "ok": ok, "error": error, "streams": [], "sources": sources, **extra}


def playable(*samples):
    return {"checked": len(samples), "resolved": sum(1 for s in samples if s.get("ok")), "skipped": 0, "samples": list(samples)}


STD = [src("ok.ru"), src("vidmoly.net")]
YAML = {"resolvers": [{"type": "iframe", "selector": "x"}, {"type": "embedded_json", "json_path": "a.b[*]"}, {"type": "iframe", "selector": "y"}]}


class GradeTests(unittest.TestCase):
    def test_standard(self):
        q = sq.assess(playable(*[sample(list(STD)) for _ in range(3)]), YAML, [])
        self.assertEqual(q["grade"], "standart")
        self.assertEqual((q["known_ratio"], q["resolved_ratio"], q["mixed_ratio"], q["challenge_ratio"]), (1.0, 1.0, 0.0, 0.0))
        self.assertEqual(q["hosts"], {"ok.ru": 3, "vidmoly.net": 3})
        self.assertEqual(q["resolvers"], ["iframe", "embedded_json"])
        self.assertEqual(q["avg_sources"], 2.0)
        self.assertEqual(q["note"], "")
        self.assertTrue(q["reasons"][0].startswith("3/3 örnek bilinen provider'dan (ok.ru, vidmoly.net)"))
        self.assertTrue(2 <= len(q["reasons"]) <= 4 or len(q["reasons"]) == 1)

    def test_mixed_hosts_and_unknown_sources(self):
        samples = [sample([src("ok.ru")]), sample([src("vidmoly.net")]), sample([src("player.odd.example", known=False, rtype="player_page")])]
        q = sq.assess(playable(*samples), YAML, [])
        self.assertEqual(q["grade"], "karışık")
        self.assertEqual(q["note"], sq.NOTE_MIXED)
        self.assertEqual(q["mixed_ratio"], 0.67)
        self.assertEqual(q["known_ratio"], 0.67)
        self.assertIn("player.odd.example", q["unknown_hosts"])
        text = " ".join(q["reasons"])
        self.assertIn("provider kütüphanesinde yok (player.odd.example)", text)
        self.assertIn("farklı host kümesi", text)
        self.assertLessEqual(len(q["reasons"]), 4)

    def test_weak_unresolved_or_challenged(self):
        bad = sample([src("x.example", ok=False, known=False, challenge=True)], error="no stream [player_page.extract: Cloudflare challenge]")
        q = sq.assess(playable(bad, sample(list(STD)), dict(bad)), YAML, [])
        self.assertEqual((q["grade"], q["resolved_ratio"], q["challenge_ratio"]), ("zayıf", 0.33, 0.67))
        self.assertEqual(q["note"], sq.NOTE_WEAK)
        self.assertIn("2 örnekte Cloudflare / challenge işareti", q["reasons"])
        self.assertTrue(q["reasons"][0].startswith("1/3 örnek çözüldü"))

    def test_one_challenge_among_good_samples_is_mixed(self):
        good = [sample(list(STD)), sample(list(STD))]
        odd = sample([src("ok.ru"), src("p.example", ok=False, known=False)], ok=True)
        blocked = sample([src("ok.ru", ok=False)], ok=False, error="Cloudflare challenge")
        q = sq.assess(playable(*good, odd, blocked), YAML, [])
        self.assertEqual(q["grade"], "karışık")     # 3/4 resolved, one challenge
        self.assertEqual(q["challenge_ratio"], 0.25)

    def test_unknown_when_too_few_samples(self):
        q = sq.assess(playable(sample(list(STD)), sample(list(STD))), YAML, [])
        self.assertEqual((q["grade"], q["n"]), ("bilinmiyor", 2))
        self.assertEqual(q["note"], "")
        self.assertEqual(sq.assess(None)["grade"], "bilinmiyor")
        self.assertEqual(sq.assess({"samples": []})["grade"], "bilinmiyor")
        skipped = playable(*[sample(list(STD)) for _ in range(2)], {**sample(list(STD)), "skipped": True}, {**sample(list(STD)), "blocked": True})
        self.assertEqual(sq.assess(skipped)["n"], 2)   # skipped / blocked samples are not judged

    def test_old_samples_without_sources_fall_back_to_stream_hosts(self):
        old = {"ok": True, "error": "", "streams": [{"type": "hls", "host": "cdn.vidmoly.net", "quality": "auto"}], "providers": ["vidmolly"]}
        q = sq.assess(playable(old, dict(old), dict(old)))
        self.assertEqual((q["grade"], q["hosts"]), ("standart", {"cdn.vidmoly.net": 3}))

    def test_challenge_found_through_the_report_warnings(self):
        failing = {"ok": False, "error": "no stream", "locator": "https://s.example/b9", "sources": [src("a.example", ok=False)], "streams": []}
        q = sq.assess(playable(failing, sample(list(STD)), sample(list(STD))), None,
                      ["playable: https://s.example/b9: the page looks like a Cloudflare challenge"])
        self.assertEqual(q["challenge_ratio"], 0.33)

    def test_duration_match_is_used_when_present_and_ignored_otherwise(self):
        base = [sample(list(STD)) for _ in range(3)]
        self.assertNotIn("duration_ratio", sq.assess(playable(*base)))
        probed = [dict(s, duration_match=False) for s in base[:2]] + [dict(base[2], duration_match={"ok": True})]
        q = sq.assess(playable(*probed))
        self.assertEqual((q["duration_ratio"], q["grade"]), (0.33, "karışık"))
        self.assertTrue(any("Akış süresi" in r for r in q["reasons"]))
        self.assertEqual(sq.assess(playable(*[dict(s, duration_match=True) for s in base]))["grade"], "standart")

    def test_thresholds_are_constants(self):
        samples = [sample([src("ok.ru")]), sample([src("vidmoly.net")]), sample([src("ok.ru")]), sample([src("vidmoly.net")])]
        self.assertEqual(sq.assess(playable(*samples))["grade"], "karışık")
        old = sq.STANDARD_MIXED
        try:
            sq.STANDARD_MIXED = 0.5
            self.assertEqual(sq.assess(playable(*samples))["grade"], "standart")
        finally:
            sq.STANDARD_MIXED = old


class Prov:
    def __init__(self, name, host):
        self.name, self._host = name, host

    def matches(self, url):
        return self._host in url


class SourceRowTests(unittest.TestCase):
    def test_rows_known_by_host_or_by_provider_name_and_challenge(self):
        pool = [Prov("okru", "ok.ru"), Prov("vidmolly", "vidmoly")]
        cands = [{"url": "//ok.ru/videoembed/1", "resolver_type": "embedded_json"}, {"url": "https://wrap.example/p/1"},
                 {"url": "https://odd.example/e", "resolver_type": "player_page"}, {"url": "https://cf.example/e"}]
        outs = [{"streams": [{"url": "u"}], "provider": "okru", "resolver_type": "embedded_json"},
                {"streams": [{"url": "u"}], "provider": "vidmolly"},               # hand-off page that led to a library provider
                {"streams": [{"url": "u"}], "provider": "Player", "resolver_type": "player_page"},
                {"streams": [], "error": "no stream", "events": [{"ok": False, "error": "Cloudflare challenge"}]}]
        rows = sq.source_rows(cands, outs, pool, "https://s.example/b")
        self.assertEqual([(r["host"], r["known"], r["ok"], r["challenge"]) for r in rows],
                         [("ok.ru", True, True, False), ("wrap.example", True, True, False), ("odd.example", False, True, False),
                          ("cf.example", False, False, True)])
        self.assertEqual(rows[0]["resolver_type"], "embedded_json")

    def test_no_pool_and_missing_outcomes_never_raise(self):
        rows = sq.source_rows([{"url": "https://a.example/x"}, "bad", {"url": ""}], [], [])
        self.assertEqual([(r["host"], r["known"], r["ok"]) for r in rows], [("a.example", False, False)])
        self.assertIsInstance(sq.provider_pool(), list)


class ViewAndPipelineTests(unittest.TestCase):
    def report(self, *samples):
        return {"playable": playable(*samples), "site_quality": sq.assess(playable(*samples), YAML, []), "criteria": {}, "warnings": [], "errors": []}

    def test_view_line_and_details(self):
        mixed = self.report(sample([src("ok.ru")]), sample([src("vidmoly.net")]), sample([src("z.example", known=False)]))
        view = sq.view(mixed["site_quality"])
        self.assertTrue(view["line"].startswith("Site kalitesi: karışık - "))
        self.assertEqual(view["note"], sq.NOTE_MIXED)
        self.assertIn("Host'lar", [d["label"] for d in view["details"]])
        self.assertIsNone(sq.view(None))
        self.assertIsNone(sq.view({"grade": "x"}))
        self.assertEqual(sq.line(None), "")

    def test_pipeline_carries_the_quality_view_and_stays_without_one(self):
        rep = self.report(*[sample(list(STD)) for _ in range(3)])
        pipe = onboard_pipeline.build(rep, [], "ready", draft={})
        self.assertEqual(pipe["quality"]["grade"], "standart")
        self.assertEqual(len(pipe["steps"]), 6)
        rep.pop("site_quality")
        self.assertNotIn("quality", onboard_pipeline.build(rep, [], "ready", draft={}))
        self.assertNotIn("quality", onboard_pipeline.build(None, [], "running", draft={}))

    def test_quality_is_not_a_criterion(self):
        weak = self.report(*[sample([src("x.example", ok=False, known=False)], error="no stream") for _ in range(3)])
        self.assertEqual(weak["site_quality"]["grade"], "zayıf")
        with_q = onboard_pipeline.build({**weak, "passed": True}, [], "ready", draft={})
        without = onboard_pipeline.build({k: v for k, v in weak.items() if k != "site_quality"} | {"passed": True}, [], "ready", draft={})
        self.assertEqual(with_q["overall"], without["overall"])   # the grade changes neither the headline nor the state


class EventTests(unittest.TestCase):
    def test_the_ops_event_carries_the_grade(self):
        from app.scraper import onboard, state as sstate
        quality = sq.assess(playable(*[sample(list(STD)) for _ in range(3)]), YAML, [])
        onboard._record({"id": "od_q1", "url": "https://s.example/", "report": {"site_quality": quality, "passed": True}}, "ready", time.monotonic(), 2)
        row = next(r for r in sstate.list_ops("onboard") if r.get("draft_id") == "od_q1")
        self.assertEqual((row["site_quality"]["grade"], row["site_quality"]["reasons"][0][:3]), ("standart", "3/3"))
        onboard._record({"id": "od_q2", "url": "https://s.example/", "report": {}}, "ready", time.monotonic(), 2)
        self.assertNotIn("site_quality", next(r for r in sstate.list_ops("onboard") if r.get("draft_id") == "od_q2"))


class SandboxHookTests(unittest.TestCase):
    """The sandbox records ``sources`` per sample and writes ``report.site_quality`` (no network: fakes at the seams)."""

    def cfg(self):
        return sb._draft_cfg(sb._load_yaml(DRAFT_YAML)[0])

    def test_follow_playback_records_the_candidate_rows(self):
        cands = [{"url": "//ok.ru/videoembed/9", "label": "OKRU", "resolver_type": "embedded_json"}, {"url": "https://odd.example/x", "label": "Odd"}]
        outs = [{"streams": [{"url": "https://cdn.example/a.mp4", "type": "mp4", "quality": "720"}], "provider": "okru", "error": "",
                 "resolver_type": "embedded_json"}, {"streams": [], "provider": "", "error": "no stream", "events": []}]
        got = {"bundle": {"initial_html": "<html></html>"}, "html": "", "meta": {"final_url": "https://demo.example/b"}}
        with patch.object(sb, "_fetch_store", return_value=got), patch("app.scraper.site_extractors.discover", return_value=cands), \
                patch("app.library.videos._run_candidates", side_effect=lambda c, run: outs), \
                patch.object(sq, "provider_pool", return_value=[Prov("okru", "ok.ru")]):
            found = sb._follow_playback(self.cfg(), "https://demo.example/b", time.monotonic() + 30)
        self.assertTrue(found["ok"])
        self.assertEqual([(r["host"], r["known"], r["ok"], r["resolver_type"]) for r in found["sources"]],
                         [("ok.ru", True, True, "embedded_json"), ("odd.example", False, False, "")])

    def test_playable_stage_keeps_sources_and_assess_reads_them(self):
        def follow(cfg, locator, deadline):
            return {"ok": True, "streams": [{"type": "hls", "host": "cdn.example", "quality": "auto"}], "error": "", "candidates": 2, "ms": 5,
                    "timeout": False, "providers": ["okru"], "sources": [src("ok.ru"), src("vidmoly.net")]}

        items = [{"title": f"F{i}", "detail_url": f"/film/{100 + i}/f"} for i in range(5)]
        with patch.object(sb, "_follow_playback", side_effect=follow):
            block = sb._playable_stage(self.cfg(), items, time.monotonic() + 100, [])
        self.assertEqual(len(block["samples"][0]["sources"]), 2)
        self.assertEqual(sq.assess(block, {"resolvers": [{"type": "iframe"}]}, [])["grade"], "standart")


if __name__ == "__main__":
    unittest.main()
