package com.diziflix.app.domain

/**
 * Oynatma tercihleri (Tizen Ayarlar: "Varsayılan altyazı dili" + "En yüksek kalite"). Saf mantık; birim testli.
 * Kalite tercihi cihaz geneli, altyazı tercihi profil başınadır. Tercih hiç seçilmediyse (null) tablet/telefonda
 * davranış öncekiyle AYNI kalır (kalite sınırı yok, altyazı kuralı sunucu varsayılanı > Türkçe); TV'de Tizen gibi
 * varsayılan kalite sınırı 1080p'dir.
 */
object PlayPrefs {
    const val QUALITY_1080 = "1080"
    const val QUALITY_1440 = "1440"
    const val QUALITY_AUTO = "auto"

    const val SUB_TR = "tr"
    const val SUB_EN = "en"
    const val SUB_OFF = "off"

    /** (değer, görünen ad) — Tizen `SUB_OPTIONS` / `QUALITY_OPTIONS` ile aynı sıra ve metinler. */
    val SUB_OPTIONS: List<Pair<String, String>> = listOf(
        SUB_TR to "Türkçe",
        SUB_EN to "İngilizce",
        SUB_OFF to "Kapalı",
    )
    val QUALITY_OPTIONS: List<Pair<String, String>> = listOf(
        QUALITY_1080 to "1080p",
        QUALITY_1440 to "1440p",
        QUALITY_AUTO to "Otomatik",
    )

    fun normalizeQuality(value: String?): String? = when (value) {
        QUALITY_1080, QUALITY_1440, QUALITY_AUTO -> value
        else -> null
    }

    fun normalizeSub(value: String?): String? = when (value) {
        SUB_TR, SUB_EN, SUB_OFF -> value
        else -> null
    }

    /** Kayıtlı (ya da yok) kalite tercihinden geçerli değer: TV varsayılanı 1080, diğerleri sınırsız. */
    fun effectiveQuality(stored: String?, isTv: Boolean): String =
        normalizeQuality(stored) ?: if (isTv) QUALITY_1080 else QUALITY_AUTO

    /** Tercih -> en yüksek yükseklik (0 = sınırsız / dokunma). */
    fun qualityCap(pref: String): Int = when (pref) {
        QUALITY_AUTO -> 0
        QUALITY_1440 -> 1440
        else -> 1080
    }

    /** Ayarlar ekranında seçili gösterilecek altyazı dili: kayıt yoksa Türkçe (varsayılan kural Türkçe öncelikli). */
    fun displaySub(stored: String?): String = normalizeSub(stored) ?: SUB_TR
}
