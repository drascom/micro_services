package com.diziflix.app.domain

import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.DetailAction
import com.diziflix.app.data.model.DetailExtras
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.EpisodeProgress
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Progress
import com.diziflix.app.data.model.Resume
import com.diziflix.app.data.model.Season
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android TV detay ekranı: Tizen ölçüleri, eylem düğmeleri, liste kaydırma/pencere, metinler ve odak gezinmesi. */
class TvDetailDesignTest {

    private val today = "2026-09-30"

    private fun ep(n: Int, season: Int = 1, state: String = "ready", air: String? = null, pct: Double? = null) = Episode(
        id = "show:s$season:e$n",
        season = season,
        episode = n,
        title = "Bölüm $n",
        airDate = air,
        availability = Availability(state = state),
        progress = pct?.let { EpisodeProgress(position = it, duration = 100.0, pct = it) },
    )

    private fun season(n: Int, eps: List<Episode>) = Season(season = n, episodes = eps, episodeCount = eps.size)

    private fun series(
        seasons: List<Season>,
        actions: List<DetailAction>? = null,
        resume: Resume? = null,
        progress: Progress? = null,
        availability: Availability = Availability(),
        playback: String? = null,
    ) = Detail(
        Item(id = "show", type = "series", title = "Dizi", availability = availability, progress = progress, playback = playback),
        DetailExtras(seasons = seasons, actions = actions, resume = resume),
    )

    private fun movie(
        actions: List<DetailAction>? = null,
        progress: Progress? = null,
        availability: Availability = Availability(),
        playback: String? = null,
        resume: Resume? = null,
    ) = Detail(
        Item(id = "film", type = "movie", title = "Film", availability = availability, progress = progress, playback = playback),
        DetailExtras(actions = actions, resume = resume),
    )

    // ---------------------------------------------------------------- Tizen ölçüleri

    @Test
    fun spec_matchesTizenDetailCss() {
        assertEquals(760f, TvDetailSpec.HERO_H, 0f)
        assertEquals(1000f, TvDetailSpec.BODY_W, 0f)
        assertEquals(172f, TvDetailSpec.EP_PITCH, 0f)
        assertEquals(162f, TvDetailSpec.EP_ROW, 0f)
        assertEquals(124f, TvDetailSpec.SEASON_PITCH, 0f)
        assertEquals(114f, TvDetailSpec.SEASON_ROW, 0f)
        assertEquals(440f, TvDetailSpec.SEASONS_W, 0f)
        assertEquals(40f, TvDetailSpec.SEASONS_GAP, 0f)
        assertEquals(240f, TvDetailSpec.EP_STILL_W, 0f)
        assertEquals(135f, TvDetailSpec.EP_STILL_H, 0f)
        assertEquals(96f, TvDetailSpec.PAGE_ANCHOR, 0f)
        assertEquals(150L, TvDetailSpec.SEASON_DEBOUNCE_MS)
        assertEquals(4, TvDetailSpec.OVERVIEW_LINES)
    }

    // ---------------------------------------------------------------- liste yüksekliği / kaydırma

    @Test
    fun viewHeight_clampsToStageAndAtLeastOneRow() {
        assertEquals(900f, TvDetailLogic.viewMax(), 0f)
        assertEquals(1020f, TvDetailLogic.viewMax(1200f), 0f)
        assertEquals(172f, TvDetailLogic.computeViewH(0f), 0f)
        assertEquals(344f, TvDetailLogic.computeViewH(344f), 0f)
        assertEquals(900f, TvDetailLogic.computeViewH(10 * 172f), 0f)
        // çok kısa sahne: en az 3 bölüm satırı
        assertEquals(516f, TvDetailLogic.viewMax(500f), 0f)
    }

    @Test
    fun viewNeed_isLongestSeasonOrSeasonCount() {
        val seasons = listOf(season(1, List(10) { ep(it + 1) }), season(2, List(3) { ep(it + 1, 2) }))
        assertEquals(10 * 172f, TvDetailLogic.viewNeed(seasons), 0f)
        val many = List(12) { season(it + 1, listOf(ep(1, it + 1))) }
        assertEquals(12 * 124f, TvDetailLogic.viewNeed(many), 0f)
        assertEquals(0f, TvDetailLogic.viewNeed(emptyList()), 0f)
    }

    @Test
    fun fitScroll_keepsOneRowAboveAndTwoBelow() {
        val pitch = 172f
        val viewH = 900f
        val total = 30
        // görünür alanın içindeyse kaymaz
        assertEquals(0f, TvDetailLogic.fitScroll(0f, 2, pitch, viewH, total), 0f)
        // alta inince: odaklı satırın altında en az 2 satır görünsün
        val down = TvDetailLogic.fitScroll(0f, 10, pitch, viewH, total)
        assertEquals(10 * pitch - (viewH - 2 * pitch), down, 0.001f)
        // yukarı çıkınca: üstünde 1 satır
        val up = TvDetailLogic.fitScroll(2000f, 4, pitch, viewH, total)
        assertEquals(4 * pitch - pitch, up, 0.001f)
        // sonda içerikten fazla kayma yok
        val end = TvDetailLogic.fitScroll(0f, 29, pitch, viewH, total)
        assertEquals(total * pitch - viewH, end, 0.001f)
        // liste görünür alandan kısa: hep 0
        assertEquals(0f, TvDetailLogic.fitScroll(50f, 1, pitch, viewH, 3), 0f)
    }

    @Test
    fun clampScroll_boundsToContent() {
        assertEquals(0f, TvDetailLogic.clampScroll(-10f, 172f, 900f, 30), 0f)
        assertEquals(30 * 172f - 900f, TvDetailLogic.clampScroll(99999f, 172f, 900f, 30), 0.001f)
        assertEquals(0f, TvDetailLogic.clampScroll(500f, 172f, 900f, 3), 0f)
    }

    @Test
    fun window_isVisibleRowsPlusMinusThree() {
        assertEquals(0..8, TvDetailLogic.window(0f, 900f, 172f, 100))
        assertEquals(7..18, TvDetailLogic.window(10 * 172f, 900f, 172f, 100))
        assertEquals(0..2, TvDetailLogic.window(0f, 900f, 172f, 3))
        assertTrue(TvDetailLogic.window(0f, 900f, 172f, 0).isEmpty())
    }

    @Test
    fun pageScroll_rules() {
        // liste odaklıyken tarayıcı 96 px aşağıda durur (hero 760 -> 664)
        assertEquals(664f, TvDetailLogic.pageYForBrowser(760f), 0f)
        assertEquals(0f, TvDetailLogic.pageYForBrowser(50f), 0f)
        // diğer satırlar: satır üstü ekran yüksekliğinin %28'ine (302); en üstte 0
        assertEquals(86f, TvDetailLogic.pageYForRow(388f), 0f)
        assertEquals(0f, TvDetailLogic.pageYForRow(200f), 0f)
        assertEquals(1000f - 302f, TvDetailLogic.pageYForRow(1000f), 0f)
    }

    // ---------------------------------------------------------------- eylem düğmeleri

    @Test
    fun epTag_formatsSeasonAndSpecials() {
        assertEquals("S04 B02", TvDetailLogic.epTag(4, 2))
        assertEquals("Özel B02", TvDetailLogic.epTag(0, 2))
    }

    @Test
    fun actions_fromServerActions_resumeEpisodeGetsFirstEpisodeButton() {
        val seasons = listOf(season(1, listOf(ep(1), ep(2), ep(3))))
        val detail = series(
            seasons,
            actions = listOf(DetailAction("resume_episode", "show:s1:e2", 120.0), DetailAction("play_trailer")),
            availability = Availability(hasTrailer = true),
        )
        val buttons = TvDetailLogic.actionButtons(detail, seasons, today)
        assertEquals(listOf("Devam Et · S01 B02", "İlk Bölümden Başla", "Fragmanı Oynat"), buttons.map { it.label })
        assertEquals(TvActionKind.ResumeEpisode, buttons[0].kind)
        assertEquals("show:s1:e2", buttons[0].episodeId)
        assertTrue(buttons[0].primary)
        assertEquals("show:s1:e1", buttons[1].episodeId)
        assertEquals(TvActionKind.PlayTrailer, buttons[2].kind)
    }

    @Test
    fun actions_resumeAtFirstEpisode_hasNoFirstEpisodeButton() {
        val seasons = listOf(season(1, listOf(ep(1), ep(2))))
        val detail = series(seasons, actions = listOf(DetailAction("resume_episode", "show:s1:e1")))
        val buttons = TvDetailLogic.actionButtons(detail, seasons, today)
        assertEquals(listOf("Devam Et · S01 B01"), buttons.map { it.label })
    }

    @Test
    fun actions_emptyServerActions_meansNothingToPlay() {
        val seasons = listOf(season(1, listOf(ep(1))))
        val detail = series(seasons, actions = emptyList())
        assertTrue(TvDetailLogic.actionButtons(detail, seasons, today).isEmpty())
    }

    @Test
    fun actions_movieUsesItemIdAndCheckRequiredShowsRetry() {
        val play = TvDetailLogic.actionButtons(movie(actions = listOf(DetailAction("play_movie"))), emptyList(), today)
        assertEquals("Oynat", play[0].label)
        assertEquals("film", play[0].episodeId)
        val retry = TvDetailLogic.actionButtons(
            movie(actions = listOf(DetailAction("play_movie")), availability = Availability(state = "check_required")),
            emptyList(),
            today,
        )
        assertEquals("Yeniden Dene", retry[0].label)
        val resume = TvDetailLogic.actionButtons(movie(actions = listOf(DetailAction("resume_movie", position = 30.0))), emptyList(), today)
        assertEquals("Devam Et", resume[0].label)
    }

    @Test
    fun actions_legacyMovie_derivedFromFields() {
        assertEquals(listOf("Oynat"), TvDetailLogic.actionButtons(movie(), emptyList(), today).map { it.label })
        assertEquals(
            listOf("Devam Et"),
            TvDetailLogic.actionButtons(movie(progress = Progress(position = 30.0, duration = 100.0, pct = 30.0)), emptyList(), today).map { it.label },
        )
        // tam kaynak yok: oynat yok
        assertTrue(TvDetailLogic.actionButtons(movie(availability = Availability(state = "unavailable")), emptyList(), today).isEmpty())
        assertTrue(TvDetailLogic.actionButtons(movie(playback = "trailer"), emptyList(), today).none { it.primary })
    }

    @Test
    fun actions_legacySeries_usesResumeOrFirstPlayableEpisode() {
        val seasons = listOf(season(1, listOf(ep(1, state = "unavailable"), ep(2), ep(3))))
        val fresh = TvDetailLogic.actionButtons(series(seasons), seasons, today)
        assertEquals("Oynat · S01 B02", fresh[0].label)
        assertEquals("show:s1:e2", fresh[0].episodeId)
        val resumed = TvDetailLogic.actionButtons(
            series(
                seasons,
                resume = Resume(episodeId = "show:s1:e3", position = 10.0),
                progress = Progress(episodeId = "show:s1:e3", position = 10.0, duration = 100.0, pct = 10.0),
            ),
            seasons,
            today,
        )
        assertEquals("Devam Et · S01 B03", resumed[0].label)
        assertEquals("İlk Bölümden Başla", resumed[1].label)
        assertEquals("show:s1:e2", resumed[1].episodeId)
        // bölümler henüz gelmedi (hidrasyon): oynatma düğmesi yok
        assertTrue(TvDetailLogic.actionButtons(series(emptyList()), emptyList(), today).none { it.primary })
    }

    @Test
    fun trailer_liveDeadNone() {
        assertEquals(TvTrailerState.Live, TvDetailLogic.trailerState(movie(availability = Availability(hasTrailer = true))))
        assertEquals(TvTrailerState.Dead, TvDetailLogic.trailerState(movie(availability = Availability(trailer = "dead"))))
        assertEquals(TvTrailerState.Dead, TvDetailLogic.trailerState(movie(availability = Availability(trailer = "unknown"))))
        assertEquals(TvTrailerState.None, TvDetailLogic.trailerState(movie()))
        assertEquals(TvTrailerState.Live, TvDetailLogic.trailerState(movie(actions = listOf(DetailAction("play_trailer")))))
        // ölü fragman: gri, eylemsiz, "Fragman yok"
        val dead = TvDetailLogic.actionButtons(movie(availability = Availability(trailer = "dead")), emptyList(), today).last()
        assertEquals("Fragman yok", dead.label)
        assertTrue(dead.disabled)
        assertEquals(TvActionKind.TrailerDead, dead.kind)
        assertEquals("Bu yapımın fragmanı artık izlenemiyor.", TvDetailLogic.TRAILER_DEAD_TOAST)
    }

    @Test
    fun myListLabel() {
        assertEquals("Listeme Ekle", TvDetailLogic.myListSpec(false).label)
        assertEquals("Listemden Çıkar", TvDetailLogic.myListSpec(true).label)
        assertEquals(TvActionKind.MyList, TvDetailLogic.myListSpec(false).kind)
    }

    // ---------------------------------------------------------------- metinler

    @Test
    fun heroTexts() {
        val detail = Detail(
            Item(
                id = "x", type = "series", title = "Dizi", year = 2021, genres = listOf("Dram", "Gerilim", "Suç", "Aksiyon"),
                country = "ABD", rating = 8.4, followers = 120,
            ),
            DetailExtras(runtime = 45.0, cast = listOf("A", "B", "C", "D", "E", "F"), director = "Yönetmen Adı"),
        )
        assertEquals(listOf("2021", "Dram, Gerilim, Suç", "ABD", "45 dk / bölüm", "120 takipçi"), TvDetailLogic.metaBits(detail))
        assertEquals("Puan 8.4", TvDetailLogic.scoreText(detail))
        assertEquals("Yönetmen: Yönetmen Adı    Oyuncular: A, B, C, D, E", TvDetailLogic.credits(detail))
        assertNull(TvDetailLogic.scoreText(movie()))
        assertNull(TvDetailLogic.credits(movie()))
    }

    @Test
    fun availabilityNotes() {
        assertNull(TvDetailLogic.availabilityNote(movie()))
        assertEquals("İzleme kaynağı henüz mevcut değil", TvDetailLogic.availabilityNote(movie(availability = Availability(state = "unavailable"))))
        assertEquals(
            "Tam izleme kaynağı yok · Fragman mevcut",
            TvDetailLogic.availabilityNote(movie(availability = Availability(state = "unavailable", hasTrailer = true))),
        )
        assertEquals(
            "Kaynakta sorun bildirildi · Yeniden deneyebilirsiniz",
            TvDetailLogic.availabilityNote(movie(availability = Availability(state = "check_required"))),
        )
    }

    @Test
    fun noVideoSource_saysNoSource_andHasNoPlayAction() {
        val noSource = Availability(state = "unavailable", reason = "no_video_source")
        assertEquals("Bu film için henüz izleme kaynağı yok.", TvDetailLogic.availabilityNote(movie(availability = noSource)))
        assertEquals("Bu dizi için henüz izleme kaynağı yok.", TvDetailLogic.availabilityNote(series(emptyList(), availability = noSource)))
        // fragman varsa eski "Fragman mevcut" notu kalır (yine doğru ve daha bilgilendirici)
        assertEquals(
            "Tam izleme kaynağı yok · Fragman mevcut",
            TvDetailLogic.availabilityNote(movie(availability = noSource.copy(hasTrailer = true))),
        )
        // sunucu yine de oynat eylemi yollasa Oynat/Devam Et çizilmez
        val acts = listOf(DetailAction(kind = "play_movie"))
        assertTrue(TvDetailLogic.actionButtons(movie(actions = acts, availability = noSource), emptyList(), today).isEmpty())
        // gerçek bir "kaynak sorunlu" durumu aynen kalır
        assertTrue(TvDetailLogic.actionButtons(movie(actions = acts), emptyList(), today).isNotEmpty())
        // bölümsüz dizi yer tutucusu
        assertEquals("Bu dizi için henüz izleme kaynağı yok.", DetailLogic.emptySeasonsText(series(emptyList(), availability = noSource)))
    }

    @Test
    fun episodeRowTexts() {
        val soon = ep(3, state = "unavailable", air = "2026-12-25")
        assertEquals(TvEpisodeFlag(TvEpisodeFlagKind.Soon, "Yakında · 25 Ara 2026"), TvDetailLogic.episodeFlag(soon, EpisodeState.Unaired))
        assertEquals(TvEpisodeFlag(TvEpisodeFlagKind.Off, "Kaynak yok"), TvDetailLogic.episodeFlag(ep(1), EpisodeState.Unavailable))
        assertEquals(TvEpisodeFlag(TvEpisodeFlagKind.Check, "Kaynak kontrol ediliyor"), TvDetailLogic.episodeFlag(ep(1), EpisodeState.Check))
        assertNull(TvDetailLogic.episodeFlag(ep(1), EpisodeState.Ready))

        val withMeta = ep(2, air = "2026-07-24").copy(runtime = 45.0)
        assertEquals("45 dk · 24 Tem 2026", TvDetailLogic.episodeMeta(withMeta, EpisodeState.Ready))
        // yayınlanmamışta tarih bayrakta, meta'da yok
        assertEquals("45 dk", TvDetailLogic.episodeMeta(withMeta, EpisodeState.Unaired))
        assertNull(TvDetailLogic.episodeMeta(ep(1), EpisodeState.Ready))

        assertEquals("2", TvDetailLogic.episodeNumber(ep(2), 0))
        assertEquals("5", TvDetailLogic.episodeNumber(ep(0).copy(episode = 0), 4))

        val notice = TvDetailLogic.episodeNotice(soon, EpisodeState.Unaired)
        assertEquals("Henüz yayınlanmadı" to "25 Ara 2026 tarihinde yayınlanacak.", notice)
        assertEquals("Bölüm kaynağı yok", TvDetailLogic.episodeNotice(ep(1), EpisodeState.Unavailable)?.first)
        assertNull(TvDetailLogic.episodeNotice(ep(1), EpisodeState.Ready))
        assertNull(TvDetailLogic.episodeNotice(ep(1), EpisodeState.Check))
    }

    @Test
    fun seasonPoster_onlyRealOnes() {
        assertTrue(TvDetailLogic.hasSeasonPoster(Season(hasPoster = true, posterUrl = "/img/x")))
        assertFalse(TvDetailLogic.hasSeasonPoster(Season(hasPoster = false, posterUrl = "/img/dizi")))
        assertFalse(TvDetailLogic.hasSeasonPoster(Season(hasPoster = true, posterUrl = null)))
        assertEquals("10 bölüm", TvDetailLogic.seasonCountText(Season(episodeCount = 10)))
    }

    // ---------------------------------------------------------------- gezinme

    private val layout = TvDetailLayout(
        actionCount = 3, hasOverview = true, seasonCount = 3, episodeCount = 8, similarCount = 5, shownSeason = 1, episodeIndex = 2,
    )
    private val noMemory = emptyMap<TvDetailSection, Int>()

    private fun pos(section: TvDetailSection, index: Int) = TvDetailPos(section, index)

    @Test
    fun initialFocus_isPlayUnlessEpisodeCardOpened() {
        assertEquals(pos(TvDetailSection.Actions, 0), TvDetailNavigation.initial(layout, null))
        assertEquals(pos(TvDetailSection.Episodes, 4), TvDetailNavigation.initial(layout, 4))
        // bölüm listede yoksa Oynat
        assertEquals(pos(TvDetailSection.Actions, 0), TvDetailNavigation.initial(layout, 99))
        assertEquals(pos(TvDetailSection.Actions, 0), TvDetailNavigation.initial(layout.copy(seasonCount = 0), 1))
    }

    @Test
    fun actionsRow_horizontalThenDownToOverview() {
        val start = pos(TvDetailSection.Actions, 0)
        assertEquals(pos(TvDetailSection.Actions, 1), TvDetailNavigation.move(layout, start, TvDir.Right, noMemory))
        assertNull(TvDetailNavigation.move(layout, start, TvDir.Left, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Actions, 2), TvDir.Right, noMemory))
        assertNull(TvDetailNavigation.move(layout, start, TvDir.Up, noMemory))
        assertEquals(pos(TvDetailSection.Overview, 0), TvDetailNavigation.move(layout, start, TvDir.Down, noMemory))
        // Özetten yukarı: düğme satırının hatırlanan sütunu
        assertEquals(
            pos(TvDetailSection.Actions, 2),
            TvDetailNavigation.move(layout, pos(TvDetailSection.Overview, 0), TvDir.Up, mapOf(TvDetailSection.Actions to 2)),
        )
    }

    @Test
    fun overviewDown_entersShownSeason_orSimilarWhenNoBrowser() {
        assertEquals(pos(TvDetailSection.Seasons, 1), TvDetailNavigation.move(layout, pos(TvDetailSection.Overview, 0), TvDir.Down, noMemory))
        val film = layout.copy(seasonCount = 0, episodeCount = 0)
        assertEquals(pos(TvDetailSection.Similar, 0), TvDetailNavigation.move(film, pos(TvDetailSection.Overview, 0), TvDir.Down, noMemory))
        assertEquals(
            pos(TvDetailSection.Similar, 3),
            TvDetailNavigation.move(film, pos(TvDetailSection.Overview, 0), TvDir.Down, mapOf(TvDetailSection.Similar to 3)),
        )
        // benzer de yoksa yerinde
        assertNull(TvDetailNavigation.move(film.copy(similarCount = 0), pos(TvDetailSection.Overview, 0), TvDir.Down, noMemory))
    }

    @Test
    fun actionsDownWithoutOverview_goesStraightToBrowser() {
        val noOverview = layout.copy(hasOverview = false)
        assertEquals(pos(TvDetailSection.Seasons, 1), TvDetailNavigation.move(noOverview, pos(TvDetailSection.Actions, 1), TvDir.Down, noMemory))
    }

    @Test
    fun seasons_verticalRightLeftAndEdges() {
        assertEquals(pos(TvDetailSection.Seasons, 2), TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 1), TvDir.Down, noMemory))
        assertEquals(pos(TvDetailSection.Seasons, 0), TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 1), TvDir.Up, noMemory))
        // ilk sezondan yukarı: özet (yoksa düğmeler)
        assertEquals(pos(TvDetailSection.Overview, 0), TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 0), TvDir.Up, noMemory))
        assertEquals(
            pos(TvDetailSection.Actions, 1),
            TvDetailNavigation.move(layout.copy(hasOverview = false), pos(TvDetailSection.Seasons, 0), TvDir.Up, mapOf(TvDetailSection.Actions to 1)),
        )
        // son sezondan aşağı: benzerler
        assertEquals(pos(TvDetailSection.Similar, 0), TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 2), TvDir.Down, noMemory))
        assertNull(TvDetailNavigation.move(layout.copy(similarCount = 0), pos(TvDetailSection.Seasons, 2), TvDir.Down, noMemory))
        // Sağ: hedef bölüme; Sol: yerinde
        assertEquals(pos(TvDetailSection.Episodes, 2), TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 1), TvDir.Right, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Seasons, 1), TvDir.Left, noMemory))
        // bölümsüz sezonda Sağ yerinde
        assertNull(TvDetailNavigation.move(layout.copy(episodeCount = 0, episodeIndex = -1), pos(TvDetailSection.Seasons, 1), TvDir.Right, noMemory))
    }

    @Test
    fun episodes_verticalLeftBackToShownSeason() {
        assertEquals(pos(TvDetailSection.Episodes, 3), TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 2), TvDir.Down, noMemory))
        assertEquals(pos(TvDetailSection.Episodes, 1), TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 2), TvDir.Up, noMemory))
        assertEquals(pos(TvDetailSection.Overview, 0), TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 0), TvDir.Up, noMemory))
        assertEquals(pos(TvDetailSection.Similar, 0), TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 7), TvDir.Down, noMemory))
        assertEquals(pos(TvDetailSection.Seasons, 1), TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 5), TvDir.Left, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Episodes, 5), TvDir.Right, noMemory))
    }

    @Test
    fun similar_horizontalAndUpToEpisodes() {
        assertEquals(pos(TvDetailSection.Similar, 1), TvDetailNavigation.move(layout, pos(TvDetailSection.Similar, 0), TvDir.Right, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Similar, 4), TvDir.Right, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Similar, 0), TvDir.Left, noMemory))
        assertNull(TvDetailNavigation.move(layout, pos(TvDetailSection.Similar, 0), TvDir.Down, noMemory))
        assertEquals(pos(TvDetailSection.Episodes, 2), TvDetailNavigation.move(layout, pos(TvDetailSection.Similar, 3), TvDir.Up, noMemory))
        // bölümü olmayan sezon: sezona
        assertEquals(
            pos(TvDetailSection.Seasons, 1),
            TvDetailNavigation.move(layout.copy(episodeCount = 0, episodeIndex = -1), pos(TvDetailSection.Similar, 0), TvDir.Up, noMemory),
        )
        // film: özete
        assertEquals(
            pos(TvDetailSection.Overview, 0),
            TvDetailNavigation.move(layout.copy(seasonCount = 0, episodeCount = 0), pos(TvDetailSection.Similar, 0), TvDir.Up, noMemory),
        )
    }

    @Test
    fun clamp_repairsPositionsAfterDataChange() {
        assertEquals(pos(TvDetailSection.Episodes, 7), TvDetailNavigation.clamp(layout, pos(TvDetailSection.Episodes, 20)))
        assertEquals(pos(TvDetailSection.Actions, 2), TvDetailNavigation.clamp(layout, pos(TvDetailSection.Actions, 9)))
        // özet kalktı -> düğmelere
        assertEquals(pos(TvDetailSection.Actions, 0), TvDetailNavigation.clamp(layout.copy(hasOverview = false), pos(TvDetailSection.Overview, 0)))
        // bölümler kalktı -> gösterilen sezona
        assertEquals(
            pos(TvDetailSection.Seasons, 1),
            TvDetailNavigation.clamp(layout.copy(episodeCount = 0), pos(TvDetailSection.Episodes, 3)),
        )
        // benzerler kalktı -> düğmelere
        assertEquals(pos(TvDetailSection.Actions, 0), TvDetailNavigation.clamp(layout.copy(similarCount = 0), pos(TvDetailSection.Similar, 2)))
    }
}
