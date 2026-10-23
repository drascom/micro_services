import _sandbox  # noqa: F401  (must be first)

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import app


def _png(w=2400, h=1600, color=(200, 40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class BackgroundsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(config, "DATA_DIR", self._tmp.name)
        self._p.start()
        self.client = TestClient(app)

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_upload_lists_serves_and_dedupes(self):
        data = _png()
        r = self.client.post("/api/backgrounds", files={"file": ("a.png", data, "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        item = r.json()
        self.assertEqual((item["width"], item["height"]), (1620, 1080))  # fit into 1920x1080, aspect kept
        self.assertEqual(item["url"], "/api/backgrounds/%s.jpg" % item["id"])
        again = self.client.post("/api/backgrounds", files={"file": ("b.png", data, "image/png")}).json()
        self.assertEqual(again["id"], item["id"])
        listing = self.client.get("/api/backgrounds")
        self.assertEqual([i["id"] for i in listing.json()["items"]], [item["id"]])
        self.assertEqual(self.client.get("/api/backgrounds", headers={"If-None-Match": listing.headers["etag"]}).status_code, 304)
        img = self.client.get(item["url"])
        self.assertEqual((img.status_code, img.headers["content-type"]), (200, "image/jpeg"))
        self.assertIn("immutable", img.headers["cache-control"])
        with Image.open(io.BytesIO(self.client.get(item["blur_url"]).content)) as blur:
            self.assertEqual(blur.size, (320, 180))
        small = self.client.post("/api/backgrounds", files={"file": ("s.png", _png(400, 300, (1, 2, 3)), "image/png")}).json()
        self.assertEqual((small["width"], small["height"]), (400, 300))  # never enlarged

    def test_non_image_is_rejected(self):
        r = self.client.post("/api/backgrounds", files={"file": ("x.png", b"not an image at all", "image/png")})
        self.assertEqual(r.status_code, 415)
        self.assertIn("error", r.json())
        gif = io.BytesIO()
        Image.new("RGB", (10, 10)).save(gif, "GIF")
        self.assertEqual(self.client.post("/api/backgrounds", files={"file": ("g.gif", gif.getvalue(), "image/gif")}).status_code, 415)
        self.assertEqual(self.client.get("/api/backgrounds").json()["items"], [])

    def test_delete(self):
        item = self.client.post("/api/backgrounds", files={"file": ("a.png", _png(), "image/png")}).json()
        self.assertEqual(self.client.delete("/api/backgrounds/" + item["id"]).json(), {"ok": True})
        self.assertEqual(self.client.get("/api/backgrounds").json()["items"], [])
        self.assertEqual(self.client.get(item["url"]).status_code, 404)
        self.assertEqual(self.client.delete("/api/backgrounds/" + item["id"]).status_code, 404)
        self.assertFalse(os.listdir(os.path.join(self._tmp.name, "backgrounds")))


if __name__ == "__main__":
    unittest.main()
