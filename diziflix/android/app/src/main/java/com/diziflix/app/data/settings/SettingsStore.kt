package com.diziflix.app.data.settings

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.PlayPrefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import java.io.IOException

/** Kalıcı ayarlar: sunucu adresi, seçili profil ve oynatma tercihleri. */
data class AppSettings(
    val baseUrl: String = UrlUtil.DEFAULT_BASE_URL,
    val profileId: String? = null,
    /** "En yüksek kalite" ("1080" | "1440" | "auto"); null = hiç seçilmedi ([com.diziflix.app.domain.PlayPrefs]). */
    val qualityPref: String? = null,
    /** SEÇİLİ profilin varsayılan altyazı dili ("tr" | "en" | "off"); null = seçilmedi. */
    val subPref: String? = null,
)

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "diziflix_settings")

class SettingsStore(private val context: Context, scope: CoroutineScope) {

    private companion object {
        const val NOTIF_PREFIX = "notif_since_"
    }

    private val keyBaseUrl = stringPreferencesKey("base_url")
    private val keyProfile = stringPreferencesKey("profile_id")
    private val keyQuality = stringPreferencesKey("quality_pref")

    private fun subKey(profileId: String) = stringPreferencesKey("sub_pref_$profileId")

    /** Bildirim imleci (profil başına): son görülen bildirim kimliği (`since`). */
    private fun notifKey(profileId: String) = longPreferencesKey("$NOTIF_PREFIX$profileId")

    /** DataStore ilk okumayı bitirene kadar null. */
    val settings: StateFlow<AppSettings?> = context.dataStore.data
        .catch { e ->
            if (e is IOException) emit(emptyPreferences()) else throw e
        }
        .map { prefs ->
            val profile = prefs[keyProfile]?.takeIf { it.isNotBlank() }
            AppSettings(
                baseUrl = UrlUtil.normalizeBaseUrl(prefs[keyBaseUrl]) ?: UrlUtil.DEFAULT_BASE_URL,
                profileId = profile,
                qualityPref = PlayPrefs.normalizeQuality(prefs[keyQuality]),
                subPref = profile?.let { PlayPrefs.normalizeSub(prefs[subKey(it)]) },
            )
        }
        .stateIn(scope, SharingStarted.Eagerly, null)

    suspend fun awaitLoaded(): AppSettings = settings.filterNotNull().first()

    suspend fun setBaseUrl(url: String) {
        val normalized = UrlUtil.normalizeBaseUrl(url) ?: UrlUtil.DEFAULT_BASE_URL
        context.dataStore.edit { prefs ->
            val previous = prefs[keyBaseUrl]
            prefs[keyBaseUrl] = normalized
            // Başka sunucu = başka bildirim kimlik uzayı: eski imleçler yeni sunucuda yanlış bildirimleri gizlerdi.
            if (previous != null && previous != normalized) {
                prefs.asMap().keys.filter { it.name.startsWith(NOTIF_PREFIX) }.forEach { prefs.remove(it) }
            }
        }
    }

    suspend fun setQualityPref(value: String) {
        val normalized = PlayPrefs.normalizeQuality(value) ?: return
        context.dataStore.edit { it[keyQuality] = normalized }
    }

    suspend fun setSubPref(profileId: String, value: String) {
        val normalized = PlayPrefs.normalizeSub(value) ?: return
        context.dataStore.edit { it[subKey(profileId)] = normalized }
    }

    /** Profilin bildirim imleci; null = bu profil için hiç yoklanmadı (ilk çalıştırma: eski bildirimler toast olmaz). */
    suspend fun notificationSince(profileId: String): Long? {
        val prefs = try {
            context.dataStore.data.first()
        } catch (e: IOException) {
            emptyPreferences()
        }
        return prefs[notifKey(profileId)]
    }

    suspend fun setNotificationSince(profileId: String, value: Long) {
        context.dataStore.edit { it[notifKey(profileId)] = value }
    }

    suspend fun setProfileId(id: String?) {
        context.dataStore.edit { prefs ->
            if (id.isNullOrBlank()) prefs.remove(keyProfile) else prefs[keyProfile] = id
        }
    }
}
