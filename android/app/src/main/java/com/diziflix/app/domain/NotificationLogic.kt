package com.diziflix.app.domain

import com.diziflix.app.data.model.AppNotification
import java.util.Locale

/** Gösterilecek tek toast: [target] doluysa tıklanabilen ekranlarda o yapımın detayına gidilir (özet toast'ta null). */
data class NotificationToast(val message: String, val target: AppNotification?)

/** Sunucu bildirimlerinin (API.md «Kaynak bulucu ve bildirimler») saf mantığı (birim testli). */
object NotificationLogic {
    /** Uygulama açıkken yoklama aralığı (oynatıcıdayken durur, çıkınca bir kez hemen sorulur). */
    const val POLL_INTERVAL_MS = 30_000L

    const val KIND_SOURCE_FOUND = "source_found"

    /** Tek yoklamada ayrı ayrı gösterilen en çok toast; fazlası tek özet toast'ta toplanır. */
    const val MAX_TOASTS = 3

    /** Oynatıcı rotasında yoklanmaz (sarmayı/oynatmayı bölmesin); diğer her ekranda yoklanır. */
    fun shouldPoll(route: String?): Boolean = route?.startsWith("player") != true

    /** "Kaynak bulundu: Dizi S01 B03" (film ya da sezon/bölüm bilinmiyorsa yalnız başlık). */
    fun message(n: AppNotification): String {
        val title = n.title.ifBlank { "Yapım" }
        val season = n.season
        val episode = n.episode
        val code = if (season != null && episode != null) " " + "S%02d B%02d".format(Locale.ROOT, season, episode) else ""
        return "Kaynak bulundu: $title$code"
    }

    /** Yeni `source_found` bildirimlerinden gösterilecek toast listesi (en çok [MAX_TOASTS] + varsa tek özet). */
    fun toasts(items: List<AppNotification>): List<NotificationToast> {
        val found = items.filter { it.kind == KIND_SOURCE_FOUND }
        val shown = found.take(MAX_TOASTS).map { NotificationToast(message(it), it) }
        val rest = found.size - shown.size
        return if (rest > 0) shown + NotificationToast("Kaynak bulundu: $rest yapım daha", null) else shown
    }
}
