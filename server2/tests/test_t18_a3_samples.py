import _sandbox  # noqa: F401  (must be first)

import re
from unittest import mock

from app.control.sitemap import store as map_store
from app.control.stages import actions, engine, hints, status
from app.core.extract import urlpatterns
from app.core.fetch import FetchError, Page
from app.core.llm import fake

from test_t08_stages import Case, html

C = "https://fakesite-c.test"
ARCHIVE = C + "/dizi-arsivi-01/"
EPISODE = re.compile(r"-\d+-bolum-izle-full-tek-parca/$")
ROUTES = [(r"^/$", "home.html"), (r"^/dizi-arsivi-\d+/$", "archive.html"), (r"^/tur/[^/]+/$", "archive.html"), (r"^/diziler/[^/]+/$", "series.html"),
          (r"^/[^/]+-\d+-bolum-izle-full-tek-parca/$", "episode.html")]


class C3Case(Case):
    def fetcher(self, url, *, site, page_type="page", timeout=None):
        self.fetches.append(url)
        path = re.sub(r"^https?://[^/]+", "", url) or "/"
        if url in self.overrides:
            return Page(url, url, 200, self.overrides[url], "http")
        for rx, name in ROUTES:
            if re.search(rx, path):
                body = html("fakesite_c/" + name)
                if name == "archive.html" and path != "/dizi-arsivi-01/":   # other pages / lists: other rows
                    body = body.replace("arsiv-", re.sub(r"\W", "", path) + "-")
                return Page(url, url, 200, body, "http")
        raise FetchError("http_404", "not found", status=404, url=url)

    def run_c(self, limit=12, strict_poster=False):
        sid = self.site(C)
        if strict_poster:   # "1 çalışan örnek yeter" made poster_fill a warning: these tests need the no-poster FAILURE
            draft = map_store.draft(sid)
            draft.setdefault("meta", {}).setdefault("title", {})["expect"] = {"poster_fill": {"optional": False}}
            map_store.save_draft(sid, draft)
        actions.start(sid)
        self.drain(limit)
        return sid


TITLE_YAML = """Here is the fix you asked for.
```yaml
pages:
  title:
    url_pattern: "^/diziler/[^/]+/?$"
    key: {regex: "^/diziler/([^/?#]+)", template: "{1}"}
    type: {from: url, map: {diziler: series}, default: series}
    fields:
      title: {selector: h1}
      poster: {selector: ".category_image img", attr: data-src, fallback: []}
      year: {selector: span.yil, regex: '([0-9]{4})', cast: int, fallback: {selector: h1}}
```
Hope it helps."""
HINT = 'dizi sayfasında afiş class="category_image" içinde; bölüm sayfasında id="benzerli" içinde'


def paths(sid, stage="A3"):
    from app.control.stages import state
    return [s["url"].replace(C, "") for s in (state.get(sid, stage)["measurement"] or {}).get("samples") or []]


class ChainAndSampleTests(C3Case):
    def test_a3_waits_for_a2_then_takes_listing_rows_not_lists_or_episodes(self):
        sid = self.site(C)
        actions.start(sid)
        self.drain(1)                                                     # A1 only
        self.assertEqual(self.st(sid, "A3")["state"], "pending")
        self.assertEqual(engine.waiting_for(sid, "A3"), "A2")             # chain: A3 does not start before A2 passes
        self.drain(10)
        self.assertEqual(self.st(sid, "A2")["state"], "passed")
        got = paths(sid)
        self.assertTrue(got and all(p.startswith("/diziler/") for p in got), got)
        self.assertTrue(any("arsiv-" in p for p in got), got)              # the A2 handoff (archive rows) was consumed
        a3 = next(st for st in status.overview(sid)["stages"] if st["id"] == "A3")
        self.assertIn("önceki aşamadan gelen (A2)", a3["handoff_in"])
        self.assertEqual(self.st(sid, "A3")["state"], "passed_warn")       # no poster: a warning only (fill threshold)

    def test_hint_selectors_are_tried_first_and_raise_poster_fill(self):
        sid = self.run_c()
        before = {c["name"]: c["value"] for c in self.st(sid, "A3")["measurement"]["checks"]}
        self.assertEqual(before["poster_fill"], 0.0)
        actions.hint(sid, "A3", HINT)
        self.drain(3)
        row = self.st(sid, "A3")
        after = {c["name"]: c["value"] for c in row["measurement"]["checks"]}
        self.assertIn(row["state"], ("passed", "passed_warn"), row["measurement"])
        self.assertEqual(after["poster_fill"], 1.0)
        poster = map_store.draft(sid)["pages"]["title"]["fields"]["poster"]
        self.assertIn(".category_image img", str(poster))
        texts = [m["text"] for m in status.overview(sid)["stages"][2]["messages"]]
        self.assertTrue(any(t.startswith("İpucundan 2 seçici denendi") for t in texts), texts)

    def test_list_address_sample_takes_rows_and_exclusion_persists(self):
        sid = self.run_c()
        out = actions.sample(sid, "A3", ARCHIVE)
        self.assertGreaterEqual(out["from_list"], 3)
        pinned = self.st(sid, "A3")["user_samples"]
        self.assertNotIn(ARCHIVE, pinned)
        self.assertTrue(all("/diziler/arsiv-" in u for u in pinned), pinned)
        actions.hint(sid, "A3", HINT)
        self.drain(3)
        gone = C + paths(sid)[0]
        actions.exclude(sid, "A3", gone)
        self.drain(3)
        self.assertNotIn(gone, [C + p for p in paths(sid)])
        self.assertGreaterEqual(len(paths(sid)), 3)                         # more samples were taken
        actions.rerun(sid, "A3")
        self.drain(3)
        self.assertEqual(self.st(sid, "A3")["excluded_samples"], [gone])
        self.assertNotIn(gone, [C + p for p in paths(sid)])

    def test_a2_changed_listing_resets_a3_with_new_rows(self):
        sid = self.run_c()
        actions.hint(sid, "A3", HINT)
        self.drain(3)
        self.assertIn(self.st(sid, "A3")["state"], ("passed", "passed_warn"))
        self.overrides[ARCHIVE] = html("fakesite_c/archive.html").replace("arsiv-", "yeni-")
        actions.rerun(sid, "A2")                                            # fresh: the cached archive is not used
        self.drain(1)
        self.assertEqual(self.st(sid, "A3")["state"], "pending")
        self.assertIn("A2 değişti", [m["text"] for m in status.overview(sid)["stages"][2]["messages"]][-1])
        self.drain(3)
        self.assertIn(self.st(sid, "A3")["state"], ("passed", "passed_warn"))
        self.assertTrue(any("/diziler/yeni-" in p for p in paths(sid)), paths(sid))


class LlmReplyTests(C3Case):
    def test_prose_reply_parses_and_bad_schema_gets_one_format_fix(self):
        fake.script(["not yaml at all, sorry", TITLE_YAML])
        sid = self.run_c(strict_poster=True)
        row = self.st(sid, "A3")
        self.assertIn(row["state"], ("passed", "passed_warn"), row["measurement"])
        self.assertEqual([r["stage"] for r in self.llm_rows()], ["A3", "A3"])   # round 1 + its format fix
        self.assertIn("could not be used", fake.prompts[1])
        self.assertEqual(row["round"], 1)
        fields = map_store.draft(sid)["pages"]["title"]["fields"]
        self.assertNotIn("fallback", fields["poster"])                            # fallback: [] dropped
        self.assertEqual(fields["year"]["fallback"], [{"selector": "h1"}])         # single rule -> list

    def test_key_hint_pairs_units(self):
        rule = urlpatterns.key_rule([C + "/diziler/omur-usta-son-bolum-izle/", C + "/diziler/b/"])
        from app.control.sitemap import classify
        self.assertEqual(classify.key_from(rule, C + "/diziler/omur-usta-son-bolum-izle/", C), "omur-usta-son-bolum-izle")
        self.assertEqual(urlpatterns.head_pattern([C + "/diziler/a/"]), "^/diziler/[^/]+/?$")
        self.assertEqual([(f, s) for f, s, _ in hints.pairs(HINT)], [("poster", ".category_image"), ("poster", "#benzerli")])
        self.assertEqual(hints.pairs("yıl <span class='yil x'> içinde, özet .aciklama"),
                         [("year", ".yil", "span"), ("overview", ".aciklama", "")])
