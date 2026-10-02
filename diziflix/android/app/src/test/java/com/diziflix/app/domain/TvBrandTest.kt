package com.diziflix.app.domain

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Marka görselleri (docs/brand -> res): boyutlar Tizen ile aynı, PNG'lerin en-boy oranı yerleşim ölçüleriyle uyumlu, her biri
 * < 150 KB, manifest/ikon kaynakları var. (Birim testleri modül dizininde çalışır: `src/main/res`.)
 */
class TvBrandTest {
    private val res = File("src/main/res")

    private fun png(rel: String): File = File(res, rel).also { assertTrue("$rel yok", it.isFile) }

    /** PNG IHDR'den (bayt 16..23) genişlik x yükseklik. */
    private fun size(rel: String): Pair<Int, Int> {
        val head = png(rel).inputStream().use { it.readNBytes(24) }
        fun int(at: Int) = ((head[at].toInt() and 0xFF) shl 24) or ((head[at + 1].toInt() and 0xFF) shl 16) or
            ((head[at + 2].toInt() and 0xFF) shl 8) or (head[at + 3].toInt() and 0xFF)
        assertEquals("PNG imzası", 0x89, head[0].toInt() and 0xFF)
        return int(16) to int(20)
    }

    @Test
    fun brandPngs_matchSpecPixels_andStaySmall() {
        assertEquals(TvBrandSpec.WIDE_PX_W to TvBrandSpec.WIDE_PX_H, size("drawable-nodpi/brand_logo_wide.png"))
        assertEquals(TvBrandSpec.SQUARE_PX_W to TvBrandSpec.SQUARE_PX_H, size("drawable-nodpi/brand_logo_square.png"))
        assertEquals(TvBrandSpec.MASCOT_PX_W to TvBrandSpec.MASCOT_PX_H, size("drawable-nodpi/brand_mascot.png"))
        assertEquals(TvBrandSpec.EMBLEM_PX_W to TvBrandSpec.EMBLEM_PX_H, size("drawable-nodpi/brand_mascot_sm.png"))
        for (name in listOf("brand_logo_wide", "brand_logo_square", "brand_mascot", "brand_mascot_sm", "ic_launcher_foreground", "ic_launcher_monochrome")) {
            assertTrue("$name < 150 KB", png("drawable-nodpi/$name.png").length() < 150 * 1024)
        }
    }

    @Test
    fun layoutBoxes_keepPngAspectRatio() {
        fun ratio(w: Float, h: Float) = w / h
        fun near(a: Float, b: Float) = kotlin.math.abs(a - b) / b < 0.02f
        assertTrue(near(ratio(TvBrandSpec.WIDE_W, TvBrandSpec.WIDE_H), TvBrandSpec.WIDE_PX_W.toFloat() / TvBrandSpec.WIDE_PX_H))
        assertTrue(near(ratio(TvBrandSpec.SQUARE_W, TvBrandSpec.SQUARE_H), TvBrandSpec.SQUARE_PX_W.toFloat() / TvBrandSpec.SQUARE_PX_H))
        assertTrue(near(ratio(TvBrandSpec.MASCOT_LOAD_W, TvBrandSpec.MASCOT_LOAD_H), TvBrandSpec.MASCOT_PX_W.toFloat() / TvBrandSpec.MASCOT_PX_H))
        assertTrue(near(ratio(TvBrandSpec.MASCOT_ERR_W, TvBrandSpec.MASCOT_ERR_H), TvBrandSpec.MASCOT_PX_W.toFloat() / TvBrandSpec.MASCOT_PX_H))
        assertTrue(near(ratio(TvBrandSpec.EMBLEM_W, TvBrandSpec.EMBLEM_H), TvBrandSpec.EMBLEM_PX_W.toFloat() / TvBrandSpec.EMBLEM_PX_H))
        // boot plakası kare logoyu içerir; üst menüdeki amblem 120 px'lik çubuğa sığar
        assertTrue(TvBrandSpec.SQUARE_W < TvBrandSpec.BOOT_PLATE_W && TvBrandSpec.SQUARE_H < TvBrandSpec.BOOT_PLATE_H)
        assertTrue(TvBrandSpec.EMBLEM_H + 12f < 120f)
        // profil ekranı: geniş logo + başlık + avatar + düğme 1080 px sahneye sığar
        val total = TvBrandSpec.WIDE_H + TvBrandSpec.WIDE_MB + 52f + TvProfilesSpec.TITLE_MB +
            TvProfilesSpec.AVATAR * TvProfilesSpec.FOCUS_SCALE + TvProfilesSpec.AVATAR_MB + 60f + TvProfilesSpec.FOOT_MT + 64f
        assertTrue("profil ekranı $total px", total < 1080f)
    }

    @Test
    fun launcherAndBanner_assetsExist_withExpectedSizes() {
        assertEquals(432 to 432, size("drawable-nodpi/ic_launcher_foreground.png"))
        assertEquals(432 to 432, size("drawable-nodpi/ic_launcher_monochrome.png"))
        // TV afişi: 320x180 dp (xhdpi 640x360, xxhdpi 960x540)
        assertEquals(640 to 360, size("drawable-xhdpi/tv_banner.png"))
        assertEquals(960 to 540, size("drawable-xxhdpi/tv_banner.png"))
        for ((dens, px) in listOf("mdpi" to 48, "hdpi" to 72, "xhdpi" to 96, "xxhdpi" to 144, "xxxhdpi" to 192)) {
            assertEquals(px to px, size("mipmap-$dens/ic_launcher.png"))
            assertEquals(px to px, size("mipmap-$dens/ic_launcher_round.png"))
        }
        for (xml in listOf("mipmap-anydpi-v26/ic_launcher.xml", "mipmap-anydpi-v26/ic_launcher_round.xml")) {
            val text = File(res, xml).readText()
            assertTrue(text.contains("@drawable/ic_launcher_foreground") && text.contains("@drawable/ic_launcher_background"))
            assertTrue("themed icon", text.contains("@drawable/ic_launcher_monochrome"))
        }
        assertTrue(File(res, "drawable/ic_launcher_background.xml").isFile)
        // Android 12+ açılış: karakter + koyu zemin
        val v31 = File(res, "values-v31/themes.xml").readText()
        assertTrue(v31.contains("windowSplashScreenAnimatedIcon") && v31.contains("windowSplashScreenBackground"))
    }

    @Test
    fun manifest_pointsAtIconsAndBanner() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android:icon=\"@mipmap/ic_launcher\""))
        assertTrue(manifest.contains("android:roundIcon=\"@mipmap/ic_launcher_round\""))
        assertTrue(manifest.contains("android:banner=\"@drawable/tv_banner\""))
    }

    @Test
    fun detailActionRow_mayOverflowBodyUpToSafeArea() {
        // 4 eylem düğmesi (~1050 px) 1000 px'lik gövdeden taşar ama güvenli alan içinde kalır; daha fazlası ikinci satıra sarar
        assertTrue(TvDetailSpec.ACTIONS_MAX_W > TvDetailSpec.BODY_W)
        assertTrue(TvDetailSpec.BODY_LEFT + TvDetailSpec.ACTIONS_MAX_W <= 1920f - 60f)
    }
}
