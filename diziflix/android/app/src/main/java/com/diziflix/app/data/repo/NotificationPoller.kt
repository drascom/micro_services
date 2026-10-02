package com.diziflix.app.data.repo

import com.diziflix.app.data.model.AppNotification
import com.diziflix.app.data.model.NotificationsResponse
import com.diziflix.app.domain.NotificationLogic
import kotlinx.coroutines.CancellationException

/**
 * Sunucu bildirimlerinin tek yoklaması (zamanlama çağıranda: uygulama açıkken 30 sn, oynatıcıda durur).
 *  - İmleç (`since`) profil başına KALICIdır ([loadSince]/[saveSince]).
 *  - İlk çalıştırmada (imleç yok) eski bildirimler toast OLMAZ: yalnızca `last_id` imlece yazılır.
 *  - Yeni bildirimler [show]'a verilir; gösterildikten SONRA imleç ilerler ve `read` gönderilir (en iyi çaba).
 * Ağ/sunucu hatası [pollOnce]'tan fırlar (çağıran yutar; imleç ilerlemediği için sonraki yoklama yeniden dener).
 */
class NotificationPoller(
    private val fetch: suspend (profileId: String, since: Long) -> NotificationsResponse,
    private val markRead: suspend (profileId: String, upto: Long) -> Unit,
    private val loadSince: suspend (profileId: String) -> Long?,
    private val saveSince: suspend (profileId: String, since: Long) -> Unit,
) {
    suspend fun pollOnce(profileId: String, show: suspend (List<AppNotification>) -> Unit) {
        val since = loadSince(profileId)
        val response = fetch(profileId, since ?: 0L)
        if (since == null) {
            saveSince(profileId, response.lastId.coerceAtLeast(0L))
            return
        }
        val fresh = response.items.filter { it.id > since }
        if (fresh.isEmpty()) return
        val shown = fresh.filter { it.kind == NotificationLogic.KIND_SOURCE_FOUND }
        if (shown.isNotEmpty()) show(shown)
        val last = maxOf(response.lastId, fresh.maxOf { it.id })
        saveSince(profileId, last)
        try {
            markRead(profileId, last)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // okundu işareti en iyi çaba: imleç ilerledi, aynı bildirim yeniden gösterilmez
        }
    }
}
