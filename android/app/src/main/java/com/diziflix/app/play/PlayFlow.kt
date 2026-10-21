package com.diziflix.app.play

import com.diziflix.app.data.model.VideoStream
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/*
 * "Oynat" akışı (tizen-client/js/playflow.js ile aynı mantık, saf Kotlin, oynatıcıdan bağımsız):
 *  - akışlar sırayla denenir: doğrulanabilir (hls/mp4) önce, embed'ler son çare
 *  - bir akış "gerçekten oynamaya başladığında" başarı sayılır ve akış biter (oynatıcı ekranına geçilir)
 *  - hata (prepare hatası / onerror / zaman aşımı) -> o akışın attempt_token'ı ile failure raporu, sıradaki akış
 *  - aynı token+olay yalnızca bir kez raporlanır (bir kaynağın tüm akışları tek token paylaşır)
 *  - offline / aborted hatasında dur; hepsi tükenirse Failed
 *  - embed akış doğrulanamaz: rapor yazılmadan WebView'a devredilir
 */

/** Oynatıcı soyutlaması: ExoPlayer gerçeklemesi [ExoStreamProbe]; testlerde sahte gerçekleme. */
interface StreamProbe {
    /**
     * [stream]'i açar ve gerçekten oynamaya başlayana ya da hata verene kadar bekler.
     * Zaman aşımını çağıran ([PlayFlow]) uygular; iptalde temizlik yapılmalıdır.
     */
    suspend fun attempt(stream: VideoStream, resumeMs: Long): ProbeResult

    /** Denemeyi/oynatıcıyı bırak (başarısız ya da iptal edilen deneme sonrası). */
    fun abort()
}

sealed interface ProbeResult {
    /** Oynatma gerçekten başladı. */
    data object Started : ProbeResult

    /** [code]: sunucu rapor kodu (bkz. PlaybackErrors); [detail]: hata ayrıntısı (`/api/playback-report` `detail`, <=120 karakter). */
    data class Failed(val code: String, val message: String, val detail: String = "") : ProbeResult
}

/** Sağlık raporu alıcısı (gerçeklemesi /api/playback-report'a fire-and-forget POST yapar). */
fun interface PlaybackReporter {
    /** [detail] yalnızca "failure" olaylarında doludur (<=120 karakter); sunucuda alan yoksa zararsız. */
    fun report(attemptToken: String, event: String, code: String, engine: String, detail: String)
}

enum class PlayStage { Searching, Preparing, TryingOther, SourceFound }

enum class FailReason { NoStreams, Offline, Aborted, Exhausted }

sealed interface PlayOutcome {
    /** [index] sıralanmış akış listesindeki konumdur ([PlayFlow.streams]). */
    data class Playing(val index: Int, val stream: VideoStream) : PlayOutcome

    /** WebView yedeği: akış doğrulanamaz, rapor yazılmadı. */
    data class Embed(val index: Int, val stream: VideoStream) : PlayOutcome

    data class Failed(val message: String, val reason: FailReason) : PlayOutcome
}

data class PlayFlowConfig(
    /** Bir akışın oynamaya başlaması için azami süre. */
    val streamTimeoutMs: Long = 15_000L,
    /** Deneme bu kadar sürerse "Başka kaynak deneniyor…" aşaması gösterilir. */
    val slowMs: Long = 8_000L,
    val maxTries: Int = 6,
    /** Sunucunun kabul ettiği engine değerlerinden biri: "", avplay, html5, embed. Yerel oynatıcı için html5. */
    val engineName: String = "html5",
)

class PlayFlow(
    private val probe: StreamProbe,
    private val reporter: PlaybackReporter,
    private val isOnline: () -> Boolean = { true },
    private val config: PlayFlowConfig = PlayFlowConfig(),
    private val onStage: (PlayStage) -> Unit = {},
) {
    companion object {
        const val MSG_NO_STREAMS = "Bu içerik için kullanılabilir video kaynağı bulunamadı."
        const val MSG_OFFLINE = "Bağlantı yok. İnternet bağlantınızı kontrol edin."
        const val MSG_EXHAUSTED = "Bu içerik şu anda oynatılamıyor. Biraz sonra tekrar deneyebilirsiniz."
    }

    /** Sıralanmış (doğrudan akışlar önce) liste; indeksler PlayOutcome'da bu listeye göredir. */
    var streams: List<VideoStream> = emptyList()
        private set

    private val tried = HashSet<Int>()
    private val reported = HashSet<String>()

    /**
     * İlk oynatma: akışlar sıralanır, deneme durumu sıfırlanır. [qualityCap] "En yüksek kalite" sınırıdır
     * (yükseklik, 0 = sınırsız): sınırı aşan doğrudan akışlar doğrudan akışların sonuna gider.
     */
    suspend fun start(all: List<VideoStream>, resumeMs: Long, qualityCap: Int = 0): PlayOutcome {
        streams = StreamLogic.order(all, qualityCap)
        tried.clear()
        reported.clear()
        if (streams.isEmpty()) return PlayOutcome.Failed(MSG_NO_STREAMS, FailReason.NoStreams)
        return attemptLoop(resumeMs, forced = null, allowEmbed = true)
    }

    /**
     * Kullanıcı kaynak/kalite menüsünden [index]'i seçti: önce o denenir (daha önce denenmiş olsa da),
     * başarısızsa denenmemiş akışlara geçilir.
     */
    suspend fun select(index: Int, resumeMs: Long): PlayOutcome {
        if (index !in streams.indices) return PlayOutcome.Failed(MSG_EXHAUSTED, FailReason.Exhausted)
        return attemptLoop(resumeMs, forced = index, allowEmbed = true)
    }

    /**
     * Oynarken akış koptu: o akışın hatası raporlanır, denenmemiş sonraki doğrudan akış konumdan devam
     * ettirilir (embed'e otomatik geçilmez).
     */
    suspend fun recover(failedIndex: Int, code: String, message: String, resumeMs: Long, detail: String = ""): PlayOutcome {
        val effective = if (!isOnline()) "offline" else code.ifBlank { "playback_failed" }
        report(failedIndex, "failure", effective, detail)
        if (effective == "offline") return PlayOutcome.Failed(MSG_OFFLINE, FailReason.Offline)
        if (effective == "aborted") return PlayOutcome.Failed(message, FailReason.Aborted)
        return attemptLoop(resumeMs, forced = null, allowEmbed = false)
    }

    /** Elle "Sorun bildir" (embed'de başarı/hata gözlenemediği için). */
    fun reportFailure(index: Int, code: String, engine: String, detail: String = "") {
        reportInternal(index, "failure", code, engine, detail)
    }

    private suspend fun attemptLoop(resumeMs: Long, forced: Int?, allowEmbed: Boolean): PlayOutcome {
        var tries = 0
        var lastMessage = ""
        var forcedIndex = forced
        while (true) {
            val index = forcedIndex ?: nextUntried(allowEmbed)
            forcedIndex = null
            if (tries >= config.maxTries || index < 0) {
                return PlayOutcome.Failed(lastMessage.ifBlank { MSG_EXHAUSTED }, FailReason.Exhausted)
            }
            tries++
            tried.add(index)
            val stream = streams[index]
            onStage(if (tries > 1) PlayStage.TryingOther else PlayStage.Preparing)

            // iframe/embed: başlama/hata gözlenemez -> rapor yazmadan devret
            if (StreamLogic.isEmbed(stream)) return PlayOutcome.Embed(index, stream)

            when (val result = tryOne(stream, resumeMs)) {
                is ProbeResult.Started -> {
                    report(index, "success", "")
                    return PlayOutcome.Playing(index, stream)
                }
                is ProbeResult.Failed -> {
                    val code = if (!isOnline()) "offline" else result.code.ifBlank { "playback_failed" }
                    report(index, "failure", code, result.detail)
                    if (code == "offline") return PlayOutcome.Failed(MSG_OFFLINE, FailReason.Offline)
                    if (code == "aborted") return PlayOutcome.Failed(result.message, FailReason.Aborted)
                    lastMessage = result.message
                }
            }
        }
    }

    private fun nextUntried(allowEmbed: Boolean): Int {
        for (i in streams.indices) {
            if (i in tried) continue
            if (!allowEmbed && StreamLogic.isEmbed(streams[i])) continue
            return i
        }
        return -1
    }

    private suspend fun tryOne(stream: VideoStream, resumeMs: Long): ProbeResult {
        val result: ProbeResult = try {
            coroutineScope {
                val slow = launch {
                    delay(config.slowMs)
                    onStage(PlayStage.TryingOther)
                }
                try {
                    withTimeoutOrNull(config.streamTimeoutMs) { probe.attempt(stream, resumeMs) }
                        ?: ProbeResult.Failed("timeout", "Video başlamadı", "start-timeout")
                } finally {
                    slow.cancel()
                }
            }
        } catch (e: CancellationException) {
            probe.abort()
            throw e
        }
        if (result is ProbeResult.Failed) probe.abort()
        return result
    }

    private fun report(index: Int, event: String, code: String, detail: String = "") {
        reportInternal(index, event, code, config.engineName, detail)
    }

    /** Her akışın kendi belirteci; aynı belirteç+olay yalnızca bir kez raporlanır. Ayrıntı yalnızca failure'da, en çok 120 karakter. */
    private fun reportInternal(index: Int, event: String, code: String, engine: String, detail: String = "") {
        val token = streams.getOrNull(index)?.attemptToken
        if (token.isNullOrBlank()) return
        if (!reported.add("$token:$event")) return
        reporter.report(token, event, code, engine, if (event == "failure") PlaybackErrors.clipDetail(detail) else "")
    }
}
