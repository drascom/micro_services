package com.diziflix.app.domain

import com.diziflix.app.data.net.ApiException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class LayoutAndErrorLogicTest {

    // ---------------------------------------------------------------- hero / yerleşim

    @Test
    fun hero_phonePortrait_is4by3() {
        // 360x800dp: 4:3 -> 270dp (ekranın %60'ı 480dp'nin altında)
        assertEquals(270f, LayoutLogic.heroHeightDp(360f, 800f), 0.01f)
    }

    @Test
    fun hero_tabletLandscape_isCappedToScreenFraction_notSquare() {
        // 1143x685dp (Nokia T10 yatay): 16:9 = 643dp ama ekranın %60'ı = 411dp'de kesilir
        val h = LayoutLogic.heroHeightDp(1143f, 685f)
        assertEquals(411f, h, 0.01f)
        assertTrue(h < 685f)
    }

    @Test
    fun hero_tabletPortrait_is16by9() {
        // 685x1143dp: 16:9 = 385dp (sınır 686dp)
        assertEquals(385.3f, LayoutLogic.heroHeightDp(685f, 1143f), 0.1f)
    }

    @Test
    fun hero_phoneLandscape_keepsMinimumHeight() {
        // 800x360dp: sınır 216dp < min 240dp -> 240dp
        assertEquals(LayoutLogic.HERO_MIN_DP, LayoutLogic.heroHeightDp(800f, 360f), 0.01f)
    }

    @Test
    fun detailBackdrop_isAtMostHalfScreenHeight() {
        assertEquals(202.5f, LayoutLogic.detailBackdropHeightDp(360f, 800f), 0.01f)          // 16:9
        assertEquals(342.5f, LayoutLogic.detailBackdropHeightDp(1143f, 685f), 0.01f)         // yarıda kesilir
        assertEquals(LayoutLogic.DETAIL_BACKDROP_MIN_DP, LayoutLogic.detailBackdropHeightDp(800f, 300f), 0.01f)
    }

    @Test
    fun posterWidth_growsWithScreen() {
        assertEquals(112f, LayoutLogic.posterWidthDp(360f), 0f)
        assertEquals(140f, LayoutLogic.posterWidthDp(685f), 0f)
        assertEquals(160f, LayoutLogic.posterWidthDp(1143f), 0f)
    }

    // ---------------------------------------------------------------- hata nedeni

    private val base = "http://192.168.0.61:8090"

    @Test
    fun offline_networkError_saysNotConnected() {
        val f = ErrorLogic.classify(ApiException("network", "Sunucuya ulaşılamıyor: x:1"), online = false, baseUrl = base)
        assertEquals(FailureKind.Offline, f.kind)
        assertEquals("Ağa bağlı değilsiniz", f.message)
    }

    @Test
    fun offline_timeoutAlsoSaysNotConnected() {
        val f = ErrorLogic.classify(ApiException("timeout", "Sunucu zaman aşımı"), online = false, baseUrl = base)
        assertEquals(FailureKind.Offline, f.kind)
    }

    @Test
    fun online_networkError_saysServerUnreachableWithAddress() {
        val f = ErrorLogic.classify(ApiException("network", "x"), online = true, baseUrl = base)
        assertEquals(FailureKind.ServerUnreachable, f.kind)
        assertEquals("Sunucuya ulaşılamıyor: http://192.168.0.61:8090", f.message)
    }

    @Test
    fun online_timeout_isServerUnreachableMentioningTimeout() {
        val f = ErrorLogic.classify(ApiException("timeout", "x"), online = true, baseUrl = base)
        assertEquals(FailureKind.ServerUnreachable, f.kind)
        assertEquals("Sunucuya ulaşılamıyor: http://192.168.0.61:8090 (zaman aşımı)", f.message)
    }

    @Test
    fun rawIoException_isTreatedAsConnectivity() {
        assertEquals(FailureKind.Offline, ErrorLogic.classify(IOException("boom"), online = false, baseUrl = base).kind)
        assertEquals(FailureKind.ServerUnreachable, ErrorLogic.classify(IOException("boom"), online = true, baseUrl = base).kind)
    }

    @Test
    fun serverAndOtherErrors_keepTheirOwnMessage_evenWhenOffline() {
        val http = ErrorLogic.classify(ApiException("http_500", "Sunucu hatası (500)", 500), online = false, baseUrl = base)
        assertEquals(FailureKind.Other, http.kind)
        assertEquals("Sunucu hatası (500)", http.message)
        val json = ErrorLogic.classify(ApiException("bad_json", "Geçersiz sunucu yanıtı"), online = true, baseUrl = base)
        assertEquals(FailureKind.Other, json.kind)
        assertEquals("Geçersiz sunucu yanıtı", json.message)
        assertEquals("Bilinmeyen hata", ErrorLogic.classify(RuntimeException(), online = true, baseUrl = base).message)
    }

    @Test
    fun badBase_isAddressProblem() {
        val f = ErrorLogic.classify(ApiException("bad_base", "Geçersiz sunucu adresi: ???"), online = true, baseUrl = "???")
        assertEquals(FailureKind.BadAddress, f.kind)
        assertEquals("Geçersiz sunucu adresi: ???", f.message)
    }
}
