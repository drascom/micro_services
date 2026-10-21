package com.diziflix.app.domain

import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.UrlUtil
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class OfflineLogicTest {

    @Test
    fun relativeTime_isHumanReadableTurkish() {
        val now = 10_000_000L
        assertEquals("az önce", Format.relativeTime(now, now))
        assertEquals("az önce", Format.relativeTime(now, now + 5_000))        // gelecek/saat farkı
        assertEquals("az önce", Format.relativeTime(now, now - 59_000))
        assertEquals("1 dk önce", Format.relativeTime(now, now - 60_000))
        assertEquals("59 dk önce", Format.relativeTime(now, now - 59 * 60_000L))
        assertEquals("1 sa önce", Format.relativeTime(now, now - 60 * 60_000L))
        assertEquals("23 sa önce", Format.relativeTime(now, now - 23 * 3_600_000L - 59 * 60_000L))
        assertEquals("1 gün önce", Format.relativeTime(now, now - 24 * 3_600_000L))
        assertEquals("3 gün önce", Format.relativeTime(now, now - 3 * 24 * 3_600_000L))
    }

    @Test
    fun offlineNotice_labelMentionsLastUpdate() {
        val now = 100 * 60_000L
        assertEquals(
            "Çevrimdışı · son güncelleme: 5 dk önce",
            OfflineNotice(FailureKind.Offline, now - 5 * 60_000L).label(now),
        )
        assertEquals(
            "Sunucuya ulaşılamıyor · son güncelleme: az önce",
            OfflineNotice(FailureKind.ServerUnreachable, now).label(now),
        )
    }

    @Test
    fun offlineNotice_onlyForConnectivityErrors() {
        val network = ApiException("network", "x")
        val timeout = ApiException("timeout", "x")
        val badProfile = ApiException("bad_request", "silinmiş profil", 400)

        assertEquals(FailureKind.Offline, ErrorLogic.offlineNotice(network, online = false, savedAtMs = 5)!!.kind)
        assertEquals(FailureKind.ServerUnreachable, ErrorLogic.offlineNotice(timeout, online = true, savedAtMs = 5)!!.kind)
        assertEquals(5L, ErrorLogic.offlineNotice(network, online = false, savedAtMs = 5)!!.savedAtMs)
        assertNull(ErrorLogic.offlineNotice(badProfile, online = true, savedAtMs = 5))
        assertNotNull(ErrorLogic.offlineNotice(IOException("reset"), online = false, savedAtMs = 1))
    }

    @Test
    fun isConnectivityError_distinguishesHttpAndParseErrors() {
        assertTrue(ErrorLogic.isConnectivityError(ApiException("network", "x")))
        assertTrue(ErrorLogic.isConnectivityError(ApiException("timeout", "x")))
        assertTrue(ErrorLogic.isConnectivityError(IOException("x")))
        assertFalse(ErrorLogic.isConnectivityError(ApiException("bad_json", "x")))
        assertFalse(ErrorLogic.isConnectivityError(ApiException("http_500", "x", 500)))
        assertFalse(ErrorLogic.isConnectivityError(IllegalStateException("x")))
    }

    @Test
    fun offlinePlayMessage_isClear() {
        assertEquals("Oynatmak için internet gerekli", ErrorLogic.OFFLINE_PLAY_MESSAGE)
    }

    // ---------------------------------------------------------------- kademeli yükleme + görsel boyutları

    @Test
    fun nextCardLimit_growsNearTheEndUpToTotal() {
        assertEquals(20, LayoutLogic.nextCardLimit(current = 20, total = 20, lastVisibleIndex = 19))
        assertEquals(16, LayoutLogic.nextCardLimit(current = 16, total = 40, lastVisibleIndex = 5))   // uzak: artmaz
        assertEquals(32, LayoutLogic.nextCardLimit(current = 16, total = 40, lastVisibleIndex = 12))  // sona 4 kala
        assertEquals(40, LayoutLogic.nextCardLimit(current = 32, total = 40, lastVisibleIndex = 31))  // en çok total
    }

    @Test
    fun lastRowToLoad_firstThreeRowsImmediatelyThenOneAhead() {
        assertEquals(2, LayoutLogic.lastRowToLoad(lastVisibleRow = -1, rowCount = 10))   // hiç satır görünmüyor: ilk 3
        assertEquals(2, LayoutLogic.lastRowToLoad(lastVisibleRow = 0, rowCount = 10))
        assertEquals(5, LayoutLogic.lastRowToLoad(lastVisibleRow = 4, rowCount = 10))    // görünen 4 -> 5'e kadar
        assertEquals(9, LayoutLogic.lastRowToLoad(lastVisibleRow = 9, rowCount = 10))    // sınırı aşmaz
        assertEquals(1, LayoutLogic.lastRowToLoad(lastVisibleRow = 0, rowCount = 2))
        assertEquals(-1, LayoutLogic.lastRowToLoad(lastVisibleRow = 0, rowCount = 0))
    }

    @Test
    fun imageSizes_areSmallWhitelistedSizes() {
        // server/app/images.py SIZES whitelist'inde olmalı
        assertEquals(200 to 300, UrlUtil.POSTER_W to UrlUtil.POSTER_H)
        assertEquals(320 to 180, UrlUtil.STILL_W to UrlUtil.STILL_H)
        assertEquals(780 to 439, UrlUtil.backdropSizeFor(800))      // Nokia T10: 640dp @ 1.25 = 800px
        assertEquals(780 to 439, UrlUtil.backdropSizeFor(720))
        assertEquals(1280 to 720, UrlUtil.backdropSizeFor(1080))    // telefon
        assertEquals(1280 to 720, UrlUtil.backdropSizeFor(1600))
    }

    @Test
    fun posterUrlIsForcedToSmallSizeEvenWhenServerGivesLargerOne() {
        assertEquals(
            "http://h:8090/img/s_1/portrait?w=200&h=300",
            UrlUtil.sizedTo("http://h:8090", "/img/s_1/portrait?w=300&h=450", UrlUtil.POSTER_W, UrlUtil.POSTER_H),
        )
    }
}
