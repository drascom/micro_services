package com.diziflix.app.play

import android.content.Context
import android.net.Uri
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.diziflix.app.data.model.Subtitle
import com.diziflix.app.data.model.VideoStream
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.Locale
import kotlin.coroutines.resume

/** ExoPlayer örneği fabrikası. */
@OptIn(UnstableApi::class)
object PlayerFactory {
    /** Bazı CDN'ler "ExoPlayerLib" kullanıcı aracısını reddeder; tarayıcı benzeri bir UA kullanılır. */
    const val USER_AGENT =
        "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"

    fun create(context: Context): ExoPlayer {
        val dataSourceFactory = DefaultHttpDataSource.Factory()
            .setUserAgent(USER_AGENT)
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(15_000)
        val audio = AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA)
            .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
            .build()
        return ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
            .setAudioAttributes(audio, true)
            .setHandleAudioBecomingNoisy(true)
            .build()
    }
}

/** `/api/playback-report` `detail`: "exo:<errorCodeName>[/<neden>][/http<kod>]" (<=120 karakter). */
@OptIn(UnstableApi::class)
fun PlaybackException.reportDetail(): String {
    val c = cause
    val http = (c as? HttpDataSource.InvalidResponseCodeException)?.responseCode ?: 0
    return PlaybackErrors.exoDetail(errorCodeName, c?.javaClass?.simpleName, http)
}

/**
 * [StreamProbe]'un ExoPlayer gerçeklemesi. Aynı ExoPlayer örneğine akışı açar; akış GERÇEKTEN oynamaya
 * başlayınca (onIsPlayingChanged(true)) Started, hata olursa Failed döner. Başarılı denemede oynatıcı
 * olduğu gibi kalır: ekran aynı örneği devralır (TV'deki "hazır motoru devret" mantığı).
 * Tüm çağrılar ana iş parçacığında yapılmalıdır (ExoPlayer uygulama looper'ı).
 */
class ExoStreamProbe(private val player: ExoPlayer) : StreamProbe {

    /** Sunucunun `subtitles[]` alanı (mutlak adresli); akışa uygulanabilenler harici altyazı olarak eklenir. */
    @Volatile
    var subtitles: List<Subtitle> = emptyList()

    /** Profilin "Varsayılan altyazı dili" tercihi ("tr" | "en" | "off"); null = tercih yok (eski kural). */
    @Volatile
    var subtitlePref: String? = null

    override suspend fun attempt(stream: VideoStream, resumeMs: Long): ProbeResult =
        suspendCancellableCoroutine { continuation ->
            val listener = object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    if (isPlaying) finish(this, ProbeResult.Started)
                }

                override fun onPlayerError(error: PlaybackException) {
                    val code = PlaybackErrors.toReportCode(error.errorCode)
                    finish(this, ProbeResult.Failed(code, PlaybackErrors.userMessage(code), error.reportDetail()))
                }

                private fun finish(self: Player.Listener, result: ProbeResult) {
                    player.removeListener(self)
                    if (continuation.isActive) continuation.resume(result)
                }
            }
            continuation.invokeOnCancellation { player.removeListener(listener) }

            player.stop()
            player.clearMediaItems()
            player.playWhenReady = true
            val item = buildMediaItem(stream)
            if (resumeMs > 0) player.setMediaItem(item, resumeMs) else player.setMediaItem(item)
            player.addListener(listener)
            player.prepare()
        }

    override fun abort() {
        player.stop()
        player.clearMediaItems()
    }

    private fun buildMediaItem(stream: VideoStream): MediaItem {
        val builder = MediaItem.Builder().setUri(Uri.parse(stream.url))
        // Tür ipucu: motor `type`'a göre seçilir (adres uzantısına güvenme: vekil `.../index.m3u8`, doğrudan adres `.txt` olabilir).
        StreamLogic.mimeTypeFor(stream)?.let { builder.setMimeType(it) }
        // Yalnızca bu akışın dosyasına (variant_id) uygulanabilen soft altyazılar; gömülü altyazılı dosyada yoktur.
        val applicable = StreamLogic.subtitlesFor(stream, subtitles)
        val defaultIndex = StreamLogic.defaultSubtitleIndex(applicable, subtitlePref)
        val configs = applicable.mapIndexedNotNull { index, sub -> toSubtitleConfiguration(sub, index == defaultIndex) }
        if (configs.isNotEmpty()) builder.setSubtitleConfigurations(configs)
        return builder.build()
    }

    private fun toSubtitleConfiguration(sub: Subtitle, isDefault: Boolean): MediaItem.SubtitleConfiguration? {
        val url = sub.url.trim()
        if (url.isEmpty()) return null
        val isSrt = sub.format.equals("srt", ignoreCase = true) ||
            url.substringBefore('?').lowercase(Locale.ROOT).endsWith(".srt")
        val mime = if (isSrt) "application/x-subrip" else "text/vtt"
        val builder = MediaItem.SubtitleConfiguration.Builder(Uri.parse(url))
            .setMimeType(mime)
            .setLanguage(sub.lang?.ifBlank { null })
            .setLabel(sub.label.ifBlank { null })
        // Sunucunun varsayılanı (yoksa ilk Türkçe) seçili gelir.
        if (isDefault) builder.setSelectionFlags(C.SELECTION_FLAG_DEFAULT)
        return builder.build()
    }
}
