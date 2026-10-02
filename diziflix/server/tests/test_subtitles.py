"""Soft subtitles + audio/subtitle track description: VidMolly ``tracks`` extraction, the additive
``/api/streams`` fields (old fields unchanged), ``GET /api/subtitles/<id>.vtt`` (allow-list, size/time limits, SRT->VTT,
disk cache, 404 for a dead source). Network-free: fixtures under tests/fixtures (signatures/IPs redacted) and fakes."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import config as app_config, db, langs, subtitles
from app.library import tracks, videos
from app.routers import streams as stream_routes
from app.scraper import fetch
from app.scraper.providers import trace, vidmolly
from test_resolvers import ResolutionCase

FIXTURES = Path(__file__).parent / "fixtures"
EN_PAGE = (FIXTURES / "vidmolly_embed_snw_s4e2_en.html").read_text(encoding="utf-8")
TR_PAGE = (FIXTURES / "vidmolly_embed_snw_s4e2_tr.html").read_text(encoding="utf-8")
EN_VTT = (FIXTURES / "vidmolly_subtitle_snw_s4e2_en.vtt").read_text(encoding="utf-8")
EMBED = "https://vidmoly.biz/embed-i0q31suzt1f5.html"
SUB_URL = "https://srt.vidmoly.me/srt/02715/i0q31suzt1f5_English.vtt"


class LanguageTests(unittest.TestCase):
    def test_codes_names_and_file_names(self):
        self.assertEqual([langs.code(t) for t in ("English", "Türkçe", "tr", "en_US", "İngilizce", "Turkish (SDH)", "")],
                         ["en", "tr", "tr", "en", "en", "tr", None])
        self.assertEqual(langs.file_lang(SUB_URL), "en")
        self.assertEqual(langs.file_lang("https://x.test/a/abc_tr.vtt"), "tr")
        self.assertIsNone(langs.file_lang("https://x.test/a/movie.srt"))
        self.assertEqual((langs.label("tr"), langs.label("en"), langs.label("xx")), ("Türkçe", "İngilizce", None))


class VidmollyTracksTests(unittest.TestCase):
    def test_english_file_yields_its_soft_track_and_skips_the_thumbnail_sprite(self):
        found = vidmolly.subtitle_tracks(EN_PAGE, EMBED)
        self.assertEqual(found, [{"url": SUB_URL, "lang": "en", "label": "İngilizce", "kind": "captions",
                                  "referer": EMBED, "default": True}])

    def test_turkish_file_has_only_thumbnails_so_no_subtitle(self):
        self.assertIn("kind: 'thumbnails'", TR_PAGE)
        self.assertEqual(vidmolly.subtitle_tracks(TR_PAGE, EMBED), [])

    def test_other_spellings_of_the_tracks_block(self):
        inline = """<script>jwplayer("x").setup({tracks:[{"file":"//srt.vidmoly.me/srt/1/a_Turkish.vtt","label":"Turkish","kind":"subtitles"},
            {file:'/api/v1/slides?x=1',kind:'thumbnails'},{file:'https://srt.vidmoly.me/srt/1/b.vtt'},
            {file:'https://cdn.example/sprite.jpg'},{file:'javascript:alert(1)',kind:'captions'},
            {file:'https://srt.vidmoly.me/srt/1/a_Turkish.vtt',kind:'captions'}]});</script>"""
        found = vidmolly.subtitle_tracks(inline, "https://vidmoly.biz/embed-a.html")
        self.assertEqual([(t["url"], t["lang"], t["kind"], t["default"]) for t in found], [
            ("https://srt.vidmoly.me/srt/1/a_Turkish.vtt", "tr", "subtitles", False),   # protocol-relative, deduplicated
            ("https://srt.vidmoly.me/srt/1/b.vtt", None, "captions", False)])           # no kind: .vtt counts, language unknown
        self.assertEqual(found[1]["label"], "Altyazı")

    def test_unexpected_shapes_never_raise_and_just_lose_the_subtitle(self):
        for page in ("", "no tracks here", "tracks: [", "var baseTracks = [ {file: 'https://a.test/x.vtt', ", "tracks: baseTracks",
                     "var baseTracks = [ 1, 2, {}, {file:''} ];", "tracks:[{file:'https://srt.vidmoly.me/x.vtt',kind:'chapters'}]"):
            self.assertEqual(vidmolly.subtitle_tracks(page, EMBED), [], page)

    def test_a_crash_in_the_parser_keeps_the_video_and_is_traced(self):
        with patch.object(vidmolly, "_track_entries", side_effect=RuntimeError("shape changed")):
            trace.begin()
            self.assertEqual(vidmolly.subtitle_tracks(EN_PAGE, EMBED), [])
            events = trace.take()
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("vidmolly.tracks", False)])
        self.assertIn("shape changed", events[0]["error"])
        with patch.object(vidmolly, "_track_entries", side_effect=RuntimeError("shape changed")), \
             patch.object(vidmolly.fetch, "fetch_url", return_value=EN_PAGE):
            result = vidmolly.resolve("https://vidmoly.me/dl/i0q31suzt1f5")
        self.assertEqual(result["subtitles"], [])
        self.assertTrue(result["streams"][0]["url"].endswith(".m3u8") or ".m3u8" in result["streams"][0]["url"])

    def test_resolve_carries_variant_and_subtitles(self):
        with patch.object(vidmolly.fetch, "fetch_url", return_value=EN_PAGE):
            result = vidmolly.resolve("https://vidmoly.me/dl/i0q31suzt1f5", referer="https://catalog.example/x")
        self.assertEqual(result["variant"], "i0q31suzt1f5")
        self.assertEqual([t["url"] for t in result["subtitles"]], [SUB_URL])
        self.assertEqual((result["provider"], result["streams"][0]["type"]), ("VidMolly", "hls"))
        with patch.object(vidmolly.fetch, "fetch_url", return_value=TR_PAGE):
            self.assertEqual(vidmolly.resolve("https://vidmoly.me/dl/nbdb0p8nd5qa")["subtitles"], [])


class DescribeTests(unittest.TestCase):
    def test_rules(self):
        soft = [{"kind": "captions", "lang": "en"}]
        self.assertEqual(tracks.describe("VidMolly", "en", "İngilizce altyazı", soft),
                         {"audio_lang": "en", "sub_mode": "soft", "hard_lang": None, "sub_known": True, "site_lang_hint": "en"})
        self.assertEqual(tracks.describe("VidMolly", "tr", "Türkçe altyazı", []),
                         {"audio_lang": None, "sub_mode": "hard", "hard_lang": "tr", "sub_known": True, "site_lang_hint": "tr"})
        self.assertEqual(tracks.describe("VidMolly", "tr", "Türkçe dublaj", []),
                         {"audio_lang": "tr", "sub_mode": "none", "hard_lang": None, "sub_known": True, "site_lang_hint": "tr"})
        # OK.ru: the language is only in the file name/tab text, never measured as hard-sub: nothing is invented
        # (old fields as before; the tab language is kept apart as a hint and the state is flagged as not known)
        self.assertEqual(tracks.describe("OK.ru", "tr", "Türkçe altyazı", []),
                         {"audio_lang": None, "sub_mode": "none", "hard_lang": None, "sub_known": False, "site_lang_hint": "tr"})
        # an English file whose tracks could not be read is not called hard-subbed
        self.assertEqual(tracks.describe("VidMolly", "en", "İngilizce altyazı", []),
                         {"audio_lang": None, "sub_mode": "none", "hard_lang": None, "sub_known": True, "site_lang_hint": "en"})
        self.assertEqual(tracks.describe("VidMolly", None, "", []),
                         {**tracks.DEFAULT_FIELDS, "sub_known": True})
        self.assertEqual(tracks.describe("OK.ru", None, "", []), dict(tracks.DEFAULT_FIELDS))
        # a provider with a soft track found is known, whoever it is
        self.assertTrue(tracks.describe("OK.ru", "tr", "Türkçe altyazı", soft)["sub_known"])

    def test_label_language_drops_an_unmeasured_subtitle_claim_but_keeps_dub_and_known_ones(self):
        known, unknown = {"sub_known": True}, {"sub_known": False}
        self.assertEqual(tracks.label_language(known, "Türkçe altyazı"), "Türkçe altyazı")
        self.assertIsNone(tracks.label_language(unknown, "Türkçe altyazı"))
        self.assertIsNone(tracks.label_language(unknown, "İngilizce altyazı"))
        self.assertEqual(tracks.label_language(unknown, "Türkçe dublaj"), "Türkçe dublaj")
        self.assertIsNone(tracks.label_language(known, ""))
        self.assertIsNone(tracks.label_language(unknown, None))

    def test_hard_file_takes_the_audio_language_of_the_clean_sibling_only(self):
        hard = {"provider": "VidMolly", "sub_mode": "hard", "audio_lang": None}
        clean = {"provider": "VidMolly", "sub_mode": "soft", "audio_lang": "en"}
        other = {"provider": "OK.ru", "sub_mode": "soft", "audio_lang": "de"}
        tracks.share_audio([hard, other])
        self.assertIsNone(hard["audio_lang"])
        tracks.share_audio([hard, other, clean])
        self.assertEqual(hard["audio_lang"], "en")

    def test_variant_id_is_stable_across_signed_urls(self):
        a = tracks.variant_id("vs1", "", "https://h1.cdn/hls2/04/02715/x.m3u8?t=aaa&s=1")
        b = tracks.variant_id("vs1", "", "https://h2.other/hls2/04/02715/x.m3u8?t=bbb&s=2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, tracks.variant_id("vs2", "", "https://h1.cdn/hls2/04/02715/x.m3u8"))
        self.assertEqual(tracks.variant_id("vs1", "code1"), tracks.variant_id("vs1", "code1", "https://anything/else"))
        self.assertTrue(a.startswith("v_"))


class MirrorTests(unittest.TestCase):
    HLS = "https://prx-vi-a-1.vmpx.online/hls2/01/02715/nbdb0p8nd5qa_,n,l,.urlset/master.m3u8"

    @staticmethod
    def st(url, variant="v_a", quality="auto", provider="VidMolly", **extra):
        return {"url": url, "provider": provider, "variant_id": variant, "quality": quality, "type": "hls", **extra}

    def test_a_resigned_url_of_the_same_file_has_the_same_identity(self):
        a = tracks.file_identity(self.st(self.HLS + "?t=AAAA&s=111&e=86400&i=a1b&sp=450&srv=x"))
        b = tracks.file_identity(self.st(self.HLS + "?t=BBBB&s=222&e=86400&i=a1b&sp=450&srv=x"))
        c = tracks.file_identity(self.st(self.HLS))
        self.assertEqual(a, b)
        self.assertEqual(a, c)   # a file path names the file: the query never matters

    def test_another_host_or_path_or_provider_is_another_stream(self):
        base = tracks.file_identity(self.st(self.HLS))
        self.assertNotEqual(base, tracks.file_identity(self.st(self.HLS.replace("prx-vi-a-1", "prx-vi-b-2"))))
        self.assertNotEqual(base, tracks.file_identity(self.st(self.HLS.replace("nbdb0p8nd5qa", "other"))))
        self.assertNotEqual(base, tracks.file_identity(self.st(self.HLS, provider="OK.ru")))

    def test_a_root_path_url_keeps_the_query_that_names_the_file_but_not_the_signature(self):
        q = "https://ok6-31.vkuser.net/?id=111&type=5&ct=0&sig=AAA&expires=1"
        self.assertEqual(tracks.file_identity(self.st(q, provider="OK.ru")),
                         tracks.file_identity(self.st(q.replace("sig=AAA", "sig=BBB").replace("expires=1", "expires=2"), provider="OK.ru")))
        self.assertNotEqual(tracks.file_identity(self.st(q, provider="OK.ru")),
                            tracks.file_identity(self.st(q.replace("type=5", "type=4"), provider="OK.ru")))   # another quality
        self.assertNotEqual(tracks.file_identity(self.st(q, provider="OK.ru")),
                            tracks.file_identity(self.st(q.replace("id=111", "id=222"), provider="OK.ru")))

    def test_spare_copies_follow_their_primary_and_carry_its_key(self):
        a1 = self.st("https://h1/a.m3u8", "v_a")
        b1 = self.st("https://h1/b.mp4", "v_b", quality="1080p")
        b2 = self.st("https://h1/b.mp4", "v_b", quality="1440p")
        a2 = self.st("https://h2/a.m3u8", "v_a")
        a3 = self.st("https://h3/a.m3u8", "v_a")
        out = tracks.link_mirrors([a1, b1, b2, a2, a3])
        self.assertEqual([s["url"] for s in out], ["https://h1/a.m3u8", "https://h2/a.m3u8", "https://h3/a.m3u8",
                                                   "https://h1/b.mp4", "https://h1/b.mp4"])
        self.assertEqual([s["mirror_of"] for s in out], [None, "v_a:auto", "v_a:auto", None, None])   # qualities of one file are not mirrors
        self.assertEqual(tracks.mirror_key(a1), "v_a:auto")
        self.assertNotIn("mirror_of", a1, "the input is not modified")

    def test_streams_without_a_variant_are_left_alone(self):
        plain = [{"url": "https://x/1.mp4"}, {"url": "https://x/2.mp4"}]
        self.assertEqual(tracks.link_mirrors(plain), plain)
        self.assertEqual(tracks.link_mirrors([]), [])

    def test_defaults_carry_every_track_field(self):
        out = tracks.with_defaults({"url": "https://media.example/a.mp4"}, "vs1")
        self.assertEqual({k: out[k] for k in ("audio_lang", "sub_mode", "hard_lang", "sub_known", "site_lang_hint", "mirror_of")},
                         {"audio_lang": None, "sub_mode": "none", "hard_lang": None, "sub_known": False,
                          "site_lang_hint": None, "mirror_of": None})
        kept = tracks.with_defaults({"url": "https://media.example/a.mp4", "mirror_of": "v_x:auto", "sub_known": True}, "vs1")
        self.assertEqual((kept["mirror_of"], kept["sub_known"]), ("v_x:auto", True))


class StreamSchemaTests(ResolutionCase):
    """End to end through ``videos``: resolution -> cached payload -> ``streams()`` response."""

    SUB = {"url": SUB_URL, "lang": "en", "label": "İngilizce", "kind": "captions", "referer": EMBED, "default": True}

    def cands(self, *specs):
        return [{"url": f"https://vidmolly.biz/embed-{code}.html", "label": label, "lang": lang, "language": language}
                for code, label, lang, language in specs]

    @staticmethod
    def provider(soft=None, names=None):
        soft = soft or {}
        names = names or {}

        def resolve(url, **kw):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            return {"provider": names.get(code, "VidMolly"), "duration": 0, "variant": code, "subtitles": soft.get(code, []),
                    "streams": [{"url": f"https://cdn.example/{code}/master.m3u8?t=sig", "type": "hls",
                                 "quality": "auto", "label": "auto"}]}
        return resolve

    def snw(self):
        """The measured SNW S4E2 situation: a hard-subbed Turkish file and a clean English file with a soft VTT."""
        self.resolve(self.cands(("trf", "VidMolly", "tr", "Türkçe altyazı"), ("enf", "VidMolly", "en", "İngilizce altyazı")),
                     self.provider(soft={"enf": [self.SUB]}))
        return videos.streams("c1", kind="video")

    def test_snw_response_adds_track_fields_and_keeps_the_old_ones(self):
        out = self.snw()
        tr, en = out["streams"]
        for stream in (tr, en):   # every pre-existing field keeps its name and meaning
            self.assertEqual((stream["type"], stream["quality"], stream["provider"], stream["source_id"], stream["source"],
                              stream["kind"]), ("hls", "auto", "VidMolly", "vs1", "yabancidizi", "movie"))
            self.assertEqual(len(stream["attempt_token"]), 32)
            self.assertTrue(stream["url"].startswith("https://cdn.example/"))
        self.assertEqual((tr["label"], en["label"]), ("VidMolly · Türkçe altyazı · auto", "VidMolly · İngilizce altyazı · auto"))
        self.assertEqual((tr["sub_mode"], tr["hard_lang"], tr["audio_lang"]), ("hard", "tr", "en"))   # audio from the clean sibling
        self.assertEqual((en["sub_mode"], en["hard_lang"], en["audio_lang"]), ("soft", None, "en"))
        self.assertNotEqual(tr["variant_id"], en["variant_id"])
        self.assertEqual(set(out), {"streams", "subtitles", "audio", "duration"})
        # additive fields: VidMolly's subtitle state is measured; the tab language is kept as a hint; nobody is a spare copy
        self.assertEqual([(s["sub_known"], s["site_lang_hint"], s["mirror_of"]) for s in (tr, en)],
                         [(True, "tr", None), (True, "en", None)])

    def test_snw_subtitles_and_audio_lists(self):
        out = self.snw()
        tr, en = out["streams"]
        self.assertEqual(len(out["subtitles"]), 1)
        sub = out["subtitles"][0]
        self.assertEqual({k: sub[k] for k in ("lang", "label", "kind", "format", "stream_ids", "default", "origin")},
                         {"lang": "en", "label": "İngilizce", "kind": "captions", "format": "vtt",
                          "stream_ids": [en["variant_id"]], "default": True, "origin": "soft"})
        self.assertEqual(sub["url"], f"/api/subtitles/{sub['id']}.vtt")
        self.assertNotIn("vidmoly", json.dumps(out["subtitles"]).lower())   # the provider URL is never exposed
        self.assertEqual(subtitles.lookup(sub["id"])["url"], SUB_URL)
        self.assertEqual(out["audio"], [{"id": "a_en", "lang": "en", "label": "İngilizce",
                                         "stream_ids": [tr["variant_id"], en["variant_id"]], "default": True}])

    def test_unknown_facts_stay_unknown(self):
        self.resolve(self.cands(("okf", "OK.ru", "tr", "Türkçe altyazı"), ("dub", "VidMolly", "tr", "Türkçe dublaj")),
                     self.provider(names={"okf": "OK.ru"}))
        dub, ok = videos.streams("c1", kind="video")["streams"]   # a file with a measured state comes before an unknown one
        self.assertEqual((ok["sub_mode"], ok["hard_lang"], ok["audio_lang"]), ("none", None, None))
        self.assertEqual((dub["sub_mode"], dub["hard_lang"], dub["audio_lang"]), ("none", None, "tr"))
        self.assertEqual((ok["sub_known"], ok["site_lang_hint"], dub["sub_known"]), (False, "tr", True))
        self.assertEqual((ok["label"], dub["label"]), ("OK.ru · auto", "VidMolly · Türkçe dublaj · auto"))   # no claimed subtitle language
        out = videos.streams("c1", kind="video")
        self.assertEqual(out["subtitles"], [])
        self.assertEqual([(a["lang"], a["label"]) for a in out["audio"]], [("tr", "Türkçe"), (None, "Orijinal")])

    def test_source_without_variant_fields_gets_defaults(self):
        db.execute("UPDATE video_sources SET resolver='direct',locator='https://media.example/a.mp4',media_type='mp4' WHERE id='vs1'")
        out = videos.streams("c1", kind="video")
        stream = out["streams"][0]
        self.assertEqual((stream["sub_mode"], stream["audio_lang"], stream["hard_lang"]), ("none", None, None))
        self.assertEqual((stream["sub_known"], stream["site_lang_hint"], stream["mirror_of"]), (False, None, None))
        self.assertTrue(stream["variant_id"].startswith("v_"))
        self.assertEqual((out["subtitles"], [a["lang"] for a in out["audio"]]), ([], [None]))

    def test_resolver_version_bump_refreshes_old_cached_payloads(self):
        self.assertGreaterEqual(videos.RESOLVER_VERSION, 5)
        self.resolve(self.cands(("enf", "VidMolly", "en", "İngilizce altyazı")), self.provider(soft={"enf": [self.SUB]}))
        payload = json.loads(self.row()["resolved_payload"])
        self.assertEqual(payload["resolver_version"], videos.RESOLVER_VERSION)
        self.assertEqual([t["url"] for t in payload["subtitles"]], [SUB_URL])
        self.assertTrue(all("variant_id" in s and "sub_mode" in s for s in payload["streams"]))
        stale = {**payload, "resolver_version": 3}
        stale.pop("subtitles")
        db.execute("UPDATE video_sources SET resolved_payload=? WHERE id='vs1'", (json.dumps(stale),))
        calls = []
        again = self.resolve(self.cands(("enf", "VidMolly", "en", "İngilizce altyazı")),
                             lambda url, **kw: (calls.append(url), self.provider(soft={"enf": [self.SUB]})(url))[1], force=False)
        self.assertEqual(len(calls), 1, "a version-3 payload has no track data: it is resolved again")
        self.assertEqual(again["resolver_version"], videos.RESOLVER_VERSION)

    def test_a_version_4_payload_with_the_old_labels_is_resolved_again(self):
        self.resolve(self.cands(("enf", "VidMolly", "en", "İngilizce altyazı")), self.provider(soft={"enf": [self.SUB]}))
        stale = {**json.loads(self.row()["resolved_payload"]), "resolver_version": 4}
        db.execute("UPDATE video_sources SET resolved_payload=? WHERE id='vs1'", (json.dumps(stale),))
        calls = []
        again = self.resolve(self.cands(("enf", "VidMolly", "en", "İngilizce altyazı")),
                             lambda url, **kw: (calls.append(url), self.provider(soft={"enf": [self.SUB]})(url))[1], force=False)
        self.assertEqual((len(calls), again["resolver_version"]), (1, videos.RESOLVER_VERSION))

    def test_disallowed_subtitle_host_is_not_advertised(self):
        evil = {**self.SUB, "url": "https://evil.example/srt/x.vtt"}
        self.resolve(self.cands(("enf", "VidMolly", "en", "İngilizce altyazı")), self.provider(soft={"enf": [evil]}))
        out = videos.streams("c1", kind="video")
        self.assertEqual(out["subtitles"], [])
        self.assertEqual(out["streams"][0]["sub_mode"], "soft")   # the file still says it carries a track

    def test_route_passes_subtitles_and_audio_through(self):
        payload = self.snw()
        snap = SimpleNamespace(by_id={"c1": {"id": "c1", "type": "movie"}}, episodes={}, source="library",
                               first_episode=lambda _id: None)
        with patch("app.routers.streams.cache.get", return_value=snap), patch.object(videos, "streams", return_value=payload):
            body = stream_routes.streams("c1", episode=None, profile="", kind="video")
        self.assertEqual((body["subtitles"], body["audio"]), (payload["subtitles"], payload["audio"]))
        self.assertEqual(set(body), {"streams", "subtitles", "audio", "resume_position", "duration"})
        # an adapter that knows nothing about audio (mock source) still answers with the field
        with patch("app.routers.streams.cache.get", return_value=SimpleNamespace(
                by_id={"c1": {"id": "c1", "type": "movie"}}, episodes={}, source="mock", first_episode=lambda _id: None)), \
             patch("app.routers.streams.cache.adapter", return_value=SimpleNamespace(
                 streams=lambda item, ep: {"streams": [], "subtitles": [], "duration": 0})):
            body = stream_routes.streams("c1", episode=None, profile="", kind=None)
        self.assertEqual((body["subtitles"], body["audio"]), ([], []))


class ToVttTests(unittest.TestCase):
    def test_webvtt_fixture_is_normalised_and_keeps_its_cues(self):
        out = subtitles.to_vtt(EN_VTT.encode("utf-8"))
        self.assertTrue(out.startswith("WEBVTT\n\n00:00:02.503 --> 00:00:04.672\n* *\n"))
        self.assertIn("00:02:29.517 --> 00:02:31.118\nThe Griffin.", out)
        self.assertEqual(out.count("-->"), EN_VTT.count("-->"))
        self.assertTrue(out.endswith("\n") and not out.endswith("\n\n"))

    def test_srt_bom_crlf_and_time_variants(self):
        srt = ("﻿1\r\n00:00:01,500 --> 00:00:03,000\r\nMerhaba\r\n\r\n2\r\n01:02:03,4 --> 01:02:04,25\r\nİkinci\r\nsatır\r\n"
               "\r\n3\r\n75:00 --> 75:01\r\nUzun\r\n").encode("utf-8")
        out = subtitles.to_vtt(srt)
        self.assertTrue(out.startswith("WEBVTT\n\n1\n00:00:01.500 --> 00:00:03.000\nMerhaba\n"))
        self.assertIn("01:02:03.400 --> 01:02:04.250\nİkinci\nsatır", out)
        self.assertIn("01:15:00.000 --> 01:15:01.000\nUzun", out)   # MM:SS with minutes > 59 and no fraction
        self.assertNotIn("\r", out)
        self.assertNotIn(",", out.split("Merhaba")[0])

    def test_webvtt_header_settings_and_notes(self):
        vtt = "﻿WEBVTT - title\nKind: captions\nLanguage: en\n\nNOTE x\n\ncue1\n00:01.000 --> 00:02.000 line:80% align:start\nHi\n"
        out = subtitles.to_vtt(vtt)
        self.assertTrue(out.startswith("WEBVTT\n\nNOTE x\n"))
        self.assertNotIn("Kind:", out)
        self.assertIn("cue1\n00:00:01.000 --> 00:00:02.000 line:80% align:start\nHi", out)

    def test_legacy_turkish_encoding_and_utf16(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\nÇığ şöyle üşüdü\n"
        self.assertIn("Çığ şöyle üşüdü", subtitles.to_vtt(srt.encode("cp1254")))
        self.assertIn("Çığ şöyle üşüdü", subtitles.to_vtt(srt.encode("utf-16")))

    def test_not_a_subtitle_is_rejected(self):
        for data in (b"", b"<html>blocked</html>", b"WEBVTT\n\nnothing", b"\x00\x01\x02"):
            with self.assertRaises(subtitles.SubtitleNotFound):
                subtitles.to_vtt(data)


class Resp:
    def __init__(self, status=200, headers=None, chunks=(b"x",), pause=0.0):
        self.status_code, self.headers, self._chunks, self._pause = status, headers or {}, chunks, pause

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        for chunk in self._chunks:
            if self._pause:
                time.sleep(self._pause)
            yield chunk


class StreamClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def stream(self, method, url, headers=None, timeout=None, follow_redirects=None):
        self.calls.append({"url": url, "headers": headers, "follow": follow_redirects, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FetchLimitedTests(unittest.TestCase):
    def get(self, client, **kw):
        with patch.object(fetch, "_shared_client", return_value=client):
            return fetch.fetch_limited(SUB_URL, allow=subtitles.allowed, **kw)

    def test_ok_and_headers(self):
        client = StreamClient(Resp(chunks=(b"ab", b"cd")))
        self.assertEqual(self.get(client, headers={"Referer": EMBED}), b"abcd")
        self.assertEqual(client.calls[0]["headers"]["Referer"], EMBED)
        self.assertIs(client.calls[0]["follow"], False)   # redirects are followed by hand, host-checked

    def test_redirects_are_host_checked_on_every_hop(self):
        good = StreamClient(Resp(302, {"location": "/srt/other.vtt"}), Resp(chunks=(b"ok",)))
        self.assertEqual(self.get(good), b"ok")
        self.assertEqual(good.calls[1]["url"], "https://srt.vidmoly.me/srt/other.vtt")
        evil = StreamClient(Resp(302, {"location": "http://169.254.169.254/latest/meta-data"}), Resp(chunks=(b"secret",)))
        with self.assertRaises(fetch.FetchError) as ctx:
            self.get(evil)
        self.assertIn("not allowed", str(ctx.exception))
        self.assertEqual(len(evil.calls), 1, "the disallowed hop is never requested")
        loop = StreamClient(*[Resp(302, {"location": SUB_URL}) for _ in range(6)])
        with self.assertRaises(fetch.FetchError):
            self.get(loop)
        with self.assertRaises(fetch.FetchError):   # a foreign first URL is refused before any request
            with patch.object(fetch, "_shared_client", return_value=StreamClient()) as shared:
                fetch.fetch_limited("https://evil.example/x.vtt", allow=subtitles.allowed)
            shared.assert_not_called()

    def test_size_cap_declared_and_streamed(self):
        with self.assertRaises(fetch.FetchError):
            self.get(StreamClient(Resp(headers={"content-length": "5000000"})), max_bytes=1000)
        with self.assertRaises(fetch.FetchError):
            self.get(StreamClient(Resp(chunks=(b"x" * 600, b"x" * 600))), max_bytes=1000)
        self.assertEqual(len(self.get(StreamClient(Resp(chunks=(b"x" * 500, b"x" * 500))), max_bytes=1000)), 1000)

    def test_total_time_budget_and_http_errors(self):
        t = time.monotonic()
        with self.assertRaises(fetch.FetchError) as ctx:
            self.get(StreamClient(Resp(chunks=(b"x",) * 50, pause=0.05)), timeout=0.2)
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertIn("timeout", str(ctx.exception))
        with self.assertRaises(fetch.FetchError) as ctx:
            self.get(StreamClient(Resp(404)))
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(fetch.FetchError):
            self.get(StreamClient(httpx.ConnectTimeout("t")))


class ProxyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("SUBTITLE_CACHE_DIR", os.path.join(self.tmp.name, "subcache")),):
            p = patch.object(app_config, name, value)
            p.start()
            self.addCleanup(p.stop)
        subtitles.reset()
        self.addCleanup(subtitles.reset)
        subtitles._last_prune = 0.0

    def sid(self):
        return subtitles.register(SUB_URL, referer=EMBED)

    def source(self, data=EN_VTT.encode("utf-8"), **kw):
        return patch.object(subtitles.fetch, "fetch_limited", return_value=data, **kw)


class AllowListTests(ProxyBase):
    def test_hosts(self):
        for url in (SUB_URL, "https://srt.vidmoly.to/x.vtt", "http://srt.vidmolly.biz/x.vtt", "https://srt.vidmoly.net:8443/a/b.vtt"):
            self.assertTrue(subtitles.allowed(url), url)
        for url in ("https://evil.example/x.vtt", "https://srt.vidmoly.me.evil.com/x.vtt", "https://vidmoly.me/x.vtt",
                    "https://xsrt.vidmoly.me/x.vtt", "https://srt.vidmoly/x.vtt", "http://127.0.0.1/x.vtt",
                    "http://169.254.169.254/x.vtt", "ftp://srt.vidmoly.me/x.vtt", "file:///etc/passwd", "srt.vidmoly.me/x", "",
                    "https://srt.vidmoly.me@evil.example/x.vtt"):
            self.assertFalse(subtitles.allowed(url), url)

    def test_env_list_extends_the_builtin_hosts(self):
        self.assertFalse(subtitles.allowed("https://subs.cdn.example/a.vtt"))
        with patch.object(app_config, "SUBTITLE_HOSTS", app_config.SUBTITLE_HOSTS + ["cdn.example"]):
            self.assertTrue(subtitles.allowed("https://subs.cdn.example/a.vtt"))
            self.assertTrue(subtitles.allowed(SUB_URL))
            self.assertFalse(subtitles.allowed("https://notcdn.example/a.vtt"))
        self.assertIn("srt.vidmoly.*", app_config.SUBTITLE_HOSTS)


class RegistryTests(ProxyBase):
    def test_id_is_stable_hides_the_url_and_survives_a_restart(self):
        a = self.sid()
        self.assertEqual(a, self.sid())
        self.assertRegex(a, r"^[0-9a-f]{20}$")
        self.assertNotIn("vidmoly", a)
        self.assertNotEqual(a, subtitles.register("https://srt.vidmoly.me/srt/02715/other.vtt"))
        self.assertIsNone(subtitles.register("https://evil.example/a.vtt"))
        subtitles.reset()   # process restart: the registry is read back from disk
        self.assertEqual(subtitles.lookup(a), {"url": SUB_URL, "referer": EMBED})
        self.assertIsNone(subtitles.lookup("0" * 20))
        self.assertIsNone(subtitles.lookup("../../etc/passwd"))


class ProxyTests(ProxyBase):
    def test_fetch_convert_cache_and_etag(self):
        sid = self.sid()
        with self.source() as fetched:
            vtt, tag = subtitles.get(sid)
            again, tag2 = subtitles.get(sid)
        self.assertEqual(fetched.call_count, 1, "second request is served from the disk cache")
        self.assertEqual((vtt, tag), (again, tag2))
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:02.503"))
        self.assertRegex(tag, r'^"[0-9a-f]{20}"$')
        kwargs = fetched.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Referer"], EMBED)
        self.assertEqual((kwargs["max_bytes"], kwargs["timeout"]), (app_config.SUBTITLE_MAX_BYTES, app_config.SUBTITLE_TIMEOUT))
        self.assertEqual((app_config.SUBTITLE_MAX_BYTES, app_config.SUBTITLE_TIMEOUT), (1_000_000, 5.0))
        self.assertIs(kwargs["allow"], subtitles.allowed)
        self.assertTrue(os.path.isfile(os.path.join(app_config.SUBTITLE_CACHE_DIR, sid + ".vtt")))
        subtitles.reset()   # restart: cache and registry come from disk, no download
        with self.source() as after_restart:
            self.assertEqual(subtitles.get(sid)[0], vtt)
        after_restart.assert_not_called()

    def test_ttl_refreshes_and_srt_sources_are_converted(self):
        sid = self.sid()
        srt = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        with self.source(srt) as first:
            self.assertIn("00:00:01.000 --> 00:00:02.000", subtitles.get(sid)[0])
        path = os.path.join(app_config.SUBTITLE_CACHE_DIR, sid + ".vtt")
        old = time.time() - app_config.SUBTITLE_CACHE_TTL - 10
        os.utime(path, (old, old))
        with self.source(EN_VTT.encode()) as second:
            self.assertIn("The Griffin.", subtitles.get(sid)[0])
        self.assertEqual((first.call_count, second.call_count), (1, 1))

    def test_dead_source_is_404_and_not_hammered(self):
        sid = self.sid()
        with patch.object(subtitles.fetch, "fetch_limited", side_effect=fetch.FetchError("HTTP 404", 404)) as dead:
            with self.assertRaises(subtitles.SubtitleNotFound):
                subtitles.get(sid)
            with self.assertRaises(subtitles.SubtitleNotFound):
                subtitles.get(sid)
        self.assertEqual(dead.call_count, 1, "a dead source is not asked again within NEG_TTL")
        subtitles._neg.clear()
        with self.source(b"<html>Access denied</html>"):   # an HTML block page is not a subtitle either
            with self.assertRaises(subtitles.SubtitleNotFound):
                subtitles.get(sid)
        with self.assertRaises(subtitles.SubtitleNotFound):
            subtitles.get("f" * 20)   # never registered

    def test_stale_copy_beats_an_error(self):
        sid = self.sid()
        with self.source():
            good = subtitles.get(sid)[0]
        path = os.path.join(app_config.SUBTITLE_CACHE_DIR, sid + ".vtt")
        old = time.time() - app_config.SUBTITLE_CACHE_TTL - 10
        os.utime(path, (old, old))
        with patch.object(subtitles.fetch, "fetch_limited", side_effect=fetch.FetchError("down")):
            self.assertEqual(subtitles.get(sid)[0], good)

    def test_oversized_source_is_refused(self):
        sid = self.sid()
        client = StreamClient(Resp(chunks=(b"x" * 600_000, b"x" * 600_000)))
        with patch.object(fetch, "_shared_client", return_value=client):
            with self.assertRaises(subtitles.SubtitleNotFound):
                subtitles.get(sid)

    def test_concurrent_requests_share_one_download(self):
        import threading
        sid = self.sid()
        calls = []

        def slow(*args, **kwargs):
            calls.append(1)
            time.sleep(0.15)
            return EN_VTT.encode("utf-8")

        results = []
        with patch.object(subtitles.fetch, "fetch_limited", side_effect=slow):
            threads = [threading.Thread(target=lambda: results.append(subtitles.get(sid)[1])) for _ in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual((len(calls), len(set(results))), (1, 1))


class RouteTests(ProxyBase):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.c = TestClient(app)

    def test_headers_etag_and_304(self):
        sid = self.sid()
        with self.source():
            r = self.c.get(f"/api/subtitles/{sid}.vtt")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/vtt"))
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
        self.assertIn("max-age", r.headers["cache-control"])
        self.assertTrue(r.text.startswith("WEBVTT\n\n"))
        tag = r.headers["etag"]
        r2 = self.c.get(f"/api/subtitles/{sid}.vtt", headers={"If-None-Match": tag})   # served from the disk cache
        self.assertEqual((r2.status_code, r2.content), (304, b""))
        r3 = self.c.get(f"/api/subtitles/{sid}.vtt", headers={"If-None-Match": '"other"'})
        self.assertEqual(r3.status_code, 200)

    def test_404_for_unknown_invalid_and_dead(self):
        for path in ("/api/subtitles/" + "a" * 20 + ".vtt", "/api/subtitles/nope.vtt", "/api/subtitles/x.vtt"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.json()["error"]["code"], "not_found")
        sid = self.sid()
        with patch.object(subtitles.fetch, "fetch_limited", side_effect=fetch.FetchError("gone", 404)):
            r = self.c.get(f"/api/subtitles/{sid}.vtt", headers={"Origin": "http://tv.local"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.headers["access-control-allow-origin"], "*")   # the TV app can read the 404 (silent "Kapalı")


if __name__ == "__main__":
    unittest.main()
