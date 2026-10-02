"""``fetch.impersonated_get`` / ``fetch_impersonated`` / ``impersonated_session``: the Chrome-TLS-fingerprint transport
(``curl_cffi``) that passes Cloudflare's TLS check AND sends a Referer / headers / cookies. Network-free: a fake
``curl_cffi.requests.Session`` (patched into ``curl_cffi.requests``) replays scripted answers and feeds the body through
``content_callback`` the way curl does, so the redirect, size, time and status handling of the real function runs."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.scraper import fetch

URL = "https://site.example/player/oynat/abc"
REFERER = "https://site.example/film/x"


class Reply:
    def __init__(self, status=200, headers=None, chunks=(b"<html>ok</html>",), raises=None):
        self.status_code, self.headers, self.chunks, self.raises = status, headers or {}, chunks, raises


class FakeSession:
    """One scripted ``Reply`` per ``get``; records every call and the cookies set on the jar."""

    created = []

    def __init__(self, *replies, **kw):
        self.replies, self.calls, self.kw, self.closed = list(replies), [], kw, False
        self.cookie_jar = []
        self.cookies = SimpleNamespace(set=lambda name, value, **k: self.cookie_jar.append((name, value, k)))
        FakeSession.created.append(self)

    def get(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, "allow_redirects": allow_redirects})
        reply = self.replies.pop(0)
        for chunk in reply.chunks:
            content_callback(chunk)
        if reply.raises is not None:
            raise reply.raises
        return SimpleNamespace(status_code=reply.status_code, headers=reply.headers)

    def close(self):
        self.closed = True


class Case(unittest.TestCase):
    def run_get(self, *replies, fn=fetch.fetch_impersonated, url=URL, **kw):
        """``fn(url, **kw)`` over a fake session that answers ``replies`` (returns ``(result, session)``)."""
        session = FakeSession(*replies)
        with patch("curl_cffi.requests.Session", return_value=session) as factory:
            result = fn(url, **kw)
        self.factory = factory
        return result, session


class BasicTests(Case):
    def test_returns_the_text_and_impersonates_chrome_by_default(self):
        body, session = self.run_get(Reply(chunks=(b"<p>a", b"b</p>")), headers={"Referer": REFERER})
        self.assertEqual(body, "<p>ab</p>")
        self.assertEqual(self.factory.call_args.kwargs, {"impersonate": "chrome"})
        call = session.calls[0]
        self.assertEqual((call["url"], call["headers"], call["allow_redirects"]), (URL, {"Referer": REFERER}, False))
        self.assertTrue(session.closed, "a private session is closed")

    def test_the_full_result_has_final_url_and_status(self):
        page, _ = self.run_get(Reply(), fn=fetch.impersonated_get)
        self.assertEqual((page.text, page.url, page.status), ("<html>ok</html>", URL, 200))

    def test_custom_impersonation_profile(self):
        self.run_get(Reply(), impersonate="chrome131")
        self.assertEqual(self.factory.call_args.kwargs, {"impersonate": "chrome131"})

    def test_no_user_agent_is_forced_and_caller_headers_are_not_mutated(self):
        headers = {"Referer": REFERER}
        _, session = self.run_get(Reply(), headers=headers)
        self.assertNotIn("User-Agent", session.calls[0]["headers"])
        self.assertEqual(headers, {"Referer": REFERER})

    def test_charset_from_the_content_type(self):
        text = "Türkçe şarkı"
        body, _ = self.run_get(Reply(headers={"content-type": "text/html; charset=iso-8859-9"},
                                     chunks=(text.encode("iso-8859-9"),)))
        self.assertEqual(body, text)
        body, _ = self.run_get(Reply(headers={"content-type": "text/html; charset=nope-9"}, chunks=(text.encode("utf-8"),)))
        self.assertEqual(body, text)   # an unknown charset falls back to utf-8

    def test_cookies_go_into_the_jar_scoped_to_the_first_host(self):
        _, session = self.run_get(Reply(), cookies={"PHPSESSID": "abc", "n": 5})
        self.assertEqual(session.cookie_jar, [("PHPSESSID", "abc", {"domain": "site.example", "path": "/"}),
                                              ("n", "5", {"domain": "site.example", "path": "/"})])


class SessionTests(Case):
    def test_a_shared_session_is_used_and_left_open(self):
        shared = FakeSession(Reply(chunks=(b"one",)), Reply(chunks=(b"two",)))
        with patch("curl_cffi.requests.Session", side_effect=AssertionError("no private session expected")):
            first = fetch.fetch_impersonated(REFERER, session=shared)
            second = fetch.fetch_impersonated(URL, session=shared, headers={"Referer": REFERER})
        self.assertEqual((first, second), ("one", "two"))
        self.assertEqual([c["url"] for c in shared.calls], [REFERER, URL])
        self.assertFalse(shared.closed)

    def test_impersonated_session_builds_a_curl_cffi_session(self):
        with patch("curl_cffi.requests.Session", return_value="S") as factory:
            self.assertEqual(fetch.impersonated_session(), "S")
            fetch.impersonated_session("chrome120")
        self.assertEqual([c.kwargs for c in factory.call_args_list], [{"impersonate": "chrome"}, {"impersonate": "chrome120"}])

    def test_the_private_session_is_closed_on_failure_too(self):
        session = FakeSession(Reply(status=500))
        with patch("curl_cffi.requests.Session", return_value=session):
            with self.assertRaises(fetch.FetchError):
                fetch.fetch_impersonated(URL)
        self.assertTrue(session.closed)


class RedirectTests(Case):
    def test_redirects_are_followed_by_hand_and_every_hop_goes_through_allow(self):
        seen = []

        def allow(url):
            seen.append(url)
            return True

        page, session = self.run_get(Reply(302, {"location": "/moved"}), Reply(301, {"location": "https://cdn.example/p"}),
                                     Reply(chunks=(b"done",)), fn=fetch.impersonated_get, allow=allow,
                                     headers={"Referer": REFERER})
        self.assertEqual((page.text, page.url), ("done", "https://cdn.example/p"))
        self.assertEqual(seen, [URL, "https://site.example/moved", "https://cdn.example/p"])
        self.assertEqual([c["url"] for c in session.calls], seen)
        self.assertTrue(all(c["headers"] == {"Referer": REFERER} for c in session.calls))   # the referer follows the hops
        self.assertTrue(all(c["allow_redirects"] is False for c in session.calls))

    def test_a_refused_hop_is_never_requested(self):
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(302, {"location": "http://169.254.169.254/latest"}), Reply(chunks=(b"secret",)),
                         allow=lambda url: "169.254" not in url)
        self.assertIn("host not allowed", str(ctx.exception))
        self.assertEqual(len(FakeSession.created[-1].calls), 1)

    def test_a_refused_first_url_makes_no_request(self):
        session = FakeSession(Reply())
        with patch("curl_cffi.requests.Session", return_value=session):
            with self.assertRaises(fetch.FetchError):
                fetch.fetch_impersonated("http://127.0.0.1/x", allow=lambda url: False)
        self.assertEqual(session.calls, [])

    def test_too_many_redirects_and_redirect_without_location(self):
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(*[Reply(302, {"location": URL}) for _ in range(4)], max_redirects=3)
        self.assertIn("too many redirects", str(ctx.exception))
        self.assertEqual(len(FakeSession.created[-1].calls), 4)   # the first request + 3 hops
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(302, {}))
        self.assertEqual(ctx.exception.status, 302)


class LimitTests(Case):
    def test_a_body_over_max_bytes_is_refused(self):
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(chunks=(b"x" * 600, b"x" * 600)), max_bytes=1000)
        self.assertIn("larger than 1000", str(ctx.exception))
        body, _ = self.run_get(Reply(chunks=(b"x" * 500, b"x" * 500)), max_bytes=1000)
        self.assertEqual(len(body), 1000)

    def test_the_abort_the_real_transport_raises_still_reads_as_a_size_error(self):
        """curl stops the transfer when the write callback aborts and ``curl_cffi`` raises: the size verdict stands."""
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(chunks=(b"x" * 2000,), raises=RuntimeError("Failed to perform, curl: (23)")), max_bytes=1000)
        self.assertIn("larger than 1000", str(ctx.exception))

    def test_total_time_budget_across_hops(self):
        clock = iter([0.0, 0.0, 0.5, 5.0])   # deadline, hop-1 remaining, chunk check, hop-2 remaining
        with patch("time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(fetch.FetchError) as ctx:
                self.run_get(Reply(302, {"location": "/a"}), Reply(), timeout=1.0)
        self.assertIn("timeout after 1s", str(ctx.exception))

    def test_each_request_gets_the_remaining_time(self):
        clock = iter([0.0, 0.0, 0.3, 0.3, 0.3])
        with patch("time.monotonic", side_effect=lambda: next(clock)):
            _, session = self.run_get(Reply(302, {"location": "/a"}), Reply(), timeout=2.0)
        self.assertEqual([round(c["timeout"], 1) for c in session.calls], [2.0, 1.7])

    def test_curl_timeout_and_network_errors_become_fetch_errors(self):
        class Boom(Exception):
            code = 28

        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(raises=Boom("Operation timed out")), timeout=3.0)
        self.assertIn("timeout after 3s", str(ctx.exception))
        with self.assertRaises(fetch.FetchError) as ctx:
            self.run_get(Reply(raises=ConnectionError("connection refused")))
        self.assertIn("failed to fetch", str(ctx.exception))
        self.assertIsNone(ctx.exception.status)


class StatusTests(Case):
    def test_a_non_200_answer_raises_with_the_status(self):
        for status in (403, 404, 429, 503):
            with self.assertRaises(fetch.FetchError) as ctx:
                self.run_get(Reply(status, chunks=(b"Just a moment...",)))
            self.assertEqual(ctx.exception.status, status)
            self.assertIn(f"HTTP {status}", str(ctx.exception))

    def test_there_are_no_retries(self):
        session = FakeSession(Reply(503), Reply(chunks=(b"later",)))
        with patch("curl_cffi.requests.Session", return_value=session):
            with self.assertRaises(fetch.FetchError):
                fetch.fetch_impersonated(URL)
        self.assertEqual(len(session.calls), 1)

    def test_the_existing_transports_are_untouched(self):
        for name in ("fetch_url", "post_url", "fetch_limited", "fetch", "reachable", "page", "browser_page"):
            self.assertTrue(callable(getattr(fetch, name)), name)


if __name__ == "__main__":
    unittest.main()
