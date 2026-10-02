package com.diziflix.app.domain

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.DetailExtras
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Season
import com.diziflix.app.data.net.ApiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Hidrasyon sözleşmesinin saf mantığı: "hazır / alınamadı / sürüyor" kararı, sabitler ve bildirim metinleri. */
class HydrateLogicTest {

    private fun detail(type: String, hydrating: Boolean, seasons: Int = 0) = Detail(
        Item(id = "x", type = type, title = "Başlık"),
        DetailExtras(seasons = List(seasons) { Season(season = it + 1) }, hydrating = hydrating),
    )

    @Test
    fun contractConstants() {
        assertEquals(3_000L, HydrateLogic.POLL_INTERVAL_MS)
        assertEquals(120_000L, HydrateLogic.TIMEOUT_MS)
        assertEquals(3, HydrateLogic.MAX_CONCURRENT)
    }

    @Test
    fun hydratingTrueIsAlwaysPending() {
        assertEquals(HydrateOutcome.Pending, HydrateLogic.outcome(detail("series", hydrating = true)))
        assertEquals(HydrateOutcome.Pending, HydrateLogic.outcome(detail("series", hydrating = true, seasons = 2)))
        assertEquals(HydrateOutcome.Pending, HydrateLogic.outcome(detail("movie", hydrating = true)))
    }

    @Test
    fun finishedMovieIsReadyEvenWithoutSeasons() {
        assertEquals(HydrateOutcome.Ready, HydrateLogic.outcome(detail("movie", hydrating = false)))
    }

    @Test
    fun finishedSeriesWithSeasonsIsReady() {
        assertEquals(HydrateOutcome.Ready, HydrateLogic.outcome(detail("series", hydrating = false, seasons = 1)))
    }

    @Test
    fun finishedSeriesWithoutSeasonsIsUnavailable() {
        assertEquals(HydrateOutcome.Unavailable, HydrateLogic.outcome(detail("series", hydrating = false)))
    }

    @Test
    fun missingHydratingFieldParsesAsFalse() {
        val json = ApiJson.instance
        for (name in listOf("detail_movie.json", "detail_series.json", "detail_unavailable.json")) {
            val element = json.parseToJsonElement(Fixtures.text(name))
            val d = Detail(
                json.decodeFromJsonElement(Item.serializer(), element),
                json.decodeFromJsonElement(DetailExtras.serializer(), element),
            )
            assertFalse("$name: alan yokken hydrating false olmalı", d.hydrating)
        }
        // sunucu alanı yollarsa okunur; null/eksik-tip değer varsayılana düşer (coerceInputValues)
        val on = json.decodeFromString(DetailExtras.serializer(), """{"hydrating":true}""")
        assertTrue(on.hydrating)
        assertFalse(json.decodeFromString(DetailExtras.serializer(), """{"hydrating":null}""").hydrating)
    }

    @Test
    fun toastMessagesAreShortTurkish() {
        assertEquals("«Dexter» izlemeye hazır", HydrateLogic.message(HydrateEvent.Ready("a", "Dexter")))
        assertEquals(
            "Bölümler şu an alınamadı, daha sonra tekrar deneyin",
            HydrateLogic.message(HydrateEvent.Unavailable("a", "Dexter")),
        )
        assertEquals("«Yapım» izlemeye hazır", HydrateLogic.message(HydrateEvent.Ready("a", "")))
    }

    // ---------------------------------------------------------------- "kaynak yok" (alınamadı DEĞİL)

    private fun noSource(type: String, hydrating: Boolean = false, seasons: Int = 0) = Detail(
        Item(
            id = "x", type = type, title = "Başlık",
            availability = Availability(state = "unavailable", reason = "no_video_source", hasTrailer = true),
        ),
        DetailExtras(seasons = List(seasons) { Season(season = it + 1) }, hydrating = hydrating),
    )

    @Test
    fun noVideoSource_isNoSourceOutcome_notUnavailable() {
        assertEquals(HydrateOutcome.NoSource, HydrateLogic.outcome(noSource("series")))
        assertEquals(HydrateOutcome.NoSource, HydrateLogic.outcome(noSource("movie")))       // film "hazır" DEĞİL
        assertEquals(HydrateOutcome.NoSource, HydrateLogic.outcome(noSource("series", seasons = 2)))
        // hidrasyon sürerken karar yok
        assertEquals(HydrateOutcome.Pending, HydrateLogic.outcome(noSource("series", hydrating = true)))
    }

    @Test
    fun unavailableWithoutNoVideoSourceReason_keepsOldDecision() {
        val other = Detail(
            Item(id = "x", type = "series", availability = Availability(state = "unavailable", reason = "sources_unavailable")),
            DetailExtras(),
        )
        assertEquals(HydrateOutcome.Unavailable, HydrateLogic.outcome(other))   // gerçek "alınamadı" yolu korunur
    }

    @Test
    fun noSourceToast_saysNoSource_notFetchFailure() {
        assertEquals("Bu dizi için henüz izleme kaynağı yok.", HydrateLogic.message(HydrateEvent.NoSource("a", "Dexter", isSeries = true)))
        assertEquals("Bu film için henüz izleme kaynağı yok.", HydrateLogic.message(HydrateEvent.NoSource("a", "Alien", isSeries = false)))
    }

    @Test
    fun detailLogic_noSourceTexts() {
        assertEquals("Bu dizi için henüz izleme kaynağı yok.", DetailLogic.emptySeasonsText(noSource("series")))
        assertEquals("Bu film için henüz izleme kaynağı yok.", DetailLogic.emptySeasonsText(noSource("movie")))
        assertEquals("Bu dizinin bölüm bilgileri henüz eklenmedi.", DetailLogic.emptySeasonsText(detail("series", hydrating = false)))
    }

    @Test
    fun parsedUnavailableFixture_isNoSource() {
        val json = ApiJson.instance
        val element = json.parseToJsonElement(Fixtures.text("detail_unavailable.json"))
        val d = Detail(
            json.decodeFromJsonElement(Item.serializer(), element),
            json.decodeFromJsonElement(DetailExtras.serializer(), element),
        )
        assertTrue(DetailLogic.isNoSource(d))
        assertTrue(d.seasons.isEmpty())
    }
}
