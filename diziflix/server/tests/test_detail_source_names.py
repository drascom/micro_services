"""Detail `source_names` (additive: display names of `sources`)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import rows


def fake_load(sid):
    if sid == "ghost":
        raise FileNotFoundError(sid)
    if sid == "noname":
        return SimpleNamespace(data={})
    return SimpleNamespace(data={"display_name": {"yabancidizi": "Yabancı Dizi", "sinemalar": "Sinemalar.com"}[sid]})


class SourceNamesTests(unittest.TestCase):
    def names(self, ids):
        with patch("app.scraper.config.load_site", side_effect=fake_load):
            return rows.source_names(ids)

    def test_single_and_multiple_sites_keep_order(self):
        self.assertEqual(self.names(["yabancidizi"]), [{"id": "yabancidizi", "name": "Yabancı Dizi"}])
        self.assertEqual([n["name"] for n in self.names(["sinemalar", "yabancidizi"])], ["Sinemalar.com", "Yabancı Dizi"])

    def test_unknown_removed_or_unnamed_site_falls_back_to_the_id(self):
        self.assertEqual(self.names(["ghost", "noname"]), [{"id": "ghost", "name": "ghost"}, {"id": "noname", "name": "noname"}])

    def test_empty_or_bad_input(self):
        self.assertEqual(self.names([]), [])
        self.assertEqual(self.names(None), [])
        self.assertEqual(self.names(["", None]), [])


if __name__ == "__main__":
    unittest.main()
