"""The "double backslash" regex mistake: ``\\d`` in a SINGLE-quoted yaml scalar is a literal backslash + ``d`` (matches no digit; the
trdiziizle onboarding case: ``rows_matched`` > 0, ``rows_accepted: 0``). A NEW site (onboarding sandbox) fails on it with a hint; loading an
existing site only logs it; the series engine's ``rejected_by`` quotes the whole regex and names the suspicion. Network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import test_onboard_sandbox as tsb
from app.scraper import config as scfg, series_generic

BAD_BLOCK = tsb.SERIES_PAGE_BLOCK.replace("\\d", "\\\\d")   # the yaml now says '(?P<season>\\d+)' in single quotes
WPFP = (Path(__file__).parent / "fixtures" / "trdiziizle_series_wpfp.html").read_text(encoding="utf-8")
WPFP_URL = "https://www.trgen.test/dizi/halka/"
LONG_RE = r"^/dizi/(?P<slug>[^/]+)/(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum-izle-full-tek-parca-hd-turkce-altyazili-yerli-dizi-izle/?$"


class LintFunctionTest(unittest.TestCase):
    def test_the_pair_plus_a_class_letter_is_flagged_and_nothing_else(self):
        for bad in (r"\\d+", r"a\\.b", r"x\\wy", r"\\S", r"\\b"):
            self.assertIsNotNone(scfg.double_backslash_hint(bad), bad)
        for good in (r"\d+", r"a\.b", r"\\\\d", r"\\\d", r"[^/]+", "", None):
            self.assertIsNone(scfg.double_backslash_hint(good), good)

    def test_every_regex_key_of_the_yaml_is_checked(self):
        data = {"series_page": {"episode_url_regex": r"\\d", "series_url_regex": r"\d", "same_series_regex": r"^/{slug}-\\d",
                                "fields": {"url": {"regex": r"(\\d+)"}}},
                "normalize": {"key": {"regex": [r"^/ok/(\d+)", r"^/bad/(\\d+)"]}},
                "collections": [{"id": "trending_x", "fields": {"title": {"regex": r"\\w+"}}}],
                "blocked": [{"on": "episode_page", "html_regex": r"telif\\s+engeli"}],
                "search": {"fields": {"title": {"regex": r"\d"}}}}
        errors = scfg.regex_lint(data)
        where = sorted(e.split(":")[0] for e in errors)
        self.assertEqual(where, ["blocked[0].html_regex", "collections[0].fields.title.regex", "normalize.key.regex[1]",
                                 "series_page.episode_url_regex", "series_page.fields.url.regex", "series_page.same_series_regex"])
        self.assertEqual(scfg.regex_lint({"series_page": {"episode_url_regex": r"\d+"}}), [])
        self.assertEqual(scfg.regex_lint(None), [])


class SandboxValidationTest(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(tsb.SERIES_LIST_HTML)
        self.detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def run_config(self, yaml_text):
        with tsb.public_dns():
            got = self.post("/test_config", {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_double_backslash_episode_regex_is_an_error_with_a_hint(self):
        out = self.run_config(tsb.series_yaml(BAD_BLOCK))
        mine = [e for e in out["errors"] if "series_page.episode_url_regex" in e and "çift ters eğik çizgi" in e]
        self.assertEqual(len(mine), 1, out["errors"])
        self.assertIn("tek tırnaklı yaml'da `\\d` yaz", mine[0])
        self.assertFalse(out["valid"])
        self.assertFalse(out["criteria"]["config_errors"]["ok"])
        hint = next(f["hint"] for f in out["failing"] if f["criterion"] == "config_errors")
        self.assertIn("çift ters eğik çizgi", hint)
        self.assertIn("series_page.episode_url_regex", hint)

    def test_the_right_single_backslash_has_no_regex_error(self):
        out = self.run_config(tsb.series_yaml())
        self.assertEqual([e for e in out["errors"] if "ters eğik" in e], [])


class SeriesDiagnosticsTest(unittest.TestCase):
    SPEC = {"row_selector": "ul.bolumler li", "fields": {"url": {"selector": "a[href]", "attr": "href"}}}

    def inventory(self, regex):
        return series_generic.series_inventory(WPFP, WPFP_URL, {**self.SPEC, "episode_url_regex": regex})

    def test_the_rejection_names_the_whole_regex_and_the_double_backslash_suspicion(self):
        result = self.inventory(LONG_RE)
        self.assertEqual((result["diagnostics"]["rows_matched"], result["diagnostics"]["rows_accepted"]), (3, 0))
        reason = result["diagnostics"]["first_rows"][0]["rejected_by"]
        self.assertIn(LONG_RE, reason)   # the regex is never cut
        self.assertIn("ile uymadı", reason)
        self.assertIn("yol '", reason)
        self.assertIn("çift ters eğik çizgi şüphesi", reason)
        self.assertLessEqual(len(reason), series_generic.REJECT_MAX)

    def test_no_suspicion_hint_for_a_plain_mismatch(self):
        reason = self.inventory(r"^/(?P<slug>[^/]+)/nothing-(?P<episode>\d+)$")["diagnostics"]["first_rows"][0]["rejected_by"]
        self.assertIn("ile uymadı", reason)
        self.assertNotIn("şüphesi", reason)

    def test_the_sandbox_report_keeps_the_whole_regex_inside_the_size_budget(self):
        from app.routers import onboard_sandbox as sb
        diag = self.inventory(LONG_RE)["diagnostics"]
        capped = sb._cap_entry(diag)
        self.assertIn(LONG_RE, capped["first_rows"][0]["rejected_by"])
        self.assertLessEqual(len(json.dumps(capped, ensure_ascii=False)), sb.DIAG_ENTRY_BYTES + 150)


class LoadOnlyWarnsTest(unittest.TestCase):
    def test_loading_a_site_with_the_mistake_logs_a_warning_and_still_loads(self):
        data = {"site_id": "dbl", "version": 1, "base_url": "https://dbl.example", "list_url": "https://dbl.example/",
                "series_page": {"row_selector": "li", "episode_url_regex": r"^/(?P<episode>\\d+)-bolum"}}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "dbl.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
            scfg._warned.clear()
            with patch.object(scfg, "CONFIG_DIR", tmp), self.assertLogs("scraper.config", level="WARNING") as logs:
                cfg = scfg.load_site("dbl")
            self.assertEqual(cfg.site_id, "dbl")
            self.assertEqual(cfg.series_page["episode_url_regex"], data["series_page"]["episode_url_regex"])   # not skipped, not rewritten
            self.assertTrue(any("çift ters eğik çizgi" in line for line in logs.output), logs.output)


if __name__ == "__main__":
    unittest.main()
