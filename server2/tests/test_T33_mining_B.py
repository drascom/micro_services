import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock

from selectolax.parser import HTMLParser

from app.core.catalog import images
from app.core.extract import cards, parse
from app.core.fetch import FetchError
from app.core.fetch.http import decode_body


class DecodeTests(unittest.TestCase):
    def test_meta_charset_used_without_header(self):
        body = '<html><head><meta charset="windows-1254"></head><body>Şahin Işık</body></html>'.encode("cp1254")
        self.assertIn("Şahin Işık", decode_body(body, "text/html"))
        body = ('<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-9"><p>Güneş</p>').encode("iso-8859-9")
        self.assertIn("Güneş", decode_body(body, ""))

    def test_undeclared_non_utf8_falls_back_to_cp1254(self):
        self.assertEqual(decode_body("Çiçek ğ".encode("cp1254"), "text/html"), "Çiçek ğ")
        self.assertEqual(decode_body("Çiçek ğ".encode("utf-8"), "text/html"), "Çiçek ğ")
        self.assertEqual(decode_body("ı".encode("cp1254"), "text/html; charset=bogus-x"), "ı")


class HotlinkTests(unittest.TestCase):
    def setUp(self):
        images.reset()

    def test_403_retried_once_with_site_referer_and_remembered(self):
        calls = []

        def fake(url, *, headers=None, **_kw):
            calls.append(headers.get("Referer"))
            if not headers.get("Referer"):
                raise FetchError("http_403", "", 403, url)
            return b"IMG"

        with mock.patch("app.core.fetch.fetch_limited", side_effect=fake), \
                mock.patch.object(images, "_site_hosts", return_value=["https://www.site-a.example"]):
            self.assertEqual(images._http_fetch("https://img.site-a.example/p/1.jpg"), b"IMG")
            self.assertEqual(images._http_fetch("https://img.site-a.example/p/2.jpg"), b"IMG")
        self.assertEqual(calls, [None, "https://www.site-a.example/", "https://www.site-a.example/"])

    def test_other_errors_not_retried(self):
        calls = []

        def fake(url, *, headers=None, **_kw):
            calls.append(headers.get("Referer"))
            raise FetchError("http_404", "", 404, url)

        with mock.patch("app.core.fetch.fetch_limited", side_effect=fake), \
                mock.patch.object(images, "_site_hosts", return_value=[]):
            self.assertIsNone(images._http_fetch("https://cdn.other.example/x.jpg"))
        self.assertEqual(calls, [None])


def _cards(html):
    return HTMLParser(html).css("div.card")


class PosterSpecTests(unittest.TestCase):
    def test_srcset_widest_candidate(self):
        html = "".join(f'<div class="card"><a href="/d/{i}">T{i}</a><picture>'
                       f'<source srcset="/s/{i}-300.webp 300w, /s/{i}-900.webp 900w"><img src="data:image/gif;base64,R0"></picture></div>'
                       for i in range(3))
        found = _cards(html)
        spec = cards.poster_spec(found)
        self.assertEqual(parse.apply_field(found[1], spec), "/s/1-900.webp")

    def test_inline_background_image(self):
        html = "".join(f'<div class="card"><a href="/d/{i}" style="color:red; background-image: url(\'/bg/{i}.jpg\')">T{i}</a></div>'
                       for i in range(3))
        found = _cards(html)
        spec = cards.poster_spec(found)
        self.assertEqual(parse.apply_field(found[2], spec), "/bg/2.jpg")


if __name__ == "__main__":
    unittest.main()
