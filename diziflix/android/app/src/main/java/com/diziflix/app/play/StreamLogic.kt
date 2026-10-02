package com.diziflix.app.play

import androidx.media3.common.MimeTypes
import com.diziflix.app.data.model.Subtitle
import com.diziflix.app.data.model.VideoStream
import java.util.Locale

/** Akış sıralama / etiket işleme (saf Kotlin, birim testli). */
object StreamLogic {

    private val DIRECT_TYPES = setOf("hls", "mp4", "dash", "mpd", "progressive")
    private val DIRECT_EXTENSIONS = setOf("m3u8", "mp4", "mpd", "webm", "mkv", "m4v")

    /**
     * Embed (iframe/WebView) mi? tizen-client player.js isEmbedStream ile aynı mantık:
     * type == "embed" ise evet; bilinen video tipiyse hayır; aksi halde adres bir video dosyasına
     * (.m3u8/.mp4 ...) bitmiyorsa evet.
     */
    fun isEmbed(stream: VideoStream): Boolean {
        val type = stream.type.trim().lowercase(Locale.ROOT)
        if (type == "embed") return true
        if (type in DIRECT_TYPES) return false
        val clean = stream.url.substringBefore('#').substringBefore('?').lowercase(Locale.ROOT)
        val ext = clean.substringAfterLast('.', "")
        return ext !in DIRECT_EXTENSIONS
    }

    /**
     * ExoPlayer'a açıkça verilecek MIME (motor seçimi `type`'a göre, adres uzantısına DEĞİL: vekil adresi uzantısız olabilir,
     * doğrudan HLS adresi `.txt` ile bitebilir). hls -> application/x-mpegURL, mp4 -> video/mp4; diğer türlerde null (ExoPlayer çıkarsar).
     */
    fun mimeTypeFor(stream: VideoStream): String? = when (stream.type.trim().lowercase(Locale.ROOT)) {
        "hls" -> MimeTypes.APPLICATION_M3U8
        "mp4" -> MimeTypes.VIDEO_MP4
        else -> null
    }

    /**
     * Doğrulanabilir (video dosyası) akışlar önce, embed'ler son çare olarak sona; kendi içinde sunucu
     * sırası korunur. Adresi boş akışlar atılır.
     */
    fun order(streams: List<VideoStream>): List<VideoStream> = order(streams, qualityCap = 0)

    /**
     * [order] + "En yüksek kalite" sınırı (Tizen playflow `orderStreams`): [qualityCap] (yükseklik, 0 = sınırsız)
     * sınırını AŞAN doğrudan akışlar doğrudan akışların SONUNA (embed'lerin önüne) atılır. Yüksekliği çözülemeyen
     * akışa dokunulmaz.
     */
    fun order(streams: List<VideoStream>, qualityCap: Int): List<VideoStream> {
        val usable = streams.filter { it.url.isNotBlank() }
        val direct = ArrayList<VideoStream>()
        val over = ArrayList<VideoStream>()
        val embeds = ArrayList<VideoStream>()
        for (stream in usable) {
            when {
                isEmbed(stream) -> embeds.add(stream)
                qualityCap > 0 && streamHeight(stream) > qualityCap -> over.add(stream)
                else -> direct.add(stream)
            }
        }
        return direct + over + embeds
    }

    private val SIZE_PAIR = Regex("(\\d{3,4})\\s*[x×]\\s*(\\d{3,4})", RegexOption.IGNORE_CASE)
    private val HEIGHT_P = Regex("(?:^|[^0-9])(\\d{3,4})\\s*p(?![a-z])", RegexOption.IGNORE_CASE)
    private val UHD = Regex("(?:^|[^a-z0-9])(4k|uhd)(?![a-z0-9])", RegexOption.IGNORE_CASE)
    private val TWO_K = Regex("(?:^|[^a-z0-9])2k(?![a-z0-9])", RegexOption.IGNORE_CASE)

    /**
     * Akışın dikey çözünürlüğü (720, 1080, 1440...): önce `quality`, sonra `label`; "1920x1080", "1080p", "4K"/"2K".
     * Bilinmiyorsa 0 (tizen-client playflow.js `streamHeight`).
     */
    fun streamHeight(stream: VideoStream): Int {
        for (text in listOf(stream.quality, stream.label)) {
            SIZE_PAIR.find(text)?.let { return it.groupValues[2].toInt() }
            HEIGHT_P.find(text)?.let { return it.groupValues[1].toInt() }
            if (UHD.containsMatchIn(text)) return 2160
            if (TWO_K.containsMatchIn(text)) return 1440
        }
        return 0
    }

    /**
     * Kaynak/kalite menüsü etiketleri: sunucu `label`ı olduğu gibi (boşsa sağlayıcı · kalite, o da yoksa
     * "Kaynak N"); aynı etiket tekrar ederse ikincisine " (2)", üçüncüsüne " (3)" eklenir.
     */
    fun uniqueLabels(streams: List<VideoStream>): List<String> {
        val counts = HashMap<String, Int>()
        return streams.mapIndexed { index, stream ->
            val base = baseLabel(stream, index)
            val n = (counts[base] ?: 0) + 1
            counts[base] = n
            if (n == 1) base else "$base ($n)"
        }
    }

    private fun baseLabel(stream: VideoStream, index: Int): String {
        val label = stream.label.trim()
        if (label.isNotEmpty()) return label
        val parts = listOfNotNull(
            stream.provider?.trim()?.takeIf { it.isNotEmpty() },
            stream.quality.trim().takeIf { it.isNotEmpty() },
        )
        return if (parts.isEmpty()) "Kaynak ${index + 1}" else parts.joinToString(" · ")
    }

    /**
     * Bu akışa uygulanabilen soft altyazılar: adresi olanlar ve `stream_ids` null (tüm akışlar) ya da akışın
     * `variant_id`sini içerenler. Gömülü altyazılı (hard) akışın variant'ı hiçbir soft izde geçmez.
     */
    fun subtitlesFor(stream: VideoStream, subtitles: List<Subtitle>): List<Subtitle> =
        subtitles.filter { sub ->
            val ids = sub.streamIds
            sub.url.isNotBlank() && (ids == null || (stream.variantId != null && stream.variantId in ids))
        }

    /**
     * Varsayılan seçili gelecek altyazının [subtitles] içindeki indeksi: sunucunun `default` işaretlisi,
     * yoksa ilk Türkçe; ikisi de yoksa null (altyazı kapalı başlar).
     */
    fun defaultSubtitleIndex(subtitles: List<Subtitle>): Int? = defaultSubtitleIndex(subtitles, pref = null)

    /**
     * Profilin "Varsayılan altyazı dili" tercihiyle ([pref]: "tr" | "en" | "off"; null = tercih yok): "off" hiçbirini
     * seçmez; dil tercihi o dildeki ilk izi seçer; o dilde iz yoksa tercihsiz kural (sunucu varsayılanı > Türkçe) işler.
     */
    fun defaultSubtitleIndex(subtitles: List<Subtitle>, pref: String?): Int? {
        if (pref == "off") return null
        if (pref != null) {
            val wanted = subtitles.indexOfFirst { it.lang?.startsWith(pref, ignoreCase = true) == true }
            if (wanted >= 0) return wanted
        }
        val flagged = subtitles.indexOfFirst { it.isDefault }
        if (flagged >= 0) return flagged
        val turkish = subtitles.indexOfFirst { it.lang?.startsWith("tr", ignoreCase = true) == true }
        return if (turkish >= 0) turkish else null
    }

    /** Altyazı görüntüye gömülüyse kullanıcıya gösterilecek not ("Altyazı görüntüye gömülü (Türkçe)"), yoksa null. */
    fun hardSubNote(stream: VideoStream): String? {
        if (stream.subMode != "hard") return null
        val lang = TrackLabels.languageName(stream.hardLang)
        return if (lang == null) "Altyazı görüntüye gömülü (kapatılamaz)" else "Altyazı görüntüye gömülü: $lang (kapatılamaz)"
    }

    /** Embed sayfası için otomatik başlatma parametresi (yoksa eklenir). */
    fun withAutoplay(url: String): String {
        if (Regex("[?&]autoplay=").containsMatchIn(url)) return url
        return url + (if (url.contains('?')) "&" else "?") + "autoplay=1"
    }

    /** YouTube gömme adresi mi (Referer/origin için loadDataWithBaseURL gerekir). */
    fun isYouTubeEmbed(url: String): Boolean {
        val host = url.substringAfter("://", "").substringBefore('/').lowercase(Locale.ROOT)
        return host == "youtube.com" || host.endsWith(".youtube.com") || host == "youtube-nocookie.com" ||
            host.endsWith(".youtube-nocookie.com")
    }
}

/**
 * ExoPlayer PlaybackException.errorCode -> sunucunun /api/playback-report `code` alanı.
 * Sunucu yalnızca şu değerleri kabul eder: "", network, timeout, playback_failed, unsupported, decode,
 * autoplay, aborted, offline (başka değer 422 döner). unsupported/decode/autoplay/aborted/offline
 * cihaz kaynaklı sayılır: kaynağı "bozuk" işaretlemez.
 *
 * Kodlar androidx.media3.common.PlaybackException sabitlerinin sayısal değerleridir (sabit, belgelenmiş):
 *   1003 TIMEOUT · 2000-2999 IO (2002 bağlantı zaman aşımı) · 3003/3004 desteklenmeyen kapsayıcı/manifest ·
 *   4001-4003 kod çözücü başlatma/sorgu/çözme · 4004/4005 biçim yetenek dışı/desteklenmiyor.
 */
object PlaybackErrors {
    /** `/api/playback-report` `detail` alanı üst sınırı. */
    const val MAX_DETAIL = 120

    /** Boşlukları sadeleştirir, en çok [MAX_DETAIL] karaktere keser; null/boş -> "". */
    fun clipDetail(raw: String?): String {
        val t = raw.orEmpty().replace(Regex("\\s+"), " ").trim()
        return if (t.length > MAX_DETAIL) t.substring(0, MAX_DETAIL) else t
    }

    /** ExoPlayer hata ayrıntısı: "exo:<errorCodeName>[/<neden sınıfı>][/http<kod>]". */
    fun exoDetail(errorCodeName: String?, causeName: String? = null, httpStatus: Int = 0): String {
        val sb = StringBuilder("exo:").append(errorCodeName?.ifBlank { null } ?: "?")
        if (!causeName.isNullOrBlank()) sb.append('/').append(causeName)
        if (httpStatus > 0) sb.append("/http").append(httpStatus)
        return clipDetail(sb.toString())
    }

    fun toReportCode(errorCode: Int): String = when {
        errorCode == 1003 || errorCode == 2002 -> "timeout"
        errorCode in 2000..2999 -> "network"
        errorCode == 3003 || errorCode == 3004 -> "unsupported"
        errorCode == 4004 || errorCode == 4005 -> "unsupported"
        errorCode in 4001..4003 -> "decode"
        else -> "playback_failed"
    }

    fun userMessage(code: String): String = when (code) {
        "timeout" -> "Video zamanında başlamadı"
        "network" -> "Video kaynağına bağlanılamadı"
        "unsupported" -> "Bu cihaz video biçimini desteklemiyor"
        "decode" -> "Video çözülemedi"
        "offline" -> "Bağlantı yok. İnternet bağlantınızı kontrol edin."
        else -> "Video oynatılamadı"
    }
}
