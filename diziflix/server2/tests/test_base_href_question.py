import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import subprocess
import unittest

from app.control.stages import explain
from app.core.extract import episodes, outline
from app.core.extract._util import absolute, doc_base, repeat_fixed

PAGE = "https://www.fakesite.test/dizi/x"
EP_RX = r"^/dizi/(?P<slug>[^/]+)/sezon-(?P<season>\d+)/bolum-(?P<episode>\d+)/?$"


def series_html(base_tag: bool = True) -> str:
    rows = "".join(f'<div class="item ep"><a href="dizi/x/sezon-1/bolum-{n}">Bölüm {n}</a></div>' for n in range(1, 5))
    head = '<base href="https://www.fakesite.test/">' if base_tag else ""
    return f'<html><head>{head}</head><body><div class="episodes">{rows}</div>' \
           '<a href="/dizi/y">Başka</a><a href="https://cdn.fakesite.test/a.js">abs</a></body></html>'


class BaseHrefTests(unittest.TestCase):
    def test_relative_href_uses_base_tag(self):
        html = series_html()
        base = doc_base(html, PAGE)
        self.assertEqual(absolute(base, "dizi/x/sezon-1/bolum-2"), "https://www.fakesite.test/dizi/x/sezon-1/bolum-2")
        self.assertEqual(absolute(base, "/dizi/y"), "https://www.fakesite.test/dizi/y")              # root-relative unchanged
        self.assertEqual(absolute(base, "https://cdn.fakesite.test/a.js"), "https://cdn.fakesite.test/a.js")
        self.assertEqual(doc_base("<p></p>", PAGE + "/"), PAGE + "/")                               # no <base>: the page itself
        spec = {"row_selector": "div.item.ep", "fields": {"url": {"selector": "a", "attr": "href"}}, "episode_url_regex": EP_RX}
        urls = [v["url"] for v in episodes.series_inventory(html, PAGE, spec)["video_sources"]]
        self.assertEqual(urls[1], "https://www.fakesite.test/dizi/x/sezon-1/bolum-2")
        self.assertFalse(any("/dizi/dizi/" in u for u in urls))
        spec2, ev = episodes.build_series_page(html, PAGE, outline.outline(html, PAGE))
        self.assertIsNotNone(spec2, ev)
        self.assertNotIn("dizi/dizi", spec2["episode_url_regex"])

    def test_repeated_segment_fixed_only_when_samples_agree(self):
        bad = "https://www.fakesite.test/dizi/dizi/x/sezon-1/bolum-6"
        self.assertEqual(repeat_fixed(bad, [EP_RX]), "https://www.fakesite.test/dizi/x/sezon-1/bolum-6")
        self.assertEqual(repeat_fixed(bad, [r"^/dizi/dizi/[^/]+/sezon-\d+/bolum-\d+"]), bad)   # matches as is: kept
        self.assertEqual(repeat_fixed(bad, [r"^/film/"]), bad)                                   # neither matches: kept
        # an old map whose regex learned the doubled path still reads the (now correct) addresses
        old = {"row_selector": "div.item.ep", "episode_url_regex": EP_RX.replace("^/dizi/", "^/?dizi/dizi/")}
        inv = episodes.series_inventory(series_html(), PAGE, old)
        self.assertEqual(len(inv["video_sources"]), 4)
        self.assertEqual(inv["video_sources"][0]["url"], "https://www.fakesite.test/dizi/x/sezon-1/bolum-1")


class QuestionFormatTests(unittest.TestCase):
    EV = {"looked_for": "oynatıcı", "summary": "0 oynatıcı sayfası",
          "tried": [f"https://www.fakesite.test/dizi/x/sezon-1/bolum-{n}" for n in range(1, 6)]}
    CHECKS = [{"name": "candidate_ratio", "ok": False, "value": 0.0, "bound": 0.6}, {"name": "ok_one", "ok": True}]

    def test_sections_and_plain_text(self):
        secs = explain.sections(self.EV, self.CHECKS, ["not 1"])
        self.assertEqual([s["title"] for s in secs], ["Ne yaptım", "Bulamadığım", "Ayrıntı", "Senden istediğim"])
        pages = secs[0]["items"][1]
        self.assertEqual(len(pages["lines"]), 4)
        self.assertEqual(pages["lines"][-1], "+2 daha")
        text = explain.question(self.EV, self.CHECKS, ["not 1"])
        lines = text.split("\n")
        self.assertEqual(lines[:3], ["Yardım gerekiyor", "Ne yaptım", " • Aradığım: oynatıcı"])
        self.assertIn("Bulamadığım", lines)
        self.assertIn("    https://www.fakesite.test/dizi/x/sezon-1/bolum-1", lines)
        self.assertTrue(any(l.startswith(" • Oynatıcının") for l in lines))

    def test_question_render_node(self):
        if not shutil.which("node"):
            self.skipTest("node yok")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "question_test.js")
        out = subprocess.run(["node", path, explain.question(self.EV, self.CHECKS)], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
