import _sandbox  # noqa: F401  (must be first)

from unittest import mock

from app import settings
from app.control.sitemap import store as map_store
from app.control.stages import actions, engine, prompts_read, status
from app.core.extract import pagemap
from app.core.fetch import Page
from app.core.llm.client import Reply

from test_t18_a3_samples import C, C3Case
from test_t22_listing_blocks import N, TRENDS, home, trends

SEC = "Haftanın Seçkileri"   # a third block whose heading says nothing; its rows even look like title rows

A2_REPLY = f"""Okudum.
```yaml
interpretation:
  - {{ref: b1, meaning: "trend diziler", take: true, why: "başlık Trend Diziler, /diziler/ adresleri"}}
  - {{ref: b2, meaning: "trend filmler", take: true, why: "/filmler/ adresleri"}}
  - {{ref: b3, meaning: "kullanıcı seçkileri", take: false, why: "kullanıcı listesi, alınmaz"}}
rules:
  pages:
    listing:
      url_pattern: '^/trendler/?$'
      row_selector: "td.poster"
      fields: {{url: {{selector: a, attr: href}}, title: {{selector: img, attr: alt}}, poster: {{selector: img, attr: data-src}}}}
      pagination: {{kind: none}}
      blocks:
        - {{url: /trendler/, index: 1, heading: "Trend Diziler", type: series}}
        - {{url: /trendler/, index: 2, heading: "Trend Filmler", type: movie}}
        - {{url: /trendler/, index: 3, heading: "{SEC}", ignore: true}}
```"""


def two_lists() -> str:
    """A list page with two unheaded lists: a big card grid (discovery's pick) and a side list (the LLM's pick)."""
    grid = "".join(f'<div class="card"><a href="/diziler/grid-{i}/"><img data-src="https://img.fakesite-c.test/g{i}.jpg" '
                   f'alt="Grid {i}"><span class="t">Grid {i}</span></a></div>' for i in range(1, 15))
    side = "".join(f'<li class="it"><a href="/diziler/side-{i}/"><img data-src="https://img.fakesite-c.test/s{i}.jpg" '
                   f'alt="Side {i}"><span class="n">Side {i}</span></a></li>' for i in range(1, 12))
    return (f'<html><body><main><div class="grid">{grid}</div><aside><ul class="side">{side}</ul></aside></main>'
            '</body></html>')


SIDE_REPLY = """```yaml
interpretation:
  - {ref: b2, meaning: "yan liste", take: true, why: "asıl liste bu"}
rules:
  pages:
    listing:
      url_pattern: '^/trendler/?$'
      row_selector: "li.it"
      fields: {url: {selector: a, attr: href}, title: {selector: img, attr: alt}, poster: {selector: img, attr: data-src}}
      pagination: {kind: none}
```"""


class OutlineTests(C3Case):
    def test_same_structure_blocks_keep_their_headings_and_link_types(self):
        pm = pagemap.build(trends(), TRENDS)
        self.assertEqual([(h, rows, kind) for _r, h, rows, kind in pagemap.summary(pm)],
                         [("Trend Diziler", N, "title"), ("Trend Filmler", N, "title"), ("Trend Kullanıcılar", N, "user")])
        b3 = pm["blocks"][2]
        self.assertEqual((b3["ref"], b3["index"], b3["head"], b3["rows"]), ("b3", 3, "uye", N))   # T22 format + more
        self.assertEqual(b3["examples"][0]["url"], "/uye/uye-1/")
        text = pagemap.render(pm)
        self.assertIn('[b3] "Trend Kullanıcılar" rows=12', text)
        self.assertIn("links=user", text)
        self.assertLessEqual(len(text), pagemap.LIMIT)
        short = pagemap.render(pm, 700)   # no room: the rest is one "… N more blocks" line
        self.assertLessEqual(len(short), 700)
        self.assertIn("more blocks", short)

    def test_reply_parts_are_validated(self):
        items, rules, notes = prompts_read.split({"interpretation": [{"ref": "b3", "meaning": "x", "take": "hayır"}, {"x": 1}],
                                                  "rules": {"pages": {"listing": {}}}})
        self.assertEqual([(i["ref"], i["take"]) for i in items], [("b3", False)])
        self.assertEqual(rules, {"pages": {"listing": {}}})
        self.assertTrue(any("ref yok" in n for n in notes))


class LlmFirstCase(C3Case):
    def setUp(self):
        super().setUp()
        for name, value in (("A1_REVIEW", True), ("LLM_FIRST", True)):
            p = mock.patch.object(engine, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.overrides.update({C + "/": home(), TRENDS: trends(SEC)})
        self.replies, self.prompts = {}, []
        p = mock.patch.object(engine, "COMPLETE", self.complete)
        p.start()
        self.addCleanup(p.stop)

    def complete(self, text, *, stage=None, **kw):
        """Per-stage scripted replies; a stage without one: the LLM cannot be reached."""
        self.prompts.append((stage, text, kw.get("background")))
        queue = self.replies.get(stage) or []
        return Reply(ok=True, text=queue.pop(0)) if queue else Reply(ok=False, error="unreachable")

    def a2(self):
        sid = self.run_c(4)
        self.assertEqual(self.st(sid, "A1")["state"], "review")
        actions.review(sid, "A1", {"links": [{"url": "/trendler/", "meaning": "category"}]})
        self.drain(6)
        return sid, self.st(sid, "A2")


class LlmFirstTests(LlmFirstCase):
    def test_a2_llm_reads_first_and_its_reading_prefills_the_block_card(self):
        self.replies["A2"] = [A2_REPLY]
        sid, a2 = self.a2()
        a1 = self.st(sid, "A1")
        self.assertIn("LLM kullanılamadı", a1["evidence"]["llm_read"]["note"])            # A1: no reply -> LLM-free
        self.assertEqual(a2["state"], "review", a2["measurement"])
        self.assertEqual(a2["evidence"]["llm_read"]["chosen"], "llm")                      # same rows read: the LLM's rules
        self.assertEqual(a2["measurement"]["samples"][0]["rows"], 2 * N)                   # its block 3 is not read
        rv = a2["evidence"]["review"]["blocks"]
        self.assertEqual([(b["index"], b["proposed"]) for b in rv], [(1, "series"), (2, "movie"), (3, "ignore")])
        self.assertEqual((rv[2]["reason"], rv[2]["llm"]["take"], rv[2]["llm"]["meaning"]),
                         ("LLM önerisi", False, "kullanıcı seçkileri"))
        self.assertEqual(a2["evidence"]["interpretation"][2]["heading"], SEC)              # ref b3 -> the block's heading
        card = next(st for st in status.overview(sid)["stages"] if st["id"] == "A2")
        self.assertTrue(card["review"]["llm_read"])
        self.assertTrue(any(m["text"].startswith("LLM'in yorumu") for m in card["messages"]))
        actions.review(sid, "A2", {"blocks": [{"url": "/trendler/", "index": i, "choice": c}
                                              for i, c in ((1, "series"), (2, "movie"), (3, "ignore"))]})
        self.assertIn(self.st(sid, "A2")["state"], ("passed", "passed_warn"))
        self.assertEqual(map_store.draft(sid)["pages"]["listing"]["row_selector"], "td.poster")

    def test_prompt_carries_purpose_page_map_and_the_reply_schema(self):
        self.replies["A2"] = [A2_REPLY]
        self.a2()
        stage, text, background = next(p for p in self.prompts if p[0] == "A2")
        for part in ("PURPOSE (A2", "Page map of " + TRENDS, f'[b3] "{SEC}"', "links=title",
                     "interpretation:", "take: <true|false>", "rules:", "row_selector:", "Short example:",
                     "The system's guess"):
            self.assertIn(part, text)
        self.assertFalse(background)                                                     # admin run: outside the budget
        self.assertLess(len(text), prompts_read.MAX_PROMPT + 5000)

    def test_both_pass_with_different_rows_wait_for_the_admin_choice(self):
        self.overrides[TRENDS] = two_lists()
        self.replies["A2"] = [SIDE_REPLY]
        sid, a2 = self.a2()
        self.assertEqual(a2["state"], "review", a2["measurement"])
        cands = a2["evidence"]["review"]["candidates"]
        self.assertEqual([c["id"] for c in cands], ["llm", "discover"])
        self.assertTrue(any("side-" in u for u in cands[0]["samples"]) and any("grid-" in u for u in cands[1]["samples"]))
        self.assertNotIn("listing", map_store.draft(sid).get("pages") or {})                # nothing written before the choice
        self.assertIn("hangisi", next(st for st in status.overview(sid)["stages"] if st["id"] == "A2")["summary"])
        with self.assertRaises(ValueError):
            actions.review(sid, "A2", {"candidate": "both"})
        actions.review(sid, "A2", {"candidate": "llm"})
        self.assertIn(self.st(sid, "A2")["state"], ("passed", "passed_warn"))
        self.assertEqual(map_store.draft(sid)["pages"]["listing"]["row_selector"], "li.it")

    def test_no_budget_runs_llm_free_with_a_card_note(self):
        settings.set("llm_daily_calls_per_site", 0)
        self.replies["A1"] = ["never asked"]
        sid = self.site(C)
        engine.enqueue(sid, "A1", trigger="system")   # a system-started run (an admin run ignores the budget)
        self.drain(4)
        a1 = self.st(sid, "A1")
        self.assertEqual(a1["state"], "review")                                           # discovery alone passed
        self.assertFalse(a1["evidence"]["llm_read"]["used"])
        self.assertIn("LLM kullanılamadı (günlük LLM bütçesi doldu", a1["evidence"]["llm_read"]["note"])
        self.assertEqual(self.prompts, [])                                                # no call at all
        self.assertEqual(self.replies["A1"], ["never asked"])

    def test_unusable_read_gets_one_format_fix_then_the_system_guess_is_measured(self):
        self.replies["A2"] = ["no yaml here", "```yaml\ninterpretation: []\n```"]
        sid, a2 = self.a2()
        calls = [t for s, t, _bg in self.prompts if s == "A2"]
        self.assertEqual(len(calls), 2)                                                  # the read + ONE format fix
        self.assertIn("could not be used", calls[1])
        self.assertIn("interpretation:", calls[1])
        self.assertEqual(a2["evidence"]["llm_read"]["chosen"], "discover")
        self.assertEqual(a2["state"], "review")                                           # the system's blocks, card as before
