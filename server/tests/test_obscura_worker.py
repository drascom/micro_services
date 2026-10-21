import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest

from app.scraper.obscura_worker import _image_urls, _looks_like_image


class ObscuraWorkerTests(unittest.TestCase):
    def test_image_urls_are_same_origin_absolute_and_unique(self):
        html = '''
        <img data-src="/uploads/a.jpg" src="/placeholder.jpg">
        <img src="https://yabancidizi.news/uploads/a.jpg">
        <img src="https://cdn.example/foreign.jpg">
        <img src="data:image/png;base64,AAAA">
        '''
        self.assertEqual(_image_urls(html, "https://yabancidizi.news/diziler"), [
            "https://yabancidizi.news/uploads/a.jpg",
        ])

    def test_supported_image_signatures(self):
        self.assertTrue(_looks_like_image(b"\xff\xd8\xffjpeg"))
        self.assertTrue(_looks_like_image(b"\x89PNG\r\n\x1a\nrest"))
        self.assertTrue(_looks_like_image(b"RIFF0000WEBPrest"))
        self.assertFalse(_looks_like_image(b"<html>blocked</html>"))


if __name__ == "__main__":
    unittest.main()
