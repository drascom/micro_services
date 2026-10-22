"""yabancidizi availability gate (fixture pages) + the page-keyed block list of dead series pages (``library/gate.py`` ``block_dead``).

A dead page (gone / no episode) of ONE site is blocked as (site, page): that source leaves the library, the same title from another
site stays; later scans meet the link (footer spotlight) and take nothing, without a request; after BLOCKED_RECHECK_DAYS the page is
asked again; the admin can list and remove a block. Network-free (fake ``fetch.page``, recorded fixtures).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, db
from app.library import gate, ingest as ingest_module
from app.routers import ops
from app.scraper import config as scfg
from app.scraper.runner import RunResult

from test_series_crawl import Base, SNW_KEY, snw_card as card

FIX = Path(__file__).parent / "fixtures"
SITE = "yabancidizi"
DEAD_SLUG = "the-blacklist-izle-10"
DEAD_KEY = "dizi/" + DEAD_SLUG
EMPTY_SLUG = "bos-dizi-izle-7"
EMPTY_KEY = "dizi/" + EMPTY_SLUG
NOT_FOUND = "<html><head><title>404 - Sayfa Bulunamadı</title></head><body><h1>Aradığınız sayfa bulunamadı</h1></body></html>"
EMPTY_PAGE = ("<html><head><title>Boş Dizi izle</title></head><body><div id='router-view'><h1 class='page-title'>Boş Dizi (2025)</h1>"
              "<p id='tv-series-desc'>Henüz bölüm yok.</p></div></body></html>")


def spotlight(slug, title):   # the footer "Öne Çıkan" link: title + series page only
    return {"title": title, "detail_url": f"https://yabancidizi.news/dizi/{slug}"}


class GateFixtureTests(unittest.TestCase):
    """The yaml gate of yabancidizi judged on recorded pages (no request): an episode page with players passes, a page without any
    player candidate (a series / list page) is "no player"."""

    def test_episode_pages_pass_pages_without_player_fail(self):
        cfg = scfg.load_site(SITE)
        with patch.object(config, "AVAILABILITY_GATE", True):
            spec = gate.gate_of(cfg)
            self.assertEqual(spec, {"probe": 2, "require": "player"})
            for name in ("yabancidizi_episode_snw_s4e10.html", "yabancidizi_episode_snw_s4e2.html"):
                verdict = gate.judge_html(cfg, (FIX / name).read_text(), "https://yabancidizi.news/dizi/x/sezon-4/bolum-2",
                                          require=spec["require"])
                self.assertEqual(verdict["state"], gate.OK, name)
                self.assertGreaterEqual(verdict["candidates"], 1)
            verdict = gate.judge_html(cfg, (FIX / "yabancidizi_series_snw.html").read_text(), "https://yabancidizi.news/dizi/x",
                                      require=spec["require"])
            self.assertEqual((verdict["state"], verdict["via"]), (gate.BLOCKED, "gate"))
        self.assertEqual(gate.gate_of(cfg), {})   # the kill switch (tests run with AVAILABILITY_GATE=0)

    def test_film_page_passes(self):
        # recorded film page (film/alpha, played OK): VidMolly iframe + OK.ru alternate handoff
        cfg = scfg.load_site(SITE)
        with patch.object(config, "AVAILABILITY_GATE", True):
            verdict = gate.judge_html(cfg, (FIX / "yabancidizi_movie.html").read_text(), "https://yabancidizi.news/film/alpha",
                                      kind=gate.KIND_MOVIE, require=gate.gate_of(cfg)["require"])
        self.assertEqual(verdict["state"], gate.OK)
        self.assertGreaterEqual(verdict["candidates"], 1)


class DeadPageTests(Base):
    def setUp(self):
        super().setUp()
        real = self.site.__call__

        def site(cfg, url, **kw):
            if DEAD_SLUG in url:
                self.site.calls.append(url)
                return NOT_FOUND
            if EMPTY_SLUG in url:
                self.site.calls.append(url)
                return EMPTY_PAGE
            return real(cfg, url, **kw)
        p = patch.object(ingest_module.fetch, "page", site)
        p.start()
        self.addCleanup(p.stop)

    def scan(self, main, spot=()):
        """One scan: ``main`` = the home list cards, ``spot`` = the footer spotlight collection."""
        result = RunResult(SITE, items=main, drift={"drift": False})

        def collection(cfg, c, limit, topup=None):
            return [dict(x) for x in spot] if c.get("role") == "spotlight" else []
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_fetch_collection", side_effect=collection):
            return ingest_module.ingest_source(SITE)

    def calls(self, slug):
        return [u for u in self.site.calls if slug in u]

    def members(self, list_id):
        return [r["canonical_id"] for r in db.query("SELECT canonical_id FROM library_lists WHERE list_id=? ORDER BY position", (list_id,))]

    def test_dead_spotlight_link_is_blocked_dropped_from_the_row_and_not_asked_again(self):
        spot = [spotlight(DEAD_SLUG, "The Blacklist"), spotlight(EMPTY_SLUG, "Boş Dizi")]
        self.scan([card(4, 10)], spot)
        rows = {r["source_key"]: r for r in db.query("SELECT * FROM blocked_pages WHERE site=?", (SITE,))}
        self.assertEqual(rows[DEAD_KEY]["via"], gate.VIA_GONE)
        self.assertEqual(rows[EMPTY_KEY]["via"], gate.VIA_EMPTY)
        for key in (DEAD_KEY, EMPTY_KEY):
            self.assertEqual((rows[key]["kind"], rows[key]["status"]), ("series", "blocked"))
            self.assertIsNone(db.query_one("SELECT * FROM source_items WHERE source_key=?", (key,)))
        self.assertTrue(db.query_one("SELECT * FROM source_items WHERE source_key=?", (SNW_KEY,)))
        self.assertEqual((len(self.calls(DEAD_SLUG)), len(self.calls(EMPTY_SLUG))), (1, 1))
        # next scan: the links are still in the footer; nothing is written, no request, not counted in the collection
        result = self.scan([card(4, 10)], spot)
        self.assertEqual((len(self.calls(DEAD_SLUG)), len(self.calls(EMPTY_SLUG))), (1, 1))
        self.assertIsNone(db.query_one("SELECT * FROM source_items WHERE source_key=?", (DEAD_KEY,)))
        report = next(r for r in result["collections"] if r["id"] == "spotlight_yabancidizi")
        self.assertEqual((report["count"], report["blocked"]), (0, 2))
        self.assertEqual(self.members("spotlight_yabancidizi"), [])
        # Olay defteri + admin list / remove
        events = [e for e in ops.events(site=SITE, limit=200, before=None)["events"] if e["kind"] == "blocked_page"]
        self.assertEqual({e["source_key"] for e in events}, {DEAD_KEY, EMPTY_KEY})
        self.assertEqual(len(ops.blocked_list(SITE, limit=200)["pages"]), 2)
        url = rows[DEAD_KEY]["url"]
        self.assertEqual(ops.blocked_remove(SITE, url)["removed"], 1)
        self.scan([card(4, 10)], spot)
        self.assertEqual(len(self.calls(DEAD_SLUG)), 2)   # asked again after the admin removed the block (still gone: blocked again)
        self.assertEqual(db.query_one("SELECT via FROM blocked_pages WHERE site=? AND url=?", (SITE, url))["via"], gate.VIA_GONE)

    def test_the_block_runs_out_and_the_page_is_asked_again(self):
        spot = [spotlight(DEAD_SLUG, "The Blacklist")]
        self.scan([card(4, 10)], spot)
        self.scan([card(4, 10)], spot)
        self.assertEqual(len(self.calls(DEAD_SLUG)), 1)
        old = int(time.time() - config.BLOCKED_RECHECK_DAYS * 86400 - 60)
        db.execute("UPDATE blocked_pages SET checked_at=? WHERE site=?", (old, SITE))
        self.scan([card(4, 10)], spot)
        self.assertEqual(len(self.calls(DEAD_SLUG)), 2)
        self.assertGreater(db.query_one("SELECT checked_at FROM blocked_pages WHERE source_key=?", (DEAD_KEY,))["checked_at"], old)

    def test_only_this_sites_source_leaves_a_title_another_site_serves(self):
        self.scan([card(4, 10)], [spotlight(DEAD_SLUG, "The Blacklist")])
        # rebuild: the title exists with a second site's source, then yabancidizi links a dead page for it
        db.execute("DELETE FROM blocked_pages")
        self.scan([card(4, 10)])
        cid = db.query_one("SELECT canonical_id FROM source_items WHERE source_key=?", (SNW_KEY,))["canonical_id"]
        db.execute("INSERT INTO source_items(source,source_key,canonical_id,raw,normalized,fetched_at) VALUES (?,?,?,?,?,?)",
                   ("othersite", "dizi/snw", cid, "{}", json.dumps({"type": "series", "title": "SNW"}), 1))
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,updated_at) VALUES (?,?,?,?,?,?,?)",
                   ("other1", cid, "othersite", "dizi/snw", "episode", "https://other.example/snw/1/1", 1))
        gate.block_dead(SITE, SNW_KEY, "https://yabancidizi.news/" + SNW_KEY, {}, gate.VIA_GONE, "test")
        self.scan([card(4, 10)])
        self.assertIsNone(db.query_one("SELECT * FROM source_items WHERE source=? AND source_key=?", (SITE, SNW_KEY)))
        self.assertEqual(db.query("SELECT source FROM video_sources WHERE canonical_id=?", (cid,))[0]["source"], "othersite")
        self.assertTrue(db.query_one("SELECT * FROM library_items WHERE id=?", (cid,)))   # the title stays (other site)
        self.assertTrue(db.query_one("SELECT * FROM source_items WHERE source='othersite'"))


class ArchiveRetryTests(unittest.TestCase):
    def test_a_blocked_archive_entry_is_picked_again_after_the_window(self):
        from app.library import archive
        now = time.time()
        entry = {"st": "blocked", "at": int(now - 3600)}
        self.assertFalse(archive._pickable(entry, now, 3))
        entry["at"] = int(now - config.BLOCKED_RECHECK_DAYS * 86400 - 1)
        self.assertTrue(archive._pickable(entry, now, 3))


if __name__ == "__main__":
    unittest.main()
