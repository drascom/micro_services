package com.diziflix.app.data.repo

import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.DetailExtras
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Season
import com.diziflix.app.data.net.ApiException
import com.diziflix.app.domain.HydrateEvent
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Sanal zamanlı HydrateWatcher testleri (gerçek ağ/bekleme yok): aralık, zaman aşımı, sessiz durma, sınırlar, karar. */
@OptIn(ExperimentalCoroutinesApi::class)
class HydrateWatcherTest {

    private fun series(hydrating: Boolean, seasons: Int = 0, id: String = "s") = Detail(
        Item(id = id, type = "series", title = "Dizi"),
        DetailExtras(seasons = List(seasons) { Season(season = it + 1) }, hydrating = hydrating),
    )

    private fun movie(hydrating: Boolean) = Detail(Item(id = "m", type = "movie", title = "Film"), DetailExtras(hydrating = hydrating))

    private class Harness(scope: TestScope, poll: suspend (String, String) -> Detail, online: () -> Boolean = { true }) {
        val events = ArrayList<HydrateEvent>()
        val watcher = HydrateWatcher(scope.backgroundScope, poll, online)

        init {
            scope.backgroundScope.launch(start = CoroutineStart.UNDISPATCHED) { watcher.events.collect { events += it } }
        }
    }

    @Test
    fun pollsEveryThreeSeconds() = runTest {
        val times = ArrayList<Long>()
        val h = Harness(this, { _, _ -> times += currentTime; series(hydrating = true) })
        assertTrue(h.watcher.watch("s", "Dizi", "p1"))

        advanceTimeBy(2_999); runCurrent()
        assertEquals(emptyList<Long>(), times)            // ilk yoklama 3 sn sonra (hemen değil)
        advanceTimeBy(1); runCurrent()
        assertEquals(listOf(3_000L), times)
        advanceTimeBy(6_000); runCurrent()
        assertEquals(listOf(3_000L, 6_000L, 9_000L), times)
        assertEquals(setOf("s"), h.watcher.active.value)   // hâlâ izleniyor
        assertTrue(h.events.isEmpty())
    }

    @Test
    fun stopsAfter120SecondsSilently() = runTest {
        val times = ArrayList<Long>()
        val h = Harness(this, { _, _ -> times += currentTime; series(hydrating = true) })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(300_000); runCurrent()
        assertTrue("en fazla 40 deneme: ${times.size}", times.size in 39..40)
        assertTrue(times.all { it <= 120_000L })
        assertTrue("süre dolunca bildirim YOK", h.events.isEmpty())
        assertTrue(h.watcher.active.value.isEmpty())
        val count = times.size
        advanceTimeBy(60_000); runCurrent()
        assertEquals("süre dolunca yoklama sürmemeli", count, times.size)
    }

    @Test
    fun networkErrorStopsSilently() = runTest {
        var calls = 0
        val h = Harness(this, { _, _ ->
            calls++
            if (calls == 2) throw ApiException("network", "Sunucuya ulaşılamıyor") else series(hydrating = true)
        })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(60_000); runCurrent()
        assertEquals("hatadan sonra yoklama yok", 2, calls)
        assertTrue(h.events.isEmpty())
        assertTrue(h.watcher.active.value.isEmpty())
    }

    @Test
    fun offlineAtStartNeverPolls() = runTest {
        var calls = 0
        val h = Harness(this, { _, _ -> calls++; series(hydrating = true) }, online = { false })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(10_000); runCurrent()
        assertEquals(0, calls)
        assertTrue(h.events.isEmpty())
        assertTrue(h.watcher.active.value.isEmpty())
    }

    @Test
    fun goingOfflineMidwayStops() = runTest {
        var online = true
        var calls = 0
        val h = Harness(this, { _, _ -> calls++; online = false; series(hydrating = true) }, online = { online })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(60_000); runCurrent()
        assertEquals(1, calls)
        assertTrue(h.events.isEmpty())
        assertTrue(h.watcher.active.value.isEmpty())
    }

    @Test
    fun seriesBecomesReadyEmitsReadyOnce() = runTest {
        var calls = 0
        val h = Harness(this, { _, _ ->
            calls++
            if (calls < 3) series(hydrating = true) else series(hydrating = false, seasons = 2)
        })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(60_000); runCurrent()
        assertEquals(3, calls)
        assertEquals(listOf<HydrateEvent>(HydrateEvent.Ready("s", "Dizi")), h.events)
        assertTrue(h.watcher.active.value.isEmpty())
    }

    @Test
    fun finishedSeriesWithoutSeasonsEmitsUnavailable() = runTest {
        val h = Harness(this, { _, _ -> series(hydrating = false, seasons = 0) })
        h.watcher.watch("s", "Dizi", "p1")

        advanceTimeBy(10_000); runCurrent()
        assertEquals(listOf<HydrateEvent>(HydrateEvent.Unavailable("s", "Dizi")), h.events)
    }

    @Test
    fun noVideoSourceEmitsNoSourceNotUnavailable() = runTest {
        val series = Detail(
            Item(id = "s", type = "series", title = "Dizi", availability = Availability(state = "unavailable", reason = "no_video_source")),
            DetailExtras(hydrating = false),
        )
        val film = Detail(
            Item(id = "m", type = "movie", title = "Film", availability = Availability(state = "unavailable", reason = "no_video_source")),
            DetailExtras(hydrating = false),
        )
        val h = Harness(this, { id, _ -> if (id == "s") series else film })
        h.watcher.watch("s", "Dizi", "p1")
        h.watcher.watch("m", "Film", "p1")

        advanceTimeBy(10_000); runCurrent()
        assertEquals(
            setOf<HydrateEvent>(HydrateEvent.NoSource("s", "Dizi", isSeries = true), HydrateEvent.NoSource("m", "Film", isSeries = false)),
            h.events.toSet(),
        )
        assertEquals(2, h.events.size)
    }

    @Test
    fun finishedMovieIsReady() = runTest {
        val h = Harness(this, { _, _ -> movie(hydrating = false) })
        h.watcher.watch("m", "Film", "p1")

        advanceTimeBy(10_000); runCurrent()
        assertEquals(listOf<HydrateEvent>(HydrateEvent.Ready("m", "Film")), h.events)
    }

    @Test
    fun oneWatcherPerIdAndProfileIsForwarded() = runTest {
        val seen = ArrayList<Pair<String, String>>()
        val h = Harness(this, { id, profile -> seen += id to profile; series(hydrating = true) })
        assertTrue(h.watcher.watch("s", "Dizi", "p1"))
        assertFalse("aynı id için ikinci izleyici açılmaz", h.watcher.watch("s", "Dizi", "p2"))
        assertFalse("boş id izlenmez", h.watcher.watch("", "?", "p1"))

        advanceTimeBy(3_000); runCurrent()
        assertEquals(listOf("s" to "p1"), seen)            // tek yoklama dizisi
    }

    @Test
    fun canWatchAgainAfterFinish() = runTest {
        val h = Harness(this, { _, _ -> series(hydrating = false, seasons = 1) })
        assertTrue(h.watcher.watch("s", "Dizi", "p1"))
        advanceTimeBy(10_000); runCurrent()
        assertTrue(h.watcher.active.value.isEmpty())
        assertTrue(h.watcher.watch("s", "Dizi", "p1"))
    }

    @Test
    fun atMostThreeIdsPolledConcurrentlyRestQueue() = runTest {
        var inFlight = 0
        var maxInFlight = 0
        val polled = ArrayList<String>()
        val h = Harness(this, { id, _ ->
            inFlight++; maxInFlight = maxOf(maxInFlight, inFlight)
            polled += id
            delay(500)
            inFlight--
            series(hydrating = false, seasons = 1, id = id)
        })
        for (id in listOf("a", "b", "c", "d", "e")) h.watcher.watch(id, id.uppercase(), "p1")
        assertEquals(setOf("a", "b", "c", "d", "e"), h.watcher.active.value)   // sıradakiler de "izleniyor"

        advanceTimeBy(3_000); runCurrent()
        assertEquals("ilk turda yalnızca 3 id yoklanır", listOf("a", "b", "c"), polled)

        advanceTimeBy(60_000); runCurrent()
        assertEquals(listOf("a", "b", "c", "d", "e"), polled)
        assertTrue("eşzamanlı yoklama sınırı aşıldı: $maxInFlight", maxInFlight <= 3)
        assertEquals(5, h.events.size)
        assertTrue(h.watcher.active.value.isEmpty())
    }

    @Test
    fun queuedWatcherTimeoutStartsWhenItGetsASlot() = runTest {
        val polled = ArrayList<Pair<String, Long>>()
        val h = Harness(this, { id, _ -> polled += id to currentTime; series(hydrating = true, id = id) })
        for (id in listOf("a", "b", "c", "d")) h.watcher.watch(id, id, "p1")

        advanceTimeBy(119_000); runCurrent()
        assertTrue("d henüz sırada", polled.none { it.first == "d" })
        advanceTimeBy(200_000); runCurrent()
        // a,b,c 120. sn'de bırakır; d o andan itibaren kendi 120 sn'sini yoklar
        val d = polled.filter { it.first == "d" }.map { it.second }
        assertTrue("d sıra gelince yoklamalı", d.isNotEmpty())
        assertTrue(d.first() >= 120_000L)
        assertTrue(h.events.isEmpty())
        assertTrue(h.watcher.active.value.isEmpty())
    }
}
