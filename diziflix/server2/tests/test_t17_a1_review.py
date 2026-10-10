import _sandbox  # noqa: F401  (must be first)

import re
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main
from app.control.crawl import frontier
from app.control.jobs import store as job_store
from app.control.sitemap import store as map_store
from app.control.stages import actions, brief, engine, status
from app.core.extract import urlpatterns

from test_t08_stages import A, Case, html

EP_LIST = "/yeni-eklenenler/"          # a menu list of EPISODE cards (A1 proposes it as a title list)
ARCHIVE = "/dizi-arsivi-01/"           # the real title archive (not in the menu; the admin adds it)


def _home(extra: str = "") -> str:
    li = f'<li><a href="{EP_LIST}">Yeni Eklenenler</a></li>' + extra
    return html("fakesite/home.html").replace('<li><a href="/tur/aksiyon/">', li + '<li><a href="/tur/aksiyon/">', 1)


def _archive(page: int) -> str:
    out = html("fakesite/listing_aksiyon.html").replace("/film/aksiyon-film-", f"/dizi/arsiv-{page}-")
    pager = " ".join(f'<a href="/dizi-arsivi-{n:02d}/">{n}</a>' for n in (1, 2, 3) if n != page)
    return re.sub(r'<div class="pager">.*?</div>', f'<div class="pager"><span>{page}</span> {pager}</div>', out, flags=re.S)


def _episodes() -> str:
    cards = "".join(f'<div class="movie-box"><a class="poster" href="/dizi-{i}-1-sezon-{i}-bolum/"><img data-src="https://img.'
                    f'fakesite.test/e{i}.jpg" alt="b"></a><h3 class="card-name"><a href="/dizi-{i}-1-sezon-{i}-bolum/">Dizi {i} '
                    f'{i}. Bölüm</a></h3></div>' for i in range(1, 11))
    return f'<html><body><main><div class="movie-list">{cards}</div></main></body></html>'


class ReviewTests(Case):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(engine, "A1_REVIEW", True)
        p.start()
        self.addCleanup(p.stop)
        self.overrides.update({A + "/": _home(), A + EP_LIST: _episodes(), A + ARCHIVE: _archive(1),
                               A + "/dizi-arsivi-02/": _archive(2), A + "/dizi-arsivi-03/": _archive(3)})

    def started(self):
        sid = self.site(A)
        actions.start(sid)
        self.drain()
        return sid

    def confirm(self, sid, links=None, added=None, sections=None):
        return actions.review(sid, "A1", {"links": links or [], "added": added or [], "sections": sections or []})

    def listing_seeds(self, sid):
        return {r["url"] for r in db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='listing' "
                                           "AND parent_url IS NULL", (sid,))}

    def test_a1_pass_waits_for_review_and_a_foreign_added_address_is_400(self):
        sid = self.started()
        a1 = status.overview(sid)["stages"][0]
        self.assertEqual(a1["state"], "review")
        self.assertEqual(map_store.get(sid)["status"], "draft")
        self.assertEqual(self.st(sid, "A2")["state"], "pending")
        self.assertIsNone(job_store.get_by_key("refresh:" + sid))
        self.assertEqual(job_store.active(sid), [])
        self.assertNotIn("home", map_store.draft(sid).get("pages") or {})    # nothing reaches the draft before the answer
        rv = a1["review"]
        self.assertEqual([s["proposed"] for s in rv["sections"]], ["episode", "title", "title"])
        self.assertEqual(len(rv["sections"][1]["samples"]), 3)
        self.assertIn(EP_LIST, [l["url"] for l in rv["links"]])
        self.assertIn("review", a1["actions"])
        client = TestClient(main.app)
        r = client.post(f"/api/control/sites/{sid}/stages/A1/review",
                        json={"added": [{"name": "x", "url": "https://other.test/arsiv/", "meaning": "series_list"}]})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.st(sid, "A1")["state"], "review")

    def test_confirmed_labels_drive_a2_and_the_k1_seeds(self):
        sid = self.started()
        self.confirm(sid, links=[{"url": EP_LIST, "meaning": "episode_list"}],
                     added=[{"name": "Dizi Arşivi", "url": ARCHIVE, "meaning": "series_list"}])
        self.drain(40)
        for stage in ("A1", "A2", "A3"):
            self.assertIn(self.st(sid, stage)["state"], ("passed", "passed_warn"), stage)
        self.assertEqual(map_store.get(sid)["status"], "active")
        draft = map_store.draft(sid)
        links = {l["url"]: l for l in draft["pages"]["home"]["nav"]["links"]}
        self.assertEqual((links[EP_LIST]["content"], links[ARCHIVE]["content"]), ("episode", "title"))
        self.assertTrue(links[ARCHIVE]["manual"] and draft["meta"]["home"]["reviewed"])
        listing = draft["pages"]["listing"]
        self.assertEqual(listing["listings"][0]["url"], ARCHIVE)                 # archive first
        self.assertNotIn(EP_LIST, [l["url"] for l in listing["listings"]])
        self.assertEqual(listing["pagination"], {"kind": "page_param", "template": "-{n:02}/"})
        seeds = self.listing_seeds(sid)
        self.assertIn(A + ARCHIVE, seeds)
        self.assertNotIn(A + EP_LIST, seeds)
        self.assertIsNone(frontier.get(sid, A + EP_LIST))
        self.assertIsNotNone(frontier.get(sid, A + "/dizi-arsivi-02/"))           # the archive's next page was followed
        self.assertIn(f"{EP_LIST}: episode_list", brief.brief(sid))

    def test_rerun_asks_only_about_new_links(self):
        sid = self.started()
        self.confirm(sid)
        self.drain(40)
        actions.rerun(sid, "A1")
        self.drain(5)
        self.assertEqual(self.st(sid, "A1")["state"], "passed")                 # nothing new: no question
        self.overrides[A + "/"] = _home('<li><a href="/yeni-liste/">Yeni Liste</a></li>')
        db.execute("DELETE FROM sample_pages WHERE url=?", (A + "/",))   # the stage page cache (1 h)
        actions.rerun(sid, "A1")
        self.drain(5)
        rv = status.overview(sid)["stages"][0]["review"]
        self.assertEqual(([s["id"] for s in rv["sections"]], [l["url"] for l in rv["links"]]), ([], ["/yeni-liste/"]))

    def test_confirm_on_an_existing_site_drops_the_ignored_lists_rows(self):
        with mock.patch.object(engine, "A1_REVIEW", False):                    # a site onboarded before the review
            sid = self.started()
            self.drain(40)
        self.assertIn(A + EP_LIST, self.listing_seeds(sid))
        frontier.add(sid, A + "/dizi/sadece-bu-listede/", "title", priority=frontier.K1, parent_url=A + EP_LIST,
                     origin=("listing", "Yeni Eklenenler"))
        actions.rerun(sid, "A1")
        self.drain(5)
        self.assertEqual(self.st(sid, "A1")["state"], "review")
        out = self.confirm(sid, links=[{"url": EP_LIST, "meaning": "ignore"}])
        self.assertGreaterEqual(out["dropped"], 2)
        self.assertIsNone(frontier.get(sid, A + EP_LIST))
        self.assertIsNone(frontier.get(sid, A + "/dizi/sadece-bu-listede/"))
        self.assertIsNotNone(frontier.get(sid, A + "/dizi/kara-orman/"))            # home section rows stay
        self.assertEqual(map_store.versions(sid)[-1]["created_by"], "edit")


class TailPaginationTests(Case):
    def test_end_number_pagination(self):
        rule = urlpatterns.pagination(_archive(1), A + ARCHIVE)
        self.assertEqual(rule["template"], "-{n:02}/")
        self.assertEqual(urlpatterns.page_url_of(rule, A + "/dizi-arsivi-02/", 3), A + "/dizi-arsivi-03/")
        self.assertIsNone(urlpatterns.page_url_of(rule, A + "/tur/dram/", 2))
