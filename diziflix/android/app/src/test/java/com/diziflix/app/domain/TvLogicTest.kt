package com.diziflix.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TvLogicTest {

    // ---------------------------------------------------------------- TV algılama

    @Test
    fun tv_uiModeTelevision_isTv() {
        assertTrue(TvDetector.isTv(uiModeType = 4, hasLeanback = false, hasTouchscreen = true))
    }

    @Test
    fun tv_leanbackFeature_isTv() {
        assertTrue(TvDetector.isTv(uiModeType = 1, hasLeanback = true, hasTouchscreen = true))
    }

    @Test
    fun tv_noTouchscreen_isTv() {
        // dokunmatiksiz kutu: kumandayla kullanılır
        assertTrue(TvDetector.isTv(uiModeType = 1, hasLeanback = false, hasTouchscreen = false))
    }

    @Test
    fun tv_touchTabletAndPhone_areNotTv() {
        // normal kip (1), leanback yok, dokunmatik var -> tablet/telefon yolu DEĞİŞMEZ
        assertFalse(TvDetector.isTv(uiModeType = 1, hasLeanback = false, hasTouchscreen = true))
        // masaüstü/araç/saat kipleri de TV değildir
        assertFalse(TvDetector.isTv(uiModeType = 2, hasLeanback = false, hasTouchscreen = true))
        assertFalse(TvDetector.isTv(uiModeType = 3, hasLeanback = false, hasTouchscreen = true))
        assertFalse(TvDetector.isTv(uiModeType = 6, hasLeanback = false, hasTouchscreen = true))
    }

    @Test
    fun tvLayout_fontScaleOnlyBoostedOnTv() {
        assertEquals(1.0f, TvLayout.fontScale(1.0f, isTv = false), 0.0001f)
        assertEquals(1.0f * TvLayout.FONT_SCALE_BOOST, TvLayout.fontScale(1.0f, isTv = true), 0.0001f)
        assertEquals(1.3f, TvLayout.fontScale(1.3f, isTv = false), 0.0001f)
        assertTrue(TvLayout.FONT_SCALE_BOOST in 1.0f..1.3f)   // "küçük artış"
        assertEquals(48f, TvLayout.OVERSCAN_H_DP, 0f)
        assertEquals(27f, TvLayout.OVERSCAN_V_DP, 0f)
    }

    // ---------------------------------------------------------------- uzun basış

    @Test
    fun longPress_shortPress_isClick() {
        val t = LongPressTracker()
        assertEquals(LongPressTracker.Result.None, t.onDown(1_000))
        assertEquals(LongPressTracker.Result.Click, t.onUp(1_150))
    }

    @Test
    fun longPress_heldViaRepeats_firesOnceWhileHeldAndNoClickOnRelease() {
        val t = LongPressTracker()
        assertEquals(LongPressTracker.Result.None, t.onDown(0))
        assertEquals(LongPressTracker.Result.None, t.onDown(510))     // sistem tekrarı, eşik altı
        assertEquals(LongPressTracker.Result.None, t.onDown(560))
        assertEquals(LongPressTracker.Result.LongPress, t.onDown(610)) // >= 600 ms
        assertEquals(LongPressTracker.Result.None, t.onDown(660))     // yeniden tetiklenmez
        assertEquals(LongPressTracker.Result.None, t.onUp(900))       // bırakma: tıklama YOK
    }

    @Test
    fun longPress_noRepeats_firesOnRelease() {
        val t = LongPressTracker()
        t.onDown(0)
        assertEquals(LongPressTracker.Result.LongPress, t.onUp(700))
    }

    @Test
    fun longPress_exactThreshold_isLong_justBelowIsClick() {
        val a = LongPressTracker()
        a.onDown(100)
        assertEquals(LongPressTracker.Result.LongPress, a.onUp(100 + LongPressTracker.LONG_PRESS_MS))
        val b = LongPressTracker()
        b.onDown(100)
        assertEquals(LongPressTracker.Result.Click, b.onUp(100 + LongPressTracker.LONG_PRESS_MS - 1))
    }

    @Test
    fun longPress_upWithoutDown_isNone_andTrackerReusable() {
        val t = LongPressTracker()
        assertEquals(LongPressTracker.Result.None, t.onUp(50))
        t.onDown(100)
        assertEquals(LongPressTracker.Result.Click, t.onUp(200))
        t.onDown(300)
        assertEquals(LongPressTracker.Result.LongPress, t.onDown(1_000))
        assertEquals(LongPressTracker.Result.None, t.onUp(1_100))
        t.onDown(2_000)
        assertEquals(LongPressTracker.Result.Click, t.onUp(2_050))
    }

    @Test
    fun longPress_cancel_dropsPress() {
        val t = LongPressTracker()
        t.onDown(0)
        t.cancel()
        assertEquals(LongPressTracker.Result.None, t.onUp(900))
    }

    // ---------------------------------------------------------------- sarma

    @Test
    fun seek_holdStepGrows() {
        assertEquals(10_000L, SeekLogic.holdStepMs(0))
        assertEquals(10_000L, SeekLogic.holdStepMs(1_499))
        assertEquals(30_000L, SeekLogic.holdStepMs(1_500))
        assertEquals(60_000L, SeekLogic.holdStepMs(3_500))
        assertEquals(120_000L, SeekLogic.holdStepMs(6_000))
    }

    @Test
    fun seek_targetClampsToBounds() {
        assertEquals(0L, SeekLogic.target(5_000, -10_000, 100_000))
        assertEquals(100_000L, SeekLogic.target(95_000, 10_000, 100_000))
        assertEquals(60_000L, SeekLogic.target(50_000, 10_000, 100_000))
        // süre bilinmiyorsa yalnızca alt sınır
        assertEquals(0L, SeekLogic.target(3_000, -10_000, 0))
        assertEquals(13_000L, SeekLogic.target(3_000, 10_000, 0))
    }

    @Test
    fun seek_label() {
        assertEquals("+10 sn", SeekLogic.label(10_000))
        assertEquals("-30 sn", SeekLogic.label(-30_000))
        assertEquals("+1 dk", SeekLogic.label(60_000))
        assertEquals("-2 dk", SeekLogic.label(-120_000))
    }

    @Test
    fun seekAccelerator_firstPressIsTenSeconds_bothDirections() {
        val a = SeekAccelerator()
        assertEquals(10_000L, a.onKey(0, isRepeat = false, direction = 1))
        assertEquals(-10_000L, a.onKey(5_000, isRepeat = false, direction = -1))
    }

    @Test
    fun seekAccelerator_repeatsAreThrottled() {
        val a = SeekAccelerator()
        assertEquals(10_000L, a.onKey(0, isRepeat = false, direction = 1))
        assertEquals(0L, a.onKey(50, true, 1))            // tekrar aralığından kısa
        assertEquals(0L, a.onKey(200, true, 1))
        assertEquals(10_000L, a.onKey(300, true, 1))       // >= 250 ms: bir adım (basılı 300 ms -> 10 sn)
        assertEquals(0L, a.onKey(400, true, 1))
    }

    @Test
    fun seekAccelerator_holdingAcceleratesMonotonically() {
        val a = SeekAccelerator()
        assertEquals(10_000L, a.onKey(0, false, 1))
        val applied = ArrayList<Long>()
        var t = 50L
        while (t <= 7_000L) {                              // sistem tekrarı ~50 ms
            val d = a.onKey(t, true, 1)
            if (d != 0L) applied.add(d)
            t += 50L
        }
        assertEquals(10_000L, applied.first())
        assertTrue(applied.contains(30_000L))
        assertTrue(applied.contains(60_000L))
        assertEquals(120_000L, applied.last())
        assertTrue(applied.zipWithNext().all { (x, y) -> y >= x })
        // en çok 250 ms'de bir adım
        assertTrue(applied.size <= 7_000 / SeekAccelerator.REPEAT_GAP_MS)
    }

    @Test
    fun seekAccelerator_longSilenceStartsNewSession() {
        val a = SeekAccelerator()
        a.onKey(0, false, 1)
        a.onKey(300, true, 1)
        // 5 sn sessizlik sonrası gelen "tekrar" yeni oturumdur: ilk adım
        assertEquals(10_000L, a.onKey(5_300, true, 1))
    }

    @Test
    fun seekAccelerator_resetStartsFresh() {
        val a = SeekAccelerator()
        a.onKey(0, false, 1)
        a.reset()
        assertEquals(-10_000L, a.onKey(100, true, -1))
    }

    // ---------------------------------------------------------------- oynatıcı tuş eşlemesi

    @Test
    fun playerKeys_mapping() {
        assertEquals(PlayerKeyAction.TogglePlay, PlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_CENTER))
        assertEquals(PlayerKeyAction.TogglePlay, PlayerKeys.actionFor(PlayerKeys.KEYCODE_ENTER))
        assertEquals(PlayerKeyAction.TogglePlay, PlayerKeys.actionFor(PlayerKeys.KEYCODE_NUMPAD_ENTER))
        assertEquals(PlayerKeyAction.TogglePlay, PlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PLAY_PAUSE))
        assertEquals(PlayerKeyAction.Play, PlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PLAY))
        assertEquals(PlayerKeyAction.Pause, PlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_PAUSE))
        assertEquals(PlayerKeyAction.SeekBack, PlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_LEFT))
        assertEquals(PlayerKeyAction.SeekBack, PlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_REWIND))
        assertEquals(PlayerKeyAction.SeekForward, PlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_RIGHT))
        assertEquals(PlayerKeyAction.SeekForward, PlayerKeys.actionFor(PlayerKeys.KEYCODE_MEDIA_FAST_FORWARD))
        assertEquals(PlayerKeyAction.FocusTopBar, PlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_UP))
        assertEquals(PlayerKeyAction.ShowControls, PlayerKeys.actionFor(PlayerKeys.KEYCODE_DPAD_DOWN))
    }

    @Test
    fun playerKeys_backIsIgnored_otherKeysRevealControls() {
        // Geri tuşu BackHandler'a bırakılır (kontroller açıksa önce kapatır)
        assertEquals(PlayerKeyAction.Ignore, PlayerKeys.actionFor(PlayerKeys.KEYCODE_BACK))
        assertFalse(PlayerKeys.revealsControls(PlayerKeyAction.Ignore))
        // herhangi başka tuş kontrolleri geri getirir
        assertEquals(PlayerKeyAction.ShowControls, PlayerKeys.actionFor(82))   // MENU
        assertTrue(PlayerKeys.revealsControls(PlayerKeyAction.ShowControls))
        assertTrue(PlayerKeys.revealsControls(PlayerKeyAction.SeekForward))
    }

    @Test
    fun playerKeys_isSelect() {
        assertTrue(PlayerKeys.isSelect(PlayerKeys.KEYCODE_DPAD_CENTER))
        assertTrue(PlayerKeys.isSelect(PlayerKeys.KEYCODE_ENTER))
        assertTrue(PlayerKeys.isSelect(PlayerKeys.KEYCODE_NUMPAD_ENTER))
        assertFalse(PlayerKeys.isSelect(PlayerKeys.KEYCODE_DPAD_LEFT))
        assertFalse(PlayerKeys.isSelect(PlayerKeys.KEYCODE_BACK))
    }

    // ---------------------------------------------------------------- odak belleği

    @Test
    fun focusMemory_fresh_needsDefaultFocus_thenEntered() {
        val m = FocusMemoryState()
        assertFalse(m.restorePending)
        assertTrue(m.needsDefaultFocus())
        m.markEntered()
        assertFalse(m.needsDefaultFocus())
    }

    @Test
    fun focusMemory_savedKey_restoresOnlyMatchingKey_thenStopsPending() {
        val m = FocusMemoryState("ep:S1E3")
        assertTrue(m.restorePending)
        assertFalse(m.needsDefaultFocus())        // önce geri yükleme beklenir
        assertFalse(m.shouldRestore("play"))
        assertTrue(m.shouldRestore("ep:S1E3"))
        m.onFocused("ep:S1E3")                    // geri yüklemenin kendi odak olayı
        assertTrue(m.restorePending)
        m.markRestored()
        assertFalse(m.restorePending)
        assertFalse(m.shouldRestore("ep:S1E3"))
        assertFalse(m.needsDefaultFocus())        // girildi
        assertEquals("ep:S1E3", m.lastKey)
    }

    @Test
    fun focusMemory_userMovesFocusElsewhere_cancelsPendingRestore() {
        val m = FocusMemoryState("row:continue:a")
        m.onFocused("play")                        // kullanıcı başka öğeye gitti
        assertFalse(m.restorePending)
        assertEquals("play", m.lastKey)
        assertFalse(m.needsDefaultFocus())
    }

    @Test
    fun focusMemory_giveUp_fallsBackToDefault() {
        val m = FocusMemoryState("row:x:gone")
        m.giveUpRestore()
        assertFalse(m.restorePending)
        assertTrue(m.needsDefaultFocus())
    }

    @Test
    fun focusMemory_phaseChange_reArmsDefaultFocus_butFirstPhaseKeepsRestore() {
        val m = FocusMemoryState("tile:1")
        m.enterPhase(true)                          // ilk aşama: bekleyen geri yükleme bozulmaz
        assertTrue(m.restorePending)
        m.markRestored()
        assertFalse(m.needsDefaultFocus())
        m.enterPhase(true)                          // aynı aşama: değişmez
        assertFalse(m.needsDefaultFocus())
        m.enterPhase(false)                         // yükleme/hata aşamasına geçti: odak öğeleri yok oldu
        assertTrue(m.needsDefaultFocus())
        m.markEntered()
        m.enterPhase(true)                          // içeriğe dönüş: ilk odak yeniden
        assertTrue(m.needsDefaultFocus())
    }

    // ---------------------------------------------------------------- ilk odak / sıra

    @Test
    fun detailInitial_prefersPlay_thenTrailer_thenMyList() {
        assertEquals(TvFocusLogic.DetailTarget.Play, TvFocusLogic.detailInitial(canPlay = true, hasTrailer = true))
        assertEquals(TvFocusLogic.DetailTarget.Trailer, TvFocusLogic.detailInitial(canPlay = false, hasTrailer = true))
        assertEquals(TvFocusLogic.DetailTarget.MyList, TvFocusLogic.detailInitial(canPlay = false, hasTrailer = false))
    }

    @Test
    fun homeInitial_heroFirst_thenFirstLoadedRow_thenNone() {
        assertEquals(TvFocusLogic.HomeTarget.Hero, TvFocusLogic.homeInitial(hasHero = true, firstLoadedRow = 2))
        assertEquals(TvFocusLogic.HomeTarget.FirstRow, TvFocusLogic.homeInitial(hasHero = false, firstLoadedRow = 0))
        assertEquals(TvFocusLogic.HomeTarget.None, TvFocusLogic.homeInitial(hasHero = false, firstLoadedRow = -1))
    }

    @Test
    fun firstLoadedRow_skipsUnloadedAndEmpty() {
        assertEquals(-1, TvFocusLogic.firstLoadedRow(emptyList(), emptyList()))
        assertEquals(2, TvFocusLogic.firstLoadedRow(listOf(false, true, true), listOf(0, 0, 5)))
        assertEquals(0, TvFocusLogic.firstLoadedRow(listOf(true, true), listOf(3, 4)))
        assertEquals(-1, TvFocusLogic.firstLoadedRow(listOf(false, false), listOf(0, 0)))
    }

    @Test
    fun heroPage_boundsChecked() {
        assertEquals(1, TvFocusLogic.heroPage(current = 0, count = 3, direction = 1))
        assertEquals(1, TvFocusLogic.heroPage(current = 2, count = 3, direction = -1))
        assertNull(TvFocusLogic.heroPage(current = 0, count = 3, direction = -1))
        assertNull(TvFocusLogic.heroPage(current = 2, count = 3, direction = 1))
        assertNull(TvFocusLogic.heroPage(current = 0, count = 1, direction = 1))
    }

    @Test
    fun episodeIndexForKey_findsByIdOrMinusOne() {
        val ids = listOf("a:s1:e1", "a:s1:e2", "a:s1:e3")
        assertEquals(1, TvFocusLogic.episodeIndexForKey("ep:a:s1:e2", ids))
        assertEquals(-1, TvFocusLogic.episodeIndexForKey("ep:zzz", ids))
        assertEquals(-1, TvFocusLogic.episodeIndexForKey("play", ids))
        assertEquals(-1, TvFocusLogic.episodeIndexForKey(null, ids))
    }

    @Test
    fun episodeEntryIndex_emptyList() {
        assertEquals(-1, TvFocusLogic.episodeEntryIndex(0))
        assertEquals(0, TvFocusLogic.episodeEntryIndex(12))
    }

    @Test
    fun choiceEntryIndex_selectedOrFirst() {
        val ids = listOf("", "drama", "komedi")
        assertEquals(2, TvFocusLogic.choiceEntryIndex(ids, "komedi"))
        assertEquals(0, TvFocusLogic.choiceEntryIndex(ids, ""))
        assertEquals(0, TvFocusLogic.choiceEntryIndex(ids, "yok"))
        assertEquals(0, TvFocusLogic.choiceEntryIndex(ids, null))
    }
}
