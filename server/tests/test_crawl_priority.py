"""series_crawl candidate order: hero (featured_*) first, then the main home rows, other home lists, the archive last."""
import _sandbox  # noqa: F401  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import unittest

from app import db
from app.library import series_crawl

SITE = "diziasya"
NOW = 2_000_000_000.0


class CrawlPriority(unittest.TestCase):
    def setUp(self):
        db.init()
        db.execute("DELETE FROM source_items")
        db.execute("DELETE FROM library_lists")

    def series(self, key, inv=None):
        norm = {"type": "series", "title": key, "source_url": "https://x/" + key}
        if inv:
            norm[series_crawl.MARKER] = inv
        db.execute("INSERT INTO source_items (source,source_key,canonical_id,raw,normalized,fetched_at) VALUES (?,?,?,?,?,?)",
                   (SITE, key, "c_" + key, "{}", json.dumps(norm), 1))

    def member(self, list_id, key, pos):
        db.execute("INSERT INTO library_lists (list_id, canonical_id, position) VALUES (?,?,?)", (list_id, "c_" + key, pos))

    def keys(self, by_key=None):
        return [c["key"] for c in series_crawl.select(SITE, None, by_key, now=NOW)]

    def test_hero_first_then_rows_then_rest(self):
        stale = {"at": 1, "ok_at": 1, "complete": True}   # long done: due as "stale"
        for k in ("arch_a", "arch_b", "row2", "row1", "other", "hero2", "hero1", "srconly"):
            self.series(k)
        self.series("hero_stale", inv=stale)
        self.series("done", inv={"at": NOW, "ok_at": NOW, "complete": True})   # fresh: skipped
        self.member("featured_" + SITE, "hero1", 0)
        self.member("featured_" + SITE, "hero2", 1)
        self.member("featured_" + SITE, "hero_stale", 2)
        self.member("featured_" + SITE, "done", 3)
        self.member("latest_episodes_" + SITE, "row1", 0)
        self.member("category_animeler_" + SITE, "row2", 1)
        self.member("trending_series_" + SITE, "hero2", 0)    # best tier wins (featured)
        self.member("latest_series_" + SITE, "other", 0)
        self.member("source_" + SITE, "srconly", 0)            # not a home list
        self.member("featured_othersite", "arch_b", 0)         # another site's hero: no effect
        self.assertEqual(self.keys(), ["hero1", "hero2", "hero_stale", "row1", "row2", "other",
                                       "arch_a", "arch_b", "srconly"])

    def test_pending_hero_beats_budget_slot(self):
        """Six never-read hero series behind many due archive series: the next 15-series pass takes the hero first."""
        for i in range(20):
            self.series("arch%02d" % i)
        heroes = ["solo", "perfect", "nemesis", "undercover", "study", "tangerines"]
        for pos, k in enumerate(heroes):
            self.series(k)
            self.member("featured_" + SITE, k, pos)
        self.member("latest_episodes_" + SITE, "arch19", 0)
        self.assertEqual(self.keys()[:7], heroes + ["arch19"])

    def test_scan_card_outside_lists_before_archive(self):
        self.series("arch")
        self.series("card")
        self.assertEqual(self.keys(by_key={"card": {}}), ["card", "arch"])


if __name__ == "__main__":
    unittest.main()
