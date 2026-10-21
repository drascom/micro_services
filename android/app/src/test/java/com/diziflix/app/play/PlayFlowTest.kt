package com.diziflix.app.play

import com.diziflix.app.data.model.VideoStream
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Sahte oynatıcı arayüzüyle akış deneme mantığı testleri (TV playflow.js davranışı). */
class PlayFlowTest {

    private class FakeProbe(private val behave: suspend (VideoStream, Long) -> ProbeResult) : StreamProbe {
        val attempts = ArrayList<String>()
        val resumes = ArrayList<Long>()
        var aborts = 0

        override suspend fun attempt(stream: VideoStream, resumeMs: Long): ProbeResult {
            attempts.add(stream.url)
            resumes.add(resumeMs)
            return behave(stream, resumeMs)
        }

        override fun abort() {
            aborts++
        }
    }

    private data class Report(val token: String, val event: String, val code: String, val engine: String, val detail: String = "")

    private class RecordingReporter : PlaybackReporter {
        val reports = ArrayList<Report>()
        override fun report(attemptToken: String, event: String, code: String, engine: String, detail: String) {
            reports.add(Report(attemptToken, event, code, engine, detail))
        }
    }

    private fun token(n: Int): String = "token$n".padEnd(32, '0')

    private fun hls(n: Int, tokenValue: String? = token(n)) = VideoStream(
        url = "https://cdn.example/$n/master.m3u8",
        type = "hls",
        quality = "auto",
        label = "Kaynak $n",
        attemptToken = tokenValue,
    )

    private fun embed(n: Int) = VideoStream(
        url = "https://www.youtube.com/embed/id$n",
        type = "embed",
        label = "Fragman $n",
        attemptToken = token(100 + n),
    )

    private fun url(n: Int) = "https://cdn.example/$n/master.m3u8"

    private fun fail(code: String = "network", message: String = "hata: $code", detail: String = "") =
        ProbeResult.Failed(code, message, detail)

    @Test
    fun firstStreamPlays_reportsSuccessAndStops() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(listOf(hls(1), hls(2)), resumeMs = 12_000L)

        assertEquals(PlayOutcome.Playing(0, hls(1)), outcome)
        assertEquals(listOf(url(1)), probe.attempts)
        assertEquals(listOf(12_000L), probe.resumes)
        assertEquals(listOf(Report(token(1), "success", "", "html5")), reporter.reports)
        assertEquals(0, probe.aborts)
    }

    @Test
    fun failureThenSuccess_reportsEachStreamWithItsOwnToken() = runTest {
        val probe = FakeProbe { stream, _ -> if (stream.url == url(1)) fail("network") else ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(PlayOutcome.Playing(1, hls(2)), outcome)
        assertEquals(listOf(url(1), url(2)), probe.attempts)
        assertEquals(
            listOf(
                Report(token(1), "failure", "network", "html5"),
                Report(token(2), "success", "", "html5"),
            ),
            reporter.reports,
        )
        assertEquals(1, probe.aborts) // başarısız deneme sonrası oynatıcı bırakıldı
    }

    @Test
    fun sharedToken_failureThenSuccessAreBothSent_butRepeatedFailureIsNot() = runTest {
        // Sunucu bir kaynağın tüm akışlarına tek attempt_token verir (streams_episode.json'daki gibi).
        val shared = token(9)
        val streams = listOf(hls(1, shared), hls(2, shared), hls(3, shared))
        val probe = FakeProbe { stream, _ -> if (stream.url == url(3)) ProbeResult.Started else fail("timeout") }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(streams, 0L)

        assertTrue(outcome is PlayOutcome.Playing)
        assertEquals(2, (outcome as PlayOutcome.Playing).index) // 3. akış = indeks 2
        // ikinci failure aynı token+olay: tekrar yazılmaz; success ayrı olay: yazılır
        assertEquals(
            listOf(
                Report(shared, "failure", "timeout", "html5"),
                Report(shared, "success", "", "html5"),
            ),
            reporter.reports,
        )
    }

    @Test
    fun allStreamsFail_returnsExhaustedWithLastMessage() = runTest {
        val probe = FakeProbe { stream, _ ->
            if (stream.url == url(1)) fail("network", "birinci") else fail("decode", "ikinci")
        }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(PlayOutcome.Failed("ikinci", FailReason.Exhausted), outcome)
        assertEquals(listOf("failure", "failure"), reporter.reports.map { it.event })
        assertEquals(listOf("network", "decode"), reporter.reports.map { it.code })
    }

    @Test
    fun emptyStreamList_isNoStreams() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val flow = PlayFlow(probe, RecordingReporter())

        val outcome = flow.start(emptyList(), 0L)

        assertEquals(PlayOutcome.Failed(PlayFlow.MSG_NO_STREAMS, FailReason.NoStreams), outcome)
        assertTrue(probe.attempts.isEmpty())
    }

    @Test
    fun embedStreamsAreTriedLast_andHandedOverWithoutAnyReport() = runTest {
        val probe = FakeProbe { _, _ -> fail("network") }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        // sunucu sırası: önce embed, sonra hls; akış motoru doğrulanabilir olanı öne alır
        val outcome = flow.start(listOf(embed(1), hls(1)), 0L)

        assertEquals(PlayOutcome.Embed(1, embed(1)), outcome)
        assertEquals(listOf(url(1)), probe.attempts) // embed için oynatıcı denenmedi
        assertEquals(listOf(Report(token(1), "failure", "network", "html5")), reporter.reports)
        assertEquals(listOf(url(1), embed(1).url), flow.streams.map { it.url })
    }

    @Test
    fun onlyEmbed_isHandedOverImmediately() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(listOf(embed(1)), 0L)

        assertEquals(PlayOutcome.Embed(0, embed(1)), outcome)
        assertTrue(probe.attempts.isEmpty())
        assertTrue(reporter.reports.isEmpty())
    }

    @Test
    fun timeout_isReportedAsTimeoutAndNextStreamIsTried() = runTest {
        val probe = FakeProbe { stream, _ ->
            if (stream.url == url(1)) {
                delay(60_000L) // hiç oynamaya başlamıyor
                ProbeResult.Started
            } else {
                ProbeResult.Started
            }
        }
        val reporter = RecordingReporter()
        val stages = ArrayList<PlayStage>()
        val flow = PlayFlow(
            probe, reporter,
            config = PlayFlowConfig(streamTimeoutMs = 1_000L, slowMs = 400L),
            onStage = { stages.add(it) },
        )

        val outcome = flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(PlayOutcome.Playing(1, hls(2)), outcome)
        assertEquals(Report(token(1), "failure", "timeout", "html5", "start-timeout"), reporter.reports[0])
        assertEquals(Report(token(2), "success", "", "html5"), reporter.reports[1])
        assertTrue(probe.aborts >= 1)
        // ilk aşama "hazırlanıyor"; yavaş zamanlayıcı + ikinci deneme "başka kaynak deneniyor"
        assertEquals(PlayStage.Preparing, stages.first())
        assertEquals(2, stages.count { it == PlayStage.TryingOther })
    }

    @Test
    fun offline_stopsImmediatelyWithoutTryingOtherStreams() = runTest {
        val probe = FakeProbe { _, _ -> fail("network") }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter, isOnline = { false })

        val outcome = flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(PlayOutcome.Failed(PlayFlow.MSG_OFFLINE, FailReason.Offline), outcome)
        assertEquals(listOf(url(1)), probe.attempts)
        assertEquals(listOf(Report(token(1), "failure", "offline", "html5")), reporter.reports)
    }

    @Test
    fun abortedError_stopsWithoutFallingThrough() = runTest {
        val probe = FakeProbe { _, _ -> fail("aborted", "iptal") }
        val flow = PlayFlow(probe, RecordingReporter())

        val outcome = flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(PlayOutcome.Failed("iptal", FailReason.Aborted), outcome)
        assertEquals(listOf(url(1)), probe.attempts)
    }

    @Test
    fun maxTries_limitsTheNumberOfAttempts() = runTest {
        val probe = FakeProbe { _, _ -> fail("network", "olmadı") }
        val flow = PlayFlow(probe, RecordingReporter(), config = PlayFlowConfig(maxTries = 2))

        val outcome = flow.start(listOf(hls(1), hls(2), hls(3)), 0L)

        assertEquals(PlayOutcome.Failed("olmadı", FailReason.Exhausted), outcome)
        assertEquals(2, probe.attempts.size)
    }

    @Test
    fun streamWithoutAttemptToken_playsButIsNotReported() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val outcome = flow.start(listOf(hls(1, tokenValue = null)), 0L)

        assertTrue(outcome is PlayOutcome.Playing)
        assertTrue(reporter.reports.isEmpty())
    }

    @Test
    fun select_triesChosenStreamFirstThenFallsBackToUntried() = runTest {
        val probe = FakeProbe { stream, _ ->
            when (stream.url) {
                url(3) -> fail("network") // kullanıcının seçtiği akış çalışmıyor
                else -> ProbeResult.Started
            }
        }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)
        assertEquals(PlayOutcome.Playing(0, hls(1)), flow.start(listOf(hls(1), hls(2), hls(3)), 0L))

        val outcome = flow.select(2, resumeMs = 5_000L)

        // seçilen (indeks 2) başarısız -> denenmemiş indeks 1'e düşer, konum korunur
        assertEquals(PlayOutcome.Playing(1, hls(2)), outcome)
        assertEquals(listOf(url(1), url(3), url(2)), probe.attempts)
        assertEquals(listOf(0L, 5_000L, 5_000L), probe.resumes)
        assertEquals(
            listOf(
                Report(token(1), "success", "", "html5"),
                Report(token(3), "failure", "network", "html5"),
                Report(token(2), "success", "", "html5"),
            ),
            reporter.reports,
        )
    }

    @Test
    fun recover_reportsFailure_skipsEmbedAndGivesUpWhenNothingLeft() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)
        assertEquals(
            PlayOutcome.Playing(0, hls(1)),
            flow.start(listOf(hls(1), embed(1), hls(2)), 0L), // sıralı: hls1, hls2, embed1
        )

        val second = flow.recover(0, "network", "koptu", resumeMs = 7_000L)
        assertEquals(PlayOutcome.Playing(1, hls(2)), second)
        assertEquals(7_000L, probe.resumes.last())

        val third = flow.recover(1, "decode", "çözülemedi", resumeMs = 9_000L)
        // kalan tek akış embed: oynarken otomatik embed'e geçilmez
        assertEquals(PlayOutcome.Failed(PlayFlow.MSG_EXHAUSTED, FailReason.Exhausted), third)
        assertEquals(listOf(url(1), url(2)), probe.attempts)
        assertEquals(
            listOf("success", "failure", "success", "failure"),
            reporter.reports.map { it.event },
        )
    }

    @Test
    fun recover_whileOffline_reportsOfflineAndStops() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        var online = true
        val flow = PlayFlow(probe, reporter, isOnline = { online })
        flow.start(listOf(hls(1), hls(2)), 0L)
        online = false

        val outcome = flow.recover(0, "network", "koptu", 1_000L)

        assertEquals(PlayOutcome.Failed(PlayFlow.MSG_OFFLINE, FailReason.Offline), outcome)
        assertEquals("offline", reporter.reports.last().code)
        assertEquals(1, probe.attempts.size)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun cancellation_abortsProbeAndReportsNothing() = runTest {
        val probe = FakeProbe { _, _ -> awaitCancellation() }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        val job = launch { flow.start(listOf(hls(1), hls(2)), 0L) }
        runCurrent()
        assertEquals(listOf(url(1)), probe.attempts)

        job.cancelAndJoin()

        assertEquals(1, probe.aborts)
        assertTrue(reporter.reports.isEmpty())
        assertEquals(1, probe.attempts.size) // iptal sonrası sıradaki akış denenmedi
    }

    @Test
    fun manualFailureReport_usesGivenEngine_andIsDeduplicated() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)
        flow.start(listOf(embed(1)), 0L)

        flow.reportFailure(0, "playback_failed", "embed")
        flow.reportFailure(0, "playback_failed", "embed")

        assertEquals(listOf(Report(token(101), "failure", "playback_failed", "embed")), reporter.reports)
    }

    // ---------------------------------------------------------------- hata ayrıntısı (detail)

    @Test
    fun failureDetail_isForwardedToTheReport_successHasNone() = runTest {
        val probe = FakeProbe { stream, _ ->
            if (stream.url == url(1)) fail("network", detail = "exo:ERROR_CODE_IO_BAD_HTTP_STATUS/InvalidResponseCodeException/http403")
            else ProbeResult.Started
        }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        flow.start(listOf(hls(1), hls(2)), 0L)

        assertEquals(
            listOf(
                Report(token(1), "failure", "network", "html5", "exo:ERROR_CODE_IO_BAD_HTTP_STATUS/InvalidResponseCodeException/http403"),
                Report(token(2), "success", "", "html5", ""),
            ),
            reporter.reports,
        )
    }

    @Test
    fun failureDetail_isClippedTo120Characters() = runTest {
        val probe = FakeProbe { _, _ -> fail("playback_failed", detail = "x".repeat(400)) }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)

        flow.start(listOf(hls(1)), 0L)

        assertEquals(120, reporter.reports.single().detail.length)
    }

    @Test
    fun recoverAndManualReport_carryTheirDetail() = runTest {
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val reporter = RecordingReporter()
        val flow = PlayFlow(probe, reporter)
        flow.start(listOf(hls(1), hls(2), embed(1)), 0L)

        flow.recover(0, "decode", "çözülemedi", 5_000L, detail = "exo:ERROR_CODE_DECODING_FAILED")
        flow.reportFailure(2, "playback_failed", "embed", "user-report")

        assertEquals(Report(token(1), "failure", "decode", "html5", "exo:ERROR_CODE_DECODING_FAILED"), reporter.reports[1])
        assertEquals(Report(token(101), "failure", "playback_failed", "embed", "user-report"), reporter.reports.last())
    }

    @Test
    fun hlsWithTxtExtensionAndExtensionlessMp4_areDirectStreams_notEmbeds() = runTest {
        val txtHls = VideoStream(url = "https://cdn.example/play/master.txt", type = "hls", attemptToken = token(7))
        val proxyMp4 = VideoStream(url = "https://tv.example/api/stream-proxy/abc123", type = "mp4", attemptToken = token(8))
        val probe = FakeProbe { _, _ -> ProbeResult.Started }
        val flow = PlayFlow(probe, RecordingReporter())

        val outcome = flow.start(listOf(embed(1), txtHls, proxyMp4), 0L)

        assertEquals(PlayOutcome.Playing(0, txtHls), outcome)
        assertEquals(listOf("hls", "mp4", "embed"), flow.streams.map { it.type })
    }
}
