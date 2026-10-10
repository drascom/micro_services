import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock

from app.control.stages import search_run
from app.core.catalog import textnorm
from app.core.extract import detail, episodes

BASE = "https://fakesite.test"
SPEC = {"row_selector": "ul.eps li", "fields": {"title": {"selector": "a"}},
        "episode_url_regex": r"^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum"}


def _page(rows: str) -> str:
    return f"<html><body><ul class='eps'>{rows}</ul></body></html>"


class SeasonTail(unittest.TestCase):
    def test_season_and_final_tails(self):
        for raw in ("Uydurma Dizi - 4. Sezon", "Uydurma Dizi 2. Sezon Finali", "Uydurma Dizi Sezon 3",
                    "Uydurma Dizi Final Bölümü", "Uydurma Dizi Sezon Finali"):
            self.assertEqual(textnorm.clean_title(raw), "Uydurma Dizi", raw)
        self.assertEqual(textnorm.clean_title("Final Destination"), "Final Destination")
        self.assertEqual(textnorm.clean_title("Uydurma - 4. Sezon", series=False), "Uydurma - 4. Sezon")


class Episodes(unittest.TestCase):
    def test_double_episode_text_and_group(self):
        html = _page("<li><a href='/kurgu-1-sezon-16-bolum'>16. Bölüm</a></li>"
                     "<li><a href='/kurgu-1-sezon-17-18-bolum'>17-18. Bölüm</a></li>")
        spec = dict(SPEC, episode_url_regex=r"^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?:\d+-)?(?P<episode>\d+)-bolum")
        out = episodes.series_inventory(html, BASE + "/kurgu", spec)
        keys = {v["key"]: v for v in out["video_sources"]}
        self.assertEqual(sorted(keys), ["s1e16", "s1e17", "s1e18"])
        self.assertEqual(keys["s1e18"]["url"], keys["s1e17"]["url"])
        self.assertEqual(keys["s1e18"]["mirror_of"], "s1e17")
        rx = r"^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)(?:-(?P<episode_end>\d+))?-bolum"
        out = episodes.series_inventory(_page("<li><a href='/kurgu-1-sezon-3-4-bolum'>x</a></li>"), BASE + "/kurgu",
                                        dict(SPEC, episode_url_regex=rx))
        self.assertEqual([v["key"] for v in out["video_sources"]], ["s1e3", "s1e4"])

    def test_unnumbered_final_and_special(self):
        html = _page("<li><a href='/kurgu-1-sezon-1-bolum'>1. Bölüm</a></li>"
                     "<li><a href='/kurgu-1-sezon-2-bolum'>2. Bölüm</a></li>"
                     "<li><a href='/kurgu-sezon-finali'>Sezon Finali</a></li>"
                     "<li><a href='/kurgu-ozel'>Özel Bölüm</a></li>")
        strict = episodes.series_inventory(html, BASE + "/kurgu", SPEC)   # url-only numbering: reported, not read
        self.assertEqual(len(strict["video_sources"]), 2)
        self.assertTrue(any("atlandı" in w for w in strict["warnings"]))
        out = episodes.series_inventory(html, BASE + "/kurgu", dict(SPEC, numbering_from=["url", "text"]))
        keys = {v["key"]: v["url"] for v in out["video_sources"]}
        self.assertEqual(keys.get("s1e3"), BASE + "/kurgu-sezon-finali")
        self.assertEqual(keys.get("s0e1"), BASE + "/kurgu-ozel")
        self.assertTrue(any("numarasız" in w for w in out["warnings"]))


class SearchElsewhere(unittest.TestCase):
    def test_query_cleaning(self):
        self.assertEqual(textnorm.search_query("Halef 37.Bölüm izle"), "Halef")
        self.assertEqual(textnorm.search_query("Uydurma Dizi S04E10"), "Uydurma Dizi")
        self.assertEqual(textnorm.first_words("Uzun Bir Dizi Adı"), "Uzun Bir")

    def test_two_word_fallback(self):
        calls = []

        def fake_search(site, query):
            calls.append(query)
            if query == "Uzun Bir":
                return [{"title": "Uzun Bir Dizi", "url": BASE + "/a"}, {"title": "Başka", "url": BASE + "/b"}]
            return []

        with mock.patch.object(search_run, "search", fake_search), \
                mock.patch.object(search_run, "import_hits", lambda site, hits: [h["url"] for h in hits]):
            ids = search_run._elsewhere_ids("s", search_run._elsewhere_queries(
                {"title": "Uzun Bir Dizi Adı - 2. Sezon", "original_title": None}))
        self.assertEqual(calls, ["Uzun Bir Dizi Adı", "Uzun Bir"])
        self.assertEqual(ids, [BASE + "/a"])


class Overview(unittest.TestCase):
    def test_read_more_and_bilingual(self):
        self.assertEqual(detail.clean_overview("Bir aile sırrı. ... Devamını Göster"), "Bir aile sırrı.")
        self.assertEqual(detail.clean_overview("Bir aile. ---- A family."), "Bir aile.")
        self.assertEqual(detail.clean_overview("Story here. Read more"), "Story here.")
        self.assertEqual(detail.clean_overview("Normal metin"), "Normal metin")


if __name__ == "__main__":
    unittest.main()
