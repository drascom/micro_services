package com.diziflix.app.data.cache

import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.ApiJson
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.IOException

/**
 * Ağ yokken gönderilemeyen izleme konumu. Anahtar: (profil, yapım, bölüm); aynı anahtar için TEK kayıt
 * tutulur ve en son konum kazanır. [savedAtMs] yalnızca sıralama içindir, sunucuya GÖNDERİLMEZ
 * (sunucu `updated_at`'i kendi saatiyle verir; istemci saati yerine son konum gider).
 * Profil kimliği kayıtla saklanır: profil/hesap değişse de ilerleme doğru profile gider.
 */
@Serializable
data class PendingProgress(
    val profileId: String,
    val itemId: String,
    val episodeId: String,
    val positionSec: Int,
    val durationSec: Int,
    val savedAtMs: Long = 0L,
) {
    internal fun sameKey(other: PendingProgress) =
        profileId == other.profileId && itemId == other.itemId && episodeId == other.episodeId
}

@Serializable
internal data class ProgressQueueFile(val v: Int = 1, val items: List<PendingProgress> = emptyList())

/** [ProgressQueue.flush] özeti. */
data class FlushResult(val sent: Int, val dropped: Int, val remaining: Int)

/**
 * Cihazda kalıcı (dosya) ilerleme kuyruğu. Sıra: son güncellenen en SONDA; ağ gelince eskiden yeniye
 * gönderilir, böylece sunucudaki "son izlenen" sırası korunur. Tüm işlemler tek kilit altındadır;
 * yalnızca ağ çağrısı (send) kilit DIŞINDA yapılır.
 */
class ProgressQueue(
    private val store: CacheStore,
    private val json: Json = ApiJson.instance,
    private val clock: () -> Long = System::currentTimeMillis,
    private val name: String = LocalCache.PROGRESS_QUEUE,
) {
    private val dataLock = Mutex()
    private val flushLock = Mutex()

    /** Aynı (profil, yapım, bölüm) için eskisini değiştirir ve kaydı sona taşır. */
    suspend fun enqueue(entry: PendingProgress) {
        val stamped = entry.copy(savedAtMs = clock())
        dataLock.withLock {
            val items = load().filterNot { it.sameKey(stamped) } + stamped
            save(items)
        }
    }

    /** Bu anahtar için bekleyen kayıt varsa siler (daha yeni konum başarıyla gitti). */
    suspend fun discard(profileId: String, itemId: String, episodeId: String) {
        dataLock.withLock {
            val items = load()
            val kept = items.filterNot {
                it.profileId == profileId && it.itemId == itemId && it.episodeId == episodeId
            }
            if (kept.size != items.size) save(kept)
        }
    }

    suspend fun pending(): List<PendingProgress> = dataLock.withLock { load() }

    suspend fun isEmpty(): Boolean = pending().isEmpty()

    /**
     * Kuyruğu sırayla gönderir. Her başarıda kayıt silinir (yalnızca gönderilirken DEĞİŞMEMİŞSE; aradan
     * daha yeni konum girdiyse o kalır). Hata: [isPermanent] ise (ör. silinmiş profil 400) kayıt atılır ve
     * devam edilir; değilse (ağ/zaman aşımı/5xx) durulur ve kalanlar korunur.
     */
    suspend fun flush(
        isPermanent: (Throwable) -> Boolean = { isPermanentFailure(it) },
        send: suspend (PendingProgress) -> Unit,
    ): FlushResult = flushLock.withLock {
        var sent = 0
        var dropped = 0
        for (entry in pending()) {
            try {
                send(entry)
                sent++
                removeIfUnchanged(entry)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (isPermanent(e)) {
                    dropped++
                    removeIfUnchanged(entry)
                } else {
                    break
                }
            }
        }
        FlushResult(sent, dropped, pending().size)
    }

    private suspend fun removeIfUnchanged(entry: PendingProgress) {
        dataLock.withLock {
            val items = load()
            if (items.any { it == entry }) save(items.filterNot { it == entry })
        }
    }

    private suspend fun load(): List<PendingProgress> {
        val text = try {
            store.read(name)
        } catch (e: IOException) {
            null
        } ?: return emptyList()
        return try {
            val file = json.decodeFromString(ProgressQueueFile.serializer(), text)
            if (file.v != 1) emptyList() else file.items
        } catch (e: IllegalArgumentException) {
            emptyList()
        }
    }

    private suspend fun save(items: List<PendingProgress>) {
        try {
            if (items.isEmpty()) {
                store.delete(name)
            } else {
                store.write(name, json.encodeToString(ProgressQueueFile.serializer(), ProgressQueueFile(1, items)))
            }
        } catch (e: IOException) {
            // yazılamıyorsa kuyruk bellekte tutulmaz: en iyi çaba
        }
    }

    companion object {
        /** 4xx (408/429 hariç) istek yeniden denense de asla başarılı olmaz: atılır. */
        fun isPermanentFailure(error: Throwable): Boolean {
            val status = (error as? ApiException)?.status ?: return false
            return status in 400..499 && status != 408 && status != 429
        }
    }
}
