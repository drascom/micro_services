"""Generic live search (``scraper/search_generic.py``): a site WITHOUT a code adapter is searched from its yaml ``search:``
block alone, through the ``site_search`` dispatcher. Network-free: ``fetch.impersonated_get`` is a recorder, or a fake
``curl_cffi.requests.Session`` replays scripted answers under the real transport (redirects, POST session); DNS is
``socket.getaddrinfo`` patched to a public address, a temp config dir stands in for ``configs/``."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import logging
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.scraper import config as scfg, fetch, search_generic as sg, site_search
from app.scraper.site_search import yabancidizi

logging.getLogger("scraper.config").setLevel(logging.ERROR)   # the "block skipped" warnings of the invalid blocks below

BASE = "https://site.example"
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(2, 1, 6, "", ("10.0.0.5", 0))]

HTML = """
<div class="results">
  <div class="item"><a href="/dizi/dark-izle-5"><img data-src="/up/dark.jpg" src="/up/lazy.gif"><h2>  Dark   </h2></a><span class="y">(2017)</span></div>
  <div class="item"><a href="film/dark-city-izle"><img src="https://cdn.example/dark-city.jpg"><h2>Dark City</h2></a><span class="y">1998</span></div>
  <div class="item"><a href="https://site.example/dizi/dark-izle-5/"><img src="/up/dup.jpg"><h2>Dark again</h2></a></div>
  <div class="item"><a href="https://other.example/dizi/foreign"><h2>Foreign</h2></a></div>
  <div class="item"><a href="javascript:void(0)"><h2>Script</h2></a></div>
  <div class="item"><a href="/dizi/no-title"><h2>   </h2></a></div>
  <div class="item"><a href="/dizi/private-poster"><img src="http://10.0.0.5/p.jpg"><h2>Private Poster</h2></a></div>
</div>
"""
HTML_SPEC = {
    "url": "/?s={query}",
    "row_selector": "div.item",
    "fields": {
        "title": {"selector": "h2"},
        "detail_url": {"selector": "a[href]", "attr": "href"},
        "poster_url": {"fallback": [{"selector": "img", "attr": "data-src"}, {"selector": "img", "attr": "src"}]},
        "year": {"selector": "span.y", "regex": r"\d{4}", "cast": "int"},
    },
}
JSON_SPEC = {
    "url": "/search?qr={query}",
    "method": "POST",
    "format": "json",
    "results_path": "data.result",
    "fields": {
        "title": "s_name",
        "detail_url": {"path": "s_link", "template": "{base}/dizi/{value}"},
        "poster_url": {"path": "s_image", "template": "{base}/uploads/series/{value}"},
        "year": "s_year",
    },
}


def make_cfg(spec, base=BASE, site="demo", **extra):
    data = {"base_url": base, "search": copy.deepcopy(spec), **extra}
    return scfg.SiteConfig(site_id=site, data=data, path="")


def page(text, url=BASE + "/", status=200):
    return fetch.ImpersonatedPage(text, url, status)


class Reply:
    def __init__(self, status=200, headers=None, body=b"", raises=None):
        self.status_code, self.headers, self.body, self.raises = status, headers or {}, body, raises


class FakeSession:
    """Scripted ``curl_cffi`` session: one ``Reply`` per request, every request recorded as (method, url, headers, body)."""

    def __init__(self, *replies):
        self.replies, self.calls, self.closed = list(replies), [], False
        self.cookies = SimpleNamespace(set=lambda *a, **k: None)

    def _answer(self, method, url, headers, body, content_callback):
        self.calls.append((method, url, headers, body))
        reply = self.replies.pop(0)
        if reply.body:
            content_callback(reply.body)
        if reply.raises is not None:
            raise reply.raises
        return SimpleNamespace(status_code=reply.status_code, headers=reply.headers)

    def get(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None):
        return self._answer("GET", url, headers, None, content_callback)

    def post(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None, data=None, json=None):
        return self._answer("POST", url, headers, data if data is not None else json, content_callback)

    def close(self):
        self.closed = True


class Case(unittest.TestCase):
    def setUp(self):
        sg.clear_cache()
        for patcher in (patch("socket.getaddrinfo", return_value=PUBLIC),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_html(self, spec=None, query="star trek", limit=20, body=HTML, url=BASE + "/", **kw):
        """``search`` over a recorder standing in for ``fetch.impersonated_get`` (returns ``(items, mock)``)."""
        with patch.object(fetch, "impersonated_get", return_value=page(body, url)) as get:
            items = sg.search(make_cfg(spec or HTML_SPEC), query, limit, **kw)
        return items, get

    def run_session(self, cfg, *replies, query="star trek", limit=20):
        """``search`` over the real transport with a fake session (returns ``(items, session)``; session is the only one)."""
        session = FakeSession(*replies)
        sg.clear_cache()
        with patch("curl_cffi.requests.Session", return_value=session):
            items = sg.search(cfg, query, limit)
        return items, session


class HtmlTests(Case):
    def test_get_search_maps_cards_to_the_adapter_item_shape(self):
        items, get = self.run_html(url="https://site.example/arama/")
        self.assertEqual(get.call_args.args[0], "https://site.example/?s=star+trek")
        self.assertEqual(items, [
            {"title": "Dark", "detail_url": "https://site.example/dizi/dark-izle-5",
             "poster_url": "https://site.example/up/dark.jpg", "year": 2017, "genres": []},
            {"title": "Dark City", "detail_url": "https://site.example/arama/film/dark-city-izle",   # relative to the answer page
             "poster_url": "https://cdn.example/dark-city.jpg", "year": 1998, "genres": []},
            {"title": "Private Poster", "detail_url": "https://site.example/dizi/private-poster",
             "poster_url": None, "year": None, "genres": []},   # a private poster address is dropped, the item stays
        ])   # duplicate detail_url, foreign host, javascript:, empty title: dropped

    def test_transport_limits_headers_and_guard(self):
        _items, get = self.run_html()
        kw = get.call_args.kwargs
        self.assertEqual((kw["max_bytes"], kw["max_redirects"]), (3_000_000, 3))
        self.assertLessEqual(kw["timeout"], 10.0)
        self.assertEqual(kw["headers"]["Referer"], BASE + "/")
        self.assertIn("text/html", kw["headers"]["Accept"])
        self.assertTrue(kw["allow"](BASE + "/x"))
        self.assertFalse(kw["allow"]("https://other.example/x"))
        with patch("socket.getaddrinfo", return_value=PRIVATE):
            self.assertFalse(kw["allow"](BASE + "/x"))

    def test_the_query_is_url_encoded_plus_in_the_query_string_percent_in_the_path(self):
        _items, get = self.run_html(query="Dark & Light/ä")
        self.assertEqual(get.call_args.args[0], "https://site.example/?s=Dark+%26+Light%2F%C3%A4")
        _items, get = self.run_html(spec={**HTML_SPEC, "url": "/ara/{query}/"}, query="star trek")
        self.assertEqual(get.call_args.args[0], "https://site.example/ara/star%20trek/")
        _items, get = self.run_html(spec={**HTML_SPEC, "url": "https://site.example/find?q={query}"})
        self.assertEqual(get.call_args.args[0], "https://site.example/find?q=star+trek")
        _items, get = self.run_html(spec={**HTML_SPEC, "url": "{base}/find?q={query}"})
        self.assertEqual(get.call_args.args[0], "https://site.example/find?q=star+trek")

    def test_user_headers_override_the_defaults(self):
        spec = {**HTML_SPEC, "headers": {"referer": "{base}/ara?x={query}", "X-Requested-With": "XMLHttpRequest"}}
        _items, get = self.run_html(spec=spec)
        headers = get.call_args.kwargs["headers"]
        self.assertEqual(headers["Referer"], "https://site.example/ara?x=star+trek")
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")

    def test_short_or_blank_queries_make_no_request(self):
        for query in ("", "  ", "ab", None, " a  "):
            with self.subTest(query=query):
                items, get = self.run_html(query=query)
                self.assertEqual(items, [])
                get.assert_not_called()

    def test_limit_is_clamped_to_one_to_twenty_and_the_block_limit(self):
        body = "".join(f'<div class="item"><a href="/dizi/s{n}"><h2>S {n}</h2></a></div>' for n in range(30))
        spec = {k: v for k, v in HTML_SPEC.items()}
        spec["fields"] = {"title": {"selector": "h2"}, "detail_url": {"selector": "a", "attr": "href"}}
        for limit, expected in ((0, 1), (-5, 1), (5, 5), (99, 20)):
            with self.subTest(limit=limit):
                sg.clear_cache()
                self.assertEqual(len(self.run_html(spec=spec, limit=limit, body=body)[0]), expected)
        sg.clear_cache()
        self.assertEqual(len(self.run_html(spec={**spec, "limit": 3}, limit=20, body=body)[0]), 3)

    def test_a_row_can_be_the_link_itself(self):
        body = '<ul><li><a href="/film/a-izle" title="Film A">Film A (2020)</a></li><li><a href="/film/b-izle">Film B</a></li></ul>'
        spec = {"url": "/?s={query}", "row_selector": "ul li a",
                "fields": {"title": {"self": True}, "detail_url": {"self": True, "attr": "href"},
                           "year": {"self": True, "regex": r"\((\d{4})\)", "cast": "int"}}}
        items, _ = self.run_html(spec=spec, body=body)
        self.assertEqual([(i["title"], i["detail_url"], i["year"]) for i in items], [
            ("Film A (2020)", "https://site.example/film/a-izle", 2020), ("Film B", "https://site.example/film/b-izle", None)])

    def test_page_with_no_match_is_an_empty_result_not_an_error(self):
        self.assertEqual(self.run_html(body="<html><body>Sonuç bulunamadı</body></html>")[0], [])

    def test_browser_fetch_uses_the_browser_engine(self):
        spec = {**HTML_SPEC, "fetch": "browser"}
        with patch.object(fetch, "browser_page", return_value=HTML) as browser, \
                patch.object(fetch, "impersonated_get") as http:
            items = sg.search(make_cfg(spec), "star trek", 5)
        self.assertEqual(browser.call_args.args[1], "https://site.example/?s=star+trek")
        http.assert_not_called()
        self.assertEqual(items[0]["detail_url"], "https://site.example/dizi/dark-izle-5")

    def test_browser_failure_is_a_short_search_error(self):
        with patch.object(fetch, "browser_page", side_effect=RuntimeError("worker crashed: " + "x" * 500)):
            with self.assertRaises(sg.SearchError) as caught:
                sg.search(make_cfg({**HTML_SPEC, "fetch": "browser"}), "star trek")
        self.assertLess(len(str(caught.exception)), 120)


class JsonTests(Case):
    PAYLOAD = {"success": 1, "data": {"result": [
        {"s_name": "Dark &amp; Co", "s_link": "dark-izle-5", "s_image": "dark.jpg", "s_year": "2017"},
        {"s_name": "Dark City", "s_link": "dark-city", "s_image": "", "s_year": 1998},
        {"s_name": "Dup", "s_link": "dark-izle-5", "s_image": "x.jpg"},
        {"s_name": "", "s_link": "no-title"},
        {"s_name": "No Link"},
        "not-a-dict",
        {"s_name": "Float Year", "s_link": "fy", "s_year": 2019.0},
    ]}}

    def run_json(self, payload, spec=None, **kw):
        spec = {**JSON_SPEC, "method": "GET"} if spec is None else spec
        body = payload if isinstance(payload, str) else json.dumps(payload)
        sg.clear_cache()
        with patch.object(fetch, "impersonated_get", return_value=page(body)) as get:
            return sg.search(make_cfg(spec), "dark", **kw), get

    def test_dotted_paths_templates_and_cleanup(self):
        items, get = self.run_json(self.PAYLOAD)
        self.assertEqual(items, [
            {"title": "Dark & Co", "detail_url": "https://site.example/dizi/dark-izle-5",
             "poster_url": "https://site.example/uploads/series/dark.jpg", "year": 2017, "genres": []},
            {"title": "Dark City", "detail_url": "https://site.example/dizi/dark-city", "poster_url": None, "year": 1998,
             "genres": []},
            {"title": "Float Year", "detail_url": "https://site.example/dizi/fy", "poster_url": None, "year": 2019,
             "genres": []},
        ])
        self.assertIn("json", get.call_args.kwargs["headers"]["Accept"])

    def test_paths_without_template_go_through_urljoin_and_list_indexes_work(self):
        payload = {"hits": [[{"n": "A Film", "u": "/film/a"}], [{"n": "B Film", "u": "https://site.example/film/b"}]]}
        spec = {"url": "/s?q={query}", "format": "json", "results_path": "hits.0",
                "fields": {"title": "n", "detail_url": "u"}}
        items, _ = self.run_json(payload, spec)
        self.assertEqual([i["detail_url"] for i in items], ["https://site.example/film/a"])

    def test_the_document_itself_is_the_list_without_results_path(self):
        spec = {"url": "/s?q={query}", "format": "json", "fields": {"title": "t", "detail_url": "u"}}
        items, _ = self.run_json([{"t": "X Film", "u": "/film/x"}], spec)
        self.assertEqual(items[0]["title"], "X Film")

    def test_missing_path_or_non_list_is_empty_invalid_json_is_an_error(self):
        self.assertEqual(self.run_json({"success": 0})[0], [])
        self.assertEqual(self.run_json({"data": {"result": {"a": 1}}})[0], [])
        with self.assertRaises(sg.SearchError) as caught:
            self.run_json("<html>blocked</html>")
        self.assertIn("JSON", str(caught.exception))


class PostTests(Case):
    def test_form_post_shares_one_cookie_session_with_a_warm_up_page_view(self):
        spec = {"url": "/ara", "method": "POST", "form": {"s": "{query}", "type": "all"}, "row_selector": "div.item",
                "fields": HTML_SPEC["fields"]}
        items, session = self.run_session(make_cfg(spec), Reply(200, {}, b"<html>home</html>"), Reply(200, {}, HTML.encode()))
        self.assertEqual([c[0] for c in session.calls], ["GET", "POST"])
        method, url, headers, body = session.calls[1]
        self.assertEqual((url, body), ("https://site.example/ara", {"s": "star trek", "type": "all"}))
        self.assertEqual(headers["Origin"], BASE)
        self.assertEqual(headers["Referer"], BASE + "/")
        self.assertEqual(session.calls[0][1], BASE + "/")
        self.assertTrue(session.closed)
        self.assertEqual(items[0]["title"], "Dark")

    def test_json_post_substitutes_the_query_in_nested_values(self):
        spec = {**JSON_SPEC, "url": "/api/search", "json": {"q": "{query}", "opts": {"term": ["x {query}", 5, True]}}}
        payload = json.dumps(JsonTests.PAYLOAD).encode()
        items, session = self.run_session(make_cfg(spec), Reply(200, {}, b"home"), Reply(200, {"content-type": "application/json"}, payload))
        self.assertEqual(session.calls[1][3], {"q": "star trek", "opts": {"term": ["x star trek", 5, True]}})
        self.assertEqual(items[0]["title"], "Dark & Co")

    def test_post_with_the_query_only_in_the_url_sends_an_empty_body(self):
        _items, session = self.run_session(make_cfg(JSON_SPEC), Reply(200, {}, b"home"),
                                           Reply(200, {}, json.dumps(JsonTests.PAYLOAD).encode()))
        method, url, _headers, body = session.calls[1]
        self.assertEqual((method, url, body), ("POST", "https://site.example/search?qr=star+trek", None))

    def test_a_failed_warm_up_does_not_stop_the_search(self):
        _items, session = self.run_session(make_cfg(JSON_SPEC), Reply(403), Reply(200, {}, b'{"data":{"result":[]}}'))
        self.assertEqual([c[0] for c in session.calls], ["GET", "POST"])

    def test_303_is_read_with_a_get_307_posts_again(self):
        _items, session = self.run_session(make_cfg(JSON_SPEC), Reply(200, {}, b"home"),
                                           Reply(303, {"location": "/sonuc?q=1"}), Reply(200, {}, b'{"data":{"result":[]}}'))
        self.assertEqual([(c[0], c[1]) for c in session.calls[1:]],
                         [("POST", "https://site.example/search?qr=star+trek"), ("GET", "https://site.example/sonuc?q=1")])
        _items, session = self.run_session(make_cfg(JSON_SPEC), Reply(200, {}, b"home"),
                                           Reply(307, {"location": "/search2?qr=1"}), Reply(200, {}, b'{"data":{"result":[]}}'))
        self.assertEqual([c[0] for c in session.calls], ["GET", "POST", "POST"])

    def test_status_errors_are_short_search_errors(self):
        with self.assertRaises(sg.SearchError) as caught:
            self.run_session(make_cfg(JSON_SPEC), Reply(200, {}, b"home"), Reply(403))
        self.assertEqual(str(caught.exception), "arama isteği başarısız: HTTP 403")

    def test_an_oversized_answer_is_refused(self):
        with patch.object(sg, "MAX_BYTES", 100), self.assertRaises(sg.SearchError) as caught:
            self.run_session(make_cfg(JSON_SPEC), Reply(200, {}, b"home"), Reply(200, {}, b"x" * 500))
        self.assertIn("larger", str(caught.exception))


class SecurityTests(Case):
    def test_a_private_address_is_refused_before_any_request(self):
        with patch("socket.getaddrinfo", return_value=PRIVATE), patch.object(fetch, "impersonated_get") as get:
            with self.assertRaises(sg.SearchError) as caught:
                sg.search(make_cfg(HTML_SPEC), "star trek")
        self.assertIn("izin verilmedi", str(caught.exception))
        get.assert_not_called()
        for base in ("http://127.0.0.1", "http://[::1]", "http://192.168.1.5", "http://169.254.169.254"):
            with self.subTest(base=base), patch.object(fetch, "impersonated_get") as get:
                sg.clear_cache()
                with self.assertRaises(sg.SearchError):
                    sg.search(make_cfg(HTML_SPEC, base=base), "star trek")
                get.assert_not_called()

    def test_a_bad_base_url_is_refused(self):
        for base in ("", "ftp://site.example", "site.example"):
            with self.subTest(base=base), self.assertRaises(sg.SearchError):
                sg.search(make_cfg(HTML_SPEC, base=base), "star trek")

    def test_an_absolute_url_on_another_host_is_refused(self):
        spec = {**HTML_SPEC, "url": "https://evil.example/?s={query}"}
        with patch.object(fetch, "impersonated_get") as get, self.assertRaises(sg.SearchError) as caught:
            sg.search(make_cfg(spec), "star trek")
        self.assertIn("geçersiz", str(caught.exception))
        get.assert_not_called()

    def test_a_redirect_to_another_host_or_a_private_address_is_refused(self):
        for location in ("https://evil.example/x", "http://10.0.0.5/x", "http://127.0.0.1:80/x"):
            with self.subTest(location=location):
                sg.clear_cache()
                session = FakeSession(Reply(302, {"location": location}), Reply(200, {}, HTML.encode()))
                with patch("curl_cffi.requests.Session", return_value=session), self.assertRaises(sg.SearchError):
                    sg.search(make_cfg(HTML_SPEC), "star trek")
                self.assertEqual(len(session.calls), 1)   # the second hop was never requested

    def test_redirects_are_followed_by_hand_up_to_three_hops_each_hop_on_the_same_host(self):
        hops = [Reply(302, {"location": f"/r{n}"}) for n in range(3)] + [Reply(200, {}, HTML.encode())]
        session = FakeSession(*hops)
        with patch("curl_cffi.requests.Session", return_value=session):
            self.assertTrue(sg.search(make_cfg(HTML_SPEC), "star trek"))
        self.assertEqual(len(session.calls), 4)
        session = FakeSession(*[Reply(302, {"location": f"/r{n}"}) for n in range(5)])
        with patch("curl_cffi.requests.Session", return_value=session), self.assertRaises(sg.SearchError) as caught:
            sg.clear_cache()
            sg.search(make_cfg(HTML_SPEC), "star trek")
        self.assertIn("redirect", str(caught.exception))
        self.assertEqual(len(session.calls), 4)

    def test_detail_urls_on_another_host_and_odd_schemes_never_reach_the_result(self):
        items, _ = self.run_html()
        for item in items:
            self.assertTrue(item["detail_url"].startswith(BASE + "/"))
        body = ('<div class="item"><a href="//evil.example/x"><h2>Proto relative</h2></a></div>'
                '<div class="item"><a href="data:text/html,x"><h2>Data</h2></a></div>'
                '<div class="item"><a href="https://user:pw@site.example/x"><h2>Creds</h2></a></div>'
                '<div class="item"><a href="https://site.example:8443/x"><h2>Port</h2></a></div>'
                '<div class="item"><a href="/ok"><h2>Fine</h2></a></div>')
        spec = {**HTML_SPEC, "fields": {"title": {"selector": "h2"}, "detail_url": {"selector": "a", "attr": "href"}}}
        sg.clear_cache()
        items, _ = self.run_html(spec=spec, body=body)
        self.assertEqual([i["title"] for i in items], ["Fine"])

    def test_a_broken_block_is_a_search_error_listing_the_problem(self):
        for spec in ({}, {"url": "/?s=fixed", "row_selector": "div", "fields": {"title": {"selector": "h2"}, "detail_url": {"selector": "a"}}}):
            with self.subTest(spec=spec), self.assertRaises(sg.SearchError):
                sg.search(make_cfg(spec), "star trek")


class CacheTests(Case):
    def test_a_query_is_cached_per_site_spec_and_query(self):
        cfg = make_cfg(HTML_SPEC)
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)) as get:
            first = sg.search(cfg, "Star Trek", 5)
            again = sg.search(cfg, "star   trek", 20)   # same query (case/space-insensitive), another limit
            self.assertEqual(get.call_count, 1)
            self.assertEqual(again[:len(first)], first)
            sg.search(cfg, "dark", 5)
            self.assertEqual(get.call_count, 2)
            sg.search(make_cfg({**HTML_SPEC, "url": "/zoek?s={query}"}), "star trek")   # another spec: not served from cache
            self.assertEqual(get.call_count, 3)
            sg.search(make_cfg(HTML_SPEC, site="other"), "star trek")                    # another site id
            self.assertEqual(get.call_count, 4)

    def test_cache_ttl_zero_disables_and_expiry_refetches(self):
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)) as get:
            cfg = make_cfg({**HTML_SPEC, "cache_ttl": 0})
            sg.search(cfg, "star trek")
            sg.search(cfg, "star trek")
            self.assertEqual(get.call_count, 2)
            cfg = make_cfg(HTML_SPEC)
            with patch.object(sg.time, "time", return_value=1000.0):
                sg.search(cfg, "dark city")
            with patch.object(sg.time, "time", return_value=1000.0 + 299):
                sg.search(cfg, "dark city")
            self.assertEqual(get.call_count, 3)
            with patch.object(sg.time, "time", return_value=1000.0 + 301):
                sg.search(cfg, "dark city")
            self.assertEqual(get.call_count, 4)

    def test_callers_cannot_poison_the_cache_and_errors_are_not_cached(self):
        cfg = make_cfg(HTML_SPEC)
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)):
            first = sg.search(cfg, "star trek")
            first[0]["title"] = "mutated"
            first[0]["genres"].append("x")
            self.assertEqual(sg.search(cfg, "star trek")[0]["title"], "Dark")
            self.assertEqual(sg.search(cfg, "star trek")[0]["genres"], [])
        sg.clear_cache()
        with patch.object(fetch, "impersonated_get", side_effect=fetch.FetchError("HTTP 503", 503)) as get:
            for _ in range(2):
                with self.assertRaises(sg.SearchError):
                    sg.search(cfg, "star trek")
            self.assertEqual(get.call_count, 2)

    def test_the_cache_is_bounded(self):
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)), patch.object(sg, "_CACHE_ENTRIES", 10):
            cfg = make_cfg(HTML_SPEC)
            for n in range(40):
                sg.search(cfg, f"query {n}")
        self.assertLessEqual(len(sg._cache), 31)


class ValidateSpecTests(unittest.TestCase):
    GOOD = {
        "wordpress": {"url": "/?s={query}", "row_selector": "article.post",
                      "fields": {"title": {"selector": "h2 a"}, "detail_url": {"selector": "h2 a", "attr": "href"},
                                 "poster_url": {"selector": "img", "attr": "src"},
                                 "year": {"selector": ".y", "regex": r"\d{4}", "cast": "int"}}},
        "post_form": {"url": "/ara", "method": "POST", "form": {"q": "{query}"}, "row_selector": "li",
                      "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"}},
                      "headers": {"X-Requested-With": "XMLHttpRequest"}},
        "json_api": {**JSON_SPEC, "method": "GET"},
        "json_post_body": {"url": "/api", "method": "POST", "json": {"q": {"text": "{query}"}}, "format": "json",
                           "fields": {"title": "name", "detail_url": "link"}},
        "referer_chrome": {"url": "{base}/search/{query}", "row_selector": "div.r", "fetch": "http", "limit": 10,
                           "cache_ttl": 0, "headers": {"Referer": "{base}/", "User-Agent": "Mozilla/5.0"},
                           "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"}}},
        "browser": {"url": "/?s={query}", "fetch": "browser", "row_selector": "div", "limit": 20, "cache_ttl": 3600,
                    "fields": {"title": {"self": True}, "detail_url": {"self": True, "attr": "href"}}},
        "fallbacks": {"url": "/?s={query}", "row_selector": "div",
                      "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"},
                                 "poster_url": {"fallback": [{"selector": "img", "attr": "data-src"}, {"selector": "img"}]}}},
    }

    def errors(self, mutate=None, base="wordpress", **kw):
        spec = copy.deepcopy(self.GOOD[base])
        if mutate:
            mutate(spec)
        return sg.validate_spec(spec, **kw)

    def test_working_examples_are_valid(self):
        for name, spec in self.GOOD.items():
            with self.subTest(name=name):
                self.assertEqual(sg.validate_spec(spec), [])
                self.assertEqual(sg.validate_spec(spec, BASE), [])

    def has(self, errors, *needles):
        text = " | ".join(errors)
        for needle in needles:
            self.assertIn(needle, text)

    def test_shape_and_unknown_keys(self):
        self.assertEqual(sg.validate_spec("x"), ["search: must be a mapping"])
        self.assertEqual(sg.validate_spec(None), ["search: must be a mapping"])
        self.has(self.errors(lambda s: s.update(selector="x")), "unknown key 'selector'")
        self.has(sg.validate_spec({}), "url: required", "row_selector", "fields: required")

    def test_url_rules(self):
        self.has(self.errors(lambda s: s.update(url="/?s=fixed")), "url: a GET search url must contain {query}")
        self.has(self.errors(lambda s: s.update(url="?s={query}")), "url: must start with")
        self.has(self.errors(lambda s: s.update(url="//evil.example/{query}")), "url: must start with")
        self.has(self.errors(lambda s: s.update(url="ftp://site.example/{query}")), "url: must start with")
        self.has(self.errors(lambda s: s.update(url="/a b?s={query}")), "url: must be one line")
        self.has(self.errors(lambda s: s.update(url="https://{query}.example/")), "host part")
        self.has(sg.validate_spec({**self.GOOD["wordpress"], "url": "https://evil.example/?s={query}"}, BASE),
                 "is not the site's host")
        self.assertEqual(sg.validate_spec({**self.GOOD["wordpress"], "url": "https://site.example/?s={query}"}, BASE), [])
        self.has(self.errors(lambda s: s.update(url=5)), "url: required")

    def test_method_body_and_fetch_rules(self):
        self.has(self.errors(lambda s: s.update(method="PUT")), "method: must be GET or POST")
        self.has(self.errors(lambda s: s.update(form={"s": "{query}"})), "only for method POST")
        self.has(self.errors(lambda s: s.update(json={"s": "{query}"})), "only for method POST")
        self.has(self.errors(lambda s: s.update(json={"q": "{query}"}), base="post_form"), "only one request body")
        self.has(self.errors(lambda s: s.update(form={"q": "fixed"}), base="post_form"), "{query}: a POST search must carry")
        self.has(self.errors(lambda s: s.update(form={}), base="post_form"), "form: must be a non-empty mapping")
        self.has(self.errors(lambda s: s.update(form={"q": ["x"]}), base="post_form"), "form: must be a non-empty mapping")
        self.has(self.errors(lambda s: s.update(fetch="tor")), "fetch: must be http or browser")
        self.has(self.errors(lambda s: s.update(format="xml")), "format: must be html or json")
        self.has(self.errors(lambda s: s.update(fetch="browser", method="POST", form={"s": "{query}"}), base="post_form"),
                 "browser supports only")
        self.has(self.errors(lambda s: s.update(fetch="browser"), base="json_api"), "browser supports only")
        self.assertEqual(self.errors(lambda s: s.update(method="post"), base="post_form"), [])   # case-insensitive

    def test_header_rules(self):
        self.has(self.errors(lambda s: s.update(headers={"Cookie": "a=b"})), "'Cookie' is not allowed")
        self.has(self.errors(lambda s: s.update(headers={"Authorization": "x"})), "is not allowed")
        self.has(self.errors(lambda s: s.update(headers={"Referer": "a\r\nX-Evil: 1"})), "headers.Referer")
        self.has(self.errors(lambda s: s.update(headers={"Referer": ""})), "headers.Referer")
        self.has(self.errors(lambda s: s.update(headers=["Referer"])), "headers: must be a mapping")
        self.assertEqual(self.errors(lambda s: s.update(headers={"ACCEPT": "*/*", "x-requested-with": "XMLHttpRequest"})), [])

    def test_html_field_rules(self):
        self.has(self.errors(lambda s: s.pop("row_selector")), "row_selector: required")
        self.has(self.errors(lambda s: s.update(row_selector="h2[[")), "row_selector: required")
        self.has(self.errors(lambda s: s.update(results_path="a.b")), "results_path: only for format json")
        self.has(self.errors(lambda s: s["fields"].pop("title")), "fields.title: required")
        self.has(self.errors(lambda s: s["fields"].pop("detail_url")), "fields.detail_url: required")
        self.has(self.errors(lambda s: s["fields"].update(genres={"selector": "a"})), "unknown field 'genres'")
        self.has(self.errors(lambda s: s.update(fields={})), "fields: required")
        self.has(self.errors(lambda s: s["fields"].update(title="h2")), "fields.title: must be a mapping")
        self.has(self.errors(lambda s: s["fields"].update(title={"selector": "h2", "regex": "("})), "does not compile")
        self.has(self.errors(lambda s: s["fields"].update(title={"selector": "h2[["})), "fields.title.selector")
        self.has(self.errors(lambda s: s["fields"].update(title={"selector": "h2", "cast": "money"})), "fields.title.cast")
        self.has(self.errors(lambda s: s["fields"].update(title={"selector": "h2", "color": "red"})), "unknown key 'color'")
        self.has(self.errors(lambda s: s["fields"].update(title={"fallback": []})), "fallback: must be a non-empty list")
        self.has(self.errors(lambda s: s["fields"].update(title={"fallback": [{"selector": "x[["}]})), "fallback[0].selector")

    def test_json_field_rules(self):
        self.has(self.errors(lambda s: s.update(row_selector="div"), base="json_api"), "row_selector: only for format html")
        self.has(self.errors(lambda s: s.update(results_path="a..b"), base="json_api"), "results_path: must be a dotted path")
        self.has(self.errors(lambda s: s["fields"].update(title="  "), base="json_api"), "fields.title")
        self.has(self.errors(lambda s: s["fields"].update(title={"selector": "x"}), base="json_api"), "unknown key 'selector'", "path: required")
        self.has(self.errors(lambda s: s["fields"].update(detail_url={"path": "a", "template": "{base}/x"}), base="json_api"),
                 "template: must be a string containing {value}")
        self.has(self.errors(lambda s: s["fields"].update(title=5), base="json_api"), "fields.title")
        self.assertEqual(self.errors(lambda s: s.pop("results_path"), base="json_api"), [])

    def test_limit_and_cache_ttl(self):
        for value in (0, 21, True, "5", 2.5):
            with self.subTest(limit=value):
                self.has(self.errors(lambda s: s.update(limit=value)), "limit: must be a whole number 1..20")
        for value in (-1, 3601, True, "5"):
            with self.subTest(ttl=value):
                self.has(self.errors(lambda s: s.update(cache_ttl=value)), "cache_ttl: must be a whole number")
        self.assertEqual(self.errors(lambda s: s.update(limit=1, cache_ttl=0)), [])

    def test_spec_keys_constant_lists_every_documented_key(self):
        self.assertEqual(sg.SPEC_KEYS, {"url", "method", "form", "json", "headers", "fetch", "format", "row_selector",
                                        "fields", "results_path", "limit", "cache_ttl"})
        for name, spec in self.GOOD.items():
            self.assertLessEqual(set(spec), sg.SPEC_KEYS, name)


class SiteConfigSearchTests(unittest.TestCase):
    def test_valid_block_is_returned_invalid_or_odd_is_empty_and_never_raises(self):
        self.assertEqual(make_cfg(HTML_SPEC).search, HTML_SPEC)
        self.assertEqual(scfg.SiteConfig("demo", {"base_url": BASE}, "").search, {})
        scfg._warned.clear()
        with self.assertLogs("scraper.config", "WARNING") as logs:
            bad = {**HTML_SPEC, "url": "/?s=fixed", "method": "PUT"}
            self.assertEqual(make_cfg(bad, site="badsearch").search, {})
            self.assertEqual(scfg.SiteConfig("badsearch2", {"base_url": BASE, "search": "x"}, "").search, {})
        text = "\n".join(logs.output)
        self.assertIn("method: must be GET or POST", text)
        self.assertIn("block skipped", text)
        self.assertIn("must be a mapping", text)

    def test_an_absolute_url_on_another_host_makes_the_block_invalid(self):
        spec = {**HTML_SPEC, "url": "https://evil.example/?s={query}"}
        self.assertEqual(make_cfg(spec, site="evilsearch").search, {})

    def test_real_site_configs_are_untouched_and_have_no_search_block(self):
        for site in scfg.list_sites():
            self.assertEqual(scfg.load_site(site).search, {}, site)


class DispatcherTests(Case):
    def test_the_registered_adapter_comes_first_and_gets_normalised_arguments(self):
        calls = []

        def adapter(query, limit):
            calls.append((query, limit))
            return [{"title": "x"}]
        cfg = make_cfg(HTML_SPEC, site="modsite")
        with patch.dict(site_search._REGISTRY, {"modsite": adapter}), patch.object(fetch, "impersonated_get") as get:
            self.assertEqual(site_search.search("modsite", "  star   trek ", 99, cfg=cfg), [{"title": "x"}])
            self.assertEqual(site_search.search("modsite", "dark", 0), [{"title": "x"}])
            self.assertEqual(site_search.search("modsite", "ab"), [])
        get.assert_not_called()
        self.assertEqual(calls, [("star trek", 20), ("dark", 1)])

    def test_yabancidizi_adapter_is_the_registered_module_untouched(self):
        self.assertIs(site_search._REGISTRY["yabancidizi"], yabancidizi.search)
        self.assertEqual(site_search.describe("yabancidizi"), {"site": "yabancidizi", "kind": "module"})
        with patch.object(yabancidizi, "search", return_value=[]) as direct, patch.dict(site_search._REGISTRY, {"yabancidizi": direct}):
            site_search.search("yabancidizi", "dark", 7)
        direct.assert_called_once_with("dark", 7)

    def test_without_an_adapter_the_yaml_block_runs_through_the_generic_engine(self):
        cfg = make_cfg(HTML_SPEC, site="yamlsite")
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)) as get:
            items = site_search.search("yamlsite", "star trek", 2, cfg=cfg)
        self.assertEqual([i["title"] for i in items], ["Dark", "Dark City"])
        self.assertEqual(get.call_args.args[0], "https://site.example/?s=star+trek")

    def test_cfg_is_loaded_from_the_site_config_when_not_given(self):
        cfg = make_cfg(HTML_SPEC, site="yamlsite")
        with patch.object(site_search.config, "load_site", return_value=cfg) as load, \
                patch.object(fetch, "impersonated_get", return_value=page(HTML)):
            self.assertTrue(site_search.search("yamlsite", "star trek"))
        load.assert_called_once_with("yamlsite")

    def test_neither_adapter_nor_block_is_a_key_error_even_for_a_short_query(self):
        for cfg in (None, make_cfg({}, site="nosearch"), make_cfg({"url": "/?s=fixed"}, site="badsearch3")):
            with self.subTest(cfg=cfg), patch.object(site_search.config, "load_site", return_value=cfg):
                with self.assertRaises(KeyError):
                    site_search.search("nosearch", "dark", cfg=cfg)
                with self.assertRaises(KeyError):
                    site_search.search("nosearch", "ab")
        with self.assertRaises(KeyError):
            site_search.search("no_such_site_at_all", "dark")

    def test_short_query_on_a_yaml_site_is_empty_without_a_request(self):
        with patch.object(fetch, "impersonated_get") as get:
            self.assertEqual(site_search.search("yamlsite", "ab", cfg=make_cfg(HTML_SPEC, site="yamlsite")), [])
        get.assert_not_called()

    def test_a_failing_generic_search_raises_search_error_for_the_caller_to_isolate(self):
        with patch.object(fetch, "impersonated_get", side_effect=fetch.FetchError("HTTP 503", 503)):
            with self.assertRaises(sg.SearchError):
                site_search.search("yamlsite", "star trek", cfg=make_cfg(HTML_SPEC, site="yamlsite"))


class RegistryTests(Case):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # yabancidizi: its adapter module is searchable only while its config is registered (a deleted site is out of service)
        for name, search in (("alpha", HTML_SPEC), ("beta", None), ("gamma", {"url": "/?s=fixed"}), ("delta", JSON_SPEC), ("yabancidizi", None)):
            data = {"base_url": BASE, "version": 1}
            if search is not None:
                data["search"] = search
            with open(_os.path.join(self.temp.name, name + ".yaml"), "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh)
        patcher = patch.object(scfg, "CONFIG_DIR", self.temp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_search_sites_supports_and_describe(self):
        self.assertEqual(site_search.search_sites(), ["alpha", "delta", "yabancidizi"])
        self.assertTrue(site_search.supports("alpha"))
        self.assertTrue(site_search.supports("yabancidizi"))
        for site in ("beta", "gamma", "nowhere"):
            self.assertFalse(site_search.supports(site), site)
        self.assertEqual(site_search.describe("alpha"), {"site": "alpha", "kind": "yaml"})
        self.assertEqual(site_search.describe("yabancidizi"), {"site": "yabancidizi", "kind": "module"})
        with self.assertRaises(KeyError):
            site_search.describe("beta")

    def test_an_adapter_without_a_registered_config_is_out_of_service(self):
        _os.unlink(_os.path.join(self.temp.name, "yabancidizi.yaml"))
        self.assertEqual(site_search.search_sites(), ["alpha", "delta"])
        self.assertFalse(site_search.supports("yabancidizi"))
        self.assertIn("yabancidizi", site_search._REGISTRY)    # the module stays; only the site registration is gone

    def test_a_registered_adapter_wins_over_a_yaml_block_of_the_same_site(self):
        with patch.dict(site_search._REGISTRY, {"alpha": lambda q, n: []}):
            self.assertEqual(site_search.describe("alpha")["kind"], "module")
            self.assertEqual(site_search.search_sites(), ["alpha", "delta", "yabancidizi"])

    def test_search_loads_the_yaml_of_the_site(self):
        with patch.object(fetch, "impersonated_get", return_value=page(HTML)) as get:
            items = site_search.search("alpha", "star trek", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(get.call_args.args[0], "https://site.example/?s=star+trek")


if __name__ == "__main__":
    unittest.main()
