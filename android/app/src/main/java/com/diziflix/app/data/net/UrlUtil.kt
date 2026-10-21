package com.diziflix.app.data.net

/** Saf (Android bağımsız) adres yardımcıları; birim testlidir. */
object UrlUtil {
    const val DEFAULT_BASE_URL = "http://192.168.0.61:8090"

    /**
     * /img izinli boyutları (server/app/images.py SIZES; whitelist dışını sunucu en yakına yuvarlar, aynı boyut =
     * aynı URL = önbellek). EN KÜÇÜK yeterli boyut seçilir: kart 112-160dp, yani ~140-200px (200x300 yeter).
     * Coil'e de aynı boyut verilir (tam hedef boyut), bellek/çözme maliyeti küçük kalır.
     */
    const val POSTER_W = 200
    const val POSTER_H = 300
    const val STILL_W = 320
    const val STILL_H = 180
    const val SEASON_POSTER_W = 200
    const val SEASON_POSTER_H = 300
    const val AVATAR = 200

    /** backdrop izinli boyutları (16:9). */
    const val BACKDROP_SMALL_W = 780
    const val BACKDROP_SMALL_H = 439
    const val BACKDROP_LARGE_W = 1280
    const val BACKDROP_LARGE_H = 720

    /** Ekran genişliği bu pikselin altındaysa küçük backdrop yeter (800px tablet: 780x439). */
    private const val SMALL_BACKDROP_MAX_SCREEN_PX = 820

    /**
     * Hero/detay artalanı için ekran piksel genişliğine uygun (w, h): dar ekranda 780x439, geniş ekranda
     * 1280x720 (1920x1080 bu kullanım için gereksiz ağır).
     */
    fun backdropSizeFor(screenWidthPx: Int): Pair<Int, Int> =
        if (screenWidthPx <= SMALL_BACKDROP_MAX_SCREEN_PX) {
            BACKDROP_SMALL_W to BACKDROP_SMALL_H
        } else {
            BACKDROP_LARGE_W to BACKDROP_LARGE_H
        }

    private val SCHEME = Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://")

    /**
     * Kullanıcı girişini sunucu taban adresine çevirir: boşlukları/sondaki eğik çizgileri atar, şema
     * yoksa http:// ekler. Geçersizse (boş, http/https dışı şema) null.
     */
    fun normalizeBaseUrl(input: String?): String? {
        val trimmed = (input ?: "").trim()
        if (trimmed.isEmpty()) return null
        val withScheme = if (SCHEME.containsMatchIn(trimmed)) trimmed else "http://$trimmed"
        val schemeEnd = withScheme.indexOf("://") + 3
        val scheme = withScheme.substring(0, schemeEnd).lowercase()
        if (scheme != "http://" && scheme != "https://") return null
        val rest = withScheme.substring(schemeEnd).trimEnd('/')
        if (rest.isBlank() || rest.startsWith("/") || rest.any { it.isWhitespace() }) return null
        return scheme + rest
    }

    /** Sunucu yolu ise taban adres eklenir; mutlak ya da data: adresi aynen kalır. */
    fun absolute(base: String, path: String?): String {
        if (path.isNullOrBlank()) return ""
        if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("//") || path.startsWith("data:")) {
            return path
        }
        val b = base.trimEnd('/')
        return if (path.startsWith("/")) b + path else "$b/$path"
    }

    /** Yolda w= ve h= zaten varsa aynen bırakır, yoksa ekler. */
    fun sized(base: String, path: String?, w: Int, h: Int): String {
        val url = absolute(base, path)
        if (url.isEmpty()) return ""
        val q = url.indexOf('?')
        if (q >= 0) {
            val params = url.substring(q + 1).split('&')
            val hasW = params.any { it.startsWith("w=") }
            val hasH = params.any { it.startsWith("h=") }
            if (hasW && hasH) return url
            return "$url&w=$w&h=$h"
        }
        return "$url?w=$w&h=$h"
    }

    /** Boyutu ZORLA ayarlar (mevcut w/h atılır, diğer sorgu parametreleri korunur). */
    fun sizedTo(base: String, path: String?, w: Int, h: Int): String {
        val url = absolute(base, path)
        if (url.isEmpty()) return ""
        val q = url.indexOf('?')
        val root = if (q >= 0) url.substring(0, q) else url
        val kept = if (q >= 0) {
            url.substring(q + 1).split('&').filter { it.isNotEmpty() && !it.startsWith("w=") && !it.startsWith("h=") }
        } else {
            emptyList()
        }
        return root + "?" + (kept + listOf("w=$w", "h=$h")).joinToString("&")
    }
}
