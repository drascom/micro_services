package com.diziflix.app.play

import com.diziflix.app.data.net.ApiException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Kaynak bulucu: hata paneli metinleri ve 5 sn'lik durum yoklaması (sanal zaman). */
@OptIn(ExperimentalCoroutinesApi::class)
class SourceFinderLogicTest {

    @Test
    fun messages_matchTheContract() {
        assertEquals(
            "Kaynak aranıyor… Bulununca haber vereceğiz. Sayfada kalabilir ya da uygulamada gezinebilirsin.",
            SourceFinderLogic.message("searching"),
        )
        assertEquals("Bu bölüm için kaynak bulunamadı.", SourceFinderLogic.message("not_found"))
        // finder yok / bilinmeyen -> genel mesaj (çağıran)
        assertNull(SourceFinderLogic.message(null))
        assertNull(SourceFinderLogic.message("idle"))
        assertNull(SourceFinderLogic.message("something_new"))
        assertEquals("Kaynak bulundu, yeniden deneniyor", SourceFinderLogic.MSG_FOUND)
        assertEquals(5_000L, SourceFinderLogic.POLL_INTERVAL_MS)
    }

    @Test
    fun awaitResult_pollsEveryFiveSeconds_untilFound() = runTest {
        val times = ArrayList<Long>()
        var calls = 0
        val result = SourceFinderLogic.awaitResult(poll = {
            times += currentTime
            calls++
            if (calls < 3) "searching" else "found"
        })
        assertEquals("found", result)
        assertEquals(listOf(5_000L, 10_000L, 15_000L), times)
    }

    @Test
    fun awaitResult_notFoundEndsPolling() = runTest {
        var calls = 0
        val result = SourceFinderLogic.awaitResult(poll = { calls++; if (calls == 2) "not_found" else "searching" })
        assertEquals("not_found", result)
        assertEquals(2, calls)
    }

    @Test
    fun awaitResult_transientErrorsDoNotStopPolling() = runTest {
        var calls = 0
        val result = SourceFinderLogic.awaitResult(poll = {
            calls++
            when (calls) {
                1 -> throw ApiException("network", "yok")
                2 -> null
                else -> "found"
            }
        })
        assertEquals("found", result)
        assertEquals(3, calls)
    }

    @Test
    fun awaitResult_givesUpAfterMaxPolls() = runTest {
        var calls = 0
        val result = SourceFinderLogic.awaitResult(poll = { calls++; "searching" }, maxPolls = 4)
        assertNull(result)
        assertEquals(4, calls)
        assertEquals(20_000L, currentTime)
    }

    @Test
    fun awaitResult_isCancellable() = runTest {
        var calls = 0
        val job = launch {
            SourceFinderLogic.awaitResult(poll = { calls++; "searching" })
        }
        advanceTimeBy(12_000)
        job.cancel()
        advanceTimeBy(60_000)
        assertEquals(2, calls)   // 5 sn ve 10 sn'de sorulmuştu; iptalden sonra yok
    }
}
