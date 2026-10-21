package com.diziflix.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** "Varsayılan altyazı dili" + "En yüksek kalite" tercihleri (Tizen Ayarlar ile aynı seçenekler/varsayılanlar). */
class PlayPrefsTest {

    @Test
    fun options_matchTizenSettings() {
        assertEquals(listOf("tr" to "Türkçe", "en" to "İngilizce", "off" to "Kapalı"), PlayPrefs.SUB_OPTIONS)
        assertEquals(listOf("1080" to "1080p", "1440" to "1440p", "auto" to "Otomatik"), PlayPrefs.QUALITY_OPTIONS)
    }

    @Test
    fun normalize_rejectsUnknownValues() {
        assertEquals("1440", PlayPrefs.normalizeQuality("1440"))
        assertNull(PlayPrefs.normalizeQuality("720"))
        assertNull(PlayPrefs.normalizeQuality(null))
        assertEquals("off", PlayPrefs.normalizeSub("off"))
        assertNull(PlayPrefs.normalizeSub("de"))
    }

    @Test
    fun effectiveQuality_defaultsToTizenOnTvAndUnlimitedElsewhere() {
        assertEquals("1080", PlayPrefs.effectiveQuality(null, isTv = true))
        assertEquals("auto", PlayPrefs.effectiveQuality(null, isTv = false))
        assertEquals("1440", PlayPrefs.effectiveQuality("1440", isTv = false))
        assertEquals("auto", PlayPrefs.effectiveQuality("auto", isTv = true))
        assertEquals("1080", PlayPrefs.effectiveQuality("bozuk", isTv = true))
    }

    @Test
    fun qualityCap_mapsPreferenceToHeight() {
        assertEquals(1080, PlayPrefs.qualityCap("1080"))
        assertEquals(1440, PlayPrefs.qualityCap("1440"))
        assertEquals(0, PlayPrefs.qualityCap("auto"))
    }

    @Test
    fun displaySub_defaultsToTurkish() {
        assertEquals("tr", PlayPrefs.displaySub(null))
        assertEquals("off", PlayPrefs.displaySub("off"))
        assertEquals("tr", PlayPrefs.displaySub("fr"))
    }
}
