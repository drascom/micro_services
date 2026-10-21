package com.diziflix.app.domain

import com.diziflix.app.data.model.Detail

/** Bir hidrasyon yoklamasının sonucu. */
enum class HydrateOutcome {
    /** Sunucu hâlâ çalışıyor: yoklamaya devam. */
    Pending,

    /** Bitti ve veri dolu (film ya da en az bir sezonlu dizi). */
    Ready,

    /** Bitti ama dizide sezon yok (kaynak site hatası): "alınamadı". */
    Unavailable,

    /** Bitti ve sunucu "izleme kaynağı yok" dedi (`availability.state == unavailable`, `reason == no_video_source`): gerçek bir hata DEĞİL. */
    NoSource,
}

/** İzleyicinin yaydığı olay (bitiş anında, süre dolma/ağ hatasında YAYILMAZ). */
sealed interface HydrateEvent {
    val id: String
    val title: String

    data class Ready(override val id: String, override val title: String) : HydrateEvent
    data class Unavailable(override val id: String, override val title: String) : HydrateEvent

    /** Henüz izleme kaynağı yok ("alınamadı" değil): [isSeries] metni "dizi"/"film" seçer. */
    data class NoSource(override val id: String, override val title: String, val isSeries: Boolean) : HydrateEvent
}

/** Detay "yükleniyor / hazır" sözleşmesinin saf mantığı (birim testli; docs: ortak hidrasyon sözleşmesi). */
object HydrateLogic {
    /** Yoklama aralığı, toplam süre ve eşzamanlı izleyici sınırı (Tizen ile aynı). */
    const val POLL_INTERVAL_MS = 3_000L
    const val TIMEOUT_MS = 120_000L
    const val MAX_CONCURRENT = 3

    /**
     * "Hazır" = `hydrating == false` VE (film ya da `seasons.length > 0`).
     * `hydrating == false` ama dizide sezon yoksa = "alınamadı".
     * Sunucu `unavailable` + `no_video_source` dediyse (hidrasyon bittikten sonra) = "kaynak yok" ([HydrateOutcome.NoSource]).
     */
    fun outcome(detail: Detail): HydrateOutcome = when {
        detail.hydrating -> HydrateOutcome.Pending
        DetailLogic.isNoSource(detail) -> HydrateOutcome.NoSource   // "alınamadı" değil: sunucu kaynak yok diyor
        !detail.item.isSeries -> HydrateOutcome.Ready
        detail.seasons.isNotEmpty() -> HydrateOutcome.Ready
        else -> HydrateOutcome.Unavailable
    }

    const val UNAVAILABLE_MESSAGE = "Bölümler şu an alınamadı, daha sonra tekrar deneyin"

    /** Genel bildirim metni: «Başlık» izlemeye hazır / alınamadı. */
    fun message(event: HydrateEvent): String = when (event) {
        is HydrateEvent.Ready -> "«${event.title.ifBlank { "Yapım" }}» izlemeye hazır"
        is HydrateEvent.Unavailable -> UNAVAILABLE_MESSAGE
        is HydrateEvent.NoSource -> DetailLogic.noSourceText(event.isSeries)
    }
}
