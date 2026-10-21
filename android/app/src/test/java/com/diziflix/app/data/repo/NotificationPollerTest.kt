package com.diziflix.app.data.repo

import com.diziflix.app.data.model.AppNotification
import com.diziflix.app.data.model.NotificationsResponse
import com.diziflix.app.data.net.ApiException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Bildirim yoklayıcı: ilk çalıştırmada toast yok (imleç ilerler), yeni bildirimde toast sonra imleç + read, hata imleci ilerletmez. */
class NotificationPollerTest {

    private fun n(id: Long, kind: String = "source_found") = AppNotification(id = id, kind = kind, canonicalId = "c", title = "T$id")

    private class Env(var response: () -> NotificationsResponse) {
        val stored = HashMap<String, Long>()
        val fetched = ArrayList<Pair<String, Long>>()
        val reads = ArrayList<Pair<String, Long>>()
        var readFails = false
        val order = ArrayList<String>()

        val poller = NotificationPoller(
            fetch = { profile, since ->
                fetched += profile to since
                response()
            },
            markRead = { profile, upto ->
                order += "read"
                if (readFails) throw ApiException("network", "yok")
                reads += profile to upto
            },
            loadSince = { stored[it] },
            saveSince = { profile, since ->
                order += "save"
                stored[profile] = since
            },
        )
    }

    @Test
    fun firstRun_doesNotShow_onlyAdvancesCursor() = runBlocking {
        val env = Env { NotificationsResponse(listOf(n(3), n(7)), lastId = 7) }
        var shown = 0
        env.poller.pollOnce("p1") { shown += it.size }
        assertEquals(0, shown)
        assertEquals(7L, env.stored["p1"])
        assertEquals(listOf("p1" to 0L), env.fetched)        // ilk istek since=0
        assertTrue(env.reads.isEmpty())
    }

    @Test
    fun firstRun_withNoNotifications_storesZero_soNextRunShows() = runBlocking {
        val env = Env { NotificationsResponse(emptyList(), lastId = 0) }
        env.poller.pollOnce("p1") { fail("toast olmamalı") }
        assertEquals(0L, env.stored["p1"])
        env.response = { NotificationsResponse(listOf(n(1)), lastId = 1) }
        val shown = ArrayList<AppNotification>()
        env.poller.pollOnce("p1") { shown += it }
        assertEquals(listOf(1L), shown.map { it.id })       // imleç 0 saklıydı: ikinci çalıştırmada yeni bildirim gösterilir
    }

    @Test
    fun newNotification_showsThenAdvancesCursorThenMarksRead() = runBlocking {
        val env = Env { NotificationsResponse(listOf(n(8), n(9)), lastId = 9) }
        env.stored["p1"] = 7
        val shown = ArrayList<AppNotification>()
        env.poller.pollOnce("p1") { shown += it; env.order += "show" }
        assertEquals(listOf(8L, 9L), shown.map { it.id })
        assertEquals(9L, env.stored["p1"])
        assertEquals(listOf("p1" to 9L), env.reads)
        assertEquals(listOf("show", "save", "read"), env.order)  // gösterildikten SONRA imleç ve read
        assertEquals(listOf("p1" to 7L), env.fetched)            // since = kayıtlı imleç
    }

    @Test
    fun nothingNew_noShowNoReadNoSave() = runBlocking {
        val env = Env { NotificationsResponse(emptyList(), lastId = 7) }
        env.stored["p1"] = 7
        env.poller.pollOnce("p1") { fail("toast olmamalı") }
        assertTrue(env.order.isEmpty())
        assertEquals(7L, env.stored["p1"])
    }

    @Test
    fun otherKinds_areNotShown_butCursorAndReadAdvance() = runBlocking {
        val env = Env { NotificationsResponse(listOf(n(5, kind = "mystery")), lastId = 5) }
        env.stored["p1"] = 4
        env.poller.pollOnce("p1") { fail("toast olmamalı") }
        assertEquals(5L, env.stored["p1"])
        assertEquals(listOf("p1" to 5L), env.reads)
    }

    @Test
    fun cursorIsPerProfile() = runBlocking {
        val env = Env { NotificationsResponse(listOf(n(2)), lastId = 2) }
        env.stored["p1"] = 1
        env.poller.pollOnce("p2") { fail("p2'nin imleci yok: ilk çalıştırma, toast olmamalı") }
        assertEquals(2L, env.stored["p2"])
        assertEquals(1L, env.stored["p1"])
    }

    @Test
    fun readFailure_isSwallowed_cursorStillAdvanced() = runBlocking {
        val env = Env { NotificationsResponse(listOf(n(2)), lastId = 2) }
        env.stored["p1"] = 1
        env.readFails = true
        env.poller.pollOnce("p1") { }
        assertEquals(2L, env.stored["p1"])
    }

    @Test
    fun fetchFailure_propagates_andKeepsCursor() = runBlocking {
        val env = Env { throw ApiException("network", "yok") }
        env.stored["p1"] = 5
        try {
            env.poller.pollOnce("p1") { fail("toast olmamalı") }
            fail("ApiException bekleniyordu")
        } catch (e: ApiException) {
            assertEquals("network", e.code)
        }
        assertEquals(5L, env.stored["p1"])
        assertNull(env.stored["p2"])
    }
}
