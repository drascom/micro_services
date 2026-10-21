package com.diziflix.app.domain

/**
 * Ekran ölçülerine göre yerleşim kararları (saf Kotlin, birim testli; hepsi dp cinsinden).
 * Sabit oran yerine genişlik/yükseklikten türetilir: tablet yatayda hero ekranı aşmaz, kırpılır.
 */
object LayoutLogic {
    /** Bu genişliğin altı "telefon" sayılır. */
    const val COMPACT_MAX_DP = 600f
    const val HERO_MIN_DP = 240f
    const val HERO_MAX_SCREEN_FRACTION = 0.6f

    const val DETAIL_BACKDROP_MIN_DP = 180f
    const val DETAIL_BACKDROP_MAX_SCREEN_FRACTION = 0.5f

    /** Hero oranı: telefonda 4:3, geniş ekranda 16:9 (sunucu artalanı 16:9'dur). */
    fun heroRatio(widthDp: Float): Float = if (widthDp < COMPACT_MAX_DP) 4f / 3f else 16f / 9f

    /** Ana sayfa hero yüksekliği: doğal orana göre, ekran yüksekliğinin %60'ını aşmaz, en az 240dp. */
    fun heroHeightDp(widthDp: Float, screenHeightDp: Float): Float {
        val natural = widthDp / heroRatio(widthDp)
        val cap = screenHeightDp * HERO_MAX_SCREEN_FRACTION
        return minOf(natural, cap).coerceAtLeast(HERO_MIN_DP)
    }

    /** Detay sayfası artalanı: 16:9, ekran yüksekliğinin yarısını aşmaz, en az 180dp. */
    fun detailBackdropHeightDp(widthDp: Float, screenHeightDp: Float): Float {
        val natural = widthDp * 9f / 16f
        val cap = screenHeightDp * DETAIL_BACKDROP_MAX_SCREEN_FRACTION
        return minOf(natural, cap).coerceAtLeast(DETAIL_BACKDROP_MIN_DP)
    }

    /** Yatay poster sırasındaki kart genişliği (geniş ekranda daha büyük). */
    fun posterWidthDp(widthDp: Float): Float = when {
        widthDp >= 840f -> 160f
        widthDp >= COMPACT_MAX_DP -> 140f
        else -> 112f
    }

    // ------------------------------------------------------------------ kademeli yükleme

    /** Satır başına başlangıçta çizilen en çok kart; kalanı kaydırdıkça eklenir. */
    const val INITIAL_CARDS = 16
    const val CARDS_STEP = 16
    private const val CARDS_LOOKAHEAD = 4

    /** Ana sayfada açılışta hemen yüklenen satır sayısı; sonrası kaydırdıkça (1 satır ilerisi) yüklenir. */
    const val INITIAL_ROWS = 3
    const val ROW_LOOKAHEAD = 1

    /** En son görünen kart sona [CARDS_LOOKAHEAD] kala limit [CARDS_STEP] artar (en çok [total]). */
    fun nextCardLimit(current: Int, total: Int, lastVisibleIndex: Int): Int = when {
        current >= total -> total
        lastVisibleIndex >= current - CARDS_LOOKAHEAD -> minOf(total, current + CARDS_STEP)
        else -> current
    }

    /**
     * Yüklenmesi gereken son satır dizini: ilk [INITIAL_ROWS] satır her zaman, sonrası son görünen satırın
     * [ROW_LOOKAHEAD] ilerisine kadar. Satır yoksa -1.
     */
    fun lastRowToLoad(lastVisibleRow: Int, rowCount: Int): Int =
        minOf(rowCount - 1, maxOf(INITIAL_ROWS - 1, lastVisibleRow + ROW_LOOKAHEAD))
}
