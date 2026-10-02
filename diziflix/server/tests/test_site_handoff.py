"""Site handoff note (``scraper/site_handoff.py``): deterministic sections from a draft, size ceilings, edit / repair entries, the
``load_site_config`` answer, deletion / rename, secret scrubbing, scan findings. No network, no pi, temporary directories."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import tempfile
import unittest
from unittest.mock import patch

import test_ops_sites as T  # noqa: E402  (its Base: temp configs / db / the ops routers; also yields helpers)
from app import config
from app.library import purge
from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, heal_agent, onboard, onboard_store as store, site_handoff as sh

DAY = 86400.0


class HandoffBase(T.Base):
    def setUp(self):
        super().setUp()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        p = patch.object(config, "DATA_DIR", self.data.name)
        p.start()
        self.addCleanup(p.stop)

    def note(self, site="alpha"):
        return sh.read(site)


def draft_with(events, **extra):
    return {"id": "od_000000000001", "events": events, "hint": "", "skipped_fields": [], **extra}


class DeterministicSectionsTest(HandoffBase):
    def test_user_lines_pair_questions_with_answers_and_list_skips(self):
        draft = draft_with([
            {"kind": "say", "text": "agent talk is not recorded"},
            {"kind": "user", "text": "Sadece diziler lazim"},
            {"kind": "ask", "field": "home_series_section", "text": "Ana sayfada dizi bolumu yok, atlayayim mi?"},
            {"kind": "user", "text": "Sitede yok, atla: home_series_section"},
            {"kind": "ask", "field": "poster", "text": "Afis nerede?"},
        ], hint="Yeni site: alpha", skipped_fields=["home_series_section"],
            report={"criteria": {"a": {"ok": True}, "b": {"ok": False}, "c": {"ok": True}}},
            provider_recipes=[{"name": "newplayer"}])
        facts = sh.facts_from_draft(draft, alpha := T.alpha_yaml(playback="video", list_url="/dizi", item_limit=20), 2, force=True)
        user = "\n".join(facts["user"])
        self.assertIn("İstek: Yeni site: alpha", user)
        self.assertIn("Talimat: Sadece diziler lazim", user)
        self.assertIn("Soru (home_series_section): Ana sayfada dizi bolumu yok, atlayayim mi? -> Cevap: Sitede yok, atla: home_series_section", user)
        self.assertIn("Soru (poster): Afis nerede? -> cevap yok", user)
        self.assertIn("Sitede yok, atla: home_series_section", facts["user"][-1])
        self.assertNotIn("agent talk", user)
        results = "\n".join(facts["results"])
        self.assertIn("Kriterler: 2/3 geçti; başarısız: b (zorla kaydedildi)", results)
        self.assertIn("newplayer", results)
        state = facts["state"]
        self.assertEqual(state["Sürüm"].split(" ")[0], "v2")
        self.assertEqual((state["Oynatma"], state["Liste"]), ("video", "/dizi"))
        self.assertIn("trending_alpha", state["Koleksiyonlar"])
        self.assertEqual(state["Arama"], "yok")
        self.assertEqual(alpha["site_id"], "alpha")

    def test_at_most_twelve_events_newest_kept(self):
        events = [{"kind": "user", "text": f"mesaj {i}"} for i in range(20)]
        user = sh.facts_from_draft(draft_with(events), {})["user"]
        self.assertEqual(len(user), 12)
        self.assertEqual((user[0], user[-1]), ("Talimat: mesaj 8", "Talimat: mesaj 19"))

    def test_save_writes_initial_note_with_agent_text_then_notes_fallback(self):
        draft = draft_with([{"kind": "user", "text": "Filmleri de al"}], handoff="Sorun: afis lazy-load\nCozum: data-src")
        report = {"criteria": {"a": {"ok": True}}, "notes": "notes of the agent"}
        self.assertTrue(onboard._write_handoff(draft, "alpha", T.alpha_yaml(), report, 1, "", False))
        text = self.note()
        self.assertIn("# alpha devir notu", text)
        self.assertIn("## Bulgular ve çözümler\nSorun: afis lazy-load\nCozum: data-src", text)
        self.assertIn("- Talimat: Filmleri de al", text)
        self.assertIn("- Kriterler: 1/1 geçti", text)
        # no handoff from the agent: its notes are the narrative; saving never fails
        self.assertTrue(onboard._write_handoff(draft_with([]), "beta", T.alpha_yaml(), report, 1, "", False))
        self.assertIn("## Bulgular ve çözümler\nnotes of the agent", sh.read("beta"))


class CeilingsTest(HandoffBase):
    def test_agent_text_is_cut_and_whole_file_stays_under_6kb(self):
        long = "\n".join(f"satır {i}: " + "ç" * 90 for i in range(200))
        sh.write_initial("alpha", long, {"state": {"Sürüm": "v1"}, "user": [f"talimat {i} " + "ğ" * 200 for i in range(40)],
                                         "results": ["- Kriterler: 3/3 geçti"]})
        text = self.note()
        self.assertLessEqual(len(text.encode("utf-8")), sh.TOTAL_MAX)
        narrative = text.split("## Bulgular ve çözümler\n")[1].split("\n\n## ")[0]
        self.assertLessEqual(len(narrative), sh.NARRATIVE_MAX)
        self.assertTrue(narrative.endswith("…"))
        self.assertIn("## Değişiklik geçmişi", text)

    def test_change_entry_is_cut_to_600_bytes(self):
        sh.append_change("alpha", "ş" * 3000)
        entry = sh._load("alpha")["changes"][0]["text"]
        self.assertLessEqual(len(entry.encode("utf-8")), sh.CHANGE_MAX)

    def test_history_keeps_eight_entries_older_ones_become_one_line(self):
        for i in range(12):
            sh.append_change("alpha", f"degisiklik {i}\nikinci satir", title=f"Duzenleme {i}", now=1_800_000_000 + i * DAY)
        model = sh._load("alpha")
        self.assertEqual(len(model["changes"]), sh.HISTORY_MAX)
        self.assertEqual(model["changes"][0]["title"], "Duzenleme 11")             # newest first
        self.assertEqual(model["changes"][-1]["title"], "Duzenleme 4")
        self.assertEqual(len(model["old"]), 4)
        self.assertTrue(all(line.startswith("- ") and "\n" not in line for line in model["old"]))
        self.assertIn("Duzenleme 3: degisiklik 3", model["old"][0])
        text = self.note()
        self.assertEqual(text.count("### 20"), sh.HISTORY_MAX)
        self.assertIn("### Eski girişler (özet)", text)
        self.assertLessEqual(len(text.encode("utf-8")), sh.TOTAL_MAX)

    def test_everything_at_once_still_fits(self):
        sh.write_initial("alpha", "x " * 3000, {"state": {k: "v" * 200 for k in "abcdefgh"}, "user": ["u" * 250] * 30,
                                                 "results": ["- " + "r" * 250] * 5})
        for i in range(15):
            sh.append_change("alpha", ("satır\n" * 40), title="T" * 50)
        for i in range(6):
            sh.append_scan_findings("alpha", {"items": 10 + i, "poster": 50 + i * 10}, now=1_800_000_000 + i * 3 * DAY)
        self.assertLessEqual(len(self.note().encode("utf-8")), sh.TOTAL_MAX)

    def test_roundtrip_is_stable(self):
        sh.write_initial("alpha", "Bulgu 1\nBulgu 2", {"state": {"Sürüm": "v1"}, "user": ["a", "b"], "results": ["- Kriterler: 1/1 geçti"]})
        sh.append_change("alpha", "ilk\nikinci", {"user": ["c"]}, title="Düzenleme v2")
        first = self.note()
        model = sh._load("alpha")
        self.assertEqual(sh._fit(model), first)
        self.assertEqual(model["user"], ["- a", "- b", "- c"])


class ChangeEntriesTest(HandoffBase):
    def test_edit_save_appends_one_entry_and_keeps_the_rest(self):
        onboard._write_handoff(draft_with([{"kind": "user", "text": "ilk"}], handoff="Bulgu: A"), "alpha", T.alpha_yaml(), {}, 1, "", False)
        draft = draft_with([{"kind": "user", "text": "poster seçicisini düzelt"}], hint="Afişler yanlış", handoff="Poster: img[data-src]\nNeden: lazy-load")
        report = {"criteria": {"x": {"ok": True}}, "edit": {"changed_keys": ["list.fields.poster_url"]}}
        self.assertTrue(onboard._write_handoff(draft, "alpha", T.alpha_yaml(), report, 2, "alpha", False))
        text = self.note()
        self.assertIn("## Bulgular ve çözümler\nBulgu: A", text)          # the first findings stay
        self.assertIn("### ", text)
        self.assertIn("Düzenleme v2\nPoster: img[data-src]\nNeden: lazy-load", text)
        self.assertIn("- Talimat: poster seçicisini düzelt", text)
        self.assertIn("- Talimat: ilk", text)                                # earlier user lines stay, the new ones are added
        self.assertIn("- İstek: Afişler yanlış", text)

    def test_edit_without_agent_handoff_falls_back_to_server_facts(self):
        draft = draft_with([], hint="Arama ekle")
        report = {"edit": {"changed_keys": ["search"]}, "notes": "arama eklendi"}
        onboard._write_handoff(draft, "alpha", T.alpha_yaml(), report, 3, "alpha", False)
        text = self.note()
        self.assertIn("İstek: Arama ekle", text)
        self.assertIn("Değişen anahtarlar: search", text)
        self.assertIn("arama eklendi", text)

    def test_applied_repair_adds_an_entry_titled_with_the_heal_id(self):
        self.write_site("alpha", T.alpha_yaml(version=4))
        trace = {"handoff": {"text": "Oynatıcı host'u değişti\nTarif güncellendi", "job": "rp_x", "kind": "playback"}, "layers": ["provider"]}
        result = {"applied": True, "new_version": 4, "recipes": [{"name": "newplayer", "action": "update", "version": 2}]}
        heal_agent._note_handoff("alpha", result, trace, {"id": "abc123def456"})
        text = self.note()
        self.assertIn("Heal abc123def456 v4", text)
        self.assertIn("Oynatıcı host'u değişti", text)
        self.assertIn("Katman: provider", text)
        self.assertIn("Tarif: newplayer v2", text)
        self.assertIn("v4 ·", text)                                         # "Güncel durum" is refreshed from the live config

    def test_repair_note_never_raises(self):
        with patch.object(sh, "append_change", side_effect=OSError("disk")):
            heal_agent._note_handoff("alpha", {"applied": True}, {}, None)

    def test_submit_repair_stores_a_scrubbed_handoff(self):
        job = store.new_repair_id()
        self.write_site("alpha", T.alpha_yaml())
        body = sb.SubmitRepairBody(site_id="alpha", notes="n", handoff="## Başlık\nHost değişti Authorization: Bearer abcdefghijklmnop")
        with patch.object(sb, "_is_repair", return_value=True):
            sb._do_submit_repair(job, body)
        record = store.load_repair(job)
        self.assertIn("Host değişti", record["handoff"])
        self.assertNotIn("abcdefghijklmnop", record["handoff"])
        self.assertFalse(record["handoff"].startswith("#"))


class SiteConfigAnswerTest(HandoffBase):
    def test_site_config_carries_the_handoff(self):
        self.write_site("alpha", T.alpha_yaml())
        self.assertEqual(sb._do_site_config("alpha")["handoff"], "")
        sh.write_initial("alpha", "Bulgu: x", {"state": {"Sürüm": "v2"}})
        answer = sb._do_site_config("alpha")
        self.assertIn("Bulgu: x", answer["handoff"])
        self.assertIn("handoff", answer["note"])

    def test_admin_endpoint_reads_the_note_and_404s_for_unknown_sites(self):
        self.write_site("alpha", T.alpha_yaml())
        got = self.c.get("/api/ops/sites/alpha/handoff")
        self.assertEqual((got.status_code, got.json()["text"]), (200, ""))
        sh.write_initial("alpha", "Bulgu: y", {})
        got = self.c.get("/api/ops/sites/alpha/handoff").json()
        self.assertIn("Bulgu: y", got["text"])
        self.assertEqual(got["bytes"], len(got["text"].encode("utf-8")))
        self.assertEqual(self.c.get("/api/ops/sites/nosuch/handoff").status_code, 404)


class DeleteAndRenameTest(HandoffBase):
    def test_site_delete_removes_the_note(self):
        self.write_site("alpha", T.alpha_yaml())
        sh.write_initial("alpha", "Bulgu", {})
        self.assertTrue(os.path.exists(sh.path("alpha")))
        self.assertEqual(self.c.delete("/api/ops/sites/alpha?purge=1").status_code, 200)
        self.assertFalse(os.path.exists(sh.path("alpha")))
        self.assertEqual(self.note(), "")

    def test_purge_function_removes_it_too(self):
        self.write_site("alpha", T.alpha_yaml())
        sh.write_initial("alpha", "Bulgu", {})
        result = purge.delete_site("alpha", purge=False, refresh=False)
        self.assertTrue(result["handoff_removed"])
        self.assertFalse(sh.delete("alpha"))     # nothing left to remove

    def test_rename_adds_a_change_entry(self):
        self.write_site("alpha", T.alpha_yaml())
        sh.write_initial("alpha", "Bulgu", {})
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "Yeni Ad"}).status_code, 200)
        self.assertIn("Alpha Dizi -> Yeni Ad", self.note())

    def test_move_carries_the_note_to_a_new_id(self):
        sh.write_initial("alpha", "Bulgu: taşınır", {})
        sh.append_change("alpha", "bir değişiklik")
        self.assertTrue(sh.move("alpha", "gamma"))
        self.assertFalse(os.path.exists(sh.path("alpha")))
        moved = sh.read("gamma")
        self.assertIn("# gamma devir notu", moved)
        self.assertIn("Bulgu: taşınır", moved)
        self.assertIn("bir değişiklik", moved)
        self.assertFalse(sh.move("nosuch", "delta"))

    def test_unsafe_site_ids_touch_nothing(self):
        for bad in ("../x", "A b", "", "a/b", ".hidden"):
            self.assertIsNone(sh.path(bad))
            self.assertFalse(sh.write_initial(bad, "x", {}))
            self.assertFalse(sh.append_change(bad, "x"))
            self.assertEqual(sh.read(bad), "")
            self.assertFalse(sh.delete(bad))
        self.assertFalse(os.path.exists(os.path.join(self.data.name, "site_handoffs")) and os.listdir(os.path.join(self.data.name, "site_handoffs")))


class SecretsTest(HandoffBase):
    def test_secrets_never_reach_the_file(self):
        text = ("Cookie: udys=SECRETCOOKIE123\nAuthorization: Bearer abcdefghijklmnop1234\n"
                "player https://host.example/v.mp4?token=TOKENVALUE99&expires=1800000000&x=1\napi_key=sk-abcdefghijklmnopqrst\n"
                "## heading\nnormal satır")
        sh.write_initial("alpha", text, {"user": ["Cookie: s=USERSECRET1"], "state": {"Liste": "/a?token=STATETOKEN"}})
        sh.append_change("alpha", "set-cookie: sid=CHANGESECRET\nJWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123", {"user": ["password=hunter2hunter2"]})
        body = self.note()
        for secret in ("SECRETCOOKIE123", "abcdefghijklmnop1234", "TOKENVALUE99", "1800000000", "sk-abcdefghijklmnopqrst", "USERSECRET1",
                       "STATETOKEN", "CHANGESECRET", "eyJhbGciOiJIUzI1NiJ9", "hunter2hunter2"):
            self.assertNotIn(secret, body, secret)
        self.assertIn("normal satır", body)
        self.assertNotIn("\n## heading", body)           # an agent heading cannot break the section structure
        self.assertEqual(body.count("## Bulgular ve çözümler"), 1)


class ScanFindingsTest(HandoffBase):
    def setUp(self):
        super().setUp()
        self.write_site("alpha", T.alpha_yaml())
        self.item("s1", "Dizi 1", "series", sources=("alpha",), poster="https://x/p1.jpg", videos=[("alpha", "episode", "s1:e1")])
        self.item("s2", "Dizi 2", "series", sources=("alpha",), poster="https://x/p2.jpg")
        self.item("m1", "Film 1", "movie", sources=("alpha",), videos=[("alpha", "movie", "")])
        self.item("m2", "Film 2", "movie", sources=("alpha",))

    def test_metrics_come_from_existing_records(self):
        result = {"ingested": 4, "scraped": 5, "series_crawl": {"series": 4, "errors": 1, "skipped": 2, "deferred": 1, "fallback": 1},
                  "blocked": {"series": 1, "episodes": 2, "probed": 9}}
        m = sh.scan_metrics("alpha", result)
        self.assertEqual((m["items"], m["titles"], m["poster"], m["sources"], m["year"]), (4, 4, 50, 50, 100))
        self.assertEqual((m["overview"], m["tmdb"]), (0, 0))
        self.assertEqual((m["crawl_series"], m["crawl_errors"], m["crawl_skipped"], m["fallback"], m["blocked"]), (4, 1, 3, 25, 3))
        line = sh._scan_line(m, 1, 1_800_000_000)
        self.assertTrue(line.startswith("- Tarama #1 (2027-01-15)"))
        self.assertIn("posterli %50", line)
        self.assertIn("dizi envanteri: 4 dizi, hata 1, atlanan 3", line)
        self.assertIn("yedek yoldan okunan dizi %25", line)
        self.assertIn("engelli 3", line)
        self.assertEqual(sh.scan_metrics("nosuch", {"ingested": 0}), {"items": 0})

    def test_first_two_scans_always_then_daily_and_only_on_change(self):
        t0 = 1_800_000_000.0
        m = {"items": 10, "poster": 80, "overview": 70, "sources": 60, "tmdb": 50}
        self.assertTrue(sh.append_scan_findings("alpha", m, now=t0))
        self.assertTrue(sh.append_scan_findings("alpha", m, now=t0 + 60))                    # second scan: still written
        self.assertFalse(sh.append_scan_findings("alpha", m, now=t0 + 120))                  # third, same day, same picture
        self.assertFalse(sh.append_scan_findings("alpha", m, now=t0 + DAY + 100))            # a day later but nothing changed
        scans = [x for x in self.note().split("\n") if x.startswith("- Tarama ")]
        self.assertEqual(len(scans), 2)
        self.assertIn('"scans":4', self.note())                                              # every scan is counted, written or not

    def test_a_change_after_a_day_is_written(self):
        t0 = 1_800_000_000.0
        m = {"items": 10, "poster": 80}
        for i in range(3):
            sh.append_scan_findings("alpha", m, now=t0 + i)
        self.assertFalse(sh.append_scan_findings("alpha", {**m, "poster": 40}, now=t0 + 3600))   # changed, but not a day yet
        self.assertTrue(sh.append_scan_findings("alpha", {**m, "poster": 40}, now=t0 + DAY + 10))
        self.assertIn("posterli %40", self.note())

    def test_record_scan_creates_a_base_note_and_skips_failed_scans(self):
        self.assertTrue(sh.record_scan("alpha", {"ingested": 4}, now=1_800_000_000.0))
        text = self.note()
        self.assertIn("## Güncel durum", text)
        self.assertIn("- Sürüm: v2", text)                  # a site without a note gets one from its config
        self.assertIn("- Tarama #1", text)
        self.assertFalse(sh.record_scan("alpha", {"error": "boom"}))
        self.assertEqual(self.note().count("- Tarama #"), 1)

    def test_a_handoff_problem_never_breaks_the_scan(self):
        for target, effect in (("scan_metrics", RuntimeError("db gone")), ("append_scan_findings", OSError("disk full")), ("_ensure_base", ValueError("x"))):
            with patch.object(sh, target, side_effect=effect):
                self.assertFalse(sh.record_scan("alpha", {"ingested": 1}))
        with patch.object(sh, "_atomic_write", side_effect=OSError("read-only")):
            self.assertFalse(sh.record_scan("alpha", {"ingested": 1}))
        self.assertFalse(sh.record_scan("../bad", {"ingested": 1}))

    def test_ingest_hook_swallows_errors(self):
        """``ingest_source`` calls ``record_scan`` inside its own try/except: even a raising hook leaves the result intact."""
        from app.library import ingest
        with patch.object(sh, "record_scan", side_effect=RuntimeError("boom")), \
                patch.object(ingest, "_ingest_source", return_value={"source": "alpha", "scraped": 1, "ingested": 1}):
            result = ingest.ingest_source("alpha", trigger="cli")
        self.assertEqual((result["source"], result["status"]), ("alpha", "success"))


if __name__ == "__main__":
    unittest.main()
