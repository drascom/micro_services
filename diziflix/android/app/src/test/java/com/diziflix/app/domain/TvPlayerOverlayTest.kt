package com.diziflix.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Oynatıcı katmanları (Ses ve Altyazılar paneli, Kaynak/kalite menüsü): tuş yönlendirme tablosu, odak devri, Geri davranışı.
 * Geçmiş kusur (TV box, Android 14): tek ses + yalnız "Kapalı" altyazıyla panel açılınca hiçbir tuş çalışmıyordu — Geri'nin
 * KeyDown'u yutuluyor (BackHandler tetiklenmiyor) ve panelde odaklanabilir seçenek olmadığından Tamam/yön tuşları etkisizdi.
 */
class TvPlayerOverlayTest {
    private val UP = PlayerKeys.KEYCODE_DPAD_UP
    private val DOWN = PlayerKeys.KEYCODE_DPAD_DOWN
    private val LEFT = PlayerKeys.KEYCODE_DPAD_LEFT
    private val RIGHT = PlayerKeys.KEYCODE_DPAD_RIGHT
    private val OK = PlayerKeys.KEYCODE_DPAD_CENTER
    private val BACK = PlayerKeys.KEYCODE_BACK
    private val YELLOW = TvPlayerKeys.KEYCODE_PROG_YELLOW
    private val VOLUME_UP = 24

    // ---------------------------------------------------------------- tuş yönlendirme tablosu

    @Test
    fun route_noOverlay_neverConsumes() {
        for (code in listOf(UP, DOWN, LEFT, RIGHT, OK, BACK, YELLOW, VOLUME_UP)) {
            assertEquals(TvOverlayKey.PassThrough, TvPlayerOverlayKeys.route(TvOverlay.None, code, true))
            assertEquals(TvOverlayKey.PassThrough, TvPlayerOverlayKeys.route(TvOverlay.None, code, false))
        }
    }

    @Test
    fun route_back_closesAnyOverlay_onKeyDown_andIsNeverPassedOrIgnoredOnDown() {
        for (overlay in listOf(TvOverlay.Tracks, TvOverlay.Sources)) {
            assertEquals(TvOverlayKey.Close, TvPlayerOverlayKeys.route(overlay, BACK, isDown = true))
            assertEquals(TvOverlayKey.Close, TvPlayerOverlayKeys.route(overlay, TvPlayerOverlayKeys.KEYCODE_ESCAPE, isDown = true))
            // bırakma: tüketilir (Activity'ye yalnız bir "up" gitmesin), tekrar eden basış ikinci kez kapatmaz
            assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(overlay, BACK, isDown = false))
            assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(overlay, BACK, isDown = true, repeatCount = 1))
        }
    }

    @Test
    fun route_yellowClosesTracksPanelOnly() {
        assertEquals(TvOverlayKey.Close, TvPlayerOverlayKeys.route(TvOverlay.Tracks, YELLOW, true))
        assertEquals(TvOverlayKey.PassThrough, TvPlayerOverlayKeys.route(TvOverlay.Sources, YELLOW, true))
    }

    @Test
    fun route_directionsAndSelect() {
        val o = TvOverlay.Tracks
        assertEquals(TvOverlayKey.Up, TvPlayerOverlayKeys.route(o, UP, true))
        assertEquals(TvOverlayKey.Down, TvPlayerOverlayKeys.route(o, DOWN, true))
        assertEquals(TvOverlayKey.Left, TvPlayerOverlayKeys.route(o, LEFT, true))
        assertEquals(TvOverlayKey.Right, TvPlayerOverlayKeys.route(o, RIGHT, true))
        for (code in listOf(OK, PlayerKeys.KEYCODE_ENTER, PlayerKeys.KEYCODE_NUMPAD_ENTER, PlayerKeys.KEYCODE_MEDIA_PLAY_PAUSE, PlayerKeys.KEYCODE_MEDIA_PLAY)) {
            assertEquals(TvOverlayKey.Select, TvPlayerOverlayKeys.route(o, code, true))
            // uzun basışın tekrarları seçimi yeniden uygulamaz; bırakma yutulur
            assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(o, code, true, repeatCount = 2))
            assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(o, code, false))
        }
        // yön tuşunun bırakılması yutulur (panelin açıldığı tuşun "up"ı kontrolleri tetiklemesin)
        assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(o, DOWN, false))
    }

    @Test
    fun route_transportKeysSwallowed_otherKeysPassThrough() {
        // arkadaki videoyu sarma/duraklatma katman açıkken çalışmaz
        assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(TvOverlay.Tracks, PlayerKeys.KEYCODE_MEDIA_FAST_FORWARD, true))
        assertEquals(TvOverlayKey.Swallow, TvPlayerOverlayKeys.route(TvOverlay.Sources, PlayerKeys.KEYCODE_MEDIA_PAUSE, true))
        // ses tuşu vb. sisteme geçer (eskiden her şey yutuluyordu)
        assertEquals(TvOverlayKey.PassThrough, TvPlayerOverlayKeys.route(TvOverlay.Tracks, VOLUME_UP, true))
    }

    // ---------------------------------------------------------------- panel: odak hedefi yoksa "Kapat"

    private fun ctx(audioActive: Boolean, subActive: Boolean, audio: Int = 1, subs: Int = 1, streams: Int = 3) =
        TvOverlayContext(audioCount = audio, subCount = subs, audioActive = audioActive, subActive = subActive, streamCount = streams)

    @Test
    fun panelFocus_hasOptionAndNormalize() {
        assertFalse(TvTracksPanelFocus.hasOption(audioActive = false, subActive = false))
        assertTrue(TvTracksPanelFocus.hasOption(audioActive = true, subActive = false))
        // odaktaki sütun pasifleşirse etkin olana geçer; hiçbiri yoksa aynı kalır
        assertEquals(1, TvTracksPanelFocus.normalize(TvTracksPanelState(0, 0, 0), audioActive = false, subActive = true).col)
        assertEquals(0, TvTracksPanelFocus.normalize(TvTracksPanelState(1, 0, 0), audioActive = true, subActive = false).col)
        assertEquals(1, TvTracksPanelFocus.normalize(TvTracksPanelState(1, 0, 0), audioActive = false, subActive = false).col)
    }

    @Test
    fun panelFocus_activate_closesWhenNothingToSelect_elseSelects() {
        val s = TvTracksPanelState(1, 0, 2)
        assertEquals(TvTracksPanelKey.Close, TvTracksPanelFocus.activate(s, audioActive = false, subActive = false))
        assertEquals(TvTracksPanelKey.Select(1, 2), TvTracksPanelFocus.activate(s, audioActive = false, subActive = true))
        // odaktaki sütun pasif ama diğeri etkin: etkin sütundaki seçenek uygulanır
        assertEquals(TvTracksPanelKey.Select(0, 0), TvTracksPanelFocus.activate(s, audioActive = true, subActive = false))
    }

    /** Çalışma kanıtı: tek ses + yalnız "Kapalı" altyazı (hardsub) -> hiçbir sütun etkin değil. */
    private val emptyPanel = TvOverlayModel(panel = TvTracksPanelState(1, 0, 0))

    @Test
    fun emptyPanel_back_closesAndIsConsumed() {
        val r = TvPlayerOverlayController.onKey(emptyPanel, ctx(false, false), BACK, isDown = true)
        assertTrue(r.consumed)
        assertTrue(r.closed)
        assertEquals(TvOverlay.None, r.model.overlay)
    }

    @Test
    fun emptyPanel_ok_closes_directionsAreConsumedNoOps() {
        val ok = TvPlayerOverlayController.onKey(emptyPanel, ctx(false, false), OK, isDown = true)
        assertTrue(ok.closed)
        assertEquals(TvOverlayEffect.None, ok.effect)
        for (code in listOf(UP, DOWN, LEFT, RIGHT)) {
            val r = TvPlayerOverlayController.onKey(emptyPanel, ctx(false, false), code, isDown = true)
            assertTrue(r.consumed)
            assertFalse(r.closed)
            assertEquals(emptyPanel, r.model)
        }
        // Sarı tuş da kapatır
        assertTrue(TvPlayerOverlayController.onKey(emptyPanel, ctx(false, false), YELLOW, isDown = true).closed)
    }

    @Test
    fun panel_walkthrough_columnsMoveSelectAndBack() {
        val c = ctx(audioActive = true, subActive = true, audio = 3, subs = 3)
        var m = TvOverlayModel(panel = TvTracksPanelState(1, 0, 0))
        // Aşağı: altyazı sütununda bir öğe
        m = TvPlayerOverlayController.onKey(m, c, DOWN, true).model
        assertEquals(1, m.panel!!.subIndex)
        // Sol: SES sütunu, Aşağı: ses öğesi
        m = TvPlayerOverlayController.onKey(m, c, LEFT, true).model
        assertEquals(0, m.panel!!.col)
        m = TvPlayerOverlayController.onKey(m, c, DOWN, true).model
        assertEquals(1, m.panel!!.audioIndex)
        // Tamam: ses seçilir, panel AÇIK kalır
        val pick = TvPlayerOverlayController.onKey(m, c, OK, true)
        assertEquals(TvOverlayEffect.SelectAudio(1), pick.effect)
        assertEquals(TvOverlay.Tracks, pick.model.overlay)
        // Sağ + Tamam: altyazı (dizin 1 -> ExoPlayer iz 0)
        var m2 = TvPlayerOverlayController.onKey(pick.model, c, RIGHT, true).model
        val sub = TvPlayerOverlayController.onKey(m2, c, OK, true)
        assertEquals(TvOverlayEffect.SelectSubtitle(1), sub.effect)
        // Geri: kapanır
        val back = TvPlayerOverlayController.onKey(sub.model, c, BACK, true)
        assertTrue(back.closed)
        assertEquals(TvOverlay.None, back.model.overlay)
    }

    @Test
    fun panel_columnBecomingInactiveWhileOpen_stillWorks() {
        // panel açıkken altyazı sütunu pasifleşti (iz listesi değişti): Tamam SES'teki seçeneği uygular, kilitlenmez
        val m = TvOverlayModel(panel = TvTracksPanelState(1, 1, 0))
        val r = TvPlayerOverlayController.onKey(m, ctx(audioActive = true, subActive = false, audio = 2, subs = 1), OK, true)
        assertEquals(TvOverlayEffect.SelectAudio(1), r.effect)
    }

    // ---------------------------------------------------------------- kaynak/kalite menüsü

    @Test
    fun sourceMenu_wrapsSelectsAndBackCloses() {
        val c = ctx(false, false, streams = 3)
        var m = TvOverlayModel(menuHover = 2)
        m = TvPlayerOverlayController.onKey(m, c, DOWN, true).model
        assertEquals(0, m.menuHover)               // sarmal
        m = TvPlayerOverlayController.onKey(m, c, UP, true).model
        assertEquals(2, m.menuHover)
        // Sol/Sağ: tüketilir, değişmez
        val side = TvPlayerOverlayController.onKey(m, c, LEFT, true)
        assertTrue(side.consumed)
        assertEquals(m, side.model)
        // Tamam: kaynak seçilir ve menü kapanır
        val pick = TvPlayerOverlayController.onKey(m, c, OK, true)
        assertEquals(TvOverlayEffect.SelectStream(2), pick.effect)
        assertTrue(pick.closed)
        // Geri: seçmeden kapanır
        val back = TvPlayerOverlayController.onKey(m, c, BACK, true)
        assertTrue(back.closed)
        assertEquals(TvOverlayEffect.None, back.effect)
    }

    @Test
    fun noOverlay_controllerPassesEverythingThrough() {
        val r = TvPlayerOverlayController.onKey(TvOverlayModel(), ctx(true, true), BACK, true)
        assertFalse(r.consumed)
        assertFalse(r.closed)
    }

    // ---------------------------------------------------------------- Geri davranışı (sıra)

    @Test
    fun back_order_overlayThenUpcomingThenControlsThenLeave() {
        assertEquals(TvBackTarget.ClosePanel, TvPlayerBack.target(TvOverlay.Tracks, hasUpcoming = true, controlsVisible = true))
        assertEquals(TvBackTarget.CloseMenu, TvPlayerBack.target(TvOverlay.Sources, hasUpcoming = true, controlsVisible = true))
        assertEquals(TvBackTarget.DismissUpcoming, TvPlayerBack.target(TvOverlay.None, hasUpcoming = true, controlsVisible = true))
        assertEquals(TvBackTarget.HideControls, TvPlayerBack.target(TvOverlay.None, hasUpcoming = false, controlsVisible = true))
        assertEquals(TvBackTarget.Leave, TvPlayerBack.target(TvOverlay.None, hasUpcoming = false, controlsVisible = false))
    }

    @Test
    fun back_handlesBackExceptWhenLeaving() {
        assertTrue(TvPlayerBack.handlesBack(TvOverlay.Tracks, false, false))
        assertTrue(TvPlayerBack.handlesBack(TvOverlay.None, true, false))
        assertTrue(TvPlayerBack.handlesBack(TvOverlay.None, false, true))
        assertFalse(TvPlayerBack.handlesBack(TvOverlay.None, false, false))
    }

    @Test
    fun scenario_seekPausePanelBack_returnsToPlayerKeys() {
        // Sağ (sar) -> Tamam (duraklat) -> Aşağı (panel) -> Geri: katman yok, kök tuşlar (oynat/sar) yeniden çalışır
        assertEquals(TvPlayerAction.SeekForward, TvPlayerKeys.actionFor(RIGHT))
        assertEquals(TvPlayerAction.TogglePlay, TvPlayerKeys.actionFor(OK))
        assertEquals(TvPlayerAction.OpenTracks, TvPlayerKeys.actionFor(DOWN))
        var m = TvOverlayModel(panel = TvTracksPanel.open(listOf(TvPanelItem("Ses 1", true)), true, listOf(TvPanelItem("Kapalı", true)), true))
        assertEquals(TvOverlay.Tracks, m.overlay)
        m = TvPlayerOverlayController.onKey(m, ctx(false, false), BACK, true).model
        assertEquals(TvOverlay.None, m.overlay)
        assertNull(m.panel)
        assertNotEquals(TvOverlayKey.Close, TvPlayerOverlayKeys.route(m.overlay, BACK, true))
        assertEquals(TvOverlayKey.PassThrough, TvPlayerOverlayKeys.route(m.overlay, OK, true))
    }
}
