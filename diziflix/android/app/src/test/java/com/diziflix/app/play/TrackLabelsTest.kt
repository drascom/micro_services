package com.diziflix.app.play

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackLabelsTest {

    @Test
    fun explicitLabelWins() {
        assertEquals("Türkçe (Dublaj)", TrackLabels.label("tr", "Türkçe (Dublaj)", 0, "Ses"))
        assertEquals("Orijinal", TrackLabels.label(null, "  Orijinal ", 3, "Ses"))
    }

    @Test
    fun undeterminedLanguageFallsBackToNumberedLabel() {
        assertEquals("Ses 1", TrackLabels.label(null, null, 0, "Ses"))
        assertEquals("Ses 2", TrackLabels.label("", "", 1, "Ses"))
        assertEquals("Altyazı 3", TrackLabels.label("und", null, 2, "Altyazı"))
    }

    @Test
    fun knownLanguageProducesAReadableName() {
        val label = TrackLabels.label("tr", null, 0, "Ses")
        assertTrue(label.isNotBlank())
        assertFalse(label.startsWith("Ses "))
    }

    @Test
    fun uniquifyNumbersDuplicates() {
        assertEquals(listOf("A", "A (2)", "B", "A (3)"), TrackLabels.uniquify(listOf("A", "A", "B", "A")))
    }

    @Test
    fun languageName_isNullForUndeterminedCodes() {
        assertNull(TrackLabels.languageName(null))
        assertNull(TrackLabels.languageName(""))
        assertNull(TrackLabels.languageName("und"))
        assertTrue(TrackLabels.languageName("tr")!!.isNotBlank())
    }
}
