package com.diziflix.app.data.cache

import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.ApiJson
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProgressQueueTest {

    private val store = InMemoryCacheStore()
    private var now = 100L
    private val queue = ProgressQueue(store, ApiJson.instance, { now })

    private fun p(profile: String, item: String, episode: String, pos: Int) =
        PendingProgress(profile, item, episode, pos, 3000)

    @Test
    fun sameEpisodeKeepsSingleRecordWithLatestPosition() = runBlocking {
        queue.enqueue(p("p1", "s1", "s1:s1:e1", 10))
        now = 200L
        queue.enqueue(p("p1", "s1", "s1:s1:e1", 250))
        now = 300L
        queue.enqueue(p("p1", "s1", "s1:s1:e1", 900))

        val pending = queue.pending()
        assertEquals(1, pending.size)
        assertEquals(900, pending[0].positionSec)
    }

    @Test
    fun flushSendsOldestFirstAndEmptiesQueue() = runBlocking {
        queue.enqueue(p("p1", "a", "a", 10)); now += 1
        queue.enqueue(p("p1", "b", "b", 20)); now += 1
        queue.enqueue(p("p1", "a", "a", 30)); now += 1   // a yeniden güncellendi: sona taşınır

        val sent = ArrayList<PendingProgress>()
        val result = queue.flush { sent += it }

        assertEquals(listOf("b" to 20, "a" to 30), sent.map { it.itemId to it.positionSec })
        assertEquals(FlushResult(sent = 2, dropped = 0, remaining = 0), result)
        assertTrue(queue.isEmpty())
        assertTrue(!store.files.containsKey(LocalCache.PROGRESS_QUEUE))
    }

    @Test
    fun recordKeepsItsOwnProfileSoProfileSwitchDoesNotMixThem() = runBlocking {
        queue.enqueue(p("p1", "x", "x", 11)); now += 1
        queue.enqueue(p("p2", "x", "x", 22))   // aynı yapım, başka profil: ayrı kayıt

        val sent = ArrayList<PendingProgress>()
        queue.flush { sent += it }
        assertEquals(listOf("p1" to 11, "p2" to 22), sent.map { it.profileId to it.positionSec })
    }

    @Test
    fun networkFailureStopsAndKeepsRemaining() = runBlocking {
        queue.enqueue(p("p1", "a", "a", 1)); now += 1
        queue.enqueue(p("p1", "b", "b", 2)); now += 1
        queue.enqueue(p("p1", "c", "c", 3))

        var calls = 0
        val result = queue.flush {
            calls++
            if (it.itemId == "b") throw ApiException("network", "yok")
        }
        assertEquals(2, calls)   // a gitti, b ağ hatası: durur, c denenmez
        assertEquals(FlushResult(sent = 1, dropped = 0, remaining = 2), result)
        assertEquals(listOf("b", "c"), queue.pending().map { it.itemId })
    }

    @Test
    fun permanentClientErrorDropsRecordAndContinues() = runBlocking {
        queue.enqueue(p("gone", "a", "a", 1)); now += 1
        queue.enqueue(p("p1", "b", "b", 2))

        val sent = ArrayList<String>()
        val result = queue.flush {
            if (it.profileId == "gone") throw ApiException("bad_request", "silinmiş profil", 400)
            sent += it.itemId
        }
        assertEquals(listOf("b"), sent)
        assertEquals(FlushResult(sent = 1, dropped = 1, remaining = 0), result)
    }

    @Test
    fun newerPositionEnqueuedDuringSendIsNotLost() = runBlocking {
        queue.enqueue(p("p1", "a", "a", 10))
        queue.flush {
            now += 5
            queue.enqueue(p("p1", "a", "a", 500))   // gönderim sürerken daha yeni konum geldi
        }
        val left = queue.pending()
        assertEquals(1, left.size)
        assertEquals(500, left[0].positionSec)
    }

    @Test
    fun discardRemovesOnlyThatKey() = runBlocking {
        queue.enqueue(p("p1", "a", "a", 1)); now += 1
        queue.enqueue(p("p1", "b", "b", 2))
        queue.discard("p1", "a", "a")
        assertEquals(listOf("b"), queue.pending().map { it.itemId })
    }

    @Test
    fun transientHttpErrorsAreNotPermanent() {
        assertTrue(ProgressQueue.isPermanentFailure(ApiException("bad_request", "x", 400)))
        assertTrue(ProgressQueue.isPermanentFailure(ApiException("not_found", "x", 404)))
        assertTrue(!ProgressQueue.isPermanentFailure(ApiException("http_500", "x", 500)))
        assertTrue(!ProgressQueue.isPermanentFailure(ApiException("http_429", "x", 429)))
        assertTrue(!ProgressQueue.isPermanentFailure(ApiException("timeout", "x", 0)))
    }
}
