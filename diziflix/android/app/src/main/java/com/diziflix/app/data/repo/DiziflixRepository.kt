package com.diziflix.app.data.repo

import com.diziflix.app.data.cache.CacheKeys
import com.diziflix.app.data.cache.Cached
import com.diziflix.app.data.cache.FlushResult
import com.diziflix.app.data.cache.Loaded
import com.diziflix.app.data.cache.LocalCache
import com.diziflix.app.data.cache.PendingProgress
import com.diziflix.app.data.cache.ProgressQueue
import com.diziflix.app.data.cache.offlineFirst
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.CatalogResponse
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.FinderStatus
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.NotificationsResponse
import com.diziflix.app.data.model.Profile
import com.diziflix.app.data.model.ProfilesResponse
import com.diziflix.app.data.model.RowResponse
import com.diziflix.app.data.model.SearchResponse
import com.diziflix.app.data.model.StreamsResponse
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.CatalogQuery
import com.diziflix.app.domain.ContinueLogic
import com.diziflix.app.domain.ErrorLogic
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.toList

/**
 * Repository: ViewModel'ler ApiClient'ı doğrudan bilmez. İki sorumluluk:
 *  1. Çevrimdışı açılış ("Netflix mantığı"): profiller, ana sayfa boot'u, açılan satırlar ve görüntülenen
 *     detaylar yerel [LocalCache]'e yazılır; `*Flow` uçları ÖNCE önbelleği yayar, sonra ağdan yeniler
 *     (stale-while-revalidate, bkz. [offlineFirst]). Ağ hatasında önbellek korunur.
 *  2. Ağ yokken gönderilemeyen izleme konumu [ProgressQueue]'ya alınır, ağ gelince sırayla gönderilir.
 * [cache]/[queue] null ise (eski kullanım, testler) her şey doğrudan ağa gider.
 */
class DiziflixRepository(
    private val api: ApiClient,
    private val cache: LocalCache? = null,
    private val queue: ProgressQueue? = null,
    private val baseUrl: suspend () -> String = { "" },
    private val clock: () -> Long = System::currentTimeMillis,
) {

    /** Oynatıcının (başlık/sonraki bölüm için) yeniden ağ isteği yapmadan kullanabileceği bellek içi detay önbelleği. */
    private val detailCache = object : LinkedHashMap<String, Detail>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Detail>?): Boolean = size > MAX_DETAILS
    }

    // ------------------------------------------------------------------ çevrimdışı-öncelikli uçlar

    fun profilesFlow(readCache: Boolean = true): Flow<Loaded<List<Profile>>> = cached(
        key = CacheKeys.PROFILES,
        serializer = ProfilesResponse.serializer(),
        readCache = readCache,
        fetch = { ProfilesResponse(api.profiles()) },
    ).map { it.mapValue { r -> r.profiles } }

    /**
     * Profil listesini ekrana bağlı OLMADAN ağdan çekip yerel önbelleğe yazar (en iyi çaba). Profil zaten
     * seçiliyse "Kim izliyor?" ekranı hiç açılmaz; bu olmadan önbellek boş kalır ve sonradan çevrimdışıyken
     * "Profil değiştir" hata verir. Ağ/sunucu hatası yutulur (mevcut önbellek KORUNUR); true = önbellek tazelendi.
     */
    suspend fun warmProfiles(): Boolean {
        if (cache == null) return false
        return try {
            profilesFlow(readCache = false).toList().isNotEmpty()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            false
        }
    }

    fun bootFlow(profileId: String, readCache: Boolean = true): Flow<Loaded<BootResponse>> = cached(
        key = CacheKeys.boot(profileId),
        serializer = BootResponse.serializer(),
        readCache = readCache,
        fetch = { api.boot(profileId) },
    )

    fun rowFlow(
        rowId: String,
        profileId: String,
        offset: Int = 0,
        limit: Int = 20,
        readCache: Boolean = true,
    ): Flow<Loaded<RowResponse>> = cached(
        key = CacheKeys.row(profileId, rowId),
        serializer = RowResponse.serializer(),
        readCache = readCache && offset == 0,   // yalnızca ilk sayfa saklanır
        fetch = { api.row(rowId, profileId, offset, limit) },
        store = offset == 0,
    )

    fun detailFlow(itemId: String, profileId: String, readCache: Boolean = true): Flow<Loaded<Detail>> {
        val local = cache
        if (local == null) {
            return offlineFirst(
                read = { null },
                write = { remember(itemId, it) },
                fetch = { api.detail(itemId, profileId) },
                clock = clock,
            )
        }
        return offlineFirst(
            read = {
                if (!readCache) null
                else {
                    local.bindServer(baseUrl())
                    local.readDetail(profileId, itemId)
                }
            },
            write = { detail ->
                remember(itemId, detail)
                local.bindServer(baseUrl())
                local.writeDetail(profileId, itemId, detail)
            },
            fetch = { api.detail(itemId, profileId) },
            clock = clock,
        )
    }

    private fun <T> cached(
        key: String,
        serializer: kotlinx.serialization.KSerializer<T>,
        readCache: Boolean,
        fetch: suspend () -> T,
        store: Boolean = true,
    ): Flow<Loaded<T>> {
        val local = cache
        if (local == null) {
            return offlineFirst(read = { null }, write = {}, fetch = fetch, clock = clock)
        }
        return offlineFirst(
            read = {
                if (!readCache) null
                else {
                    local.bindServer(baseUrl())
                    local.read(key, serializer)
                }
            },
            write = { value ->
                if (store) {
                    local.bindServer(baseUrl())
                    local.write(key, serializer, value)
                }
            },
            fetch = fetch,
            clock = clock,
        )
    }

    private fun <A, B> Loaded<A>.mapValue(transform: (A) -> B): Loaded<B> =
        Loaded(transform(value), fromCache, savedAtMs, refreshing, refreshError)

    // ------------------------------------------------------------------ doğrudan ağ uçları (eski/oynatıcı kullanımı)

    suspend fun profiles(): List<Profile> = api.profiles()

    suspend fun boot(profileId: String): BootResponse = api.boot(profileId)

    suspend fun row(rowId: String, profileId: String, offset: Int = 0, limit: Int = 20): RowResponse =
        api.row(rowId, profileId, offset, limit)

    suspend fun detail(itemId: String, profileId: String): Detail {
        val detail = api.detail(itemId, profileId)
        store(itemId, profileId, detail)
        return detail
    }

    /**
     * Hidrasyon yoklaması (`?poll=1`): sunucuda iş BAŞLATMAZ. Hidrasyon bittiyse (hydrating=false) yeni veri
     * bellek içi detay önbelleğine ve yerel detay önbelleğine (LRU) yazılır; sürüyorsa önbelleklere dokunulmaz.
     * Ağ/sunucu hatası yukarı fırlar (çağıran sessizce durur).
     */
    suspend fun pollDetail(itemId: String, profileId: String): Detail {
        val detail = api.detail(itemId, profileId, poll = true)
        if (!detail.hydrating) store(itemId, profileId, detail)
        return detail
    }

    private suspend fun store(itemId: String, profileId: String, detail: Detail) {
        remember(itemId, detail)
        try {
            cache?.let {
                it.bindServer(baseUrl())
                it.writeDetail(profileId, itemId, detail)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // önbellek yazımı en iyi çaba
        }
    }

    private fun remember(itemId: String, detail: Detail) {
        synchronized(detailCache) { detailCache[itemId] = detail }
    }

    /** Son alınan detay (oynatıcı başlığı/sonraki bölüm için); yoksa null. */
    fun cachedDetail(itemId: String): Detail? = synchronized(detailCache) { detailCache[itemId] }

    fun invalidateDetail(itemId: String) {
        synchronized(detailCache) { detailCache.remove(itemId) }
    }

    /** Bellek içi detay önbelleğini + (varsa) yerel önbelleği temizler. İlerleme kuyruğu KALIR. */
    suspend fun clearCaches() {
        synchronized(detailCache) { detailCache.clear() }
        cache?.clearAll()
    }

    suspend fun streams(itemId: String, profileId: String, episodeId: String?, kind: String?): StreamsResponse =
        api.streams(itemId, profileId, episodeId, kind)

    /** Kaynak bulucu durumu (oynatıcı hata panelinde 5 sn'de bir). */
    suspend fun sourceFinder(itemId: String, episodeId: String?): FinderStatus = api.sourceFinder(itemId, episodeId)

    suspend fun notifications(profileId: String, since: Long): NotificationsResponse = api.notifications(profileId, since)

    suspend fun markNotificationsRead(profileId: String, upto: Long) = api.markNotificationsRead(profileId, upto)

    suspend fun catalog(query: CatalogQuery): CatalogResponse = api.catalog(query)

    suspend fun search(q: String, profileId: String, limit: Int = 20): SearchResponse = api.search(q, profileId, limit)

    suspend fun myList(profileId: String): List<Item> = api.mylist(profileId)

    suspend fun setInMyList(profileId: String, itemId: String, add: Boolean) {
        if (add) api.addToMyList(profileId, itemId) else api.removeFromMyList(profileId, itemId)
        invalidateDetail(itemId)
        // Saklı detaydaki "listemde" bilgisi bayatlamasın (çevrimdışı açılışta yanlış göstermesin).
        try {
            cache?.removeDetail(profileId, itemId)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // yoksay
        }
    }

    /**
     * "İzlemeye Devam Et" satırından kaldırır. Ağ/sunucu hatası yukarı fırlar ve yerel önbelleğe DOKUNULMAZ.
     * Başarıda saklı `boot:<profil>` ve `row:<profil>:continue` kopyalarından öğe çıkarılır ki çevrimdışı
     * açılışta geri gelmesin. İlerleme kayıtları sunucuda kalır (yumuşak gizleme): detay önbelleği geçerlidir.
     */
    suspend fun removeFromContinue(itemId: String, profileId: String) {
        api.removeFromContinue(itemId, profileId)
        val local = cache ?: return
        try {
            local.bindServer(baseUrl())
            local.update(CacheKeys.boot(profileId), BootResponse.serializer()) { ContinueLogic.bootWithout(it, itemId) }
            local.update(CacheKeys.row(profileId, ContinueLogic.ROW_ID), RowResponse.serializer()) {
                ContinueLogic.rowWithout(it, itemId)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // önbellek düzeltmesi en iyi çaba: sunucu tarafı zaten gizledi
        }
    }

    suspend fun reportPlayback(attemptToken: String, event: String, code: String, engine: String, detail: String = "") =
        api.playbackReport(attemptToken, event, code, engine, detail)

    // ------------------------------------------------------------------ ilerleme (çevrimdışı kuyruk)

    /**
     * Konumu sunucuya gönderir. Bağlantı hatasında (ağ yok/zaman aşımı) cihazdaki kuyruğa alınır
     * (anahtar başına tek kayıt, son konum kazanır) ve hata YUTULUR; diğer hatalar (ör. 400) yukarı iletilir.
     * Başarılı gönderimden sonra bu anahtarın eski kuyruk kaydı silinir ve kalan kuyruk sırayla boşaltılır.
     */
    suspend fun saveProgress(profileId: String, itemId: String, episodeId: String, positionSec: Int, durationSec: Int) {
        try {
            api.progress(profileId, itemId, episodeId, positionSec, durationSec)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            val q = queue
            if (q != null && ErrorLogic.isConnectivityError(e)) {
                q.enqueue(PendingProgress(profileId, itemId, episodeId, positionSec, durationSec))
                return
            }
            throw e
        }
        val q = queue ?: return
        q.discard(profileId, itemId, episodeId)
        if (!q.isEmpty()) flushProgress()
    }

    /** Bekleyen ilerlemeleri (kayıtlı profil kimlikleriyle) sırayla gönderir. Ağ gelince/uygulama açılınca çağrılır. */
    suspend fun flushProgress(): FlushResult? {
        val q = queue ?: return null
        return q.flush { p -> api.progress(p.profileId, p.itemId, p.episodeId, p.positionSec, p.durationSec) }
    }

    private companion object {
        const val MAX_DETAILS = 24
    }
}
