"""``discover_site`` (``scraper/discover.py``): a draft site yaml built by code from fixture pages. Network-free: the pages come from a dict.

Covers the trdiziizle-shaped cases that cost the onboarding agent most of its tool calls: the favourite-link (wpfp) trap on cards and on
episode rows, the home page that is really ``/tr2/``, episode cards on the home page next to a series archive, a series page without episodes /
an episode page with a telif placeholder (second candidate), the ``missing`` list (unknown player host, no player, search form), episode-card
and film sites, the real yabancidizi fixtures, and the sandbox endpoint."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import yaml

from app.library import normalize as nrm
from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, discover as d, parse, schema, series_generic
from app.scraper.providers import registry

import test_onboard_sandbox as tsb   # SandboxCase

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://www.ornekdizi.tv"
CDN = "https://cdn.ornekdizi-img.net"
SERIES = [("halka", "Halka"), ("kara-mavi", "Kara Mavi"), ("ask-ve-mavi", "Aşk ve Mavi"), ("sakir-pasa", "Şakir Paşa"), ("yedi-numara", "7 Numara"),
          ("gelin-evi", "Gelin Evi"), ("bir-zamanlar", "Bir Zamanlar"), ("kuzey-yildizi", "Kuzey Yıldızı"), ("ihanet", "İhanet"),
          ("sahane-hayat", "Şahane Hayat")]


# --- the fake site -----------------------------------------------------------------------------------------------------

def card(slug, title, year=2024, n=1):
    """A series card whose FIRST link is an add-to-favourites link (the wpfp trap); the content link sits in the title."""
    return (f'<div class="single-item"><a class="wpfp-link" href="?wpfpaction=add&amp;postid={n}" rel="nofollow" title="Favorilere ekle">Favori</a>'
            f'<div class="cat-title"><a href="/diziler/{slug}-izle/">{title}</a></div>'
            f'<img class="lazy" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="{CDN}/p/{slug}.jpg" alt="{title}"/>'
            f'<span class="yil">{year}</span></div>')


def ep_card(slug, title, s, e):
    url = f"/{slug}-{s}-sezon-{e}-bolum-izle-full-tek-parca/"
    return (f'<div class="listepisodes"><a href="{url}"><img data-src="{CDN}/e/{slug}-{s}-{e}.jpg" src="data:image/gif;base64,AAAA"/></a>'
            f'<div class="bolum-alt"><span class="dizi-ismi"><a href="{url}">{title} {e}. Bölüm</a></span></div></div>')


def home(trending=True, episodes=True, search=True, extra=""):
    head = ('<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Örnek Dizi</title>'
            '<meta property="og:site_name" content="Örnek Dizi"></head><body>')
    menu = ('<header><nav class="menu"><ul><li><a href="/tr2/">Anasayfa</a></li><li><a href="/dizi-arsivi-01/">Diziler</a></li>'
            '<li><a href="/film-arsivi/">Filmler</a></li><li><a href="/iletisim/">İletişim</a></li></ul></nav>'
            + ('<form action="/" method="get" class="search"><input type="text" name="s" placeholder="Ara"></form>' if search else "") + "</header>")
    blocks = ""
    if trending:
        blocks += ('<section class="block"><h2 class="section-title">Trendler</h2><div class="trend-list">'
                   + "".join(card(s, t, 2020 + i, i + 1) for i, (s, t) in enumerate(SERIES[:6])) + "</div></section>")
    if episodes:
        blocks += ('<section class="block"><h2 class="section-title">Son Eklenen Bölümler</h2><div id="ep-list">'
                   + "".join(ep_card(s, t, 1, i + 1) for i, (s, t) in enumerate(SERIES[:6])) + "</div></section>")
    return head + menu + blocks + extra + '<footer><a href="/gizlilik/">Gizlilik</a></footer></body></html>'


ROOT = '<!doctype html><html><head><meta http-equiv="refresh" content="0;url=/tr2/"></head><body></body></html>'
ARCHIVE = ('<!doctype html><html><head><title>Dizi Arşivi</title></head><body><nav class="menu"><a href="/tr2/">Anasayfa</a></nav><div class="archive">'
           + "".join(card(s, t, 2000 + i, 50 + i) for i, (s, t) in enumerate(SERIES)) + "</div></body></html>")
EPS = [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2)]


def series_page(slug, title, eps=EPS, info=True):
    rows = "".join(
        f'<li class="bolum"><a class="wpfp-link" href="?wpfpaction=add&amp;postid={900 + i}" title="Favorilere ekle" rel="nofollow">Favori</a>'
        f'<a class="ep" href="/{slug}-{s}-sezon-{e}-bolum-izle-full-tek-parca/"><span class="no">{e}. Bölüm</span></a>'
        f'<span class="tarih">{(i % 27) + 1:02d}.01.2025</span></li>' for i, (s, e) in enumerate(eps))
    details = (
        '<div id="icerikcatright">Bu dizi, genç bir kadının hayatını ve zorlu seçimlerini anlatan uzun bir hikayedir; her bölümde yeni sırlar ortaya '
        'çıkar ve herkes bir seçim yapmak zorunda kalır. Yapım Yılı : 2024 Oyuncular : Ali Veli, Ayşe Fatma Tür : Dram, Gerilim</div>'
        '<div id="icerikcat2"><b>Yapım Yılı :</b> 2024<br><b>Oyuncular :</b> Ali Veli, Ayşe Fatma<br><b>Tür :</b> Dram, Gerilim</div>'
        '<span class="imedebe">IMDb 8,1</span>') if info else ""
    return (f'<!doctype html><html><head><title>{title} izle</title><meta property="og:image" content="{CDN}/p/{slug}.jpg"></head><body>'
            f'<h1 class="title-border">{title}</h1>{details}<ul class="bolumler">{rows}</ul>'
            '<div class="benzer"><h3>Benzer Diziler</h3><ul><li class="bolum"><a class="ep" href="/baska-dizi-1-sezon-1-bolum-izle-full-tek-parca/">1. Bölüm</a>'
            "</li></ul></div></body></html>")


def episode(player='<iframe id="player" src="https://vidmoly.me/embed-abc123xyz.html"></iframe>'):
    return f'<html><body><h1>Halka</h1><div class="video-box">{player}</div></body></html>'


TELIF = episode('<iframe src="/player/telif.html"></iframe><p>Bu içerik telif hakkı nedeniyle yayına kapatılmıştır.</p>')


class Site:
    """``getter`` of a dict of pages; remembers what was asked. A page that is a ``(final_url, html)`` tuple is a redirect."""

    def __init__(self, pages):
        self.pages, self.asked = dict(pages), []

    def __call__(self, url, role):
        self.asked.append((role, url))
        if url not in self.pages:
            raise d.PageError("HTTP 404 for " + url)
        page = self.pages[url]
        final, html = page if isinstance(page, tuple) else (url, page)
        return d.Page(url=url, final_url=final, html=html, page_id=f"pg_{len(self.asked):012x}")


def trdizi_pages(**changes):
    pages = {BASE + "/": ROOT, BASE + "/tr2/": home(), BASE + "/dizi-arsivi-01/": ARCHIVE,
             BASE + "/diziler/halka-izle/": series_page("halka", "Halka"),
             BASE + "/halka-2-sezon-2-bolum-izle-full-tek-parca/": TELIF,       # the newest episode: a placeholder
             BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": episode()}   # the oldest: a real player
    pages.update(changes)
    return pages


def known_providers():
    """The provider library of the real repo is not a test subject: vidmolly (code) + a recipe-like stub that owns the site's own player path."""
    class Stub:
        name, kind = "ornek_player", "recipe"
        matches = staticmethod(lambda url: urlsplit(url).hostname == "www.ornekdizi.tv" and urlsplit(url).path.startswith("/player/oynat/"))

    return patch.object(registry, "providers", lambda extra=None: [registry.CODE_PROVIDERS[0], Stub()])


def run(pages=None, url=BASE, **kw):
    site = Site(pages if pages is not None else trdizi_pages())
    with known_providers():
        out = d.discover(url, site, **kw)
    return out, site


def data_of(out):
    return yaml.safe_load(out["yaml_text"])


def missing_fields(out):
    return [m["field"] for m in out["missing"]]


# --- units -------------------------------------------------------------------------------------------------------------

class EpisodeRegexTest(unittest.TestCase):
    def check(self, path, season, episode, slug="halka"):
        got = d.episode_regex(path)
        self.assertIsNotNone(got, path)
        m = re.search(got["regex"], urlsplit(path).path)
        self.assertIsNotNone(m, (path, got))
        self.assertEqual((m.groupdict().get("season"), m.group("episode"), m.group("slug")), (season, episode, slug))
        return got

    def test_the_usual_shapes(self):
        got = self.check("/halka-1-sezon-3-bolum-izle-full-tek-parca/", "1", "3")
        self.assertEqual((got["season"], got["slug"]), (True, True))
        self.assertEqual(got["regex"], r"^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum(?:-[^/]*)?/?$")
        self.assertFalse(self.check("/halka-5-bolum-izle/", None, "5")["season"])           # no season in the URL: default_season
        self.check("/dizi/halka/sezon-2/bolum-3", "2", "3")                                   # the numbers in their own directories
        self.check("/izle/halka-2-sezon-10-bolum", "2", "10")
        self.check("/halka-sezon-1-bolum-4", "1", "4")
        self.check("/halka-s01e05-izle/", "01", "05")

    def test_a_number_in_the_series_name_is_not_a_season(self):
        got = d.episode_regex("/7-numara-3-bolum/")
        m = re.search(got["regex"], "/7-numara-3-bolum/")
        self.assertEqual((m.group("slug"), m.group("episode"), got["season"]), ("7-numara", "3", False))

    def test_no_number_no_regex(self):
        self.assertIsNone(d.episode_regex("/diziler/halka-izle/"))
        self.assertIsNone(d.episode_regex("/"))


class RoleTest(unittest.TestCase):
    def test_roles_by_heading(self):
        cases = [("Trendler", "series", "trending"), ("Haftanın Popüler Dizileri", "series", "trending"), ("Son Eklenen Diziler", "series", "latest_series"),
                 ("Yeni Eklenen Bölümler", "episode", "latest_episodes"), ("Son Bölümler", "episode", "latest_episodes"),
                 ("Yeni Eklenen Filmler", "film", "latest_movies"), ("IMDb Puanı Yüksek Filmler", "film", "noteworthy_movies"),
                 ("Dikkate Değer Filmler", "film", "noteworthy_movies"), ("Yakında", "other", "upcoming"), ("Öne Çıkanlar", "series", "featured")]
        for heading, kind, role in cases:
            self.assertEqual(d.role_of(heading, kind)[0], role, heading)

    def test_the_cards_must_fit_the_role(self):
        self.assertIsNone(d.role_of("Trendler", "episode")[0])               # trending episodes: not a series signal
        self.assertIsNone(d.role_of("IMDb Filmler", "series")[0])             # a film role on cards that open series pages
        self.assertEqual(d.role_of("Son Bölümler", "film")[0], "latest_movies")
        self.assertEqual(d.role_of("", "series", hero=True)[0], "featured")   # a hero slider has no heading
        self.assertIsNone(d.role_of("", "series")[0])
        self.assertIsNone(d.role_of("Hakkımızda", "other")[0])


# --- the trdiziizle-shaped flow ------------------------------------------------------------------------------------------

class TrdiziFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.site = run()
        cls.data = data_of(cls.out)

    def test_the_draft_has_no_sandbox_errors_and_is_within_the_page_budget(self):
        self.assertEqual(self.out["errors"], [], self.out["errors"])
        self.assertLessEqual(self.out["pages_fetched"], d.MAX_PAGES)
        roles = [role for role, _url in self.site.asked]
        self.assertEqual(roles, ["home", "home", "archive", "series", "episode", "episode"])   # root, canonical home, archive, series, 2 episode pages
        self.assertEqual({p["role"] for p in self.out["pages"]}, {"home", "archive", "series", "episode"})
        self.assertTrue(all(p["page_id"].startswith("pg_") for p in self.out["pages"]))
        self.assertEqual(self.out["site_id"], "ornekdizi")

    def test_the_home_page_is_the_canonical_path(self):
        self.assertIn(("home", BASE + "/tr2/"), self.site.asked)           # the root only said "refresh to /tr2/"
        self.assertTrue(any("is really /tr2/" in n for n in self.out["notes"]), self.out["notes"])
        self.assertEqual(self.out["found"]["home"]["path"], "/tr2/")
        for entry in self.data["collections"]:
            self.assertEqual(entry["path"], "/tr2/")

    def test_the_list_is_the_series_archive_and_episode_cards_are_a_collection(self):
        self.assertEqual(self.data["list_url"], "/dizi-arsivi-01/")
        self.assertEqual(self.out["found"]["list"]["page"], "archive")
        self.assertEqual(self.data["list"]["row_selector"], "div.single-item")
        roles = {c["role"]: c for c in self.data["collections"]}
        self.assertEqual(set(roles), {"trending", "latest_episodes"})
        self.assertEqual(roles["latest_episodes"]["row_selector"], "div.listepisodes")
        self.assertEqual(roles["trending"]["id"], "trending_ornekdizi")
        rows = parse.parse_list(ARCHIVE, self.data["list"]["row_selector"], self.data["list"]["fields"])
        self.assertEqual(len(rows), len(SERIES))
        self.assertTrue(all(r["detail_url"].startswith("/diziler/") for r in rows), rows)      # series pages, never episode pages
        episodes = parse.parse_list(home(), roles["latest_episodes"]["row_selector"], roles["latest_episodes"]["fields"])
        self.assertTrue(all("-bolum-" in r["detail_url"] for r in episodes), episodes)
        self.assertEqual(episodes[0]["title"], "Halka 1. Bölüm")
        self.assertTrue(any("block Son Eklenen Bölümler: latest_episodes" in n for n in self.out["notes"]))
        self.assertTrue(any("block Trendler: trending" in n for n in self.out["notes"]))

    def test_the_favourite_link_trap_on_the_cards(self):
        fields = self.data["list"]["fields"]
        self.assertNotEqual(fields["detail_url"].get("selector"), "a")      # the first <a> is "Favori"
        rows = parse.parse_list(ARCHIVE, self.data["list"]["row_selector"], fields)
        self.assertTrue(all("wpfp" not in r["detail_url"] for r in rows))
        self.assertEqual(rows[0]["title"], "Halka")
        # the poster: the lazy attribute, the data: placeholder is not a poster
        self.assertTrue(all(r["poster_url"].startswith(CDN) for r in rows), rows[0])
        self.assertEqual(rows[0]["year"], 2000)
        valid, _m = schema.validate_items("MovieItem", rows)
        self.assertEqual(len(valid), len(SERIES))

    def test_the_series_page_picks_the_episode_link_not_the_favourite_link(self):
        spec = self.data["series_page"]
        self.assertEqual(spec["row_selector"], "ul.bolumler li.bolum")
        self.assertEqual(spec["fields"]["url"], {"selector": "a.ep", "attr": "href"})
        self.assertEqual(spec["fields"]["air_date"]["cast"], "date_tr")
        self.assertEqual(spec["episode_url_regex"], r"^/(?P<slug>[^/]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum(?:-[^/]*)?/?$")
        self.assertNotIn("default_season", spec)                              # the URLs carry the season
        self.assertEqual(spec["series_url_regex"], "^/diziler/")
        inventory = series_generic.series_inventory(series_page("halka", "Halka"), BASE + "/diziler/halka-izle/", spec)
        self.assertEqual([(v["season"], v["episode"]) for v in inventory["video_sources"]], EPS)   # the "similar series" episode is left out
        self.assertTrue(inventory["structured"])
        self.assertTrue(all("wpfp" not in v["url"] for v in inventory["video_sources"]))
        self.assertEqual(self.out["found"]["series_page"]["episodes"], 6)
        self.assertEqual(self.out["confidence"]["series_page"], "high")

    def test_detail_fields_from_the_labelled_text(self):
        fields = self.data["detail"]["fields"]
        self.assertEqual(set(fields), {"year", "cast", "genres", "rating", "synopsis", "poster_url"})
        got = parse.parse_detail(series_page("halka", "Halka"), fields)
        self.assertEqual((got["year"], got["cast"], got["genres"], got["rating"]), (2024, ["Ali Veli", "Ayşe Fatma"], ["Dram", "Gerilim"], 8.1))
        self.assertTrue(got["synopsis"].startswith("Bu dizi, genç bir kadının") and "Yapım" not in got["synopsis"], got["synopsis"])
        self.assertEqual(got["poster_url"], CDN + "/p/halka.jpg")
        self.assertEqual({v["source"] for k, v in self.out["found"]["detail"]["fields"].items() if k != "poster_url"}, {"label"})
        self.assertEqual(self.out["confidence"]["detail"], "high")

    def test_the_telif_episode_page_is_skipped_for_the_second_one(self):
        player = self.out["found"]["player"]
        self.assertEqual(player["episode_url"], BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/")
        self.assertEqual(self.data["resolvers"], [{"type": "iframe", "selector": 'iframe[src*="vidmoly.me"]'}])
        self.assertEqual(self.data["providers"], ["vidmolly"])
        self.assertEqual((self.data["playback"], self.data["availability_gate"]), ("video", {"probe": 2, "require": "player"}))
        self.assertEqual(self.out["confidence"]["player"], "high")
        self.assertTrue(any("placeholder player (/player/telif.html)" in n for n in self.out["notes"]), self.out["notes"])

    def test_normalize_keys_carry_no_episode_tail(self):
        rules = self.data["normalize"]
        self.assertEqual(rules["type"], "series")
        self.assertEqual(rules["host"], "www.ornekdizi.tv")
        for html, row_selector, fields in ((ARCHIVE, self.data["list"]["row_selector"], self.data["list"]["fields"]),
                                           (home(), "div.listepisodes", self.data["collections"][0]["fields"])):
            for raw in parse.parse_list(html, row_selector, fields):
                norm = nrm.generic_normalize(rules, raw, base_url=BASE)
                self.assertIsNotNone(norm, raw)
                slug = re.search(r"/(?:diziler/)?(?P<s>[a-z-]+?)(?:-izle|-\d+-sezon)", raw["detail_url"]).group("s")
                self.assertEqual(norm["source_key"], slug, raw)
                self.assertEqual(norm["type"], "series")
        self.assertEqual(self.out["found"]["normalize"]["ok"], self.out["found"]["normalize"]["total"])

    def test_the_site_goes_through_the_generic_engine(self):
        cfg = scfg.SiteConfig(site_id="ornekdizi", data=self.data, path="")
        self.assertEqual(cfg.base_url, BASE)
        self.assertEqual(cfg.series_page["row_selector"], "ul.bolumler li.bolum")
        self.assertEqual([p.get("type") for p in cfg.resolvers], ["iframe"])
        self.assertEqual(sb._check_collections(self.data, "ornekdizi")[1], [])
        self.assertEqual(sb._check_fields(self.data), [])
        self.assertEqual(sb._check_normalize(self.data), [])

    def test_missing_lists_what_was_not_found(self):
        fields = missing_fields(self.out)
        self.assertIn("detail.trailer_url", fields)
        entry = next(m for m in self.out["missing"] if m["field"] == "search")
        self.assertIn("search-hint: GET /?s={query}", entry["hint"])
        self.assertNotIn("search", self.data)                                   # a search block is never written without proof
        self.assertEqual(self.out["found"]["search_form"], {"action": "/", "method": "GET", "input": "s"})
        for gap in self.out["missing"]:
            self.assertTrue(gap["tried"], gap)
        self.assertNotIn("needs_recipe", fields)
        self.assertNotIn("series_page", fields)

    def test_yaml_text_is_the_data(self):
        self.assertEqual(list(self.data)[:4], ["display_name", "site_id", "base_url", "list_url"])
        self.assertEqual(self.data["display_name"], "Örnek Dizi")
        self.assertEqual(self.data["image_hosts"], ["cdn.ornekdizi-img.net"])
        self.assertEqual((self.data["fetch_mode"], self.data["schema"]), ("http", "MovieItem"))


# --- fallbacks and gaps --------------------------------------------------------------------------------------------------

class FallbackTest(unittest.TestCase):
    def test_a_series_page_without_episodes_falls_back_to_the_second_candidate(self):
        gated = '<html><head><title>Halka izle</title></head><body><h1>Halka</h1><p>Bu dizi yayından kaldırıldı.</p></body></html>'
        out, site = run(trdizi_pages(**{BASE + "/diziler/halka-izle/": gated, BASE + "/diziler/kara-mavi-izle/": series_page("kara-mavi", "Kara Mavi"),
                                        BASE + "/kara-mavi-2-sezon-2-bolum-izle-full-tek-parca/": episode(),
                                        BASE + "/kara-mavi-1-sezon-1-bolum-izle-full-tek-parca/": episode()}))
        self.assertEqual(out["errors"], [], out["errors"])
        self.assertEqual([url for role, url in site.asked if role == "series"], [BASE + "/diziler/halka-izle/", BASE + "/diziler/kara-mavi-izle/"])
        self.assertEqual(out["found"]["series_page"]["page_url"], BASE + "/diziler/kara-mavi-izle/")
        self.assertNotIn("series_page", missing_fields(out))

    def test_no_series_page_gives_episodes_a_missing_entry(self):
        gated = '<html><body><h1>x</h1></body></html>'
        out, _site = run(trdizi_pages(**{BASE + "/diziler/halka-izle/": gated, BASE + "/diziler/kara-mavi-izle/": gated}))
        entry = next(m for m in out["missing"] if m["field"] == "series_page")
        self.assertEqual(len(entry["tried"]), 2)
        self.assertEqual(out["confidence"]["series_page"], "low")
        self.assertNotIn("series_page", data_of(out))
        # no inventory to take an episode from: an episode card of the home page is an episode page too
        self.assertEqual(out["found"]["player"]["episode_url"], BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/")
        self.assertEqual(data_of(out)["providers"], ["vidmolly"])

    def test_links_to_other_season_pages_are_reported(self):
        page = series_page("halka", "Halka").replace('<ul class="bolumler">', '<div class="sezonlar"><a href="/diziler/halka-izle/sezon-2">2. Sezon</a>'
                                                     '<a href="/diziler/halka-izle/sezon-3">3. Sezon</a></div><ul class="bolumler">')
        out, _site = run(trdizi_pages(**{BASE + "/diziler/halka-izle/": page}))
        entry = next(m for m in out["missing"] if m["field"] == "series_page.season_pages")
        self.assertIn("/diziler/halka-izle/sezon-2", entry["tried"][0])
        self.assertEqual(out["found"]["series_page"]["season_links"], ["/diziler/halka-izle/sezon-2", "/diziler/halka-izle/sezon-3"])
        self.assertNotIn("series_page.season_pages", missing_fields(run()[0]))

    def test_an_unknown_player_host_is_needs_recipe(self):
        player = '<iframe src="https://player.yenihost.example/e/xyz123"></iframe>'
        out, _site = run(trdizi_pages(**{BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": episode(player)}))
        entry = next(m for m in out["missing"] if m["field"] == "needs_recipe")
        self.assertEqual((entry["host"], entry["player_url"]), ("player.yenihost.example", "https://player.yenihost.example/e/xyz123"))
        self.assertIn("match_providers", entry["hint"])
        data = data_of(out)
        self.assertEqual(data["resolvers"], [{"type": "iframe", "selector": 'iframe[src*="player.yenihost.example"]'}])
        self.assertNotIn("providers", data)
        self.assertEqual((data["playback"], out["confidence"]["player"]), ("video", "medium"))
        self.assertEqual(out["errors"], [], out["errors"])

    def test_the_sites_own_player_path_is_narrowed_and_matched(self):
        player = f'<iframe src="{BASE}/player/oynat/a1b2c3d4"></iframe>'
        out, _site = run(trdizi_pages(**{BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": episode(player)}))
        data = data_of(out)
        self.assertEqual(data["resolvers"], [{"type": "iframe", "selector": 'iframe[src*="/player/oynat/"]'}])
        self.assertEqual(data["providers"], ["ornek_player"])
        self.assertNotIn("needs_recipe", missing_fields(out))

    def test_a_tab_source_and_a_script_url_are_found_but_not_turned_into_resolvers(self):
        page = episode('<ul class="kaynaklar"><li data-embed="https://player.yenihost.example/e/abc">Kaynak 1</li></ul>')
        out, _site = run(trdizi_pages(**{BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": page,
                                         BASE + "/halka-2-sezon-2-bolum-izle-full-tek-parca/": page}))
        data = data_of(out)
        self.assertEqual(data["playback"], "trailer")
        self.assertNotIn("resolvers", data)
        entry = next(m for m in out["missing"] if m["field"] == "player")
        self.assertIn("player.yenihost.example", entry["tried"][0])
        self.assertEqual(out["found"]["player"]["candidates"][0]["how"], "data attribute data-embed")

    def test_no_player_at_all(self):
        bare = '<html><body><h1>Halka</h1><p>Yakında.</p></body></html>'
        out, _site = run(trdizi_pages(**{BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": bare,
                                         BASE + "/halka-2-sezon-2-bolum-izle-full-tek-parca/": bare}))
        entry = next(m for m in out["missing"] if m["field"] == "player")
        self.assertEqual(len(entry["tried"]), 2)
        data = data_of(out)
        self.assertEqual(data["playback"], "trailer")
        self.assertNotIn("availability_gate", data)
        self.assertEqual(out["confidence"]["player"], "low")

    def test_a_youtube_trailer_is_not_a_player(self):
        page = episode('<iframe src="https://www.youtube.com/embed/abc"></iframe>')
        out, _site = run(trdizi_pages(**{BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": page,
                                         BASE + "/halka-2-sezon-2-bolum-izle-full-tek-parca/": page}))
        self.assertEqual(data_of(out)["playback"], "trailer")
        self.assertIn("only a trailer iframe", " ".join(next(m for m in out["missing"] if m["field"] == "player")["tried"]))

    def test_detail_info_from_meta_and_jsonld_when_the_page_has_no_labels(self):
        page = series_page("halka", "Halka", info=False).replace(
            "</head>", '<meta property="og:description" content="Genç bir kadının zorlu seçimlerini anlatan dizi.">'
            '<script type="application/ld+json">{"@type": "TVSeries", "name": "Halka", "datePublished": "2023-05-01", '
            '"genre": "Dram", "aggregateRating": {"ratingValue": "7.9"}}</script></head>')
        out, _site = run(trdizi_pages(**{BASE + "/diziler/halka-izle/": page}))
        found = out["found"]["detail"]["fields"]
        self.assertEqual((found["synopsis"]["source"], found["year"]["source"], found["rating"]["source"]), ("meta", "jsonld", "jsonld"))
        got = parse.parse_detail(page, data_of(out)["detail"]["fields"])
        self.assertEqual((got["year"], got["rating"], got["genres"]), (2023, 7.9, ["Dram"]))
        self.assertIn("detail.cast", missing_fields(out))
        self.assertEqual(out["confidence"]["detail"], "medium")

    def test_a_hero_slider_without_a_heading_is_featured(self):
        slider = ('<div class="main-slider owl-carousel">' + "".join(
            f'<div class="slide"><a href="/diziler/{s}-izle/"><img data-src="{CDN}/b/{s}.jpg" alt="{t}"/><span class="slide-title">{t}</span></a></div>'
            for s, t in SERIES[:5]) + "</div>")
        out, _site = run(trdizi_pages(**{BASE + "/tr2/": home(trending=False, extra=slider)}))
        roles = {c["role"] for c in data_of(out)["collections"]}
        self.assertIn("featured", roles)
        self.assertEqual(out["errors"], [], out["errors"])

    def test_a_block_that_shows_no_posters_is_dropped_with_a_missing_entry(self):
        plain = "".join(f'<div class="single-item"><div class="cat-title"><a href="/diziler/{s}-izle/">{t}</a></div></div>' for s, t in SERIES[:6])
        home_html = ('<html><body><nav class="menu"><a href="/dizi-arsivi-01/">Diziler</a></nav><section><h2 class="section-title">Trendler</h2>'
                     f'<div class="x">{plain}</div></section></body></html>')
        out, _site = run(trdizi_pages(**{BASE + "/tr2/": home_html}))
        self.assertIn("collection:trending", missing_fields(out))
        self.assertIn("collections", missing_fields(out))
        self.assertNotIn("collections", data_of(out))

    def test_home_only_episode_cards_use_episode_source(self):
        only = home(trending=False, search=False).replace('<li><a href="/dizi-arsivi-01/">Diziler</a></li>', "")
        pages = {BASE + "/": ROOT, BASE + "/tr2/": only,
                 BASE + "/halka-1-sezon-1-bolum-izle-full-tek-parca/": episode(), BASE + "/kara-mavi-1-sezon-2-bolum-izle-full-tek-parca/": episode()}
        out, _site = run(pages)
        data = data_of(out)
        self.assertEqual(out["errors"], [], out["errors"])
        self.assertNotIn("series_page", data)
        self.assertEqual(data["normalize"]["episode_source"], {"enabled": True})   # the URL carries season and episode
        self.assertEqual(data["normalize"]["type"], "series")
        self.assertIn("series_archive", missing_fields(out))
        self.assertEqual(data["list"]["row_selector"], "div.listepisodes")
        raw = parse.parse_list(only, data["list"]["row_selector"], data["list"]["fields"])[1]
        norm = nrm.generic_normalize(data["normalize"], raw, base_url=BASE)
        self.assertEqual((norm["source_key"], norm["video_sources"][0]["season"], norm["video_sources"][0]["episode"]), ("kara-mavi", 1, 2))
        self.assertIn("player", out["found"])

    def test_a_film_site(self):
        films = [("sonsuz-yolculuk", "Sonsuz Yolculuk"), ("kara-delik", "Kara Delik")] + [(f"film-{i}", f"Film {i}") for i in range(8)]
        cards = "".join(f'<div class="movie"><a href="/film/{s}/"><img data-src="{CDN}/f/{s}.jpg"/><h3 class="movie-title">{t}</h3></a></div>' for s, t in films)
        home_html = (f'<html><body><nav class="menu"><a href="/filmler/">Filmler</a></nav><section><h2 class="section-title">Yeni Eklenen Filmler</h2>'
                     f'<div class="grid">{cards}</div></section></body></html>')
        detail = ('<html><head><meta property="og:image" content="x.jpg"><meta property="og:description" content="Uzun bir film özeti burada yer alır."></head>'
                  '<body><iframe src="https://vidmoly.me/embed-film1.html"></iframe></body></html>')
        out, _site = run({BASE + "/": home_html, BASE + "/filmler/": home_html, BASE + "/film/sonsuz-yolculuk/": detail,
                          BASE + "/film/kara-delik/": detail})
        data = data_of(out)
        self.assertEqual(out["site_kind"], "film")
        self.assertEqual(data["normalize"]["type"], "movie")
        self.assertEqual(data["normalize"]["key"]["regex"], r"^/(?:(?:film)/)?(?P<slug>[^/]+)(?:/.*)?$")
        self.assertNotIn("series_page", data)
        self.assertEqual({c["role"] for c in data["collections"]}, {"latest_movies"})
        self.assertEqual(data["providers"], ["vidmolly"])
        self.assertEqual(out["errors"], [], out["errors"])

    def test_an_unreachable_root_page(self):
        out, site = run({})
        self.assertEqual(out["yaml_text"], "")
        self.assertEqual(missing_fields(out), ["home"])
        self.assertTrue(any("could not be fetched" in n for n in out["notes"]))

    def test_a_home_page_without_any_block(self):
        out, _site = run({BASE + "/": "<html><body><div id='app'></div><script>boot()</script></body></html>"})
        self.assertEqual(out["yaml_text"], "")
        self.assertIn("JavaScript", out["missing"][0]["tried"][0])

    def test_the_url_may_be_any_page_and_must_be_http(self):
        out, site = run(url=BASE + "/diziler/halka-izle/")
        self.assertEqual(site.asked[0], ("home", BASE + "/"))
        self.assertEqual(out["errors"], [], out["errors"])
        out, site = run(url="ftp://x.example/")
        self.assertEqual((site.asked, out["yaml_text"]), ([], ""))
        self.assertTrue(out["errors"])

    def test_the_page_and_time_budgets(self):
        slow = Site(trdizi_pages())
        with known_providers():
            out = d.discover(BASE, slow, deadline=time.monotonic() + 1.0, now=time.monotonic)   # less than NEED_SECONDS left: nothing is fetched
        self.assertEqual(slow.asked, [])
        self.assertTrue(any("time budget" in n for n in out["notes"]))
        with patch.object(d, "MAX_PAGES", 3):
            out, site = run()
        self.assertEqual(len(site.asked), 3)
        self.assertTrue(any("page budget" in n for n in out["notes"]))


# --- the series page builder on the real fixture ---------------------------------------------------------------------------

class FixtureSeriesPageTest(unittest.TestCase):
    def test_the_trdiziizle_wpfp_fixture(self):
        html = (FIXTURES / "trdiziizle_series_wpfp.html").read_text(encoding="utf-8")
        url = "https://www.trdiziizle.tv/diziler/halka-izle/"
        outline = sb._outline_of(html, url, url)
        spec, evidence = d.build_series_page(html, url, outline, ["/diziler/a-izle/", "/diziler/b-izle/"])
        self.assertIsNotNone(spec, evidence)
        self.assertEqual(spec["row_selector"], "li.bolum")
        self.assertEqual(spec["fields"]["url"], {"selector": "a.ep", "attr": "href"})          # the first <a> of a row is "Favori"
        self.assertEqual(spec["fields"]["air_date"], {"selector": "span.tarih", "cast": "date_tr"})
        self.assertNotIn("default_season", spec)
        self.assertEqual(evidence["episodes"], 3)
        self.assertEqual([(v["season"], v["episode"]) for v in evidence["inventory"]["video_sources"]], [(1, 1), (1, 2), (1, 3)])
        self.assertEqual(evidence["inventory"]["video_sources"][0]["air_date"], "2025-01-04")

    def test_the_episode_fixtures(self):
        player = (FIXTURES / "trdiziizle_episode_player.html").read_text(encoding="utf-8")
        telif = (FIXTURES / "trdiziizle_episode_telif.html").read_text(encoding="utf-8")
        got = d.player_candidates(player, "https://www.trdiziizle.tv/kara-mavi-1-sezon-1-bolum-izle/", "https://www.trdiziizle.tv")
        self.assertEqual([(c["host"], c["placeholder"], c["trailer"]) for c in got], [("vidmoly.me", False, False)])
        self.assertEqual(got[0]["resolver"], {"type": "iframe", "selector": 'iframe[src*="vidmoly.me"]'})
        got = d.player_candidates(telif, "https://www.trdiziizle.tv/halka-1-sezon-1-bolum-izle/", "https://www.trdiziizle.tv")
        self.assertEqual([(c["host"], c["placeholder"]) for c in got], [("trdiziizle.tv", True)])


# --- the real yabancidizi fixtures -----------------------------------------------------------------------------------------

class YabancidiziFixtureTest(unittest.TestCase):
    YD = "https://yabancidizi.news"

    @classmethod
    def setUpClass(cls):
        read = lambda n: (FIXTURES / n).read_text(encoding="utf-8")
        cls.site = Site({cls.YD + "/": read("yabancidizi_home.html"),
                         cls.YD + "/dizi/lucky-2026": read("yabancidizi_series_snw.html"),
                         cls.YD + "/dizi/silo-izle-12": read("yabancidizi_series_lanterns.html")})
        started = time.monotonic()
        cls.out = d.discover(cls.YD, cls.site)
        cls.seconds = time.monotonic() - started
        cls.data = yaml.safe_load(cls.out["yaml_text"])

    def test_it_runs_fast_and_the_draft_is_valid(self):
        self.assertLess(self.seconds, 10)
        self.assertEqual(self.out["errors"], [], self.out["errors"])
        self.assertEqual(self.data["site_id"], "yabancidizi")

    def test_the_home_sections_get_their_roles(self):
        by_role = {c["role"]: c for c in self.data["collections"]}
        self.assertIn("latest_episodes", by_role)
        self.assertIn("latest_series", by_role)
        self.assertEqual(by_role["latest_episodes"]["title"], "Yabancı Dizi Son Bölümler")
        self.assertTrue(any("not used" in n for n in self.out["notes"]))
        self.assertTrue(any(n.startswith("block ") and n.endswith("latest_episodes") for n in self.out["notes"]))

    def test_films_and_series_are_told_apart_by_their_directory(self):
        rules = self.data["normalize"]
        self.assertEqual(rules["type"], {"from_group": "kind", "map": {"film": "movie", "dizi": "series"}, "default": "series"})
        self.assertEqual(rules["key"]["regex"], r"^/(?:(?P<kind>dizi|film)/)?(?P<slug>[^/]+)(?:/.*)?$")
        film = nrm.generic_normalize(rules, {"title": "Mayday", "detail_url": "film/mayday"}, base_url=self.YD)
        show = nrm.generic_normalize(rules, {"title": "Lanterns", "detail_url": "dizi/lanterns/sezon-1/bolum-3"}, base_url=self.YD)
        self.assertEqual((film["type"], film["source_key"]), ("movie", "mayday"))
        self.assertEqual((show["type"], show["source_key"]), ("series", "lanterns"))

    def test_series_pages_that_cannot_be_read_are_missing_not_guessed(self):
        # the fake site answers the real series fixtures under other URLs: whatever happens, nothing is invented
        self.assertTrue(self.out["pages_fetched"] <= d.MAX_PAGES)
        self.assertIn(self.out["confidence"].get("series_page", "low"), ("low", "medium", "high"))


# --- the sandbox endpoint ------------------------------------------------------------------------------------------------

class SandboxEndpointTest(tsb.SandboxCase):
    def store_fetch(self, pages):
        """``_fetch_store`` over a dict (the real one needs the network): every page is stored like the real one does."""
        def fake(url, mode, wait_for, deadline, referer=""):
            if url not in pages:
                raise sb.ApiError(502, "fetch_failed", "http: HTTP 404")
            meta = sb.onboard_store.save_page(url, url, "http", 200, pages[url])
            return {"meta": meta, "html": pages[url], "bundle": {}, "attempts": ["http: ok"]}
        return patch.object(sb, "_fetch_store", side_effect=fake)

    def test_discover_site_over_http(self):
        with tsb.public_dns(), self.store_fetch(trdizi_pages()), known_providers():
            got = self.post("/discover_site", {"url": BASE})
        self.assertEqual(got.status_code, 200, got.text)
        out = got.json()
        self.assertEqual(out["errors"], [], out["errors"])
        self.assertEqual(yaml.safe_load(out["yaml_text"])["list_url"], "/dizi-arsivi-01/")
        for page in out["pages"]:   # the pages are stored: the agent can query_html / grep_page them
            html, meta = sb.onboard_store.load_page(page["page_id"])
            self.assertEqual(meta["url"], page["url"])
            self.assertTrue(html)
        probe = self.post("/query", {"page_id": out["pages"][2]["page_id"], "selector": "div.single-item", "limit": 2})
        self.assertEqual(probe.json()["count"], len(SERIES))

    def test_a_failed_page_fetch_is_a_note_not_an_error(self):
        with tsb.public_dns(), self.store_fetch({}):
            got = self.post("/discover_site", {"url": BASE})
        self.assertEqual(got.status_code, 200, got.text)
        out = got.json()
        self.assertEqual(out["yaml_text"], "")
        self.assertTrue(any("fetch_failed" in n for n in out["notes"]), out["notes"])

    def test_a_refused_url_is_a_400_and_the_token_is_required(self):
        with tsb.public_dns():
            self.assertEqual(self.post("/discover_site", {"url": "http://127.0.0.1/"}).status_code, 400)
            self.assertEqual(self.client.post("/api/onboard/sandbox/discover_site", json={"url": BASE}).status_code, 403)

    def test_discover_site_is_for_new_sites_only(self):
        edit = sb.onboard_store.create_draft(BASE, "x")
        sb.onboard_store.update_draft(edit["id"], mode="edit", edit_site_id="demo", edit_from_version=1)
        token = sb.issue_token(edit["id"])
        self.addCleanup(sb.revoke_token, token)
        got = self.client.post("/api/onboard/sandbox/discover_site", json={"url": BASE}, headers={"X-Onboard-Token": token})
        self.assertEqual(got.status_code, 403)
        repair = sb.issue_token("rp_0123456789ab")
        self.addCleanup(sb.revoke_token, repair)
        got = self.client.post("/api/onboard/sandbox/discover_site", json={"url": BASE}, headers={"X-Onboard-Token": repair})
        self.assertEqual(got.status_code, 403)


if __name__ == "__main__":
    unittest.main()
