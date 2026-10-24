"""Canlı TV M3U import (library/m3u.py): parser (normal + broken), LLM answer validation (fake LLM, invented URL refused),
import -> channels (visible, headers -> stream proxy, famelack pass leaves them alone, delete). No network."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402
import json
import unittest

from app import db
from app.library import live, m3u

GOOD = """#EXTM3U
#EXTINF:-1 tvg-id="trt1.tr" tvg-name="TRT 1" tvg-logo="https://logo.example/trt1.png" group-title="Ulusal" tvg-language="Turkish" tvg-country="TR",TRT 1 HD
https://a.example/trt1.m3u8
#EXTINF:-1 group-title="Haber",Haber Kanalı
#EXTVLCOPT:http-referrer=https://ref.example/
#EXTVLCOPT:http-user-agent=TestUA/1.0
https://b.example/haber.m3u8
#EXTINF:-1 group-title="Spor",TRT 1 kopya
https://a.example/trt1.m3u8
#EXTINF:-1 tvg-id="trt1.tr",TRT 1 yedek
https://c.example/trt1b.m3u8
"""

BROKEN = """#EXTM3U
#EXTINF:-1 group-title="Haber",Kopuk Kanal
#EXTINF:-1 group-title="Spor",Spor 1
https://s.example/spor1.m3u8
https://x.example/isimsiz.m3u8
Belgesel TV https://d.example/belgesel.m3u8
"""


def fake_llm(answer):
    calls = []

    def call(prompt):
        calls.append(prompt)
        return json.dumps(answer)
    call.calls = calls
    return call


class ParseTests(unittest.TestCase):
    def test_normal_playlist(self):
        entries, problems, stats = m3u.parse(GOOD)
        self.assertEqual(problems, [])
        self.assertTrue(stats["m3u"])
        self.assertEqual(len(entries), 4)
        e = entries[0]
        self.assertEqual((e["name"], e["logo"], e["group"], e["tvg_id"], e["country"]),
                         ("TRT 1 HD", "https://logo.example/trt1.png", "Ulusal", "trt1.tr", "TR"))
        self.assertEqual(entries[1]["headers"], {"Referer": "https://ref.example/", "User-Agent": "TestUA/1.0"})
        recs, skipped = m3u.build("s1", entries)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(recs), 2)   # duplicate URL merged; same tvg-id = one channel, two streams
        trt = next(r for r in recs.values() if r["name"] == "TRT 1 HD")
        self.assertEqual(trt["streams"], ["https://a.example/trt1.m3u8", "https://c.example/trt1b.m3u8"])
        self.assertEqual(trt["categories"], ["general", "sports"])
        self.assertEqual(trt["languages"], ["turkish"])
        self.assertEqual(trt["country"], "tr")
        self.assertEqual(m3u.category_of("Belgesel & Doğa"), "belgesel-doga")

    def test_broken_parts_go_to_problems(self):
        entries, problems, _ = m3u.parse(BROKEN)
        self.assertEqual([e["name"] for e in entries], ["Spor 1"])
        joined = "\n".join(problems)
        self.assertIn("Kopuk Kanal", joined)
        self.assertIn("https://x.example/isimsiz.m3u8", joined)
        self.assertIn("Belgesel TV https://d.example/belgesel.m3u8", joined)

    def test_non_m3u_text(self):
        entries, problems, stats = m3u.parse("Kanal listesi\nTRT Haber\nhttps://h.example/live.m3u8\n")
        self.assertFalse(stats["m3u"])
        self.assertEqual(entries, [])
        self.assertEqual(problems, ["TRT Haber", "https://h.example/live.m3u8"])

    def test_private_hosts_skipped(self):
        entries, _, _ = m3u.parse("#EXTM3U\n#EXTINF:-1,Yerel\nhttp://127.0.0.1/x.m3u8\n#EXTINF:-1,LH\nhttp://localhost/y.m3u8\n")
        recs, skipped = m3u.build("s1", entries)
        self.assertEqual((recs, skipped), ({}, 2))


class LlmTests(unittest.TestCase):
    def test_invented_url_refused(self):
        chunk = "Belgesel TV https://d.example/belgesel.m3u8\n"
        answer = json.dumps([
            {"name": "Belgesel TV", "url": "https://d.example/belgesel.m3u8", "logo": "https://evil.example/l.png",
             "group": "Belgesel", "language": "tur"},
            {"name": "Uydurma", "url": "https://invented.example/x.m3u8"},
            {"name": "Ftp", "url": "ftp://d.example/belgesel.m3u8"},
            {"name": "", "url": "https://d.example/belgesel.m3u8"},
            "junk"])
        out = m3u.validate_llm("```json\n" + answer + "\n```", chunk)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["name"], out[0]["url"], out[0]["logo"], out[0]["group"]),
                         ("Belgesel TV", "https://d.example/belgesel.m3u8", "", "Belgesel"))
        self.assertEqual(m3u.validate_llm("no json here", chunk), [])

    def test_unavailable_llm_is_skipped(self):
        def off(prompt):
            raise m3u.LLMUnavailable("kapalı")
        out, status = m3u.llm_fix(["https://x.example/a.m3u8"], off)
        self.assertEqual(out, [])
        self.assertTrue(status.startswith("off:"))
        self.assertEqual(m3u.llm_fix([], off), ([], "not_needed"))


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def setUp(self):
        db.execute("DELETE FROM channels")
        db.execute("DELETE FROM live_sources")
        live.invalidate()

    def test_import_into_channels(self):
        llm = fake_llm([{"name": "Belgesel TV", "url": "https://d.example/belgesel.m3u8", "group": "Doğa Belgeselleri"},
                        {"name": "İsimsiz", "url": "https://x.example/isimsiz.m3u8"},
                        {"name": "Hayali", "url": "https://fake.example/z.m3u8"}])
        res = m3u.create("Test", "file", content=GOOD + BROKEN.replace("#EXTM3U\n", ""), llm_call=llm, probe=False)
        s = res["summary"]
        self.assertEqual(s["llm"], "ok")
        self.assertEqual(s["llm_fixed"], 2)          # invented URL dropped
        self.assertEqual(s["added"], 5)
        self.assertTrue(llm.calls)
        src = res["source"]
        self.assertEqual((src["kind"], src["channels"]), ("file", 5))
        vis = {c["title"]: c for c in live.visible_channels(fresh=True)}
        self.assertIn("Belgesel TV", vis)
        self.assertEqual(vis["Belgesel TV"]["categories"], ["doga-belgeselleri"])
        self.assertEqual(live.row_title("live_doga-belgeselleri"), "Doğa Belgeselleri")
        self.assertEqual(live.card(vis["Belgesel TV"])["genres"], ["Doğa Belgeselleri"])
        card = live.card(vis["TRT 1 HD"])
        self.assertEqual((card["sources"], card["logo"]), (["m3u"], "https://logo.example/trt1.png"))
        streams = live.streams(vis["Haber Kanalı"])
        self.assertEqual(streams[0]["request_headers"], {"Referer": "https://ref.example/", "User-Agent": "TestUA/1.0"})
        self.assertTrue(streams[0]["stream_proxy"])
        # the famelack pass (country tr) leaves M3U channels alone
        live.upsert({}, ["tr"])
        self.assertEqual(len([c for c in live.all_channels() if c["active"]]), 5)
        # re-import: unchanged; delete removes every channel of the source
        again = m3u.refresh(src["id"], llm_call=llm, probe=False)
        self.assertEqual((again["added"], again["unchanged"]), (0, 5))
        self.assertEqual(m3u.delete(src["id"]), 5)
        self.assertEqual(live.all_channels(), [])

    def test_llm_off_summary_and_link_guard(self):
        def off(prompt):
            raise m3u.LLMUnavailable("kapalı")
        s = m3u.create("B", "file", content=BROKEN, llm_call=off, probe=False)["summary"]
        self.assertEqual((s["added"], s["llm_fixed"], s["unresolved"] > 0), (1, 0, True))
        self.assertTrue(s["llm"].startswith("off:"))
        s2 = m3u.create("C", "file", content=BROKEN, llm=False, probe=False)["summary"]
        self.assertEqual(s2["llm"], "disabled")
        with self.assertRaises(ValueError):
            m3u.create("L", "link", url="http://127.0.0.1/list.m3u")
        self.assertEqual(len(m3u.list_sources()), 2)


if __name__ == "__main__":
    unittest.main()
