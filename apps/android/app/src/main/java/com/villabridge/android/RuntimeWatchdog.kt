package com.villabridge.android

import android.content.Context

/**
 * Calisma zamani beklenmedik bicimde kapandiginda yeniden baslatma kararini veren defter.
 *
 * Tek amac: duvardaki tablet kendi kendine toparlansin, ama SONSUZ DONGUYE girmesin. Node
 * surecini yeniden ayaga kaldirmanin tek yolu bu sureci oldurmek oldugu icin (JNI ile ayni
 * surecte calisiyor), her deneme bir sure olumu demektir; geri cekilme olmadan bozuk bir
 * yapilandirma tableti saniyede bir yeniden baslatan bir makineye cevirir.
 *
 * Politika:
 *  - Gecikme ustel: 5s, 10s, 20s, 40s, 80s, 160s, sonra 300s tavaninda sabitlenir.
 *  - Ust sinir: art arda 8 basarisiz denemeden sonra birakilir (`exhausted`), kullanici
 *    uygulamayi acip yeniden baslatana ya da cihaz yeniden acilana kadar denenmez.
 *  - Sayac SIFIRLANIR: calisma zamani 2 dakika boyunca saglikli kaldiysa o tur "basarili"
 *    sayilir. Boylece uc gun sonra bir kez cokup toparlanan sistem, uc gun onceki hatalari
 *    yuzunden vazgecmis olmaz.
 *
 * Defter cihaz-korumali depoda tutulur: karar surec olumunden ve yeniden acilistan sag cikar,
 * kullanici ekrani acmadan once de okunabilir.
 */
object RuntimeWatchdog {
    /** Ilk yeniden baslatma gecikmesi. */
    const val FIRST_DELAY_MS = 5_000L

    /** Gecikme tavani; ustel buyume burada durur. */
    const val MAX_DELAY_MS = 300_000L

    /** Art arda kac basarisiz denemeden sonra birakilir. */
    const val MAX_FAILURES = 8

    /** Sayaci sifirlayan "yeterince uzun ayakta kaldi" suresi. */
    const val STABLE_MS = 120_000L

    private const val PREFS = "villa_runtime_watchdog"
    private const val KEY_FAILURES = "failures"
    private const val KEY_LAST_EXIT = "last_exit_code"
    private const val KEY_LAST_FAILURE_AT = "last_failure_at"
    private const val KEY_NEXT_DELAY = "next_delay_ms"
    private const val KEY_EXHAUSTED = "exhausted"
    private const val KEY_HEALTHY_SINCE = "healthy_since"

    data class Decision(val restart: Boolean, val delayMs: Long, val attempt: Int)

    data class Report(
        val failures: Int,
        val lastExitCode: Int,
        val lastFailureAt: Long,
        val nextDelayMs: Long,
        val exhausted: Boolean,
        val healthySince: Long
    )

    /**
     * Saglikli bir yoklama. Ilk saglikli yoklama zamani isaretlenir; kararlilik suresi dolunca
     * defter temizlenir.
     */
    fun noteHealthy(context: Context) {
        val preferences = preferences(context)
        val now = System.currentTimeMillis()
        val healthySince = preferences.getLong(KEY_HEALTHY_SINCE, 0L)
        if (healthySince == 0L) {
            preferences.edit().putLong(KEY_HEALTHY_SINCE, now).apply()
            return
        }
        if (now - healthySince < STABLE_MS) return
        if (preferences.getInt(KEY_FAILURES, 0) == 0 && !preferences.getBoolean(KEY_EXHAUSTED, false)) return
        preferences.edit()
            .putInt(KEY_FAILURES, 0)
            .putLong(KEY_NEXT_DELAY, FIRST_DELAY_MS)
            .putBoolean(KEY_EXHAUSTED, false)
            .apply()
    }

    /** Beklenmedik cikis. Donen karar yeniden baslatilacak mi ve ne kadar beklenecegidir. */
    fun noteFailure(context: Context, exitCode: Int): Decision {
        val preferences = preferences(context)
        val attempt = preferences.getInt(KEY_FAILURES, 0) + 1
        val delay = (FIRST_DELAY_MS shl (attempt - 1).coerceIn(0, 16)).coerceAtMost(MAX_DELAY_MS)
        val restart = attempt <= MAX_FAILURES
        preferences.edit()
            .putInt(KEY_FAILURES, attempt)
            .putInt(KEY_LAST_EXIT, exitCode)
            .putLong(KEY_LAST_FAILURE_AT, System.currentTimeMillis())
            .putLong(KEY_NEXT_DELAY, if (restart) delay else 0L)
            .putBoolean(KEY_EXHAUSTED, !restart)
            .putLong(KEY_HEALTHY_SINCE, 0L)
            .apply()
        return Decision(restart, delay, attempt)
    }

    /**
     * Kullanicinin ya da yeniden acilisin acik istegi: defter temizlenir. Vazgecilmis bir sistem
     * ancak bu yolla yeniden denenir.
     */
    fun reset(context: Context) {
        preferences(context).edit()
            .putInt(KEY_FAILURES, 0)
            .putLong(KEY_NEXT_DELAY, FIRST_DELAY_MS)
            .putBoolean(KEY_EXHAUSTED, false)
            .putLong(KEY_HEALTHY_SINCE, 0L)
            .apply()
    }

    fun report(context: Context): Report {
        val preferences = preferences(context)
        return Report(
            failures = preferences.getInt(KEY_FAILURES, 0),
            lastExitCode = preferences.getInt(KEY_LAST_EXIT, 0),
            lastFailureAt = preferences.getLong(KEY_LAST_FAILURE_AT, 0L),
            nextDelayMs = preferences.getLong(KEY_NEXT_DELAY, FIRST_DELAY_MS),
            exhausted = preferences.getBoolean(KEY_EXHAUSTED, false),
            healthySince = preferences.getLong(KEY_HEALTHY_SINCE, 0L)
        )
    }

    private fun preferences(context: Context) = context.createDeviceProtectedStorageContext()
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
