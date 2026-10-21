"""Generic resolver types (``app.scraper.resolvers``): discover on inline HTML, mock-transport ``resolve_candidate``
(``ajax_handoff`` hand-off + browser-cookie fallback, ``data_attr_token`` hand-off page, ``json_api``), ``validate``,
``catalog``, and the yabancidizi equivalence (the site module's discovery re-expressed with the generic types).
Network-free (fake ``ctx.fetch``)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.scraper import resolvers
from app.scraper.fetch import FetchError
from app.scraper.providers import trace
from app.scraper.resolvers import ajax_handoff, anchor_host, data_attr_token, iframe, json_api
from app.scraper.site_extractors import yabancidizi

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://site.example"
PAGE_URL = BASE + "/film/x"


class FakeFetch:
    """``ctx.fetch`` stand-in: ``post`` / ``get`` are ``fn(url, data_or_None, headers) -> text`` (or raise)."""

    def __init__(self, post=None, get=None):
        self.posts, self.gets, self._post, self._get = [], [], post, get

    def post_url(self, url, *, data, headers=None, timeout=None, retries=None, check_robots=False, min_delay=None):
        self.posts.append({"url": url, "data": data, "headers": headers or {}, "check_robots": check_robots})
        return self._post(url, data, headers or {})

    def fetch_url(self, url, *, headers=None, timeout=None, retries=None, check_robots=True, min_delay=None):
        self.gets.append({"url": url, "headers": headers or {}, "check_robots": check_robots})
        return self._get(url, None, headers or {})


def make_ctx(fetch=None, base_url=BASE):
    return resolvers.Ctx(site_id="site", base_url=base_url, cfg=None, fetch=fetch or FakeFetch())


class FakeSite:
    """A cookie-gated site: hand-off calls answer 520/403 unless the Cookie header carries every ``need`` cookie."""

    def __init__(self, need=("udys",), ajax_answer=None):
        self.need, self.ajax_answer = tuple(need), ajax_answer
        self.fetch = FakeFetch(post=self._post, get=self._get)

    def _allowed(self, headers):
        jar = headers.get("Cookie", "")
        return all(f"{name}=" in jar for name in self.need)

    def _post(self, url, data, headers):
        if not self._allowed(headers):
            raise FetchError("HTTP 520", 520)
        origin = url.split("/ajax", 1)[0]
        answer = self.ajax_answer if self.ajax_answer is not None else {
            "success": 1, "api_iframe": origin + "/api/play/" + data["link"].replace("/", "_")}
        return json.dumps(answer)

    def _get(self, url, data, headers):
        if not self._allowed(headers):
            raise FetchError("HTTP 403", 403)
        if "/api/play/" in url:
            return '<html><iframe src="https://ok.ru/videoembed/123"></iframe></html>'
        if "/api/moly/" in url:
            return '<iframe src="https://vidmoly.biz/embed-abc.html"></iframe>'
        raise FetchError("HTTP 404", 404)


class IframeTests(unittest.TestCase):
    HTML = '''
    <div id="player"><iframe src="https://vidmoly.me/embed-a.html"></iframe>
      <iframe src="/embed/b"></iframe><iframe src="https://vidmoly.me/embed-a.html"></iframe>
      <iframe data-src="https://ok.ru/videoembed/1" title="Okru"></iframe><iframe></iframe></div>
    <iframe src="https://ads.example/x"></iframe>'''

    def test_collects_absolute_deduplicated_urls(self):
        found = iframe.discover(make_ctx(), self.HTML, PAGE_URL, {"selector": "#player iframe[src]"})
        self.assertEqual([c["url"] for c in found], ["https://vidmoly.me/embed-a.html", BASE + "/embed/b"])
        self.assertEqual(found[0]["label"], "vidmoly.me")   # default label = host

    def test_attr_label_lang_and_host_filter(self):
        found = iframe.discover(make_ctx(), self.HTML, PAGE_URL, {
            "selector": "#player iframe", "attr": "data-src", "label_from": "title", "lang": "TR"})
        self.assertEqual(found, [{"url": "https://ok.ru/videoembed/1", "label": "Okru", "lang": "tr"}])
        found = iframe.discover(make_ctx(), self.HTML, PAGE_URL, {
            "selector": "iframe[src]", "host_regex": r"(?:.+\.)?vidmoly\.me", "label": "VidMoly"})
        self.assertEqual([(c["url"], c["label"]) for c in found], [("https://vidmoly.me/embed-a.html", "VidMoly")])
        # the host filter is a FULL match on the hostname
        self.assertEqual(iframe.discover(make_ctx(), self.HTML, PAGE_URL, {"selector": "iframe[src]", "host_regex": "vidmoly"}), [])

    def test_never_stamps_dispatcher_fields_and_survives_a_bad_regex(self):
        found = iframe.discover(make_ctx(), self.HTML, PAGE_URL, {"selector": "#player iframe[src]"})
        self.assertFalse({"resolver", "resolver_type"} & set(found[0]))
        with self.assertLogs("scraper.resolvers", "WARNING"):
            self.assertEqual(iframe.discover(make_ctx(), self.HTML, PAGE_URL, {"selector": "iframe", "host_regex": "("}), [])
        candidate = {"url": "https://x.example/a", "label": "x"}
        self.assertIs(iframe.resolve_candidate(make_ctx(), candidate, PAGE_URL, {}, None), candidate)


class AnchorHostTests(unittest.TestCase):
    HTML = '''
    <a href="https://vidmoly.me/dl/aaa">İngilizce Altyazılı İndir</a>
    <a href="//vidmolly.net/dl/bbb">Türkçe Altyazılı İndir</a>
    <a href="https://vidmoly.me/dl/aaa">dup</a>
    <a href="https://www.imdb.com/title/tt1">IMDb</a><a href="/local">local</a><a>no href</a>
    <a href="https://vidmoly.me/dl/ccc">Download</a>'''

    def test_links_on_the_provider_host_with_label_and_language_from_text(self):
        found = anchor_host.discover(make_ctx(), self.HTML, PAGE_URL, {
            "host_regex": r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+", "lang_from": "text"})
        self.assertEqual(found, [
            {"url": "https://vidmoly.me/dl/aaa", "label": "İngilizce Altyazılı İndir", "lang": "en", "language": "İngilizce altyazı"},
            {"url": "https://vidmolly.net/dl/bbb", "label": "Türkçe Altyazılı İndir", "lang": "tr", "language": "Türkçe altyazı"},
            {"url": "https://vidmoly.me/dl/ccc", "label": "Download"}])   # text names no language: left out, not guessed

    def test_selector_label_attribute_and_constant_language(self):
        html = '<a class="mirror" data-name="Mirror 1" href="https://ok.ru/video/9">go</a><a class="x" href="https://ok.ru/video/8">x</a>'
        found = anchor_host.discover(make_ctx(), html, PAGE_URL, {
            "selector": "a.mirror", "host_regex": r"ok\.ru", "label_from": "data-name", "lang": "en"})
        self.assertEqual(found, [{"url": "https://ok.ru/video/9", "label": "Mirror 1", "lang": "en"}])

    def test_dub_text_is_recognised(self):
        found = anchor_host.discover(make_ctx(), '<a href="https://ok.ru/video/1">Türkçe Dublaj</a>', PAGE_URL,
                                     {"host_regex": r"ok\.ru", "lang_from": "text"})
        self.assertEqual((found[0]["lang"], found[0]["language"]), ("tr", "Türkçe dublaj"))


class DataAttrTokenTests(unittest.TestCase):
    HTML = '''<div class="alts">
      <div data-link="tok/en+1=" >VidMoly</div><div data-link="">VidMoly</div>
      <div data-link="other">Mac</div><div>no token</div></div>'''
    PARAMS = {"selector": ".alts [data-link]", "attr": "data-link", "url_template": "{base}/api/moly/{token}"}

    def test_template_expansion_and_token_quoting(self):
        found = data_attr_token.discover(make_ctx(), self.HTML, PAGE_URL, self.PARAMS)
        self.assertEqual([c["url"] for c in found], [BASE + "/api/moly/tok%2Fen+1=", BASE + "/api/moly/other"])
        self.assertEqual([c["label"] for c in found], ["VidMoly", "Mac"])

    def test_filter_label_lang_and_base_fallback(self):
        found = data_attr_token.discover(make_ctx(), self.HTML, PAGE_URL, {
            **self.PARAMS, "filter_regex": "^vidmoly$", "label": "VidMolly", "lang": "tr"})
        self.assertEqual(found, [{"url": BASE + "/api/moly/tok%2Fen+1=", "label": "VidMolly", "lang": "tr"}])
        # no base_url configured: the origin of the page is used
        found = data_attr_token.discover(make_ctx(base_url=""), '<i data-t="a">x</i>', "https://other.example/p/1",
                                         {"selector": "i", "attr": "data-t", "url_template": "{base}/go/{token}"})
        self.assertEqual(found[0]["url"], "https://other.example/go/a")

    def test_without_expect_host_regex_the_candidate_is_left_alone(self):
        candidate = {"url": BASE + "/api/moly/a", "label": "VidMoly"}
        self.assertIs(data_attr_token.resolve_candidate(make_ctx(), candidate, PAGE_URL, self.PARAMS, None), candidate)

    def test_hand_off_page_light_cookie_then_browser_fallback(self):
        params = {**self.PARAMS, "expect_host_regex": r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+",
                  "cookie_seed": {"udys": "now_ms"}}
        candidate = {"url": BASE + "/api/moly/a", "label": "VidMolly", "lang": "tr"}
        site = FakeSite()
        trace.begin()
        found = data_attr_token.resolve_candidate(make_ctx(site.fetch), candidate, PAGE_URL, params, None)
        events = trace.take()
        self.assertEqual(found, {**candidate, "url": "https://vidmoly.biz/embed-abc.html"})
        self.assertEqual(len(site.fetch.gets), 1)
        self.assertRegex(site.fetch.gets[0]["headers"]["Cookie"], r"^udys=\d{13}$")
        self.assertEqual(site.fetch.gets[0]["headers"]["Referer"], PAGE_URL)
        self.assertFalse(site.fetch.gets[0]["check_robots"])
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("data_attr_token.handoff", True)])

        # the site wants a cookie only the browser has: no fallback -> None; with fallback -> merged cookie header
        site = FakeSite(need=("udys", "cf"))
        self.assertIsNone(data_attr_token.resolve_candidate(make_ctx(site.fetch), candidate, PAGE_URL, params, lambda: []))
        load = Mock(return_value=[{"name": "cf", "value": "1", "domain": ".site.example", "path": "/"},
                                  {"name": "other", "value": "z", "domain": "elsewhere.example"}])
        trace.begin()
        found = data_attr_token.resolve_candidate(make_ctx(site.fetch), candidate, PAGE_URL,
                                                  {**params, "browser_fallback": True}, load)
        events = trace.take()
        self.assertEqual(found["url"], "https://vidmoly.biz/embed-abc.html")
        load.assert_called_once()
        cookie = site.fetch.gets[-1]["headers"]["Cookie"]
        self.assertIn("cf=1", cookie)
        self.assertIn("udys=", cookie)
        self.assertNotIn("other=", cookie)   # a cookie of another domain is not sent
        self.assertEqual([(e["stage"], e["ok"]) for e in events],
                         [("data_attr_token.handoff", False), ("data_attr_token.handoff.browser", True)])

    def test_candidate_off_the_site_is_not_opened(self):
        params = {**self.PARAMS, "expect_host_regex": r"ok\.ru"}
        candidate = {"url": "https://tracker.example/x", "label": "x"}
        site = FakeSite()
        self.assertIs(data_attr_token.resolve_candidate(make_ctx(site.fetch), candidate, PAGE_URL, params, None), candidate)
        self.assertEqual(site.fetch.gets, [])


class AjaxHandoffTests(unittest.TestCase):
    HTML = '''
    <a data-lango class="item"><i></i>İngilizce Altyazı</a>
    <div class="alts">
      <div data-link="MAC" data-hash="h0" data-querytype="alternate">Mac</div>
      <div data-link="/FIV+x==" data-hash="h1" data-querytype="alternate">Okru</div>
      <div data-link="/FIV+x==" data-hash="h1" data-querytype="alternate">Okru</div>
      <div data-link="lnk2" data-hash="">Okru</div>
      <div data-link="lnk3" data-hash="h3" data-querytype="dub">OK.ru</div>
    </div>'''
    PARAMS = {
        "selector": ".alts [data-link]", "url": "{base}/ajax/service", "filter_regex": r"^ok\.?ru$", "label": "OK.ru",
        "form": {"link": "attr:data-link", "hash": "attr:data-hash", "querytype": "attr:data-querytype",
                 "type": "const:videoGet"},
        "json_key": "api_iframe", "expect_host_regex": r"(?:.+\.)?ok\.ru", "cookie_seed": {"udys": "now_ms"},
        "browser_fallback": True, "lang": "tr"}

    def candidates(self):
        return ajax_handoff.discover(make_ctx(), self.HTML, PAGE_URL, self.PARAMS)

    def test_discover_marks_candidates_with_a_resolved_hand_off(self):
        found = self.candidates()
        self.assertEqual(len(found), 2)   # Mac filtered out, duplicate dropped, element without data-hash skipped
        self.assertEqual(found[0], {
            "url": PAGE_URL, "label": "OK.ru", "lang": "tr",
            "handoff": {"url": BASE + "/ajax/service", "method": "POST",
                        "form": {"link": "/FIV+x==", "hash": "h1", "querytype": "alternate", "type": "videoGet"},
                        "json_key": "api_iframe", "expect_host_regex": r"(?:.+\.)?ok\.ru",
                        "cookie_seed": {"udys": "now_ms"}, "browser_fallback": True}})
        self.assertEqual(found[1]["handoff"]["form"]["link"], "lnk3")
        self.assertFalse({"resolver", "resolver_type"} & set(found[0]))
        self.assertEqual(ajax_handoff.discover(make_ctx(), self.HTML, PAGE_URL, {**self.PARAMS, "form": {"x": "attr:data-nope"}}), [])

    def test_resolve_posts_the_form_and_follows_the_on_site_hand_off_page(self):
        site = FakeSite()
        trace.begin()
        result = ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, None)
        events = trace.take()
        self.assertEqual(result, {"url": "https://ok.ru/videoembed/123", "label": "OK.ru", "lang": "tr"})   # no handoff key
        post = site.fetch.posts[0]
        self.assertEqual(post["url"], BASE + "/ajax/service")
        self.assertEqual(post["data"], {"link": "/FIV+x==", "hash": "h1", "querytype": "alternate", "type": "videoGet"})
        self.assertFalse(post["check_robots"])
        headers = post["headers"]
        self.assertEqual((headers["Origin"], headers["Referer"], headers["X-Requested-With"]), (BASE, PAGE_URL, "XMLHttpRequest"))
        self.assertRegex(headers["Cookie"], r"^udys=\d{13}$")
        self.assertEqual(site.fetch.gets[0]["url"], BASE + "/api/play/_FIV+x==")
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("ajax_handoff.handoff", True), ("ajax_handoff.handoff.page", True)])

    def test_json_url_on_the_expected_host_is_used_directly(self):
        site = FakeSite(ajax_answer={"api_iframe": "https://ok.ru/videoembed/77"})
        result = ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, None)
        self.assertEqual(result["url"], "https://ok.ru/videoembed/77")
        self.assertEqual(site.fetch.gets, [])

    def test_blocked_answer_retries_with_browser_cookies_only_when_enabled(self):
        site = FakeSite(need=("udys", "cf_clearance"))
        load = Mock(return_value=[{"name": "cf_clearance", "value": "tok", "domain": "site.example", "path": "/"}])
        trace.begin()
        result = ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, load)
        events = trace.take()
        self.assertEqual(result["url"], "https://ok.ru/videoembed/123")
        load.assert_called_once()
        self.assertEqual(len(site.fetch.posts), 2)
        self.assertNotIn("cf_clearance", site.fetch.posts[0]["headers"]["Cookie"])
        self.assertIn("cf_clearance=tok", site.fetch.posts[1]["headers"]["Cookie"])
        self.assertIn("udys=", site.fetch.posts[1]["headers"]["Cookie"])   # light cookie kept next to the browser's
        self.assertEqual(events[0]["stage"], "ajax_handoff.handoff")
        self.assertFalse(events[0]["ok"])
        self.assertIn("520", events[0]["error"])
        self.assertTrue(any(e["stage"] == "ajax_handoff.handoff.browser" and e["ok"] for e in events))
        # browser_fallback off in the hand-off: no browser run at all
        candidate = self.candidates()[0]
        candidate["handoff"]["browser_fallback"] = False
        load.reset_mock()
        self.assertIsNone(ajax_handoff.resolve_candidate(make_ctx(FakeSite(need=("udys", "cf_clearance")).fetch),
                                                         candidate, PAGE_URL, self.PARAMS, load))
        load.assert_not_called()

    def test_definite_no_is_never_retried(self):
        load = Mock(return_value=[])
        site = FakeSite(ajax_answer={"success": 0, "error": "video not found"})
        trace.begin()
        self.assertIsNone(ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, load))
        events = trace.take()
        load.assert_not_called()
        self.assertEqual(len(site.fetch.posts), 1)
        self.assertIn("video not found", events[-1]["error"])
        # the key is missing from a JSON answer, an iframe on the wrong host, a 404 hand-off page: all definite
        for answer in ({"success": 1}, {"api_iframe": "https://evil.example/x"}):
            site = FakeSite(ajax_answer=answer)
            self.assertIsNone(ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, load))
        load.assert_not_called()

    def test_non_json_answer_counts_as_a_block_but_an_html_iframe_answer_is_an_answer(self):
        load = Mock(return_value=[])
        site = SimpleNamespace(fetch=FakeFetch(post=lambda url, data, headers: "<html>Just a moment...</html>"))
        self.assertIsNone(ajax_handoff.resolve_candidate(make_ctx(site.fetch), self.candidates()[0], PAGE_URL, self.PARAMS, load))
        load.assert_called_once()   # challenge page: retried with the browser cookies
        html_fetch = FakeFetch(post=lambda url, data, headers: '<p><iframe src="https://ok.ru/videoembed/5"></iframe></p>')
        candidate = ajax_handoff.discover(make_ctx(), self.HTML, PAGE_URL, {**self.PARAMS, "json_key": None})[0]
        self.assertEqual(ajax_handoff.resolve_candidate(make_ctx(html_fetch), candidate, PAGE_URL, self.PARAMS, None)["url"],
                         "https://ok.ru/videoembed/5")

    def test_hand_off_endpoint_must_be_on_the_site(self):
        candidate = self.candidates()[0]
        candidate["handoff"]["url"] = "https://evil.example/ajax"
        site = FakeSite()
        trace.begin()
        self.assertIsNone(ajax_handoff.resolve_candidate(make_ctx(site.fetch), candidate, PAGE_URL, self.PARAMS, None))
        self.assertIn("not on", trace.take()[0]["error"])
        self.assertEqual(site.fetch.posts, [])

    def test_get_method_and_candidates_of_other_types_pass_through(self):
        fetch = FakeFetch(get=lambda url, data, headers: json.dumps({"u": "https://ok.ru/videoembed/6"}))
        params = {"selector": "[data-id]", "method": "get", "url": "{base}/x?y=1", "form": {"id": "attr:data-id"},
                  "json_key": "u", "expect_host_regex": r"ok\.ru"}
        candidate = ajax_handoff.discover(make_ctx(), '<b data-id="7">z</b>', PAGE_URL, params)[0]
        self.assertEqual(candidate["handoff"]["method"], "GET")
        self.assertEqual(ajax_handoff.resolve_candidate(make_ctx(fetch), candidate, PAGE_URL, params, None)["url"],
                         "https://ok.ru/videoembed/6")
        self.assertEqual(fetch.gets[0]["url"], BASE + "/x?y=1&id=7")
        plain = {"url": "https://vidmoly.me/dl/a", "label": "x"}
        self.assertIs(ajax_handoff.resolve_candidate(make_ctx(), plain, PAGE_URL, params, None), plain)


class JsonApiTests(unittest.TestCase):
    RECIPE = {"endpoint": "https://api.example/v1/{video_id}", "referer": "https://site.example/",
              "quality_preference": ["720", "480"], "duration_json_path": "media.duration", "media_type": "mp4"}
    ANSWER = {"media": {"duration": 61000, "level": [
        {"value": "480", "source": "https://cdn/480.mp4"}, {"value": "720", "source": "https://cdn/720.mp4"}]}}

    def test_discover_needs_a_selector(self):
        html = '<div><iframe id="e" src="https://api.example/embed/123"></iframe></div>'
        self.assertEqual(json_api.discover(make_ctx(), html, PAGE_URL, self.RECIPE), [])
        found = json_api.discover(make_ctx(), html, PAGE_URL, {**self.RECIPE, "selector": "#e"})
        self.assertEqual(found, [{"url": "https://api.example/embed/123", "label": "api.example"}])

    def test_resolve_hands_the_recipe_to_resolve_stream_and_returns_the_stream(self):
        seen = {}
        def fake_resolve_stream(cfg, url):
            seen["recipe"], seen["url"] = cfg.stream_resolver, url
            return {"url": "https://cdn/720.mp4", "type": "mp4", "quality": "720", "duration": 61, "streams": []}
        candidate = {"url": "https://api.example/embed/123", "label": "api.example"}
        with patch("app.scraper.resolve.resolve_stream", fake_resolve_stream):
            result = json_api.resolve_candidate(make_ctx(), candidate, PAGE_URL,
                                                {**self.RECIPE, "selector": "#e", "attr": "src", "media_type": "hls"}, None)
        self.assertEqual(result["stream"]["url"], "https://cdn/720.mp4")
        self.assertEqual(result["url"], candidate["url"])
        self.assertEqual(seen["url"], candidate["url"])
        recipe = seen["recipe"]
        self.assertEqual((recipe["endpoint"], recipe["type"], recipe["video_id_regex"]),
                         (self.RECIPE["endpoint"], "hls", r"(\d+)"))
        self.assertNotIn("selector", recipe)
        self.assertNotIn("media_type", recipe)

    def test_end_to_end_through_the_real_resolve_stream(self):
        with patch("app.scraper.fetch.fetch_url", return_value=json.dumps(self.ANSWER)) as request:
            result = json_api.resolve_candidate(make_ctx(), {"url": "https://api.example/embed/123", "label": "x"},
                                                PAGE_URL, self.RECIPE, None)
        self.assertEqual(request.call_args.args[0], "https://api.example/v1/123")
        self.assertEqual(request.call_args.kwargs["headers"], {"Referer": "https://site.example/"})
        self.assertEqual((result["stream"]["url"], result["stream"]["duration"]), ("https://cdn/720.mp4", 61))
        self.assertEqual([s["quality"] for s in result["stream"]["streams"]], ["720", "480"])

    def test_unresolvable_candidate_gives_none(self):
        with patch("app.scraper.fetch.fetch_url", side_effect=FetchError("HTTP 500", 500)), \
                self.assertLogs("scraper.resolve", "WARNING"):
            self.assertIsNone(json_api.resolve_candidate(make_ctx(), {"url": "https://api.example/embed/123"},
                                                         PAGE_URL, self.RECIPE, None))
        self.assertIsNone(json_api.resolve_candidate(make_ctx(), {"url": ""}, PAGE_URL, self.RECIPE, None))


class ValidateCatalogTests(unittest.TestCase):
    def test_valid_lists_pass(self):
        self.assertEqual(resolvers.validate([]), [])
        self.assertEqual(resolvers.validate([
            {"type": "iframe", "selector": "#p iframe"},
            {"type": "anchor_host", "host_regex": r"ok\.ru"},
            {"type": "data_attr_token", "selector": "[data-l]", "attr": "data-l", "url_template": "{base}/x/{token}"},
            {"type": "ajax_handoff", "selector": "[data-l]", "url": "{base}/a", "form": {"l": "attr:data-l", "t": "const:x"},
             "cookie_seed": {"udys": "now_ms"}, "browser_fallback": True},
            {"type": "json_api", "endpoint": "https://a.example/{video_id}", "quality_preference": ["720"]}]), [])
        self.assertEqual(resolvers.validate([{"type": "iframe", "selector": "x", "attr": None}]), [])   # null = absent

    def test_error_messages(self):
        errors = resolvers.validate([
            {"type": "iframe", "selector": "iframe"},
            {"type": "carousel"},
            {"type": "iframe"},
            {"type": "iframe", "selector": "iframe", "attr": 5, "host_regex": "("},
            {"type": "anchor_host", "host_regex": r"ok\.ru", "selector": ":::"},
            {"selector": "x"}, "iframe",
            {"type": "iframe", "selector": "iframe", "selektor": "x"},
            {"type": "data_attr_token", "selector": "a", "attr": "b", "url_template": "{base}/nope"},
            {"type": "ajax_handoff", "selector": "a", "url": "/x", "method": "PUT", "form": {"a": "oops"}},
            {"type": "json_api", "endpoint": "http://x/{video_id}", "video_id_regex": "\\d+", "verify": "yes"}])
        self.assertEqual(errors[0], "resolvers[1]: unknown type 'carousel'")
        joined = "\n".join(errors)
        for expected in ("resolvers[2]: missing required parameter 'selector'",
                         "resolvers[3]: parameter 'attr' must be str (got int)",
                         "resolvers[3]: parameter 'host_regex': invalid regex",
                         "resolvers[4]: parameter 'selector': invalid CSS selector ':::'",
                         "resolvers[5]: missing 'type'",
                         "resolvers[6]: must be a mapping",
                         "resolvers[7]: unknown parameter 'selektor' for type 'iframe'",
                         "resolvers[8]: parameter 'url_template' must contain '{token}'",
                         "resolvers[9]: parameter 'method' must be GET or POST",
                         "resolvers[9]: parameter 'form': field 'a' must be",
                         "resolvers[10]: parameter 'verify' must be bool (got str)"):
            self.assertIn(expected, joined)
        self.assertNotIn("resolvers[0]", joined)

    def test_single_item_and_non_list(self):
        self.assertEqual(resolvers.validate([{"type": "nope"}]), ["resolvers[0]: unknown type 'nope'"])
        self.assertEqual(len(resolvers.validate({"type": "iframe"})), 1)
        self.assertEqual(len(resolvers.validate("iframe")), 1)
        self.assertEqual(len(resolvers.validate(None)), 1)
        self.assertEqual(resolvers.validate([{"type": "json_api", "endpoint": "http://x/{video_id}", "video_id_regex": "abc"}]),
                         ["resolvers[0]: parameter 'video_id_regex' needs one capture group around the id"])

    def test_catalog_shape(self):
        catalog = resolvers.catalog()
        self.assertEqual([c["type"] for c in catalog],
                         ["iframe", "anchor_host", "data_attr_token", "ajax_handoff", "json_api", "player_page", "embedded_json"])
        self.assertEqual(list(resolvers.TYPES), [c["type"] for c in catalog])
        for entry in catalog:
            self.assertEqual(set(entry), {"type", "description", "params"})
            self.assertTrue(entry["description"])
            self.assertTrue(entry["params"])
            for name, spec in entry["params"].items():
                self.assertEqual(set(spec), {"type", "required", "default", "help"}, name)
                self.assertIn(spec["type"], ("str", "int", "bool", "list", "dict"))
                self.assertTrue(spec["help"])
        by_type = {c["type"]: c["params"] for c in catalog}
        self.assertEqual({n for n, s in by_type["iframe"].items() if s["required"]}, {"selector"})
        self.assertEqual(by_type["iframe"]["attr"]["default"], "src")
        self.assertEqual(by_type["anchor_host"]["selector"]["default"], "a[href]")
        self.assertEqual({n for n, s in by_type["anchor_host"].items() if s["required"]}, {"host_regex"})
        self.assertEqual({n for n, s in by_type["data_attr_token"].items() if s["required"]}, {"selector", "attr", "url_template"})
        self.assertEqual(by_type["ajax_handoff"]["method"]["default"], "POST")
        self.assertIs(by_type["ajax_handoff"]["browser_fallback"]["default"], False)
        self.assertEqual(by_type["json_api"]["sources_json_path"]["default"], "media.level")
        json.dumps(catalog)   # the admin endpoint serialises it
        catalog[0]["params"].clear()
        self.assertTrue(resolvers.catalog()[0]["params"])   # a copy: callers cannot corrupt the schema

    def test_params_of_and_make_ctx(self):
        self.assertEqual(resolvers.params_of({"type": "iframe", "selector": "x"}), {"selector": "x"})
        ctx = resolvers.make_ctx(SimpleNamespace(site_id="s", base_url="https://s.example"))
        self.assertEqual((ctx.site_id, ctx.base_url), ("s", "https://s.example"))
        self.assertTrue(callable(ctx.fetch.fetch_url) and callable(ctx.fetch.post_url))


class YabancidiziEquivalenceTests(unittest.TestCase):
    """The yabancidizi module's discovery expressed with the generic types (resolver list lives only here)."""
    PAGE_URL = "https://yabancidizi.news/dizi/star-trek-strange-new-worlds-izle-3/sezon-4/bolum-2"
    PLAYER = '<div id="video-area"><div class="player"><iframe src="/api/drive/abc"></iframe></div></div>'
    HTML = (FIXTURES / "yabancidizi_episode_snw_s4e2.html").read_text() + PLAYER
    RESOLVERS = [
        {"type": "anchor_host", "selector": 'a[href*="/dl/"]', "host_regex": r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+",
         "label_from": "text", "lang_from": "text"},
        {"type": "data_attr_token", "selector": ".alternatives-for-this [data-link]", "attr": "data-link",
         "url_template": "{base}/api/moly/{token}", "filter_regex": "^vidmoly$", "label": "VidMolly", "lang": "tr",
         "expect_host_regex": r"(?:.+\.)?vidmol{1,2}y\.[a-z0-9.-]+", "cookie_seed": {"udys": "now_ms"},
         "browser_fallback": True},
        {"type": "ajax_handoff", "selector": ".alternatives-for-this [data-link]", "url": "{base}/ajax/service",
         "filter_regex": r"^ok\.?ru$", "label": "OK.ru", "lang": "tr", "json_key": "api_iframe",
         "form": {"link": "attr:data-link", "hash": "attr:data-hash", "querytype": "attr:data-querytype",
                  "type": "const:videoGet"},
         "expect_host_regex": r"(?:.+\.)?ok\.ru", "cookie_seed": {"udys": "now_ms"}, "browser_fallback": True},
        {"type": "iframe", "selector": yabancidizi.PLAYER_IFRAMES, "label": "YabancıDizi", "lang": "tr"},
    ]

    def generic(self, html=None):
        ctx = make_ctx(base_url="https://yabancidizi.news")
        found = []
        for item in self.RESOLVERS:
            found += resolvers.TYPES[item["type"]].discover(ctx, html or self.HTML, self.PAGE_URL, resolvers.params_of(item))
        return found

    def test_the_resolver_list_is_valid(self):
        self.assertEqual(resolvers.validate(self.RESOLVERS), [])

    def test_same_candidates_as_the_site_module(self):
        module = yabancidizi.discover(self.HTML, self.PAGE_URL)
        generic = self.generic()
        self.assertEqual(len(module), 5)
        self.assertEqual([c["url"] for c in generic], [c["url"] for c in module])   # same URLs, same order
        self.assertEqual({c["url"] for c in generic}, {c["url"] for c in module})
        self.assertEqual([c.get("lang") for c in generic], [c.get("lang") for c in module])
        # the download links read their language label from the link text exactly like the module does
        self.assertEqual([c.get("language") for c in generic[:2]], [c.get("language") for c in module[:2]])
        self.assertEqual([c["label"] for c in generic], [c["label"] for c in module][:2] + ["VidMolly", "OK.ru", "YabancıDizi"])
        okru, module_okru = generic[3], module[3]
        self.assertEqual(okru["handoff"]["url"], module_okru["handoff"]["url"])
        self.assertEqual({k: okru["handoff"]["form"][k] for k in ("link", "hash", "querytype")},
                         {k: module_okru["handoff"][k] for k in ("link", "hash", "querytype")})
        self.assertEqual(okru["handoff"]["form"]["type"], "videoGet")

    def test_same_resolved_provider_urls_through_the_cookie_gated_site(self):
        site = FakeSite()
        ctx = make_ctx(site.fetch, base_url="https://yabancidizi.news")
        resolved = {}
        for item in self.RESOLVERS[1:3]:
            module = resolvers.TYPES[item["type"]]
            for candidate in module.discover(ctx, self.HTML, self.PAGE_URL, resolvers.params_of(item)):
                result = module.resolve_candidate(ctx, candidate, self.PAGE_URL, resolvers.params_of(item), None)
                resolved[candidate["label"]] = result["url"]
        # (FakeSite's hand-off page is /api/play/..., the real site's is /api/ruplay/...: same shape)
        self.assertEqual(resolved, {"VidMolly": "https://vidmoly.biz/embed-abc.html", "OK.ru": "https://ok.ru/videoembed/123"})
        self.assertTrue(all("udys=" in call["headers"]["Cookie"] for call in site.fetch.posts + site.fetch.gets))


if __name__ == "__main__":
    unittest.main()
