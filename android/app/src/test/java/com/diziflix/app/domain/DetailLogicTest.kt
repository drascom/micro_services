package com.diziflix.app.domain

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.EpisodeProgress
import com.diziflix.app.data.model.Season
import com.diziflix.app.data.net.ApiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DetailLogicTest {

    private val today = "2026-09-30"

    private fun detail(name: String): Detail {
        val json = ApiJson.instance
        val element = json.parseToJsonElement(Fixtures.text(name))
        return Detail(
            json.decodeFromJsonElement(com.diziflix.app.data.model.Item.serializer(), element),
            json.decodeFromJsonElement(com.diziflix.app.data.model.DetailExtras.serializer(), element),
        )
    }

    private fun ep(
        n: Int,
        state: String = "ready",
        airDate: String? = null,
        pct: Double? = null,
        season: Int = 1,
    ) = Episode(
        id = "show:s$season:e$n",
        season = season,
        episode = n,
        title = "Bölüm $n",
        airDate = airDate,
        availability = Availability(state = state),
        progress = pct?.let { EpisodeProgress(position = it, duration = 100.0, pct = it) },
    )

    // ---------------------------------------------------------------- bölüm durumu

    @Test
    fun episodeState_futureAirDateWithoutReadySourceIsUnaired() {
        assertEquals(EpisodeState.Unaired, DetailLogic.episodeState(ep(1, "unavailable", "2026-10-15"), today))
        assertEquals(EpisodeState.Unaired, DetailLogic.episodeState(ep(1, "check_required", "2026-10-01"), today))
    }

    @Test
    fun episodeState_futureAirDateButReadySourceIsPlayable() {
        assertEquals(EpisodeState.Ready, DetailLogic.episodeState(ep(1, "ready", "2026-12-25"), today))
    }

    @Test
    fun episodeState_usesServerAvailabilityForPastEpisodes() {
        assertEquals(EpisodeState.Unavailable, DetailLogic.episodeState(ep(1, "unavailable", "2026-01-01"), today))
        assertEquals(EpisodeState.Check, DetailLogic.episodeState(ep(1, "check_required", "2026-01-01"), today))
        assertEquals(EpisodeState.Ready, DetailLogic.episodeState(ep(1, "ready", "2026-01-01"), today))
        assertEquals(EpisodeState.Ready, DetailLogic.episodeState(ep(1, "ready", null), today))
        // bugünün tarihi "gelecek" sayılmaz
        assertEquals(EpisodeState.Unavailable, DetailLogic.episodeState(ep(1, "unavailable", today), today))
    }

    @Test
    fun playable_isReadyOrCheckOnly() {
        assertTrue(DetailLogic.isPlayable(EpisodeState.Ready))
        assertTrue(DetailLogic.isPlayable(EpisodeState.Check))
        assertFalse(DetailLogic.isPlayable(EpisodeState.Unavailable))
        assertFalse(DetailLogic.isPlayable(EpisodeState.Unaired))
    }

    // ---------------------------------------------------------------- hedef bölüm

    @Test
    fun targetEpisodeIndex_firstUnwatchedPlayable() {
        val eps = listOf(ep(1, pct = 95.0), ep(2, pct = 92.0), ep(3, pct = 10.0), ep(4))
        assertEquals(2, DetailLogic.targetEpisodeIndex(eps, today))
    }

    @Test
    fun targetEpisodeIndex_skipsUnplayableEpisodes() {
        val eps = listOf(ep(1, "unavailable"), ep(2, pct = 100.0), ep(3, "ready", "2027-01-01").copy(availability = Availability("unavailable")), ep(4))
        assertEquals(3, DetailLogic.targetEpisodeIndex(eps, today))
    }

    @Test
    fun targetEpisodeIndex_allWatchedGoesToFirstPlayable() {
        val eps = listOf(ep(1, "unavailable"), ep(2, pct = 99.0), ep(3, pct = 99.0))
        assertEquals(1, DetailLogic.targetEpisodeIndex(eps, today))
    }

    @Test
    fun targetEpisodeIndex_nothingPlayableIsZero_andEmptyIsMinusOne() {
        assertEquals(0, DetailLogic.targetEpisodeIndex(listOf(ep(1, "unavailable"), ep(2, "unavailable")), today))
        assertEquals(-1, DetailLogic.targetEpisodeIndex(emptyList(), today))
    }

    @Test
    fun targetEpisodeIndex_realSeason_startsAtFirstEpisode() {
        val d = detail("detail_series.json")
        val season4 = DetailLogic.sortSeasons(d.seasons)[3]
        // s4:e1 %2 (izlenmemiş sayılır), hepsi ready
        assertEquals(0, DetailLogic.targetEpisodeIndex(season4.episodes, today))
    }

    // ---------------------------------------------------------------- sezonlar

    @Test
    fun sortSeasons_putsSpecialsLast_withoutMutatingInput() {
        val input = listOf(Season(season = 2), Season(season = 0), Season(season = 1), Season(season = 3))
        val sorted = DetailLogic.sortSeasons(input)
        assertEquals(listOf(1, 2, 3, 0), sorted.map { it.season })
        assertEquals(listOf(2, 0, 1, 3), input.map { it.season })
    }

    @Test
    fun seasonLabelAndCount() {
        assertEquals("Özel Bölümler", DetailLogic.seasonLabel(Season(season = 0, title = "Specials")))
        assertEquals("1. Sezon", DetailLogic.seasonLabel(Season(season = 1, title = "1. Sezon")))
        assertEquals("5. Sezon", DetailLogic.seasonLabel(Season(season = 5, title = "")))
        assertEquals(10, DetailLogic.episodeCount(Season(episodeCount = 10)))
        assertEquals(2, DetailLogic.episodeCount(Season(episodes = listOf(ep(1), ep(2)))))
    }

    @Test
    fun realSeries_hasFourSeasonsWithTenEpisodesEach() {
        val d = detail("detail_series.json")
        val sorted = DetailLogic.sortSeasons(d.seasons)
        assertEquals(listOf(1, 2, 3, 4), sorted.map { it.season })
        assertTrue(sorted.all { it.episodes.size == 10 && it.hasPoster && DetailLogic.episodeCount(it) == 10 })
        assertEquals("/img/tmdb_tv_103516:s1/portrait?w=300&h=450", sorted[0].posterUrl)
        val e = sorted[3].episodes[0]
        assertEquals("tmdb_tv_103516:s4:e1", e.id)
        assertEquals("Valles Marineris", e.title)
        assertEquals(59.0, e.runtime!!, 0.0)
        assertEquals("2026-07-24", e.airDate)
        assertTrue(e.hasStill)
        assertEquals("/img/tmdb_tv_103516:s4:e1/still?w=320&h=180", e.stillPath)
        assertEquals(2.0, e.progress!!.pct, 0.0)
    }

    // ---------------------------------------------------------------- bölüm bulma

    @Test
    fun findEpisode_byIdAndBySeasonEpisodeSuffix() {
        val seasons = DetailLogic.sortSeasons(detail("detail_series.json").seasons)
        assertEquals(DetailLogic.EpisodePos(3, 0), DetailLogic.findEpisode(seasons, "tmdb_tv_103516:s4:e1"))
        assertEquals(DetailLogic.EpisodePos(0, 9), DetailLogic.findEpisode(seasons, "tmdb_tv_103516:s1:e10"))
        // farklı yapım kimliği ama :sN:eM eki aynı -> sezon/bölüm numarasıyla eşleşir
        assertEquals(DetailLogic.EpisodePos(1, 2), DetailLogic.findEpisode(seasons, "baska-kimlik:s2:e3"))
        assertNull(DetailLogic.findEpisode(seasons, "baska-kimlik:s9:e1"))
        assertNull(DetailLogic.findEpisode(seasons, "duz-kimlik"))
        assertNull(DetailLogic.findEpisode(seasons, null))
        assertNull(DetailLogic.findEpisode(seasons, ""))
    }

    @Test
    fun nextEpisode_crossesSeasonBoundary_andEndsAtLast() {
        val seasons = detail("detail_series.json").seasons
        assertEquals("tmdb_tv_103516:s4:e2", DetailLogic.nextEpisode(seasons, "tmdb_tv_103516:s4:e1")?.id)
        assertEquals("tmdb_tv_103516:s2:e1", DetailLogic.nextEpisode(seasons, "tmdb_tv_103516:s1:e10")?.id)
        assertNull(DetailLogic.nextEpisode(seasons, "tmdb_tv_103516:s4:e10"))
        assertNull(DetailLogic.nextEpisode(seasons, "bilinmeyen"))
    }

    @Test
    fun episodeLabel_matchesServerEpisodeLabelFormat() {
        val seasons = detail("detail_series.json").seasons
        assertEquals("S04 B01 · Valles Marineris", DetailLogic.episodeLabel(seasons, "tmdb_tv_103516:s4:e1"))
        assertNull(DetailLogic.episodeLabel(seasons, "bilinmeyen"))
    }

    // ---------------------------------------------------------------- oynat / devam et / fragman

    @Test
    fun resumeTarget_andProgress_forSeriesWithProgress() {
        val d = detail("detail_series.json")
        assertEquals("tmdb_tv_103516:s4:e1", DetailLogic.resumeTarget(d))
        assertTrue(DetailLogic.hasProgress(d))
        assertTrue(DetailLogic.canPlayFull(d))
        assertTrue(DetailLogic.hasTrailer(d))
    }

    @Test
    fun movie_playsFromItsOwnId_withoutProgress() {
        val d = detail("detail_movie.json")
        assertEquals("tmdb_1322562", DetailLogic.resumeTarget(d))
        assertFalse(DetailLogic.hasProgress(d))
        assertTrue(DetailLogic.canPlayFull(d))
        assertTrue(DetailLogic.hasTrailer(d))
        assertTrue(d.seasons.isEmpty())
        assertEquals("", Format.minutes(d.extras.runtime)) // sunucu runtime=0 döndürüyor: "0 dk" gösterilmez
    }

    @Test
    fun trailerOnlySeries_hidesPlay_butShowsTrailer() {
        val d = detail("detail_unavailable.json")
        assertFalse(DetailLogic.canPlayFull(d))
        assertTrue(DetailLogic.hasTrailer(d))
        assertEquals("tmdb_tv_171802", DetailLogic.resumeTarget(d))
        assertTrue(d.seasons.isEmpty())
    }

    @Test
    fun similarItemsAreFilteredForBlankIds() {
        val d = detail("detail_series.json")
        assertEquals(3, d.similar.size)
        assertEquals("tmdb_tv_19885", d.similar[0].id)
    }

    // ---------------------------------------------------------------- vurgu / etiket (kaydırma yok)

    @Test
    fun episodeCode_isShortSeasonEpisodeCode() {
        val seasons = DetailLogic.sortSeasons(detail("detail_series.json").seasons)
        assertEquals("S04 B01", DetailLogic.episodeCode(seasons, "tmdb_tv_103516:s4:e1"))
        assertEquals("S01 B10", DetailLogic.episodeCode(seasons, "tmdb_tv_103516:s1:e10"))
        assertNull(DetailLogic.episodeCode(seasons, "bilinmeyen"))
    }

    @Test
    fun playLabel_seriesWithProgressShowsResumeEpisode() {
        val d = detail("detail_series.json")
        val seasons = DetailLogic.sortSeasons(d.seasons)
        assertEquals("Devam Et \u00b7 S04 B01", DetailLogic.playLabel(d, seasons))
    }

    @Test
    fun playLabel_movieOrNoProgressIsPlain() {
        val movie = detail("detail_movie.json")
        assertEquals("Oynat", DetailLogic.playLabel(movie, emptyList()))
        // dizi ama ilerleme yok -> "Oynat"
        val d = detail("detail_series.json")
        val fresh = d.copy(
            item = d.item.copy(progress = null),
            extras = d.extras.copy(resume = null),
        )
        assertEquals("Oynat", DetailLogic.playLabel(fresh, DetailLogic.sortSeasons(fresh.seasons)))
    }

    @Test
    fun highlight_cardEpisodeWinsOverResume() {
        val d = detail("detail_series.json")
        val seasons = DetailLogic.sortSeasons(d.seasons)
        assertEquals("tmdb_tv_103516:s2:e3", DetailLogic.highlightEpisodeId(seasons, d, "tmdb_tv_103516:s2:e3"))
        assertEquals(DetailLogic.EpisodePos(1, 2), DetailLogic.focusPosition(seasons, d, "tmdb_tv_103516:s2:e3"))
    }

    @Test
    fun highlight_fallsBackToResumeThenProgress_andNullWhenNothing() {
        val d = detail("detail_series.json")
        val seasons = DetailLogic.sortSeasons(d.seasons)
        // karttan bölüm yok -> sunucunun devam/ilerleme bölümü (S04 B01 = 4. sezon, 0. bölüm)
        assertEquals("tmdb_tv_103516:s4:e1", DetailLogic.highlightEpisodeId(seasons, d, null))
        assertEquals(DetailLogic.EpisodePos(3, 0), DetailLogic.focusPosition(seasons, d, null))
        assertEquals("tmdb_tv_103516:s4:e1", DetailLogic.resumeEpisodeId(seasons, d))
        // bulunamayan kart bölümü de devam bölümüne düşer
        assertEquals("tmdb_tv_103516:s4:e1", DetailLogic.highlightEpisodeId(seasons, d, "baska:s9:e9"))
        // hiçbir ipucu yok -> vurgu yok, konum yok (sayfa yine de en üstten açılır)
        val fresh = d.copy(item = d.item.copy(progress = null), extras = d.extras.copy(resume = null))
        assertNull(DetailLogic.highlightEpisodeId(seasons, fresh, null))
        assertNull(DetailLogic.focusPosition(seasons, fresh, null))
        assertNull(DetailLogic.resumeEpisodeId(seasons, fresh))
    }

    @Test
    fun highlight_movieHasNone() {
        val m = detail("detail_movie.json")
        assertNull(DetailLogic.highlightEpisodeId(emptyList(), m, null))
    }
}
