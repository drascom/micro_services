import _sandbox  # noqa: F401  (must be first)

from unittest import mock

from app.control.sitemap import store as map_store
from app.control.stages import a2_listing, actions, engine, state
from app.control.stages.a4_episodes import EpisodesStage
from app.core.extract import episodes, outline
from app.core.fetch import FetchError, Page

from test_t08_stages import html
from test_t18_a3_samples import ARCHIVE, C, HINT, C3Case

HUMAN = ("needs_human", "review", "engine_gap")
NO_PAGER = html("fakesite_c/archive.html").split('<div class="pager">')[0] + "</main></body></html>"


class ChainCase(C3Case):
    def setUp(self):
        super().setUp()
        self.missing, self.redirect = set(), {}
        stages = {k: v for k, v in engine.STAGES.items()}
        stages["A4"] = EpisodesStage()
        p = mock.patch.dict(engine.STAGES, stages, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def fetcher(self, url, *, site, page_type="page", timeout=None):
        if url in self.missing:
            raise FetchError("http_404", "not found", status=404, url=url)
        if url in self.redirect:
            page = super().fetcher(url, site=site, page_type=page_type)   # 302 -> the target serves the home page
            return Page(url, self.redirect[url], 200, page.html, "http")
        return super().fetcher(url, site=site, page_type=page_type, timeout=timeout)

    def states(self, sid):
        return {k: v["state"] for k, v in state.all_for(sid).items()}

    def humans(self, sid):
        return [k for k, v in self.states(sid).items() if v in HUMAN]


class ChainGatingTests(ChainCase):
    def test_upstream_rerun_holds_later_stages_and_one_card_asks_at_a_time(self):
        sid = self.run_c()
        actions.hint(sid, "A3", HINT)
        self.drain(10)
        before = self.states(sid)
        self.assertIn(before["A4"], ("passed", "passed_warn", "needs_human"), before)
        starts = len([m for m in state.messages(sid, "A4", limit=200) if "aşaması başladı" in m["text"]])
        self.overrides[ARCHIVE] = "<html><body><p>bos</p></body></html>"   # A2 now fails
        actions.rerun(sid, "A2")
        now = self.states(sid)
        self.assertEqual((now["A3"], now["A4"]), ("pending", "pending"))       # at once, before A2 even runs
        self.assertEqual(state.get(sid, "A4")["evidence"]["held_by"], "A2")
        while self.drain(1):
            self.assertLessEqual(len(self.humans(sid)), 1, self.states(sid))
        self.assertEqual(self.humans(sid), ["A2"])
        self.assertEqual(engine.waiting_for(sid, "A4"), "A2")
        self.assertEqual(starts, len([m for m in state.messages(sid, "A4", limit=200) if "aşaması başladı" in m["text"]]))
        self.assertEqual(actions.rerun(sid, "A4")["reason"], "waiting")       # a later stage cannot jump the chain
        # the archive is back: A2 passes with the same result -> A3 / A4 get their earlier results back
        del self.overrides[ARCHIVE]
        actions.rerun(sid, "A2")
        self.drain(10)
        self.assertEqual(self.states(sid), before)

    def test_out_of_order_chain_is_put_back_in_line(self):
        sid = self.run_c()
        state.update(sid, "A2", state="needs_human")      # a site from before the fix: A2 and A3 both "done" / asking
        state.update(sid, "A4", state="needs_human")
        engine.reconcile()
        st = self.states(sid)
        self.assertEqual((st["A2"], st["A3"], st["A4"]), ("needs_human", "pending", "pending"))
        self.assertEqual(state.get(sid, "A4")["evidence"]["held_prev"], "needs_human")


class ListingTests(ChainCase):
    def test_single_confirmed_archive_passes_and_end_number_page_two_is_probed(self):
        home = html("fakesite_c/home.html")
        self.overrides[C + "/"] = "\n".join(l for l in home.splitlines() if "/tur/" not in l)
        self.overrides[ARCHIVE] = NO_PAGER                                   # no page-2 link on the page
        sid = self.run_c()
        a2 = self.st(sid, "A2")
        self.assertIn(a2["state"], ("passed", "passed_warn"), a2["measurement"])
        listing = map_store.draft(sid)["pages"]["listing"]
        self.assertEqual(listing["pagination"], {"kind": "page_param", "template": "-{n:02}/"})
        self.assertIn(C + "/dizi-arsivi-02/", self.fetches)
        # page 2 does not exist: still one confirmed archive with many rows is enough
        self.missing.add(C + "/dizi-arsivi-02/")
        actions.rerun(sid, "A2")
        self.drain(2)
        a2 = self.st(sid, "A2")
        self.assertIn(a2["state"], ("passed", "passed_warn"), a2["measurement"])
        sc = next(c for c in a2["measurement"]["checks"] if c["name"] == "samples_count")
        self.assertEqual((sc["value"], sc["ok"]), (1, True))

    def test_unconfirmed_link_and_the_home_redirect_are_never_listing_samples(self):
        self.redirect[C + "/"] = C + "/tr2/"
        self.overrides[C + "/tr2/"] = html("fakesite_c/home.html")
        sid = self.run_c()
        self.assertEqual(map_store.draft(sid)["pages"]["home"]["url"], "/tr2/")
        self.assertIn("Ana sayfa /tr2/ adresine yönleniyor", " ".join(m["text"] for m in state.messages(sid, "A1")))
        ctx = engine.build_ctx(sid, engine.STAGES["A2"])
        ctx.samples = [C + "/rastgele-liste/", C + "/tr2/", ARCHIVE]
        got = a2_listing._candidates(ctx)
        self.assertNotIn(C + "/rastgele-liste/", got)
        self.assertNotIn(C + "/tr2/", got)
        self.assertEqual(got[0], ARCHIVE)
        out = actions.sample(sid, "A2", C + "/tr2/")
        self.assertTrue(out["home"])
        self.assertNotIn(C + "/tr2/", self.st(sid, "A2")["user_samples"])
        self.assertIn(actions.HOME_SAMPLE, [m["text"] for m in state.messages(sid, "A2")])


AZ = "".join(f'<li><a href="/diziler/dizi-{i}-son-bolum-izle-{i}/">Dizi {i}</a></li>' for i in range(2, 12))
SERIES = ('<html><body><nav class="menu"><a href="/">Ana</a></nav><main><h1>Omur Usta</h1>'
          '<div class="eps">' + "".join(f'<div class="bolumust"><a href="/omur-usta-{n}-bolum-izle-full-tek-parca/">{n}. Bölüm</a></div>'
                                        for n in (3, 2, 1)) + '</div>'
          '<aside><ul class="az-list">' + AZ + '</ul></aside></main></body></html>')


class EpisodeListTests(ChainCase):
    def test_az_list_of_other_series_is_not_the_episode_row(self):
        url = C + "/diziler/omur-usta-son-bolum-izle/"
        spec, ev = episodes.build_series_page(SERIES, url, outline.outline(SERIES, url), title_pattern="^/diziler/[^/]+/?$")
        self.assertIsNotNone(spec, ev)
        self.assertIn("bolumust", spec["row_selector"])
        self.assertEqual(ev["episodes"], 3)
        only_az = SERIES.replace(SERIES[SERIES.index('<div class="eps">'):SERIES.index("<aside>")], "")
        spec, ev = episodes.build_series_page(only_az, url, outline.outline(only_az, url), title_pattern="^/diziler/[^/]+/?$")
        self.assertIsNone(spec)
