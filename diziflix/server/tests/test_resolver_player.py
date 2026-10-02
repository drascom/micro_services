"""``player_page`` resolver type (``app.scraper.resolvers.player_page``) and its p.a.c.k.e.r. unpacker: discover, the
declarative ``extract`` rules (plain URL, ``<source>``, packed JS, base64, JSON body), ``follow`` (nested iframes),
referer / header handling, the Chrome-impersonating http transport (``fetch_impersonated``, ``warm_session``), the
browser transport choice, the netguard (SSRF) refusals, ``verify``, the iframe fallback, the trace and ``validate``.
Network-free: a fake ``ctx.fetch`` (or a fake ``curl_cffi`` session) and a patched DNS answer."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import base64
import ipaddress
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.scraper import fetch, resolvers, site_extractors
from app.scraper.fetch import FetchError
from app.scraper.providers import trace
from app.scraper.resolvers import _unpack, player_page

BASE = "https://site.example"
PAGE_URL = BASE + "/film/x"
PLAYER = BASE + "/player/oynat/abc123"
DETAIL = f'<div><iframe src="/player/oynat/abc123"></iframe><iframe src="https://ads.example/x"></iframe></div>'
PUBLIC = ipaddress.ip_address("93.184.216.34")
HLS = "https://cdn.example.net/hls/master.m3u8"


def fake_addresses(host, port):
    """DNS stand-in for ``netguard``: IP literals are themselves, ``intranet.test`` is private, the rest public."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        return [ipaddress.ip_address("10.0.0.5")] if host == "intranet.test" else [PUBLIC]


def words_of(source):
    """The packer dictionary of ``source``: every word (a real packer encodes all of them)."""
    return sorted(set(re.findall(r"\w+", source)))


class FakeSession:
    """What ``fetch.impersonated_session()`` returns: only its identity and ``close`` matter here."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeFetch:
    """``ctx.fetch`` stand-in: ``pages`` maps URL -> body (str / bytes) or an Exception to raise. The http transport of
    ``player_page`` is ``fetch_impersonated`` (there is deliberately no ``fetch_limited`` here)."""

    def __init__(self, pages=None, reachable=lambda url: True, browser=None):
        self.pages, self.gets, self.browsers, self.probes, self.sessions = dict(pages or {}), [], [], [], []
        self._reachable, self._browser = reachable, browser

    def impersonated_session(self, impersonate="chrome"):
        self.sessions.append(FakeSession())
        return self.sessions[-1]

    def fetch_impersonated(self, url, *, headers=None, cookies=None, timeout=12.0, max_bytes=3_000_000, max_redirects=3,
                           allow=None, impersonate="chrome", session=None):
        self.gets.append({"url": url, "headers": headers or {}, "allow": allow, "timeout": timeout,
                          "max_bytes": max_bytes, "session": session})
        body = self.pages.get(url)
        if body is None:
            raise FetchError(f"HTTP 404 for {url}", 404)
        if isinstance(body, Exception):
            raise body
        return body

    def browser_page(self, cfg, url, *, wait_for=""):
        self.browsers.append({"cfg": cfg, "url": url, "wait_for": wait_for})
        if self._browser is None:
            raise FetchError("no browser in this test")
        return self._browser(url)

    def reachable(self, url, *, timeout=15.0):
        self.probes.append(url)
        return self._reachable(url)


def make_ctx(fake=None, base_url=BASE, cfg=None):
    return resolvers.Ctx(site_id="site", base_url=base_url, cfg=cfg or SimpleNamespace(fetch_mode="http", data={}),
                         fetch=fake or FakeFetch())


def item(**over):
    return {"type": "player_page", "selector": 'iframe[src*="/player/"]',
            "extract": [{"regex": r'file\s*:\s*"([^"]+\.m3u8[^"]*)"'}], **over}


def params_of(it):
    return resolvers.params_of(it)


class Case(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.netguard._addresses", side_effect=fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self, it, pages, candidate=None, ctx=None, **fake_kw):
        fake = FakeFetch(pages, **fake_kw)
        ctx = ctx or make_ctx(fake)
        self.fake = ctx.fetch
        trace.begin()
        try:
            return player_page.resolve_candidate(ctx, candidate or {"url": PLAYER, "label": "Player", "resolver": 0},
                                                 PAGE_URL, params_of(it), None)
        finally:
            self.events = trace.take()


class UnpackTests(unittest.TestCase):
    def test_classic_packed_block(self):
        packed = ("eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}"
                  "('0 1=\"2\";',3,3,'var|a|hello'.split('|'),0,{}))")
        self.assertEqual(_unpack.unpack("<script>" + packed + "</script>"), ['var a="hello";'])

    def test_round_trip_with_quotes_slashes_and_every_radix(self):
        source = ("jwplayer('v').setup({sources:[{file:\"https:\\/\\/cdn.example.net\\/hls\\/master.m3u8?t=1&e=2\","
                  "label:'it\\'s 720p'}],image:\"x.jpg\"});")
        for radix in (10, 36, 62):
            packed = _unpack.pack(source, words_of(source), radix)
            self.assertIn("'.split('|')", packed)
            self.assertEqual(_unpack.unpack(packed), [source], radix)

    def test_two_blocks_and_nested_block(self):
        inner = 'var a="https://x.example/a.mp4";'
        inner_packed = _unpack.pack(inner, words_of(inner), 36)
        outer_source = "var z=1;" + inner_packed
        self.assertEqual(_unpack.unpack(_unpack.pack(outer_source, words_of(outer_source), 62)), [inner])
        two = _unpack.pack("a=1", words_of("a=1"), 36) + "<i>" + _unpack.pack("b=2", words_of("b=2"), 36)
        self.assertEqual(_unpack.unpack(two), ["a=1", "b=2"])

    def test_no_packer_or_garbage_returns_nothing(self):
        self.assertEqual(_unpack.unpack("var x = 1; eval(alert(1))"), [])
        self.assertEqual(_unpack.unpack(""), [])
        self.assertEqual(_unpack.unpack(None), [])
        self.assertEqual(_unpack.unpack("eval(function(p,a,c,k,e,d){} ('x',99,1,'a'.split('|'),0,{}))"), [])   # radix 99

    def test_the_unpacked_code_is_text_only(self):
        source = "require('child_process').exec('rm -rf /')"
        self.assertEqual(_unpack.unpack(_unpack.pack(source, words_of(source), 36)), [source])


class DiscoverTests(Case):
    def test_collects_absolute_player_urls_with_default_label(self):
        found = player_page.discover(make_ctx(), DETAIL, PAGE_URL, params_of(item()))
        self.assertEqual(found, [{"url": PLAYER, "label": "Player"}])

    def test_attr_host_regex_label_lang_and_dedupe(self):
        html = ('<div data-p="https://pl.example/a"></div><div data-p="https://pl.example/a"></div>'
                '<div data-p="https://other.example/b"></div><div></div>')
        found = player_page.discover(make_ctx(), html, PAGE_URL, {
            "selector": "div", "attr": "data-p", "host_regex": r"pl\.example", "label": "Sunucu 1", "lang": "TR"})
        self.assertEqual(found, [{"url": "https://pl.example/a", "label": "Sunucu 1", "lang": "tr"}])

    def test_garbage_selector_never_raises(self):
        self.assertEqual(player_page.discover(make_ctx(), DETAIL, PAGE_URL, {"selector": "iframe:foo", "extract": []}), [])


class ExtractTests(Case):
    def test_plain_file_url_hls_referer_and_result_shape(self):
        out = self.resolve(item(), {PLAYER: f'<script>jwplayer().setup({{file:"{HLS}?t=9"}});</script>'})
        cand = {"url": PLAYER, "label": "Player", "resolver": 0}
        self.assertEqual(out, {**cand, "stream": {
            "url": HLS + "?t=9", "type": "hls", "quality": "auto", "duration": 0, "provider": "Player",
            "streams": [{"url": HLS + "?t=9", "type": "hls", "quality": "auto", "label": "auto"}]}})
        call = self.fake.gets[0]
        self.assertEqual((call["url"], call["headers"]["Referer"]), (PLAYER, PAGE_URL))   # default referer = detail page
        self.assertTrue(callable(call["allow"]))
        self.assertEqual([(e["stage"], e["ok"]) for e in self.events], [("player_page.fetch", True), ("player_page.extract", True)])

    def test_custom_referer_template_and_headers(self):
        out = self.resolve(item(referer="{base}/", headers={"X-Requested-With": "XMLHttpRequest", "Origin": "{base}",
                                                           "X-Page": "{page_url}"}),
                           {PLAYER: f'file:"{HLS}"'})
        self.assertTrue(out)
        headers = self.fake.gets[0]["headers"]
        self.assertEqual((headers["Referer"], headers["X-Requested-With"], headers["Origin"], headers["X-Page"]),
                         (BASE + "/", "XMLHttpRequest", BASE, PAGE_URL))

    def test_source_tags_relative_urls_and_mp4_type_labels(self):
        html = ('<video><source src="/media/720.mp4?x=1" label="HD"><source src="//cdn.example.net/v/360.mp4"></video>')
        it = item(extract=[{"css": "video source", "attr": "src", "quality": 720, "label": "HD"}])
        out = self.resolve(it, {PLAYER: html})
        streams = out["stream"]["streams"]
        self.assertEqual([(s["url"], s["type"], s["quality"], s["label"]) for s in streams],
                         [(BASE + "/media/720.mp4?x=1", "mp4", "720", "HD"),
                          ("https://cdn.example.net/v/360.mp4", "mp4", "720", "HD")])
        self.assertEqual(out["stream"]["url"], BASE + "/media/720.mp4?x=1")

    def test_rules_merge_in_order_and_dedupe_and_forced_type(self):
        body = f'<source src="{HLS}"> sources:[{{"src":"{HLS}"}},{{"src":"https://c.example/v.bin"}}]'
        it = item(extract=[{"css": "source"}, {"regex": r'"src":"([^"]+)"', "type": "mp4", "quality": "480p"}])
        streams = self.resolve(it, {PLAYER: body})["stream"]["streams"]
        self.assertEqual([(s["url"], s["type"], s["quality"]) for s in streams],
                         [(HLS, "hls", "auto"), ("https://c.example/v.bin", "mp4", "480p")])

    def test_regex_without_group_uses_the_whole_match_and_group_zero(self):
        body = f"x {HLS} y"
        for rule in ({"regex": r"https://[^\s]+\.m3u8"}, {"regex": r"https://([^\s]+)\.m3u8", "group": 0}):
            streams = player_page.extract(body, PLAYER, [rule])
            self.assertEqual([s["url"] for s in streams], [HLS], rule)

    def test_unescape_slashes_unicode_and_entities(self):
        body = r'{"file":"https:\/\/cdn.example.net\/v.m3u8?a=1&b=2&amp;c=3&copy=4"}'
        streams = player_page.extract(body, PLAYER, [{"regex": r'"file":"([^"]+)"'}])
        self.assertEqual(streams[0]["url"], "https://cdn.example.net/v.m3u8?a=1&b=2&c=3&copy=4")   # bare &copy stays
        self.assertEqual(player_page.extract(body, PLAYER, [{"regex": r'"file":"([^"]+)"', "unescape": False}]), [])

    def test_packed_javascript_needs_unpack_true(self):
        script = 'jwplayer("p").setup({sources:[{file:"%s"}]});' % HLS
        page = "<html><script>" + _unpack.pack(script, words_of(script), 62) + "</script></html>"
        rule = {"regex": r'file:"([^"]+)"'}
        self.assertIsNone(self.resolve(item(extract=[rule]), {PLAYER: page}))
        out = self.resolve(item(extract=[{**rule, "unpack": True}]), {PLAYER: page})
        self.assertEqual(out["stream"]["url"], HLS)

    def test_unpack_keeps_searching_the_plain_page_too(self):
        inner = 'file:"https://a.example/a.m3u8"'
        packed = _unpack.pack(inner, words_of(inner), 36)
        page = f'<script>{packed}</script><script>file:"https://a.example/b.m3u8"</script>'
        streams = player_page.extract(page, PLAYER, [{"regex": r'file:"([^"]+)"', "unpack": True}])
        self.assertEqual({s["url"] for s in streams}, {"https://a.example/a.m3u8", "https://a.example/b.m3u8"})

    def test_base64_values(self):
        token = base64.b64encode(HLS.encode()).decode().rstrip("=")   # padding left out on purpose
        urlsafe = base64.urlsafe_b64encode(b"https://cdn.example.net/a?x=~~~>>>.mp4").decode()
        junk = base64.b64encode(b"just some words").decode()
        page = f'var a = atob("{token}"); var b = atob("{urlsafe}"); var c = atob("{junk}");'
        streams = player_page.extract(page, PLAYER, [{"regex": r'atob\("([^"]+)"\)', "base64": True}])
        self.assertEqual([s["url"] for s in streams], [HLS, "https://cdn.example.net/a?x=~~~>>>.mp4"])
        self.assertRegex(urlsafe, "[-_]")   # the test really exercises the URL-safe alphabet

    def test_json_body_paths(self):
        body = json.dumps({"data": {"sources": [{"file": HLS, "q": 720}, {"file": "https:\\/\\/c.example\\/b.mp4"}],
                                    "primary": "/v/main.m3u8", "urls": ["https://d.example/1.mp4", 5]}})
        rules = [{"json_path": "data.primary"}, {"json_path": "data.sources[*].file"},
                 {"json_path": "data.sources[0].file"}, {"json_path": "data.urls"}, {"json_path": "data.nope[*].x"}]
        streams = self.resolve(item(extract=rules), {PLAYER: body})["stream"]["streams"]
        self.assertEqual([s["url"] for s in streams],
                         ["https://site.example/v/main.m3u8", HLS, "https://c.example/b.mp4", "https://d.example/1.mp4"])
        self.assertEqual(player_page.extract("not json", PLAYER, [{"json_path": "a"}]), [])

    def test_non_http_and_empty_values_are_dropped_and_cap_applies(self):
        body = 'a:"javascript:alert(1)" a:"data:video/mp4;base64,AAAA" a:"" a:"mailto:x@y"'
        self.assertEqual(player_page.extract(body, PLAYER, [{"regex": r'a:"([^"]*)"'}]), [])
        many = " ".join(f'u:"https://c.example/{i}.mp4"' for i in range(30))
        self.assertEqual(len(player_page.extract(many, PLAYER, [{"regex": r'u:"([^"]+)"'}])), player_page.MAX_STREAMS)

    def test_bytes_body_and_empty_body(self):
        out = self.resolve(item(), {PLAYER: f'file:"{HLS}"'.encode("utf-8")})
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertIsNone(self.resolve(item(), {PLAYER: "   "}))
        self.assertEqual(self.events[-1]["error"], "empty page")

    def test_a_fetch_failure_is_a_traced_none_not_an_exception(self):
        self.assertIsNone(self.resolve(item(), {PLAYER: FetchError("HTTP 403", 403)}))
        self.assertFalse(self.events[0]["ok"])
        self.assertIn("403", self.events[0]["error"])

    def test_cloudflare_page_gets_a_hint(self):
        page = "<html><title>Just a moment...</title><body>challenge-platform</body></html>"
        self.assertIsNone(self.resolve(item(), {PLAYER: page}))
        self.assertIn("Cloudflare", self.events[-1]["error"])
        self.assertIn("fetch: browser", self.events[-1]["error"])
        self.assertIsNone(self.resolve(item(fetch="browser"), {}, ctx=make_ctx(FakeFetch(browser=lambda url: page))))
        self.assertNotIn("try fetch", self.events[-1]["error"])


class TypeTests(Case):
    """The URL decides the stream type when its extension is unmistakable; ``auto`` is the default."""
    M3U8 = "https://video.twimg.com/amplify_video/1/pl/abc.m3u8?tag=12"
    MP4 = "https://cdn.example.net/v/film.mp4?x=1"

    def kinds(self, body, **rule):
        notes = []
        streams = player_page.extract(body, PLAYER, [{"regex": r'file:"([^"]+)"', **rule}], notes)
        return [(s["url"], s["type"]) for s in streams], notes

    def test_default_is_auto_and_an_extension_less_url_is_mp4(self):
        got, notes = self.kinds('file:"https://redirector.googlevideo.com/videoplayback?id=1&itag=22"')
        self.assertEqual(got, [("https://redirector.googlevideo.com/videoplayback?id=1&itag=22", "mp4")])
        self.assertEqual(notes, [])

    def test_extension_less_url_takes_the_declared_type(self):
        got, notes = self.kinds('file:"https://c.example/stream/9"', type="hls")
        self.assertEqual(got, [("https://c.example/stream/9", "hls")])
        self.assertEqual(notes, [])

    def test_a_m3u8_url_beats_a_declared_mp4(self):
        got, notes = self.kinds(f'file:"{self.M3U8}"', type="mp4")
        self.assertEqual(got, [(self.M3U8, "hls")])
        self.assertEqual(notes, ["declared mp4, url is m3u8 -> hls"])

    def test_an_mp4_url_beats_a_declared_hls_and_webm_counts_as_mp4(self):
        got, notes = self.kinds(f'file:"{self.MP4}" file:"https://c.example/a.webm"', type="hls")
        self.assertEqual(got, [(self.MP4, "mp4"), ("https://c.example/a.webm", "mp4")])
        self.assertEqual(notes, ["declared hls, url is mp4 -> mp4", "declared hls, url is webm -> mp4"])

    def test_matching_declaration_and_auto_leave_no_note(self):
        for kind in ("auto", "hls"):
            got, notes = self.kinds(f'file:"{self.M3U8}"', type=kind)
            self.assertEqual((got, notes), ([(self.M3U8, "hls")], []))
        self.assertEqual(self.kinds(f'file:"{self.MP4}"', type="mp4"), ([(self.MP4, "mp4")], []))

    def test_dash_is_skipped_with_a_note_and_stays_skipped(self):
        body = 'file:"https://c.example/manifest.mpd" file:"https://c.example/manifest.mpd" file:"https://c.example/a.mp4"'
        got, notes = self.kinds(body)
        self.assertEqual(got, [("https://c.example/a.mp4", "mp4")])
        self.assertEqual(notes, ["skipped a DASH (.mpd) stream: not supported"])
        again = player_page.extract(body, PLAYER, [{"regex": r'file:"([^"]+)"'}, {"regex": r'(https://c\.example/manifest\.mpd)'}])
        self.assertEqual([s["url"] for s in again], ["https://c.example/a.mp4"])

    def test_resolve_traces_the_contradiction_and_the_stream_is_the_url_kind(self):
        out = self.resolve(item(extract=[{"regex": r'file:"([^"]+)"', "type": "mp4"}]), {PLAYER: f'file:"{self.M3U8}"'})
        self.assertEqual(out["stream"]["type"], "hls")
        self.assertEqual(out["stream"]["streams"][0]["type"], "hls")
        stages = [(e["stage"], e["ok"]) for e in self.events]
        self.assertIn(("player_page.type: declared mp4, url is m3u8 -> hls", True), stages)
        self.assertEqual(stages[-1], ("player_page.extract", True))

    def test_only_dash_is_no_stream_and_the_reason_says_why(self):
        self.assertIsNone(self.resolve(item(extract=[{"regex": r'file:"([^"]+)"'}]), {PLAYER: 'file:"https://c.example/m.mpd"'}))
        self.assertIn("DASH", self.events[-1]["error"])


class QualityTests(Case):
    SOURCES = ('player.setup({sources: [{file:"https://c.example/360.mp4", label:"360p"}, '
               '{file:"https://c.example/1080.mp4", label:"1080p"}, {file:"https://c.example/master.m3u8", label:"auto"}, '
               '{file:"https://c.example/720.mp4", label:"720p"}, {file:"https://c.example/x.mp4"}]});')
    RULE = {"regex": r'file\s*:\s*"([^"]+)"[^}]*?label\s*:\s*"([^"]+)"', "group": 1, "quality_group": 2}

    def streams(self, body=None, *rules):
        return player_page.extract(body or self.SOURCES, PLAYER, list(rules) or [self.RULE])

    def test_one_stream_per_sources_entry_best_first_auto_last(self):
        got = self.streams()
        self.assertEqual([(s["quality"], s["type"]) for s in got],
                         [("1080p", "mp4"), ("720p", "mp4"), ("360p", "mp4"), ("auto", "hls")])
        self.assertEqual([s["url"] for s in got][:2], ["https://c.example/1080.mp4", "https://c.example/720.mp4"])
        self.assertEqual(got[0]["label"], "1080p")   # no label_group: the quality text is the label
        # the entry without a label does not match the rule at all (the lazy [^}]*? stops at the object's end)
        self.assertNotIn("https://c.example/x.mp4", [s["url"] for s in got])

    def test_label_group_and_quality_group_are_independent(self):
        body = '{"file":"https://c.example/a.mp4","q":"FHD","name":"Full HD"}{"file":"https://c.example/b.mp4","q":"HD","name":"HD"}'
        rule = {"regex": r'"file":"([^"]+)","q":"([^"]+)","name":"([^"]+)"', "quality_group": 2, "label_group": 3}
        got = self.streams(body, rule)
        self.assertEqual([(s["quality"], s["label"]) for s in got], [("FHD", "Full HD"), ("HD", "HD")])
        only_label = self.streams(body, {**rule, "quality_group": None, "quality": "720"})
        self.assertEqual([(s["quality"], s["label"]) for s in only_label], [("720", "Full HD"), ("720", "HD")])

    def test_text_forms_of_a_quality(self):
        number = player_page._quality_number
        self.assertEqual([number(t) for t in ("720p", "1080", "1920x1080", "4K", "FHD", "Full HD", "HD", "SD", "auto", "", "720p HD")],
                         [720, 1080, 1080, 2160, 1080, 1080, 720, 480, 0, 0, 720])

    def test_without_a_quality_group_the_rule_order_is_kept(self):
        body = 'a:"https://c.example/a.mp4" a:"https://c.example/b.mp4"'
        got = player_page.extract(body, PLAYER, [{"regex": r'a:"([^"]+)"', "quality": "360p"}])
        self.assertEqual([s["url"] for s in got], ["https://c.example/a.mp4", "https://c.example/b.mp4"])
        mixed = player_page.extract(body, PLAYER, [{"regex": r'a:"([^"]+b\.mp4)"', "quality": "360p"},
                                                   {"regex": r'a:"([^"]+a\.mp4)"', "quality": "720p"}])
        self.assertEqual([s["quality"] for s in mixed], ["360p", "720p"])   # constants are the author's priority

    def test_rule_order_wins_among_equal_qualities_and_other_rules_merge(self):
        body = self.SOURCES + ' extra:"https://c.example/other.mp4"'
        got = self.streams(body, {"regex": r'extra:"([^"]+)"', "quality": "720p"}, self.RULE)
        self.assertEqual([s["quality"] for s in got], ["1080p", "720p", "720p", "360p", "auto"])
        self.assertEqual(got[1]["url"], "https://c.example/other.mp4")   # earlier rule first among the 720p

    def test_a_duplicate_url_is_kept_once_and_a_real_quality_upgrades_auto(self):
        body = 'x:"https://c.example/a.mp4" ' + self.SOURCES
        got = self.streams(body, {"regex": r'x:"([^"]+)"'}, {"regex": r'file\s*:\s*"([^"]+)"[^}]*?label\s*:\s*"([^"]+)"',
                                                              "quality_group": 2}, {"css": "source"})
        self.assertEqual(len([s for s in got if s["url"] == "https://c.example/a.mp4"]), 1)
        upgraded = self.streams('x:"https://c.example/1080.mp4" ' + self.SOURCES, {"regex": r'x:"([^"]+)"'}, self.RULE)
        self.assertEqual([(s["url"], s["quality"]) for s in upgraded if s["url"].endswith("1080.mp4")],
                         [("https://c.example/1080.mp4", "1080p")])
        self.assertEqual(upgraded[0]["url"], "https://c.example/1080.mp4")

    def test_resolve_candidate_returns_the_best_stream_first(self):
        out = self.resolve(item(extract=[self.RULE]), {PLAYER: self.SOURCES})
        stream = out["stream"]
        self.assertEqual((stream["url"], stream["quality"], stream["type"]), ("https://c.example/1080.mp4", "1080p", "mp4"))
        self.assertEqual([s["quality"] for s in stream["streams"]], ["1080p", "720p", "360p", "auto"])

    def test_the_cap_applies_before_sorting_and_a_non_matching_group_is_empty(self):
        many = " ".join(f'{{file:"https://c.example/{i}.mp4", label:"{300 + i}p"}}' for i in range(20))
        got = self.streams(many)
        self.assertEqual(len(got), player_page.MAX_STREAMS)
        self.assertEqual(got[0]["quality"], f"{300 + player_page.MAX_STREAMS - 1}p")
        optional = {"regex": r'file:"([^"]+)"(?:, label:"([^"]+)")?', "quality_group": 2}
        got = self.streams('file:"https://c.example/a.mp4" file:"https://c.example/b.mp4", label:"480p"', optional)
        self.assertEqual([(s["url"][-5:], s["quality"]) for s in got], [("b.mp4", "480p"), ("a.mp4", "auto")])


class FollowTests(Case):
    OUTER = BASE + "/player/oynat/abc123"
    INNER = "https://embed.example.org/e/xyz"
    DEEP = "https://deep.example.org/v/1"

    def test_nested_iframe_is_followed_with_the_parent_as_referer(self):
        pages = {self.OUTER: '<body><iframe src="https://embed.example.org/e/xyz"></iframe></body>',
                 self.INNER: f'<script>var x = {{file:"{HLS}"}};</script>'}
        out = self.resolve(item(follow=[{"selector": "iframe"}]), pages)
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [self.OUTER, self.INNER])
        self.assertEqual(self.fake.gets[1]["headers"]["Referer"], self.OUTER)
        self.assertEqual([e["stage"] for e in self.events], ["player_page.fetch", "player_page.follow1", "player_page.extract"])

    def test_two_hops_regex_filter_and_relative_urls(self):
        pages = {self.OUTER: '<iframe src="https://ads.example/x"></iframe><iframe src="/e/real"></iframe>',
                 BASE + "/e/real": f'<iframe src="{self.DEEP}"></iframe>',
                 self.DEEP: f'sources: [{{file: "{HLS}"}}]'}
        follow = [{"selector": "iframe", "regex": r"/e/"}, {"selector": "iframe[src]", "attr": "src"}]
        out = self.resolve(item(follow=follow), pages)
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [self.OUTER, BASE + "/e/real", self.DEEP])

    def test_follow_without_a_match_or_a_dead_hop_is_none(self):
        pages = {self.OUTER: "<p>no iframe here</p>"}
        self.assertIsNone(self.resolve(item(follow=[{"selector": "iframe"}]), pages))
        self.assertEqual(self.events[-1]["stage"], "player_page.follow1")
        pages = {self.OUTER: f'<iframe src="{self.INNER}"></iframe>'}   # inner page answers 404
        self.assertIsNone(self.resolve(item(follow=[{"selector": "iframe"}]), pages))
        self.assertFalse(self.events[-1]["ok"])


class IframeFallbackTests(Case):
    def test_unmatched_rules_hand_the_inner_iframe_to_the_registry(self):
        pages = {PLAYER: '<body><iframe src="https://vidmoly.me/embed-abc.html"></iframe></body>'}
        cand = {"url": PLAYER, "label": "Player", "resolver": 0, "resolver_type": "player_page", "lang": "tr"}
        out = self.resolve(item(), pages, candidate=cand)
        self.assertEqual(out, {**cand, "url": "https://vidmoly.me/embed-abc.html"})
        self.assertNotIn("stream", out)
        self.assertEqual(self.events[-1]["stage"], "player_page.iframe")

    def test_no_iframe_or_self_or_internal_iframe_is_none(self):
        self.assertIsNone(self.resolve(item(), {PLAYER: "<p>nothing</p>"}))
        self.assertFalse(self.events[-1]["ok"])
        self.assertIsNone(self.resolve(item(), {PLAYER: f'<iframe src="{PLAYER}"></iframe>'}))
        self.assertIsNone(self.resolve(item(), {PLAYER: '<iframe src="http://intranet.test/x"></iframe>'}))

    def test_streams_win_over_the_iframe(self):
        page = f'<iframe src="https://vidmoly.me/embed-abc.html"></iframe> file:"{HLS}"'
        self.assertIn("stream", self.resolve(item(), {PLAYER: page}))


class VerifyTests(Case):
    def test_unreachable_streams_are_dropped(self):
        page = 'file:"https://a.example/ok.m3u8" file:"https://a.example/dead.m3u8"'
        out = self.resolve(item(verify=True), {PLAYER: page}, reachable=lambda url: "ok" in url)
        self.assertEqual([s["url"] for s in out["stream"]["streams"]], ["https://a.example/ok.m3u8"])
        self.assertEqual(self.fake.probes, ["https://a.example/ok.m3u8", "https://a.example/dead.m3u8"])

    def test_all_dead_is_none_and_unverified_never_probes(self):
        page = f'file:"{HLS}"'
        self.assertIsNone(self.resolve(item(verify=True), {PLAYER: page}, reachable=lambda url: False))
        self.assertEqual(self.events[-1]["stage"], "player_page.verify")
        self.assertIn("1-byte", self.events[-1]["error"])
        self.resolve(item(), {PLAYER: page})
        self.assertEqual(self.fake.probes, [])

    def test_internal_stream_urls_are_never_probed(self):
        page = 'file:"http://intranet.test/a.m3u8" file:"https://a.example/ok.m3u8"'
        out = self.resolve(item(verify=True), {PLAYER: page})
        self.assertEqual(self.fake.probes, ["https://a.example/ok.m3u8"])
        self.assertEqual(len(out["stream"]["streams"]), 1)


class WarmSessionTests(Case):
    """``fetch: http`` = Chrome-impersonating transport; ``warm_session`` GETs the detail page first in the same session."""

    def test_default_is_one_request_without_a_shared_session(self):
        out = self.resolve(item(), {PLAYER: f'file:"{HLS}"'})
        self.assertTrue(out)
        self.assertEqual([(g["url"], g["session"]) for g in self.fake.gets], [(PLAYER, None)])
        self.assertEqual(self.fake.sessions, [])
        self.assertEqual([e["stage"] for e in self.events], ["player_page.fetch", "player_page.extract"])

    def test_the_detail_page_is_fetched_first_then_the_player_in_the_same_session(self):
        out = self.resolve(item(warm_session=True), {PAGE_URL: "<html>detail</html>", PLAYER: f'file:"{HLS}"'})
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [PAGE_URL, PLAYER])          # page first, then the player
        self.assertEqual(len(self.fake.sessions), 1)
        session = self.fake.sessions[0]
        self.assertTrue(all(g["session"] is session for g in self.fake.gets))
        self.assertNotIn("Referer", self.fake.gets[0]["headers"])                         # the warm-up is a plain page load
        self.assertEqual(self.fake.gets[1]["headers"]["Referer"], PAGE_URL)               # the player still gets the referer
        self.assertTrue(session.closed)
        self.assertEqual([(e["stage"], e["ok"]) for e in self.events],
                         [("player_page.warm", True), ("player_page.fetch", True), ("player_page.extract", True)])

    def test_follow_hops_share_the_warmed_session(self):
        inner = "https://embed.example.org/e/xyz"
        pages = {PAGE_URL: "x", PLAYER: f'<iframe src="{inner}"></iframe>', inner: f'file:"{HLS}"'}
        out = self.resolve(item(warm_session=True, follow=[{"selector": "iframe"}]), pages)
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [PAGE_URL, PLAYER, inner])
        self.assertEqual({id(g["session"]) for g in self.fake.gets}, {id(self.fake.sessions[0])})
        self.assertEqual(self.fake.gets[2]["headers"]["Referer"], PLAYER)

    def test_a_failed_warm_up_is_traced_and_the_player_is_still_tried(self):
        out = self.resolve(item(warm_session=True), {PAGE_URL: FetchError("HTTP 403", 403), PLAYER: f'file:"{HLS}"'})
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([(e["stage"], e["ok"]) for e in self.events],
                         [("player_page.warm", False), ("player_page.fetch", True), ("player_page.extract", True)])
        self.assertIn("403", self.events[0]["error"])
        self.assertTrue(self.fake.sessions[0].closed)

    def test_the_session_is_closed_when_the_player_fails_too(self):
        self.assertIsNone(self.resolve(item(warm_session=True), {PAGE_URL: "x", PLAYER: FetchError("HTTP 404", 404)}))
        self.assertTrue(self.fake.sessions[0].closed)

    def test_an_internal_detail_page_is_never_warmed(self):
        fake = FakeFetch({PLAYER: f'file:"{HLS}"'})
        trace.begin()
        try:
            out = player_page.resolve_candidate(make_ctx(fake), {"url": PLAYER, "resolver": 0}, "http://intranet.test/film",
                                                params_of(item(warm_session=True)), None)
        finally:
            events = trace.take()
        self.assertTrue(out)
        self.assertEqual([g["url"] for g in fake.gets], [PLAYER])
        self.assertEqual((events[0]["stage"], events[0]["ok"]), ("player_page.warm", False))
        self.assertIn("non-public", events[0]["error"])

    def test_browser_mode_ignores_the_session_machinery(self):
        fake = FakeFetch(browser=lambda url: f'file:"{HLS}"')
        self.resolve(item(fetch="browser"), {}, ctx=make_ctx(fake))
        self.assertEqual((fake.sessions, fake.gets), ([], []))

    def test_every_hop_of_the_http_transport_goes_through_netguard(self):
        """Real ``fetch`` module over a fake ``curl_cffi`` session: a redirect to an internal address is refused."""
        class Resp:
            def __init__(self, status, headers=None, body=b""):
                self.status_code, self.headers, self.body = status, headers or {}, body

        class Session:
            seen = []

            def __init__(self, **kw):
                self.cookies = SimpleNamespace(set=lambda *a, **k: None)

            def get(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None):
                Session.seen.append(url)
                if url == PLAYER:
                    return Resp(302, {"location": "http://intranet.test/secret"})
                content_callback(b"file:'x'")
                return Resp(200)

            def close(self):
                pass

        ctx = resolvers.make_ctx(SimpleNamespace(site_id="site", base_url=BASE, fetch_mode="http", data={}))
        trace.begin()
        try:
            with patch("curl_cffi.requests.Session", Session):
                out = player_page.resolve_candidate(ctx, {"url": PLAYER, "resolver": 0}, PAGE_URL, params_of(item()), None)
        finally:
            events = trace.take()
        self.assertIsNone(out)
        self.assertEqual(Session.seen, [PLAYER])   # the internal hop was never requested
        self.assertIn("host not allowed", events[0]["error"])


class BrowserTests(Case):
    def test_browser_mode_uses_the_browser_transport_only(self):
        cfg = SimpleNamespace(fetch_mode="http", data={})
        fake = FakeFetch(browser=lambda url: f'<script>file:"{HLS}"</script>')
        out = self.resolve(item(fetch="browser", wait_for="video"), {}, ctx=make_ctx(fake, cfg=cfg))
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual(fake.gets, [])
        self.assertEqual([(b["url"], b["wait_for"], b["cfg"]) for b in fake.browsers], [(PLAYER, "video", cfg)])

    def test_http_mode_never_touches_the_browser_and_follow_uses_it_per_hop(self):
        fake = FakeFetch({PLAYER: f'file:"{HLS}"'})
        self.resolve(item(), {}, ctx=make_ctx(fake))
        self.assertEqual(fake.browsers, [])
        pages = {PLAYER: '<iframe src="https://e.example/1"></iframe>'}
        fake = FakeFetch(browser=lambda url: pages.get(url) or f'file:"{HLS}"')
        out = self.resolve(item(fetch="browser", follow=[{"selector": "iframe"}]), {}, ctx=make_ctx(fake))
        self.assertEqual(out["stream"]["url"], HLS)
        self.assertEqual([b["url"] for b in fake.browsers], [PLAYER, "https://e.example/1"])
        self.assertEqual(fake.gets, [])

    def test_browser_failure_is_traced_none(self):
        fake = FakeFetch(browser=lambda url: (_ for _ in ()).throw(FetchError("obscura timeout")))
        self.assertIsNone(self.resolve(item(fetch="browser"), {}, ctx=make_ctx(fake)))
        self.assertIn("obscura timeout", self.events[0]["error"])

    def test_fetch_browser_page_selects_the_browser_whatever_the_site_fetch_mode(self):
        cfg = SimpleNamespace(fetch_mode="http", data={"cache_images": True, "obscura_stealth": False})
        with patch("app.scraper.transport.fetch_page", return_value="<html>x</html>") as transport:
            self.assertEqual(fetch.browser_page(cfg, "https://p.example/1", wait_for="#v"), "<html>x</html>")
        shim, url = transport.call_args.args
        self.assertEqual((url, transport.call_args.kwargs), ("https://p.example/1", {"wait_for": "#v"}))
        self.assertEqual(shim.fetch_mode, "browser")
        self.assertEqual(shim.data, {"cache_images": False, "obscura_stealth": False})
        self.assertEqual((cfg.fetch_mode, cfg.data["cache_images"]), ("http", True))   # the site's cfg is untouched

    def test_fetch_browser_page_makes_the_worker_run_in_browser_mode(self):
        cfg = SimpleNamespace(fetch_mode="http", data={})
        with patch("app.scraper.transport._worker_result", return_value={"html": "<p>x</p>"}) as worker:
            self.assertEqual(fetch.browser_page(cfg, "https://p.example/1"), "<p>x</p>")
        self.assertEqual(worker.call_args.args[0].fetch_mode, "browser")


class NetguardTests(Case):
    def test_internal_player_url_is_refused_before_any_fetch(self):
        for url in ("http://127.0.0.1/player/1", "http://192.168.0.61:8090/admin", "http://intranet.test/p",
                    "http://[::1]/p", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
            out = self.resolve(item(), {url: f'file:"{HLS}"'}, candidate={"url": url, "resolver": 0})
            self.assertIsNone(out, url)
            self.assertEqual(self.fake.gets, [], url)
            self.assertIn("player_page.fetch", self.events[0]["stage"])
            self.assertFalse(self.events[0]["ok"], url)
        self.assertEqual(self.fake.browsers, [])

    def test_internal_player_url_is_refused_in_browser_mode_too(self):
        fake = FakeFetch(browser=lambda url: f'file:"{HLS}"')
        out = self.resolve(item(fetch="browser"), {}, candidate={"url": "http://intranet.test/p", "resolver": 0},
                           ctx=make_ctx(fake))
        self.assertIsNone(out)
        self.assertEqual(fake.browsers, [])
        self.assertIn("blocked", self.events[0]["error"])

    def test_follow_to_an_internal_address_is_refused(self):
        pages = {PLAYER: '<iframe src="http://intranet.test/secret"></iframe>', "http://intranet.test/secret": f'file:"{HLS}"'}
        self.assertIsNone(self.resolve(item(follow=[{"selector": "iframe"}]), pages))
        self.assertEqual([g["url"] for g in self.fake.gets], [PLAYER])
        self.assertEqual(self.events[-1]["stage"], "player_page.follow1")
        self.assertIn("blocked", self.events[-1]["error"])

    def test_redirect_hops_are_checked_through_the_allow_callback(self):
        self.resolve(item(), {PLAYER: f'file:"{HLS}"'})
        allow = self.fake.gets[0]["allow"]
        self.assertTrue(allow("https://cdn.example.net/x"))
        for url in ("http://intranet.test/x", "http://127.0.0.1/", "ftp://a.example/x", "http://a.example:8090/x"):
            self.assertFalse(allow(url), url)

    def test_the_catalog_url_may_not_be_empty(self):
        self.assertIsNone(self.resolve(item(), {}, candidate={"url": "", "resolver": 0}))
        self.assertEqual(self.fake.gets, [])


class DispatchTests(Case):
    """Through ``site_extractors`` with a real cfg-like object, the way ``videos`` calls it."""

    def cfg(self, *items):
        return SimpleNamespace(site_id="site", base_url=BASE, resolvers=list(items), providers=None, use_site_module=False,
                               fetch_mode="http", data={})

    def test_discover_then_resolve_candidate(self):
        cfg = self.cfg(item(label="Oynatıcı", lang="tr"))
        cands = site_extractors.discover("site", DETAIL, PAGE_URL, cfg=cfg)
        self.assertEqual([(c["url"], c["label"], c["lang"], c["resolver"], c["resolver_type"]) for c in cands],
                         [(PLAYER, "Oynatıcı", "tr", 0, "player_page")])
        with patch("app.scraper.fetch.fetch_impersonated", return_value=f'file:"{HLS}"') as get, \
                patch("app.scraper.fetch.fetch_limited", side_effect=AssertionError("plain httpx is not the transport")):
            out = site_extractors.resolve_candidate("site", cands[0], PAGE_URL, None, cfg=cfg)
        self.assertEqual(out["stream"]["provider"], "Oynatıcı")
        self.assertEqual(out["stream"]["streams"][0]["url"], HLS)
        self.assertEqual(get.call_args.kwargs["headers"]["Referer"], PAGE_URL)
        self.assertEqual((out["resolver"], out["resolver_type"], out["lang"]), (0, "player_page", "tr"))

    def test_make_ctx_gives_the_real_fetch_module(self):
        ctx = resolvers.make_ctx(self.cfg())
        self.assertTrue(callable(ctx.fetch.fetch_impersonated) and callable(ctx.fetch.impersonated_session)
                        and callable(ctx.fetch.browser_page) and callable(ctx.fetch.reachable))


class ValidateTests(unittest.TestCase):
    def errors(self, **over):
        return resolvers.validate([item(**over)])

    def test_valid_items(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.errors(fetch="browser", wait_for="video", follow=[{"selector": "iframe", "regex": "/e/"}],
                                     extract=[{"regex": "a(b)", "group": 1, "unpack": True, "base64": False, "type": "hls",
                                               "quality": 720, "label": "HD"}, {"css": "source", "attr": "src"},
                                               {"json_path": "a.b[*].c"}], verify=True), [])
        self.assertEqual(self.errors(referer="{base}/x", headers={"X-A": "1", "Accept-Language": "tr"}), [])
        self.assertEqual(self.errors(warm_session=True, referer="{page_url}"), [])
        self.assertEqual(self.errors(fetch="browser", warm_session=False), [])

    def test_required_and_unknown_parameters(self):
        self.assertEqual(resolvers.validate([{"type": "player_page", "extract": [{"css": "a"}]}]),
                         ["resolvers[0]: missing required parameter 'selector'"])
        self.assertEqual(resolvers.validate([{"type": "player_page", "selector": "iframe"}]),
                         ["resolvers[0]: missing required parameter 'extract'"])
        self.assertEqual(self.errors(timeout=5), ["resolvers[0]: unknown parameter 'timeout' for type 'player_page'"])
        self.assertTrue(self.errors(selector="iframe:foo"))
        self.assertTrue(self.errors(host_regex="("))

    def test_extract_rules(self):
        cases = {
            "needs at least one rule": [],
            "at most 8 rules": [{"css": "a"}] * 9,
            "must be a mapping": ["file"],
            "needs exactly one of regex / css / json_path (got none)": [{"attr": "src"}],
            "needs exactly one of regex / css / json_path (got regex, css)": [{"regex": "a", "css": "b"}],
            "unknown key 'xpath'": [{"xpath": "//a"}],
            "invalid regex": [{"regex": "(unclosed"}],
            "catastrophic backtracking": [{"regex": "(a+)+$"}],
            "regex longer than 500": [{"regex": "a" * 501}],
            "group 2 but the regex has 1 capture group": [{"regex": "(a)", "group": 2}],
            "'group' must be a non-negative integer": [{"regex": "(a)", "group": "1"}],
            "'group' only applies to a regex rule": [{"css": "a", "group": 1}],
            "invalid CSS selector": [{"css": "a:foo"}],
            "'unpack' must be true or false": [{"regex": "a", "unpack": "yes"}],
            "'type' must be one of auto, hls, mp4": [{"regex": "a", "type": "dash"}],
            "'quality' must be a string or number": [{"regex": "a", "quality": ["720"]}],
        }
        for needle, rules in cases.items():
            errors = self.errors(extract=rules)
            self.assertTrue(any(needle in e for e in errors), (needle, errors))

    def test_fetch_follow_headers(self):
        cases = {
            "parameter 'fetch' must be 'http' or 'browser'": dict(fetch="curl"),
            "'wait_for' only applies with fetch: browser": dict(wait_for="video"),
            "invalid CSS selector": dict(fetch="browser", wait_for="v:foo"),
            "'referer' only applies with fetch: http": dict(fetch="browser", referer="{base}/"),
            "'headers' only applies with fetch: http": dict(fetch="browser", headers={"X-A": "1"}),
            "'warm_session' only applies with fetch: http": dict(fetch="browser", warm_session=True),
            "parameter 'warm_session' must be bool": dict(warm_session="yes"),
            "at most 2 hops": dict(follow=[{"selector": "iframe"}] * 3),
            "follow[0]: missing 'selector'": dict(follow=[{"attr": "src"}]),
            "follow[0]: unknown key 'xpath'": dict(follow=[{"selector": "i", "xpath": "x"}]),
            "follow[1]: invalid regex": dict(follow=[{"selector": "i"}, {"selector": "i", "regex": "("}]),
            "invalid header name": dict(headers={"bad name": "x"}),
            "'Host' cannot be set": dict(headers={"Host": "evil"}),
            "must be single-line text": dict(headers={"X-A": "a\r\nInjected: 1"}),
        }
        for needle, over in cases.items():
            errors = self.errors(**over)
            self.assertTrue(any(needle in e for e in errors), (needle, errors))

    def test_quality_and_label_groups(self):
        ok = [{"regex": "(a)(b)", "quality_group": 2, "label_group": 1}, {"regex": "a(b)", "quality_group": 0}]
        self.assertEqual(self.errors(extract=ok), [])
        cases = {
            "quality_group 3 but the regex has 2 capture group(s)": [{"regex": "(a)(b)", "quality_group": 3}],
            "label_group 2 but the regex has 1 capture group(s)": [{"regex": "(a)", "label_group": 2}],
            "'quality_group' must be a non-negative integer": [{"regex": "(a)", "quality_group": "1"}],
            "'label_group' must be a non-negative integer": [{"regex": "(a)", "label_group": -1}],
            "'quality_group' only applies to a regex rule": [{"css": "a", "quality_group": 1}],
            "'label_group' only applies to a regex rule": [{"json_path": "a", "label_group": 1}],
        }
        for needle, rules in cases.items():
            errors = self.errors(extract=rules)
            self.assertTrue(any(needle in e for e in errors), (needle, errors))

    def test_catalog_entry(self):
        entry = {c["type"]: c for c in resolvers.catalog()}["player_page"]
        params = entry["params"]
        self.assertEqual({n for n, s in params.items() if s["required"]}, {"selector", "extract"})
        self.assertEqual((params["attr"]["default"], params["fetch"]["default"], params["label"]["default"],
                          params["referer"]["default"], params["verify"]["default"]), ("src", "http", "Player", "{page_url}", False))
        self.assertIs(params["warm_session"]["default"], False)
        for needle in ("quality_group", "label_group", "extension", "auto"):
            self.assertIn(needle, params["extract"]["help"])
        self.assertIn("Chrome", params["fetch"]["help"])
        self.assertIn("Cloudflare", params["fetch"]["help"])
        self.assertEqual(params["follow"]["default"], [])
        self.assertEqual(list(resolvers.TYPES)[-1], "player_page")
        self.assertEqual(len(resolvers.TYPES), 6)
        for name, spec in params.items():
            self.assertTrue(spec["help"], name)
        json.dumps(entry)


if __name__ == "__main__":
    unittest.main()
