package com.diziflix.app

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import coil.Coil
import com.diziflix.app.data.cache.FileCacheStore
import com.diziflix.app.data.cache.Loaded
import com.diziflix.app.data.cache.LocalCache
import com.diziflix.app.data.cache.ProgressQueue
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiJson
import com.diziflix.app.data.net.ConnectivityMonitor
import com.diziflix.app.data.repo.DiziflixRepository
import com.diziflix.app.data.repo.HydrateWatcher
import com.diziflix.app.data.repo.NotificationPoller
import com.diziflix.app.data.settings.SettingsStore
import com.diziflix.app.domain.ErrorLogic
import com.diziflix.app.domain.Failure
import com.diziflix.app.domain.OfflineNotice
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.Cache
import okhttp3.OkHttpClient
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Elle kurulan basit bağımlılık kabı (DI kütüphanesi yok). Application ömrü boyunca yaşar. */
class AppContainer(val app: Application) {

    /** Ekran/VM ömründen bağımsız işler (ilerleme kaydı, sağlık raporu, ayar yazma). */
    val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val json = ApiJson.instance

    /** Cihaz TV kumandalı kutu mu (çalışma zamanı algısı; TV'ye özgü varsayılanlar için). */
    val isTv: Boolean by lazy { com.diziflix.app.ui.tv.detectTv(app) }

    val settings = SettingsStore(app, appScope)

    /**
     * Ortak OkHttp: 50 MB disk önbelleği; sunucunun ETag/Cache-Control başlıkları uygulanır
     * (API çağrıları max-age=0 ile koşullu/304 gider, /img görselleri günlük önbellekten gelir).
     */
    val http: OkHttpClient = OkHttpClient.Builder()
        .cache(Cache(File(app.cacheDir, "http"), 50L * 1024 * 1024))
        // Kısa bağlantı zaman aşımı: sunucu/ağ yoksa önbellek bekletmeden gösterilir, yenileme arkada başarısız olur.
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    val api = ApiClient(http, { settings.awaitLoaded().baseUrl }, json)

    /** Çevrimdışı açılış önbelleği: filesDir altındaki cache klasörü, JSON dosyaları (sistem temizlemesin diye cacheDir DEĞİL). */
    private val cacheStore = FileCacheStore(File(app.filesDir, "cache"))
    val localCache = LocalCache(cacheStore)
    val progressQueue = ProgressQueue(cacheStore)

    val repository = DiziflixRepository(
        api = api,
        cache = localCache,
        queue = progressQueue,
        baseUrl = { settings.awaitLoaded().baseUrl },
    )

    /**
     * Detay hidrasyon izleyicisi (uygulama ömrü): `hydrating` true gelen yapımları `?poll=1` ile yoklar,
     * bitince olay yayar (MainNav toast'ı, açık detay yenilemesi). Çevrimdışıyken sessizce durur.
     */
    val hydrateWatcher = HydrateWatcher(
        scope = appScope,
        poll = { itemId, profileId -> repository.pollDetail(itemId, profileId) },
        isOnline = { isOnline() },
    )

    /**
     * Kaynak bulucu bildirimleri (`GET /api/notifications`): tek yoklama mantığı; zamanlama [com.diziflix.app.ui.nav.MainNav]'da
     * (uygulama açıkken 30 sn, oynatıcıda durur). İmleç profil başına DataStore'da kalıcıdır.
     */
    val notificationPoller = NotificationPoller(
        fetch = { profileId, since -> repository.notifications(profileId, since) },
        markRead = { profileId, upto -> repository.markNotificationsRead(profileId, upto) },
        loadSince = { profileId -> settings.notificationSince(profileId) },
        saveSince = { profileId, since -> settings.setNotificationSince(profileId, since) },
    )

    /** Ağ durumu (geri çağırmalı): ağ gelince ekranlar kendini yeniler, bekleyen ilerleme gönderilir. */
    val connectivity = ConnectivityMonitor(app)

    init {
        appScope.launch {
            connectivity.online.collect { online ->
                if (online) {
                    try {
                        repository.flushProgress()
                    } catch (e: kotlinx.coroutines.CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        // en iyi çaba: bir sonraki bağlantıda yeniden denenir
                    }
                    // Profil zaten seçiliyken Profiller ekranı açılmaz; listeyi burada önbelleğe al ki
                    // çevrimdışıyken "Profil değiştir" önbellekten gelsin (uygulama çevrimiçi açılınca + ağ gelince).
                    repository.warmProfiles()
                }
            }
        }
    }

    /** Cihaz ağa bağlı mı (oynatma hatasında "offline" ayrımı için). Belirsizse true. */
    fun isOnline(): Boolean {
        return try {
            val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return true
            val network = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(network) ?: return false
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } catch (e: SecurityException) {
            true
        }
    }

    /** Hatayı nedene göre sınıflar: ağ yok / sunucuya ulaşılamıyor (adresle) / diğer. */
    suspend fun describeFailure(error: Throwable): Failure =
        ErrorLogic.classify(error, isOnline(), settings.awaitLoaded().baseUrl)

    /**
     * Önbellekten gösterilen içeriğin üstündeki şerit bilgisi: ağ yenilemesi bağlantı hatasıyla
     * başarısızsa, ya da önbellek gösterilirken cihaz zaten çevrimdışıysa; aksi null.
     */
    suspend fun <T> offlineNotice(snapshot: Loaded<T>): OfflineNotice? {
        val error = snapshot.refreshError
        if (error != null) return ErrorLogic.offlineNotice(error, isOnline(), snapshot.savedAtMs)
        if (snapshot.fromCache && snapshot.refreshing && !isOnline()) {
            return OfflineNotice(com.diziflix.app.domain.FailureKind.Offline, snapshot.savedAtMs)
        }
        return null
    }

    /**
     * Elle "Önbelleği temizle" / sunucu adresi değişimi: OkHttp + bellek + yerel (çevrimdışı) önbellek +
     * görsel (Coil) önbellekleri. Bekleyen ilerleme kuyruğu KALIR (kullanıcı verisi).
     */
    @OptIn(coil.annotation.ExperimentalCoilApi::class)
    suspend fun clearCaches() {
        repository.clearCaches()
        try {
            http.cache?.evictAll()
        } catch (e: IOException) {
            // yoksay
        }
        try {
            val loader = Coil.imageLoader(app)
            loader.memoryCache?.clear()
            loader.diskCache?.clear()
        } catch (e: Exception) {
            // yoksay
        }
        // Temizleme profil listesini de sildi; profil seçiliyken ekran açılmayacağından çevrimiçiyse yeniden doldur.
        if (isOnline()) appScope.launch { repository.warmProfiles() }
    }
}
