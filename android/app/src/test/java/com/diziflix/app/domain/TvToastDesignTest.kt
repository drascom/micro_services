package com.diziflix.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android TV bildirimleri: Tizen toast.js ile aynı kurallar (etiketler, süreler, kuyruk). */
class TvToastDesignTest {

    @Test
    fun spec_matchesTizenToast() {
        assertEquals(3_500L, TvToastSpec.TOAST_MS)
        assertEquals(7_000L, TvToastSpec.RICH_MS)
        assertEquals(400L, TvToastSpec.RICH_EXIT_MS)
        assertEquals(3, TvToastSpec.RICH_MAX_WAIT)
        assertEquals(780f, TvToastSpec.RICH_W, 0f)
        assertEquals(800f, TvToastSpec.TOAST_W, 0f)
        assertEquals(220f, TvToastSpec.ORN_W, 0f)
        assertEquals(156f, TvToastSpec.ORN_H, 0f)
        assertEquals(0.14f, TvToastSpec.RICH_TOP_FRACTION, 0f)
    }

    @Test
    fun richItem_defaultsLabelAndDurationByKind() {
        val ok = TvRichToasts.of("Lanterns")
        assertEquals("İzlemeye hazır", ok.label)
        assertEquals(7_000L, ok.ms)
        assertFalse(ok.hasMessage)
        val warn = TvRichToasts.of("Dizi", "Bölümler alınamadı", TvRichKind.Warn)
        assertEquals("Bilgi", warn.label)
        assertTrue(warn.hasMessage)
        val custom = TvRichToasts.of("x", label = "ÖZEL", ms = 1_000L)
        assertEquals("ÖZEL", custom.label)
        assertEquals(1_000L, custom.ms)
        assertEquals(7_000L, TvRichToasts.of("x", ms = -5L).ms)
    }

    @Test
    fun hydrateEvents_mapToRichToasts() {
        val ready = TvRichToasts.forHydrate(HydrateEvent.Ready("id", "Lanterns"))
        assertEquals(TvRichKind.Success, ready.kind)
        assertEquals("Lanterns", ready.title)
        assertEquals("İzlemeye hazır", ready.label)
        assertEquals("", ready.message)

        val failed = TvRichToasts.forHydrate(HydrateEvent.Unavailable("id", "Lanterns"))
        assertEquals(TvRichKind.Warn, failed.kind)
        assertEquals("Lanterns", failed.title)
        assertEquals(HydrateLogic.UNAVAILABLE_MESSAGE, failed.message)

        // kaynak yok: uyarı türü ama "alınamadı" metni DEĞİL
        val noSource = TvRichToasts.forHydrate(HydrateEvent.NoSource("id", "Lanterns", isSeries = true))
        assertEquals(TvRichKind.Warn, noSource.kind)
        assertEquals("Bu dizi için henüz izleme kaynağı yok.", noSource.message)
        assertEquals("Bu film için henüz izleme kaynağı yok.", TvRichToasts.forHydrate(HydrateEvent.NoSource("id", "Alien", isSeries = false)).message)

        assertEquals("İçerik", TvRichToasts.forHydrate(HydrateEvent.Ready("id", "  ")).title)
    }

    @Test
    fun queue_oneAtATime_maxThreeWaiting() {
        val q = TvRichQueue()
        val items = List(6) { TvRichToasts.of("t$it") }
        assertTrue(q.offer(items[0]))                 // boşta: hemen gösterilecek
        assertEquals(items[0], q.next())
        assertTrue(q.busy)
        assertTrue(q.offer(items[1]))                 // meşgul: sıraya
        assertTrue(q.offer(items[2]))
        assertTrue(q.offer(items[3]))
        assertFalse(q.offer(items[4]))                // 3 bekleyen doldu
        assertEquals(3, q.waitingCount)
        assertEquals(items[1], q.next())
        assertEquals(items[2], q.next())
        assertEquals(items[3], q.next())
        assertNull(q.next())                          // bitti
        assertFalse(q.busy)
        assertTrue(q.offer(items[5]))                 // yeniden boşta
    }

    @Test
    fun queue_clearDropsWaitingAndResetsBusy() {
        val q = TvRichQueue()
        q.offer(TvRichToasts.of("a"))
        q.next()
        q.offer(TvRichToasts.of("b"))
        q.clear()
        assertEquals(0, q.waitingCount)
        assertFalse(q.busy)
        assertNull(q.next())
    }
}
