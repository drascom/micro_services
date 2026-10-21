import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from app import config, db
from app.core import fetch
from app.core.fetch import browser, http, limiter, netguard, pages, playback
from app.core.fetch.base import FetchError, Page, SiteCtx

FAT = "<html><body>" + "".join(f"<p>paragraph {i} with some visible text</p>" for i in range(80)) + "</body></html>"
THIN = "<html><body><div id=app></div></body></html>"
CHALLENGE = "<html><head><title>Just a moment...</title></head><body>checking</body></html>"


class _Handler(BaseHTTPRequestHandler):
    log = []          # (path, start, end)
    hits = {}
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _send(self, status, body, headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):   # the client stopped reading on purpose (size caps)
            pass

    def do_GET(self):
        start = time.monotonic()
        path = self.path.split("?", 1)[0]
        with self.lock:
            self.hits[path] = self.hits.get(path, 0) + 1
        if path == "/robots.txt":
            return self._send(404, "nope")
        if path.startswith("/slow"):
            time.sleep(0.08)
            self._send(200, FAT)
        elif path == "/thin":
            self._send(200, THIN)
        elif path == "/challenge":
            self._send(503, CHALLENGE)
        elif path == "/err500":
            self._send(500, "boom")
        elif path == "/429":
            self._send(429, "slow down")
        elif path == "/big":
            self._send(200, "x" * 50_000)
        elif path == "/hang":
            time.sleep(1.5)
            self._send(200, FAT)
        elif path == "/bigmedia":   # ignores Range: a whole "video"
            self._send(200, b"v" * 2_000_000)
        elif path == "/redir-private":
            self._send(302, "", {"Location": "http://10.1.2.3/inside"})
        else:
            self._send(200, FAT)
        with self.lock:
            self.log.append((path, start, time.monotonic()))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        if self.path.startswith("/post307"):
            return self._send(307, "", {"Location": "http://localhost:%d/ok" % self.server.server_address[1]})
        self._send(200 if body == "q=a" else 400, FAT)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def setUpModule():
    global SERVER, BASE, ALT, TMP, OLD_DB
    OLD_DB = config.DB_PATH
    TMP = tempfile.mkdtemp(dir=_sandbox.ROOT)
    migs = os.path.join(TMP, "migs")
    os.makedirs(migs)
    for name in ("001_base.py", "002_fetch.py"):   # only ours: other tasks' migrations may be half-written
        shutil.copy(os.path.join(db.MIGRATIONS_DIR, name), migs)
    config.DB_PATH = os.path.join(TMP, "t01.db")
    db.init(migrations_dir=migs)
    netguard.allow_test_hosts("127.0.0.1", "localhost")
    SERVER = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    port = SERVER.server_address[1]
    BASE = f"http://127.0.0.1:{port}"
    ALT = f"http://localhost:{port}"


def tearDownModule():
    SERVER.shutdown()
    SERVER.server_close()
    netguard.clear_test_hosts()
    config.DB_PATH = OLD_DB


def site(**kw):
    kw.setdefault("delay_ms", 0)
    kw.setdefault("concurrency", 4)
    return SiteCtx("t01site", BASE, **kw)


class FetchCase(unittest.TestCase):
    def setUp(self):
        fetch.LIMITER.clear()
        with _Handler.lock:
            _Handler.log.clear()
            _Handler.hits.clear()


class GuardTests(FetchCase):
    def test_ssrf_blocked(self):
        for url in ("http://10.0.0.1/", "http://169.254.169.254/latest", "file:///etc/passwd",
                    "http://example.com:8080/", BASE + "/redir-private"):
            with self.subTest(url=url):
                with self.assertRaises(FetchError) as ctx:
                    fetch.get(url, site=site(), mode="http")
                self.assertEqual(ctx.exception.code, "blocked_ssrf")
        with self.assertRaises(FetchError) as ctx:   # playback wrappers guard too
            fetch.fetch_limited(BASE + "/redir-private")
        self.assertEqual(ctx.exception.code, "blocked_ssrf")
        self.assertTrue(fetch.reachable(BASE + "/ok"))
        self.assertFalse(fetch.reachable("http://10.0.0.1/a.mp4"))

    def test_test_hosts_need_sandbox(self):
        self.assertTrue(netguard.sandbox_active())
        for env in ({"DIZIFLIX_TEST_SANDBOX": ""}, {"DATA_DIR": "/srv/data"}):   # the env var alone is not enough
            with mock.patch.dict(os.environ, env):
                self.assertFalse(netguard.sandbox_active())
                with self.assertRaises(RuntimeError):
                    netguard.allow_test_hosts("127.0.0.1")

    def test_dns_is_pinned_to_the_checked_address(self):
        """netguard resolves once; both transports connect to that IP (a name nothing else can resolve)."""
        real = netguard.socket.getaddrinfo
        calls = []

        def fake_dns(host, *a, **kw):
            if host == "rebind.test":
                calls.append(host)
                return [(2, 1, 6, "", ("127.0.0.1", 0))]
            return real(host, *a, **kw)

        port = SERVER.server_address[1]
        netguard.allow_test_hosts("rebind.test")
        no_proxy = "localhost,127.0.0.1,::1,rebind.test"
        with mock.patch.object(netguard.socket, "getaddrinfo", side_effect=fake_dns), \
                mock.patch.dict(os.environ, {"NO_PROXY": no_proxy, "no_proxy": no_proxy}):
            self.assertEqual(http.request(f"http://rebind.test:{port}/ok").status, 200)
            self.assertIn(b"paragraph", fetch.fetch_limited(f"http://rebind.test:{port}/ok"))
        self.assertEqual(calls, ["rebind.test", "rebind.test"])

    def test_playback_body_caps(self):
        t0 = time.monotonic()
        self.assertTrue(fetch.reachable(BASE + "/bigmedia"))   # headers only, Range ignored by the server
        self.assertLess(time.monotonic() - t0, 2)
        with mock.patch.object(playback, "MAX_BODY", 100_000):
            with self.assertRaises(FetchError) as ctx:
                fetch.fetch_url(BASE + "/bigmedia", check_robots=False, retries=1)
        self.assertEqual(ctx.exception.code, "too_large")

    def test_post_307_to_other_host_refused(self):
        with self.assertRaises(FetchError) as ctx:
            fetch.get(BASE + "/post307", site=site(), method="POST", data={"q": "a"})
        self.assertEqual(ctx.exception.code, "bad_request")

    def test_post_rules(self):
        page = fetch.get(BASE + "/ok", site=site(), method="POST", data={"q": "a"})
        self.assertEqual(page.engine, "http")
        for url, data in ((ALT + "/ok", {}), (BASE + "/ok", {"q": "x" * 5000})):
            with self.assertRaises(FetchError) as ctx:
                fetch.get(url, site=site(), method="POST", data=data)
            self.assertEqual(ctx.exception.code, "bad_request")


class LimitTests(FetchCase):
    def test_same_host_concurrency_and_delay(self):
        urls = [f"{BASE}/slow{i}" for i in range(4)]
        out = fetch.get_many(urls, site=site(concurrency=2, delay_ms=100), mode="http")
        self.assertTrue(all(isinstance(p, Page) for p in out))
        log = sorted((s, e) for p, s, e in _Handler.log if p.startswith("/slow"))
        self.assertEqual(len(log), 4)
        peak = max(sum(1 for s2, e2 in log if s2 <= s < e2) for s, _ in log)
        self.assertLessEqual(peak, 2)
        gaps = [b[0] - a[0] for a, b in zip(log, log[1:])]
        self.assertGreaterEqual(min(gaps), 0.09)   # server-side clock; request start spacing is 100 ms

    def test_hosts_do_not_wait_for_each_other(self):
        urls = [f"{h}/ok{i}" for i in range(3) for h in (BASE, ALT)]
        t0 = time.monotonic()
        out = fetch.get_many(urls, site=site(concurrency=1, delay_ms=150), mode="http")
        took = time.monotonic() - t0
        self.assertTrue(all(isinstance(p, Page) for p in out))
        self.assertLess(took, 0.7)   # serial on one host would be >= 5 * 0.15 = 0.75 s

    def test_429_backoff_grows_then_resets(self):
        host = "127.0.0.1"
        with mock.patch.object(limiter, "BACKOFF_BASE_S", 0.05):
            for expected in (0.05, 0.1):
                with self.assertRaises(FetchError) as ctx:
                    fetch.get(BASE + "/429", site=site(), mode="http")
                self.assertEqual(ctx.exception.code, "http_429")
                self.assertAlmostEqual(fetch.LIMITER.backoff_s(host), expected)
            t0 = time.monotonic()
            fetch.get(BASE + "/ok", site=site(), mode="http")
            self.assertGreaterEqual(time.monotonic() - t0, 0.075)
            self.assertEqual(fetch.LIMITER.backoff_s(host), 0.0)

    def test_get_many_keeps_order_and_isolates_errors(self):
        urls = [BASE + "/a", BASE + "/b", BASE + "/err500", BASE + "/c", BASE + "/d"]
        out = fetch.get_many(urls, site=site(), mode="http")
        self.assertEqual([type(x).__name__ for x in out], ["Page", "Page", "FetchError", "Page", "Page"])
        self.assertEqual(out[2].code, "http_500")
        self.assertEqual([p.url for p in out if isinstance(p, Page)], [urls[0], urls[1], urls[3], urls[4]])


class EngineTests(FetchCase):
    def test_auto_does_not_escalate_to_unguarded_browser(self):
        self.assertFalse(browser.GUARDED)   # Obscura's own redirects/subresources bypass netguard (SSRF)
        with mock.patch.object(limiter, "BACKOFF_BASE_S", 0.01), \
                mock.patch.object(browser, "render", return_value=(FAT, 200)) as fake:   # challenge back-off: 10 ms
            self.assertEqual(fetch.get(BASE + "/thin", site=site()).engine, "http")
            with self.assertRaises(FetchError) as ctx:
                fetch.get(BASE + "/challenge", site=site())
            self.assertEqual(ctx.exception.code, "challenge")
            fake.assert_not_called()
            self.assertEqual(fetch.get(BASE + "/thin", site=site(), mode="browser").engine, "browser")

    def test_auto_thin_escalates_to_browser(self):
        with mock.patch.object(browser, "GUARDED", True), \
                mock.patch.object(browser, "render", return_value=(FAT, 200)) as fake:
            page = fetch.get(BASE + "/thin", site=site(), wait_for="#app")
            fake.assert_called_once()
            self.assertEqual(page.engine, "browser")
            fat = fetch.get(BASE + "/ok", site=site())
            self.assertEqual(fat.engine, "http")
            self.assertEqual(fake.call_count, 1)

    def test_auto_thin_without_browser_keeps_http_page(self):
        page = fetch.get(BASE + "/thin", site=site())   # OBSCURA_BIN is empty in the sandbox
        self.assertEqual((page.engine, page.html), ("http", THIN))

    def test_challenge(self):
        unavailable = FetchError("browser_unavailable")
        with mock.patch.object(limiter, "BACKOFF_BASE_S", 0.01), mock.patch.object(browser, "GUARDED", True), \
                mock.patch.object(browser, "render", side_effect=unavailable) as fake:
            for mode in ("http", "auto"):
                with self.assertRaises(FetchError) as ctx:
                    fetch.get(BASE + "/challenge", site=site(), mode=mode)
                self.assertEqual(ctx.exception.code, "challenge")
            self.assertEqual(fake.call_count, 1)   # auto: one escalation
        with self.assertRaises(FetchError) as ctx:
            fetch.get(BASE + "/ok", site=site(), mode="browser")
        self.assertEqual(ctx.exception.code, "browser_unavailable")

    def test_size_and_time_limits(self):
        with mock.patch.object(http, "MAX_BYTES", 10_000):
            with self.assertRaises(FetchError) as ctx:
                fetch.get(BASE + "/big", site=site(), mode="http")
            self.assertEqual(ctx.exception.code, "too_large")
        with mock.patch.object(http, "TOTAL_TIMEOUT_S", 0.2):
            t0 = time.monotonic()
            with self.assertRaises(FetchError) as ctx:
                fetch.get(BASE + "/hang", site=site(), mode="http")
            self.assertEqual(ctx.exception.code, "timeout")
            self.assertLess(time.monotonic() - t0, 1.0)


class PageStoreTests(FetchCase):
    def test_save_load_and_cache(self):
        s = SiteCtx("store1", BASE, delay_ms=0)
        first = fetch.get(BASE + "/cached", site=s, mode="http", cache_s=60, page_type="title")
        second = fetch.get(BASE + "/cached", site=s, mode="http", cache_s=60)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.html, first.html)
        self.assertEqual(_Handler.hits.get("/cached"), 1)
        self.assertEqual([r["url"] for r in pages.list("store1", "title")], [BASE + "/cached"])
        self.assertIsNone(pages.load("store1", BASE + "/missing"))

    def test_cap_drops_oldest(self):
        with mock.patch.object(pages, "MAX_PER_SITE", 3):
            paths = [pages.save("store2", "listing", Page(f"https://x.test/{i}", "", 200, f"<p>{i}</p>", "http"))
                     for i in range(5)]
        kept = pages.list("store2")
        self.assertEqual(len(kept), 3)
        self.assertFalse(os.path.exists(paths[0]))
        self.assertTrue(os.path.exists(paths[4]))
        self.assertTrue(paths[4].startswith(config.PAGES_DIR))


class SignalTests(unittest.TestCase):
    def test_page_signals(self):
        html = ('<html><head><link rel="canonical" href="/tr2/"><meta http-equiv="refresh" content="0;url=/tr2/">'
                '</head><body></body></html>')
        out = fetch.page_signals(html, "https://example.tld/")
        self.assertEqual(out["canonical_url"], "https://example.tld/tr2/")
        self.assertEqual(out["redirect_hint"]["kind"], "meta")
        self.assertEqual(fetch.page_signals("", "https://example.tld/"), {})


if __name__ == "__main__":
    unittest.main()
