package com.diziflix.app.data.cache

import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.net.ApiJson
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.io.IOException

/** Önbellekten okunan değer + ağdan son başarılı alınma zamanı (epoch ms). */
data class Cached<T>(val value: T, val savedAtMs: Long)

/** Dosya zarfı: şema sürümü + yazım zamanı + veri. */
@Serializable
internal data class Envelope<T>(val v: Int = 0, val t: Long = 0L, val data: T)

@Serializable
internal data class CacheMeta(val v: Int = 0, val base: String = "")

/** Önbellek anahtarları (profil bazlı: başka profilin ilerleme/listem bilgisi karışmasın). */
object CacheKeys {
    const val PROFILES = "profiles"
    fun boot(profileId: String) = "boot:$profileId"
    fun row(profileId: String, rowId: String) = "row:$profileId:$rowId"
    fun detail(profileId: String, itemId: String) = "detail:$profileId:$itemId"
}

/**
 * Çevrimdışı açılış için yerel JSON önbelleği.
 *  - Şema sürümlü: [SCHEMA_VERSION] uyuşmayan kayıt yok sayılır (yeniden ağdan dolar).
 *  - Sunucu adresine bağlı: [bindServer] adres değişmişse HER ŞEYİ (ilerleme kuyruğu dahil) siler;
 *    başka sunucunun kimlikleri/içeriği karışmaz.
 *  - Detaylar LRU: en son [maxDetails] adet (profil + yapım başına bir dosya).
 * Tüm erişim tek kilit altındadır; depolama [CacheStore] (dosya ya da test sahtesi).
 */
class LocalCache(
    private val store: CacheStore,
    private val json: Json = ApiJson.instance,
    private val clock: () -> Long = System::currentTimeMillis,
    private val maxDetails: Int = DEFAULT_MAX_DETAILS,
) {
    companion object {
        /** Önbellek şeması: model/zarf biçimi değişince artırılır. */
        const val SCHEMA_VERSION = 1
        const val DEFAULT_MAX_DETAILS = 30

        internal const val META = "meta"
        internal const val DETAIL_INDEX = "detail_index"

        /** İlerleme kuyruğu ([ProgressQueue]) — elle "Önbelleği temizle" bunu SİLMEZ. */
        const val PROGRESS_QUEUE = "progress_queue"
    }

    private val lock = Mutex()
    private var boundBase: String? = null
    private var detailIndex: MutableList<String>? = null

    /** Önbelleği [base] sunucusuna bağlar; adres (ya da şema) değişmişse eski içeriği temizler. */
    suspend fun bindServer(base: String) {
        if (boundBase == base) return
        lock.withLock {
            if (boundBase == base) return
            val meta = readMeta()
            when {
                meta == null -> Unit
                meta.base != base -> wipe(keepQueue = false)
                meta.v != SCHEMA_VERSION -> wipe(keepQueue = true)
            }
            writeMeta(base)
            boundBase = base
        }
    }

    suspend fun <T> read(key: String, serializer: KSerializer<T>): Cached<T>? =
        lock.withLock { readLocked(key, serializer) }

    suspend fun <T> write(key: String, serializer: KSerializer<T>, value: T) =
        lock.withLock { writeLocked(key, serializer, value) }

    suspend fun remove(key: String) = lock.withLock { store.delete(key) }

    /**
     * Saklı kaydı yerinde düzeltir: [transform] sonucu değiştiyse yazılır. Kayıt yoksa/bozuksa hiçbir şey
     * yapılmaz. Yazım zamanı KORUNUR (ağdan yenilenmedi: "son güncelleme" şeridi yalan söylemesin).
     */
    suspend fun <T> update(key: String, serializer: KSerializer<T>, transform: (T) -> T) {
        lock.withLock {
            val hit = readLocked(key, serializer) ?: return@withLock
            val next = transform(hit.value)
            if (next != hit.value) writeLocked(key, serializer, next, hit.savedAtMs)
        }
    }

    // ------------------------------------------------------------------ detay (LRU)

    suspend fun readDetail(profileId: String, itemId: String): Cached<Detail>? = lock.withLock {
        val key = CacheKeys.detail(profileId, itemId)
        val hit = readLocked(key, Detail.serializer()) ?: return@withLock null
        val index = loadIndex()
        if (index.lastOrNull() != key) {
            index.remove(key)
            index.add(key)
            saveIndex(index)
        }
        hit
    }

    suspend fun writeDetail(profileId: String, itemId: String, detail: Detail) = lock.withLock {
        val key = CacheKeys.detail(profileId, itemId)
        // `hydrating` geçici bir sunucu durumudur: saklanırsa çevrimdışı açılışta sonsuza dek "yükleniyor" görünürdü.
        // Şema sürümü artmaz: alan varsayılanlı, eski kayıtlar false okunur.
        val stored = if (detail.hydrating) detail.copy(extras = detail.extras.copy(hydrating = false)) else detail
        writeLocked(key, Detail.serializer(), stored)
        val index = loadIndex()
        index.remove(key)
        index.add(key)
        while (index.size > maxDetails) {
            store.delete(index.removeAt(0))
        }
        saveIndex(index)
    }

    suspend fun removeDetail(profileId: String, itemId: String) = lock.withLock {
        val key = CacheKeys.detail(profileId, itemId)
        store.delete(key)
        val index = loadIndex()
        if (index.remove(key)) saveIndex(index)
    }

    /** Test/teşhis: LRU sırası (eskiden yeniye). */
    internal suspend fun detailKeys(): List<String> = lock.withLock { loadIndex().toList() }

    // ------------------------------------------------------------------ temizleme

    /** "Önbelleği temizle": içerik silinir; bağlı sunucu bilgisi ve ilerleme kuyruğu KALIR. */
    suspend fun clearAll() = lock.withLock {
        wipe(keepQueue = true)
        boundBase?.let { writeMeta(it) }
    }

    // ------------------------------------------------------------------ iç

    private suspend fun <T> readLocked(key: String, serializer: KSerializer<T>): Cached<T>? {
        val text = try {
            store.read(key)
        } catch (e: IOException) {
            null
        } ?: return null
        return try {
            val env = json.decodeFromString(Envelope.serializer(serializer), text)
            if (env.v != SCHEMA_VERSION) null else Cached(env.data, env.t)
        } catch (e: IllegalArgumentException) {
            null   // bozuk/uyumsuz kayıt: yok sayılır (SerializationException, IllegalArgumentException alt sınıfı)
        }
    }

    private suspend fun <T> writeLocked(key: String, serializer: KSerializer<T>, value: T, savedAtMs: Long = clock()) {
        val text = json.encodeToString(Envelope.serializer(serializer), Envelope(SCHEMA_VERSION, savedAtMs, value))
        try {
            store.write(key, text)
        } catch (e: IOException) {
            // disk dolu/yazılamıyor: önbellek en iyi çabadır
        }
    }

    private suspend fun readMeta(): CacheMeta? = try {
        store.read(META)?.let { json.decodeFromString(CacheMeta.serializer(), it) }
    } catch (e: IllegalArgumentException) {
        null
    } catch (e: IOException) {
        null
    }

    private suspend fun writeMeta(base: String) {
        try {
            store.write(META, json.encodeToString(CacheMeta.serializer(), CacheMeta(SCHEMA_VERSION, base)))
        } catch (e: IOException) {
            // yoksay
        }
    }

    private suspend fun wipe(keepQueue: Boolean) {
        for (name in store.names()) {
            if (keepQueue && name == PROGRESS_QUEUE) continue
            try {
                store.delete(name)
            } catch (e: IOException) {
                // yoksay
            }
        }
        detailIndex = null
    }

    private suspend fun loadIndex(): MutableList<String> {
        detailIndex?.let { return it }
        val stored = readLocked(DETAIL_INDEX, ListSerializer(String.serializer()))?.value
        val list = stored?.toMutableList()
            ?: store.names().filter { it.startsWith("detail:") }.toMutableList()
        detailIndex = list
        return list
    }

    private suspend fun saveIndex(index: List<String>) {
        writeLocked(DETAIL_INDEX, ListSerializer(String.serializer()), index)
    }
}
