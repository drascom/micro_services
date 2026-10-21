package com.diziflix.app.play

import com.diziflix.app.data.model.Subtitle
import com.diziflix.app.data.model.VideoStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Kalite sınırı (akış sıralaması) ve altyazı dili tercihi: Tizen playflow.js / tracks.js kuralları. */
class StreamPrefsTest {

    private fun stream(url: String, type: String = "mp4", quality: String = "", label: String = "") =
        VideoStream(url = url, type = type, quality = quality, label = label)

    @Test
    fun streamHeight_parsesQualityThenLabel() {
        assertEquals(1080, StreamLogic.streamHeight(stream("u", quality = "1080p")))
        assertEquals(1080, StreamLogic.streamHeight(stream("u", quality = "1920x1080")))
        assertEquals(720, StreamLogic.streamHeight(stream("u", label = "OK.ru · 720p")))
        assertEquals(2160, StreamLogic.streamHeight(stream("u", label = "VidMolly 4K")))
        assertEquals(1440, StreamLogic.streamHeight(stream("u", label = "Kaynak 2K")))
        assertEquals(0, StreamLogic.streamHeight(stream("u", label = "VidMolly · Türkçe altyazı")))
        // quality önce bakılır
        assertEquals(480, StreamLogic.streamHeight(stream("u", quality = "480p", label = "1080p")))
        // 'p' harfini izleyen harf yükseklik sayılmaz ("1080pro")
        assertEquals(0, StreamLogic.streamHeight(stream("u", label = "1080pro")))
    }

    @Test
    fun order_withCap_movesOverCapDirectStreamsBehindDirectButBeforeEmbeds() {
        val list = listOf(
            stream("https://cdn/2160.mp4", label = "4K"),
            stream("https://cdn/1080.mp4", quality = "1080p"),
            stream("https://e/embed", type = "embed"),
            stream("https://cdn/unknown.m3u8", type = "hls"),
            stream("https://cdn/1440.mp4", label = "1440p"),
        )
        assertEquals(
            listOf("https://cdn/1080.mp4", "https://cdn/unknown.m3u8", "https://cdn/2160.mp4", "https://cdn/1440.mp4", "https://e/embed"),
            StreamLogic.order(list, qualityCap = 1080).map { it.url },
        )
        assertEquals(
            listOf("https://cdn/1080.mp4", "https://cdn/unknown.m3u8", "https://cdn/1440.mp4", "https://cdn/2160.mp4", "https://e/embed"),
            StreamLogic.order(list, qualityCap = 1440).map { it.url },
        )
        // sınırsız: eski davranış (doğrudan akışlar sunucu sırasında, embed sonda)
        assertEquals(
            listOf("https://cdn/2160.mp4", "https://cdn/1080.mp4", "https://cdn/unknown.m3u8", "https://cdn/1440.mp4", "https://e/embed"),
            StreamLogic.order(list, qualityCap = 0).map { it.url },
        )
        assertEquals(StreamLogic.order(list, qualityCap = 0), StreamLogic.order(list))
    }

    private fun sub(lang: String?, default: Boolean = false) =
        Subtitle(id = lang ?: "x", lang = lang, url = "u", isDefault = default)

    @Test
    fun defaultSubtitle_withoutPreferenceKeepsOldRule() {
        assertEquals(0, StreamLogic.defaultSubtitleIndex(listOf(sub("en", default = true)), pref = null))
        assertEquals(1, StreamLogic.defaultSubtitleIndex(listOf(sub("fr"), sub("tr")), pref = null))
        assertNull(StreamLogic.defaultSubtitleIndex(listOf(sub("fr")), pref = null))
    }

    @Test
    fun defaultSubtitle_preferenceWinsThenFallsBack() {
        val subs = listOf(sub("tr", default = true), sub("en"))
        assertEquals(1, StreamLogic.defaultSubtitleIndex(subs, pref = "en"))
        assertEquals(0, StreamLogic.defaultSubtitleIndex(subs, pref = "tr"))
        // kapalı tercihi: hiçbiri seçilmez (sunucu varsayılanı bile)
        assertNull(StreamLogic.defaultSubtitleIndex(subs, pref = "off"))
        // tercih edilen dilde iz yok: eski kural (sunucu varsayılanı > Türkçe)
        assertEquals(0, StreamLogic.defaultSubtitleIndex(subs, pref = "de"))
        assertEquals(1, StreamLogic.defaultSubtitleIndex(listOf(sub("fr"), sub("tr")), pref = "en"))
    }
}
