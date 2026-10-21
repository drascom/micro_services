import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import html as html_lib
import json
import unittest
from unittest.mock import Mock, patch

from app.scraper.providers import okru, registry, vidmolly
from app.scraper.site_extractors import detail_metadata, discover, resolve_candidate, series_catalog


class VideoProviderTests(unittest.TestCase):
    def test_yabancidizi_extractor_returns_only_content_player(self):
        html = '''
        <iframe src="https://ads.example/ad"></iframe>
        <div id="video-area"><div class="player">
          <iframe src="/api/drives/player-token"></iframe>
        </div></div>'''
        self.assertEqual(discover("yabancidizi", html, "https://yabancidizi.news/film/mayday"), [
            {"url": "https://yabancidizi.news/api/drives/player-token", "label": "YabancıDizi"}
        ])

    def test_yabancidizi_prefers_vidmolly_alternative(self):
        html = '''
        <div class="alternatives-for-this">
          <div data-link="mac/token+one=">Mac</div>
          <div data-link="moly/token+two=">VidMoly</div>
        </div>
        <div id="video-area"><iframe src="/api/drives/default"></iframe></div>'''
        self.assertEqual(discover("yabancidizi", html, "https://yabancidizi.news/film/mayday"), [
            {"url": "https://yabancidizi.news/api/moly/moly_token-two=", "label": "VidMolly"},
            {"url": "https://yabancidizi.news/api/drives/default", "label": "YabancıDizi"},
        ])

    def test_yabancidizi_prefers_vidmolly_download_hint(self):
        html = '''
        <a href="https://vidmoly.me/dl/filecode123">Türkçe Altyazılı İndir</a>
        <div class="alternatives-for-this">
          <div data-link="moly/token+two=">VidMoly</div>
        </div>'''
        found = discover("yabancidizi", html, "https://yabancidizi.news/film/mayday")
        self.assertEqual(found[0], {
            "url": "https://vidmoly.me/dl/filecode123",
            "label": "Türkçe Altyazılı İndir",
            "lang": "tr", "language": "Türkçe altyazı",   # the link text names the file's subtitle language
        })
        self.assertIn("/api/moly/", found[1]["url"])

    def test_yabancidizi_discovers_okru_session_handoff(self):
        page = '''<div class="alternatives-for-this">
          <div data-link="ok/token+one=" data-hash="hash-one"
               data-querytype="alternate">Okru</div>
        </div>'''
        found = discover("yabancidizi", page, "https://yabancidizi.news/film/mayday")
        self.assertEqual(found[0]["label"], "OK.ru")
        self.assertEqual(found[0]["handoff"], {
            "provider": "okru",
            "url": "https://yabancidizi.news/ajax/service",
            "link": "ok/token+one=",
            "hash": "hash-one",
            "querytype": "alternate",
        })

    def test_yabancidizi_resolves_okru_session_handoff(self):
        candidate = {
            "url": "https://yabancidizi.news/film/mayday", "label": "OK.ru",
            "handoff": {
                "provider": "okru", "url": "https://yabancidizi.news/ajax/service",
                "link": "opaque", "hash": "hash-one", "querytype": "alternate",
            },
        }
        session = Mock()
        post = Mock(status_code=200)
        post.json.return_value = {"api_iframe": "/api/ruplay/opaque"}
        session.post.return_value = post
        session.get.return_value = Mock(
            status_code=200,
            text='<iframe src="https://ok.ru/videoembed/12345"></iframe>',
        )
        with patch("curl_cffi.requests.Session", return_value=session):
            result = resolve_candidate(
                "yabancidizi", candidate, "https://yabancidizi.news/film/mayday",
                lambda: [{"name": "ci_session", "value": "abc", "domain": "yabancidizi.news", "path": "/"}],
            )
        self.assertEqual(result, {"url": "https://ok.ru/videoembed/12345", "label": "OK.ru"})
        self.assertEqual(session.post.call_args.kwargs["data"]["type"], "videoGet")
        # the site's own one-cookie requirement (`udys`) is enough: no browser session is opened for it
        session.cookies.set.assert_called_once()
        self.assertEqual(session.cookies.set.call_args.args[0], "udys")
        session.close.assert_called_once()

    def test_yabancidizi_resolves_vidmolly_session_handoff_without_browser(self):
        candidate = {
            "url": "https://yabancidizi.news/api/moly/opaque-token=",
            "label": "VidMolly",
        }
        session = Mock()
        session.get.return_value = Mock(
            status_code=200,
            text='<iframe src="https://vidmolly.biz/embed-filecode.html"></iframe>',
        )
        load_cookies = Mock(return_value=[{
            "name": "ci_session", "value": "abc",
            "domain": "yabancidizi.news", "path": "/",
        }])
        with patch("curl_cffi.requests.Session", return_value=session):
            result = resolve_candidate(
                "yabancidizi", candidate, "https://yabancidizi.news/film/mayday",
                load_cookies,
            )
        self.assertEqual(result, {
            "url": "https://vidmolly.biz/embed-filecode.html",
            "label": "VidMolly",
        })
        session.get.assert_called_once_with(
            candidate["url"], headers={"Referer": "https://yabancidizi.news/film/mayday"},
            timeout=10,
        )
        load_cookies.assert_not_called()      # the light `udys` cookie sufficed: no browser (Obscura) session
        self.assertEqual(session.cookies.set.call_args.args[0], "udys")
        session.close.assert_called_once()

    def test_plain_candidate_does_not_open_browser_cookie_session(self):
        candidate = {"url": "https://vidmolly.to/embed-abc", "label": "VidMolly"}
        load_cookies = Mock()
        self.assertIs(
            resolve_candidate(
                "yabancidizi", candidate, "https://yabancidizi.news/film/mayday",
                load_cookies,
            ),
            candidate,
        )
        load_cookies.assert_not_called()

    def test_yabancidizi_series_catalog_discovers_seasons_and_episodes(self):
        html = '''<h1 class="page-title">Lanterns <span>(2026)</span></h1>
        <meta property="og:image" content="/uploads/series/cover/lanterns.jpg">
        <img class="series-profile-thumb" src="uploads/series/lanterns.jpg">
        <div id="series-profile-content-wrapper"><a href="/dizi/tur/dram-izle">Dram</a></div>
        <p id="tv-series-desc">Türkçe bölüm özeti<br>--------<br>English summary</p>
        <div class="media-meta"><table><tr>
          <td><div>Süre</div><div>51 dk</div></td>
          <td><div class="color-imdb">8.2</div></td>
        </tr></table></div>
        <a href="/dizi/lanterns/sezon-1">Sezon 1</a>
        <a href="/dizi/lanterns/sezon-1/bolum-1">Bölüm 1</a>
        <a href="/dizi/lanterns/sezon-1/bolum-1">Pilot</a>
        <a href="/dizi/other/sezon-1/bolum-9">Other</a>'''
        found = series_catalog("yabancidizi", html, "https://yabancidizi.news/dizi/lanterns")
        self.assertEqual(found["season_pages"], ["https://yabancidizi.news/dizi/lanterns/sezon-1"])
        self.assertEqual(found["video_sources"][0]["title"], "Pilot")
        self.assertEqual(found["video_sources"][0]["episode"], 1)
        self.assertEqual(found["metadata"], {
            "poster_url": "https://yabancidizi.news/uploads/series/lanterns.jpg",
            "backdrop_url": "https://yabancidizi.news/uploads/series/cover/lanterns.jpg",
            "year": 2026,
            "title": "Lanterns",
            "overview": "Türkçe bölüm özeti",
            "genres": ["Dram"],
            "rating": 8.2,
            "runtime": 51,
        })

    def test_yabancidizi_movie_detail_extracts_all_available_metadata(self):
        html = '''
        <div class="bg-cover-bg"><img src="uploads/series/cover/one-night.jpg"></div>
        <h1 class="page-title">Miami'de Bir Gece... <span>(2021)</span></h1>
        <div id="series-profile-wrapper">
          <img class="series-profile-thumb" src="uploads/series/one-night.jpg"
               alt="One Night in Miami...">
          <div id="series-profile-content-wrapper">
            <p id="tv-series-desc">Tam film özeti <span class="tv-more">devamı</span>
              <a id="tv-show-more">... Devamını Göster</a></p>
            <a href="/film/tur/dram-izle">Dram</a>
            <a href="/oyuncu/michael-imperioli">Michael Imperioli</a>
            <a href="/oyuncu/aldis-hodge">Aldis Hodge</a>
            <div class="media-meta"><table><tr>
              <td><div>Ülke</div><div>US</div></td>
              <td><div>Süre</div><div>110 dk</div></td>
              <td><div>Takipçiler</div><div>27</div></td>
              <td><div>IMDb Puanı</div><div class="color-imdb">7.1</div></td>
              <td><div>Yapım Yılı</div><div>2021</div></td>
            </tr></table></div>
            <div class="media-trailer" data-yt="K8vf_Cmh9nY"></div>
          </div>
        </div>'''
        self.assertEqual(
            detail_metadata("yabancidizi", html,
                            "https://yabancidizi.news/film/one-night-in-miami-izle-1"),
            {
                "poster_url": "https://yabancidizi.news/uploads/series/one-night.jpg",
                "backdrop_url": "https://yabancidizi.news/uploads/series/cover/one-night.jpg",
                "year": 2021,
                "title": "Miami'de Bir Gece...",
                "original_title": "One Night in Miami...",
                "overview": "Tam film özeti devamı",
                "genres": ["Dram"],
                "cast": ["Michael Imperioli", "Aldis Hodge"],
                "country": "US",
                "runtime": 110,
                "followers": 27,
                "rating": 7.1,
                "trailer_url": "https://www.youtube.com/embed/K8vf_Cmh9nY",
            },
        )

    def test_vidmolly_resolver_extracts_ranked_media(self):
        page = '''<video><source src="/media/film-480.mp4"></video>
        <script>sources:[{file:"https://cdn.example/slides?url=poster.jpg"},
        {file:"https://cdn.example/film-1080.m3u8"}]</script>'''
        with patch.object(vidmolly.fetch, "fetch_url", return_value=page) as request:
            result = vidmolly.resolve("https://vidmolly.to/embed-abc", referer="https://catalog.example/item")
        self.assertEqual(result["streams"][0]["url"], "https://cdn.example/film-1080.m3u8")
        self.assertEqual(result["streams"][0]["type"], "hls")
        self.assertEqual(len(result["streams"]), 2)
        self.assertEqual(request.call_args.kwargs["headers"], {"Referer": "https://catalog.example/item"})

    def test_vidmolly_download_url_uses_canonical_embed_host(self):
        page = '''<script>sources:[{file:"https://cdn.example/film-720.m3u8"}]</script>'''
        with patch.object(vidmolly.fetch, "fetch_url", return_value=page) as request:
            result = vidmolly.resolve(
                "https://vidmoly.me/dl/filecode123",
                referer="https://catalog.example/item",
            )
        self.assertEqual(result["streams"][0]["url"], "https://cdn.example/film-720.m3u8")
        self.assertEqual(request.call_args.args[0], "https://vidmoly.biz/embed-filecode123.html")
        self.assertEqual(request.call_args.kwargs["headers"], {"Referer": "https://catalog.example/item"})

    def test_okru_resolver_extracts_and_ranks_mp4_variants(self):
        options = {"flashvars": {"metadata": {
            "movie": {"duration": "3388"},
            "videos": [
                {"name": "low", "url": "https://vd1.okcdn.ru/low", "disallowed": False},
                {"name": "full", "url": "https://vd1.okcdn.ru/full", "disallowed": False},
                {"name": "hd", "url": "https://vd1.okcdn.ru/blocked", "disallowed": True},
            ],
        }}}
        page = '<div data-options="' + html_lib.escape(json.dumps(options), quote=True) + '"></div>'
        # metadata API unavailable -> the embed page's data-options is the fallback (no real network here)
        with patch.object(okru.fetch, "post_url", side_effect=okru.fetch.FetchError("blocked in tests")), \
             patch.object(okru.fetch, "fetch_url", return_value=page) as request:
            result = okru.resolve("https://ok.ru/videoembed/12345", referer="https://catalog.example/item")
        self.assertEqual(result["streams"][0], {
            "url": "https://vd1.okcdn.ru/full", "type": "mp4",
            "quality": "1080p", "label": "1080p",
            "request_headers": {"User-Agent": okru.fetch.USER_AGENT},   # the signed URL is bound to the UA the page was read with
        })
        self.assertEqual(result["duration"], 3388)
        self.assertEqual(len(result["streams"]), 2)
        self.assertEqual(request.call_args.kwargs["headers"], {"Referer": "https://catalog.example/item"})

    def test_registry_dispatches_okru_provider(self):
        expected = {"streams": [{"url": "https://vd1.okcdn.ru/full"}]}
        with patch.object(registry.okru, "resolve", return_value=expected) as resolver:
            result = registry.resolve("https://ok.ru/videoembed/12345", referer="https://catalog.example/item")
        self.assertIs(result, expected)
        resolver.assert_called_once_with(
            "https://ok.ru/videoembed/12345", referer="https://catalog.example/item")

    def test_registry_follows_wrapper_then_uses_shared_provider(self):
        with patch.object(registry.fetch, "fetch_url", return_value='<iframe src="https://vidmolly.to/embed-abc"></iframe>') as handoff, \
             patch.object(registry.vidmolly, "resolve", return_value={"streams": [{"url": "https://cdn.example/a.mp4"}]}) as resolve:
            result = registry.resolve("https://yabancidizi.news/api/drives/token", referer="https://yabancidizi.news/film/mayday")
        self.assertEqual(result["streams"][0]["url"], "https://cdn.example/a.mp4")
        self.assertEqual(handoff.call_args.kwargs["headers"], {"Referer": "https://yabancidizi.news/film/mayday"})
        resolve.assert_called_once_with("https://vidmolly.to/embed-abc", referer="https://yabancidizi.news/film/mayday")

    def test_registry_accepts_a_site_browser_transport_for_handoffs(self):
        loader = Mock(return_value='<iframe src="https://vidmolly.to/embed-abc"></iframe>')
        with patch.object(registry.vidmolly, "resolve", return_value={"streams": [{"url": "https://cdn.example/a.mp4"}]}) as resolve:
            result = registry.resolve("https://catalog.example/player", load_handoff=loader)
        self.assertTrue(result["streams"])
        loader.assert_called_once_with("https://catalog.example/player")
        resolve.assert_called_once_with("https://vidmolly.to/embed-abc", referer="")
