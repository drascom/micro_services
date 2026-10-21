package com.diziflix.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Üst menü odak belleği: öğeyle ayrılıp Geri ile dönünce o öğeye odak (Ayarlar/Listem/Arama), bayat bellek geri yüklenmez. */
class TvBarReturnTest {
    private val HOME = "home"
    private val SETTINGS = "settings"

    @Test
    fun settingsAndBack_restoresSettingsItem_once() {
        val r = TvBarReturn()
        r.arm(TvBarItem.Settings, from = HOME, to = SETTINGS)
        r.onRoute(SETTINGS)
        r.onRoute(HOME)
        assertEquals(TvBarItem.Settings, r.take(HOME))
        // bir kez: sonraki girişte (ör. başka ekrandan ana sayfaya) odak geri yüklenmez
        assertNull(r.take(HOME))
    }

    @Test
    fun nothingArmed_nothingRestored() {
        assertNull(TvBarReturn().take(HOME))
    }

    @Test
    fun takeBeforeLeaving_doesNothing() {
        val r = TvBarReturn()
        r.arm(TvBarItem.MyList, from = HOME, to = "mylist")
        // henüz gidilmedi (ör. ana sayfa yeniden kuruldu): geri yükleme yok, bellek korunur
        assertNull(r.take(HOME))
        r.onRoute("mylist")
        r.onRoute(HOME)
        assertEquals(TvBarItem.MyList, r.take(HOME))
    }

    @Test
    fun differentReturnRoute_isNotRestored_andGoingElsewhereClears() {
        val r = TvBarReturn()
        // Filmler'den Listem'e: Geri ana sayfaya döner (sekme yığını) -> bayat bellek silinir
        r.arm(TvBarItem.MyList, from = "movies", to = "mylist")
        r.onRoute("mylist")
        r.onRoute(HOME)
        assertNull(r.take(HOME))
        assertNull(r.item)
        assertNull(r.take("movies"))
    }

    @Test
    fun wrongScreenDoesNotConsume() {
        val r = TvBarReturn()
        r.arm(TvBarItem.Settings, from = "movies", to = SETTINGS)
        r.onRoute(SETTINGS)
        r.onRoute("movies")
        assertNull(r.take(HOME))
        assertEquals(TvBarItem.Settings, r.take("movies"))
    }

    @Test
    fun newArmOverridesAndClearDrops() {
        val r = TvBarReturn()
        r.arm(TvBarItem.Settings, HOME, SETTINGS)
        r.arm(TvBarItem.Search, HOME, "search")
        r.onRoute("search")
        r.clear()
        assertNull(r.take(HOME))
    }
}
