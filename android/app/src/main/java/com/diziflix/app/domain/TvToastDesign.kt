package com.diziflix.app.domain

/*
 * Android TV bildirimleri — Tizen `js/ui/toast.js` + `css/base.css` (.toast, .toast-rich, .tr-*) karşılığı (saf Kotlin;
 * birim testli). Tablet/telefon Snackbar'ı bunlardan etkilenmez. Ölçüler Tizen px (ekrana `tvDp` ile çevrilir).
 */
object TvToastSpec {
    const val TOAST_MS = 3_500L          // toast.js TOAST_MS
    const val RICH_MS = 7_000L           // toast.js RICH_MS
    const val RICH_EXIT_MS = 400L        // toast.js RICH_EXIT_MS
    const val RICH_MAX_WAIT = 3          // toast.js RICH_MAX_WAIT (gösterilen hariç bekleyen)

    // ---- .toast (küçük): alttan 60, 800 genişlik, 26 px/700, 16/28 dolgu, 2 px kenar, 8 px köşe
    const val TOAST_W = 800f
    const val TOAST_BOTTOM = 60f
    const val TOAST_TEXT = 26f
    const val TOAST_PAD_V = 16f
    const val TOAST_PAD_H = 28f
    const val TOAST_RADIUS = 8f

    // ---- .toast-rich (zengin): üst-orta, 780 genişlik, üstten sahne yüksekliğinin %14'ü
    const val RICH_W = 780f
    const val RICH_TOP_FRACTION = 0.14f
    const val RICH_PAD_TOP = 52f
    const val RICH_PAD_H = 72f
    const val RICH_PAD_BOTTOM = 48f
    const val RICH_RADIUS = 14f
    const val RICH_BORDER = 2f
    const val RICH_LABEL = 24f           // .tr-label 24 px/800, harf aralığı .12em
    const val RICH_LABEL_MB = 10f
    const val RICH_TITLE = 52f           // .tr-title 52 px/900, satır 1.2, en çok 2 satır
    const val RICH_TITLE_LINE = 62.4f
    const val RICH_MSG = 28f             // .tr-msg 28 px/500, satır 1.35, üst boşluk 14
    const val RICH_MSG_LINE = 37.8f
    const val RICH_MSG_MT = 14f
    const val RICH_ENTER_MS = 350
    const val RICH_FADE_MS = 300

    // ---- .tr-orn (köşe süsü): 220x156, -16 taşma; sağ-alttaki 180° döner; uyarıda %50 opaklık
    const val ORN_W = 220f
    const val ORN_H = 156f
    const val ORN_OUT = 16f
    const val ORN_WARN_ALPHA = 0.5f

    // ---- renkler (ARGB): kart zemini rgba(31,31,31,.97), toast zemini rgba(20,20,20,.96)
    const val RICH_BG_ARGB = 0xF71F1F1F
    const val TOAST_BG_ARGB = 0xF5141414
    const val TOAST_BORDER_ARGB = 0x47FFFFFF     // rgba(255,255,255,.28)

    const val LABEL_SUCCESS = "İzlemeye hazır"
    const val LABEL_WARN = "Bilgi"
}

enum class TvRichKind { Success, Warn }

/** Zengin bildirim içeriği. */
data class TvRichItem(
    val kind: TvRichKind,
    val label: String,
    val title: String,
    val message: String,
    val ms: Long,
) {
    val hasMessage: Boolean get() = message.isNotEmpty()
}

object TvRichToasts {
    /** Tizen `showRich(o)`: etiket yoksa türe göre varsayılan; süre geçersizse [TvToastSpec.RICH_MS]. */
    fun of(
        title: String,
        message: String = "",
        kind: TvRichKind = TvRichKind.Success,
        label: String? = null,
        ms: Long = 0L,
    ): TvRichItem = TvRichItem(
        kind = kind,
        label = label?.takeIf { it.isNotEmpty() } ?: if (kind == TvRichKind.Warn) TvToastSpec.LABEL_WARN else TvToastSpec.LABEL_SUCCESS,
        title = title,
        message = message,
        ms = if (ms > 0L) ms else TvToastSpec.RICH_MS,
    )

    /**
     * Hidrasyon olayı -> zengin bildirim (hydrate_watch.js `richFor`): Hazır = «başlık» + "İzlemeye hazır";
     * Alınamadı = başlık + sunucu hatası metni, uyarı türü; Kaynak yok = başlık + "Bu dizi/film için henüz izleme kaynağı yok.",
     * uyarı türü ("alınamadı" DEĞİL). Başlık boşsa "İçerik".
     */
    fun forHydrate(event: HydrateEvent): TvRichItem {
        val title = event.title.ifBlank { "İçerik" }
        return when (event) {
            is HydrateEvent.Ready -> of(title, "", TvRichKind.Success)
            is HydrateEvent.Unavailable -> of(title, HydrateLogic.UNAVAILABLE_MESSAGE, TvRichKind.Warn)
            is HydrateEvent.NoSource -> of(title, DetailLogic.noSourceText(event.isSeries), TvRichKind.Warn)
        }
    }
}

/**
 * Zengin bildirim kuyruğu (toast.js `richBusy` / `richQueue`): aynı anda TEK kart; gelenler sırayla; en çok
 * [TvToastSpec.RICH_MAX_WAIT] bekleyen (fazlası düşer -> false).
 */
class TvRichQueue(private val maxWait: Int = TvToastSpec.RICH_MAX_WAIT) {
    private val waiting = ArrayDeque<TvRichItem>()

    /** Bir kart gösteriliyor ya da çıkış aralığında (yeni gelen bekler). */
    var busy: Boolean = false
        private set

    val waitingCount: Int get() = waiting.size

    /** true: kabul edildi (hemen gösterilecek ya da sıraya alındı); false: kuyruk dolu. */
    fun offer(item: TvRichItem): Boolean {
        if (!busy && waiting.isEmpty()) {
            waiting.addLast(item)
            return true
        }
        if (waiting.size >= maxWait) return false
        waiting.addLast(item)
        return true
    }

    /** Gösterilecek sıradaki kart (yoksa null ve meşgul bayrağı kalkar). */
    fun next(): TvRichItem? {
        val item = waiting.removeFirstOrNull()
        busy = item != null
        return item
    }

    /** Görünen kartı kapat, bekleyenleri at (toast.js `hideRich`). */
    fun clear() {
        waiting.clear()
        busy = false
    }
}
