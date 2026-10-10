import _sandbox  # noqa: F401  (must be first)

import unittest
from types import SimpleNamespace
from unittest import mock

from app.control.sitemap import schema as map_schema
from app.control.stages import a4_episodes, prompts_a4_a7
from app.control.stages.base import Finding
from app.core.extract import episodes as x_episodes
from app.core.llm import parse as llm_parse

BASE = "https://ornekdizi.test"
TITLE = BASE + "/diziler/mavi-kus-izle/"


def row(path, text="Bölüm"):
    return f'<div class="bolum"><a href="{path}">{text}</a></div>'


def page(rows, extra=""):
    return f"<html><body><div class='liste'>{''.join(rows)}</div>{extra}</body></html>"


OWN = [row(f"/mavi-kus-{n}-bolum-izle-full-tek-parca/") for n in range(1, 13)]
SPEC = {"row_selector": "div.bolum", "episode_url_regex": r"^/(?P<slug>[^/]+?)-(?P<episode>\d+)-bolum(?:-[^/]*)?/?$",
        "series_slug_regex": r"^/diziler/([^/]+)/?$"}

MAP = {"base_url": BASE, "pages": {
    "title": {"url_pattern": "^/diziler/", "key": {"regex": r"^/diziler/([^/]+)/?$"},
              "type": {"from": "url", "map": {"diziler": "series"}}},
    "episodes": {"where": "title_page", "row_selector": "div.bolum",
                 "numbering": {"from": "url", "regex": r"^/([^/]+?)-(\d+)-bolum(?:-[^/]*)?/?$", "episode": 2,
                               "default_season": 1}}}}


class OwnershipTests(unittest.TestCase):
    def test_suffixed_title_key_counts_own_rows_and_a_stranger_row_only_warns(self):
        inv = x_episodes.series_inventory(page(OWN + [row("/kara-orman-40-bolum-izle/")]), TITLE, SPEC)
        self.assertEqual(len(inv["video_sources"]), 13)   # the page's own list: every row is counted
        self.assertEqual(inv["ownership"]["foreign"], 1)
        self.assertIn("başka diziye benziyor", " ".join(inv["warnings"]))
        self.assertIn("'kara-orman'", inv["ownership"]["example"])

    def test_link_scan_drops_other_series_with_an_example(self):
        spec = {**SPEC, "row_selector": "div.yok"}   # rows find nothing: the link scan filters by the series name
        inv = x_episodes.series_inventory(page(OWN + [row("/kara-orman-3-bolum-izle/")]), TITLE, spec)
        self.assertEqual(len(inv["video_sources"]), 12)
        self.assertIn("örn. bölüm 'kara-orman'", " ".join(inv["warnings"]))

    def test_full_url_regex_is_matched_against_the_whole_address(self):
        spec = {**SPEC, "episode_url_regex": r"^https?://[^/]+/.+?-(?P<episode>\d+)-bolum"}
        inv = x_episodes.series_inventory(page(OWN), TITLE, spec)
        self.assertEqual(len(inv["video_sources"]), 12)
        self.assertEqual(inv["ownership"]["foreign"], 0)

    def test_a4_measurement_warns_but_passes_on_strangers(self):
        html = page(OWN[:4] + [row(f"/kara-orman-{n}-bolum-izle/") for n in range(5, 9)])
        second = BASE + "/diziler/sari-ev-izle/"
        pages = {TITLE: SimpleNamespace(url=TITLE, final_url=TITLE, html=html),
                 second: SimpleNamespace(url=second, final_url=second, html=page([row("/sari-ev-1-bolum-izle/"),
                                                                                   row("/sari-ev-2-bolum-izle/")]))}
        ep = lambda u: SimpleNamespace(url=u, final_url=u, html="<h1>bölüm</h1>", status=200)   # noqa: E731  (A4 opens episodes)
        ctx = SimpleNamespace(samples=[TITLE, second], base_url=BASE,
                              get=lambda u, kind="page": pages.get(u) or (ep(u) if "-bolum-izle" in u else None),
                              merged=lambda patch: MAP)
        m = a4_episodes.EpisodesStage().verify(ctx, {})
        self.assertTrue(m.ok)
        self.assertTrue(m.warn)
        self.assertIn("başka diziye benziyor", " ".join(m.notes))

    def test_ambiguous_groups_go_into_the_llm_prompt(self):
        other = "".join(f'<li class="oneri"><a href="/kara-orman-{n}-bolum-izle/">x</a></li>' for n in range(1, 5))
        html = page(OWN, f"<ul class='benzer'>{other}</ul>")
        groups = a4_episodes.link_groups(html, TITLE, BASE, slug="mavi-kus-izle",
                                         regex=r"^/(?P<slug>[^/]+?)-(?P<episode>\d+)-bolum", title_pattern="^/diziler/")
        self.assertTrue(a4_episodes.ambiguity(groups, {"rows": 12, "foreign": 0}))
        stage = a4_episodes.EpisodesStage()
        finding = Finding({"pages": {"episodes": MAP["pages"]["episodes"]}}, {"ask_llm": "2 grup", "link_groups": groups}, 0.8)
        ctx = SimpleNamespace(site_id="ornek", base_url=BASE)
        with mock.patch.object(prompts_a4_a7.site_brief, "brief", lambda site_id: "-"):
            text = prompts_a4_a7.full(stage, ctx, finding, "", None, [])
        self.assertIn("Candidate link groups", text)
        self.assertIn("li.oneri a", text)
        self.assertIn("which one is THIS series' episode list", text)


class LenientReplyTests(unittest.TestCase):
    def test_double_quoted_regex_escape_is_repaired(self):
        reply = '```yaml\npages:\n  episodes:\n    numbering: {from: url, regex: "^/(.+?)-(\\d+)-bolum", episode: 2}\n```'
        data, err = llm_parse.extract_yaml(reply)
        self.assertIsNone(err)
        self.assertEqual(data["pages"]["episodes"]["numbering"]["regex"], r"^/(.+?)-(\d+)-bolum")
        self.assertTrue(llm_parse.LAST_NOTES)

    def test_unknown_cast_is_dropped_with_a_note_and_date_tr_kept(self):
        data = {"pages": {"episodes": {"fields": {"air_date": {"selector": ".t", "cast": "date_xx"},
                                                  "title": {"selector": ".n", "cast": "date_tr"}}}}}
        notes = []
        map_schema.normalize_rules(data, notes)
        fields = data["pages"]["episodes"]["fields"]
        self.assertNotIn("cast", fields["air_date"])
        self.assertEqual(fields["title"]["cast"], "date_tr")
        self.assertIn("date_xx", notes[0])


if __name__ == "__main__":
    unittest.main()
