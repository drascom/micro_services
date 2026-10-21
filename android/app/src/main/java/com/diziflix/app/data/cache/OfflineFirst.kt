package com.diziflix.app.data.cache

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * "Önce önbellek, arkada yenile" sonucu.
 *  - [fromCache]: değer yerel depodan (true) ya da az önce ağdan (false) geldi.
 *  - [refreshing]: önbellekten gösterildi, ağ yenilemesi sürüyor.
 *  - [refreshError]: önbellekten gösterilirken ağ yenilemesi BAŞARISIZ oldu (değer korunur;
 *    ekranda çevrimdışı şeridi/uyarı gösterilir).
 */
data class Loaded<T>(
    val value: T,
    val fromCache: Boolean,
    val savedAtMs: Long,
    val refreshing: Boolean = false,
    val refreshError: Throwable? = null,
)

/**
 * Stale-while-revalidate akışı:
 *  1. [read] (varsa) önbellekteki değeri HEMEN yayar;
 *  2. [fetch] ağdan yeniler, [write] ile saklar ve taze değeri yayar;
 *  3. ağ başarısızsa ve önbellek varsa: yerel değer [Loaded.refreshError] ile yeniden yayılır (korunur);
 *     önbellek de yoksa hata akıştan fırlatılır (ekran net hata gösterir).
 * İptal (CancellationException) her zaman yukarı iletilir.
 */
fun <T> offlineFirst(
    read: suspend () -> Cached<T>?,
    write: suspend (T) -> Unit,
    fetch: suspend () -> T,
    clock: () -> Long = System::currentTimeMillis,
): Flow<Loaded<T>> = flow {
    val cached = try {
        read()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        null   // okunamayan önbellek = önbellek yok
    }
    if (cached != null) {
        emit(Loaded(cached.value, fromCache = true, savedAtMs = cached.savedAtMs, refreshing = true))
    }
    val fresh = try {
        fetch()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        if (cached == null) throw e
        emit(Loaded(cached.value, fromCache = true, savedAtMs = cached.savedAtMs, refreshError = e))
        return@flow
    }
    val now = clock()
    try {
        write(fresh)
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        // yazılamayan önbellek taze veriyi engellemez
    }
    emit(Loaded(fresh, fromCache = false, savedAtMs = now))
}
