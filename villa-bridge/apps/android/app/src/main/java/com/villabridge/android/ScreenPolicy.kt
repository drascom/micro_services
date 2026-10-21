package com.villabridge.android

import android.content.Context
import android.os.Build
import android.provider.Settings
import org.json.JSONObject

/**
 * Duvardaki tabletin parlakligi — SAYILAR HALINDE.
 *
 * Kullanicinin gordugu her ayar (kaydiricinin yeri, gece penceresinin saatleri, bekleme suresi,
 * ekran koruyucunun kendisi) paylasilan paneldedir. Konak yalnizca "su an sunu uygula" der:
 * etkin bir deger, kararmis bir deger ve hangisinde oldugumuz. Burada takvim, esik ya da
 * zamanlayici YOKTUR — bekleme suresini panelin ekran koruyucusu sayar, ikinci bir sayac
 * kurulmaz.
 */
data class ScreenPolicy(
    /** Kullanimdaki parlaklik yuzdesi; -1 "dokunma" demek. */
    val activeBrightness: Int,
    /** Ekran koruyucu acikken uygulanan parlaklik; -1 "dokunma". */
    val idleBrightness: Int,
    /** Su anda ekran koruyucu acik mi. */
    val dimmed: Boolean
) {
    val effective: Int get() = if (dimmed) idleBrightness else activeBrightness

    fun toJson(): JSONObject = JSONObject()
        .put("activeBrightness", activeBrightness)
        .put("idleBrightness", idleBrightness)
        .put("dimmed", dimmed)

    companion object {
        /** Varsayilan: hicbir seye dokunma — tablet kendi parlakligiyla acilir. */
        val DEFAULT = ScreenPolicy(-1, -1, false)

        fun fromJson(source: String?): ScreenPolicy? {
            if (source.isNullOrBlank()) return null
            return runCatching {
                val json = JSONObject(source)
                ScreenPolicy(
                    activeBrightness = json.optInt("activeBrightness", -1).coerceIn(-1, 100),
                    idleBrightness = json.optInt("idleBrightness", -1).coerceIn(-1, 100),
                    dimmed = json.optBoolean("dimmed", false)
                )
            }.getOrNull()
        }
    }
}

object ScreenPolicyStore {
    private const val PREFS = "villa_screen_policy"
    private const val KEY_POLICY = "policy"

    fun load(context: Context): ScreenPolicy =
        ScreenPolicy.fromJson(preferences(context).getString(KEY_POLICY, null)) ?: ScreenPolicy.DEFAULT

    fun save(context: Context, policy: ScreenPolicy) {
        preferences(context).edit().putString(KEY_POLICY, policy.toJson().toString()).apply()
    }

    private fun preferences(context: Context) = context.createDeviceProtectedStorageContext()
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}

/**
 * CIHAZIN KENDI PARLAKLIK SISTEMI.
 *
 * Panelden verilen deger, uygulamaya ozel bir katman olarak degil, tabletin kendi
 * `Settings.System.SCREEN_BRIGHTNESS` ayari olarak yazilir: kullanici Android ayarlarindan
 * bakinca ayni degeri gorur, oradan degistirirse panel de onu okur. Bunun bedeli `WRITE_SETTINGS`
 * ozel iznidir; izin YOKSA uygulama calismaya devam eder ve pencere duzeyi parlakliga duser
 * (yalnizca Villa Bridge ekraninda gecerli olan, cihaz ayarina yansimayan eski davranis).
 *
 * OTOMATIK KIP: isik sensoru surerken `SCREEN_BRIGHTNESS` yazmak beklendigi gibi calismaz —
 * sensor bir sonraki okumada degeri geri alir, kaydirici hicbir sey yapmiyormus gibi gorunur.
 * Bu yuzden kullanici paneldan bir deger SECTIGINDE otomatik kip elle kipe alinir. Bu gorunur
 * bir cihaz degisikligi oldugu icin sessizce yapilmaz: `hostStatus()` otomatik kipin kapatildigini
 * bildirir ve panel bunu kullaniciya yazar.
 */
object SystemBrightness {
    /** Android'in bu ayardaki tam olcegi. */
    private const val SYSTEM_MAX = 255

    fun canWrite(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.System.canWrite(context)

    fun automatic(context: Context): Boolean = runCatching {
        Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS_MODE) ==
            Settings.System.SCREEN_BRIGHTNESS_MODE_AUTOMATIC
    }.getOrDefault(false)

    /** Cihazin su anki parlakligi yuzde olarak; okunamazsa -1. */
    fun current(context: Context): Int = runCatching {
        val raw = Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS)
        Math.round(raw * 100f / SYSTEM_MAX).coerceIn(1, 100)
    }.getOrDefault(-1)

    /** Yuzdeyi cihaz ayarina yazar. Basarisizsa `false` doner ve cagiran pencereye duser. */
    fun apply(context: Context, percent: Int): Boolean {
        if (percent < 0 || !canWrite(context)) return false
        return runCatching {
            if (automatic(context)) {
                Settings.System.putInt(
                    context.contentResolver,
                    Settings.System.SCREEN_BRIGHTNESS_MODE,
                    Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL
                )
            }
            Settings.System.putInt(
                context.contentResolver,
                Settings.System.SCREEN_BRIGHTNESS,
                Math.round(percent.coerceIn(1, 100) * SYSTEM_MAX / 100f).coerceIn(1, SYSTEM_MAX)
            )
        }.isSuccess
    }
}
