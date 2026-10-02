package com.diziflix.app.domain

import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Item
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android TV Aşama B: katalog/arama, ayarlar, profiller, oynatıcı (Tizen ölçüleri, metinler, odak kararları). */
class TvScreensDesignTest {

    // ---------------------------------------------------------------- katalog ızgarası

    @Test
    fun catalogSpec_matchesTizenCss() {
        assertEquals(5, TvCatalogSpec.COLUMNS)
        assertEquals(300f, TvCatalogSpec.TILE_W, 0f)
        assertEquals(450f, TvCatalogSpec.CARD_H, 0f)
        assertEquals(60f, TvCatalogSpec.GRID_GAP, 0f)
        assertEquals(60f, TvCatalogSpec.PAGE_PAD_H, 0f)
        assertEquals(1.04f, TvCatalogSpec.FOCUS_SCALE, 0f)
        // .catalog-page padding-top 160 - üst menü 120
        assertEquals(40f, TvCatalogSpec.CONTENT_TOP_PAD, 0f)
        // poster sütunlarına sığar: 5*300 + 4*60 + sol 60 = 1800 <= 1920
        assertTrue(TvCatalogSpec.PAGE_PAD_H + 5 * TvCatalogSpec.TILE_W + 4 * TvCatalogSpec.GRID_GAP <= 1920f)
    }

    @Test
    fun catalogTexts() {
        assertEquals("120 yapım", TvCatalogLogic.subtitle(120, null))
        assertEquals("3 yapım · “lantern”", TvCatalogLogic.subtitle(3, "lantern"))
        assertEquals(
            "3 yapım · “lan” · Kaynakta aranıyor…",
            TvCatalogLogic.subtitle(3, "lan", searching = true),
        )
        assertEquals(
            "2 yapım · “lan” · Canlı kaynak şu an yanıt vermedi",
            TvCatalogLogic.subtitle(2, "lan", note = "Canlı kaynak şu an yanıt vermedi"),
        )
        assertEquals("2021 · Dizi", TvCatalogLogic.tileMeta(Item(type = "series", year = 2021)))
        assertEquals("Yıl bilinmiyor · Film", TvCatalogLogic.tileMeta(Item(type = "movie", year = null)))
    }

    @Test
    fun catalogRows_andPinOffset() {
        assertEquals(0, TvCatalogLogic.rowCount(0))
        assertEquals(1, TvCatalogLogic.rowCount(5))
        assertEquals(2, TvCatalogLogic.rowCount(6))
        assertEquals(4, TvCatalogLogic.rowCount(20))
        // satırın üst boşluğundan sonraki kenarı y=20'ye (ekranda 140) oturur
        assertEquals(8f, TvCatalogLogic.pinOffset(28f), 0f)
        assertEquals(4f, TvCatalogLogic.pinOffset(24f), 0f)
    }

    @Test
    fun catalogLoadMore_whenFocusNearEnd() {
        assertTrue(TvCatalogLogic.shouldLoadMore(focusRow = 2, rows = 4, loaded = 20, total = 100))
        assertTrue(TvCatalogLogic.shouldLoadMore(focusRow = 3, rows = 4, loaded = 20, total = 100))
        assertFalse(TvCatalogLogic.shouldLoadMore(focusRow = 1, rows = 4, loaded = 20, total = 100))
        assertFalse(TvCatalogLogic.shouldLoadMore(focusRow = 3, rows = 4, loaded = 100, total = 100))
        assertFalse(TvCatalogLogic.shouldLoadMore(focusRow = 0, rows = 0, loaded = 0, total = 100))
    }

    private fun move(pos: TvGridPos, dir: TvDir, filters: Int = 5, items: Int = 12, lastGrid: Int = 0, filtersCol: Int = 0) =
        TvCatalogLogic.move(pos, dir, filters, items, lastGrid, filtersCol)

    @Test
    fun grid_horizontalStaysInRow() {
        assertEquals(TvGridMove.To(TvGridPos.Grid(1)), move(TvGridPos.Grid(0), TvDir.Right))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Grid(0), TvDir.Left))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Grid(4), TvDir.Right)) // satır sonu, sonraki satıra geçmez
        assertEquals(TvGridMove.Stay, move(TvGridPos.Grid(5), TvDir.Left))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Grid(11), TvDir.Right)) // son kart
    }

    @Test
    fun grid_verticalKeepsColumnAndClampsToShortLastRow() {
        assertEquals(TvGridMove.To(TvGridPos.Grid(8)), move(TvGridPos.Grid(3), TvDir.Down))
        assertEquals(TvGridMove.To(TvGridPos.Grid(3)), move(TvGridPos.Grid(8), TvDir.Up))
        // son satır eksik (12 kart: 5+5+2): 4. sütundan aşağı -> son kart
        assertEquals(TvGridMove.To(TvGridPos.Grid(11)), move(TvGridPos.Grid(9), TvDir.Down))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Grid(10), TvDir.Down))
    }

    @Test
    fun grid_upFromFirstRowGoesToFiltersThenTopBar() {
        assertEquals(TvGridMove.To(TvGridPos.Filters(2)), move(TvGridPos.Grid(1), TvDir.Up, filtersCol = 2))
        assertEquals(TvGridMove.ToTopBar, move(TvGridPos.Grid(1), TvDir.Up, filters = 0)) // arama: süzgeç yok
        assertEquals(TvGridMove.ToTopBar, move(TvGridPos.Filters(0), TvDir.Up))
    }

    @Test
    fun filters_horizontalAndDownReturnsToLastCard() {
        assertEquals(TvGridMove.To(TvGridPos.Filters(1)), move(TvGridPos.Filters(0), TvDir.Right))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Filters(4), TvDir.Right))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Filters(0), TvDir.Left))
        assertEquals(TvGridMove.To(TvGridPos.Grid(7)), move(TvGridPos.Filters(2), TvDir.Down, lastGrid = 7))
        assertEquals(TvGridMove.To(TvGridPos.Grid(11)), move(TvGridPos.Filters(2), TvDir.Down, lastGrid = 50))
        assertEquals(TvGridMove.Stay, move(TvGridPos.Filters(2), TvDir.Down, items = 0))
    }

    // ---------------------------------------------------------------- ayarlar

    @Test
    fun settingsTexts() {
        assertEquals("Varsayılan altyazı: Türkçe", TvSettingsLogic.subStatus("tr"))
        assertEquals("Varsayılan altyazı: İngilizce", TvSettingsLogic.subStatus("en"))
        assertEquals("Varsayılan altyazı: Kapalı", TvSettingsLogic.subStatus("off"))
        assertEquals("En yüksek kalite: 1080p", TvSettingsLogic.qualityStatus("1080"))
        assertEquals("En yüksek kalite: 1440p", TvSettingsLogic.qualityStatus("1440"))
        assertEquals("En yüksek kalite: Otomatik (sınırsız)", TvSettingsLogic.qualityStatus("auto"))
        assertEquals("Sunucu: library · 121 içerik", TvSettingsLogic.serverInfo("library", 121))
        assertEquals("Sunucu: ? · ? içerik", TvSettingsLogic.serverInfo(null, null))
        assertEquals("Sürüm: 1.0.0   ·   Motor: Media3 ExoPlayer", TvSettingsLogic.buildInfo("1.0.0"))
        assertEquals(
            "Sunucu adresi: http://a   ·   Varsayılan: http://b",
            TvSettingsLogic.addressInfo("http://a", "http://b"),
        )
    }

    @Test
    fun settingsSpec_matchesTizenCss() {
        assertEquals(80f, TvSettingsSpec.PAD_H, 0f)
        assertEquals(68f, TvSettingsSpec.OPT_H, 0f)
        assertEquals(72f, TvSettingsSpec.FIELD_H, 0f)
        assertEquals(60f, TvSettingsSpec.BTN_H, 0f)
        assertEquals(52f, TvSettingsSpec.BTN_TOP_H, 0f)
        assertEquals(20f, TvSettingsSpec.CARD_RADIUS, 0f)
    }

    // ---------------------------------------------------------------- profiller

    @Test
    fun profiles_initialsAndSpec() {
        assertEquals("A", TvProfilesLogic.initials("ali"))
        assertEquals("İ", TvProfilesLogic.initials("irem")) // Türkçe büyük harf
        assertEquals("?", TvProfilesLogic.initials("  "))
        assertEquals("?", TvProfilesLogic.initials(null))
        assertEquals(200f, TvProfilesSpec.AVATAR, 0f)
        assertEquals(240f, TvProfilesSpec.PROFILE_W, 0f)
        assertEquals("ÇOCUK", TvProfilesLogic.KIDS_BADGE)
    }

    // ---------------------------------------------------------------- oynatıcı

    @Test
    fun player_timeTexts() {
        assertEquals(0.5f, TvPlayerLogic.fraction(30_000L, 60_000L), 0.0001f)
        assertEquals(0f, TvPlayerLogic.fraction(30_000L, 0L), 0f)
        assertEquals(1f, TvPlayerLogic.fraction(90_000L, 60_000L), 0f)
        assertEquals("-0:30", TvPlayerLogic.remainingText(30_000L, 60_000L))
        assertEquals("-1:01:00", TvPlayerLogic.remainingText(0L, 3_660_000L))
        assertEquals("", TvPlayerLogic.remainingText(10_000L, 0L))
    }

    @Test
    fun player_seekHintUsesArrows() {
        assertEquals(">> 10 sn", TvPlayerLogic.seekHint(10_000L))
        assertEquals("<< 30 sn", TvPlayerLogic.seekHint(-30_000L))
        assertEquals(">> 1 dk", TvPlayerLogic.seekHint(60_000L))
        assertEquals("<< 2 dk", TvPlayerLogic.seekHint(-120_000L))
    }

    @Test
    fun player_buttonLabels() {
        assertEquals("Ses ve Altyazılar · Türkçe", TvPlayerLogic.tracksButtonLabel("Türkçe"))
        assertEquals("Kaynak / kalite: VidMolly · 1080p", TvPlayerLogic.sourceButtonLabel("VidMolly · 1080p"))
        assertEquals("Kaynak / kalite: -", TvPlayerLogic.sourceButtonLabel(""))
        assertEquals("Şimdi oynat", TvPlayerLogic.nextNowLabel(null))
        assertEquals("Şimdi oynat (5)", TvPlayerLogic.nextNowLabel(5))
        val ep = Episode(id = "x:s4:e2", season = 4, episode = 2, title = "Ad")
        assertEquals("S04 B02 · Ad", TvPlayerLogic.nextLabel(ep))
        assertEquals("Özel B01", TvPlayerLogic.nextLabel(Episode(season = 0, episode = 1)))
    }

    @Test
    fun player_upcomingWindow_last45Seconds() {
        val dur = 2_700_000L // 45 dk
        assertEquals(false, TvPlayerLogic.upcomingDecision(dur, 600_000L))
        assertEquals(true, TvPlayerLogic.upcomingDecision(dur, dur - 45_000L))
        assertEquals(true, TvPlayerLogic.upcomingDecision(dur, dur - 1_000L))
        // pencere dışına 5 sn payla çıkınca kapanır
        assertEquals(null, TvPlayerLogic.upcomingDecision(dur, dur - 48_000L))
        assertEquals(false, TvPlayerLogic.upcomingDecision(dur, dur - 51_000L))
        // kısa bölümde (10 dk altı) erken teklif yok
        assertNull(TvPlayerLogic.upcomingDecision(300_000L, 280_000L))
        // bitti (kalan 0): teklif kalkmaz, bitiş akışı devralır
        assertNull(TvPlayerLogic.upcomingDecision(dur, dur))
    }

    @Test
    fun player_keyMapping_upSourcesDownTracksYellowTracks() {
        assertEquals(TvPlayerAction.OpenSources, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_UP))
        assertEquals(TvPlayerAction.OpenTracks, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_DOWN))
        assertEquals(TvPlayerAction.OpenTracks, TvPlayerKeys.actionFor(TvPlayerKeys.KEYCODE_PROG_YELLOW))
        assertEquals(TvPlayerAction.TogglePlay, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_CENTER))
        assertEquals(TvPlayerAction.TogglePlay, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_ENTER))
        assertEquals(TvPlayerAction.TogglePlay, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PLAY_PAUSE))
        assertEquals(TvPlayerAction.Play, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PLAY))
        assertEquals(TvPlayerAction.Pause, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PAUSE))
        assertEquals(TvPlayerAction.SeekBack, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_LEFT))
        assertEquals(TvPlayerAction.SeekForward, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_FAST_FORWARD))
        assertEquals(TvPlayerAction.Ignore, TvPlayerKeys.actionFor(PlayerKeys.KEYCODE_BACK))
        assertEquals(TvPlayerAction.ShowControls, TvPlayerKeys.actionFor(999))
    }

    @Test
    fun player_sourceMenuWraps() {
        assertEquals(1, TvSourceMenu.move(0, 1, 3))
        assertEquals(0, TvSourceMenu.move(2, 1, 3))
        assertEquals(2, TvSourceMenu.move(0, -1, 3))
        assertEquals(0, TvSourceMenu.move(0, 1, 0))
    }

    private fun items(vararg current: Boolean) = current.mapIndexed { i, c -> TvPanelItem("öğe$i", c) }

    @Test
    fun tracksPanel_opensOnSubtitleColumnAtCurrent() {
        val audio = items(false, true)
        val subs = items(false, false, true)
        val s = TvTracksPanel.open(audio, audioDim = false, subs = subs, subsDim = false)
        assertEquals(TvTracksPanelState(col = 1, audioIndex = 1, subIndex = 2), s)
        // altyazı sütunu pasifse (hiç iz yok) SES'e
        val only = TvTracksPanel.open(audio, audioDim = false, subs = items(true), subsDim = true)
        assertEquals(0, only.col)
        // ikisi de pasif: ALTYAZI
        assertEquals(1, TvTracksPanel.open(items(true), true, items(true), true).col)
    }

    @Test
    fun tracksPanel_moveColumnsAndSelect() {
        val start = TvTracksPanelState(col = 1, audioIndex = 0, subIndex = 0)
        val down = TvTracksPanel.move(start, 1, audioCount = 3, subCount = 3, audioActive = true, subActive = true)
        assertEquals(1, down.subIndex)
        // sınırlı (sarmaz)
        assertEquals(2, TvTracksPanel.move(down.copy(subIndex = 2), 1, 3, 3, true, true).subIndex)
        assertEquals(0, TvTracksPanel.move(start, -1, 3, 3, true, true).subIndex)
        // Sol -> SES (aktifse); pasifse yerinde
        assertEquals(0, TvTracksPanel.toColumn(start, 0, audioActive = true, subActive = true).col)
        assertEquals(1, TvTracksPanel.toColumn(start, 0, audioActive = false, subActive = true).col)
        // Tamam: odaktaki seçeneği uygula
        assertEquals(TvTracksPanelKey.Select(1, 1), TvTracksPanel.select(down, audioActive = true, subActive = true))
        assertNull(TvTracksPanel.select(down, audioActive = true, subActive = false))
        // pasif sütunda hareket yok
        assertEquals(start, TvTracksPanel.move(start, 1, 3, 3, audioActive = true, subActive = false))
    }

    @Test
    fun tracksPanel_clampAfterListShrinks() {
        val s = TvTracksPanel.clamp(TvTracksPanelState(1, 5, 7), audioCount = 2, subCount = 3)
        assertEquals(TvTracksPanelState(1, 1, 2), s)
    }

    @Test
    fun playerSpec_matchesTizenCss() {
        assertEquals(60f, TvPlayerSpec.SAFE, 0f)
        assertEquals(44f, TvPlayerSpec.TOP_TITLE, 0f)
        assertEquals(8f, TvPlayerSpec.BAR_H, 0f)
        assertEquals(24f, TvPlayerSpec.KNOB, 0f)
        assertEquals(3_000L, TvPlayerSpec.HIDE_MS)
        assertEquals(45, TvPlayerSpec.NEXT_WINDOW_S)
        assertEquals(600, TvPlayerSpec.NEXT_MIN_DURATION_S)
        assertEquals(1200f, TvPlayerSpec.TP_W, 0f)
        assertEquals(560f, TvPlayerSpec.NEXT_W, 0f)
        assertEquals(96f, TvPlayerSpec.LOAD_SPINNER, 0f)
        assertEquals(40f, TvPlayerSpec.LOAD_PROVERB, 0f)
    }

    @Test
    fun availabilityOptions_matchTizenCatalog() {
        assertEquals(
            listOf("Tümü", "İzlenebilir", "Kontrol gereken", "İzleme kaynağı yok"),
            CatalogLogic.AVAILABILITY_OPTIONS.map { it.second },
        )
        assertEquals(listOf("new", "year", "title"), CatalogLogic.SORT_OPTIONS.map { it.first })
        assertEquals(
            "Yalnızca fragman",
            CatalogLogic.statusNote(Item(availability = Availability(state = "unavailable", hasTrailer = true)), isSearch = false),
        )
    }
}
