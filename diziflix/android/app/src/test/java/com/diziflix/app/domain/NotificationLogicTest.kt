package com.diziflix.app.domain

import com.diziflix.app.data.model.AppNotification
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Bildirim toast metni, toplu gösterim sınırı ve yoklama kapısı (oynatıcıda durur). */
class NotificationLogicTest {

    private fun found(id: Long, title: String = "Dizi", season: Int? = 1, episode: Int? = 3, kind: String = "source_found") =
        AppNotification(id = id, kind = kind, canonicalId = "tmdb_tv_1", episodeId = "tmdb_tv_1:s1:e3", title = title, season = season, episode = episode)

    @Test
    fun seriesToast_hasSeasonAndEpisodeCode() {
        assertEquals("Kaynak bulundu: Dizi S01 B03", NotificationLogic.message(found(1)))
        assertEquals("Kaynak bulundu: Dizi S12 B10", NotificationLogic.message(found(2, season = 12, episode = 10)))
    }

    @Test
    fun movieToast_isTitleOnly() {
        assertEquals("Kaynak bulundu: Film", NotificationLogic.message(found(1, title = "Film", season = null, episode = null)))
        assertEquals("Kaynak bulundu: Yapım", NotificationLogic.message(found(1, title = " ", season = null, episode = null)))
    }

    @Test
    fun toasts_onlySourceFound_firstThreeThenSummary() {
        val items = (1L..5L).map { found(it, title = "D$it") } + found(9, kind = "other")
        val toasts = NotificationLogic.toasts(items)
        assertEquals(4, toasts.size)
        assertEquals(listOf("D1", "D2", "D3"), toasts.take(3).map { it.target!!.title })
        assertEquals("Kaynak bulundu: 2 yapım daha", toasts[3].message)
        assertNull(toasts[3].target)
        assertTrue(NotificationLogic.toasts(listOf(found(1, kind = "other"))).isEmpty())
    }

    @Test
    fun polling_pausesOnlyInPlayer() {
        assertFalse(NotificationLogic.shouldPoll("player/{id}?episode={episode}&kind={kind}"))
        assertTrue(NotificationLogic.shouldPoll("home"))
        assertTrue(NotificationLogic.shouldPoll("detail/{id}?episode={episode}"))
        assertTrue(NotificationLogic.shouldPoll("catalog/{row}"))
        assertTrue(NotificationLogic.shouldPoll("search"))
        assertTrue(NotificationLogic.shouldPoll(null))
        assertEquals(30_000L, NotificationLogic.POLL_INTERVAL_MS)
    }
}
