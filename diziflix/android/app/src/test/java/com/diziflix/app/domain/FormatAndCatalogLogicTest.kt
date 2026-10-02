package com.diziflix.app.domain

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.CatalogResponse
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Progress
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FormatAndCatalogLogicTest {

    @Test
    fun clock_formatsMinutesAndHours() {
        assertEquals("0:00", Format.clock(0))
        assertEquals("0:00", Format.clock(-5))
        assertEquals("1:05", Format.clock(65_000))
        assertEquals("59:59", Format.clock(3_599_999))
        assertEquals("1:02:03", Format.clock(3_723_000))
    }

    @Test
    fun minutes_formatsRuntime() {
        assertEquals("45 dk", Format.minutes(45.0))
        assertEquals("59 dk", Format.minutes(58.6))
        assertEquals("1 sa 30 dk", Format.minutes(90.0))
        assertEquals("2 sa 0 dk", Format.minutes(120.0))
        assertEquals("", Format.minutes(0.0))
        assertEquals("", Format.minutes(null))
    }

    @Test
    fun date_formatsIsoDatesInTurkish() {
        assertEquals("24 Tem 2026", Format.date("2026-07-24"))
        assertEquals("1 Oca 2027", Format.date("2027-01-01T00:00:00Z"))
        assertEquals("", Format.date(null))
        assertEquals("", Format.date("dün"))
        assertEquals("", Format.date("2026-13-01"))
    }

    @Test
    fun truncate_cutsAtWordBoundary() {
        assertEquals("kısa", Format.truncate("  kısa  ", 20))
        assertEquals("aaa bbb…", Format.truncate("aaa bbb ccc ddd", 9))
        assertEquals("a b c…", Format.truncate("a\n\nb   c d e f g", 8))
        assertEquals("", Format.truncate(null, 5))
    }

    // ---------------------------------------------------------------- kart mantığı

    @Test
    fun cardEpisodeId_continueRowOpensProgressEpisode() {
        val boot = Fixtures.parse("boot.json", BootResponse.serializer())
        val continueSeries = boot.rows.first { it.id == "continue" }.items[0]
        assertEquals("tmdb_tv_103516:s4:e1", CatalogLogic.cardEpisodeId(continueSeries, "continue"))
        // aynı kart başka satırda bölüm hedefi taşımaz
        assertNull(CatalogLogic.cardEpisodeId(continueSeries, "series"))
    }

    @Test
    fun cardEpisodeId_episodeCardUsesEpisodeId_movieNever() {
        val episodeCard = Item(id = "s1", type = "series", cardKind = "episode", episodeId = "e9")
        assertEquals("e9", CatalogLogic.cardEpisodeId(episodeCard, "latest_episodes"))
        val movie = Item(id = "m1", type = "movie", progress = Progress(episodeId = "m1", pct = 10.0))
        assertNull(CatalogLogic.cardEpisodeId(movie, "continue"))
    }

    @Test
    fun statusNote_matchesTvTexts() {
        val res = Fixtures.parse("catalog_unavailable.json", CatalogResponse.serializer())
        val series = res.items[0]
        assertEquals("Yalnızca fragman", CatalogLogic.statusNote(series, isSearch = false))
        assertEquals("Açınca bölümler yüklenecek", CatalogLogic.statusNote(series, isSearch = true))

        val noTrailer = Item(id = "x", availability = Availability(state = "unavailable", hasTrailer = false))
        assertEquals("İzleme kaynağı yok", CatalogLogic.statusNote(noTrailer, isSearch = false))
        assertEquals("İzleme kaynağı yok", CatalogLogic.statusNote(noTrailer, isSearch = true)) // film

        val check = Item(id = "y", availability = Availability(state = "check_required"))
        assertEquals("Kaynak kontrol ediliyor", CatalogLogic.statusNote(check, isSearch = false))
        assertNull(CatalogLogic.statusNote(Item(id = "z"), isSearch = false))
    }
}
