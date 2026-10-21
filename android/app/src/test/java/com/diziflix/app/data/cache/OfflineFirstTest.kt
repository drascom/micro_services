package com.diziflix.app.data.cache

import com.diziflix.app.data.net.ApiException
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** "Önce önbellek, arkada yenile" akışının sözleşmesi (sahte depolama + sahte ağ). */
class OfflineFirstTest {

    private val netError = ApiException("network", "Sunucuya ulaşılamıyor")

    @Test
    fun cacheIsEmittedFirstThenNetworkRefresh_andFreshValueIsStored() = runBlocking {
        var stored: String? = null
        val emissions = offlineFirst(
            read = { Cached("eski", 1_000L) },
            write = { stored = it },
            fetch = { "yeni" },
            clock = { 5_000L },
        ).toList()

        assertEquals(2, emissions.size)
        val first = emissions[0]
        assertEquals("eski", first.value)
        assertTrue(first.fromCache)
        assertTrue(first.refreshing)
        assertEquals(1_000L, first.savedAtMs)
        assertNull(first.refreshError)

        val second = emissions[1]
        assertEquals("yeni", second.value)
        assertFalse(second.fromCache)
        assertEquals(5_000L, second.savedAtMs)
        assertEquals("yeni", stored)
    }

    @Test
    fun networkFailureKeepsCachedValueAndFlagsRefreshError() = runBlocking {
        var written = false
        val emissions = offlineFirst(
            read = { Cached("eski", 1_000L) },
            write = { written = true },
            fetch = { throw netError },
        ).toList()

        assertEquals(2, emissions.size)
        val last = emissions.last()
        assertEquals("eski", last.value)              // yerel veri korunur
        assertTrue(last.fromCache)
        assertFalse(last.refreshing)
        assertEquals(netError, last.refreshError)     // çevrimdışı durumu ekrana bildirilir
        assertFalse(written)
    }

    @Test
    fun noCacheAndNoNetworkThrowsTheNetworkError() = runBlocking {
        var failure: Throwable? = null
        val emissions = offlineFirst<String>(
            read = { null },
            write = {},
            fetch = { throw netError },
        ).catch { failure = it }.toList()

        assertTrue(emissions.isEmpty())
        assertEquals(netError, failure)
    }

    @Test
    fun noCacheWithNetworkEmitsOnlyFreshValue() = runBlocking {
        val emissions = offlineFirst(
            read = { null },
            write = {},
            fetch = { "taze" },
            clock = { 9L },
        ).toList()
        assertEquals(1, emissions.size)
        assertEquals("taze", emissions[0].value)
        assertFalse(emissions[0].fromCache)
        assertNotNull(emissions[0].savedAtMs)
    }

    @Test
    fun unreadableCacheIsTreatedAsMissAndWriteFailureDoesNotHideFreshValue() = runBlocking {
        val emissions = offlineFirst(
            read = { error("bozuk dosya") },
            write = { error("disk dolu") },
            fetch = { "taze" },
        ).toList()
        assertEquals(listOf("taze"), emissions.map { it.value })
    }
}
