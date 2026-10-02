"""Site-onboarding sandbox: SSRF guard (``netguard``), page/draft store, localhost + token access, and the tool
endpoints (fetch/query/outline/test_config/resolvers/test_resolvers/submit). Network-free: ``fetch.page_bundle`` and DNS
(``socket.getaddrinfo``) are mocked; nothing may write ``scraper/configs``."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import re
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, netguard
from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, fetch, onboard_store
from app.scraper.providers import recipes, vidmolly

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
BASE = "https://demo.example"


def public_dns():
    return patch("socket.getaddrinfo", return_value=PUBLIC)


def card(i, poster=True, title=True):
    return (f'<div class="card item"><a class="poster-link" href="/film/{100 + i}/film-{i}">'
            + (f'<img class="thumb" src="/p/{i}.jpg" alt="Film {i}">' if poster else "")
            + "</a>" + (f'<h2 class="card-title">Film {i}</h2>' if title else "")
            + f'<span class="year">{2000 + i}</span><p class="blurb">Short blurb of film number {i} for the demo site.</p></div>')


def list_html(n=12, **kw):
    cards = "".join(card(i, **kw) for i in range(n))
    nav = "".join(f'<a class="nav-link" href="/kategori/{i}">Kategori {i}</a>' for i in range(6))
    return (f'<html><head><title>Demo Film Sitesi</title></head><body><div class="menu">{nav}</div>'
            f'<div class="grid">{cards}</div><footer><a href="https://other.example/x">partner</a></footer></body></html>')


DETAIL_HTML = ('<html><head><title>Film 1</title></head><body><p class="synopsis">A long synopsis of the film.</p>'
               '<iframe id="player" src="https://vidmoly.me/embed-abc123.html"></iframe></body></html>')

DRAFT_YAML = """
display_name: Demo
playback: video
fetch_mode: http
schema: MovieItem
base_url: https://demo.example
list_url: /filmler
list:
  row_selector: "div.card.item"
  fields:
    title: {selector: "h2.card-title"}
    detail_url: {selector: "a.poster-link", attr: href}
    poster_url: {selector: "img.thumb", attr: src}
    year: {selector: "span.year", regex: '(\\d{4})', cast: int}
detail:
  fields:
    synopsis: {selector: "p.synopsis"}
    player: {selector: "iframe#player", attr: src}
normalize:
  host: demo.example
  key: {from: [detail_url], regex: '/film/(?P<id>\\d+)/', template: '{id}'}
  type: movie
resolvers:
  - {type: iframe, selector: "iframe#player", attr: src}
providers: [vidmolly]
"""


def with_yaml(**changes):
    """DRAFT_YAML with plain string replacements (key = old text, value = new text)."""
    text = DRAFT_YAML
    for old, new in changes.items():
        assert old in text, old
        text = text.replace(old, new)
    return text


def bundle(html, status=200, **extra):
    return {"html": html, "initial_html": html, "status": status, **extra}


GOOD_STREAM = {"provider": "vidmolly", "duration": 100, "streams": [{"url": "https://cdn.example/v/a.m3u8", "type": "hls", "quality": "auto"}]}


def vidmolly_resolves(stream=None):
    """The provider step of the playable stage (``submit`` always runs it): DETAIL_HTML's vidmoly iframe resolves to a stream."""
    return patch.object(vidmolly, "resolve", return_value=stream or GOOD_STREAM)


def any_page(html=None):
    """``fetch.page_bundle`` that answers every playback page with ``html`` (DETAIL_HTML: a vidmoly player iframe)."""
    return patch.object(fetch, "page_bundle", side_effect=lambda cfg, url, **kw: bundle(html or DETAIL_HTML))


def config_snapshot():
    out = {}
    for name in sorted(os.listdir(scfg.CONFIG_DIR)):
        st = os.stat(os.path.join(scfg.CONFIG_DIR, name))
        out[name] = (st.st_size, st.st_mtime_ns)
    return out


class NetguardTest(unittest.TestCase):
    def test_refused(self):
        bad = ["http://127.0.0.1", "http://10.0.0.5", "http://192.168.0.61:8090", "file:///etc/passwd",
               "http://user:p@x.com", "http://[::1]/", "http://169.254.169.254/latest", "http://100.64.1.1/",
               "http://2130706433/", "http://[::ffff:10.0.0.1]/", "ftp://example.com/x", "javascript:alert(1)",
               "https://example.com:8443/", "http://224.0.0.1/", "http://0.0.0.0/", "", "http:///nohost", "http://a b.com/"]
        with public_dns():
            for url in bad:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    netguard.check_url(url)

    def test_host_resolving_to_private_address_is_refused(self):
        for address in ("10.1.2.3", "127.0.0.1", "192.168.1.9", "169.254.1.1", "100.64.0.1", "::1", "fd00::1", "fe80::1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 0))]):
                with self.assertRaises(ValueError):
                    netguard.check_url("https://rebind.example/")

    def test_any_private_answer_among_several_refuses(self):
        mixed = PUBLIC + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=mixed), self.assertRaises(ValueError):
            netguard.check_url("https://mixed.example/")

    def test_unresolvable_host_is_refused(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")), self.assertRaises(ValueError):
            netguard.check_url("https://nowhere.invalid/")

    def test_public_host_is_accepted_and_normalised(self):
        with public_dns():
            self.assertEqual(netguard.check_url("HTTPS://Example.COM/a/b?x=1#frag"), "https://example.com/a/b?x=1")
            self.assertEqual(netguard.check_url("http://example.com"), "http://example.com/")
            self.assertEqual(netguard.check_url("https://example.com:443/x"), "https://example.com:443/x")
            self.assertEqual(netguard.check_url("http://93.184.216.34/x"), "http://93.184.216.34/x")


class StoreTest(unittest.TestCase):
    def test_page_roundtrip_and_cap(self):
        meta = onboard_store.save_page("https://a.example/", "https://a.example/x", "http", 200, "<p>héllo</p>")
        self.assertRegex(meta["page_id"], r"^pg_[0-9a-f]{12}$")
        html, loaded = onboard_store.load_page(meta["page_id"])
        self.assertEqual(html, "<p>héllo</p>")
        self.assertEqual({k: loaded[k] for k in ("url", "final_url", "fetch_mode", "status")},
                         {"url": "https://a.example/", "final_url": "https://a.example/x", "fetch_mode": "http", "status": 200})
        self.assertEqual(loaded["bytes"], len("<p>héllo</p>".encode()))
        self.assertTrue(loaded["fetched_at"])
        with patch.object(config, "ONBOARD_MAX_PAGE_BYTES", 100):
            big = onboard_store.save_page("u", "u", "http", 200, "x" * 1000)
        self.assertEqual(big["bytes"], 100)
        self.assertTrue(big["truncated"])
        self.assertEqual(len(onboard_store.load_page(big["page_id"])[0]), 100)

    def test_bad_ids_never_touch_the_disk(self):
        for bad in ("../../etc/passwd", "pg_zzzzzzzzzzzz", "", None, "pg_123", "od_123456789abc"):
            self.assertIsNone(onboard_store.load_page(bad))
            self.assertIsNone(onboard_store.get_draft(bad))
            self.assertIsNone(onboard_store.update_draft(bad, status="failed"))
            self.assertIsNone(onboard_store.append_event(bad, {"type": "x"}))

    def test_draft_crud(self):
        draft = onboard_store.create_draft("https://a.example/", "demo")
        self.assertRegex(draft["id"], r"^od_[0-9a-f]{12}$")
        self.assertEqual({k: draft[k] for k in ("url", "status", "site_id_suggestion", "yaml_text", "report", "events", "error")},
                         {"url": "https://a.example/", "status": "running", "site_id_suggestion": "demo", "yaml_text": "",
                          "report": None, "events": [], "error": None})
        self.assertTrue(draft["created_at"] and draft["updated_at"])
        onboard_store.append_event(draft["id"], {"type": "tool", "name": "fetch"})
        updated = onboard_store.update_draft(draft["id"], status="needs_input", yaml_text="a: 1", id="od_000000000000")
        self.assertEqual((updated["id"], updated["status"], updated["yaml_text"]), (draft["id"], "needs_input", "a: 1"))
        got = onboard_store.get_draft(draft["id"])
        self.assertEqual([e["type"] for e in got["events"]], ["tool"])
        self.assertIn("ts", got["events"][0])
        with self.assertRaises(ValueError):
            onboard_store.update_draft(draft["id"], status="bogus")
        other = onboard_store.create_draft("https://b.example/")
        onboard_store.update_draft(other["id"], status="ready")
        ids = [d["id"] for d in onboard_store.list_drafts()]
        self.assertIn(draft["id"], ids)
        self.assertEqual([d["id"] for d in onboard_store.list_drafts("ready")], [other["id"]])
        self.assertIsNone(onboard_store.update_draft("od_ffffffffffff", status="failed"))

    def test_prune_old_pages_and_drafts_but_not_saved(self):
        page = onboard_store.save_page("u", "u", "http", 200, "<p/>")
        fresh = onboard_store.save_page("u", "u", "http", 200, "<p/>")
        old_draft = onboard_store.create_draft("u")
        saved = onboard_store.create_draft("u")
        onboard_store.update_draft(saved["id"], status="saved")
        recent = onboard_store.create_draft("u")
        root = onboard_store.root()
        old = time.time() - (config.ONBOARD_RETENTION_DAYS + 1) * 86400
        for rel in (f"pages/{page['page_id']}.html", f"pages/{page['page_id']}.json",
                    f"drafts/{old_draft['id']}.json", f"drafts/{saved['id']}.json"):
            os.utime(os.path.join(root, rel), (old, old))
        removed = onboard_store.prune()
        self.assertEqual(removed, {"pages": 1, "drafts": 1})
        self.assertIsNone(onboard_store.load_page(page["page_id"]))
        self.assertIsNotNone(onboard_store.load_page(fresh["page_id"]))
        self.assertIsNone(onboard_store.get_draft(old_draft["id"]))
        self.assertIsNotNone(onboard_store.get_draft(saved["id"]))
        self.assertIsNotNone(onboard_store.get_draft(recent["id"]))


class SandboxCase(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(onboard_store.root(), ignore_errors=True)   # one process shares the sandbox data dir
        app = FastAPI()
        app.include_router(sb.router)
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        self.draft = onboard_store.create_draft("https://demo.example/", "demo")
        self.token = sb.issue_token(self.draft["id"])
        self.addCleanup(sb.revoke_token, self.token)
        self.headers = {"X-Onboard-Token": self.token}
        self.configs_before = config_snapshot()
        self.addCleanup(self.assert_configs_untouched)

    def assert_configs_untouched(self):
        self.assertEqual(config_snapshot(), self.configs_before, "the sandbox must never write scraper/configs")

    def post(self, path, body, **kw):
        return self.client.post("/api/onboard/sandbox" + path, json=body, headers=self.headers, **kw)

    def page(self, html, url="https://demo.example/filmler"):
        return onboard_store.save_page(url, url, "http", 200, html)["page_id"]


class AccessTest(SandboxCase):
    def test_resolvers_with_token_from_localhost(self):
        got = self.client.get("/api/onboard/sandbox/resolvers", headers=self.headers)
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual({r["type"] for r in body["resolvers"]},
                         {"iframe", "anchor_host", "data_attr_token", "ajax_handoff", "json_api", "player_page"})
        self.assertIn("vidmolly", {p["name"] for p in body["providers"]})

    def test_missing_or_wrong_token_is_403(self):
        for headers in ({}, {"X-Onboard-Token": "nope"}, {"X-Onboard-Token": ""}):
            got = self.client.get("/api/onboard/sandbox/resolvers", headers=headers)
            self.assertEqual(got.status_code, 403, headers)
            self.assertEqual(got.json()["detail"]["code"], "forbidden")
        got = self.client.post("/api/onboard/sandbox/fetch", json={"url": "https://x.example/"})
        self.assertEqual(got.status_code, 403)

    def test_non_localhost_client_is_403_even_with_a_valid_token(self):
        app = FastAPI()
        app.include_router(sb.router)
        for host in ("192.168.0.20", "testclient", "10.0.0.2"):
            client = TestClient(app, client=(host, 40000))
            got = client.get("/api/onboard/sandbox/resolvers", headers=self.headers)
            self.assertEqual(got.status_code, 403, host)
        ipv6 = TestClient(app, client=("::1", 40000))
        self.assertEqual(ipv6.get("/api/onboard/sandbox/resolvers", headers=self.headers).status_code, 200)

    def test_revoked_token_stops_working(self):
        extra = sb.issue_token(self.draft["id"])
        headers = {"X-Onboard-Token": extra}
        self.assertEqual(self.client.get("/api/onboard/sandbox/resolvers", headers=headers).status_code, 200)
        sb.revoke_token(extra)
        self.assertEqual(self.client.get("/api/onboard/sandbox/resolvers", headers=headers).status_code, 403)

    def test_router_is_part_of_the_app(self):
        from app import main
        client = TestClient(main.app, client=("testclient", 1))
        got = client.post("/api/onboard/sandbox/outline", json={"page_id": "pg_000000000000"})
        self.assertEqual(got.status_code, 403)
        self.assertEqual(got.json()["error"]["code"], "forbidden")

    def test_slow_call_is_a_504(self):
        def slow(body, *, deadline):
            time.sleep(1.5)
            return {}
        with patch.object(config, "ONBOARD_TOOL_TIMEOUT", 0.2), patch.object(sb, "_do_outline", slow):
            got = self.post("/outline", {"page_id": "pg_000000000000"})
        self.assertEqual(got.status_code, 504)
        self.assertEqual(got.json()["detail"]["code"], "timeout")


class FetchTest(SandboxCase):
    def pages_on_disk(self):
        folder = os.path.join(onboard_store.root(), "pages")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_auto_switches_to_the_browser_after_a_challenge(self):
        modes = []

        def fake(cfg, url, *, wait_for=""):
            modes.append((cfg.fetch_mode, wait_for))
            if cfg.fetch_mode == "http":
                raise fetch.FetchError(f"crawlee-http http: challenge_blocked for {url}")
            return bundle(list_html())

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake):
            got = self.post("/fetch", {"url": "https://demo.example/filmler", "mode": "auto", "wait_for": "div.card"})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual([m for m, _w in modes], ["http", "browser"])
        self.assertEqual((body["fetch_mode"], body["status"], body["title"]), ("browser", 200, "Demo Film Sitesi"))
        self.assertEqual(body["final_url"], "https://demo.example/filmler")
        self.assertTrue(body["bytes"] > 1000)
        self.assertTrue(any("challenge_blocked" in a for a in body["attempts"]))
        self.assertLessEqual(len(body["html_excerpt"]), sb.EXCERPT_CHARS)
        self.assertIn("card-title", body["html_excerpt"])
        self.assertNotIn("<head", body["html_excerpt"])
        # the page is on disk and readable back
        self.assertEqual(self.pages_on_disk(), sorted([body["page_id"] + ".html", body["page_id"] + ".json"]))
        html, meta = onboard_store.load_page(body["page_id"])
        self.assertIn("Film 3", html)
        self.assertEqual(meta["fetch_mode"], "browser")

    def test_auto_keeps_http_when_the_page_is_fine(self):
        with public_dns(), patch.object(fetch, "page_bundle", return_value=bundle(list_html())) as m:
            got = self.post("/fetch", {"url": "https://demo.example/filmler"})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["fetch_mode"], "http")
        self.assertEqual(m.call_count, 1)
        self.assertNotIn("attempts", got.json())

    def test_auto_tries_the_browser_for_a_thin_page_and_falls_back_to_it_when_that_fails(self):
        shell = "<html><head><title>App</title></head><body><div id=root></div></body></html>"
        calls = []

        def fake(cfg, url, *, wait_for=""):
            calls.append(cfg.fetch_mode)
            if cfg.fetch_mode == "http":
                return bundle(shell)
            return bundle(list_html())

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake):
            got = self.post("/fetch", {"url": "https://demo.example/"})
        self.assertEqual((calls, got.json()["fetch_mode"]), (["http", "browser"], "browser"))

        def fail_browser(cfg, url, *, wait_for=""):
            if cfg.fetch_mode == "browser":
                raise fetch.FetchError("obscura browser: empty_response for " + url)
            return bundle(shell)

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fail_browser):
            got = self.post("/fetch", {"url": "https://demo.example/"})
        self.assertEqual((got.status_code, got.json()["fetch_mode"]), (200, "http"))   # the thin page is all there is

    def test_http_mode_does_not_escalate_and_a_failure_is_a_502(self):
        with public_dns(), patch.object(fetch, "page_bundle",
                                        side_effect=fetch.FetchError("crawlee-http http: challenge_blocked for u")) as m:
            got = self.post("/fetch", {"url": "https://demo.example/", "mode": "http"})
        self.assertEqual((got.status_code, m.call_count), (502, 1))
        self.assertEqual(got.json()["detail"]["code"], "fetch_failed")
        self.assertIn("challenge_blocked", got.json()["detail"]["message"])
        self.assertEqual(self.pages_on_disk(), [])

    def test_robots_refusal_is_not_retried_in_the_browser(self):
        err = fetch.FetchError("crawlee-http http: request_not_processed (check robots.txt/access) for u")
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=err) as m:
            got = self.post("/fetch", {"url": "https://demo.example/", "mode": "auto"})
        self.assertEqual((got.status_code, m.call_count), (502, 1))

    def test_refused_urls_never_reach_the_transport(self):
        with public_dns(), patch.object(fetch, "page_bundle") as m:
            for url in ("http://127.0.0.1:8090/admin", "http://10.0.0.5/", "file:///etc/passwd", "http://u:p@demo.example/"):
                got = self.post("/fetch", {"url": url})
                self.assertEqual(got.status_code, 400, url)
                self.assertEqual(got.json()["detail"]["code"], "url_rejected")
        m.assert_not_called()

    def test_a_redirect_to_a_private_address_is_refused_and_nothing_is_stored(self):
        with public_dns(), patch.object(fetch, "page_bundle",
                                        return_value=bundle(list_html(), final_url="http://10.0.0.5/secret")):
            got = self.post("/fetch", {"url": "https://demo.example/", "mode": "http"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "url_rejected"))
        self.assertEqual(self.pages_on_disk(), [])

    def test_final_url_is_reported(self):
        with public_dns(), patch.object(fetch, "page_bundle",
                                        return_value=bundle(list_html(), final_url="https://www.demo.example/filmler")):
            got = self.post("/fetch", {"url": "https://demo.example/filmler", "mode": "http"})
        self.assertEqual(got.json()["final_url"], "https://www.demo.example/filmler")


class QueryOutlineTest(SandboxCase):
    def test_query_text_attr_and_clipping(self):
        pid = self.page(list_html(12))
        got = self.post("/query", {"page_id": pid, "selector": "div.card.item a.poster-link", "attr": "href", "limit": 3})
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["count"], 12)
        self.assertEqual(len(body["items"]), 3)
        self.assertEqual(body["items"][0]["attr_value"], "/film/100/film-0")
        self.assertIn("poster-link", body["items"][0]["outer_html"])
        titles = self.post("/query", {"page_id": pid, "selector": "h2.card-title"}).json()
        self.assertEqual(titles["count"], 12)
        self.assertEqual(len(titles["items"]), 10)                      # default limit
        self.assertEqual(titles["items"][2]["text"], "Film 2")
        self.assertNotIn("attr_value", titles["items"][0])
        long = self.page("<p>" + "x" * 5000 + "</p>")
        item = self.post("/query", {"page_id": long, "selector": "p"}).json()["items"][0]
        self.assertLessEqual(len(item["text"]), sb.TEXT_CLIP)
        self.assertLessEqual(len(item["outer_html"]), sb.OUTER_CLIP)
        self.assertEqual(self.post("/query", {"page_id": pid, "selector": "div.zzz"}).json(), {"count": 0, "items": []})

    def test_query_errors(self):
        pid = self.page(list_html(2))
        self.assertEqual(self.post("/query", {"page_id": "pg_000000000000", "selector": "p"}).status_code, 404)
        self.assertEqual(self.post("/query", {"page_id": "../../x", "selector": "p"}).status_code, 404)
        bad = self.post("/query", {"page_id": pid, "selector": "a:::b"})
        self.assertEqual((bad.status_code, bad.json()["detail"]["code"]), (400, "bad_selector"))

    def test_outline_finds_the_card_group(self):
        pid = self.page(list_html(12))
        got = self.post("/outline", {"page_id": pid})
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["title"], "Demo Film Sitesi")
        top = body["repeating"][0]
        self.assertEqual((top["selector"], top["count"], top["selector_matches"]), ("div.card.item", 12, 12))
        self.assertEqual((top["with_link"], top["with_image"]), (1.0, 1.0))
        self.assertEqual(top["sample_href"], "/film/100/film-0")
        self.assertEqual(top["sample_image"], "/p/0.jpg")
        self.assertIn("Film 0", top["sample_text"])
        # the 6 nav links repeat too, but they are not card-like, so they rank after the cards
        selectors = [g["selector"] for g in body["repeating"]]
        self.assertIn("a.nav-link", selectors)
        self.assertLess(selectors.index("div.card.item"), selectors.index("a.nav-link"))
        self.assertEqual(body["link_hosts"]["demo.example"], 12 + 6)
        self.assertEqual(body["link_hosts"]["other.example"], 1)
        self.assertEqual(body["counts"]["img"], 12)

    def test_outline_ignores_groups_below_five_and_counts_iframe_hosts(self):
        html = ('<html><head><title>T</title></head><body>' + '<div class="a b">x</div>' * 4
                + '<iframe src="https://vidmoly.me/embed-1.html"></iframe><iframe src="https://vidmoly.me/embed-2.html"></iframe>'
                + '<iframe src="/local"></iframe></body></html>')
        body = self.post("/outline", {"page_id": self.page(html)}).json()
        self.assertEqual(body["repeating"], [])
        self.assertEqual(body["iframe_hosts"], {"vidmoly.me": 2, "demo.example": 1})

    def test_outline_on_the_real_homepage_fixture(self):
        html = (FIXTURES / "yabancidizi_home.html").read_text(encoding="utf-8")
        pid = self.page(html, "https://yabancidizi.news/")
        body = self.post("/outline", {"page_id": pid}).json()
        self.assertTrue(body["repeating"])
        self.assertLessEqual(len(body["repeating"]), sb.LIST_CAP)
        self.assertTrue(all(g["count"] >= 5 for g in body["repeating"]))
        self.assertLess(len(json.dumps(body)), 20000)   # small enough for an LLM tool answer


def home_html():
    """A home page: menu + header + footer links, two titled sections (one with an h2 per card, like posters), a heading
    that has no cards, and a link list in the footer."""
    def poster(i, group):
        return (f'<div class="poster"><a href="/{group}/{i}-slug"><img src="/i/{group}{i}.jpg" alt=""><h2 class="truncate">'
                f'{group} {i}</h2></a></div>')
    trend = "".join(poster(i, "trend") for i in range(6))
    fresh = "".join(poster(i, "fresh") for i in range(8))
    return ('<html><head><title>Anasayfa</title></head><body>'
            '<header><a href="/">Ana sayfa</a><nav><a href="/trends">Trendler</a><a href="/film-izle">Filmler</a>'
            '<a href="/trends">Trendler (again)</a><a href="https://www.demo.example/diziler">Diziler</a>'
            '<a href="https://other.example/x">Partner</a><a href="#top">Top</a><a href="javascript:void(0)">js</a>'
            '<a href="mailto:a@b.c">mail</a></nav></header>'
            '<div class="main-menu"><a href="/yeni"><img src="/m.png" alt="Yeni eklenenler"></a></div>'
            '<div id="body"><a href="/not-in-a-region">x</a>'
            f'<div class="sec"><h2 class="segment-title">Trendler</h2><div class="row" id="trendrow">{trend}</div></div>'
            f'<div class="sec"><div class="segment-title"><a href="/yeni-eklenenler">Yeni Eklenenler</a></div><div class="row" id="freshrow">{fresh}</div></div>'
            '<div class="sec"><h2 class="segment-title">Boş Bölüm</h2><p>no cards</p></div></div>'
            '<footer><ul><li class="flink"><a href="/hakkimizda">Hakkımızda</a></li><li class="flink"><a href="/iletisim">İletişim</a></li>'
            '<li class="flink"><a href="/gizlilik">Gizlilik</a></li></ul></footer></body></html>')


class OutlineHomeTest(SandboxCase):
    def outline(self, html, url="https://demo.example/"):
        got = self.post("/outline", {"page_id": self.page(html, url)})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_nav_links_are_unique_same_host_and_in_a_region(self):
        links = self.outline(home_html())["nav_links"]
        by_href = {l["href"]: l for l in links}
        self.assertEqual(by_href["/trends"], {"text": "Trendler", "href": "/trends", "region": "nav"})   # first text wins
        self.assertEqual(by_href["/film-izle"]["region"], "nav")
        self.assertEqual(by_href["/diziler"]["region"], "nav")             # www. host counts as the same host
        self.assertEqual(by_href["/"]["region"], "header")
        self.assertEqual(by_href["/yeni"], {"text": "Yeni eklenenler", "href": "/yeni", "region": "menu"})   # img alt, menu class
        self.assertEqual(by_href["/hakkimizda"]["region"], "footer")
        self.assertEqual(len(by_href), len(links))                          # unique
        for gone in ("/not-in-a-region", "#top", "/x"):
            self.assertNotIn(gone, by_href)
        self.assertTrue(all(set(l) == {"text", "href", "region"} for l in links))

    def test_nav_links_are_capped(self):
        many = "".join(f'<a href="/k/{i}">K{i}</a>' for i in range(90))
        links = self.outline(f"<html><body><nav>{many}</nav></body></html>")["nav_links"]
        self.assertEqual(len(links), sb.MAX_NAV_LINKS)

    def test_sections_pair_a_heading_with_its_card_group(self):
        body = self.outline(home_html())
        sections = {s["heading"]: s for s in body["sections"]}
        self.assertEqual(list(sections), ["Trendler", "Yeni Eklenenler"])   # the empty heading and the footer links are no sections
        trend, fresh = sections["Trendler"], sections["Yeni Eklenenler"]
        self.assertEqual((trend["selector"], trend["count"]), ("#trendrow div.poster", 6))   # fenced in: cards of one section only
        self.assertEqual((fresh["selector"], fresh["count"]), ("#freshrow div.poster", 8))
        self.assertEqual(trend["sample_link"], "https://demo.example/trend/0-slug")
        self.assertEqual(fresh["heading_link"], "https://demo.example/yeni-eklenenler")
        self.assertNotIn("heading_link", trend)
        # the fenced selectors really match that many cards
        pid = self.page(home_html(), "https://demo.example/")
        for sec in (trend, fresh):
            got = self.post("/query", {"page_id": pid, "selector": sec["selector"]}).json()
            self.assertEqual(got["count"], sec["count"])
        for sec in sections.values():
            self.assertEqual(set(sec) - {"heading_link", "selector_matches"},
                             {"heading", "selector", "count", "sample_link", "link_kind", "sample_title", "sample_hrefs"})

    def test_card_titles_inside_cards_are_not_sections(self):
        # every poster carries an <h2>: those are card titles, not headings of sections
        headings = [s["heading"] for s in self.outline(home_html())["sections"]]
        self.assertFalse(any(h.startswith(("trend ", "fresh ")) for h in headings))

    def test_sections_are_capped_and_the_answer_stays_small(self):
        block = "".join(f'<div><h2 class="segment-title">Bölüm {i}</h2><div id="r{i}">'
                        + "".join(f'<div class="c{i}"><a href="/x/{i}/{j}"><img src="/i.jpg"></a></div>' for j in range(4))
                        + "</div></div>" for i in range(20))
        body = self.outline(f"<html><body>{block}</body></html>")
        self.assertEqual(len(body["sections"]), sb.MAX_SECTIONS)
        self.assertLess(len(json.dumps(body)), 20000)

    def test_a_page_without_headings_has_no_sections_and_old_fields_stay(self):
        body = self.outline(list_html(12))
        self.assertEqual(body["sections"], [])
        self.assertEqual(body["repeating"][0]["selector"], "div.card.item")
        self.assertEqual(set(body), {"title", "counts", "repeating", "iframe_hosts", "link_hosts", "nav_links", "sections",
                                     "episode_links", "blocks"})

    def test_the_real_homepage_fixture_yields_sections(self):
        html = (FIXTURES / "yabancidizi_home.html").read_text(encoding="utf-8")
        body = self.outline(html, "https://yabancidizi.news/")
        self.assertTrue(body["sections"])
        self.assertLessEqual(len(body["sections"]), sb.MAX_SECTIONS)
        self.assertIn("Dikkate Değer Diziler", [s["heading"] for s in body["sections"]])
        self.assertTrue(all(s["count"] >= 3 and s["sample_link"] for s in body["sections"]))
        self.assertLess(len(json.dumps(body)), 20000)


PLAYER_HTML = ('<html><head><title>Oynat</title><script>var cfg = {"file":"https:\\/\\/cdn.example\\/hls\\/a1.m3u8?t=9",'
               '"poster":"\\/p.jpg"}; jwplayer("x").setup({sources:[{file:"https://cdn.example/v.mp4"}]});</script></head>'
               '<body><div id="x"></div>' + "<p>filler</p>" * 40 + '<iframe src="/inner/1"></iframe></body></html>')


class GrepTest(SandboxCase):
    def grep(self, pattern, html=PLAYER_HTML, status=200, **extra):
        pid = self.page(html)
        got = self.post("/grep", {"page_id": pid, "pattern": pattern, **extra})
        self.assertEqual(got.status_code, status, got.text)
        return got.json()

    def test_searches_the_raw_text_scripts_included(self):
        out = self.grep(r"https?:[^\"'\s]+\.mp4")
        self.assertEqual(out["count"], 1)
        match = out["matches"][0]
        self.assertEqual(match["offset"], PLAYER_HTML.index("https://cdn.example/v.mp4"))
        self.assertIn("https://cdn.example/v.mp4", match["text"])
        self.assertIn("sources:[{file:", match["text"])          # the default context (120) around the match
        self.assertNotIn("count_capped", out)
        self.assertNotIn("page_truncated", out)
        # JSON-escaped slashes are kept as they are on the page: the pattern has to allow them
        self.assertEqual(self.grep(r"https:\\?/\\?/cdn\.example\\?/hls[^\"]*\.m3u8")["count"], 1)
        self.assertEqual(self.grep(r"https://cdn\.example/hls")["count"], 0)

    def test_context_limit_count_and_flags(self):
        out = self.grep("filler", context=3, limit=2)
        self.assertEqual(out["count"], 40)
        self.assertEqual(len(out["matches"]), 2)
        self.assertEqual(out["matches"][0]["text"], "<p>" + "filler" + "</p")   # 3 characters either side
        self.assertEqual(self.grep("JWPLAYER")["count"], 0)
        self.assertEqual(self.grep("JWPLAYER", flags="i")["count"], 1)
        self.assertEqual(self.grep("x", context=0, limit=1)["matches"][0]["text"], "x")

    def test_long_match_is_clipped_and_the_count_is_capped(self):
        out = self.grep(r"<p>.*</p>", flags="s", context=10, limit=1)
        self.assertEqual(out["count"], 1)
        self.assertLessEqual(len(out["matches"][0]["text"]), 10 + sb.GREP_MATCH_CLIP + 1)
        self.assertTrue(out["matches"][0]["text"].endswith("…"))
        with patch.object(sb, "GREP_COUNT_CAP", 5):
            capped = self.grep("filler")
        self.assertEqual((capped["count"], capped["count_capped"]), (5, True))

    def test_non_ascii_text_keeps_character_offsets(self):
        html = "<p>Dizi izle: şğüİ</p><script>var u = 'https://cdn.example/Türkçe.m3u8';</script>"
        out = self.grep(r"https://[^']+", html=html)
        self.assertEqual(out["matches"][0]["offset"], html.index("https://"))
        self.assertIn("Türkçe.m3u8", out["matches"][0]["text"])
        self.assertIn("şğüİ", self.grep("izle", html=html)["matches"][0]["text"])

    def test_bad_input_is_a_clear_error(self):
        pid = self.page(PLAYER_HTML)
        got = self.post("/grep", {"page_id": pid, "pattern": "(unclosed"})
        self.assertEqual(got.status_code, 422)
        self.assertEqual(got.json()["detail"]["code"], "bad_pattern")
        self.assertIn("'(unclosed'", got.json()["detail"]["message"])
        self.assertEqual(self.post("/grep", {"page_id": pid, "pattern": "x" * (sb.GREP_MAX_PATTERN + 1)}).status_code, 422)
        self.assertEqual(self.post("/grep", {"page_id": pid, "pattern": ""}).status_code, 422)
        self.assertEqual(self.post("/grep", {"page_id": pid, "pattern": "x", "context": 501}).status_code, 422)
        self.assertEqual(self.post("/grep", {"page_id": pid, "pattern": "x", "limit": 0}).status_code, 422)
        got = self.post("/grep", {"page_id": pid, "pattern": "x", "flags": "ix?"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (422, "bad_flags"))
        got = self.post("/grep", {"page_id": "pg_000000000000", "pattern": "x"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (404, "not_found"))

    def test_catastrophic_pattern_is_stopped_not_hung(self):
        html = "<p>" + "a" * 40 + "b</p>"
        started = time.monotonic()
        with patch.object(sb, "GREP_TIMEOUT", 1.0):
            out = self.grep(r"(a+)+$", html=html, status=422)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(out["detail"]["code"], "pattern_too_slow")
        self.assertIn("simplify", out["detail"]["message"])
        self.assertEqual(self.grep("filler")["count"], 40)    # the server still answers afterwards

    def test_big_pages_are_searched_up_to_the_cap_and_say_so(self):
        html = "<p>head</p>" + "x" * 200 + "<p>tail</p>"
        with patch.object(sb, "GREP_MAX_BYTES", 100):
            out = self.grep("tail", html=html)
        self.assertEqual(out["count"], 0)
        self.assertTrue(out["page_truncated"])
        with patch.object(config, "ONBOARD_MAX_PAGE_BYTES", 100_000):
            pid = onboard_store.save_page("https://demo.example/p", "https://demo.example/p", "http", 200, "y" * 100_050)["page_id"]
        got = self.post("/grep", {"page_id": pid, "pattern": "y", "limit": 1}).json()
        self.assertTrue(got["page_truncated"])

    def test_needs_the_token(self):
        pid = self.page(PLAYER_HTML)
        got = self.client.post("/api/onboard/sandbox/grep", json={"page_id": pid, "pattern": "x"})
        self.assertEqual(got.status_code, 403)


class TestConfigTest(SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")

    def run_config(self, yaml_text=DRAFT_YAML, **extra):
        body = {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id, **extra}
        got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_valid_draft_passes(self):
        out = self.run_config()
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["valid"])
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual((out["list"]["count"], out["list"]["valid_count"]), (12, 12))
        self.assertEqual({k: out["list"]["field_fill"][k] for k in ("title", "detail_url", "poster_url")},
                         {"title": 1.0, "detail_url": 1.0, "poster_url": 1.0})
        self.assertEqual(len(out["list"]["samples"]), 5)
        self.assertEqual(out["list"]["samples"][0]["title"], "Film 0")
        self.assertEqual((out["normalize"]["total"], out["normalize"]["ok"], out["normalize"]["duplicate_keys"]), (12, 12, []))
        self.assertEqual(out["normalize"]["samples"][0]["source_key"], "100")
        self.assertLessEqual(len(out["normalize"]["samples"]), 5)
        self.assertEqual(out["detail"]["fields"]["synopsis"], "A long synopsis of the film.")
        self.assertEqual(out["detail"]["fill"], {"synopsis": 1.0, "player": 1.0})
        self.assertTrue(all(c["ok"] for c in out["criteria"].values()))
        self.assertEqual(set(out["criteria"]), {"valid_count", "title_fill", "detail_url_fill", "poster_url_fill",
                                                "normalize_ok_ratio", "duplicate_key_ratio", "config_errors"})

    def test_ingest_block_says_what_a_scan_takes_from_the_list(self):
        out = self.run_config()
        self.assertEqual(out["ingest"], {"item_limit": 30, "list_items_on_page": 12, "would_ingest": 12})
        big = self.page('<html><body><div class="grid">' + "".join(card(i) for i in range(45)) + "</div></body></html>")
        out = self.run_config(page_id=big)
        self.assertEqual(out["ingest"], {"item_limit": 30, "list_items_on_page": 45, "would_ingest": 30})   # default cap, no pagination
        out = self.run_config(with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: 5"}))
        self.assertEqual(out["ingest"], {"item_limit": 5, "list_items_on_page": 12, "would_ingest": 5})
        self.assertEqual(out["errors"], [])
        out = self.run_config(with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: 200"}), page_id=big)
        self.assertEqual(out["ingest"], {"item_limit": 200, "list_items_on_page": 45, "would_ingest": 45})
        self.assertNotIn("ingest", out["criteria"])   # informational: not an acceptance criterion

    def test_item_limit_must_be_a_whole_number_between_1_and_200(self):
        for bad in ("0", "201", "-3", "abc", "true", "2.5", "[5]"):
            out = self.run_config(with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: %s" % bad}))
            self.assertTrue(any(e.startswith("item_limit:") for e in out["errors"]), (bad, out["errors"]))
            self.assertFalse(out["passed"], bad)
        out = self.run_config(with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: abc"}))
        self.assertEqual(out["ingest"]["item_limit"], 30)   # an unusable value reports the default
        out = self.run_config(with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: 1"}))
        self.assertEqual((out["errors"], out["ingest"]["would_ingest"]), ([], 1))

    def test_ingest_block_is_there_even_when_the_list_does_not_parse(self):
        out = self.run_config(with_yaml(**{"div.card.item": "div.card.gone"}))
        self.assertEqual(out["ingest"], {"item_limit": 30, "list_items_on_page": 0, "would_ingest": 0})

    def test_acceptance_constants(self):
        self.assertEqual((sb.MIN_VALID_COUNT, sb.MIN_FILL, sb.MIN_NORMALIZE_OK_RATIO, sb.MAX_DUPLICATE_KEY_RATIO),
                         (8, {"title": 0.95, "detail_url": 0.95, "poster_url": 0.8}, 0.9, 0.1))

    def test_broken_row_selector_fails_with_a_clear_error(self):
        out = self.run_config(with_yaml(**{"div.card.item": "div.card.gone"}))
        self.assertFalse(out["passed"])
        self.assertFalse(out["valid"])
        self.assertTrue(any("matched 0 elements" in e for e in out["errors"]), out["errors"])
        self.assertEqual(out["list"]["count"], 0)
        self.assertFalse(out["criteria"]["valid_count"]["ok"])

    def test_broken_field_selector_shows_in_field_fill(self):
        out = self.run_config(with_yaml(**{"h2.card-title": "h2.gone"}))
        self.assertFalse(out["passed"])
        self.assertEqual(out["list"]["field_fill"]["title"], 0.0)
        self.assertEqual(out["list"]["valid_count"], 0)
        self.assertTrue(any("list.fields.title" in e and "found nothing" in e for e in out["errors"]), out["errors"])
        self.assertFalse(out["criteria"]["title_fill"]["ok"])

    def test_partial_poster_fill_fails_the_criterion_with_a_warning(self):
        html = "".join(card(i, poster=i % 2 == 0) for i in range(12))
        out = self.run_config(page_id=self.page(f'<html><body><div class="grid">{html}</div></body></html>'))
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["list"]["field_fill"]["poster_url"], 0.5)
        self.assertFalse(out["passed"])
        self.assertFalse(out["criteria"]["poster_url_fill"]["ok"])
        self.assertTrue(any("poster_url" in w and "50%" in w for w in out["warnings"]), out["warnings"])

    def test_too_few_items_fails(self):
        out = self.run_config(page_id=self.page(list_html(5)))
        self.assertEqual(out["errors"], [])
        self.assertFalse(out["passed"])
        self.assertEqual(out["criteria"]["valid_count"], {"value": 5, "min": 8, "ok": False})

    def test_unknown_resolver_type_is_an_error(self):
        out = self.run_config(with_yaml(**{"{type: iframe, selector:": "{type: telepathy, selector:"}))
        self.assertFalse(out["valid"])
        self.assertTrue(any("unknown type 'telepathy'" in e for e in out["errors"]), out["errors"])
        self.assertFalse(out["passed"])
        self.assertEqual(out["list"]["valid_count"], 12)   # the list part still ran

    def test_unknown_provider_and_bad_normalize_are_errors(self):
        out = self.run_config(with_yaml(**{"providers: [vidmolly]": "providers: [vidmolly, nosuch]"}))
        self.assertTrue(any("unknown provider 'nosuch'" in e for e in out["errors"]))
        out = self.run_config(with_yaml(**{"regex: '/film/(?P<id>\\d+)/', template: '{id}'": "regex: '/film/(?P<id>\\d+)/', template: '{nope}'"}))
        self.assertTrue(any("nope" in e for e in out["errors"]), out["errors"])
        self.assertIsNone(out["normalize"])
        self.assertFalse(out["passed"])

    def test_missing_normalize_block_and_bad_yaml(self):
        out = self.run_config(DRAFT_YAML.split("normalize:")[0] + "resolvers: []\n")
        self.assertTrue(any(e.startswith("normalize: missing") for e in out["errors"]), out["errors"])
        self.assertFalse(out["passed"])
        for text in ("a: [unclosed", "- just\n- a list\n", "base: &x 1\nother: *x\n"):
            out = self.run_config(text)
            self.assertFalse(out["valid"], text)
            self.assertTrue(out["errors"][0].startswith("yaml:"), out["errors"])
            self.assertFalse(out["passed"])

    def test_bad_selector_and_regex_in_the_yaml_are_reported_with_their_path(self):
        out = self.run_config(with_yaml(**{'selector: "span.year"': 'selector: "span:::year"'}))
        self.assertTrue(any(e.startswith("list.fields.year") and "invalid CSS selector" in e for e in out["errors"]), out["errors"])
        out = self.run_config(with_yaml(**{"regex: '(\\d{4})'": "regex: '(\\d{4'"}))
        self.assertTrue(any(e.startswith("list.fields.year.regex") for e in out["errors"]), out["errors"])
        self.assertIsNone(out["list"])   # a broken field spec stops before parsing

    def test_duplicate_keys_fail_the_criterion(self):
        same = "".join(f'<div class="card item"><a class="poster-link" href="/film/{100 + i // 3}/f"><img class="thumb" src="/p.jpg"></a>'
                       f'<h2 class="card-title">Film {i}</h2></div>' for i in range(12))
        out = self.run_config(page_id=self.page(f"<html><body>{same}</body></html>"))
        self.assertEqual(out["normalize"]["ok"], 12)
        self.assertEqual(len(out["normalize"]["duplicate_keys"]), 4)
        self.assertFalse(out["criteria"]["duplicate_key_ratio"]["ok"])
        self.assertFalse(out["passed"])

    def test_missing_page_is_reported_in_errors(self):
        out = self.run_config(page_id="pg_000000000000")
        self.assertTrue(any("page not available" in e for e in out["errors"]), out["errors"])
        self.assertFalse(out["passed"])

    def test_list_and_detail_pages_are_fetched_when_no_page_ids_are_given(self):
        urls = []

        def fake(cfg, url, *, wait_for=""):
            urls.append((url, cfg.fetch_mode, wait_for))
            return bundle(list_html(12) if url.endswith("/filmler") else DETAIL_HTML)

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake):
            got = self.post("/test_config", {"yaml_text": DRAFT_YAML})
        out = got.json()
        self.assertEqual(urls, [("https://demo.example/filmler", "http", "div.card.item"),
                                ("https://demo.example/film/100/film-0", "http", "")])
        self.assertTrue(out["passed"], out)
        self.assertTrue(out["list_page_id"].startswith("pg_"))
        self.assertTrue(out["detail"]["page_id"].startswith("pg_"))

    def test_private_base_url_is_refused_not_fetched(self):
        with public_dns(), patch.object(fetch, "page_bundle") as m:
            got = self.post("/test_config", {"yaml_text": with_yaml(**{"https://demo.example": "http://10.0.0.5"})})
        m.assert_not_called()
        out = got.json()
        self.assertFalse(out["valid"])
        self.assertTrue(any("non-public" in e for e in out["errors"]), out["errors"])

    def test_resolver_endpoints_in_the_yaml_are_checked(self):
        text = with_yaml(**{"  - {type: iframe, selector: \"iframe#player\", attr: src}":
                            "  - {type: json_api, endpoint: 'http://127.0.0.1:8090/api/{video_id}'}"})
        out = self.run_config(text)
        self.assertTrue(any("resolvers[0].endpoint" in e for e in out["errors"]), out["errors"])


class ResolversTest(SandboxCase):
    def run_resolvers(self, yaml_text=DRAFT_YAML, detail=DETAIL_HTML, **extra):
        with public_dns(), patch.object(fetch, "page_bundle", return_value=bundle(detail)) as m:
            got = self.post("/test_resolvers", {"yaml_text": yaml_text, "detail_url": "https://demo.example/film/100/film-0", **extra})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json(), m

    def test_candidates_found_and_resolved(self):
        stream = {"provider": "vidmolly", "duration": 100, "streams": [{"url": "https://cdn.example/v.m3u8", "type": "hls"}]}
        with patch.object(vidmolly, "resolve", return_value=stream) as resolve:
            out, fetched = self.run_resolvers()
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["candidates"], [{"url": "https://vidmoly.me/embed-abc123.html", "label": "vidmoly.me",
                                              "resolver_type": "iframe"}])
        self.assertEqual(len(out["resolved"]), 1)
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["provider"], got["streams_count"], got["error"]), (True, "vidmolly", 1, ""))
        self.assertEqual(got["candidate"], "https://vidmoly.me/embed-abc123.html")
        self.assertEqual(resolve.call_count, 1)
        self.assertTrue(out["page_id"].startswith("pg_"))   # the detail page is kept for query/outline
        self.assertIsNotNone(onboard_store.load_page(out["page_id"]))
        self.assertEqual(fetched.call_args_list[0].args[1], "https://demo.example/film/100/film-0")   # the detail page first

    def test_provider_failure_is_reported_per_candidate(self):
        with patch.object(vidmolly, "resolve", return_value=None):
            out, _ = self.run_resolvers()
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["streams_count"], got["provider"]), (False, 0, ""))
        self.assertIn("no stream", got["error"])

    def test_no_candidates_and_no_resolvers(self):
        out, _ = self.run_resolvers(detail="<html><body><p>nothing</p></body></html>")
        self.assertEqual((out["candidates"], out["resolved"]), ([], []))
        self.assertTrue(any("no candidates" in e for e in out["errors"]))
        out, m = self.run_resolvers(DRAFT_YAML.split("resolvers:")[0])
        self.assertTrue(any("no resolvers list" in e for e in out["errors"]), out["errors"])
        m.assert_not_called()

    def test_unknown_resolver_type_and_bad_provider(self):
        out, m = self.run_resolvers(with_yaml(**{"type: iframe": "type: telepathy"}))
        self.assertTrue(any("unknown type 'telepathy'" in e for e in out["errors"]), out["errors"])
        m.assert_not_called()
        out, _ = self.run_resolvers(with_yaml(**{"providers: [vidmolly]": "providers: [nosuch]"}))
        self.assertTrue(any("unknown provider" in e for e in out["errors"]))

    def test_internal_candidate_url_is_not_resolved(self):
        evil = '<iframe id="player" src="http://10.0.0.5/embed/1"></iframe>'
        with patch.object(vidmolly, "resolve") as resolve, patch("app.scraper.providers.registry.fetch.fetch_url") as fetch_url:
            out, _ = self.run_resolvers(detail=evil)
        self.assertEqual(len(out["candidates"]), 1)
        got = out["resolved"][0]
        self.assertFalse(got["ok"])
        self.assertIn("non-public", got["error"])
        resolve.assert_not_called()
        fetch_url.assert_not_called()

    def test_stored_page_is_used_without_fetching(self):
        pid = self.page_id = onboard_store.save_page("https://demo.example/film/1", "https://demo.example/film/1", "http", 200, DETAIL_HTML)["page_id"]
        stream = {"provider": "vidmolly", "streams": [{"url": "https://cdn.example/v.mp4", "type": "mp4"}]}
        with public_dns(), patch.object(fetch, "page_bundle") as m, patch.object(vidmolly, "resolve", return_value=stream):
            got = self.post("/test_resolvers", {"yaml_text": DRAFT_YAML, "detail_url": "https://demo.example/film/1",
                                                "page_id": pid, "detail_urls": []})   # [] = this page only (no list sampling)
        m.assert_not_called()
        self.assertTrue(got.json()["resolved"][0]["ok"])

    def test_json_api_endpoint_in_yaml_is_checked_before_anything_runs(self):
        text = with_yaml(**{"  - {type: iframe, selector: \"iframe#player\", attr: src}":
                            "  - {type: json_api, endpoint: 'http://192.168.0.61:8090/x/{video_id}', selector: 'iframe#player'}"})
        out, m = self.run_resolvers(text)
        self.assertTrue(any("resolvers[0].endpoint" in e for e in out["errors"]), out["errors"])
        m.assert_not_called()


class MultiPageResolversTest(SandboxCase):
    """``test_resolvers`` on several pages (``detail_url`` + ``detail_urls``, or items sampled from the draft yaml's list page):
    one ``pages[]`` entry per page, overall status resolved (all) / partial (some) / no_stream / no_candidates / error, the
    "varied hosts/types" warning, ``skipped`` pages, and the old single-page fields staying the first page's."""
    LIST = "https://demo.example/filmler"
    CODES = {0: "aaa", 1: "bbb", 2: "ccc"}
    STREAMS = {   # embed code -> what the (patched) vidmolly provider answers: one site, three players
        "aaa": {"provider": "vidmolly", "duration": 0, "streams": [{"url": "https://downloader.disk.yandex.ru/d/a.mp4", "type": "mp4", "quality": "auto"}]},
        "bbb": {"provider": "vidmolly", "duration": 0, "streams": [{"url": "https://video.cdnhost.example/pl/b.m3u8", "type": "hls", "quality": "auto"}]},
        "ccc": {"provider": "vidmolly", "duration": 0, "streams": [{"url": "https://redirector.googlevideo.com/v/c.mp4", "type": "mp4", "quality": "360p"}]},
        "none": None,
    }

    def url(self, i):
        return f"https://demo.example/film/{100 + i}/film-{i}"

    def site(self, codes=None, lists=True):
        """URL -> HTML: the list page and the detail pages film-0 .. film-5 (each with a vidmoly iframe, ``None`` = no player)."""
        codes = {**self.CODES, **(codes or {})}
        pages = {self.LIST: list_html(6)} if lists else {}
        for i in range(6):
            code = codes.get(i, "aaa")
            pages[self.url(i)] = ('<html><body><p>x</p></body></html>' if code is None else
                                  f'<html><body><iframe id="player" src="https://vidmoly.me/embed-{code}.html"></iframe></body></html>')
        return pages

    def go(self, pages=None, streams=None, yaml_text=DRAFT_YAML, **extra):
        pages, self.fetched = pages if pages is not None else self.site(), []
        table = streams or self.STREAMS

        def fake_page(cfg, url, *, wait_for=""):
            self.fetched.append(url)
            if url not in pages:
                raise fetch.FetchError("HTTP 404 for " + url, 404)
            return bundle(pages[url])

        def provider(url, **kw):
            return table[url.rsplit("embed-", 1)[1].split(".")[0]]

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake_page), patch.object(vidmolly, "resolve", side_effect=provider):
            got = self.post("/test_resolvers", {"yaml_text": yaml_text, "detail_url": self.url(0), **extra})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def warning(self, out, start):
        found = [w for w in out["warnings"] if w.startswith(start)]
        return found[0] if found else None

    def test_two_more_pages_are_sampled_from_the_list_and_all_resolve(self):
        out = self.go()
        self.assertEqual(out["errors"], [])
        self.assertEqual((out["status"], out["pages_checked"], out["pages_resolved"]), ("resolved", 3, 3))
        self.assertEqual(self.fetched, [self.url(0), self.LIST, self.url(1), self.url(2)])   # detail first, then the list, then the first other items
        self.assertEqual([p["detail_url"] for p in out["pages"]], [self.url(i) for i in range(3)])
        first, second, third = out["pages"]
        self.assertEqual((first["status"], first["candidates"], first["resolved"], first["error"]), ("resolved", 1, 1, ""))
        self.assertEqual(first["streams"], [{"type": "mp4", "host": "downloader.disk.yandex.ru", "quality": "auto"}])
        self.assertEqual(second["streams"], [{"type": "hls", "host": "video.cdnhost.example", "quality": "auto"}])
        self.assertEqual(third["streams"], [{"type": "mp4", "host": "redirector.googlevideo.com", "quality": "360p"}])
        self.assertTrue(all(p["page_id"].startswith("pg_") for p in out["pages"]))

    def test_the_old_fields_are_the_first_pages(self):
        out = self.go()
        self.assertEqual(out["page_id"], out["pages"][0]["page_id"])
        self.assertEqual((out["candidate_count"], len(out["candidates"]), out["resolved_ok"]), (1, 1, 1))
        self.assertEqual(out["candidates"][0]["url"], "https://vidmoly.me/embed-aaa.html")
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["provider"], got["streams_count"], got["host"], got["stream_type"]),
                         (True, "vidmolly", 1, "downloader.disk.yandex.ru", "mp4"))

    def test_different_hosts_and_types_are_warned_about(self):
        out = self.go()
        warned = self.warning(out, "varied hosts/types:")
        self.assertIsNotNone(warned, out["warnings"])
        for needle in ("page 1 = downloader.disk.yandex.ru/mp4", "page 2 = video.cdnhost.example/hls", "page 3 = redirector.googlevideo.com/mp4"):
            self.assertIn(needle, warned)
        same = self.go(self.site({0: "aaa", 1: "aaa", 2: "aaa"}))
        self.assertEqual((same["status"], same["pages_resolved"]), ("resolved", 3))
        self.assertIsNone(self.warning(same, "varied hosts/types:"), same["warnings"])

    def test_some_pages_failing_is_partial(self):
        out = self.go(self.site({2: "none"}))
        self.assertEqual(out["errors"], [])
        self.assertEqual((out["status"], out["pages_checked"], out["pages_resolved"]), ("partial", 3, 2))
        failed = out["pages"][2]
        self.assertEqual((failed["status"], failed["candidates"], failed["resolved"], failed["streams"]), ("no_stream", 1, 0, []))
        self.assertIn("no stream", failed["error"])
        warned = self.warning(out, "partial: 2 of 3 pages resolved")
        self.assertIsNotNone(warned, out["warnings"])
        self.assertIn(self.url(2), warned)
        self.assertEqual(out["resolved_ok"], 1)   # the old fields are the first page's

    def test_a_page_without_player_or_that_cannot_be_fetched_counts_too(self):
        out = self.go(self.site({1: None}))
        self.assertEqual([p["status"] for p in out["pages"]], ["resolved", "no_candidates", "resolved"])
        self.assertEqual(out["status"], "partial")
        self.assertIn("no candidates", out["pages"][1]["error"])
        pages = self.site()
        del pages[self.url(2)]
        out = self.go(pages)
        self.assertEqual([p["status"] for p in out["pages"]], ["resolved", "resolved", "error"])
        self.assertIn("detail page:", out["pages"][2]["error"])
        self.assertEqual(out["status"], "partial")

    def test_no_page_resolving_keeps_the_old_statuses_and_errors(self):
        out = self.go(streams={**self.STREAMS, "aaa": None, "bbb": None, "ccc": None})
        self.assertEqual((out["status"], out["pages_resolved"], out["errors"]), ("no_stream", 0, []))
        out = self.go(self.site({0: None, 1: None, 2: None}))
        self.assertEqual(out["status"], "no_candidates")
        self.assertTrue(any("no candidates found on the detail page" in e for e in out["errors"]), out["errors"])
        self.assertEqual((out["candidates"], out["resolved"], out["candidate_count"]), ([], [], 0))
        out2 = self.go({}, detail_urls=[])   # the detail page itself cannot be fetched
        self.assertEqual(out2["status"], "error")
        self.assertTrue(any(e.startswith("detail page:") for e in out2["errors"]), out2["errors"])
        self.assertNotIn("page_id", out2)

    def test_detail_urls_name_the_pages_and_the_list_is_not_fetched(self):
        out = self.go(detail_urls=[self.url(5), self.url(0) + "/", self.url(5), "  "])
        self.assertEqual(self.fetched, [self.url(0), self.url(5)])   # the detail_url again and the duplicates are dropped
        self.assertEqual([p["detail_url"] for p in out["pages"]], [self.url(0), self.url(5)])
        self.assertEqual((out["status"], out["pages_checked"]), ("resolved", 2))
        self.assertIsNone(self.warning(out, "only one page"))

    def test_an_empty_detail_urls_is_one_page_and_the_old_shape(self):
        out = self.go(detail_urls=[])
        self.assertEqual(self.fetched, [self.url(0)])
        self.assertEqual((out["status"], len(out["pages"]), out["resolved_ok"]), ("resolved", 1, 1))
        self.assertIsNone(self.warning(out, "only one page"))

    def test_at_most_four_detail_urls_are_accepted(self):
        with public_dns():
            got = self.post("/test_resolvers", {"yaml_text": DRAFT_YAML, "detail_url": self.url(0),
                                                "detail_urls": [self.url(i) for i in range(1, 6)]})
        self.assertEqual(got.status_code, 422, got.text)
        out = self.go(detail_urls=[self.url(i) for i in range(1, 5)])
        self.assertEqual(len(out["pages"]), 5)   # detail_url + 4

    def test_a_stored_list_page_is_sampled_without_fetching_the_list(self):
        lid = self.page(list_html(6), self.LIST)
        out = self.go(list_page_id=lid)
        self.assertEqual(self.fetched, [self.url(0), self.url(1), self.url(2)])
        self.assertEqual(out["pages_checked"], 3)

    def test_a_stored_detail_page_is_still_the_first_page(self):
        pid = self.page(self.site()[self.url(0)], self.url(0))
        out = self.go(page_id=pid)
        self.assertNotIn(self.url(0), self.fetched)
        self.assertEqual((out["page_id"], out["pages_checked"]), (pid, 3))

    def test_no_list_page_means_one_page_and_a_warning(self):
        out = self.go(self.site(lists=False))
        self.assertEqual((out["status"], len(out["pages"])), ("resolved", 1))
        warned = self.warning(out, "only one page was tried")
        self.assertIsNotNone(warned, out["warnings"])
        self.assertIn("detail_urls", warned)

    def test_pages_that_do_not_fit_the_call_are_skipped_and_not_judged(self):
        with patch.object(sb, "_trial_need", return_value=1e9):
            out = self.go(detail_urls=[self.url(1), self.url(2)])
            sampled = self.go()
        self.assertEqual([p["status"] for p in out["pages"]], ["resolved", "skipped", "skipped"])
        self.assertEqual((out["status"], out["pages_checked"], out["pages_resolved"]), ("resolved", 1, 1))
        self.assertIn("not tried", out["pages"][1]["error"])
        self.assertIsNotNone(self.warning(out, "2 page(s) not tried"), out["warnings"])
        self.assertEqual(len(sampled["pages"]), 1)   # sampling needs the time too
        self.assertIsNotNone(self.warning(sampled, "only the first page was tried"), sampled["warnings"])
        self.assertIsNone(self.warning(out, "varied hosts/types:"))
        self.assertEqual(self.fetched, [self.url(0)])

    def test_the_call_limit_while_fetching_a_page_is_a_skip_not_an_error(self):
        real = sb._fetch_store

        def late(url, mode, wait_for, deadline, referer=""):
            if url.endswith("film-2"):
                raise sb.ApiError(504, "timeout", "the call exceeded its time limit")
            return real(url, mode, wait_for, deadline, referer)

        with patch.object(sb, "_fetch_store", side_effect=late):
            out = self.go()
        self.assertEqual([p["status"] for p in out["pages"]], ["resolved", "resolved", "skipped"])
        self.assertEqual(out["status"], "resolved")

    def test_a_site_that_needs_a_browser_is_judged_page_by_page_within_the_call(self):
        yaml_text = DRAFT_YAML.replace("  - {type: iframe, selector: \"iframe#player\", attr: src}",
                                       "  - {type: iframe, selector: \"iframe#player\", attr: src, fetch: browser}")
        self.assertIn("fetch: browser", yaml_text)
        self.assertGreater(sb._trial_need(sb._load_yaml(yaml_text)[0]), sb._trial_need(sb._load_yaml(DRAFT_YAML)[0]))


class ChromeFetchTest(SandboxCase):
    """``POST /fetch`` with a ``referer`` (or ``mode: chrome``): the Chrome-fingerprint transport (``impersonated_get``),
    never crawlee / the browser; ``auto`` is unchanged."""
    URL = "https://demo.example/player/oynat/abc123"
    DETAIL = "https://demo.example/film/100/film-0"
    PLAYER_PAGE = '<html><head><title>Player</title></head><body><script>sources: [{file:"https://cdn.example/v.mp4?x=1&y=2"}]</script></body></html>'

    def page_of(self, html=None, url=None, status=200):
        return fetch.ImpersonatedPage(html or self.PLAYER_PAGE, url or self.URL, status)

    def test_a_referer_fetches_with_the_chrome_transport_and_stores_it(self):
        with public_dns(), patch.object(fetch, "impersonated_get", return_value=self.page_of()) as get, \
                patch.object(fetch, "page_bundle", side_effect=AssertionError("no crawlee / browser")):
            got = self.post("/fetch", {"url": self.URL, "referer": self.DETAIL})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["fetch_mode"], body["status"], body["final_url"], body["title"]),
                         ("chrome", 200, self.URL, "Player"))
        self.assertNotIn("sources:", body["html_excerpt"])   # the excerpt hides scripts: grep_page (below) finds the media URL
        call = get.call_args
        self.assertEqual(call.args[0], self.URL)
        self.assertEqual(call.kwargs["headers"], {"Referer": self.DETAIL})
        self.assertEqual((call.kwargs["max_bytes"], call.kwargs["max_redirects"]), (3_000_000, 3))
        self.assertLessEqual(call.kwargs["timeout"], sb.CHROME_TIMEOUT)
        self.assertIn("NOT a valid yaml fetch_mode", body["hint"])
        html, meta = onboard_store.load_page(body["page_id"])
        self.assertIn("cdn.example/v.mp4", html)
        self.assertEqual((meta["fetch_mode"], meta["referer"], meta["status"]), ("chrome", self.DETAIL, 200))
        grep = self.post("/grep", {"page_id": body["page_id"], "pattern": r'sources\s*:\s*\[\s*\{\s*file\s*:\s*"([^"]+)"'})
        self.assertEqual(grep.json()["count"], 1)

    def test_mode_chrome_without_a_referer(self):
        with public_dns(), patch.object(fetch, "impersonated_get", return_value=self.page_of()) as get:
            got = self.post("/fetch", {"url": self.URL, "mode": "chrome"})
        self.assertEqual((got.status_code, got.json()["fetch_mode"]), (200, "chrome"))
        self.assertEqual(get.call_args.kwargs["headers"], {})
        self.assertNotIn("referer", onboard_store.load_page(got.json()["page_id"])[1])

    def test_a_referer_wins_over_the_other_modes_and_auto_is_unchanged(self):
        for mode in ("auto", "http", "browser"):
            with public_dns(), patch.object(fetch, "impersonated_get", return_value=self.page_of()) as get, \
                    patch.object(fetch, "page_bundle", side_effect=AssertionError):
                got = self.post("/fetch", {"url": self.URL, "mode": mode, "referer": self.DETAIL})
            self.assertEqual((got.status_code, got.json()["fetch_mode"], get.call_count), (200, "chrome", 1), mode)
        with public_dns(), patch.object(fetch, "impersonated_get", side_effect=AssertionError("auto never uses chrome")), \
                patch.object(fetch, "page_bundle", return_value=bundle(list_html())) as m:
            got = self.post("/fetch", {"url": "https://demo.example/filmler"})
        self.assertEqual((got.json()["fetch_mode"], m.call_count), ("http", 1))
        with public_dns(), patch.object(fetch, "impersonated_get", side_effect=AssertionError), \
                patch.object(fetch, "page_bundle", return_value=bundle(list_html())) as m:
            got = self.post("/fetch", {"url": "https://demo.example/filmler", "referer": ""})   # an empty referer = none
        self.assertEqual((got.json()["fetch_mode"], m.call_count), ("http", 1))

    def test_a_failure_is_a_502_without_escalation_and_nothing_is_stored(self):
        err = fetch.FetchError("HTTP 404 for " + self.URL, 404)
        with public_dns(), patch.object(fetch, "impersonated_get", side_effect=err) as get, \
                patch.object(fetch, "page_bundle", side_effect=AssertionError("no browser escalation")):
            got = self.post("/fetch", {"url": self.URL, "mode": "chrome", "referer": self.DETAIL})
        self.assertEqual((got.status_code, get.call_count), (502, 1))
        self.assertEqual(got.json()["detail"]["code"], "fetch_failed")
        self.assertIn("chrome: HTTP 404", got.json()["detail"]["message"])
        folder = os.path.join(onboard_store.root(), "pages")
        self.assertEqual(os.listdir(folder) if os.path.isdir(folder) else [], [])

    def test_private_urls_are_refused_before_any_request_and_on_every_hop(self):
        with public_dns(), patch.object(fetch, "impersonated_get") as get:
            for url in ("http://127.0.0.1/p", "http://10.0.0.5/p", "file:///etc/passwd"):
                got = self.post("/fetch", {"url": url, "referer": self.DETAIL})
                self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "url_rejected"), url)
        get.assert_not_called()
        with public_dns(), patch.object(fetch, "impersonated_get",
                                        return_value=self.page_of(url="http://10.0.0.5/secret")):
            got = self.post("/fetch", {"url": self.URL, "referer": self.DETAIL})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "url_rejected"))   # reported final URL re-checked
        # the allow callback handed to the transport is netguard (every redirect hop)
        with public_dns(), patch.object(fetch, "impersonated_get", return_value=self.page_of()) as get:
            self.post("/fetch", {"url": self.URL, "referer": self.DETAIL})
        allow = get.call_args.kwargs["allow"]
        with public_dns():
            self.assertTrue(allow("https://cdn.example/x"))
        for url in ("http://10.0.0.5/x", "http://127.0.0.1:8090/admin", "ftp://a.example/x"):
            with public_dns():
                self.assertFalse(allow(url), url)

    def test_the_referer_is_a_header_not_a_fetch_target(self):
        with public_dns(), patch.object(fetch, "impersonated_get", return_value=self.page_of()) as get:
            got = self.post("/fetch", {"url": self.URL, "referer": "http://192.168.0.61:8090/"})
        self.assertEqual(got.status_code, 200)   # like {page_url} in a yaml referer: only sent, never requested
        self.assertEqual(get.call_args.kwargs["headers"], {"Referer": "http://192.168.0.61:8090/"})

    def test_a_malformed_referer_is_a_400(self):
        for referer in ("not a url", "javascript:alert(1)", "ftp://x.example/", "https://a.example/x\r\nX-Evil: 1",
                        "//demo.example/film"):
            with public_dns(), patch.object(fetch, "impersonated_get") as get:
                got = self.post("/fetch", {"url": self.URL, "referer": referer})
            self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "bad_referer"), referer)
            get.assert_not_called()

    def test_the_fetch_mode_enum_and_schema(self):
        got = self.post("/fetch", {"url": self.URL, "mode": "curl"})
        self.assertEqual(got.status_code, 422)
        props = sb.FetchBody.model_json_schema()["properties"]
        self.assertEqual(set(props["mode"]["enum"]), {"auto", "http", "browser", "chrome"})
        self.assertIn("referer", props)


class FakePlayer:
    """Stands in for a resolver type that answers with the ``stream`` shortcut (json_api / player_page)."""
    NAME = "fake_player"
    DESCRIPTION = "test double"
    PARAMS = {"selector": {"type": "str", "required": True, "default": None, "help": "x"},
              "fetch": {"type": "str", "required": False, "default": "http", "help": "x"}}
    delays: dict = {}
    streams: dict = {}

    @staticmethod
    def discover(ctx, html, page_url, params):
        from selectolax.parser import HTMLParser
        from urllib.parse import urljoin
        return [{"url": urljoin(page_url, n.attributes["src"]), "label": "Fake"}
                for n in HTMLParser(html).css(params["selector"]) if n.attributes.get("src")]

    @staticmethod
    def resolve_candidate(ctx, candidate, page_url, params, load_cookies):
        time.sleep(FakePlayer.delays.get(candidate["url"].rsplit("/", 1)[-1], 0))
        stream = FakePlayer.streams.get(candidate["url"].rsplit("/", 1)[-1])
        return {**candidate, "stream": stream} if stream else {**candidate}


PLAYERS_HTML = ('<html><body><iframe class="p" src="/player/oynat/aaa"></iframe>'
                '<iframe class="p" src="/player/oynat/bbb"></iframe></body></html>')
HLS = {"url": "https://cdn.example/hls/a.m3u8", "type": "hls", "quality": "auto", "duration": 0, "provider": "Player",
       "streams": [{"url": "https://cdn.example/hls/a.m3u8", "type": "hls", "quality": "auto", "label": "Player"}]}


class StreamShortcutTest(SandboxCase):
    """A resolver type that returns ``stream`` (json_api, player_page) skips the providers; test_resolvers counts it."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(sb.resolvers.TYPES, {"fake_player": FakePlayer})
        patcher.start()
        self.addCleanup(patcher.stop)
        FakePlayer.delays, FakePlayer.streams = {}, {}
        self.addCleanup(lambda: (FakePlayer.delays.clear(), FakePlayer.streams.clear()))

    def yaml(self, **extra):
        item = ", ".join(f"{k}: {v}" for k, v in {"type": "fake_player", "selector": '"iframe.p"', **extra}.items())
        return with_yaml(**{'  - {type: iframe, selector: "iframe#player", attr: src}': "  - {" + item + "}"})

    def run_resolvers(self, yaml_text, detail=PLAYERS_HTML):
        with public_dns(), patch.object(fetch, "page_bundle", return_value=bundle(detail)), \
                patch("app.scraper.providers.registry.fetch.fetch_url") as fetch_url:
            got = self.post("/test_resolvers", {"yaml_text": yaml_text, "detail_url": "https://demo.example/film/100/film-0"})
        self.assertEqual(got.status_code, 200, got.text)
        fetch_url.assert_not_called()   # no provider was asked
        return got.json()

    def test_stream_shortcut_counts_as_resolved(self):
        FakePlayer.streams = {"aaa": {**HLS, "provider": "SiteHost"}}
        out = self.run_resolvers(self.yaml())
        self.assertEqual(out["errors"], [])
        self.assertEqual((out["status"], out["resolved_ok"], out["candidate_count"]), ("resolved", 1, 2))
        first, second = out["resolved"]
        self.assertEqual((first["ok"], first["provider"], first["streams_count"]), (True, "SiteHost", 1))
        self.assertEqual((first["host"], first["stream_type"]), ("cdn.example", "hls"))
        self.assertEqual(first["candidate"], "https://demo.example/player/oynat/aaa")
        self.assertFalse(second["ok"])                       # bbb gave no stream
        self.assertIn("no stream", second["error"])
        self.assertNotIn("host", second)
        self.assertEqual(out["candidates"][0]["resolver_type"], "fake_player")

    def test_a_shortcut_with_only_a_url_is_one_stream(self):
        FakePlayer.streams = {"aaa": {"url": "https://cdn.example/v/a.mp4", "type": "mp4", "provider": "P"}}
        out = self.run_resolvers(self.yaml())
        self.assertEqual((out["resolved"][0]["ok"], out["resolved"][0]["streams_count"], out["resolved"][0]["stream_type"]),
                         (True, 1, "mp4"))

    def test_status_tells_the_outcome(self):
        out = self.run_resolvers(self.yaml())
        self.assertEqual((out["status"], out["resolved_ok"]), ("no_stream", 0))
        out = self.run_resolvers(self.yaml(), detail="<html><body>nothing</body></html>")
        self.assertEqual(out["status"], "no_candidates")
        out = self.run_resolvers(self.yaml(**{"nosuch": "1"}))
        self.assertEqual(out["status"], "error")
        self.assertTrue(any("unknown parameter" in e for e in out["errors"]), out["errors"])

    def test_fetch_browser_runs_one_by_one_and_stays_inside_the_call_limit(self):
        FakePlayer.streams = {"aaa": HLS, "bbb": HLS}
        FakePlayer.delays = {"aaa": 0.1, "bbb": 1.5}
        started = time.monotonic()
        with patch.object(config, "ONBOARD_TOOL_TIMEOUT", 1.0), patch.object(sb, "RESOLVE_MARGIN", 0.3):
            out = self.run_resolvers(self.yaml(fetch="browser"))
        self.assertLess(time.monotonic() - started, 1.4)
        self.assertTrue(any("fetch: browser" in w and "one after the other" in w for w in out["warnings"]), out["warnings"])
        first, second = out["resolved"]
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("timeout: not finished within the 1s call limit", second["error"])
        self.assertIn("fetch_page mode browser", second["error"])
        self.assertEqual((out["status"], out["resolved_ok"]), ("resolved", 1))

    def test_browser_candidates_are_not_cut_by_the_live_playback_limits(self):
        FakePlayer.streams = {"aaa": HLS}
        FakePlayer.delays = {"aaa": 0.3}
        with patch.object(config, "RESOLVE_CANDIDATE_TIMEOUT", 0.1), patch.object(config, "RESOLVE_TOTAL_TIMEOUT", 0.2):
            out = self.run_resolvers(self.yaml(fetch="browser"))
        self.assertTrue(out["resolved"][0]["ok"], out["resolved"])
        self.assertTrue(any("live playback" in w and "cut off" in w for w in out["warnings"]), out["warnings"])

    def stamped(self, budget):
        """FakePlayer.discover that stamps a per-candidate ``timeout`` like player_page does with fetch: browser."""
        original = FakePlayer.discover
        return patch.object(FakePlayer, "discover", staticmethod(lambda *a: [{**c, "timeout": budget} for c in original(*a)]))

    def test_candidate_budget_is_the_live_limit_the_warning_compares_against(self):
        FakePlayer.streams = {"aaa": HLS}
        FakePlayer.delays = {"aaa": 0.3}
        with patch.object(config, "RESOLVE_CANDIDATE_TIMEOUT", 0.1), patch.object(config, "RESOLVE_TOTAL_TIMEOUT", 0.2), \
                self.stamped(0.2):
            out = self.run_resolvers(self.yaml(fetch="browser"))
        self.assertTrue(any("live playback gives this candidate 0.2s" in w and "cut off" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(any("only 0.2s (2.2s in total)" in w for w in out["warnings"]), out["warnings"])
        with patch.object(config, "RESOLVE_CANDIDATE_TIMEOUT", 0.1), patch.object(config, "RESOLVE_TOTAL_TIMEOUT", 0.2), \
                self.stamped(5.0):
            out = self.run_resolvers(self.yaml(fetch="browser"))
        self.assertTrue(out["resolved"][0]["ok"], out["resolved"])
        self.assertFalse(any("cut off" in w for w in out["warnings"]), out["warnings"])

    def test_a_call_that_runs_into_the_hard_limit_says_why(self):
        def slow(body, *, deadline):
            time.sleep(1.0)
            return {}
        with patch.object(config, "ONBOARD_TOOL_TIMEOUT", 0.2), patch.object(sb, "_do_test_resolvers", slow):
            got = self.post("/test_resolvers", {"yaml_text": DRAFT_YAML, "detail_url": "https://demo.example/film/1"})
        self.assertEqual(got.status_code, 504)
        self.assertIn("fetch: browser", got.json()["detail"]["message"])

    def test_page_url_in_a_referer_is_not_an_ssrf_problem(self):
        data = {"base_url": "https://demo.example", "resolvers": [{"type": "player_page", "selector": "iframe", "referer": "{page_url}"}]}
        with public_dns():
            self.assertEqual(sb._url_params_errors(data), [])
            data["resolvers"][0]["referer"] = "http://192.168.0.61:8090/"
            self.assertEqual(len(sb._url_params_errors(data)), 1)


@unittest.skipUnless("player_page" in sb.resolvers.TYPES, "the player_page resolver type is not there")
class PlayerPageTest(SandboxCase):
    """The real ``player_page`` type end to end through ``test_resolvers`` (player page bodies are canned)."""
    YAML = with_yaml(**{'  - {type: iframe, selector: "iframe#player", attr: src}': (
        '  - type: player_page\n    selector: \'iframe[src*="/player/"]\'\n    label: SiteHost\n'
        '    extract:\n      - {regex: \'file:"(https?://[^"]+\\.mp4)"\'}')})
    DETAIL = '<html><body><iframe src="/player/oynat/abc123"></iframe></body></html>'

    def run_resolvers(self, yaml_text=None, body=PLAYER_HTML, browser=None):
        with public_dns(), patch.object(fetch, "page_bundle", return_value=bundle(self.DETAIL)), \
                patch.object(fetch, "fetch_impersonated", return_value=body) as limited, \
                patch.object(fetch, "browser_page", return_value=browser or body) as browser_page:
            got = self.post("/test_resolvers", {"yaml_text": yaml_text or self.YAML, "detail_url": "https://demo.example/film/100/film-0"})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json(), limited, browser_page

    def test_media_url_is_found_in_the_player_page(self):
        out, limited, browser_page = self.run_resolvers()
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["candidates"][0]["resolver_type"], "player_page")
        self.assertEqual(out["candidates"][0]["url"], "https://demo.example/player/oynat/abc123")
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["provider"], got["streams_count"], got["host"], got["stream_type"]),
                         (True, "SiteHost", 1, "cdn.example", "mp4"))
        self.assertEqual((out["status"], out["resolved_ok"]), ("resolved", 1))
        self.assertEqual(limited.call_args.args[0], "https://demo.example/player/oynat/abc123")
        self.assertEqual(limited.call_args.kwargs["headers"]["Referer"], "https://demo.example/film/100/film-0")
        browser_page.assert_not_called()
        self.assertTrue(any(e["stage"] == "player_page.extract" and e["ok"] for e in got["trace"]), got["trace"])

    def test_cloudflare_page_is_explained_with_the_browser_hint(self):
        out, _limited, _browser = self.run_resolvers(body="<html><title>Just a moment...</title><body>Checking</body></html>")
        got = out["resolved"][0]
        self.assertFalse(got["ok"])
        self.assertEqual(out["status"], "no_stream")
        self.assertIn("Cloudflare challenge", got["error"])
        self.assertIn("player_page.extract", got["error"])
        self.assertIn("fetch: browser", got["error"])

    def test_browser_mode_uses_the_browser_and_is_time_boxed(self):
        text = self.YAML.replace("    label: SiteHost\n", "    label: SiteHost\n    fetch: browser\n")
        out, limited, browser_page = self.run_resolvers(text)
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["resolved"][0]["ok"], out)
        limited.assert_not_called()
        self.assertEqual(browser_page.call_args.args[1], "https://demo.example/player/oynat/abc123")
        self.assertTrue(any("fetch: browser" in w for w in out["warnings"]), out["warnings"])

    def yaml_with(self, extract):
        return with_yaml(**{'  - {type: iframe, selector: "iframe#player", attr: src}': (
            '  - type: player_page\n    selector: \'iframe[src*="/player/"]\'\n    label: SiteHost\n    extract:\n      - ' + extract)})

    def test_a_declared_type_that_contradicts_the_url_is_warned_and_the_url_wins(self):
        text = self.yaml_with('{regex: \'file:"([^"]+)"\', type: mp4}')
        out, _limited, _browser = self.run_resolvers(text, body='jwplayer().setup({file:"https://video.cdnhost.example/pl/x.m3u8?tag=12"});')
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["host"], got["stream_type"]), (True, "video.cdnhost.example", "hls"))
        self.assertTrue(any(e["stage"] == "player_page.type: declared mp4, url is m3u8 -> hls" and e["ok"] for e in got["trace"]), got["trace"])
        warned = [w for w in out["warnings"] if "declared mp4, url is m3u8 -> hls" in w]
        self.assertEqual(len(warned), 1, out["warnings"])
        self.assertIn("candidate 0", warned[0])
        self.assertIn("auto", warned[0])
        self.assertEqual(out["pages"][0]["streams"], [{"type": "hls", "host": "video.cdnhost.example", "quality": "auto"}])

    def test_a_sources_list_gives_one_stream_per_quality_best_first(self):
        text = self.yaml_with('{regex: \'file:"([^"]+)",label:"([^"]+)"\', quality_group: 2}')
        body = ('sources:[{file:"https://cdn.example/360.mp4",label:"360p"},{file:"https://cdn.example/m.m3u8",label:"auto"},'
                '{file:"https://cdn.example/1080.mp4",label:"1080p"}]')
        out, _limited, _browser = self.run_resolvers(text, body=body)
        got = out["resolved"][0]
        self.assertEqual((got["ok"], got["streams_count"], got["stream_type"]), (True, 3, "mp4"))
        self.assertEqual([(s["quality"], s["type"]) for s in out["pages"][0]["streams"]],
                         [("1080p", "mp4"), ("360p", "mp4"), ("auto", "hls")])

    def test_private_player_url_is_refused(self):
        evil = '<iframe src="http://10.0.0.7/player/x"></iframe>'
        with public_dns(), patch.object(fetch, "page_bundle", return_value=bundle(evil)), \
                patch.object(fetch, "fetch_impersonated") as limited:
            out = self.post("/test_resolvers", {"yaml_text": self.YAML, "detail_url": "https://demo.example/film/1"}).json()
        limited.assert_not_called()
        self.assertFalse(out["resolved"][0]["ok"])
        self.assertEqual(out["status"], "no_stream")


TREND_HTML = ('<html><head><title>Trends</title></head><body><ul class="weekly">'
              + "".join(f'<li class="tr"><a href="/film/{200 + i}/trend-{i}"><h5>Trend {i}</h5><img src="/t/{i}.jpg"></a></li>'
                        for i in range(6)) + "</ul></body></html>")
NEW_HTML = ('<html><body>' + "".join(f'<div class="card item"><a class="poster-link" href="/film/{300 + i}/new-{i}">'
                                      f'<img class="thumb" src="/n/{i}.jpg"></a><h2 class="card-title">New {i}</h2>'
                                      f'<span class="year">{2020 + i % 5}</span></div>' for i in range(5)) + '</body></html>')

COLLECTIONS_YAML = """
site_id: demo
collections:
  - {id: latest_movies_demo, title: Yeni Filmler, path: /filmler, role: latest_movies}
  - id: trending_demo
    title: Trendler
    path: /trends
    role: trending
    row_selector: "li.tr"
    fields:
      title: {selector: "h5"}
      detail_url: {selector: "a", attr: href}
      poster_url: {selector: "img", attr: src}
  - {id: noteworthy_movies_demo, title: Dikkate Değer, path: /yeni, role: noteworthy_movies, excluded_fields: [rating]}
"""


class CollectionsTest(SandboxCase):
    def setUp(self):
        super().setUp()
        self.pages = {"/trends": TREND_HTML, "/yeni": NEW_HTML, "/n": NEW_HTML}
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")
        self.fetched = []

    def fake(self, cfg, url, *, wait_for=""):
        path = url.replace(BASE, "")
        if path.startswith("/film/"):   # a playback page (the playable stage of submit): a player iframe, not a collection
            return bundle(DETAIL_HTML)
        self.fetched.append(path)
        html = list_html(12) if path == "/filmler" else self.pages.get(path)
        if html is None:
            raise fetch.FetchError("http_404")
        return bundle(html)

    def run_config(self, extra_yaml=COLLECTIONS_YAML, base=DRAFT_YAML, **body):
        payload = {"yaml_text": base + extra_yaml, "page_id": self.list_id, "detail_page_id": self.detail_id,
                   "collections": True, **body}
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake):
            got = self.post("/test_config", payload)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def by_id(self, out):
        return {c["id"]: c for c in out["collections"]}

    def test_collections_are_checked_and_fetched(self):
        out = self.run_config()
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["passed"], out["criteria"])
        cols = self.by_id(out)
        self.assertEqual(list(cols), ["latest_movies_demo", "trending_demo", "noteworthy_movies_demo"])
        latest, trend, note = cols["latest_movies_demo"], cols["trending_demo"], cols["noteworthy_movies_demo"]
        self.assertEqual((latest["status"], latest["role"], latest["path"], latest["count"], latest["valid_count"]),
                         ("ok", "latest_movies", "/filmler", 12, 12))
        self.assertEqual((trend["status"], trend["count"], trend["valid_count"]), ("ok", 6, 6))   # its own row_selector / fields
        self.assertEqual(trend["samples"][0]["title"], "Trend 0")
        self.assertEqual((note["status"], note["valid_count"]), ("ok", 5))
        for entry in cols.values():
            self.assertTrue(entry["normalize_ok"])
            self.assertEqual(entry["errors"], [])
            self.assertLessEqual(len(entry["samples"]), 3)
        self.assertEqual(out["criteria"]["collections_valid_count"], {"value": 5, "min": sb.MIN_COLLECTION_COUNT, "ok": True})
        self.assertEqual(out["criteria"]["collections_normalize_ok_ratio"]["value"], 1.0)
        # the list page is not fetched again (stored page), /trends and /yeni once each
        self.assertEqual(sorted(self.fetched), ["/trends", "/yeni"])

    def test_collections_report_would_ingest_under_the_item_limit(self):
        out = self.run_config()
        self.assertEqual({k: c["would_ingest"] for k, c in self.by_id(out).items()},
                         {"latest_movies_demo": 12, "trending_demo": 6, "noteworthy_movies_demo": 5})
        self.assertEqual(out["ingest"], {"item_limit": 30, "list_items_on_page": 12, "would_ingest": 12})
        capped = self.run_config(base=with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: 4"}))
        self.assertEqual({k: (c["valid_count"], c["would_ingest"]) for k, c in self.by_id(capped).items()},
                         {"latest_movies_demo": (4, 4), "trending_demo": (4, 4), "noteworthy_movies_demo": (4, 4)})
        self.assertEqual(capped["ingest"], {"item_limit": 4, "list_items_on_page": 12, "would_ingest": 4})
        wide = self.run_config(base=with_yaml(**{"display_name: Demo": "display_name: Demo\nitem_limit: 9999"}))
        self.assertTrue(any(e.startswith("item_limit:") for e in wide["errors"]))
        self.assertEqual(wide["ingest"]["item_limit"], 200)   # ingest would clamp it to 200 as well

    def test_a_collection_that_is_not_read_ingests_nothing(self):
        out = self.run_config("""
site_id: demo
collections:
  - {id: trending_demo, title: T, path: /yok, role: trending}
""")
        self.assertEqual(out["collections"][0]["status"], "error")
        self.assertEqual(out["collections"][0]["would_ingest"], 0)

    def test_latest_series_is_a_role_onboarding_writes(self):
        self.assertIn("latest_series", sb.ONBOARD_ROLES)
        out = self.run_config("""
site_id: demo
collections:
  - {id: latest_series_demo, title: Yeni Diziler, path: /yeni, role: latest_series}
""")
        self.assertEqual([c["status"] for c in out["collections"]], ["ok"])
        self.assertEqual(out["collections"][0]["would_ingest"], 5)
        self.assertFalse(any("whole catalogue" in w for w in out["warnings"]), out["warnings"])
        wrong = self.run_config("""
site_id: demo
collections:
  - {id: latest_series_other, title: Yeni Diziler, path: /yeni, role: latest_series}
""")
        self.assertTrue(any("must be 'latest_series_demo'" in e for e in wrong["errors"]), wrong["errors"])

    def test_two_collections_on_one_page_fetch_it_once(self):
        yaml_text = """
site_id: demo
collections:
  - {id: trending_demo, title: T, path: /yeni, role: trending}
  - {id: latest_episodes_demo, title: E, path: /yeni, role: latest_episodes, required_fields: [year]}
"""
        out = self.run_config(yaml_text)
        self.assertEqual([c["status"] for c in out["collections"]], ["ok", "ok"])
        self.assertEqual(self.fetched, ["/yeni"])

    def test_the_list_path_with_its_own_selector_is_parsed_separately_without_one_it_is_filtered(self):
        yaml_text = """
site_id: demo
collections:
  - {id: trending_demo, title: T, path: /filmler, role: trending, required_fields: [poster_url]}
  - id: featured_demo
    title: F
    path: /filmler
    role: featured
    row_selector: "div.card.item:nth-child(-n+4)"
"""
        out = self.run_config(yaml_text)
        cols = self.by_id(out)
        self.assertEqual((cols["trending_demo"]["status"], cols["trending_demo"]["valid_count"]), ("ok", 12))
        self.assertEqual((cols["featured_demo"]["count"], cols["featured_demo"]["valid_count"]), (4, 4))
        self.assertEqual(self.fetched, [])   # the stored list page served both

    def test_too_few_items_fail_the_criterion(self):
        self.pages["/few"] = "<html><body>" + NEW_HTML.split("<body>")[1].split("</div>")[0] + "</div></body></html>"
        out = self.run_config(COLLECTIONS_YAML.replace("/yeni", "/few"))
        note = self.by_id(out)["noteworthy_movies_demo"]
        self.assertEqual((note["status"], note["valid_count"]), ("error", 1))
        self.assertIn("only 1 usable", note["errors"][0])
        self.assertFalse(out["criteria"]["collections_valid_count"]["ok"])
        self.assertFalse(out["passed"])
        self.assertTrue(any("noteworthy_movies_demo" in w for w in out["warnings"]))
        self.assertEqual(out["errors"], [])   # a thin collection is a criterion, not a config error

    def test_a_low_normalize_share_fails_the_criterion(self):
        self.pages["/yeni"] = NEW_HTML.replace("/film/300/", "/serie/300/").replace("/film/301/", "/serie/301/").replace("/film/302/", "/serie/302/")
        out = self.run_config()
        note = self.by_id(out)["noteworthy_movies_demo"]
        self.assertEqual((note["valid_count"], note["normalize_ok"]), (5, False))
        self.assertEqual(note["normalize_ok_ratio"], 0.4)
        self.assertEqual(out["criteria"]["collections_normalize_ok_ratio"], {"value": 0.4, "min": 0.9, "ok": False})
        self.assertFalse(out["passed"])

    def test_a_page_that_cannot_be_fetched_or_a_selector_that_matches_nothing_is_an_error(self):
        self.pages["/yeni"] = None
        self.pages["/trends"] = "<html><body><p>nothing here</p></body></html>"
        out = self.run_config()
        cols = self.by_id(out)
        self.assertEqual(cols["noteworthy_movies_demo"]["status"], "error")
        self.assertIn("page not available", cols["noteworthy_movies_demo"]["errors"][0])
        self.assertIn("matched 0 elements", cols["trending_demo"]["errors"][0])
        self.assertEqual(cols["latest_movies_demo"]["status"], "ok")
        self.assertFalse(out["valid"])
        self.assertTrue(any(e.startswith("collections[trending_demo]: row_selector") for e in out["errors"]))
        self.assertFalse(out["passed"])

    def test_syntax_errors_are_reported_per_collection_and_nothing_is_fetched_for_them(self):
        yaml_text = """
site_id: demo
collections:
  - {id: trending_other, title: T, path: /trends, role: trending}
  - {id: latest_movies_demo, title: L, path: "https://elsewhere.example/x", role: latest_movies}
  - {id: noteworthy_movies_demo, title: N, path: /yeni, role: noteworthy_movies, colour: red, row_selector: "a:::b", required_fields: year}
  - {id: genre_demo, title: G, path: /g, role: genre}
  - {id: made_up_demo, title: M, path: /m, role: made_up}
  - {id: featured_demo, title: F, path: /f, role: featured, fields: {title: {selector: h2}}}
  - {id: new_demo, title: N, path: /n, role: new}
  - {id: new_demo, title: N2, path: /n2, role: new}
"""
        out = self.run_config(yaml_text)
        cols = out["collections"]
        text = lambda i: " | ".join(cols[i]["errors"])
        self.assertIn("must be 'trending_demo'", text(0))
        self.assertIn("own host", text(1))
        self.assertIn("unknown key(s) colour", text(2))
        self.assertIn("row_selector: invalid CSS selector", text(2))
        self.assertIn("genre: role genre needs a slug", text(3))
        self.assertIn("role: 'made_up' is not one of", text(4))
        self.assertIn("fields.detail_url: required", text(5))
        self.assertIn("required_fields: must be a list", text(2))
        self.assertEqual((cols[6]["errors"], cols[6]["status"]), ([], "ok"))
        self.assertIn("used twice", text(7))
        self.assertTrue(all(c["status"] == "error" for i, c in enumerate(cols) if i != 6))
        self.assertEqual(self.fetched, ["/n"])   # only the clean one is fetched
        self.assertFalse(out["valid"])
        self.assertTrue(any(e.startswith("collections[trending_other]: id:") for e in out["errors"]))
        self.assertFalse(out["passed"])

    def test_more_than_eight_collections_are_refused(self):
        roles = ["trending", "latest_episodes", "latest_movies", "noteworthy_movies", "featured", "upcoming", "new", "catalog"]
        lines = "".join(f"  - {{id: {r}_demo, title: T, path: /yeni, role: {r}}}\n" for r in roles)
        lines += "  - {id: genre_demo, title: G, path: /yeni, role: genre, genre: x}\n"
        out = self.run_config("site_id: demo\ncollections:\n" + lines)
        self.assertTrue(any("at most 8" in e for e in out["errors"]))
        self.assertEqual(len(out["collections"]), 8)

    def test_ids_end_in_the_site_id_of_the_yaml_or_one_shared_suffix(self):
        yaml_text = "collections:\n  - {id: trending_one, title: T, path: /yeni, role: trending}\n  - {id: featured_two, title: F, path: /yeni, role: featured}\n"
        out = self.run_config(yaml_text)
        self.assertTrue(any("share one site id" in e for e in out["errors"]))
        self.assertTrue(any("no site_id in the yaml" in w for w in out["warnings"]))
        ok = self.run_config("collections:\n  - {id: trending_one, title: T, path: /yeni, role: trending}\n")
        self.assertEqual(ok["errors"], [])
        self.assertTrue(any("no site_id in the yaml" in w for w in ok["warnings"]))

    def test_no_collections_warns_but_does_not_fail(self):
        out = self.run_config("")
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual(out["collections"], [])
        self.assertIn(sb.NO_COLLECTIONS_WARNING, out["warnings"])
        self.assertNotIn("collections_valid_count", out["criteria"])

    def test_without_the_flag_nothing_changes(self):
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake):
            got = self.post("/test_config", {"yaml_text": DRAFT_YAML + COLLECTIONS_YAML, "page_id": self.list_id,
                                             "detail_page_id": self.detail_id}).json()
        self.assertNotIn("collections", got)
        self.assertEqual(self.fetched, [])
        self.assertEqual(got["errors"], [])
        self.assertNotIn(sb.NO_COLLECTIONS_WARNING, got["warnings"])
        self.assertEqual(set(got["criteria"]), {"valid_count", "title_fill", "detail_url_fill", "poster_url_fill",
                                                "normalize_ok_ratio", "duplicate_key_ratio", "config_errors"})

    def test_a_broken_list_leaves_the_collections_unfetched(self):
        out = self.run_config(base=DRAFT_YAML.replace('row_selector: "div.card.item"', 'row_selector: "a:::b"'))
        self.assertFalse(out["passed"])
        self.assertEqual({c["status"] for c in out["collections"]}, {"skipped"})
        self.assertTrue(any("not fetched" in w for w in out["warnings"]))
        self.assertEqual(self.fetched, [])

    def test_collections_that_do_not_fit_the_time_are_skipped_with_a_warning(self):
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake):
            out = sb._analyze(DRAFT_YAML + COLLECTIONS_YAML, self.list_id, self.detail_id, time.monotonic() + 2.0,
                              collections=True)
        cols = self.by_id(out)
        self.assertEqual(cols["latest_movies_demo"]["status"], "ok")   # the stored list page needs no fetch
        self.assertEqual((cols["trending_demo"]["status"], cols["noteworthy_movies_demo"]["status"]), ("skipped", "skipped"))
        self.assertEqual(self.fetched, [])
        self.assertTrue(any("trending_demo, noteworthy_movies_demo not checked" in w for w in out["warnings"]))
        self.assertTrue(out["passed"], out["criteria"])   # unchecked is a warning, not a failure
        self.assertEqual(out["criteria"]["collections_valid_count"]["value"], 12)

    def test_the_collection_page_goes_through_the_ssrf_guard(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]), \
                patch.object(fetch, "page_bundle", side_effect=self.fake) as m:
            out = sb._analyze(DRAFT_YAML + COLLECTIONS_YAML, self.list_id, self.detail_id, time.monotonic() + 100,
                              collections=True)
        self.assertEqual(m.call_count, 0)
        self.assertEqual(self.by_id(out)["trending_demo"]["status"], "error")
        self.assertIn("page not available", self.by_id(out)["trending_demo"]["errors"][0])

    def test_acceptance_constants(self):
        self.assertEqual((sb.MIN_COLLECTION_COUNT, sb.MAX_COLLECTIONS), (3, 8))

    def test_submit_always_checks_collections_and_stores_them_in_the_report(self):
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake), vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": DRAFT_YAML + COLLECTIONS_YAML,
                                        "site_id_suggestion": "demo", "notes": "", "page_id": self.list_id,
                                        "detail_page_id": self.detail_id})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertTrue(body["passed"], body["criteria"])
        self.assertEqual([(c["id"], c["status"]) for c in body["collections"]],
                         [("latest_movies_demo", "ok"), ("trending_demo", "ok"), ("noteworthy_movies_demo", "ok")])
        self.assertEqual([c["would_ingest"] for c in body["collections"]], [12, 6, 5])
        self.assertEqual(body["ingest"], {"item_limit": 30, "list_items_on_page": 12, "would_ingest": 12})
        report = onboard_store.get_draft(self.draft["id"])["report"]
        self.assertEqual(len(report["collections"]), 3)
        self.assertEqual(report["collections"][1]["samples"][0]["title"], "Trend 0")
        self.assertIn("collections_valid_count", report["criteria"])

    def test_submit_ids_must_follow_the_suggested_site_id(self):
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=self.fake):
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": DRAFT_YAML + COLLECTIONS_YAML,
                                        "site_id_suggestion": "other", "notes": "", "page_id": self.list_id,
                                        "detail_page_id": self.detail_id}).json()
        self.assertFalse(got["passed"])
        self.assertTrue(any("must be 'latest_movies_other'" in e for e in got["errors"]), got["errors"])
        self.assertTrue(any("differs from site_id_suggestion" in w for w in got["warnings"]))


# an episode-page-per-card site (series): /<show>-<season>-sezon-<episode>-bolum-izle-full-tek-parca/
def episode_card(i, shows=4):
    return (f'<div class="card item"><a class="poster-link" href="/show-{i % shows}-{1 + i // 8}-sezon-{i + 1}-bolum-izle-full-tek-parca/">'
            f'<img class="thumb" src="/p/{i}.jpg"></a><h2 class="card-title">Show {i % shows} bolum {i + 1}</h2>'
            f'<span class="year">{2000 + i}</span></div>')


EPISODES_HTML = "<html><body><div class=\"grid\">" + "".join(episode_card(i) for i in range(12)) + "</div></body></html>"
EPISODE_NORMALIZE = """normalize:
  host: demo.example
  key:
    from: [detail_url]
    regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum[^/]*/?$'
    template: '{slug}'
  type: series
  episode_source: {enabled: true}
"""


NO_SEASON_NORMALIZE = """normalize:
  host: demo.example
  key:
    from: [detail_url]
    regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<episode>\\d+)-bolum[^/]*/?$'
    template: '{slug}'
  type: series
  episode_source: {enabled: true, default_season: 1}
"""


def swap_normalize(block):
    """DRAFT_YAML with its ``normalize:`` block (up to ``resolvers:``) replaced."""
    head, tail = DRAFT_YAML.split("normalize:\n", 1)
    return head + block + "resolvers:" + tail.split("resolvers:", 1)[1]


class PlayableTest(SandboxCase):
    """``test_config(playable: true)`` (``submit`` always): normalized items are followed from their playback page to a
    stream; the ``playable_ratio`` and ``series_have_episode_sources`` criteria. Pages and providers are canned."""

    def setUp(self):
        super().setUp()
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")
        self.asked = []

    def run_config(self, yaml_text=DRAFT_YAML, list_page=None, player_html=None, stream=None, **extra):
        def fake(cfg, url, *, wait_for=""):
            self.asked.append(url)
            return bundle(player_html or DETAIL_HTML)

        body = {"yaml_text": yaml_text, "page_id": list_page or self.list_id, "detail_page_id": self.detail_id,
                "playable": True, **extra}
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), vidmolly_resolves(stream):
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_film_site_is_followed_to_streams_with_three_different_films(self):
        out = self.run_config()
        self.assertEqual(out["errors"], [])
        block = out["playable"]
        self.assertEqual((block["checked"], block["resolved"], block["skipped"]), (3, 3, 0))
        locators = [s["locator"] for s in block["samples"]]
        self.assertEqual(len(set(locators)), 3)
        self.assertEqual((locators[0], locators[-1]), ("https://demo.example/film/100/film-0", "https://demo.example/film/111/film-11"))
        self.assertEqual(self.asked, locators)   # the playback pages of the items, one after the other
        first = block["samples"][0]
        self.assertEqual((first["ok"], first["error"], first["kind"], first["key"]), (True, "", "movie", "100"))
        self.assertEqual(first["streams"], [{"type": "hls", "host": "cdn.example", "quality": "auto"}])
        self.assertEqual(out["criteria"]["playable_ratio"], {"value": 1.0, "min": sb.MIN_PLAYABLE_RATIO, "ok": True})
        self.assertNotIn("series_have_episode_sources", out["criteria"])   # films: only playable_ratio
        self.assertTrue(out["passed"], out["criteria"])

    def test_constants(self):
        self.assertEqual((sb.MIN_PLAYABLE_RATIO, sb.MIN_SERIES_SOURCE_RATIO, sb.PLAYABLE_SAMPLES), (0.67, 0.9, 3))
        self.assertIn("configure normalize.episode_source", sb.SERIES_SOURCES_ERROR)
        self.assertEqual(sb.TestConfigBody.model_fields["playable"].default, False)

    def test_without_the_flag_nothing_is_fetched_and_no_criterion_is_added(self):
        with public_dns(), patch.object(fetch, "page_bundle") as m:
            out = self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id,
                                             "detail_page_id": self.detail_id}).json()
        m.assert_not_called()
        self.assertNotIn("playable", out)
        self.assertNotIn("playable_ratio", out["criteria"])
        self.assertTrue(out["passed"])

    def test_two_of_three_resolved_passes_and_one_of_three_fails(self):
        def fetch_streams(results):
            side = [GOOD_STREAM if ok else None for ok in results]
            with public_dns(), any_page(), patch.object(vidmolly, "resolve", side_effect=side):
                return self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id,
                                                  "detail_page_id": self.detail_id, "playable": True}).json()

        out = fetch_streams([True, False, True])
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 2))
        self.assertEqual(out["criteria"]["playable_ratio"], {"value": 0.67, "min": 0.67, "ok": True})
        self.assertTrue(out["passed"])
        failed = out["playable"]["samples"][1]
        self.assertEqual((failed["ok"], failed["streams"]), (False, []))
        self.assertIn("no stream", failed["error"])
        out = fetch_streams([False, True, False])
        self.assertEqual(out["criteria"]["playable_ratio"], {"value": 0.33, "min": 0.67, "ok": False})
        self.assertFalse(out["passed"])
        out = fetch_streams([False, False, False])
        self.assertEqual((out["playable"]["resolved"], out["criteria"]["playable_ratio"]["value"]), (0, 0.0))
        self.assertFalse(out["passed"])

    def test_every_sample_reports_its_own_host_type_and_quality(self):
        streams = [{"provider": "p", "streams": [{"url": "https://disk.example/a.mp4", "type": "mp4", "quality": "720p"},
                                                 {"url": "https://disk.example/a-1080.mp4", "type": "mp4", "quality": "1080p"}]},
                   {"provider": "p", "streams": [{"url": "https://redirector.googlevideo.com/b.mp4", "type": "mp4", "quality": "360p"}]},
                   {"provider": "p", "streams": [{"url": "https://video.cdnhost.example/c.m3u8", "type": "hls", "quality": "auto"}]}]
        with public_dns(), any_page(), patch.object(vidmolly, "resolve", side_effect=streams):
            out = self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        got = [[(s["type"], s["host"], s["quality"]) for s in sample["streams"]] for sample in out["playable"]["samples"]]
        self.assertEqual(got, [[("mp4", "disk.example", "720p"), ("mp4", "disk.example", "1080p")],
                               [("mp4", "redirector.googlevideo.com", "360p")], [("hls", "video.cdnhost.example", "auto")]])

    def test_a_page_without_a_player_is_a_failed_sample(self):
        out = self.run_config(player_html="<html><body><p>no player here</p></body></html>")
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 0))
        self.assertIn("no candidates", out["playable"]["samples"][0]["error"])
        self.assertEqual(out["playable"]["samples"][0]["candidates"], 0)
        self.assertFalse(out["criteria"]["playable_ratio"]["ok"])
        self.assertFalse(out["passed"])

    def test_a_trailer_only_draft_cannot_pass(self):
        text = with_yaml(**{"playback: video": "playback: trailer"}).split("resolvers:")[0]
        out = self.run_config(text)
        self.assertEqual(out["playable"]["resolved"], 0)
        self.assertFalse(out["passed"])

    def test_a_page_that_cannot_be_fetched_is_a_failed_sample(self):
        def boom(cfg, url, *, wait_for=""):
            raise fetch.FetchError("http_404")

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=boom), vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 0))
        self.assertRegex(out["playable"]["samples"][0]["error"], r"^page: .*http_404")

    def test_private_playback_pages_are_refused_before_any_request(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]), \
                patch.object(fetch, "page_bundle") as m, patch.object(vidmolly, "resolve") as resolve:
            out = self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        m.assert_not_called()
        resolve.assert_not_called()
        self.assertEqual(out["playable"]["resolved"], 0)
        self.assertIn("non-public", out["playable"]["samples"][0]["error"])

    def test_an_internal_player_url_is_not_resolved(self):
        out = self.run_config(player_html='<iframe id="player" src="http://10.0.0.5/embed/1"></iframe>')
        sample = out["playable"]["samples"][0]
        self.assertFalse(sample["ok"])
        self.assertIn("non-public", sample["error"])

    def test_nothing_is_written_to_the_library_database(self):
        from app import db
        db.init()
        before = {name: db.query_one(f"SELECT COUNT(*) AS n FROM {name}")["n"] for name in ("video_sources", "source_items", "playback_attempts")}
        self.run_config()
        after = {name: db.query_one(f"SELECT COUNT(*) AS n FROM {name}")["n"] for name in before}
        self.assertEqual(after, before)

    # --- series -------------------------------------------------------------------------------------------------------

    def test_series_cards_without_an_episode_source_fail_the_criterion_with_the_hint(self):
        out = self.run_config(with_yaml(**{"type: movie": "type: series"}))
        norm = out["normalize"]
        self.assertEqual((norm["episode_items"], norm["with_video_sources"], norm["series_without_sources"]), (12, 0, 12))
        self.assertEqual(out["criteria"]["series_have_episode_sources"], {"value": 0.0, "min": 0.9, "ok": False})
        self.assertIn(sb.SERIES_SOURCES_ERROR, out["errors"])
        self.assertFalse(out["valid"])
        self.assertFalse(out["passed"])
        self.assertEqual(out["playable"]["samples"][0]["kind"], "series_page")   # no episode page: the card's own page is tried
        self.assertTrue(out["criteria"]["playable_ratio"]["ok"])   # ... and it plays: the other criterion is what catches this
        self.assertFalse(out["criteria"]["config_errors"]["ok"])

    def test_series_with_episode_pages_are_followed_from_their_episode_page(self):
        out = self.run_config(swap_normalize(EPISODE_NORMALIZE), list_page=self.page(EPISODES_HTML))
        self.assertEqual(out["errors"], [])
        norm = out["normalize"]
        self.assertEqual((norm["ok"], norm["episode_items"], norm["with_video_sources"], norm["series_without_sources"]), (12, 12, 12, 0))
        self.assertEqual(norm["duplicate_keys"], [])   # four shows with three episodes each: other episodes are not repeats
        self.assertEqual(out["criteria"]["series_have_episode_sources"], {"value": 1.0, "min": 0.9, "ok": True})
        samples = out["playable"]["samples"]
        self.assertEqual([s["kind"] for s in samples], ["episode"] * 3)
        self.assertEqual(len({s["key"] for s in samples}), 3)   # three different shows, each at one of its episode pages
        self.assertTrue(all(s["locator"].endswith("-bolum-izle-full-tek-parca/") for s in samples), samples)
        self.assertEqual(len({s["locator"] for s in samples}), 3)
        self.assertEqual(out["playable"]["resolved"], 3)
        self.assertTrue(out["passed"], out["criteria"])

    def test_a_site_without_seasons_uses_default_season(self):
        html = ("<html><body>" + "".join(
            f'<div class="card item"><a class="poster-link" href="/show-{i % 4}-{i + 1}-bolum-izle/"><img class="thumb" src="/p/{i}.jpg"></a>'
            f'<h2 class="card-title">Show {i % 4} bolum {i + 1}</h2><span class="year">2001</span></div>' for i in range(12)) + "</body></html>")
        out = self.run_config(swap_normalize(NO_SEASON_NORMALIZE), list_page=self.page(html))
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["normalize"]["with_video_sources"], 12)
        self.assertEqual(out["criteria"]["series_have_episode_sources"]["ok"], True)

    def test_picks_are_titles_first_then_other_pages_of_the_same_titles(self):
        cfg = sb._draft_cfg(sb._load_yaml(swap_normalize(EPISODE_NORMALIZE))[0])
        raws = [{"title": f"S{i % 2} {i}", "detail_url": f"/show-{i % 2}-1-sezon-{i + 1}-bolum-x/"} for i in range(8)]
        picks = sb._playable_picks(cfg, raws)
        self.assertEqual(len(picks), 3)
        self.assertEqual([p["key"] for p in picks], ["show-0", "show-1", "show-0"])   # 2 titles, then a second page of one
        self.assertEqual(len({p["locator"] for p in picks}), 3)
        self.assertEqual(sb._playable_picks(cfg, raws[:1]), [picks[0]])
        self.assertEqual(sb._playable_picks(cfg, [{"title": "x", "detail_url": "/nothing/"}]), [])

    # --- time ---------------------------------------------------------------------------------------------------------

    def test_samples_that_do_not_fit_the_call_are_skipped_and_not_counted(self):
        calls = []

        def follow(cfg, locator, deadline):
            calls.append(locator)
            if len(calls) == 1:
                return {"ok": True, "streams": [{"type": "hls", "host": "cdn.example", "quality": "auto"}], "error": "",
                        "candidates": 1, "ms": 5, "timeout": False}
            return {"ok": False, "streams": [], "error": "page: timeout", "candidates": 0, "ms": 5, "timeout": True}

        warnings = []
        cfg = sb._draft_cfg(sb._load_yaml(DRAFT_YAML)[0])
        items = [{"title": f"F{i}", "detail_url": f"/film/{100 + i}/f"} for i in range(12)]
        with patch.object(sb, "_follow_playback", side_effect=follow), patch.object(sb, "PLAYABLE_SAMPLE_SECONDS", 10 ** 6):
            block = sb._playable_stage(cfg, items, time.monotonic() + 100, warnings)
        self.assertEqual((block["checked"], block["resolved"], block["skipped"]), (1, 1, 2))
        self.assertEqual([bool(s.get("skipped")) for s in block["samples"]], [False, True, True])
        self.assertTrue(any("2 sample(s) not checked" in w for w in warnings), warnings)
        self.assertEqual(sb._playable_criteria(block, None)["playable_ratio"], {"value": 1.0, "min": 0.67, "ok": True})

    def test_a_sample_that_runs_out_of_its_own_time_is_a_failure_not_a_skip(self):
        def follow(cfg, locator, deadline):
            return {"ok": False, "streams": [], "error": "page: timeout", "candidates": 0, "ms": 5, "timeout": True}

        cfg = sb._draft_cfg(sb._load_yaml(DRAFT_YAML)[0])
        items = [{"title": f"F{i}", "detail_url": f"/film/{100 + i}/f"} for i in range(12)]
        with patch.object(sb, "_follow_playback", side_effect=follow):   # the sample deadline (25 s) is before the call's (100 s)
            block = sb._playable_stage(cfg, items, time.monotonic() + 100, [])
        self.assertEqual((block["checked"], block["resolved"], block["skipped"]), (3, 0, 0))

    def test_no_time_left_at_all_skips_everything_and_the_criterion_fails(self):
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=AssertionError("no fetch")), vidmolly_resolves():
            out = sb._analyze(DRAFT_YAML, self.list_id, self.detail_id, time.monotonic() + sb.PLAYABLE_MIN_LEFT - 1, playable=True)
        self.assertEqual((out["playable"]["checked"], out["playable"]["skipped"]), (0, 3))
        self.assertTrue(all(s["skipped"] for s in out["playable"]["samples"]))
        self.assertEqual(out["criteria"]["playable_ratio"]["value"], 0.0)
        self.assertFalse(out["passed"])
        self.assertTrue(any("not checked" in w and "playable: true" in w for w in out["warnings"]), out["warnings"])

    # --- the other paths ----------------------------------------------------------------------------------------------

    def test_config_errors_leave_the_playable_stage_out_and_the_criterion_fails(self):
        with public_dns(), patch.object(fetch, "page_bundle") as m:
            out = self.post("/test_config", {"yaml_text": with_yaml(**{"type: iframe": "type: telepathy"}), "page_id": self.list_id,
                                             "detail_page_id": self.detail_id, "playable": True}).json()
        m.assert_not_called()
        self.assertIsNone(out["playable"])
        self.assertFalse(out["criteria"]["playable_ratio"]["ok"])
        bad = self.post("/test_config", {"yaml_text": "a: [unclosed", "playable": True}).json()
        self.assertIsNone(bad["playable"])
        self.assertFalse(bad["criteria"]["playable_ratio"]["ok"])

    def test_submit_always_runs_it_and_stores_the_samples_in_the_report(self):
        with public_dns(), any_page(), vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": DRAFT_YAML, "site_id_suggestion": "demo",
                                        "notes": "", "page_id": self.list_id, "detail_page_id": self.detail_id})
        body = got.json()
        self.assertTrue(body["passed"], body["criteria"])
        self.assertEqual(body["playable"], {"checked": 3, "resolved": 3, "skipped": 0})
        self.assertIn("playable_ratio", body["criteria"])
        report = onboard_store.get_draft(self.draft["id"])["report"]
        self.assertEqual(len(report["playable"]["samples"]), 3)
        self.assertEqual(report["playable"]["samples"][0]["streams"][0]["host"], "cdn.example")

    def test_submit_of_unplayable_episode_pages_is_not_passed(self):
        with public_dns(), any_page(), vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": with_yaml(**{"type: movie": "type: series"}),
                                        "site_id_suggestion": "demo", "notes": "", "page_id": self.list_id,
                                        "detail_page_id": self.detail_id})
        body = got.json()
        self.assertFalse(body["passed"])
        self.assertIn(sb.SERIES_SOURCES_ERROR, body["errors"])
        self.assertFalse(body["criteria"]["series_have_episode_sources"]["ok"])


# a site whose cards link to SERIES pages (/diziler/<show>-izle/); the episodes are only on the series page
SERIES_LIST_HTML = ("<html><body><div class=\"grid\">" + "".join(
    f'<div class="card item"><a class="poster-link" href="/diziler/show-{i}-izle/"><img class="thumb" src="/p/{i}.jpg"></a>'
    f'<h2 class="card-title">Show {i}</h2><span class="year">{2000 + i}</span></div>' for i in range(12)) + "</div></body></html>")
SERIES_CARD_NORMALIZE = """normalize:
  host: demo.example
  key:
    from: [detail_url]
    regex: '^/diziler/(?P<slug>[a-z0-9-]+?)-izle/?$'
    template: '{slug}'
  type: series
"""
SERIES_PAGE_BLOCK = """series_page:
  row_selector: "ul.episodes li"
  fields:
    title: {selector: "span.name"}
    air_date: {selector: "span.date", cast: date_tr}
  episode_url_regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum'
  series_slug_regex: '^/diziler/(?P<slug>[a-z0-9-]+?)-izle/?$'
  same_series_regex: '^/{slug}-\\d+-sezon'
"""
SEASONLESS_PAGE_BLOCK = """series_page:
  row_selector: "ul.episodes li"
  episode_url_regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<episode>\\d+)-bolum'
  default_season: 1
"""


def series_yaml(block=SERIES_PAGE_BLOCK):
    return swap_normalize(SERIES_CARD_NORMALIZE + block)


def series_page_html(slug, seasons=((1, 3), (2, 2)), row_class="episodes", similar=3, seasonless=False):
    """A series page: ``seasons`` = ((season, episodes) ...) as ``ul.<row_class> li > a`` rows + a "similar series" block."""
    rows = ""
    for s, n in seasons:
        for e in range(1, n + 1):
            href = f"/{slug}-{e}-bolum-izle/" if seasonless else f"/{slug}-{s}-sezon-{e}-bolum-izle-full-tek-parca/"
            rows += (f'<li class="row"><a href="{href}"><span class="name">{e}. Bolum {slug}</span></a>'
                     f'<span class="date">0{e}.0{s}.2025</span></li>')
    more = "".join(f'<li><a href="/beta-1-sezon-{i + 1}-bolum-izle-full-tek-parca/">Beta {i + 1}</a></li>' for i in range(similar))
    return (f'<html><head><title>{slug}</title></head><body><ul class="{row_class}">{rows}</ul>'
            f'<div class="similar"><ul>{more}</ul></div></body></html>')


class SeriesInventoryTest(SandboxCase):
    """``test_config(playable: true)`` reads the series pages of the list's series cards with the yaml ``series_page`` (generic
    inventory engine, in memory): the ``series`` block, the ``series_inventory_ok`` criterion, ``series_have_episode_sources``
    that accepts the inventory, the playable samples taken from the inventory, and the ``episode_links`` of ``outline_page``."""

    def setUp(self):
        super().setUp()
        self.list_id = self.page(SERIES_LIST_HTML)
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")
        self.asked = []
        self.series_html = series_page_html

    def run_config(self, yaml_text=None, page_id=None, **extra):
        def fake(cfg, url, *, wait_for=""):
            self.asked.append(url)
            found = re.search(r"/diziler/(.+?)-izle", url)
            return bundle(self.series_html(found.group(1)) if found else DETAIL_HTML)

        body = {"yaml_text": yaml_text or series_yaml(), "page_id": page_id or self.list_id, "detail_page_id": self.detail_id,
                "playable": True, **extra}
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), vidmolly_resolves():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_three_different_series_pages_are_read_and_the_criteria_pass(self):
        out = self.run_config()
        self.assertEqual(out["errors"], [])
        block = out["series"]
        self.assertEqual((block["checked"], block["with_episodes"], block["skipped"]), (3, 3, 0))
        first, middle, last = block["samples"]
        self.assertEqual((first["series_url"], middle["series_url"], last["series_url"]),
                         ("https://demo.example/diziler/show-0-izle/", "https://demo.example/diziler/show-6-izle/",
                          "https://demo.example/diziler/show-11-izle/"))
        self.assertEqual((first["key"], middle["key"], last["key"]), ("show-0", "show-6", "show-11"))
        self.assertFalse(first["blocked"])
        self.assertEqual((first["episodes"], first["seasons"], first["structured"], first["error"]), (5, 2, True, ""))
        self.assertEqual(first["first"], {"season": 1, "episode": 1, "url": "https://demo.example/show-0-1-sezon-1-bolum-izle-full-tek-parca/"})
        self.assertEqual((first["last"]["season"], first["last"]["episode"]), (2, 2))
        self.assertEqual(first["warnings"], [])
        self.assertEqual(out["criteria"]["series_inventory_ok"], {"value": 1.0, "min": 1.0, "ok": True})
        self.assertEqual(out["normalize"]["series_without_sources"], 12)   # the cards themselves carry no episode ...
        self.assertEqual(out["criteria"]["series_have_episode_sources"], {"value": 1.0, "min": 0.9, "ok": True})   # ... the inventory does
        self.assertNotIn(sb.SERIES_SOURCES_ERROR, out["errors"])
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual(self.asked[:3], [first["series_url"], middle["series_url"], last["series_url"]])

    def test_the_playable_samples_come_from_the_inventory_one_per_different_series(self):
        out = self.run_config()
        samples = out["playable"]["samples"]
        self.assertEqual([s["kind"] for s in samples], ["episode"] * 3)
        self.assertEqual([s["key"] for s in samples], ["show-0", "show-6", "show-11"])   # three DIFFERENT series, not two episodes of one
        self.assertEqual([s["locator"] for s in samples],
                         [f"https://demo.example/show-{n}-1-sezon-3-bolum-izle-full-tek-parca/" for n in (0, 6, 11)])   # a middle episode each
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 3))
        self.assertEqual(len(set(self.asked)), len(self.asked))   # three DIFFERENT pages + the three series pages, none twice

    def test_inventory_picks(self):
        def inv(key, n):
            return {"key": key, "url": f"https://x/{key}", "episodes": [{"season": 1, "episode": e, "url": f"https://x/{key}/{e}"} for e in range(1, n + 1)]}

        one = sb._inventory_picks([inv("a", 7)])
        self.assertEqual([p["locator"] for p in one], ["https://x/a/4", "https://x/a/1", "https://x/a/7"])   # one series: middle, then first / last
        self.assertEqual({p["kind"] for p in one}, {"episode"})
        two = sb._inventory_picks([inv("a", 7), inv("b", 4)])
        self.assertEqual([(p["key"], p["locator"]) for p in two], [("a", "https://x/a/4"), ("b", "https://x/b/3"), ("a", "https://x/a/1")])
        three = sb._inventory_picks([inv("a", 7), inv("b", 4), inv("c", 9)])
        self.assertEqual([p["key"] for p in three], ["a", "b", "c"])   # three series: three different series
        self.assertEqual(len(sb._inventory_picks([inv(k, 5) for k in "abcde"], 5)), 5)   # spares (blocked samples are replaced)
        ok_page = inv("a", 7)
        ok_page["ok_page"] = "https://x/a/6"   # the page the availability check already fetched is the sample
        self.assertEqual(sb._inventory_picks([ok_page])[0]["locator"], "https://x/a/6")
        self.assertEqual(len(sb._inventory_picks([inv("a", 2)])), 2)   # fewer episodes than samples: no repeats
        self.assertEqual(sb._inventory_picks([inv("a", 0)]), [])
        self.assertEqual(sb._inventory_picks(None), [])

    def test_without_series_page_the_hint_says_what_to_add(self):
        out = self.run_config(swap_normalize(SERIES_CARD_NORMALIZE))
        self.assertEqual(out["series"], {"checked": 0, "with_episodes": 0, "skipped": 0, "samples": [], "hint": sb.SERIES_HINT})
        self.assertEqual(sb.SERIES_HINT, "cards link to series pages: add series_page (see references/series-page.md)")
        self.assertNotIn("series_inventory_ok", out["criteria"])   # only with a series_page
        self.assertFalse(out["criteria"]["series_have_episode_sources"]["ok"])
        self.assertIn(sb.SERIES_SOURCES_ERROR, out["errors"])
        self.assertIn("series_page", sb.SERIES_SOURCES_ERROR)
        self.assertFalse(out["passed"])

    def test_an_invalid_series_page_is_an_error_and_nothing_is_read(self):
        no_selector = SERIES_PAGE_BLOCK.replace('  row_selector: "ul.episodes li"\n', "")
        out = self.run_config(series_yaml(no_selector + "  tab_selector: x\n"))
        self.assertTrue(any(e.startswith("series_page: row_selector: required") for e in out["errors"]), out["errors"])
        self.assertTrue(any(e == "series_page: unknown key 'tab_selector'" for e in out["errors"]), out["errors"])
        self.assertEqual(out["series"]["samples"], [])
        self.assertEqual(out["criteria"]["series_inventory_ok"], {"value": 0.0, "min": 1.0, "ok": False})
        self.assertFalse(out["criteria"]["config_errors"]["ok"])
        self.assertFalse(out["passed"])

    def test_a_series_page_that_gives_no_episode_fails_with_a_reason(self):
        self.series_html = lambda slug: "<html><body><p>nothing here</p></body></html>"
        out = self.run_config()
        self.assertEqual((out["series"]["checked"], out["series"]["with_episodes"]), (3, 0))
        self.assertEqual(out["series"]["samples"][0]["episodes"], 0)
        self.assertEqual(out["criteria"]["series_inventory_ok"], {"value": 0.0, "min": 1.0, "ok": False})
        self.assertFalse(out["criteria"]["series_have_episode_sources"]["ok"])
        self.assertIn(sb.SERIES_SOURCES_ERROR, out["errors"])
        self.assertTrue(any("series_page: no episode found on https://demo.example/diziler/show-0-izle/" in w for w in out["warnings"]),
                        out["warnings"])
        self.assertFalse(out["passed"])

    def test_a_drifted_row_selector_falls_back_to_the_links_and_warns(self):
        self.series_html = lambda slug: series_page_html(slug, row_class="eps")   # the markup moved: ul.episodes is gone
        out = self.run_config()
        first = out["series"]["samples"][0]
        self.assertEqual((first["episodes"], first["structured"]), (5, False))   # the link scan still finds them (own series only)
        self.assertEqual(out["criteria"]["series_inventory_ok"]["ok"], True)
        self.assertTrue(any("structured=false" in w and "fix row_selector" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(first["warnings"])

    def test_a_site_without_seasons_uses_default_season(self):
        self.series_html = lambda slug: series_page_html(slug, seasons=((1, 5),), seasonless=True)
        out = self.run_config(series_yaml(SEASONLESS_PAGE_BLOCK))
        first = out["series"]["samples"][0]
        self.assertEqual((first["episodes"], first["seasons"], first["structured"]), (5, 1, True))
        self.assertEqual((first["first"]["season"], first["first"]["episode"]), (1, 1))
        self.assertTrue(out["criteria"]["series_inventory_ok"]["ok"])

    def test_a_series_page_that_cannot_be_fetched_is_a_failed_sample(self):
        def boom(cfg, url, *, wait_for=""):
            raise fetch.FetchError("http_404")

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=boom), vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": series_yaml(), "page_id": self.list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        self.assertEqual((out["series"]["checked"], out["series"]["with_episodes"]), (3, 0))
        self.assertRegex(out["series"]["samples"][0]["error"], r"^page: .*http_404")
        self.assertTrue(any("series page not available" in w for w in out["warnings"]), out["warnings"])
        self.assertFalse(out["criteria"]["series_inventory_ok"]["ok"])

    def test_private_series_pages_are_refused_before_any_request(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]), patch.object(fetch, "page_bundle") as m, \
                vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": series_yaml(), "page_id": self.list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        m.assert_not_called()
        self.assertIn("non-public", out["series"]["samples"][0]["error"])
        self.assertEqual(out["series"]["with_episodes"], 0)

    def test_series_pages_that_do_not_fit_the_call_are_skipped_and_not_counted(self):
        cfg = sb._draft_cfg(sb._load_yaml(series_yaml())[0])
        items = [{"title": f"Show {i}", "detail_url": f"/diziler/show-{i}-izle/"} for i in range(12)]
        warnings = []
        with patch.object(fetch, "page_bundle", side_effect=AssertionError("no fetch")):
            block, inventories = sb._series_stage(cfg, items, [], time.monotonic() + sb.PLAYABLE_MIN_LEFT - 1, warnings)
        self.assertEqual((block["checked"], block["with_episodes"], block["skipped"]), (0, 0, 3))
        self.assertTrue(all(s["skipped"] for s in block["samples"]))
        self.assertEqual(inventories, [])
        self.assertTrue(any("3 series page(s) not checked" in w for w in warnings), warnings)
        self.assertEqual(sb._series_criteria(block, True)["series_inventory_ok"], {"value": 0.0, "min": 1.0, "ok": False})
        self.assertEqual(sb._series_criteria(block, False), {})

    def test_films_have_no_series_block_and_a_series_page_without_series_items_fails(self):
        film = DRAFT_YAML
        with public_dns(), any_page(), vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": film, "page_id": self.page(list_html(12)), "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        self.assertIsNone(out["series"])
        self.assertNotIn("series_inventory_ok", out["criteria"])
        self.assertTrue(out["passed"], out["criteria"])
        with public_dns(), any_page(), vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": film.replace("providers: [vidmolly]", "providers: [vidmolly]\n" + SERIES_PAGE_BLOCK),
                                             "page_id": self.page(list_html(12)), "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        self.assertEqual(out["series"]["samples"], [])
        self.assertTrue(any("no series item to check" in w for w in out["warnings"]), out["warnings"])
        self.assertFalse(out["criteria"]["series_inventory_ok"]["ok"])

    def test_without_the_playable_flag_no_series_page_is_read(self):
        with public_dns(), patch.object(fetch, "page_bundle") as m:
            out = self.post("/test_config", {"yaml_text": series_yaml(), "page_id": self.list_id,
                                             "detail_page_id": self.detail_id}).json()
        m.assert_not_called()
        self.assertNotIn("series", out)
        self.assertNotIn("series_inventory_ok", out["criteria"])

    def test_submit_always_checks_the_inventory_and_reports_it(self):
        def fake(cfg, url, *, wait_for=""):
            found = re.search(r"/diziler/(.+?)-izle", url)
            return bundle(series_page_html(found.group(1)) if found else DETAIL_HTML)

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": series_yaml(), "site_id_suggestion": "demo",
                                        "notes": "", "page_id": self.list_id, "detail_page_id": self.detail_id})
        body = got.json()
        self.assertTrue(body["passed"], body["criteria"])
        self.assertEqual(body["series"], {"checked": 3, "with_episodes": 3, "skipped": 0})
        self.assertIn("series_inventory_ok", body["criteria"])
        report = onboard_store.get_draft(self.draft["id"])["report"]
        self.assertEqual(report["series"]["samples"][0]["episodes"], 5)

    # --- outline_page: episode_links ----------------------------------------------------------------------------------

    def test_link_shapes(self):
        shape = sb._link_shape
        self.assertEqual(shape("/lost-1-sezon-2-bolum-izle/"), "/<slug>-N-sezon-N-bolum-izle/")
        self.assertEqual(shape("/dizi-adi-1-sezon-12-bolum-izle-full-tek-parca/"), "/<slug>-N-sezon-N-bolum-izle-full-tek-parca/")
        self.assertEqual(shape("/diziler/dizi-adi/sezon-2/bolum-5"), "/diziler/dizi-adi/sezon-N/bolum-N")   # keyword before the number
        self.assertEqual(shape("/dizi/some-show-12/"), "/dizi/<slug>-N/")
        self.assertEqual(shape("/page/2/"), "/page/N/")
        self.assertEqual(shape("/about/"), "/about/")

    def test_outline_lists_the_episode_link_groups_of_a_series_page(self):
        page_id = self.page(series_page_html("alpha-show"), "https://demo.example/diziler/alpha-show-izle/")
        got = self.post("/outline", {"page_id": page_id})
        self.assertEqual(got.status_code, 200, got.text)
        groups = got.json()["episode_links"]
        # the "similar series" links (beta-1-sezon-N-...) have the same shape: the bigger group of the shape speaks for it
        self.assertEqual(groups, [{"selector": "li.row a", "count": 5, "shape": "/<slug>-N-sezon-N-bolum-izle-full-tek-parca/",
                                   "sample_hrefs": ["/alpha-show-1-sezon-1-bolum-izle-full-tek-parca/",
                                                    "/alpha-show-1-sezon-2-bolum-izle-full-tek-parca/",
                                                    "/alpha-show-1-sezon-3-bolum-izle-full-tek-parca/"]}])

    def test_outline_orders_groups_by_size_and_caps_the_list(self):
        def block(slug, n, word):
            return "".join(f'<a href="/{slug}-{i}-{word}/">x {i}</a>' for i in range(1, n + 1))

        html = ("<html><body>" + block("aa", 4, "bolum-izle") + block("bb", 9, "episode-watch") + block("cc", 6, "season-play")
                + "".join(f'<a href="/page/{i}/">{i}</a>' for i in range(2, 9))   # pagination: numbered, but not an episode
                + "".join(f'<a href="/x{i}-yy/">Dizi {i}. Bolum</a>' for i in range(1, 5))   # the link TEXT says episode
                + "</body></html>")
        groups = self.post("/outline", {"page_id": self.page(html)}).json()["episode_links"]
        self.assertEqual([g["shape"] for g in groups],
                         ["/<slug>-N-episode-watch/", "/<slug>-N-season-play/", "/<slug>-N-bolum-izle/", "/xN-yy/"])
        self.assertEqual([g["count"] for g in groups], [9, 6, 4, 4])
        self.assertTrue(all("selector_matches" in g for g in groups))   # no fence: "a" matches every link of the page

    def test_outline_without_episode_links_has_an_empty_list(self):
        got = self.post("/outline", {"page_id": self.page(list_html(12))})
        self.assertEqual(got.json()["episode_links"], [])

    def test_the_outline_selector_is_checked_against_the_page(self):
        page_id = self.page(series_page_html("show-0"), "https://demo.example/diziler/show-0-izle/")
        group = self.post("/outline", {"page_id": page_id}).json()["episode_links"][0]
        found = sb.HTMLParser(series_page_html("show-0")).css(group["selector"])
        self.assertEqual(len(found), group["count"])


class SubmitTest(SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")

    def submit(self, yaml_text=DRAFT_YAML, **extra):
        body = {"draft_id": self.draft["id"], "yaml_text": yaml_text, "site_id_suggestion": "demo", "notes": "looks fine",
                "page_id": self.list_id, "detail_page_id": self.detail_id, **extra}
        with public_dns(), any_page(), vidmolly_resolves():   # submit always follows a few titles to a stream
            return self.post("/submit", body)

    def test_submit_writes_the_draft_as_ready(self):
        got = self.submit()
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["draft_id"], body["status"], body["passed"], body["errors"]), (self.draft["id"], "ready", True, []))
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual((draft["status"], draft["yaml_text"], draft["site_id_suggestion"]), ("ready", DRAFT_YAML, "demo"))
        self.assertIsNone(draft["error"])
        self.assertTrue(draft["report"]["passed"])
        self.assertEqual(draft["report"]["notes"], "looks fine")
        self.assertEqual(draft["report"]["list"]["valid_count"], 12)
        self.assertEqual(draft["events"][-1]["type"], "submit")

    def test_failing_criteria_are_still_written_with_passed_false(self):
        got = self.submit(page_id=self.page(list_html(5)), notes="only 5 items")
        self.assertEqual(got.status_code, 200)
        self.assertFalse(got.json()["passed"])
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual(draft["status"], "ready")
        self.assertFalse(draft["report"]["passed"])
        self.assertEqual(draft["report"]["notes"], "only 5 items")

    def test_invalid_yaml_is_still_stored(self):
        got = self.submit("a: [unclosed")
        self.assertEqual((got.status_code, got.json()["passed"]), (200, False))
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual((draft["status"], draft["yaml_text"]), ("ready", "a: [unclosed"))

    def test_bad_or_existing_site_id_suggestion_fails_the_report(self):
        out = self.submit(site_id_suggestion="Bad Name!").json()
        self.assertFalse(out["passed"])
        self.assertTrue(any("site_id_suggestion" in e for e in out["errors"]))
        existing = scfg.list_sites()[0]
        out = self.submit(site_id_suggestion=existing).json()
        self.assertFalse(out["passed"])
        self.assertTrue(any("already exists" in e for e in out["errors"]))

    def test_token_of_another_draft_cannot_submit(self):
        other = onboard_store.create_draft("https://other.example/")
        got = self.submit(draft_id=other["id"])
        self.assertEqual(got.status_code, 403)
        self.assertEqual(onboard_store.get_draft(other["id"])["status"], "running")

    def test_unknown_draft_and_closed_drafts(self):
        ghost = "od_ffffffffffff"
        sb._tokens[self.token] = ghost
        self.assertEqual(self.submit(draft_id=ghost).status_code, 404)
        sb._tokens[self.token] = self.draft["id"]
        onboard_store.update_draft(self.draft["id"], status="cancelled")
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(onboard_store.get_draft(self.draft["id"])["status"], "cancelled")

    def test_submit_without_page_ids_fetches_again(self):
        def fake(cfg, url, *, wait_for=""):
            return bundle(list_html(12) if url.endswith("/filmler") else DETAIL_HTML)

        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake) as m, vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": DRAFT_YAML,
                                        "site_id_suggestion": "demo", "notes": ""})
        self.assertEqual((got.status_code, got.json()["passed"]), (200, True))
        self.assertEqual(m.call_count, 2 + 3)   # list + detail, then the three playback pages of the playable stage

PLAYER_URL = "https://player.example/player/oynat/abc"
PLAYER_PAGE = '<script>jwplayer().setup({file:"https://cdn.example/hls/abc/master.m3u8"});</script>'
PLAYER_DETAIL = ('<html><head><title>Film 1</title></head><body><p class="synopsis">A long synopsis of the film.</p>'
                 f'<iframe id="player" src="{PLAYER_URL}"></iframe></body></html>')
RECIPE_YAML = r"""
name: demo_player
description: "Player pages of player.example: the HLS URL sits in a jwplayer setup."
version: 1
match:
  host_regex: '(^|\.)player\.example$'
  path_regex: '^/player/'
referer: "{page_url}"
extract:
  - regex: 'file\s*:\s*\x22([^\x22]+\.m3u8[^\x22]*)\x22'
"""
RECIPE_DRAFT_YAML = DRAFT_YAML.replace("providers: [vidmolly]", "providers: [demo_player]")


def player_fetch(pages=None):
    """The transport of a provider recipe (``fetch.fetch_impersonated``): canned player pages, 404 for everything else."""
    served = dict(pages if pages is not None else {PLAYER_URL: PLAYER_PAGE})
    asked = []

    def fake(url, *, headers=None, **kw):
        asked.append((url, dict(headers or {})))
        if url in served:
            return served[url]
        raise fetch.FetchError("HTTP 404 for " + url, 404)

    patcher = patch.object(fetch, "fetch_impersonated", side_effect=fake)
    patcher.asked = asked
    return patcher


class ProviderRecipesCase(SandboxCase):
    """Base of the provider-recipe tests: the library directory is a temporary one (the sandbox must never write the real one)."""

    def setUp(self):
        super().setUp()
        self.libdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.libdir.cleanup)
        patcher = patch.object(scfg, "CONFIG_DIR", self.libdir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        recipes._cache["sig"] = None
        self.addCleanup(lambda: recipes._cache.update(sig=None, providers=[]))
        self.addCleanup(self.assert_library_untouched)

    def assert_library_untouched(self):
        self.assertFalse(os.path.exists(scfg.provider_dir()), "the sandbox must never write configs/providers")

    def recipe_item(self, text=RECIPE_YAML, name="demo_player"):
        return {"name": name, "yaml": text}


class TestProviderTest(ProviderRecipesCase):
    def run_provider(self, recipe_yaml=RECIPE_YAML, sample_url=PLAYER_URL, referer="https://demo.example/film/100/film-0", pages=None, **extra):
        body = {"recipe_yaml": recipe_yaml, "sample_url": sample_url, **({"referer": referer} if referer else {}), **extra}
        fake = player_fetch(pages)
        with public_dns(), fake:
            got = self.post("/test_provider", body)
        self.fake_asked = fake.asked
        return got

    def test_a_recipe_is_validated_and_run_on_a_player_url(self):
        got = self.run_provider()
        self.assertEqual(got.status_code, 200, got.text)
        out = got.json()
        self.assertEqual((out["valid"], out["errors"], out["matched"], out["status"]), (True, [], True, "resolved"))
        self.assertEqual(out["streams"], [{"type": "hls", "host": "cdn.example", "quality": "auto"}])
        self.assertEqual((out["name"], out["version"], out["fetch"]), ("demo_player", 1, "http"))
        self.assertEqual(out["hosts"], [r"(^|\.)player\.example$", "^/player/"])
        self.assertEqual([(e["stage"], e["ok"]) for e in out["trace"]], [("player_page.fetch", True), ("player_page.extract", True)])
        self.assertEqual(out["warnings"], [])
        self.assertEqual(self.fake_asked[0][0], PLAYER_URL)
        self.assertEqual(self.fake_asked[0][1]["Referer"], "https://demo.example/film/100/film-0")   # {page_url}

    def test_it_needs_the_token_and_localhost(self):
        got = self.client.post("/api/onboard/sandbox/test_provider", json={"recipe_yaml": RECIPE_YAML, "sample_url": PLAYER_URL})
        self.assertEqual(got.status_code, 403)

    def test_an_invalid_recipe_is_reported_not_run(self):
        bad = RECIPE_YAML.replace("regex: 'file", "regex: '(file")   # an unbalanced group
        got = self.run_provider(bad)
        out = got.json()
        self.assertEqual((got.status_code, out["valid"], out["status"], out["streams"]), (200, False, "invalid", []))
        self.assertTrue(any("extract[0]" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.fake_asked, [])
        out = self.run_provider("a: [unclosed").json()
        self.assertEqual((out["valid"], out["status"]), (False, "invalid"))
        self.assertTrue(out["errors"][0].startswith("yaml:"))
        out = self.run_provider(RECIPE_YAML.replace("name: demo_player", "name: vidmolly")).json()
        self.assertTrue(any("code provider" in e for e in out["errors"]), out["errors"])

    def test_a_sample_the_match_does_not_cover_is_no_match(self):
        out = self.run_provider(sample_url="https://other.example/player/oynat/abc").json()
        self.assertEqual((out["valid"], out["matched"], out["status"]), (True, False, "no_match"))
        self.assertTrue(any("match" in e and "other.example" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.fake_asked, [])   # never fetched
        out = self.run_provider(sample_url="https://player.example/embed/abc").json()   # right host, wrong path
        self.assertEqual((out["matched"], out["status"]), (False, "no_match"))

    def test_no_stream_says_which_stage_failed(self):
        out = self.run_provider(pages={PLAYER_URL: "<html>no media here</html>"}).json()
        self.assertEqual((out["valid"], out["matched"], out["status"], out["streams"]), (True, True, "no_stream", []))
        self.assertIn("player_page.extract", out["error"])
        self.assertIn("no extract rule found a media URL", out["error"])
        self.assertFalse(out["trace"][-1]["ok"])
        out = self.run_provider(pages={}).json()   # 404 on the player page
        self.assertEqual(out["status"], "no_stream")
        self.assertIn("player_page.fetch", out["error"])
        self.assertIn("404", out["error"])

    def test_urls_are_checked_before_anything_is_fetched(self):
        got = self.run_provider(sample_url="http://10.0.0.5/player/x")
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "url_rejected"))
        got = self.run_provider(referer="javascript:alert(1)")
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "bad_referer"))
        self.assertEqual(self.fake_asked, [])

    def test_missing_referer_and_existing_name_are_warnings(self):
        out = self.run_provider(referer="").json()
        self.assertEqual(out["status"], "resolved")
        self.assertTrue(any("referer" in w for w in out["warnings"]), out["warnings"])
        self.assertEqual(self.fake_asked[0][1]["Referer"], "https://player.example/")   # the player's own origin
        scfg.save_recipe("demo_player", recipes.parse(RECIPE_YAML)[0])
        out = self.run_provider().json()
        self.assertTrue(any("already exists in the library" in w for w in out["warnings"]), out["warnings"])
        shutil.rmtree(scfg.provider_dir())   # (the base class checks the library is never written by the sandbox itself)

    def test_a_declared_type_that_the_url_contradicts_is_a_warning(self):
        text = RECIPE_YAML.replace(r"\.m3u8[^\x22]*)\x22'", r"\.m3u8[^\x22]*)\x22'" + "\n    type: mp4")
        out = self.run_provider(text).json()
        self.assertEqual(out["streams"][0]["type"], "hls")
        self.assertTrue(any("declared mp4" in w and "extension wins" in w for w in out["warnings"]), out["warnings"])

    def test_a_crashing_transport_does_not_crash_the_call(self):
        with public_dns(), patch.object(fetch, "fetch_impersonated", side_effect=RuntimeError("boom")):
            got = self.post("/test_provider", {"recipe_yaml": RECIPE_YAML, "sample_url": PLAYER_URL, "referer": "https://demo.example/x"})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["status"], "no_stream")


class RecipeCatalogTest(ProviderRecipesCase):
    def test_resolvers_lists_code_providers_and_recipes_with_kind(self):
        scfg.save_recipe("lib_player", {**recipes.parse(RECIPE_YAML)[0], "name": "lib_player"})
        got = self.client.get("/api/onboard/sandbox/resolvers", headers=self.headers)
        self.assertEqual(got.status_code, 200)
        providers = got.json()["providers"]
        self.assertEqual([(p["name"], p["kind"]) for p in providers], [("vidmolly", "code"), ("okru", "code"), ("lib_player", "recipe")])
        entry = providers[2]
        self.assertEqual((entry["version"], entry["hosts"]), (1, [r"(^|\.)player\.example$", "^/player/"]))
        self.assertTrue(entry["description"])
        shutil.rmtree(scfg.provider_dir())


class DraftRecipesTest(ProviderRecipesCase):
    """The draft's own recipes join the providers IN MEMORY of test_config / test_resolvers / submit; nothing is written."""

    def setUp(self):
        super().setUp()
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(PLAYER_DETAIL, "https://demo.example/film/100/film-0")

    def config(self, yaml_text=RECIPE_DRAFT_YAML, **extra):
        body = {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id, "playable": True, **extra}
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_without_the_recipe_the_provider_name_is_unknown(self):
        out = self.config()
        self.assertTrue(any("unknown provider 'demo_player'" in e for e in out["errors"]), out["errors"])
        self.assertNotIn("provider_recipes", out)

    def test_test_config_runs_the_playable_chain_through_the_recipe(self):
        out = self.config(provider_recipes=[self.recipe_item()])
        self.assertEqual(out["errors"], [], out["errors"])
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 3))
        self.assertTrue(all(s["providers"] == ["demo_player"] for s in out["playable"]["samples"]), out["playable"]["samples"])
        self.assertTrue(out["passed"], out["criteria"])
        [entry] = out["provider_recipes"]
        self.assertEqual((entry["name"], entry["valid"], entry["used"], entry["version"]), ("demo_player", True, 3, 1))
        self.assertEqual(entry["streams"], [{"type": "hls", "host": "cdn.example", "quality": "auto"}])
        self.assertEqual(entry["hosts"], [r"(^|\.)player\.example$", "^/player/"])
        self.assertEqual(out["warnings"], [sb.NO_SEARCH_WARNING])   # the playable run also looks for a search: block (none here)

    def test_a_recipe_that_nothing_resolved_through_is_flagged(self):
        with_vidmolly = self.config(RECIPE_DRAFT_YAML.replace("providers: [demo_player]", "providers: [vidmolly, demo_player]"),
                                    provider_recipes=[self.recipe_item(RECIPE_YAML.replace(r"player\.example", r"other\.example"))])
        # the recipe matches another host: the player is not owned by it, the playable stage finds no provider for the URL
        self.assertFalse(with_vidmolly["passed"])
        [entry] = with_vidmolly["provider_recipes"]
        self.assertEqual((entry["valid"], entry["used"]), (True, 0))
        self.assertTrue(any("no playable sample resolved through the recipe 'demo_player'" in w for w in with_vidmolly["warnings"]),
                        with_vidmolly["warnings"])

    def test_an_invalid_recipe_is_an_error_and_not_used(self):
        out = self.config(provider_recipes=[self.recipe_item(RECIPE_YAML.replace("version: 1", "version: 0"))])
        self.assertTrue(any(e.startswith("provider_recipes[0] demo_player: version:") for e in out["errors"]), out["errors"])
        self.assertFalse(out["passed"])
        self.assertEqual(out["criteria"]["config_errors"]["ok"], False)
        [entry] = out["provider_recipes"]
        self.assertEqual((entry["valid"], entry["used"]), (False, 0))
        self.assertTrue(entry["errors"])
        self.assertTrue(any("unknown provider" in e for e in out["errors"]))   # the recipe never joined the registry

    def test_name_problems(self):
        item = self.recipe_item()
        cases = {
            "code provider": [self.recipe_item(RECIPE_YAML.replace("demo_player", "okru"), "okru")],
            "the yaml says": [self.recipe_item(RECIPE_YAML, "other_name")],
        }
        for needle, items in cases.items():
            with self.subTest(needle=needle):
                out = self.config(provider_recipes=items)
                self.assertTrue(any(needle in e for e in out["errors"]), (needle, out["errors"]))
        self.assertTrue(any("listed twice" in e for e in sb._prepare_recipes([item, item]).errors))   # (the calls merge by name)
        scfg.save_recipe("demo_player", recipes.parse(RECIPE_YAML)[0])   # a recipe of that name is already in the library
        out = self.config(provider_recipes=[item])
        self.assertTrue(any("already exists in the library" in e for e in out["errors"]), out["errors"])
        shutil.rmtree(scfg.provider_dir())

    def test_at_most_three_recipes(self):
        items = [self.recipe_item(RECIPE_YAML.replace("demo_player", f"demo_player{i}"), f"demo_player{i}") for i in range(4)]
        got = self.post("/test_config", {"yaml_text": RECIPE_DRAFT_YAML, "provider_recipes": items})
        self.assertEqual(got.status_code, 422)   # the request model refuses it
        self.assertEqual(sb._prepare_recipes(items).errors[0], "provider_recipes: at most 3 recipes per draft (got 4)")

    def test_the_recipes_stored_in_the_draft_join_every_call(self):
        onboard_store.update_draft(self.draft["id"], provider_recipes=[self.recipe_item()])
        out = self.config()   # not passed in this call
        self.assertEqual(out["errors"], [], out["errors"])
        self.assertEqual(out["provider_recipes"][0]["used"], 3)
        other = RECIPE_YAML.replace("demo_player", "second_player").replace(r"player\.example", r"second\.example")
        out = self.config(provider_recipes=[self.recipe_item(other, "second_player")])
        self.assertEqual([e["name"] for e in out["provider_recipes"]], ["demo_player", "second_player"])
        # a recipe of the same name in the call replaces the stored one
        out = self.config(provider_recipes=[self.recipe_item(RECIPE_YAML.replace("m3u8", "nothing"))])
        self.assertEqual(out["provider_recipes"][0]["used"], 0)

    def test_test_resolvers_resolves_through_the_recipe(self):
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch():
            got = self.post("/test_resolvers", {"yaml_text": RECIPE_DRAFT_YAML, "detail_url": "https://demo.example/film/100/film-0",
                                                "detail_urls": [], "provider_recipes": [self.recipe_item()]})
        out = got.json()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((out["status"], out["errors"], out["resolved"][0]["provider"]), ("resolved", [], "demo_player"))
        self.assertEqual(out["pages"][0]["streams"], [{"type": "hls", "host": "cdn.example", "quality": "auto"}])
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch():
            got = self.post("/test_resolvers", {"yaml_text": RECIPE_DRAFT_YAML, "detail_url": "https://demo.example/film/100/film-0",
                                                "detail_urls": []})
        self.assertTrue(any("unknown provider" in e for e in got.json()["errors"]))
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch():   # a bad recipe stops the call before any page is fetched
            got = self.post("/test_resolvers", {"yaml_text": RECIPE_DRAFT_YAML, "detail_url": "https://demo.example/film/100/film-0",
                                                "provider_recipes": [self.recipe_item("a: [")]})
        out = got.json()
        self.assertEqual(out["status"], "error")
        self.assertTrue(any(e.startswith("provider_recipes[0] demo_player: yaml:") for e in out["errors"]), out["errors"])

    def test_a_recipe_does_not_take_over_hosts_the_site_does_not_allow(self):
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch() as fake:
            got = self.post("/test_resolvers", {"yaml_text": RECIPE_DRAFT_YAML.replace("providers: [demo_player]", "providers: [vidmolly]"),
                                                "detail_url": "https://demo.example/film/100/film-0", "detail_urls": [],
                                                "provider_recipes": [self.recipe_item()]})
        out = got.json()
        self.assertNotEqual(out["status"], "resolved")   # providers: [vidmolly] only; the recipe's host is "known but not allowed"
        self.assertEqual([url for url, _h in fake.asked], [])


class SubmitRecipesTest(ProviderRecipesCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(PLAYER_DETAIL, "https://demo.example/film/100/film-0")

    def submit(self, yaml_text=RECIPE_DRAFT_YAML, **extra):
        body = {"draft_id": self.draft["id"], "yaml_text": yaml_text, "site_id_suggestion": "demo", "notes": "n",
                "page_id": self.list_id, "detail_page_id": self.detail_id, **extra}
        with public_dns(), any_page(PLAYER_DETAIL), player_fetch():
            return self.post("/submit", body)

    def test_submit_stores_the_recipes_and_reports_them(self):
        got = self.submit(provider_recipes=[{"name": "demo_player", "yaml": RECIPE_YAML}])
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["passed"], body["errors"]), (True, []), body)
        self.assertEqual(body["provider_recipes"], [{"name": "demo_player", "valid": True, "used": 3, "errors": []}])
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual(draft["provider_recipes"], [{"name": "demo_player", "yaml": RECIPE_YAML}])
        [entry] = draft["report"]["provider_recipes"]
        self.assertEqual((entry["name"], entry["valid"], entry["used"], entry["version"]), ("demo_player", True, 3, 1))
        self.assertEqual(entry["description"], "Player pages of player.example: the HLS URL sits in a jwplayer setup.")
        self.assertEqual(entry["streams"], [{"type": "hls", "host": "cdn.example", "quality": "auto"}])
        self.assertEqual(draft["status"], "ready")

    def test_a_site_without_recipes_has_no_recipe_report(self):
        with public_dns(), any_page(), vidmolly_resolves():
            got = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": DRAFT_YAML, "site_id_suggestion": "demo",
                                        "page_id": self.list_id, "detail_page_id": self.detail_id})
        self.assertEqual(got.json()["provider_recipes"], [])
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertNotIn("provider_recipes", draft["report"])
        self.assertEqual(draft["provider_recipes"], [])

    def test_an_invalid_recipe_fails_the_report_but_is_still_stored(self):
        bad = RECIPE_YAML.replace("version: 1", "version: 0")
        got = self.submit(provider_recipes=[{"name": "demo_player", "yaml": bad}])
        body = got.json()
        self.assertEqual((got.status_code, body["passed"]), (200, False))
        self.assertTrue(any(e.startswith("provider_recipes[0] demo_player: version:") for e in body["errors"]), body["errors"])
        self.assertEqual(body["provider_recipes"][0]["valid"], False)
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual(draft["status"], "ready")
        self.assertEqual(draft["report"]["criteria"]["config_errors"]["ok"], False)

    def test_resubmitting_without_the_argument_keeps_the_recipes_and_a_list_replaces_them(self):
        self.submit(provider_recipes=[{"name": "demo_player", "yaml": RECIPE_YAML}])
        again = self.submit()
        self.assertEqual((again.json()["passed"], again.json()["provider_recipes"][0]["name"]), (True, "demo_player"))
        self.assertEqual(len(onboard_store.get_draft(self.draft["id"])["provider_recipes"]), 1)
        cleared = self.submit(yaml_text=DRAFT_YAML, provider_recipes=[])
        self.assertEqual(cleared.json()["provider_recipes"], [])
        self.assertEqual(onboard_store.get_draft(self.draft["id"])["provider_recipes"], [])

    def test_a_name_that_is_taken_fails_the_report(self):
        scfg.save_recipe("demo_player", recipes.parse(RECIPE_YAML)[0])
        body = self.submit(provider_recipes=[{"name": "demo_player", "yaml": RECIPE_YAML}]).json()
        self.assertFalse(body["passed"])
        self.assertTrue(any("already exists in the library" in e for e in body["errors"]), body["errors"])
        shutil.rmtree(scfg.provider_dir())

    def test_more_than_three_recipes_are_refused_by_the_request_model(self):
        items = [{"name": f"p{i}x", "yaml": RECIPE_YAML} for i in range(4)]
        self.assertEqual(self.submit(provider_recipes=items).status_code, 422)
        self.assertEqual(onboard_store.get_draft(self.draft["id"])["status"], "running")

    def test_the_site_yaml_provider_names_must_be_known(self):
        body = self.submit().json()   # providers: [demo_player] but no recipe handed in
        self.assertFalse(body["passed"])
        self.assertTrue(any("unknown provider 'demo_player'" in e for e in body["errors"]), body["errors"])


# --- live search (``search:``): test_search + the search stage of test_config(playable) / submit --------------------------

SEARCH_BLOCK = """
search:
  url: "/?s={query}"
  row_selector: "div.res"
  fields:
    title: {selector: "h3"}
    detail_url: {selector: "a", attr: href}
    poster_url: {selector: "img", attr: src}
    year: {selector: "span.y", regex: '(\\d{4})', cast: int}
"""
SEARCH_YAML = DRAFT_YAML + SEARCH_BLOCK


def result_row(i, path=None, poster=True):
    return (f'<div class="res"><a href="{path or f"/film/{100 + i}/film-{i}"}">'
            + (f'<img src="/p/{i}.jpg">' if poster else "") + f'<h3>Film {i}</h3></a><span class="y">{2000 + i}</span></div>')


def results_html(*ids, **kw):
    return "<html><body>" + "".join(result_row(i, **kw) for i in ids) + "</body></html>"


def search_transport(html, status=200):
    """``fetch.impersonated_get`` of the generic search engine: one canned results page; every request is recorded."""
    asked = []

    def fake(url, *, headers=None, **kw):
        asked.append(url)
        if isinstance(html, Exception):
            raise html
        return fetch.ImpersonatedPage(html, url, status)

    patcher = patch.object(fetch, "impersonated_get", side_effect=fake)
    patcher.asked = asked
    return patcher


class SearchCase(SandboxCase):
    def setUp(self):
        super().setUp()
        from app.scraper import search_generic
        search_generic.clear_cache()
        self.addCleanup(search_generic.clear_cache)
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")

    def run_search(self, html=None, yaml_text=SEARCH_YAML, **extra):
        transport = search_transport(html if html is not None else results_html(0, 1))
        with public_dns(), transport:
            got = self.post("/test_search", {"yaml_text": yaml_text, **extra})
        self.assertEqual(got.status_code, 200, got.text)
        self.transport = transport
        return got.json()


class SearchToolTest(SearchCase):
    def test_the_block_runs_on_the_title_of_the_first_list_item_and_finds_it(self):
        out = self.run_search(page_id=self.list_id)
        self.assertEqual((out["valid"], out["errors"], out["warnings"]), (True, [], []))
        self.assertEqual((out["query"], out["count"], out["found_known"], out["normalize_ok_ratio"]), ("Film 0", 2, True, 1.0))
        self.assertEqual(out["known"], {"title": "Film 0", "detail_url": "/film/100/film-0"})
        self.assertEqual(out["samples"][0], {"title": "Film 0", "detail_url": "https://demo.example/film/100/film-0",
                                             "poster_url": "https://demo.example/p/0.jpg", "year": 2000})
        self.assertEqual(set(out["samples"][1]), {"title", "detail_url", "poster_url", "year"})
        self.assertIsInstance(out["ms"], int)
        self.assertEqual(self.transport.asked, ["https://demo.example/?s=Film+0"])   # ONE query, URL-encoded by the engine

    def test_samples_are_capped_at_five_and_a_missing_poster_is_none(self):
        out = self.run_search(html=results_html(0, 1, 2, 3, 4, 5, 6, poster=False), page_id=self.list_id)
        self.assertEqual((out["count"], len(out["samples"])), (7, 5))
        self.assertEqual({s["poster_url"] for s in out["samples"]}, {None})

    def test_found_known_is_false_when_the_results_are_other_pages(self):
        out = self.run_search(html=results_html(5, 6), page_id=self.list_id)
        self.assertEqual((out["valid"], out["count"], out["found_known"]), (True, 2, False))
        self.assertTrue(any("found_known: false" in w for w in out["warnings"]), out["warnings"])

    def test_the_same_title_under_another_url_shape_is_found_by_its_normalize_key(self):
        html = results_html(0, path="/film/100/another-slug-for-the-same-film")   # list item: /film/100/film-0 (key 100)
        out = self.run_search(html=html, page_id=self.list_id)
        self.assertEqual((out["count"], out["found_known"]), (1, True))
        self.assertEqual(out["normalize_ok_ratio"], 1.0)

    def test_a_trailing_slash_and_www_do_not_matter(self):
        out = self.run_search(html=results_html(0, path="/film/100/film-0/"), page_id=self.list_id)
        self.assertEqual((out["count"], out["found_known"]), (1, True))
        self.assertEqual(sb._url_key("https://demo.example", "/a/b/"), sb._url_key("https://demo.example", "https://www.demo.example/a/b"))

    def test_results_the_normalize_rules_reject_lower_the_ratio(self):
        out = self.run_search(html=results_html(0, 1, path=None).replace('/film/101/', '/serie/101/'), page_id=self.list_id)
        self.assertEqual((out["count"], out["normalize_ok_ratio"]), (2, 0.5))

    def test_a_given_query_is_used_and_judged_against_the_list_item_it_names(self):
        out = self.run_search(html=results_html(3), query="film 3", page_id=self.list_id)
        self.assertEqual((out["query"], out["known"]["title"], out["found_known"]), ("film 3", "Film 3", True))
        self.assertEqual(self.transport.asked, ["https://demo.example/?s=film+3"])

    def test_a_query_that_names_no_list_item_is_not_judged(self):
        out = self.run_search(html=results_html(1), query="something else", page_id=self.list_id)
        self.assertEqual((out["count"], out["found_known"], out["known"]), (1, None, None))
        self.assertTrue(any("not judged" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(out["valid"])

    def test_a_given_query_without_page_id_does_not_fetch_the_list(self):
        with patch.object(fetch, "page_bundle") as pages:
            out = self.run_search(html=results_html(1), query="film 1")
        pages.assert_not_called()
        self.assertEqual((out["valid"], out["count"], out["found_known"]), (True, 1, None))

    def test_without_a_query_or_page_id_the_list_page_is_fetched(self):
        with patch.object(fetch, "page_bundle", side_effect=lambda cfg, url, **kw: bundle(list_html(12))) as pages:
            out = self.run_search(html=results_html(0))
        self.assertEqual(pages.call_count, 1)
        self.assertEqual((out["valid"], out["query"], out["found_known"]), (True, "Film 0", True))

    def test_a_stored_detail_page_is_a_known_title_when_the_list_gives_none(self):
        detail = self.page('<html><head><meta property="og:title" content="Film 0 izle"></head><body><h1>x</h1></body></html>',
                           "https://demo.example/film/100/film-0")
        broken_list = self.page("<html><body><p>nothing</p></body></html>")
        out = self.run_search(html=results_html(0), page_id=broken_list, detail_page_id=detail)
        self.assertEqual((out["valid"], out["query"], out["found_known"]), (True, "Film 0", True))   # the site litter (" izle") is cut from the query (D5)

    def test_results_of_another_host_are_not_counted(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(validate_spec=lambda spec, base=None: [],
                               search=lambda cfg, q, n: [{"title": "Film 0", "detail_url": "https://other.example/film/100/film-0"},
                                                         {"title": "Film 1", "detail_url": "https://demo.example/film/101/film-1"}])
        with patch.object(sb, "_search_mod", return_value=fake):
            out = self.post("/test_search", {"yaml_text": SEARCH_YAML, "page_id": self.list_id}).json()
        self.assertEqual((out["count"], out["found_known"]), (1, False))

    def test_an_edited_block_is_never_answered_from_the_engine_cache(self):
        self.assertEqual(self.run_search(html=results_html(0, 1), page_id=self.list_id)["count"], 2)
        wrong = SEARCH_YAML.replace('row_selector: "div.res"', 'row_selector: "div.nothing"')
        out = self.run_search(html=results_html(0, 1), yaml_text=wrong, page_id=self.list_id)
        self.assertEqual((out["count"], out["found_known"]), (0, False))

    def test_nothing_is_written(self):
        before = onboard_store.get_draft(self.draft["id"])
        self.run_search(page_id=self.list_id)
        self.assertEqual(onboard_store.get_draft(self.draft["id"])["updated_at"], before["updated_at"])   # configs: SandboxCase cleanup


class SearchToolErrorsTest(SearchCase):
    def test_no_search_block(self):
        out = self.run_search(yaml_text=DRAFT_YAML, page_id=self.list_id)
        self.assertEqual((out["valid"], out["count"]), (False, 0))
        self.assertTrue(any("no `search:` block" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.transport.asked, [])

    def test_an_invalid_block_names_the_problem_and_nothing_is_requested(self):
        broken = DRAFT_YAML + "search:\n  row_selector: 'div.res'\n  fields: {title: {selector: h3}}\n"
        out = self.run_search(yaml_text=broken, page_id=self.list_id)
        self.assertFalse(out["valid"])
        self.assertTrue(any(e.startswith("search:") and "url" in e for e in out["errors"]), out["errors"])
        self.assertTrue(any("fields.detail_url" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.transport.asked, [])
        wrong_host = SEARCH_YAML.replace('url: "/?s={query}"', 'url: "https://evil.example/?s={query}"')
        out = self.run_search(yaml_text=wrong_host, page_id=self.list_id)
        self.assertTrue(any("evil.example" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.transport.asked, [])
        not_a_mapping = DRAFT_YAML + "search: nope\n"
        self.assertTrue(any("mapping" in e for e in self.run_search(yaml_text=not_a_mapping)["errors"]))

    def test_bad_yaml_and_a_missing_base_url(self):
        self.assertFalse(self.run_search(yaml_text="a: [unclosed")["valid"])
        out = self.run_search(yaml_text=SEARCH_YAML.replace("base_url: https://demo.example", "base_url: demo"))
        self.assertTrue(any(e.startswith("base_url") for e in out["errors"]), out["errors"])

    def test_the_query_needs_three_characters(self):
        out = self.run_search(query="ab", page_id=self.list_id)
        self.assertFalse(out["valid"])
        self.assertTrue(any("3 characters" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.transport.asked, [])
        self.assertEqual(self.post("/test_search", {"yaml_text": SEARCH_YAML, "query": "x" * 101}).status_code, 422)

    def test_no_query_and_no_known_title_asks_for_a_query(self):
        out = self.run_search(page_id=self.page("<html><body><p>nothing</p></body></html>"))
        self.assertFalse(out["valid"])
        self.assertTrue(any("pass a query" in e for e in out["errors"]), out["errors"])
        self.assertEqual(self.transport.asked, [])

    def test_a_list_page_that_cannot_be_fetched_is_reported(self):
        with patch.object(fetch, "page_bundle", side_effect=fetch.FetchError("HTTP 503", 503)):
            out = self.run_search()
        self.assertFalse(out["valid"])
        self.assertTrue(any("list page is not available" in e for e in out["errors"]), out["errors"])

    def test_a_failed_request_is_an_error_with_a_short_message(self):
        out = self.run_search(html=fetch.FetchError("HTTP 503 for https://demo.example/", 503), page_id=self.list_id)
        self.assertEqual((out["valid"], out["count"], out["found_known"]), (False, 0, False))
        self.assertTrue(any("failed" in e and "503" in e for e in out["errors"]), out["errors"])
        self.assertLessEqual(len(out["error"]), 300)

    def test_a_site_that_resolves_to_a_private_address_is_never_requested(self):
        transport = search_transport(results_html(0))
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]), transport:
            out = self.post("/test_search", {"yaml_text": SEARCH_YAML, "page_id": self.list_id}).json()
        self.assertFalse(out["valid"])
        self.assertEqual(transport.asked, [])
        self.assertTrue(out["errors"])

    def test_zero_results_is_valid_but_warns(self):
        out = self.run_search(html="<html><body></body></html>", page_id=self.list_id)
        self.assertEqual((out["valid"], out["count"], out["found_known"]), (True, 0, False))
        self.assertTrue(any("no result" in w for w in out["warnings"]), out["warnings"])

    def test_a_slow_call_is_a_504_and_the_endpoint_needs_the_token(self):
        def slow(body, *, deadline):
            time.sleep(1.5)
            return {}
        with patch.object(config, "ONBOARD_TOOL_TIMEOUT", 0.2), patch.object(sb, "_do_test_search", slow):
            got = self.post("/test_search", {"yaml_text": SEARCH_YAML})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (504, "timeout"))
        self.assertEqual(self.client.post("/api/onboard/sandbox/test_search", json={"yaml_text": SEARCH_YAML}).status_code, 403)


class SearchStageTest(SearchCase):
    """``test_config(playable: true)`` and ``submit`` run ONE live query when the yaml has ``search:`` (criterion ``search_ok``)."""

    def config(self, yaml_text=SEARCH_YAML, html=None, playable=True, **extra):
        transport = search_transport(html if html is not None else results_html(0, 1))
        body = {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id, "playable": playable, **extra}
        with public_dns(), any_page(), vidmolly_resolves(), transport:
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        self.transport = transport
        return got.json()

    def test_a_working_search_adds_the_block_and_the_criterion(self):
        out = self.config()
        self.assertEqual(out["errors"], [], out["errors"])
        block = out["search"]
        self.assertEqual((block["query"], block["count"], block["found_known"], block["normalize_ok_ratio"]), ("Film 0", 2, True, 1.0))
        self.assertEqual(out["criteria"]["search_ok"], {"value": 1, "min": 1, "ok": True})
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual(self.transport.asked, ["https://demo.example/?s=Film+0"])   # one query
        self.assertNotIn(sb.NO_SEARCH_WARNING, out["warnings"])

    def test_no_result_or_a_missing_known_title_fails_the_criterion(self):
        for html, why in (("<html><body></body></html>", "no result"), (results_html(7, 8), "known title missing")):
            with self.subTest(why):
                out = self.config(html=html)
                self.assertEqual(out["criteria"]["search_ok"]["ok"], False)
                self.assertFalse(out["passed"])
                self.assertTrue(any(w.startswith("search:") for w in out["warnings"]), out["warnings"])

    def test_a_failed_query_is_an_error_and_fails_the_criterion(self):
        out = self.config(html=fetch.FetchError("HTTP 500", 500))
        self.assertTrue(any(e.startswith("search: query 'Film 0' failed") for e in out["errors"]), out["errors"])
        self.assertEqual((out["criteria"]["search_ok"]["ok"], out["criteria"]["config_errors"]["ok"], out["passed"]), (False, False, False))

    def test_a_yaml_without_search_gets_a_warning_and_no_criterion(self):
        out = self.config(yaml_text=DRAFT_YAML)
        self.assertIn(sb.NO_SEARCH_WARNING, out["warnings"])
        self.assertIn("search block: this site will not be searchable", sb.NO_SEARCH_WARNING)
        self.assertNotIn("search_ok", out["criteria"])
        self.assertIsNone(out["search"])
        self.assertTrue(out["passed"])
        self.assertEqual(self.transport.asked, [])

    def test_an_invalid_block_is_an_error_and_counts_zero(self):
        out = self.config(yaml_text=DRAFT_YAML + "search:\n  url: '/?s=fixed'\n  row_selector: 'div.res'\n  fields: {title: {selector: h3}, detail_url: {selector: a, attr: href}}\n")
        self.assertTrue(any(e.startswith("search:") and "{query}" in e for e in out["errors"]), out["errors"])
        self.assertEqual(out["criteria"]["search_ok"], {"value": 0, "min": 1, "ok": False})
        self.assertIsNone(out["search"])
        self.assertEqual(self.transport.asked, [])

    def test_without_the_playable_flag_there_is_no_search_stage(self):
        out = self.config(playable=False)
        self.assertNotIn("search", out)
        self.assertNotIn("search_ok", out["criteria"])
        self.assertNotIn(sb.NO_SEARCH_WARNING, out["warnings"])
        self.assertEqual(self.transport.asked, [])
        self.assertTrue(out["passed"])

    def test_static_search_errors_show_without_the_flag_too(self):
        out = self.config(yaml_text=DRAFT_YAML + "search:\n  url: '/x'\n", playable=False)
        self.assertTrue(any(e.startswith("search:") for e in out["errors"]), out["errors"])

    def test_no_time_left_skips_the_stage_without_judging(self):
        with patch.object(sb, "SEARCH_NEED", 10_000.0):
            out = self.config()
        self.assertEqual(out["search"], {"skipped": "the call ran out of time"})
        self.assertNotIn("search_ok", out["criteria"])
        self.assertTrue(any(w.startswith("search: not checked") for w in out["warnings"]), out["warnings"])
        self.assertEqual(self.transport.asked, [])

    def test_a_list_without_items_skips_the_stage(self):
        out = self.config(page_id=self.page("<html><body><p>nothing</p></body></html>"))
        self.assertEqual(out["search"], {"skipped": "no list item to take a query from"})
        self.assertNotIn("search_ok", out["criteria"])

    def test_a_repair_run_never_queries_the_live_search(self):
        transport = search_transport(results_html(0))
        body = sb.TestConfigBody(yaml_text=SEARCH_YAML, page_id=self.list_id, detail_page_id=self.detail_id, playable=True)
        with public_dns(), any_page(), vidmolly_resolves(), transport:
            out = sb._do_test_config(body, deadline=time.monotonic() + 100, repair=True)
        self.assertNotIn("search", out)
        self.assertNotIn("search_ok", out["criteria"])
        self.assertEqual(transport.asked, [])

    def test_the_heal_analysis_is_untouched(self):
        transport = search_transport(results_html(0))
        with public_dns(), any_page(), vidmolly_resolves(), transport:
            out = sb._analyze(SEARCH_YAML, self.list_id, self.detail_id, time.monotonic() + 100, playable=True)
        self.assertNotIn("search", out)
        self.assertNotIn("search_ok", out["criteria"])
        self.assertEqual(transport.asked, [])


class SearchSubmitTest(SearchCase):
    def submit(self, yaml_text=SEARCH_YAML, html=None):
        transport = search_transport(html if html is not None else results_html(0, 1))
        body = {"draft_id": self.draft["id"], "yaml_text": yaml_text, "site_id_suggestion": "demo", "notes": "",
                "page_id": self.list_id, "detail_page_id": self.detail_id}
        with public_dns(), any_page(), vidmolly_resolves(), transport:
            got = self.post("/submit", body)
        self.assertEqual(got.status_code, 200, got.text)
        self.transport = transport
        return got.json()

    def test_submit_always_includes_the_search_stage(self):
        out = self.submit()
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual(out["criteria"]["search_ok"]["ok"], True)
        self.assertEqual((out["search"]["query"], out["search"]["count"], out["search"]["found_known"]), ("Film 0", 2, True))
        self.assertEqual(len(self.transport.asked), 1)
        stored = onboard_store.get_draft(self.draft["id"])["report"]
        self.assertEqual(stored["search"]["count"], 2)
        self.assertEqual(stored["criteria"]["search_ok"]["ok"], True)

    def test_a_failing_search_makes_the_draft_not_passed(self):
        out = self.submit(html=results_html(8, 9))
        self.assertFalse(out["passed"])
        self.assertFalse(out["criteria"]["search_ok"]["ok"])
        self.assertEqual(out["search"]["found_known"], False)

    def test_without_a_search_block_the_submit_warns_and_still_passes(self):
        out = self.submit(yaml_text=DRAFT_YAML)
        self.assertTrue(out["passed"])
        self.assertIn(sb.NO_SEARCH_WARNING, out["warnings"])
        self.assertNotIn("search_ok", out["criteria"])
        self.assertNotIn("search", out)
        self.assertEqual(self.transport.asked, [])


class SearchHelpersTest(unittest.TestCase):
    def test_known_item_picks_the_first_usable_title(self):
        items = [{"title": "ab", "detail_url": "/a"}, {"title": "  Dark   Matter ", "detail_url": "/b"}, {"title": "Third", "detail_url": "/c"}]
        self.assertEqual(sb._known_item(items, None), (items[1], "Dark Matter"))
        self.assertEqual(sb._known_item(items, "third"), (items[2], "third"))
        self.assertEqual(sb._known_item(items, "nothing"), (None, "nothing"))
        self.assertEqual(sb._known_item([], None), (None, None))
        self.assertEqual(sb._known_item([{"title": "x" * 300, "detail_url": "/z"}], None)[1], "x" * 100)

    def test_constants(self):
        self.assertEqual((sb.SEARCH_LIMIT, sb.SEARCH_SAMPLES, sb.SEARCH_QUERY_MAX), (20, 5, 100))
        self.assertGreaterEqual(sb.SEARCH_NEED, 10.0)   # the engine's own request limit
        self.assertEqual(sb.TestSearchBody.model_fields["query"].default, None)



class EditModeTest(SandboxCase):
    """An EDIT draft (``mode: "edit"`` + ``edit_site_id``, ``onboard.start(mode="edit")``): ``load_site_config`` reads that site only,
    ``submit`` locks the site id, ``test_config`` / ``submit`` check against the edited site and look its code modules up by its real id."""

    def setUp(self):
        self.cfg_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cfg_tmp.cleanup)
        patcher = patch.object(scfg, "CONFIG_DIR", self.cfg_tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        import yaml
        data = yaml.safe_load(DRAFT_YAML)
        data["site_id"] = "demo"
        scfg.save_new_version("demo", data)
        super().setUp()
        onboard_store.update_draft(self.draft["id"], mode="edit", edit_site_id="demo", edit_from_version=1)
        self.list_id = self.page(list_html(12))
        self.detail_id = self.page(DETAIL_HTML, "https://demo.example/film/100/film-0")

    def get(self, path, token=None):
        return self.client.get("/api/onboard/sandbox" + path, headers={"X-Onboard-Token": token or self.token})

    def submit(self, yaml_text=DRAFT_YAML, **extra):
        body = {"draft_id": self.draft["id"], "yaml_text": yaml_text, "site_id_suggestion": "demo", "notes": "n",
                "page_id": self.list_id, "detail_page_id": self.detail_id, **extra}
        with public_dns(), any_page(), vidmolly_resolves():
            return self.post("/submit", body)

    def test_load_site_config_reads_only_the_edited_site(self):
        got = self.get("/site_config/demo")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["site_id"], got.json()["version"]), ("demo", 1))
        self.assertIn("base_url: https://demo.example", got.json()["yaml_text"])
        data = [("other", 403)] + [(s, 403) for s in scfg.list_sites() if s != "demo"]
        for site, status in data:
            self.assertEqual(self.get("/site_config/" + site).status_code, status, site)
        plain = onboard_store.create_draft("https://demo.example/", "demo")   # a NEW-site draft has no registered site to read
        token = sb.issue_token(plain["id"])
        self.addCleanup(sb.revoke_token, token)
        self.assertEqual(self.get("/site_config/demo", token).status_code, 403)

    def test_submit_locks_the_site_id_suggestion_to_the_edited_site(self):
        out = self.submit(site_id_suggestion="other", yaml_text=DRAFT_YAML + "item_limit: 20\n").json()
        draft = onboard_store.get_draft(self.draft["id"])
        self.assertEqual(draft["site_id_suggestion"], "demo")
        self.assertTrue(any("'other' ignored" in w for w in out["warnings"]), out["warnings"])
        self.assertFalse(any("already exists" in e for e in out["errors"]), out["errors"])   # the site existing is the whole point
        self.assertTrue(out["passed"], out)
        self.assertEqual(draft["report"]["edit"], {"site_id": "demo", "from_version": 1, "changed_keys": ["item_limit"],
                                                   "changed_paths": ["item_limit"]})
        self.assertEqual(self.submit().json()["passed"], True)   # the unchanged yaml: nothing changed
        self.assertEqual(onboard_store.get_draft(self.draft["id"])["report"]["edit"]["changed_keys"], [])

    def test_a_yaml_of_another_site_id_is_an_error(self):
        out = self.submit(yaml_text=DRAFT_YAML + "site_id: other\n").json()
        self.assertFalse(out["passed"])
        self.assertTrue(any("keeps the site id 'demo'" in e for e in out["errors"]), out["errors"])
        self.assertTrue(self.submit(yaml_text=DRAFT_YAML + "site_id: demo\n").json()["passed"])

    def test_the_collection_ids_are_checked_against_the_edited_site(self):
        text = DRAFT_YAML + "collections:\n  - {id: trending_zzz, title: Trendler, path: /filmler, role: trending}\n"
        out = self.submit(yaml_text=text, site_id_suggestion="zzz").json()
        self.assertTrue(any("must be 'trending_demo'" in e for e in out["errors"]), out["errors"])
        with public_dns(), any_page():   # test_config as well: the token says which site this is
            got = self.post("/test_config", {"yaml_text": text, "page_id": self.list_id, "collections": True}).json()
        self.assertTrue(any("must be 'trending_demo'" in e for e in got["errors"]), got["errors"])
        self.assertTrue(self.submit(yaml_text=text.replace("trending_zzz", "trending_demo"), site_id_suggestion="demo").json()["passed"])

    def test_the_site_code_modules_are_looked_up_by_the_real_site_id(self):
        from app.scraper import site_extractors
        seen = []
        with public_dns(), any_page(), patch.object(site_extractors, "discover", side_effect=lambda site, *a, **k: seen.append(site) or []):
            self.post("/test_config", {"yaml_text": DRAFT_YAML, "page_id": self.list_id, "playable": True})
            self.post("/test_resolvers", {"yaml_text": DRAFT_YAML, "detail_url": "https://demo.example/film/100/film-0"})
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"demo"})
        self.assertEqual(sb._module_site(), sb.DRAFT_SITE_ID)
        seen.clear()   # a new-site draft keeps the draft id
        plain = onboard_store.create_draft("https://demo.example/")
        token = sb.issue_token(plain["id"])
        self.addCleanup(sb.revoke_token, token)
        with public_dns(), any_page(), patch.object(site_extractors, "discover", side_effect=lambda site, *a, **k: seen.append(site) or []):
            self.client.post("/api/onboard/sandbox/test_config", headers={"X-Onboard-Token": token},
                             json={"yaml_text": DRAFT_YAML, "page_id": self.list_id, "playable": True})
        self.assertTrue(seen)
        self.assertEqual(set(seen), {sb.DRAFT_SITE_ID})

    def test_the_module_site_context_is_scoped(self):
        self.assertEqual(sb._module_site(), sb.DRAFT_SITE_ID)
        with sb.module_site("demo"):
            self.assertEqual(sb._module_site(), "demo")
            with sb.module_site(""):
                self.assertEqual(sb._module_site(), sb.DRAFT_SITE_ID)
            self.assertEqual(sb._module_site(), "demo")
        self.assertEqual(sb._module_site(), sb.DRAFT_SITE_ID)
        self.assertEqual(sb._edit_site(self.draft["id"]), "demo")
        self.assertEqual(sb._edit_site("rp_0123456789ab"), "")
        self.assertEqual(sb._edit_site(None), "")


if __name__ == "__main__":
    unittest.main()
