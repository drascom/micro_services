import _sandbox  # noqa: F401  (must be first)

from unittest import mock

from fastapi.testclient import TestClient

from app import main
from app.control.routing import suggest
from app.control.sitemap import site_notes, store as map_store
from app.control.stages import actions, engine, hints, state, status
from app.core.llm import fake

from test_t08_stages import A, Case
from test_t17_a1_review import ARCHIVE, EP_LIST, _archive, _episodes, _home
from test_t18_a3_samples import C, C3Case

NOTE_C = ("Dizi sayfası /diziler/<slug>/, dizi sayfasında afiş .category_image içinde. "
          "Bölüm sayfasında afiş #benzerli içinde. Arama /?s=<kelime>. password: hunter2secret")


class NoteStoreTests(C3Case):
    def test_save_read_limit_and_create_with_note(self):
        client = TestClient(main.app)
        r = client.post("/api/control/sites", json={"url": "https://fakesite-n.test/", "notes": " Arama /?s=<kelime> "})
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["site_id"]
        got = client.get(f"/api/control/sites/{sid}/notes").json()
        self.assertEqual((got["notes"], got["changed"]), ("Arama /?s=<kelime>", False))
        r = client.put(f"/api/control/sites/{sid}/notes", json={"notes": "x" * 4001})
        self.assertEqual(r.status_code, 400, r.text)
        r = client.put(f"/api/control/sites/{sid}/notes", json={"notes": "Dizi listesi /x-arsivi-01/"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(site_notes.get(sid), "Dizi listesi /x-arsivi-01/")
        self.assertEqual(client.post("/api/control/sites", json={"url": "https://fakesite-m.test/",
                                                                  "notes": "y" * 4001}).status_code, 400)

    def test_changed_note_offers_a_rerun_from_a1(self):
        sid = self.run_c()
        self.assertFalse(status.overview(sid)["notes"]["changed"])
        actions.save_notes(sid, NOTE_C)                       # saving resets nothing
        self.assertEqual(state.get(sid, "A2")["state"], "passed")
        ov = status.overview(sid)
        self.assertTrue(ov["notes"]["changed"] and all(st["notes_changed"] for st in ov["stages"]))
        out = actions.rerun_with_notes(sid)
        self.assertTrue(out["queued"])
        self.assertIn("A2", out["reset"])
        self.assertFalse(status.overview(sid)["notes"]["changed"])


class NotePromptTests(C3Case):
    def test_note_heads_this_sites_prompts_only_and_is_scrubbed(self):
        other = self.site("https://fakesite-o.test")
        site_notes.save(other, "BASKA SITE NOTU", applied=True)
        sid = self.site(C)
        site_notes.save(sid, "A3 ICIN NOT: dizi listesi /dizi-arsivi-01/. password: hunter2secret", applied=True)
        draft = map_store.draft(sid)   # poster_fill is a warning now ("1 çalışan örnek yeter"): required here for the LLM rounds
        draft.setdefault("meta", {}).setdefault("title", {})["expect"] = {"poster_fill": {"optional": False}}
        map_store.save_draft(sid, draft)
        fake.script([])
        actions.start(sid)
        self.drain(12)                                        # A3 finds no poster -> LLM rounds (fake: empty replies)
        stage_prompts = list(fake.prompts)
        self.assertTrue(stage_prompts)
        for text in stage_prompts:
            self.assertTrue(text.startswith("## " + site_notes.TITLE.upper()), text[:120])   # the stable prefix
            self.assertIn("A3 ICIN NOT", text)
            self.assertNotIn("BASKA SITE NOTU", text)
            self.assertNotIn("hunter2secret", text)
        fake.script([])
        suggest.suggest(other)                                # routing suggestion of the other site: its own note only
        self.assertIn("BASKA SITE NOTU", fake.prompts[-1])
        self.assertNotIn("A3 ICIN NOT", fake.prompts[-1])

    def test_note_selector_is_tried_first_for_a3_and_shape_is_checked(self):
        sid = self.site(C)
        site_notes.save(sid, NOTE_C, applied=True)
        actions.start(sid)
        self.drain(12)
        row = state.get(sid, "A3")
        checks = {c["name"]: c["value"] for c in row["measurement"]["checks"]}
        self.assertIn(row["state"], ("passed", "passed_warn"), row["measurement"])
        self.assertEqual(checks["poster_fill"], 1.0)
        page = map_store.draft(sid)["pages"]["title"]
        self.assertIn(".category_image img", str(page["fields"]["poster"]))
        self.assertNotIn("benzerli", str(page["fields"]["poster"]))       # the episode page's selector is not A3's
        self.assertEqual(page["url_pattern"], "^/diziler/[^/?#]+/?$")
        used = row["evidence"]["note_used"]
        self.assertTrue(any("örneklere uydu" in u for u in used), used)


class NoteParseTests(C3Case):
    def test_search_template_title_shape_and_page_words(self):
        items = hints.note_items("Arama /?s=<kelime>. Dizi sayfası /diziler/<slug>/. Film arşivi /filmler/ sayfalı.")
        self.assertEqual(items["search"], "/?s={query}")
        self.assertEqual(items["title"]["key"], {"regex": "^/diziler/([^/?#]+)", "template": "{1}"})
        self.assertEqual(items["listings"], [{"url": "/filmler/", "meaning": "film_list", "name": "Film arşivi sayfalı"}])
        self.assertEqual(hints.note_items("bölüm listesi /yeni-bolumler/")["listings"][0]["meaning"], "episode_list")
        self.assertEqual(hints.selector_list(["Bölüm sayfasında oynatıcı #player içinde"], "player", ("episode",)),
                         [("#player", "")])
        self.assertEqual(hints.candidates(["bölüm sayfasında afiş .x içinde"], ("poster",), pages=("title",)), {})


class NoteReviewTests(Case):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(engine, "A1_REVIEW", True)
        p.start()
        self.addCleanup(p.stop)
        self.overrides.update({A + "/": _home(), A + EP_LIST: _episodes(), A + ARCHIVE: _archive(1)})

    def test_note_list_address_is_proposed_as_a_title_list(self):
        sid = self.site(A)
        site_notes.save(sid, "Dizi listesi /x-arsivi-01/ (sayfalı: -02, -03).", applied=True)
        actions.start(sid)
        self.drain()
        a1 = status.overview(sid)["stages"][0]
        self.assertEqual(a1["state"], "review")
        sug = a1["review"]["suggested"]
        self.assertEqual([(s["url"], s["meaning"], s["content"]) for s in sug], [("/x-arsivi-01/", "series_list", "title")])
        self.assertTrue(any("x-arsivi-01" in u for u in a1["evidence"]["note_used"]))
        actions.review(sid, "A1", {"sections": [], "links": [], "added": [{"name": s["name"], "url": s["url"], "meaning": s["meaning"]} for s in sug]})
        links = map_store.draft(sid)["pages"]["home"]["nav"]["links"]
        got = next(l for l in links if l["url"] == "/x-arsivi-01/")
        self.assertEqual((got["content"], got.get("manual")), ("title", True))



class CardSyncJsTests(C3Case):
    def test_polling_decisions_node(self):
        import os
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node yok")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "cardsync_test.js")
        out = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
