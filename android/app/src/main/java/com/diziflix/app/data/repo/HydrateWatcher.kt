package com.diziflix.app.data.repo

import com.diziflix.app.data.model.Detail
import com.diziflix.app.domain.HydrateEvent
import com.diziflix.app.domain.HydrateLogic
import com.diziflix.app.domain.HydrateOutcome
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Uygulama kapsamlı (appScope) hidrasyon izleyicisi. Detay yanıtı `hydrating: true` ise öğe buraya alınır;
 * her [intervalMs]'de (3 sn) `?poll=1` ile yoklanır (sunucuda iş BAŞLATMAZ), en fazla [timeoutMs] (120 sn).
 *
 *  - Aynı id için tek izleyici; en fazla [maxConcurrent] (3) id AYNI ANDA yoklanır, fazlası sırada bekler
 *    (süre sayacı yoklamaya başlayınca işler).
 *  - Ağ hatasında / çevrimdışıyken SESSİZCE durur (olay yok). Süre dolarsa da olay yok.
 *  - `hydrating` false olunca: [HydrateLogic.outcome] kararı -> [events] (Ready | Unavailable | NoSource).
 *    Yeni veri önbelleklere [poll] (repository) tarafından yazılır; olay bundan SONRA yayılır.
 *  - İzleyici silinmeden önce [active]'den düşer; ekranlar "yükleniyor"u buna bakarak gösterir/gizler.
 * Kalıcı değil (bellek içi). Toast odak çalmaz: yalnızca olay yayar.
 */
class HydrateWatcher(
    private val scope: CoroutineScope,
    /** (id, profileId) -> `?poll=1` detayı; hatada fırlatır. */
    private val poll: suspend (itemId: String, profileId: String) -> Detail,
    /** false ise yoklama yapılmadan durulur. */
    private val isOnline: () -> Boolean = { true },
    private val intervalMs: Long = HydrateLogic.POLL_INTERVAL_MS,
    private val timeoutMs: Long = HydrateLogic.TIMEOUT_MS,
    maxConcurrent: Int = HydrateLogic.MAX_CONCURRENT,
) {
    private val permits = Semaphore(maxConcurrent)

    private val _active = MutableStateFlow<Set<String>>(emptySet())

    /** Şu an izlenen (ya da sırada bekleyen) yapım kimlikleri. */
    val active: StateFlow<Set<String>> = _active.asStateFlow()

    private val _events = MutableSharedFlow<HydrateEvent>(extraBufferCapacity = 16, onBufferOverflow = BufferOverflow.DROP_OLDEST)

    /** Bitiş olayları; dinleyen yoksa kaybolur (kalıcı değil). */
    val events: SharedFlow<HydrateEvent> = _events.asSharedFlow()

    /**
     * [itemId]'yi izlemeye alır. Aynı id zaten izleniyorsa false (tek izleyici). İzleme eşzamanlı sınıra
     * takılırsa sırada bekler; [active] hemen güncellenir.
     */
    fun watch(itemId: String, title: String, profileId: String): Boolean {
        if (itemId.isBlank()) return false
        var added = false
        _active.update { current ->
            added = itemId !in current
            if (added) current + itemId else current
        }
        if (!added) return false
        scope.launch { run(itemId, title, profileId) }
        return true
    }

    private suspend fun run(itemId: String, title: String, profileId: String) {
        val done: Pair<HydrateOutcome, Detail>? = try {
            permits.withPermit {
                withTimeoutOrNull(timeoutMs) { pollUntilDone(itemId, profileId) }
            }
        } finally {
            _active.update { it - itemId }
        }
        if (done == null) return   // süre doldu ya da ağ hatası/çevrimdışı: sessiz
        val (outcome, detail) = done
        when (outcome) {
            HydrateOutcome.Ready -> _events.tryEmit(HydrateEvent.Ready(itemId, title))
            HydrateOutcome.Unavailable -> _events.tryEmit(HydrateEvent.Unavailable(itemId, title))
            HydrateOutcome.NoSource -> _events.tryEmit(HydrateEvent.NoSource(itemId, title, detail.item.isSeries))
            HydrateOutcome.Pending -> Unit
        }
    }

    /** Bitiş kararı + son detay (Ready/Unavailable/NoSource) ya da sessiz durma (ağ hatası / çevrimdışı) için null. */
    private suspend fun pollUntilDone(itemId: String, profileId: String): Pair<HydrateOutcome, Detail>? {
        while (true) {
            delay(intervalMs)
            if (!isOnline()) return null
            val detail = try {
                poll(itemId, profileId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return null
            }
            when (val outcome = HydrateLogic.outcome(detail)) {
                HydrateOutcome.Pending -> Unit
                else -> return outcome to detail
            }
        }
    }
}
