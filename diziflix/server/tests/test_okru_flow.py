"""Star Trek: Strange New Worlds S4E2 ("Okru" button works on the site, but our stream list had VidMolly only).

Root cause (see ``docs``/README "Playback resolution"): the OK.ru (and ``/api/moly``) hand-off was opened with a
browser cookie session (an Obscura run, 10+ s) although the site only needs its own ``udys`` cookie; the fast VidMolly
``/dl/`` candidates finished in ~0.2 s and ``RESOLVE_GRACE`` (3 s) cut the slow hand-offs off ("zaman aşımı" at
3168 ms in ``last_resolver``). These tests pin: candidate extraction from the real page (fixture), the light
hand-off + browser fallback, OK.ru stream production from real metadata, the merge, language labels/order,
and "stage ok but no streams" being traced as a failure. Network-free (fake site, fake transports).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from app import config as app_config, db
from app.library import videos
from app.scraper import fetch, state
from app.scraper.providers import okru, trace
from app.scraper.site_extractors import discover, resolve_candidate

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://yabancidizi.news/dizi/star-trek-strange-new-worlds-izle-3/sezon-4/bolum-2"
PAGE = (FIXTURES / "yabancidizi_episode_snw_s4e2.html").read_text()
METADATA = (FIXTURES / "okru_metadata_snw_s4e2.json").read_text()
OKRU_LINK = "/FIVVLk6VDcxRbyZTr0X58ZazSYTLg=="
OKRU_HASH = "c047adfcd53cf73aef7b329104e2d638"


def _safe(token):
    return token.strip().replace("/", "_").replace("+", "-")


class FakeJar:
    def __init__(self):
        self.items = {}

    def set(self, name, value, domain=None, path=None):
        self.items[name] = value


class FakeSite:
    """Just enough of yabancidizi.news: hand-offs need ``need`` cookies; without them Cloudflare answers 520/403."""

    def __init__(self, need=("udys",), ajax_answer=None):
        self.need, self.ajax_answer, self.sessions = tuple(need), ajax_answer, []

    def factory(self, **kwargs):
        session = Mock()
        session.cookies = FakeJar()
        session.post.side_effect = lambda url, data=None, headers=None, timeout=None: self._post(session, url, data)
        session.get.side_effect = lambda url, headers=None, timeout=None: self._get(session, url)
        self.sessions.append(session)
        return session

    def _allowed(self, session):
        return all(name in session.cookies.items for name in self.need)

    def _post(self, session, url, data):
        if not self._allowed(session):
            return Mock(status_code=520, text='{"error_code":520}', json=Mock(return_value={"error_code": 520}))
        answer = self.ajax_answer if self.ajax_answer is not None else {
            "api": False, "success": 1, "vs_id": "15",
            "api_iframe": "https://yabancidizi.news/api/ruplay/" + _safe(data["link"])}
        return Mock(status_code=200, text=json.dumps(answer), json=Mock(return_value=answer))

    def _get(self, session, url):
        if not self._allowed(session):
            return Mock(status_code=403, text="blocked")
        if "/api/ruplay/" in url:
            return Mock(status_code=200, text='<iframe src="https://ok.ru/videoembed/15385614158383"></iframe>')
        if "/api/moly/" in url:
            return Mock(status_code=200, text='<iframe src="https://vidmoly.biz/embed-nbdb0p8nd5qa.html"></iframe>')
        return Mock(status_code=404, text="")


def okru_candidate():
    return next(c for c in discover("yabancidizi", PAGE, PAGE_URL) if c["label"] == "OK.ru")


class ExtractionTests(unittest.TestCase):
    def test_okru_button_is_extracted_with_its_handoff_and_language(self):
        found = discover("yabancidizi", PAGE, PAGE_URL)
        self.assertEqual([(c["label"], c.get("lang")) for c in found], [
            ("İngilizce Altyazılı İndir", "en"), ("Türkçe Altyazılı İndir", "tr"), ("VidMolly", "tr"), ("OK.ru", "tr")])
        self.assertEqual([c["url"] for c in found[:3]], [
            "https://vidmoly.me/dl/i0q31suzt1f5", "https://vidmoly.me/dl/nbdb0p8nd5qa",
            "https://yabancidizi.news/api/moly/TbSIXT7BnXsiqhpwXl4enBaAk9c="])
        self.assertEqual(found[3]["language"], "Türkçe altyazı")
        self.assertEqual(found[3]["handoff"], {
            "provider": "okru", "url": "https://yabancidizi.news/ajax/service",
            "link": OKRU_LINK, "hash": OKRU_HASH, "querytype": "alternate"})
        # "Mac" is the site's own player (ydx.molystream.org embed behind /api/drive/): not a supported provider
        self.assertNotIn("Mac", [c["label"] for c in found])

    def test_active_tab_is_read_despite_duplicate_class_attributes(self):
        def page(active):
            classes = ("ui pointing item active", "ui pointing item") if active == "tr" else ("ui pointing item", "ui pointing item active")
            return f'''
            <a class="{classes[0]}" href="#" data-eid="a" data-type="1" class="item" data-lango><i class="x"></i>Türkçe Altyazı</a>
            <a class="{classes[1]}" href="#" data-eid="b" data-type="3" class="item" data-lango>İngilizce Altyazı</a>
            <div class="alternatives-for-this">
              <div class="item active" data-hash="h" data-link="l" data-querytype="alternate">Okru</div>
            </div>'''
        self.assertEqual(discover("yabancidizi", page("tr"), PAGE_URL)[0]["language"], "Türkçe altyazı")
        english = discover("yabancidizi", page("en"), PAGE_URL)[0]
        self.assertEqual((english["lang"], english["language"]), ("en", "İngilizce altyazı"))

    def test_language_is_left_out_when_the_page_does_not_say(self):
        html = '<div class="alternatives-for-this"><div data-hash="h" data-link="l" data-querytype="alternate">Okru</div></div>'
        found = discover("yabancidizi", html, PAGE_URL)
        self.assertEqual(len(found), 1)
        self.assertNotIn("lang", found[0])
        self.assertNotIn("language", found[0])


class HandoffTests(unittest.TestCase):
    def resolve(self, site, load_cookies):
        with patch("curl_cffi.requests.Session", side_effect=site.factory):
            trace.begin()
            result = resolve_candidate("yabancidizi", okru_candidate(), PAGE_URL, load_cookies)
            return result, trace.take()

    def test_light_cookie_opens_the_okru_handoff_without_a_browser(self):
        site, browser = FakeSite(), Mock(side_effect=AssertionError("no browser session needed"))
        result, events = self.resolve(site, browser)
        self.assertEqual(result, {"url": "https://ok.ru/videoembed/15385614158383", "label": "OK.ru"})
        session = site.sessions[0]
        self.assertLess(abs(int(session.cookies.items["udys"]) / 1000 - time.time()), 60)   # Date.now() in ms
        post = session.post.call_args
        self.assertEqual(post.args[0], "https://yabancidizi.news/ajax/service")
        self.assertEqual(post.kwargs["data"], {"link": OKRU_LINK, "hash": OKRU_HASH, "querytype": "alternate", "type": "videoGet"})
        self.assertEqual(post.kwargs["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(session.get.call_args.args[0], "https://yabancidizi.news/api/ruplay/" + _safe(OKRU_LINK))
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("yabancidizi.handoff", True)])
        session.close.assert_called_once()

    def test_browser_cookies_are_only_the_fallback_when_the_site_blocks_the_light_session(self):
        site = FakeSite(need=("udys", "cf_clearance"))
        jar = [{"name": "cf_clearance", "value": "abc", "domain": "yabancidizi.news", "path": "/"}]
        browser = Mock(return_value=jar)
        result, events = self.resolve(site, browser)
        self.assertEqual(result["url"], "https://ok.ru/videoembed/15385614158383")
        browser.assert_called_once()
        self.assertEqual(len(site.sessions), 2)
        self.assertEqual(set(site.sessions[1].cookies.items), {"cf_clearance", "udys"})   # jar + the light cookie
        self.assertEqual([(e["stage"], e["ok"]) for e in events],
                         [("yabancidizi.handoff", False), ("yabancidizi.handoff.browser", True)])
        self.assertIn("520", events[0]["error"])

    def test_a_definite_no_opens_no_browser_and_says_why(self):
        site = FakeSite(ajax_answer={"success": 0, "error": "hash yok"})
        result, events = self.resolve(site, Mock(side_effect=AssertionError("a definite answer needs no browser")))
        self.assertIsNone(result)
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("yabancidizi.handoff", False)])
        self.assertIn("hash yok", events[0]["error"])

    def test_browser_failure_is_traced(self):
        site = FakeSite(need=("udys", "cf_clearance"))
        result, events = self.resolve(site, Mock(side_effect=RuntimeError("obscura timeout")))
        self.assertIsNone(result)
        self.assertEqual([e["stage"] for e in events], ["yabancidizi.handoff", "yabancidizi.handoff.browser"])
        self.assertIn("obscura timeout", events[1]["error"])

    def test_vidmolly_moly_handoff_uses_the_same_light_cookie(self):
        site = FakeSite()
        moly = next(c for c in discover("yabancidizi", PAGE, PAGE_URL) if "/api/moly/" in c["url"])
        with patch("curl_cffi.requests.Session", side_effect=site.factory):
            result = resolve_candidate("yabancidizi", moly, PAGE_URL, Mock(side_effect=AssertionError("no browser")))
        self.assertEqual(result, {"url": "https://vidmoly.biz/embed-nbdb0p8nd5qa.html", "label": "VidMolly"})


class OkruProviderTests(unittest.TestCase):
    def resolve(self, metadata_text):
        with patch.object(okru.fetch, "post_url", return_value=metadata_text), \
             patch.object(okru.fetch, "fetch_url", side_effect=fetch.FetchError("no page in tests")):
            trace.begin()
            result = okru.resolve("https://ok.ru/videoembed/15385614158383", referer=PAGE_URL)
            return result, trace.take()

    def test_real_metadata_yields_ranked_mp4_streams(self):
        result, events = self.resolve(METADATA)
        self.assertEqual([(s["type"], s["quality"], s["label"]) for s in result["streams"]],
                         [("mp4", "1440p", "1440p"), ("mp4", "1080p", "1080p")])
        self.assertEqual((result["provider"], result["duration"]), ("OK.ru", 3565))
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("okru.metadata", True)])

    def test_ondemand_hls_is_used_when_there_is_no_mp4(self):
        data = json.loads(METADATA)
        data["videos"] = []
        result, _ = self.resolve(json.dumps(data))
        self.assertEqual(len(result["streams"]), 1)
        self.assertEqual((result["streams"][0]["type"], result["streams"][0]["label"]), ("hls", "auto"))
        self.assertEqual(result["streams"][0]["url"], data["ondemandHls"])

    def test_nothing_playable_is_explained(self):
        data = {"videos": [{"name": "hd", "url": "https://vd.okcdn.ru/x", "disallowed": True}], "movie": {"duration": 1}}
        result, events = self.resolve(json.dumps(data))
        self.assertIsNone(result)
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("okru.metadata", False), ("okru.html", False)])
        self.assertIn("all disallowed", events[0]["error"])


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, name, value in ((app_config, "DB_PATH", str(Path(self.temp.name) / "t.db")),
                                    (state, "STATE_DIR", str(Path(self.temp.name) / "state"))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.init()
        videos.reset_caches()
        videos._last_prune = 0.0
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                   "media_type,updated_at) VALUES ('vs1','c1','yabancidizi','k','','movie',?,'page','mp4',1)", (PAGE_URL,))

    def last(self):
        return state.get_site_state("yabancidizi")["last_resolver"]


class FullFlowTests(PipelineCase):
    """The real extractor, hand-off, registry, OK.ru and VidMolly code; only the network is fake."""

    @staticmethod
    def vidmolly_page(url, **kwargs):
        code = url.rsplit("embed-", 1)[-1].split(".")[0]
        return '<script>sources:[{file:"https://cdn.example/hls/%s/index.m3u8"}]</script>' % code

    @contextmanager
    def flow(self, site=None, okru_delay=0.0, **cfg):
        site = site or FakeSite()

        def post_url(url, **kwargs):
            time.sleep(okru_delay)   # the slow step comes last: an abandoned thread ends without touching the network
            return METADATA
        patches = [
            patch("app.scraper.fetch.page_bundle", return_value={"initial_html": PAGE, "html": PAGE, "frames": [], "network_pages": []}),
            patch("curl_cffi.requests.Session", side_effect=site.factory),
            patch("app.scraper.fetch.post_url", side_effect=post_url),
            patch("app.scraper.fetch.fetch_url", side_effect=self.vidmolly_page),
        ] + [patch.object(app_config, key, value) for key, value in cfg.items()]
        for patcher in patches:
            patcher.start()
        try:
            yield site
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    def test_okru_streams_sit_between_the_measured_turkish_file_and_the_english_one_without_a_claimed_language(self):
        with self.flow():
            payload = videos.streams("c1", kind="video")
        # the site's TR tab says "Türkçe altyazı" for the OK.ru files too, but nothing measured it: no language in the label
        self.assertEqual([s["label"] for s in payload["streams"]], [
            "VidMolly · Türkçe altyazı · auto",
            "OK.ru · 1440p",
            "OK.ru · 1080p",
            "VidMolly · İngilizce altyazı · auto",
        ])
        self.assertEqual([s["type"] for s in payload["streams"]], ["hls", "mp4", "mp4", "hls"])
        self.assertEqual([s["provider"] for s in payload["streams"]], ["VidMolly", "OK.ru", "OK.ru", "VidMolly"])
        # contract: old fields as before (OK.ru stays sub_mode "none"), the additions say it is merely not known
        self.assertNotIn("lang", payload["streams"][0])
        self.assertNotIn("language", payload["streams"][0])
        self.assertEqual([(s["sub_mode"], s["audio_lang"], s["hard_lang"]) for s in payload["streams"]],
                         [("hard", None, "tr"), ("none", None, None), ("none", None, None), ("none", None, None)])
        self.assertEqual([s["sub_known"] for s in payload["streams"]], [True, False, False, True])
        self.assertEqual([s["site_lang_hint"] for s in payload["streams"]], ["tr", "tr", "tr", "en"])
        self.assertEqual([s["mirror_of"] for s in payload["streams"]], [None, None, None, None])
        self.assertEqual(payload["duration"], 3565)
        self.assertEqual(len({s["label"] for s in payload["streams"]}), 4)   # every menu row reads differently
        # audio[] is untouched: no language is invented for the OK.ru file (the client keeps treating an unknown one as a joker)
        self.assertEqual([(a["lang"], a["label"]) for a in payload["audio"]], [(None, "Orijinal")])

    def test_the_admin_trail_shows_the_okru_candidate_as_ok_with_its_stage(self):
        with self.flow():
            videos.streams("c1", kind="video")
        last = self.last()
        self.assertTrue(last["ok"])
        self.assertEqual(last["streams"], 5)   # raw count: /dl/ TR + /api/moly TR (same file, deduplicated later) + 2x OK.ru + /dl/ EN
        by_label = {c["label"]: c for c in last["candidates"]}
        self.assertEqual(set(by_label), {"OK.ru", "VidMolly", "Türkçe Altyazılı İndir", "İngilizce Altyazılı İndir"})
        okru_row = by_label["OK.ru"]
        self.assertEqual((okru_row["ok"], okru_row["stage"], okru_row["host"], okru_row["lang"], okru_row["error"]),
                         (True, "okru.metadata", "ok.ru", "tr", ""))

    def test_the_moly_handoff_of_the_same_file_is_deduplicated_not_listed_twice(self):
        with self.flow():
            result = videos.resolve_source(db.query_one("SELECT * FROM video_sources WHERE id='vs1'"), force=True)
        urls = [s["url"] for s in result["streams"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(sum(1 for s in result["streams"] if "nbdb0p8nd5qa" in s["url"]), 1)   # /dl/ TR == /api/moly TR

    def test_the_same_file_resigned_by_the_second_candidate_is_one_stream_not_auto_2(self):
        """Live S4E2: the /dl/ link and the /api/moly hand-off lead to ONE VidMolly file; each fetch of its embed page
        returns a freshly signed master.m3u8 (same host and path, other ``s``/``t``), so the exact-URL dedup missed it
        and the menu showed "VidMolly · Türkçe altyazı · auto (2)"."""
        counter = []

        def signed_page(url, **kwargs):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            counter.append(code)
            return ('<script>sources:[{file:"https://prx-vi-a-1.vmpx.online/hls2/01/02715/%s_,n,l,.urlset/master.m3u8'
                    '?t=SIG%d&s=%d&e=86400&i=a1b&sp=450"}]</script>' % (code, len(counter), 1000 + len(counter)))
        with self.flow(), patch("app.scraper.fetch.fetch_url", side_effect=signed_page):
            payload = videos.streams("c1", kind="video")
        vid = [s for s in payload["streams"] if s["provider"] == "VidMolly"]
        self.assertGreaterEqual(counter.count("nbdb0p8nd5qa"), 2, "both candidates did resolve the same file")
        self.assertEqual([s["label"] for s in vid], ["VidMolly · Türkçe altyazı · auto", "VidMolly · İngilizce altyazı · auto"])
        self.assertEqual(len({s["variant_id"] for s in vid}), 2)
        self.assertEqual(len(payload["streams"]), 4)
        self.assertNotIn("(2)", " ".join(s["label"] for s in payload["streams"]))
        self.assertTrue(all(s["mirror_of"] is None for s in payload["streams"]))

    def test_a_handoff_that_needs_a_few_seconds_is_kept_within_the_grace_period(self):
        with self.flow(okru_delay=0.6, RESOLVE_GRACE=3.0):
            payload = videos.streams("c1", kind="video")
        self.assertIn("OK.ru · 1080p", [s["label"] for s in payload["streams"]])

    def test_a_handoff_cut_by_the_grace_period_is_reported_as_such(self):
        # the failure seen on the live server: fast VidMolly candidates finish, the slow hand-off is cut and
        # the trail must say so (not just "zaman aşımı")
        with self.flow(okru_delay=0.8, RESOLVE_GRACE=0.2):
            payload = videos.streams("c1", kind="video")
            self.assertNotIn("OK.ru", [s["provider"] for s in payload["streams"]])
            row = next(c for c in self.last()["candidates"] if c["label"] == "OK.ru")
            self.assertFalse(row["ok"])
            self.assertTrue(row["error"].startswith("zaman aşımı: ilk akıştan sonra 0.2 sn bekleme süresi doldu"))
            time.sleep(1.0)   # let the abandoned thread finish while the fakes are still installed


class OrderAndLabelTests(PipelineCase):
    def resolve(self, candidates, provider, **cfg):
        videos.reset_caches()
        db.execute("UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id='vs1'")   # no cached payload
        patches = [
            patch("app.scraper.fetch.page_bundle", return_value={"initial_html": "<html></html>", "html": "", "frames": [], "network_pages": []}),
            patch("app.scraper.site_extractors.discover", return_value=candidates),
            patch("app.scraper.site_extractors.resolve_candidate", side_effect=lambda site, c, page, cookies: c),
            patch("app.scraper.providers.resolve", side_effect=provider),
        ] + [patch.object(app_config, key, value) for key, value in cfg.items()]
        for patcher in patches:
            patcher.start()
        try:
            return videos.streams("c1", kind="video")
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    @staticmethod
    def cand(code, lang=None):
        item = {"url": f"https://vidmolly.biz/embed-{code}.html", "label": code}
        if lang:
            item["lang"] = lang
            item["language"] = {"tr": "Türkçe altyazı", "en": "İngilizce altyazı"}[lang]
        return item

    @staticmethod
    def provider(delays=None, name="VidMolly"):
        delays = delays or {}

        def resolve(url, **kwargs):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            time.sleep(delays.get(code, 0))
            return {"provider": name, "duration": 0,
                    "streams": [{"url": f"https://cdn.example/{code}.m3u8", "type": "hls", "quality": "auto", "label": "auto"}]}
        return resolve

    def test_turkish_subtitle_files_come_before_english_even_when_english_answers_first(self):
        payload = self.resolve([self.cand("en1", "en"), self.cand("tr1", "tr")], self.provider({"tr1": 0.7}))
        self.assertEqual([s["label"] for s in payload["streams"]],
                         ["VidMolly · Türkçe altyazı · auto", "VidMolly · İngilizce altyazı · auto"])
        self.assertTrue(payload["streams"][0]["url"].endswith("tr1.m3u8"))

    def test_speed_order_still_applies_inside_one_language(self):
        payload = self.resolve([self.cand("a", "tr"), self.cand("b", "tr")], self.provider({"a": 0.7}))
        self.assertEqual([s["url"].rsplit("/", 1)[-1] for s in payload["streams"]], ["b.m3u8", "a.m3u8"])
        payload = self.resolve([self.cand("a", "tr"), self.cand("b", "tr")], self.provider({"a": 0.7}), RESOLVE_FAST_FIRST=False)
        self.assertEqual([s["url"].rsplit("/", 1)[-1] for s in payload["streams"]], ["a.m3u8", "b.m3u8"])

    def test_unknown_language_is_not_guessed_and_keeps_the_old_label(self):
        payload = self.resolve([self.cand("x")], self.provider())
        self.assertEqual([s["label"] for s in payload["streams"]], ["VidMolly · auto"])

    def test_identical_labels_are_made_unique(self):
        payload = self.resolve([self.cand("x"), self.cand("y")], self.provider())
        self.assertEqual([s["label"] for s in payload["streams"]], ["VidMolly · auto", "VidMolly · auto (2)"])

    @staticmethod
    def okru_cand():
        return {"url": "https://ok.ru/videoembed/77", "label": "OK.ru", "lang": "tr", "language": "Türkçe altyazı"}

    @staticmethod
    def mixed_provider(okru_delay=0.0):
        def resolve(url, **kwargs):
            if "ok.ru" in url:
                time.sleep(okru_delay)
                return {"provider": "OK.ru", "duration": 0, "variant": "77",
                        "streams": [{"url": "https://ok1.vkuser.net/?id=1&type=5&sig=A", "type": "mp4", "quality": "1080p", "label": "1080p"}]}
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            return {"provider": "VidMolly", "duration": 0, "variant": code,
                    "streams": [{"url": f"https://cdn.example/{code}.m3u8", "type": "hls", "quality": "auto", "label": "auto"}]}
        return resolve

    def test_unknown_subtitle_state_sits_between_measured_turkish_and_english_however_fast_it_answers(self):
        cands = [self.cand("en1", "en"), self.okru_cand(), self.cand("tr1", "tr")]
        for fast in (True, False):
            payload = self.resolve(cands, self.mixed_provider(), RESOLVE_FAST_FIRST=fast)
            self.assertEqual([s["label"] for s in payload["streams"]],
                             ["VidMolly · Türkçe altyazı · auto", "OK.ru · 1080p", "VidMolly · İngilizce altyazı · auto"], fast)
            self.assertEqual([s["sub_known"] for s in payload["streams"]], [True, False, True])

    def test_a_spare_copy_on_another_host_is_kept_as_yedek_right_behind_its_primary(self):
        """Same file (variant) and quality on a second CDN host: a real backup, kept and named, not "(2)"."""
        def provider(url, **kwargs):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            host = {"a": "h1", "b": "h1", "c": "h2", "d": "h3"}[code]
            file = {"a": "x", "b": "y", "c": "x", "d": "x"}[code]
            return {"provider": "VidMolly", "duration": 0, "variant": file,
                    "streams": [{"url": f"https://{host}.cdn/{file}.m3u8?t={code}", "type": "hls", "quality": "auto", "label": "auto"}]}
        cands = [self.cand("a", "tr"), self.cand("b", "tr"), self.cand("c", "tr"), self.cand("d", "tr")]
        payload = self.resolve(cands, provider, RESOLVE_FAST_FIRST=False)
        streams = payload["streams"]
        self.assertEqual([s["url"].split("?")[0] for s in streams],
                         ["https://h1.cdn/x.m3u8", "https://h2.cdn/x.m3u8", "https://h3.cdn/x.m3u8", "https://h1.cdn/y.m3u8"])
        self.assertEqual([s["label"] for s in streams], [
            "VidMolly · Türkçe altyazı · auto", "VidMolly · Türkçe altyazı · auto · yedek",
            "VidMolly · Türkçe altyazı · auto · yedek 2", "VidMolly · Türkçe altyazı · auto (2)"])   # y: other file, same label: old rule
        primary = streams[0]
        self.assertEqual([s["mirror_of"] for s in streams], [None, primary["variant_id"] + ":auto", primary["variant_id"] + ":auto", None])
        self.assertEqual(len({s["variant_id"] for s in streams[:3]}), 1, "variant shared: the client groups its tracks by variant")
        self.assertEqual(len({s["attempt_token"] for s in streams}), 1)
        # one variant = one audio/subtitle choice: the spare copies add no extra audio row
        self.assertEqual([(a["id"], len(a["stream_ids"])) for a in payload["audio"]], [("a_orig", 2)])


class EmptyButOkTests(PipelineCase):
    def test_a_stage_that_says_ok_without_streams_is_a_failure_with_a_reason(self):
        def provider(url, **kwargs):
            trace.note("okru.metadata", "ok.ru", True, time.monotonic())
            return {"provider": "OK.ru", "duration": 0, "streams": []}

        candidate = {"url": "https://ok.ru/videoembed/1", "label": "OK.ru"}
        patches = [
            patch("app.scraper.fetch.page_bundle", return_value={"initial_html": "<html></html>", "html": "", "frames": [], "network_pages": []}),
            patch("app.scraper.site_extractors.discover", return_value=[candidate]),
            patch("app.scraper.site_extractors.resolve_candidate", side_effect=lambda site, c, page, cookies: c),
            patch("app.scraper.providers.resolve", side_effect=provider),
        ]
        for patcher in patches:
            patcher.start()
        try:
            with self.assertRaises(ValueError):
                videos.resolve_source(db.query_one("SELECT * FROM video_sources WHERE id='vs1'"), force=True)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        last = self.last()
        self.assertFalse(last["ok"])
        self.assertEqual(last["streams"], 0)
        row = last["candidates"][0]
        self.assertFalse(row["ok"])
        self.assertIn("başarılı görünüyor ama akış yok", row["error"])
        self.assertIn("okru.metadata", row["error"])
        self.assertTrue(last["error"])


if __name__ == "__main__":
    unittest.main()
