"""Optional ``method: POST`` + ``data: {...}`` of a yaml collection (a list page that opens only with a form POST): the yaml check, the
transport job (``transport._worker_result``), the Crawlee worker request, ``ingest._fetch_collection`` / ``parses_main_list``, the sandbox
(``/fetch`` ``method`` + ``data``, ``test_config(collections: true)`` page cache). Network-free: Popen, crawlee, ``fetch.page_bundle`` and DNS
are faked; GET behaviour is asserted unchanged."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import asyncio
import json
import types
import unittest
from unittest.mock import patch

import yaml

from app.library import ingest
from app.routers import onboard_sandbox as sb
from app.scraper import collections as col, config as scfg, crawlee_worker, fetch, onboard_store, transport

import test_onboard_sandbox as tos   # helpers only (SandboxCase, public_dns, bundle, list_html, DRAFT_YAML, NEW_HTML, DETAIL_HTML, with_yaml)

BASE = "https://ddizi.example"


def cfg_of(mode="http", base=BASE, **extra):
    return scfg.SiteConfig(site_id="ddizi", data={"base_url": base, "fetch_mode": mode, **extra}, path="")


class RequestVocabularyTests(unittest.TestCase):
    def test_default_is_get_and_nothing_changes(self):
        for spec in ({}, {"path": "/x"}, {"method": "get"}, {"method": "GET", "path": "/x"}, None, "x"):
            self.assertEqual(col.request_of(spec), ("GET", None), spec)
            self.assertEqual(col.request_key(spec), ("GET", ""), spec)
            self.assertEqual(col.check_request(spec), [], spec)

    def test_post_any_case_and_form_encoding(self):
        self.assertEqual(col.request_of({"method": " post "}), ("POST", {}))   # an empty POST
        self.assertEqual(col.request_of({"method": "POST", "data": {}}), ("POST", {}))
        self.assertEqual(col.form_body({"a": 1, "b": "x y", "c": 2.5}), "a=1&b=x+y&c=2.5")
        self.assertEqual(col.form_body({}), "")
        self.assertEqual(col.request_key({"method": "Post", "data": {"q": "ş"}}), ("POST", "q=%C5%9F"))

    def test_the_key_tells_bodies_apart(self):
        self.assertNotEqual(col.request_key({"method": "POST", "data": {"a": 1}}), col.request_key({"method": "POST", "data": {"a": 2}}))
        self.assertNotEqual(col.request_key({"method": "POST"}), col.request_key({}))

    def test_valid_requests(self):
        for spec in ({"method": "POST"}, {"method": "post", "data": {}}, {"method": "POST", "data": {"a": "b", "n": 3, "f": 1.5}}):
            self.assertEqual(col.check_request(spec), [], spec)

    def test_invalid_requests(self):
        many = {f"k{i}": "v" for i in range(col.MAX_POST_FIELDS + 1)}
        huge = {"k": "x" * (col.MAX_POST_BYTES + 1)}
        bad = [{"method": "PUT"}, {"method": 5}, {"method": ""}, {"data": {"a": "b"}}, {"method": "GET", "data": {}},
               {"method": "POST", "data": []}, {"method": "POST", "data": "a=b"}, {"method": "POST", "data": many},
               {"method": "POST", "data": {"a": ["x"]}}, {"method": "POST", "data": {"a": {"b": 1}}}, {"method": "POST", "data": {"a": None}},
               {"method": "POST", "data": {"a": True}}, {"method": "POST", "data": {"": "x"}}, {"method": "POST", "data": {1: "x"}},
               {"method": "POST", "data": huge}]
        for spec in bad:
            self.assertTrue(col.check_request(spec), spec)


class FakeProc:
    """``subprocess.Popen`` stand-in: records the job JSON the parent sends and answers like the worker."""
    jobs: list = []
    reply: dict = {}

    def __init__(self, command, **kw):
        self.returncode = 0

    def communicate(self, job=None, timeout=None):
        FakeProc.jobs.append(json.loads(job))
        reply = dict(FakeProc.reply)
        reply.setdefault("html", "<html>" + "x" * 20 + "</html>")
        reply.setdefault("method", json.loads(job).get("method", "GET"))   # a worker that knows POST echoes the method
        return json.dumps(reply), ""


class TransportTests(unittest.TestCase):
    def setUp(self):
        FakeProc.jobs, FakeProc.reply = [], {}
        for target in (patch.object(transport.subprocess, "Popen", FakeProc), patch.object(transport.time, "sleep")):
            target.start()
            self.addCleanup(target.stop)

    def test_post_job_carries_a_form_body_referer_and_origin(self):
        html = fetch.page(cfg_of(), BASE + "/arama/", method="POST", data={"q": "a b", "n": 2})
        self.assertIn("xxx", html)
        job = FakeProc.jobs[-1]
        self.assertEqual((job["method"], job["form"], job["referer"], job["origin"]), ("POST", "q=a+b&n=2", BASE + "/", BASE))
        self.assertEqual(job["mode"], "http")

    def test_empty_data_is_an_empty_post(self):
        fetch.page(cfg_of(), BASE + "/arama/", method="post", data={})
        job = FakeProc.jobs[-1]
        self.assertEqual((job["method"], job["form"]), ("POST", ""))

    def test_get_job_is_unchanged(self):
        fetch.page(cfg_of(), BASE + "/arama/")
        job = FakeProc.jobs[-1]
        self.assertNotIn("method", job)
        self.assertNotIn("form", job)
        fetch.page_bundle(cfg_of(), BASE + "/", wait_for="div.x")
        self.assertEqual(set(FakeProc.jobs[-1]), {"url", "mode", "capture", "wait_for", "cache_images", "stealth", "obey_robots", "settle_seconds"})

    def test_another_host_is_refused_before_any_process_starts(self):
        for url in ("https://evil.example/arama/", "https://ddizi.example.evil.example/x", "http://127.0.0.1/x"):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch.page(cfg_of(), url, method="POST", data={})
            self.assertIn("own host", str(caught.exception))
        self.assertEqual(FakeProc.jobs, [])

    def test_www_prefix_is_the_same_host(self):
        fetch.page(cfg_of(base="https://www.ddizi.example"), "https://ddizi.example/arama/", method="POST", data={})
        self.assertEqual(len(FakeProc.jobs), 1)

    def test_browser_mode_is_a_clear_error_never_a_get(self):
        with self.assertRaises(fetch.FetchError) as caught:
            fetch.page(cfg_of("browser"), BASE + "/arama/", method="POST", data={})
        self.assertIn("fetch_mode: http", str(caught.exception))
        self.assertEqual(FakeProc.jobs, [])

    def test_oversized_body_is_refused(self):
        with self.assertRaises(fetch.FetchError):
            fetch.page(cfg_of(), BASE + "/arama/", method="POST", data={"k": "x" * (col.MAX_POST_BYTES + 1)})
        self.assertEqual(FakeProc.jobs, [])

    def test_an_old_worker_that_does_not_echo_post_is_an_error_not_a_silent_get(self):
        class OldProc(FakeProc):
            def communicate(self, job=None, timeout=None):
                return json.dumps({"html": "<html>" + "x" * 20 + "</html>", "engine": "crawlee-http"}), ""
        with patch.object(transport.subprocess, "Popen", OldProc):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch.page(cfg_of(), BASE + "/arama/", method="POST", data={})
            self.assertIn("install_crawler", str(caught.exception))
            self.assertIn("xxx", fetch.page(cfg_of(), BASE + "/arama/"))   # a GET never needed the echo


def fake_crawlee():
    """A stand-in ``crawlee`` package that records what the worker passes to ``crawler.run``."""
    seen: dict = {}

    class Request:
        @staticmethod
        def from_url(url, **kw):
            return types.SimpleNamespace(url=url, **kw)

    class Router:
        def default_handler(self, fn):
            seen["handler"] = fn
            return fn

    class HttpCrawler:
        def __init__(self, **kw):
            self.router = Router()

        def failed_request_handler(self, fn):
            return fn

        async def run(self, requests):
            seen["requests"] = list(requests)
            response = types.SimpleNamespace(status_code=200, read=lambda: _answer("<html>ok</html>"))
            await seen["handler"](types.SimpleNamespace(http_response=response))

    async def _answer(text):
        return text.encode()

    crawlee = types.ModuleType("crawlee")
    crawlee.ConcurrencySettings = lambda **kw: None
    crawlee.Request = Request
    crawlers = types.ModuleType("crawlee.crawlers")
    crawlers.HttpCrawler = HttpCrawler
    crawlee.crawlers = crawlers
    return {"crawlee": crawlee, "crawlee.crawlers": crawlers}, seen


class WorkerTests(unittest.TestCase):
    def run_job(self, job):
        modules, seen = fake_crawlee()
        with patch.dict(_sys.modules, modules):
            result = asyncio.run(crawlee_worker.crawl(job))
        return result, seen["requests"]

    def test_post_builds_a_form_request_and_echoes_the_method(self):
        result, requests = self.run_job({"url": BASE + "/arama/", "mode": "http", "method": "POST", "form": "q=%C5%9F", "referer": BASE + "/", "origin": BASE})
        self.assertEqual(result["method"], "POST")
        (request,) = requests
        self.assertEqual((request.url, request.method, request.payload), (BASE + "/arama/", "POST", b"q=%C5%9F"))
        self.assertEqual(request.headers, {"Content-Type": "application/x-www-form-urlencoded", "Referer": BASE + "/", "Origin": BASE})

    def test_empty_post_has_an_empty_payload(self):
        _result, (request,) = self.run_job({"url": BASE + "/arama/", "mode": "http", "method": "POST", "form": ""})
        self.assertEqual((request.method, request.payload), ("POST", b""))

    def test_get_job_passes_the_plain_url_as_before(self):
        result, requests = self.run_job({"url": BASE + "/", "mode": "http"})
        self.assertEqual((result["method"], requests), ("GET", [BASE + "/"]))


class IngestTests(unittest.TestCase):
    def cfg(self):
        return scfg.SiteConfig("ddizi", dict(yaml.safe_load(tos.DRAFT_YAML), base_url=BASE, list_url="/"), "")

    HTML = tos.list_html(6)

    def test_post_collection_passes_method_and_data(self):
        calls = []
        with patch.object(ingest.fetch, "page", side_effect=lambda cfg, url, **kw: calls.append((url, kw)) or self.HTML):
            items = ingest._fetch_collection(self.cfg(), {"path": "/arama/", "method": "POST", "data": {"q": "x"}}, 10)
            ingest._fetch_collection(self.cfg(), {"path": "/arama/", "method": "post"}, 10)
        self.assertEqual(len(items), 6)
        self.assertEqual(calls[0], (BASE + "/arama/", {"wait_for": "div.card.item", "method": "POST", "data": {"q": "x"}}))
        self.assertEqual(calls[1][1]["data"], {})

    def test_get_collection_call_is_unchanged(self):
        calls = []
        with patch.object(ingest.fetch, "page", side_effect=lambda cfg, url, **kw: calls.append((url, kw)) or self.HTML):
            ingest._fetch_collection(self.cfg(), {"path": "/yeni"}, 10)
        self.assertEqual(calls, [(BASE + "/yeni", {"wait_for": "div.card.item"})])

    def test_a_post_collection_is_never_the_main_list_page(self):
        cfg = self.cfg()
        self.assertTrue(ingest.parses_main_list(cfg, {"path": "/"}))
        self.assertFalse(ingest.parses_main_list(cfg, {"path": "/", "method": "POST", "data": {}}))
        with patch.object(ingest.fetch, "page", return_value=self.HTML) as page:
            ingest.load_collection(cfg, {"path": "/", "method": "POST"}, [{"title": "main"}], 10)
        self.assertEqual(page.call_args.kwargs["method"], "POST")   # fetched itself, not filtered from the main items


POST_YAML = """
site_id: demo
collections:
  - {id: trending_demo, title: Yerli, path: /arama/, role: trending, method: POST, data: {}}
"""


class SandboxPostTests(tos.SandboxCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.list_id = self.page(tos.list_html(12))
        self.detail_id = self.page(tos.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def fake(self, cfg, url, *, wait_for="", method="GET", data=None):
        path = url.replace(tos.BASE, "")
        if path.startswith("/film/"):
            return tos.bundle(tos.DETAIL_HTML)
        self.calls.append((path, method, data))
        if path == "/arama/" and method != "POST":
            raise fetch.FetchError("http_502")   # the site answers a GET with 502: only the POST opens it
        return tos.bundle(tos.NEW_HTML)

    def config(self, extra=POST_YAML, base=tos.DRAFT_YAML):
        payload = {"yaml_text": base + extra, "page_id": self.list_id, "detail_page_id": self.detail_id, "collections": True}
        with tos.public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake):
            got = self.post("/test_config", payload)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_test_config_runs_a_post_collection_end_to_end(self):
        out = self.config()
        self.assertEqual(out["errors"], [])
        (entry,) = out["collections"]
        self.assertEqual((entry["status"], entry["count"], entry["valid_count"]), ("ok", 5, 5))
        self.assertEqual(self.calls, [("/arama/", "POST", {})])
        html, meta = onboard_store.load_page(entry["page_id"])
        self.assertEqual((meta["method"], meta["post_data"]), ("POST", ""))

    def test_the_same_url_with_other_bodies_is_not_one_cached_page(self):
        yaml_text = """
site_id: demo
collections:
  - {id: trending_demo, title: A, path: /arama/, role: trending, method: POST, data: {q: a}}
  - {id: latest_movies_demo, title: B, path: /arama/, role: latest_movies, method: POST, data: {q: b}}
  - {id: noteworthy_movies_demo, title: C, path: /arama/, role: noteworthy_movies, method: POST, data: {q: a}}
"""
        out = self.config(yaml_text)
        self.assertEqual([c["status"] for c in out["collections"]], ["ok", "ok", "ok"], out["errors"])
        self.assertEqual(self.calls, [("/arama/", "POST", {"q": "a"}), ("/arama/", "POST", {"q": "b"})])   # same body = one fetch

    def test_a_post_collection_on_the_list_path_does_not_reuse_the_stored_get_page(self):
        yaml_text = """
site_id: demo
collections:
  - {id: trending_demo, title: A, path: /filmler, role: trending, method: POST}
"""
        out = self.config(yaml_text)
        self.assertEqual(out["collections"][0]["status"], "ok", out["errors"])
        self.assertEqual(self.calls, [("/filmler", "POST", {})])

    def test_get_collections_are_fetched_as_before(self):
        yaml_text = """
site_id: demo
collections:
  - {id: noteworthy_movies_demo, title: C, path: /yeni, role: noteworthy_movies}
"""
        out = self.config(yaml_text)
        self.assertEqual(out["collections"][0]["status"], "ok")
        self.assertEqual(self.calls, [("/yeni", "GET", None)])

    def test_a_post_collection_in_browser_mode_is_a_yaml_error_and_nothing_is_fetched(self):
        out = self.config(base=tos.with_yaml(**{"fetch_mode: http": "fetch_mode: browser"}))
        self.assertTrue(any("fetch_mode: http" in e for e in out["errors"]), out["errors"])
        self.assertEqual(out["collections"][0]["status"], "error")
        self.assertEqual(self.calls, [])

    def test_yaml_check_negatives(self):
        data = {"site_id": "demo", "base_url": tos.BASE}
        cases = {
            "method: PUT": ({"method": "PUT"}, "GET or POST"),
            "data without POST": ({"data": {"a": "b"}}, "only with method: POST"),
            "data list": ({"method": "POST", "data": ["a"]}, "must be a mapping"),
            "nested value": ({"method": "POST", "data": {"a": {"b": 1}}}, "data.a"),
            "too many": ({"method": "POST", "data": {f"k{i}": 1 for i in range(21)}}, "at most 20"),
            "too big": ({"method": "POST", "data": {"k": "x" * 5000}}, "bytes"),
            "other host": ({"method": "POST", "path": "https://evil.example/arama/"}, "own host"),
        }
        for name, (extra, needle) in cases.items():
            spec = {"id": "trending_demo", "title": "T", "path": "/arama/", "role": "trending", **extra}
            _pairs, errors, _warnings = sb._check_collections(dict(data, collections=[spec]), "demo")
            self.assertTrue(any(needle in e for e in errors), (name, errors))
        spec = {"id": "trending_demo", "title": "T", "path": "/arama/", "role": "trending", "method": "post", "data": {"a": "b"}}
        _pairs, errors, _warnings = sb._check_collections(dict(data, collections=[spec]), "demo")
        self.assertEqual(errors, [])

    def test_old_collection_keys_still_pass(self):
        spec = {"id": "trending_demo", "title": "T", "path": "/x", "role": "trending", "sort_by": "year", "sort_desc": True}
        _pairs, errors, _warnings = sb._check_collections({"site_id": "demo", "base_url": tos.BASE, "collections": [spec]}, "demo")
        self.assertEqual(errors, [])


class SandboxFetchPostTests(tos.SandboxCase):
    URL = "https://demo.example/arama/"

    def fetch_post(self, body, got_html=None):
        calls = []

        def fake(cfg, url, *, wait_for="", method="GET", data=None):
            calls.append((cfg.fetch_mode, cfg.base_url, url, method, data))
            return tos.bundle(got_html or tos.list_html(4))
        with tos.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake):
            return self.post("/fetch", body), calls

    def test_post_with_data_is_sent_over_http_to_the_draft_site(self):
        got, calls = self.fetch_post({"url": self.URL, "method": "POST", "data": {"q": "x"}})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(calls, [("http", "https://demo.example", self.URL, "POST", {"q": "x"})])
        body = got.json()
        self.assertEqual((body["method"], body["fetch_mode"]), ("POST", "http"))
        self.assertEqual(onboard_store.load_page(body["page_id"])[1]["post_data"], "q=x")

    def test_empty_data_and_missing_data_are_an_empty_post(self):
        for body in ({"url": self.URL, "method": "POST", "data": {}}, {"url": self.URL, "method": "POST"}):
            got, calls = self.fetch_post(body)
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual(calls[0][3:], ("POST", {}))

    def test_get_fetch_is_unchanged(self):
        got, calls = self.fetch_post({"url": self.URL})
        self.assertEqual(got.status_code, 200)
        self.assertNotIn("method", got.json())
        self.assertNotIn("method", onboard_store.load_page(got.json()["page_id"])[1])

    def test_another_host_is_refused(self):
        got, calls = self.fetch_post({"url": "https://other.example/arama/", "method": "POST"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"], calls), (400, "post_host", []))

    def test_browser_chrome_and_referer_are_refused_never_turned_into_a_get(self):
        for extra in ({"mode": "browser"}, {"mode": "chrome"}, {"referer": "https://demo.example/x"}):
            got, calls = self.fetch_post({"url": self.URL, "method": "POST", **extra})
            self.assertEqual((got.status_code, got.json()["detail"]["code"], calls), (400, "post_unsupported", []), extra)

    def test_bad_data_is_refused(self):
        for body in ({"url": self.URL, "method": "POST", "data": {"a": [1]}}, {"url": self.URL, "data": {"a": "b"}},
                     {"url": self.URL, "method": "POST", "data": {f"k{i}": 1 for i in range(21)}}):
            got, calls = self.fetch_post(body)
            self.assertEqual((got.status_code, got.json()["detail"]["code"], calls), (400, "bad_post", []), body)

    def test_a_job_without_a_draft_site_cannot_post(self):
        with tos.public_dns(), patch.object(fetch, "page_bundle", side_effect=AssertionError):
            with self.assertRaises(sb.ApiError) as caught:
                sb._do_fetch(sb.FetchBody(url=self.URL, method="POST"), "", deadline=1e12)
        self.assertEqual(caught.exception.detail["code"], "post_host")


if __name__ == "__main__":
    unittest.main()
