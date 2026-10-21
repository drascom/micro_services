package com.diziflix.app.domain

import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Progress
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android TV tasarım mantığı: Tizen ölçekleme, satır/odak genişleme ölçüleri, gezinme ve kaydırma kararları, kart metinleri. */
class TvDesignTest {

    private val base = "http://srv:8090"

    // ---------------------------------------------------------------- tvDp ölçekleme

    @Test
    fun tvDp_1080pBoxWithDensity2_isHalfDpPerTizenPixel() {
        // 1920x1080 piksel, density 320 (2.0): 1 Tizen px = 0,5 dp = 1 gerçek piksel
        assertEquals(120f, tvDp(240f, 1920, 2f), 0.001f)
        assertEquals(320f, tvDp(640f, 1920, 2f), 0.001f)
        assertEquals(30f, tvDp(60f, 1920, 2f), 0.001f)
        assertEquals(240f, tvDp(240f, 1920, 1f), 0.001f)
    }

    @Test
    fun tvDp_staysProportionalOnOtherResolutions() {
        // 4K (3840 piksel, density 4): aynı dp; 1280x720 piksel (density 1.333): ekranın aynı oranı
        assertEquals(tvDp(240f, 1920, 2f), tvDp(240f, 3840, 4f), 0.001f)
        val ratio720 = tvDp(240f, 1280, 4f / 3f) * (4f / 3f) / 1280f
        val ratio1080 = tvDp(240f, 1920, 2f) * 2f / 1920f
        assertEquals(ratio1080, ratio720, 0.0001f)
    }

    @Test
    fun tvDp_guardsAgainstBadInput() {
        assertEquals(240f, tvDp(240f, 0, 1f), 0.001f)
        assertEquals(240f, tvDp(240f, 1920, 0f), 0.001f)
    }

    // ---------------------------------------------------------------- satır / kart ölçüleri

    @Test
    fun rowMetrics_visibleCards_matchTizen() {
        // 1920 - 60 güvenli boşluk; adım 240+12=252: 7 tam, kısmi 8. dahil 8 (row.js VISIBLE)
        assertEquals(7, TvRowMetrics.fullyVisibleCards())
        assertEquals(8, TvRowMetrics.visibleCards())
        // dar sahne (tablet yerine TV ama 1280 gibi)
        assertTrue(TvRowMetrics.fullyVisibleCards(1280f) < 7)
    }

    @Test
    fun rowMetrics_stripOffset_focusedCardSitsInSecondSlot() {
        assertEquals(0f, TvRowMetrics.stripOffset(0), 0f)
        assertEquals(0f, TvRowMetrics.stripOffset(1), 0f)
        assertEquals(252f, TvRowMetrics.stripOffset(2), 0f)
        assertEquals(252f * 4, TvRowMetrics.stripOffset(5), 0f)
        // odaklı kartın ekran x'i (güvenli + sütun*adım - kaydırma): 1. sütundan sonra hep ikinci yuva
        for (col in 1..12) {
            val x = TvSpec.SAFE + col * TvRowMetrics.STEP - TvRowMetrics.stripOffset(col)
            assertEquals(TvSpec.SAFE + TvRowMetrics.STEP, x, 0f)
        }
        // hemen soldaki kart (col-1) tam görünür: x >= güvenli boşluk
        for (col in 2..12) {
            val left = TvSpec.SAFE + (col - 1) * TvRowMetrics.STEP - TvRowMetrics.stripOffset(col)
            assertEquals(TvSpec.SAFE, left, 0f)
        }
    }

    @Test
    fun rowMetrics_imageWindow_isFocusPlusMinusPad() {
        assertEquals(0..12, TvRowMetrics.imageWindow(0, 20))
        assertEquals(6..19, TvRowMetrics.imageWindow(10, 20))
        assertEquals(0..2, TvRowMetrics.imageWindow(0, 3))
        assertTrue(TvRowMetrics.imageWindow(0, 0).isEmpty())
    }

    @Test
    fun rowMetrics_rowWindow_focusMinusOneToPlusTwo() {
        assertEquals(0..2, TvRowMetrics.rowWindow(0, 10))
        assertEquals(3..6, TvRowMetrics.rowWindow(4, 10))
        assertEquals(8..9, TvRowMetrics.rowWindow(9, 10))
        assertTrue(TvRowMetrics.rowWindow(0, 0).isEmpty())
    }

    @Test
    fun spec_rowHeightAddsUp_andSlotMatchesNavJs() {
        // nav.js TARGET=140 (sayfa), üst menü 120 -> içerikte 20
        assertEquals(20f, TvSpec.ROW_PAD_TOP, 0f)
        assertEquals(439f, TvSpec.TILE_H, 0f)
        assertEquals(TvSpec.POSTER_H + 16f + 30f + 5f + 28f, TvSpec.TILE_H, 0f)
        assertEquals(20f + 41f + 14f + (26f + 439f + 32f) + 32f, TvSpec.ROW_H, 0f)
        assertEquals(604f, TvSpec.HERO_BLOCK, 0f)
        // poster 240 + boşluk 12 = adım
        assertEquals(252f, TvRowMetrics.STEP, 0f)
    }

    // ---------------------------------------------------------------- odak genişleme

    @Test
    fun focusExpansion_widthsScalesAndCaption() {
        assertEquals(240f, TvFocusExpansion.tileWidth(TvFocusMode.None), 0f)
        assertEquals(640f, TvFocusExpansion.tileWidth(TvFocusMode.Expanded), 0f)
        assertEquals(270f, TvFocusExpansion.tileWidth(TvFocusMode.Poster), 0f)
        assertEquals(240f, TvFocusExpansion.tileWidth(TvFocusMode.ScaleOnly), 0f)
        assertEquals(1.02f, TvFocusExpansion.cardScale(TvFocusMode.Expanded), 0f)
        assertEquals(1.12f, TvFocusExpansion.cardScale(TvFocusMode.Poster), 0f)
        assertEquals(1f, TvFocusExpansion.cardScale(TvFocusMode.None), 0f)
        assertEquals(38f, TvFocusExpansion.captionTop(TvFocusMode.Poster), 0f)
        assertEquals(16f, TvFocusExpansion.captionTop(TvFocusMode.Expanded), 0f)
        assertEquals(16f, TvFocusExpansion.captionTop(TvFocusMode.None), 0f)
    }

    @Test
    fun focusExpansion_modeDependsOnLandscapeArt() {
        assertEquals(TvFocusMode.None, TvFocusExpansion.posterMode(focused = false, hasLandscape = true))
        assertEquals(TvFocusMode.Expanded, TvFocusExpansion.posterMode(focused = true, hasLandscape = true))
        assertEquals(TvFocusMode.Poster, TvFocusExpansion.posterMode(focused = true, hasLandscape = false))
    }

    @Test
    fun focusExpansion_fractionGoesFromPosterToBackdrop() {
        assertEquals(0f, TvFocusExpansion.expandFraction(240f), 0f)
        assertEquals(0.5f, TvFocusExpansion.expandFraction(440f), 0.0001f)
        assertEquals(1f, TvFocusExpansion.expandFraction(640f), 0f)
        assertEquals(0f, TvFocusExpansion.expandFraction(100f), 0f)
        assertEquals(1f, TvFocusExpansion.expandFraction(900f), 0f)
    }

    // ---------------------------------------------------------------- kart görselleri ve metinleri

    private fun movie(
        id: String = "m1",
        rating: Double? = null,
        year: Int? = null,
        hasBackdrop: Boolean = true,
        backdrop: String? = "/img/m1/backdrop?w=1280&h=720",
        portrait: String? = "/img/m1/portrait?w=200&h=300",
    ) = Item(id = id, type = "movie", title = "Film", year = year, rating = rating, hasBackdrop = hasBackdrop, backdrop = backdrop, portrait = portrait)

    private fun episode(
        stillUrl: String? = "/img/s:s1:e2/still?w=320&h=180",
        hasStill: Boolean? = true,
        hasBackdrop: Boolean = true,
    ) = Item(
        id = "s", type = "series", title = "Dizi", cardKind = "episode", episodeLabel = "S01 B02 · Ad",
        portrait = "/img/s/portrait?w=200&h=300", backdrop = "/img/s/backdrop?w=1280&h=720", hasBackdrop = hasBackdrop,
        stillUrl = stillUrl, hasStill = hasStill,
    )

    @Test
    fun subtitle_episodeLabelThenScore() {
        assertEquals("S01 B02 · Ad", TvCardLogic.subtitle(episode()))
        assertEquals("★ Puan 7.2", TvCardLogic.subtitle(movie(rating = 7.2)))
        assertEquals("★ Puan 9.0", TvCardLogic.subtitle(movie(rating = 9.0)))
        assertNull(TvCardLogic.subtitle(movie(rating = 0.0)))
        assertNull(TvCardLogic.subtitle(movie(rating = null)))
        // etiket varsa puan yerine etiket (Tizen row.js)
        assertEquals("S01 B02 · Ad", TvCardLogic.subtitle(episode().copy(rating = 8.1)))
    }

    @Test
    fun previewMeta_yearAndScore_orEpisodeLabel() {
        assertEquals("2021  •  ★ 7.2", TvCardLogic.previewMeta(movie(year = 2021, rating = 7.2)))
        assertEquals("2021", TvCardLogic.previewMeta(movie(year = 2021)))
        assertEquals("★ 7.2", TvCardLogic.previewMeta(movie(rating = 7.2)))
        assertNull(TvCardLogic.previewMeta(movie()))
        assertEquals("S01 B02 · Ad", TvCardLogic.previewMeta(episode()))
    }

    @Test
    fun progressFraction_clampsToUnit() {
        assertEquals(0f, TvCardLogic.progressFraction(movie()), 0f)
        assertEquals(0.4f, TvCardLogic.progressFraction(movie().copy(progress = Progress(pct = 40.0))), 0.0001f)
        assertEquals(1f, TvCardLogic.progressFraction(movie().copy(progress = Progress(pct = 140.0))), 0f)
    }

    @Test
    fun posterArt_usesAllowedPosterSize() {
        assertEquals("$base/img/m1/portrait?w=200&h=300", TvCardLogic.posterArtUrl(base, movie()))
        assertEquals("$base/img/m1/card?w=300&h=450", TvCardLogic.posterArtUrl(base, movie(portrait = null).copy(card = "/img/m1/card")))
        // bölüm kartı: önce dizinin posteri
        assertEquals("$base/img/s/portrait?w=200&h=300", TvCardLogic.posterArtUrl(base, episode()))
        // posteri yoksa still (454x254)
        assertEquals("$base/img/s:s1:e2/still?w=454&h=254", TvCardLogic.posterArtUrl(base, episode().copy(portrait = null)))
    }

    @Test
    fun focusArt_backdropForTitles_stillForEpisodes_noneWithoutArt() {
        // başlık: backdrop, sorgu atılır, 640x360 istenir
        assertEquals("$base/img/m1/backdrop?w=640&h=360", TvCardLogic.focusArtUrl(base, movie()))
        assertTrue(TvCardLogic.canExpand(movie()))
        // gerçek yatay afiş yok -> genişleme yok (yalnız poster büyümesi)
        assertEquals("", TvCardLogic.focusArtUrl(base, movie(hasBackdrop = false)))
        assertFalse(TvCardLogic.canExpand(movie(hasBackdrop = false)))
        // bölüm: gerçek still öncelikli (640x360)
        assertEquals("$base/img/s:s1:e2/still?w=640&h=360", TvCardLogic.focusArtUrl(base, episode()))
        // still yok (has_still=false) -> dizinin backdrop'u
        assertEquals("$base/img/s/backdrop?w=640&h=360", TvCardLogic.focusArtUrl(base, episode(hasStill = false)))
        // still de backdrop da yok -> genişleme yok
        assertEquals("", TvCardLogic.focusArtUrl(base, episode(stillUrl = null, hasBackdrop = false)))
    }

    @Test
    fun heroMeta_matchesTizenMetaLine() {
        val item = Item(id = "a", type = "series", year = 2020, rating = 8.04, genres = listOf("Dram", "Gerilim", "Suç", "Bilim"))
        assertEquals("2020   ·   Puan 8.0   ·   Dram - Gerilim - Suç   ·   Dizi", TvCardLogic.heroMeta(item))
        assertEquals("Film", TvCardLogic.heroMeta(Item(id = "b", type = "movie")))
    }

    // ---------------------------------------------------------------- gezinme

    private val rows = listOf(
        TvNavRow("hero", 1),
        TvNavRow("continue", 4),
        TvNavRow("loading", 0),       // iskelet: odaklanamaz, atlanır
        TvNavRow("trend", 9),
    )

    @Test
    fun nav_downSkipsNonFocusableRows_andUsesColumnMemory() {
        val memory = mapOf("trend" to 5)
        assertEquals(TvMove.To("continue", 0), TvHomeNavigation.move(rows, "hero", 0, TvDir.Down, memory, 3))
        assertEquals(TvMove.To("trend", 5), TvHomeNavigation.move(rows, "continue", 2, TvDir.Down, memory, 3))
        assertEquals(TvMove.To("continue", 2), TvHomeNavigation.move(rows, "trend", 5, TvDir.Up, mapOf("continue" to 2), 3))
        // hafıza satırdan uzunsa sıkıştırılır
        assertEquals(TvMove.To("continue", 3), TvHomeNavigation.move(rows, "hero", 0, TvDir.Down, mapOf("continue" to 99), 3))
    }

    @Test
    fun nav_upFromFirstRow_leavesToTopBar_downFromLastStays() {
        assertEquals(TvMove.ToTopBar, TvHomeNavigation.move(rows, "hero", 0, TvDir.Up, emptyMap(), 3))
        assertEquals(TvMove.Stay, TvHomeNavigation.move(rows, "trend", 0, TvDir.Down, emptyMap(), 3))
        // hero yoksa ilk satırdan yukarı da üst menüye
        assertEquals(TvMove.ToTopBar, TvHomeNavigation.move(rows.drop(1), "continue", 1, TvDir.Up, emptyMap(), 0))
    }

    @Test
    fun nav_leftRight_stayInRow_heroChangesSlide() {
        assertEquals(TvMove.To("trend", 4), TvHomeNavigation.move(rows, "trend", 3, TvDir.Right, emptyMap(), 3))
        assertEquals(TvMove.To("trend", 2), TvHomeNavigation.move(rows, "trend", 3, TvDir.Left, emptyMap(), 3))
        assertEquals(TvMove.Stay, TvHomeNavigation.move(rows, "trend", 0, TvDir.Left, emptyMap(), 3))
        assertEquals(TvMove.Stay, TvHomeNavigation.move(rows, "trend", 8, TvDir.Right, emptyMap(), 3))
        assertEquals(TvMove.HeroPage(1), TvHomeNavigation.move(rows, "hero", 0, TvDir.Right, emptyMap(), 3))
        assertEquals(TvMove.HeroPage(-1), TvHomeNavigation.move(rows, "hero", 0, TvDir.Left, emptyMap(), 3))
        assertEquals(TvMove.Stay, TvHomeNavigation.move(rows, "hero", 0, TvDir.Right, emptyMap(), 1))
    }

    @Test
    fun nav_heroStep_wrapsAround() {
        assertEquals(1, TvHomeNavigation.heroStep(0, 3, 1))
        assertEquals(0, TvHomeNavigation.heroStep(2, 3, 1))
        assertEquals(2, TvHomeNavigation.heroStep(0, 3, -1))
        assertEquals(0, TvHomeNavigation.heroStep(0, 1, 1))
    }

    @Test
    fun nav_initialAndRepair() {
        assertEquals("hero", TvHomeNavigation.initialRow(rows))
        assertEquals("continue", TvHomeNavigation.initialRow(rows.drop(1)))
        assertNull(TvHomeNavigation.initialRow(listOf(TvNavRow("a", 0))))
        // satır duruyor: sütun sıkışır
        assertEquals("continue" to 3, TvHomeNavigation.repair(rows, "continue", 9, 1))
        // satır kalktı: bir üstteki odaklanabilir satıra
        val without = rows.filter { it.id != "continue" }
        assertEquals("hero" to 0, TvHomeNavigation.repair(without, "continue", 2, 1))
        // en üst kalktıysa ilkine
        assertEquals("trend" to 0, TvHomeNavigation.repair(listOf(TvNavRow("trend", 9)), "hero", 0, 0))
        assertNull(TvHomeNavigation.repair(emptyList(), "x", 0, 0))
    }

    // ---------------------------------------------------------------- dikey kaydırma (hangi odakta sayfa y=0)

    private val rowIds = listOf("continue", "loading", "trend")

    @Test
    fun scroll_heroAndTopBar_keepPageAtTop() {
        assertEquals(0f, TvHomeScroll.targetY("hero", rowIds, hasHero = true), 0f)
        assertEquals(0f, TvHomeScroll.targetY(null, rowIds, hasHero = true), 0f)   // üst menü / odak yok
        assertEquals(0f, TvHomeScroll.targetY("bilinmeyen", rowIds, hasHero = true), 0f)
    }

    @Test
    fun scroll_focusedRowSnapsToFixedTopSlot() {
        // hero varken ilk satır: hero bloğu kadar yukarı
        assertEquals(604f, TvHomeScroll.targetY("continue", rowIds, hasHero = true), 0f)
        assertEquals(604f + 604f, TvHomeScroll.targetY("loading", rowIds, hasHero = true), 0f)
        assertEquals(604f + 2 * 604f, TvHomeScroll.targetY("trend", rowIds, hasHero = true), 0f)
        // hero yokken ilk satır y=0'da (başlığı zaten yuvada)
        assertEquals(0f, TvHomeScroll.targetY("continue", rowIds, hasHero = false), 0f)
        assertEquals(604f, TvHomeScroll.targetY("loading", rowIds, hasHero = false), 0f)
    }

    @Test
    fun scroll_upFromFirstRowToHeroRestoresPageFully() {
        val down = TvHomeScroll.targetY("continue", rowIds, hasHero = true)
        val back = TvHomeScroll.targetY("hero", rowIds, hasHero = true)
        assertTrue(down > 0f)
        assertEquals(0f, back, 0f)
    }
}
