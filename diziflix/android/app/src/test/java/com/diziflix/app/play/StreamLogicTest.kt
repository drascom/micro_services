package com.diziflix.app.play

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.StreamsResponse
import com.diziflix.app.data.model.Subtitle
import com.diziflix.app.data.model.VideoStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamLogicTest {

    private fun stream(url: String, type: String = "", label: String = "", provider: String? = null, quality: String = "") =
        VideoStream(url = url, type = type, label = label, provider = provider, quality = quality)

    // ---------------------------------------------------------------- embed / sıralama

    @Test
    fun isEmbed_followsTvClientRules() {
        assertTrue(StreamLogic.isEmbed(stream("https://www.youtube.com/embed/abc", type = "embed")))
        assertFalse(StreamLogic.isEmbed(stream("https://cdn/x/master.m3u8?t=1", type = "hls")))
        assertFalse(StreamLogic.isEmbed(stream("https://cdn/x/video.mp4", type = "mp4")))
        // type yoksa adresin uzantısına bakılır (sorgu ve # atılır)
        assertFalse(StreamLogic.isEmbed(stream("https://cdn/x/master.m3u8?sig=a.b.c#frag")))
        assertFalse(StreamLogic.isEmbed(stream("https://cdn/x/clip.MP4")))
        assertTrue(StreamLogic.isEmbed(stream("https://player.example.com/e/12345")))
        assertTrue(StreamLogic.isEmbed(stream("https://cdn/x/page.html?file=a.mp4")))
    }

    @Test
    fun order_putsDirectStreamsFirstKeepingServerOrder_andDropsBlankUrls() {
        val list = listOf(
            stream("https://e1.example/embed", type = "embed"),
            stream("https://cdn/a.m3u8", type = "hls"),
            stream("", type = "hls"),
            stream("https://e2.example/embed", type = "embed"),
            stream("https://cdn/b.mp4", type = "mp4"),
        )
        val ordered = StreamLogic.order(list)
        assertEquals(
            listOf("https://cdn/a.m3u8", "https://cdn/b.mp4", "https://e1.example/embed", "https://e2.example/embed"),
            ordered.map { it.url },
        )
    }

    @Test
    fun order_realEpisodeStreamsKeepServerOrder() {
        val res = Fixtures.parse("streams_episode.json", StreamsResponse.serializer())
        val ordered = StreamLogic.order(res.streams)
        assertEquals(res.streams.map { it.url }, ordered.map { it.url })
    }

    @Test
    fun realTrailerStreamIsEmbed() {
        val res = Fixtures.parse("streams_trailer.json", StreamsResponse.serializer())
        assertTrue(StreamLogic.isEmbed(res.streams[0]))
        assertTrue(StreamLogic.isYouTubeEmbed(res.streams[0].url))
    }

    // ---------------------------------------------------------------- etiketler

    @Test
    fun uniqueLabels_keepsServerLabelAndNumbersDuplicates() {
        val labels = StreamLogic.uniqueLabels(
            listOf(
                stream("u1", label = "VidMolly · Türkçe altyazı · auto"),
                stream("u2", label = "OK.ru · Türkçe altyazı · 1080p"),
                stream("u3", label = "VidMolly · Türkçe altyazı · auto"),
                stream("u4", label = "VidMolly · Türkçe altyazı · auto"),
            ),
        )
        assertEquals(
            listOf(
                "VidMolly · Türkçe altyazı · auto",
                "OK.ru · Türkçe altyazı · 1080p",
                "VidMolly · Türkçe altyazı · auto (2)",
                "VidMolly · Türkçe altyazı · auto (3)",
            ),
            labels,
        )
    }

    @Test
    fun uniqueLabels_realEpisodeStreams() {
        val res = Fixtures.parse("streams_episode.json", StreamsResponse.serializer())
        assertEquals(
            listOf("VidMolly · auto", "VidMolly · auto (2)", "VidMolly · auto (3)"),
            StreamLogic.uniqueLabels(res.streams),
        )
    }

    @Test
    fun uniqueLabels_fallsBackToProviderQualityThenIndex() {
        val labels = StreamLogic.uniqueLabels(
            listOf(
                stream("u1", label = "  ", provider = "VidMolly", quality = "720p"),
                stream("u2", label = "", provider = " ", quality = "auto"),
                stream("u3"),
            ),
        )
        assertEquals(listOf("VidMolly · 720p", "auto", "Kaynak 3"), labels)
    }

    // ---------------------------------------------------------------- altyazı (API.md "Ses / altyazı izleri")

    private fun sub(lang: String?, url: String = "https://x/s.vtt", ids: List<String>? = null, default: Boolean = false) =
        Subtitle(id = "s", lang = lang, label = "L", url = url, streamIds = ids, isDefault = default)

    @Test
    fun subtitlesFor_matchesVariantIds_nullMeansAllStreams() {
        val soft = VideoStream(url = "a", variantId = "v_en", subMode = "soft")
        val hard = VideoStream(url = "b", variantId = "v_tr", subMode = "hard", hardLang = "tr")
        val legacy = VideoStream(url = "c") // eski sunucu: variant_id yok
        val subs = listOf(
            sub("en", ids = listOf("v_en")),
            sub("de", ids = null),
            sub("fr", ids = emptyList()),
            sub("es", url = "  ", ids = null),
        )
        assertEquals(listOf("en", "de"), StreamLogic.subtitlesFor(soft, subs).map { it.lang })
        assertEquals(listOf("de"), StreamLogic.subtitlesFor(hard, subs).map { it.lang })
        assertEquals(listOf("de"), StreamLogic.subtitlesFor(legacy, subs).map { it.lang })
    }

    @Test
    fun defaultSubtitleIndex_prefersServerDefault_thenTurkish_thenNone() {
        assertEquals(0, StreamLogic.defaultSubtitleIndex(listOf(sub("en", default = true))))
        assertEquals(1, StreamLogic.defaultSubtitleIndex(listOf(sub("fr"), sub("tr"))))
        assertEquals(1, StreamLogic.defaultSubtitleIndex(listOf(sub("tr"), sub("en", default = true))))
        assertNull(StreamLogic.defaultSubtitleIndex(listOf(sub("fr"), sub(null))))
        assertNull(StreamLogic.defaultSubtitleIndex(emptyList()))
    }

    @Test
    fun hardSubNote_onlyForBurnedInSubtitles() {
        assertNull(StreamLogic.hardSubNote(VideoStream(url = "a", subMode = "soft")))
        assertNull(StreamLogic.hardSubNote(VideoStream(url = "a", subMode = "none")))
        assertNull(StreamLogic.hardSubNote(VideoStream(url = "a")))
        val withLang = StreamLogic.hardSubNote(VideoStream(url = "a", subMode = "hard", hardLang = "tr"))!!
        assertTrue(withLang.startsWith("Altyazı görüntüye gömülü"))
        assertTrue(withLang.contains("kapatılamaz"))
        assertEquals(
            "Altyazı görüntüye gömülü (kapatılamaz)",
            StreamLogic.hardSubNote(VideoStream(url = "a", subMode = "hard")),
        )
    }

    // ---------------------------------------------------------------- embed sayfası yardımcıları

    @Test
    fun withAutoplay_addsParameterOnce() {
        assertEquals("https://www.youtube.com/embed/x?autoplay=1", StreamLogic.withAutoplay("https://www.youtube.com/embed/x"))
        assertEquals("https://e/x?a=1&autoplay=1", StreamLogic.withAutoplay("https://e/x?a=1"))
        assertEquals("https://e/x?autoplay=0", StreamLogic.withAutoplay("https://e/x?autoplay=0"))
    }

    @Test
    fun isYouTubeEmbed_matchesHostsOnly() {
        assertTrue(StreamLogic.isYouTubeEmbed("https://www.youtube.com/embed/abc"))
        assertTrue(StreamLogic.isYouTubeEmbed("https://youtube.com/embed/abc"))
        assertTrue(StreamLogic.isYouTubeEmbed("https://www.youtube-nocookie.com/embed/abc"))
        assertFalse(StreamLogic.isYouTubeEmbed("https://notyoutube.com/embed/abc"))
        assertFalse(StreamLogic.isYouTubeEmbed("https://example.com/youtube.com/embed"))
    }

    // ---------------------------------------------------------------- hata kodu eşleme

    @Test
    fun playbackErrors_mapExoCodesToServerReportCodes() {
        assertEquals("timeout", PlaybackErrors.toReportCode(1003))
        assertEquals("timeout", PlaybackErrors.toReportCode(2002))
        assertEquals("network", PlaybackErrors.toReportCode(2001))
        assertEquals("network", PlaybackErrors.toReportCode(2004))
        assertEquals("unsupported", PlaybackErrors.toReportCode(3003))
        assertEquals("playback_failed", PlaybackErrors.toReportCode(3001))
        assertEquals("decode", PlaybackErrors.toReportCode(4001))
        assertEquals("decode", PlaybackErrors.toReportCode(4003))
        assertEquals("unsupported", PlaybackErrors.toReportCode(4004))
        assertEquals("unsupported", PlaybackErrors.toReportCode(4005))
        assertEquals("playback_failed", PlaybackErrors.toReportCode(1000))
        assertEquals("playback_failed", PlaybackErrors.toReportCode(5001))
    }

    // ---------------------------------------------------------------- MIME ipucu + hata ayrıntısı

    @Test
    fun mimeTypeFor_followsTypeNotUrlExtension() {
        assertEquals("application/x-mpegURL", StreamLogic.mimeTypeFor(stream("https://cdn.example/play/master.txt", "hls")))
        assertEquals("application/x-mpegURL", StreamLogic.mimeTypeFor(stream("https://tv.example/api/stream-proxy/tok/index.m3u8", "HLS")))
        assertEquals("video/mp4", StreamLogic.mimeTypeFor(stream("https://tv.example/api/stream-proxy/abc123", "mp4")))
        assertEquals("video/mp4", StreamLogic.mimeTypeFor(stream("https://cdn.example/file.m3u8", " mp4 ")))
        assertNull(StreamLogic.mimeTypeFor(stream("https://cdn.example/a.mp4", "")))
        assertNull(StreamLogic.mimeTypeFor(stream("https://www.youtube.com/embed/x", "embed")))
        assertEquals("application/x-mpegURL", androidx.media3.common.MimeTypes.APPLICATION_M3U8)
    }

    @Test
    fun hlsTxtAndExtensionlessMp4_areNotEmbeds() {
        assertFalse(StreamLogic.isEmbed(stream("https://cdn.example/play/master.txt", "hls")))
        assertFalse(StreamLogic.isEmbed(stream("https://tv.example/api/stream-proxy/abc123", "mp4")))
        assertTrue(StreamLogic.isEmbed(stream("https://cdn.example/video.mp4", "embed")))
    }

    @Test
    fun playbackErrors_detailIsCompactAndClipped() {
        assertEquals("exo:ERROR_CODE_IO_NETWORK_CONNECTION_FAILED", PlaybackErrors.exoDetail("ERROR_CODE_IO_NETWORK_CONNECTION_FAILED"))
        assertEquals(
            "exo:ERROR_CODE_IO_BAD_HTTP_STATUS/InvalidResponseCodeException/http403",
            PlaybackErrors.exoDetail("ERROR_CODE_IO_BAD_HTTP_STATUS", "InvalidResponseCodeException", 403),
        )
        assertEquals("exo:?", PlaybackErrors.exoDetail(null))
        assertEquals("a b", PlaybackErrors.clipDetail("  a \n b  "))
        assertEquals("", PlaybackErrors.clipDetail(null))
        assertEquals(PlaybackErrors.MAX_DETAIL, PlaybackErrors.clipDetail("y".repeat(500)).length)
        assertEquals(120, PlaybackErrors.exoDetail("E", "c".repeat(300)).length)
    }

    @Test
    fun playbackErrors_onlyProduceCodesTheServerAccepts() {
        // /api/playback-report code alanı: "", network, timeout, playback_failed, unsupported, decode, autoplay, aborted, offline
        val accepted = setOf("", "network", "timeout", "playback_failed", "unsupported", "decode", "autoplay", "aborted", "offline")
        for (code in 0..7000) {
            assertTrue("kod $code", PlaybackErrors.toReportCode(code) in accepted)
        }
    }
}
