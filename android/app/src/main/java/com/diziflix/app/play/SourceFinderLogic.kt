package com.diziflix.app.play

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay

/**
 * Kaynak bulucu (API.md «Kaynak bulucu ve bildirimler»): `/api/streams` akış çıkaramazsa yanıtta isteğe bağlı
 * `finder.state` (`searching` | `not_found`) gelir; oynatıcı hata paneli buna göre konuşur. Panelde kalınırsa durum ucu
 * (`/api/source-finder/{id}?episode=`) 5 sn'de bir yoklanır; `found` olunca akış otomatik yeniden istenir. Saf mantık, birim testli.
 */
object SourceFinderLogic {
    const val STATE_IDLE = "idle"
    const val STATE_SEARCHING = "searching"
    const val STATE_FOUND = "found"
    const val STATE_NOT_FOUND = "not_found"

    const val POLL_INTERVAL_MS = 5_000L

    /** Yoklamanın üst sınırı (5 sn x 120 = 10 dk): sonra panel olduğu gibi kalır, bildirim yine gelir. */
    const val MAX_POLLS = 120

    const val MSG_SEARCHING = "Kaynak aranıyor… Bulununca haber vereceğiz. Sayfada kalabilir ya da uygulamada gezinebilirsin."
    const val MSG_NOT_FOUND = "Bu bölüm için kaynak bulunamadı."

    /** Panelde kaynak bulunduğunda kısa süre görünen bilgi. */
    const val MSG_FOUND = "Kaynak bulundu, yeniden deneniyor"

    /** Hata paneli metni; `finder` yoksa ya da bilinmeyen değerse null (çağıran genel mesajı kullanır). */
    fun message(state: String?): String? = when (state) {
        STATE_SEARCHING -> MSG_SEARCHING
        STATE_NOT_FOUND -> MSG_NOT_FOUND
        else -> null
    }

    /**
     * Durumu [poll] ile, [intervalMs] aralıkla `searching` dışına çıkana kadar yoklar; yeni durumu döndürür
     * (`found` | `not_found` | `idle`). Geçici ağ/sunucu hatası yoklamayı DURDURMAZ (bir sonraki turda yeniden sorulur);
     * [maxPolls] dolarsa null. İptal normal şekilde yayılır.
     */
    suspend fun awaitResult(
        poll: suspend () -> String?,
        intervalMs: Long = POLL_INTERVAL_MS,
        maxPolls: Int = MAX_POLLS,
    ): String? {
        repeat(maxPolls) {
            delay(intervalMs)
            val state = try {
                poll()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (state != null && state != STATE_SEARCHING) return state
        }
        return null
    }
}
