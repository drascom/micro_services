package com.diziflix.app.domain

import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Item
import java.util.Locale
import kotlin.math.ceil

/*
 * Android TV ekranları (Aşama B: Ayarlar, Profil seçimi, Katalog/Arama, Oynatıcı) tasarım mantığı — saf Kotlin, birim
 * testli. Kaynak: Tizen `css/{settings,profiles,home(catalog),player}.css`, `js/screens/{settings,profiles,catalog,player}.js`,
 * `js/ui/{tracks_panel,modal}.js`. Ölçüler Tizen px (ekrana `tvDp` ile çevrilir). Tablet yolu bunlardan etkilenmez.
 */

// ---------------------------------------------------------------------------------------------- Ayarlar

object TvSettingsSpec {
    const val PAD_TOP = 36f               // .settings{padding:36px 80px 0}
    const val PAD_H = 80f
    const val TOP_ROW_H = 64f             // .set-top
    const val TITLE = 48f                 // .set-title 48 px/900, marjin 14 0 22
    const val TITLE_MT = 14f
    const val TITLE_MB = 22f
    const val CARD_RADIUS = 20f           // .set-card
    const val CARD_BORDER = 2f
    const val CARD_PAD_V = 24f
    const val CARD_PAD_H = 28f
    const val CARD_TITLE = 26f
    const val HINT = 20f                  // .set-hint
    const val HINT_MIN_H = 28f
    const val STATUS = 22f                // .set-status
    const val STATUS_MIN_H = 30f
    const val FIELD_H = 72f               // .set-field
    const val FIELD_RADIUS = 14f
    const val FIELD_BORDER = 3f
    const val FIELD_MR = 20f
    const val INPUT_TEXT = 26f
    const val BTN_H = 60f                 // .set-btn
    const val BTN_TEXT = 24f
    const val BTN_PAD_H = 30f
    const val BTN_RADIUS = 12f
    const val BTN_BORDER = 4f
    const val BTN_TOP_H = 52f             // .set-btn-top
    const val BTN_TOP_TEXT = 22f
    const val BTN_TOP_PAD_H = 26f
    const val BTN_INLINE_H = 52f          // .set-btn-inline
    const val BTN_INLINE_PAD_H = 22f
    const val BTN_INLINE_RADIUS = 10f
    const val BTN_INLINE_MR = 8f
    const val COLS_MT = 24f
    const val COLS_GAP = 24f
    const val OPT_H = 68f                 // .opt
    const val OPT_MB = 10f
    const val OPT_PAD_H = 24f
    const val OPT_TEXT = 28f
    const val OPT_RADIUS = 12f
    const val OPT_BORDER = 4f
    const val MARK = 28f                  // .opt-mark
    const val MARK_BORDER = 3f
    const val MARK_DOT = 12f
    const val MARK_MR = 20f
    const val FOOT_MT = 24f
    const val INFO = 20f                  // .set-info 20 px, satır 1.45
    const val INFO_LINE = 29f
    const val INFO_MT = 20f
    const val FOCUS_SCALE = 1.05f

    const val SUB_TITLE = "Varsayılan altyazı dili"
    const val QUALITY_TITLE = "En yüksek kalite"
    const val QUALITY_HINT = "bu çözünürlüğün üstündeki akışlar en son denenir"
    const val NEED_PROFILE = "Önce bir profil seçin"
    const val SERVER_TITLE = "Sunucu adresi"
}

object TvSettingsLogic {
    /** Varsayılan altyazı seçiminin durum metni ("Varsayılan altyazı: Türkçe"). */
    fun subStatus(value: String): String {
        val name = PlayPrefs.SUB_OPTIONS.firstOrNull { it.first == value }?.second ?: value
        return "Varsayılan altyazı: $name"
    }

    /** Kalite seçiminin durum metni ("En yüksek kalite: 1080p" / "Otomatik (sınırsız)"). */
    fun qualityStatus(value: String): String =
        "En yüksek kalite: " + if (value == PlayPrefs.QUALITY_AUTO) "Otomatik (sınırsız)" else "${value}p"

    fun buildInfo(version: String): String = "Sürüm: $version   ·   Motor: Media3 ExoPlayer"

    fun addressInfo(baseUrl: String, defaultUrl: String): String = "Sunucu adresi: $baseUrl   ·   Varsayılan: $defaultUrl"

    const val SERVER_PENDING = "Sunucu: bilgi alınıyor…"
    const val SERVER_DOWN = "Sunucu: ulaşılamıyor"

    fun serverInfo(source: String?, items: Int?): String =
        "Sunucu: ${source ?: "?"} · ${items?.toString() ?: "?"} içerik"
}

// ---------------------------------------------------------------------------------------------- Profil seçimi

object TvProfilesSpec {
    const val WORDMARK = 56f              // .wordmark.big (56 px, 0.26em), altı 48
    const val WORDMARK_MB = 48f
    const val TITLE = 44f                 // .profiles h1 44 px/400 #e5e5e5, altı 48
    const val TITLE_MB = 48f
    const val PROFILE_W = 240f            // .profile
    const val PROFILE_MX = 24f
    const val AVATAR = 200f               // .profile .av 200x200, 8 px köşe, 5 px kenar
    const val AVATAR_RADIUS = 8f
    const val AVATAR_BORDER = 5f
    const val AVATAR_MB = 18f
    const val INITIAL = 72f               // avatarda baş harf 72 px/900 vurgu rengi
    const val NAME = 24f
    const val KIDS = 18f                  // .kids 18 px, harf aralığı .1em
    const val FOCUS_SCALE = 1.1f
    const val FOOT_MT = 56f
    const val AVATAR_IMG = 200
}

/**
 * Marka görselleri (docs/brand -> res/drawable-nodpi/brand_xxx.png, 2x keskinlik; Tizen `img` klasöründeki PNG'lerle aynı). Boyutlar Tizen px
 * (bkz. `tizen-client/css/{profiles,home,base}.css`); PNG oranlarıyla uyumludur (test: TvBrandTest).
 */
object TvBrandSpec {
    const val WIDE_W = 360f               // brand_logo_wide 720x710 -> profil seçimi (.profiles .brand-logo)
    const val WIDE_H = 355f
    const val WIDE_MB = 4f
    const val SQUARE_W = 316f             // brand_logo_square 630x640 -> açılış/yükleme (.boot-logo-img)
    const val SQUARE_H = 320f
    const val BOOT_PLATE_W = 388f         // .boot-logo: 388x368, 32 köşe, rgba(20,20,20,.9)
    const val BOOT_PLATE_H = 368f
    const val BOOT_PLATE_RADIUS = 32f
    const val MASCOT_LOAD_W = 155f        // brand_mascot 310x240 -> oynatma yükleniyor (.loading-mascot), altı 28
    const val MASCOT_LOAD_H = 120f
    const val MASCOT_LOAD_MB = 28f
    const val MASCOT_ERR_W = 194f         // hata ekranları (.brand-mascot), altı 12
    const val MASCOT_ERR_H = 150f
    const val MASCOT_ERR_MB = 12f
    const val EMBLEM_W = 62f              // brand_mascot_sm 124x96 -> üst menüde DIZIFLIX yazısının solu (.brand-emblem), sağı 14
    const val EMBLEM_H = 48f
    const val EMBLEM_MR = 14f

    // PNG piksel boyutları (en-boy oranı denetimi için)
    const val WIDE_PX_W = 720
    const val WIDE_PX_H = 710
    const val SQUARE_PX_W = 630
    const val SQUARE_PX_H = 640
    const val MASCOT_PX_W = 310
    const val MASCOT_PX_H = 240
    const val EMBLEM_PX_W = 124
    const val EMBLEM_PX_H = 96
}

object TvProfilesLogic {
    /** Avatar yer tutucusu: adın baş harfi (büyük), boşsa "?". */
    fun initials(name: String?): String {
        val s = (name ?: "?").trim()
        return if (s.isEmpty()) "?" else s.substring(0, s.offsetByCodePoints(0, 1)).uppercase(Locale("tr"))
    }

    const val KIDS_BADGE = "ÇOCUK"
}

// ---------------------------------------------------------------------------------------------- Katalog / Arama

object TvCatalogSpec {
    const val CONTENT_TOP_PAD = 40f       // .catalog-page padding-top 160 - üst menü 120
    const val PAGE_PAD_H = 60f
    const val PAGE_PAD_BOTTOM = 140f
    const val H1 = 48f                    // .catalog-heading h1 (satır 1.35), alt 10
    const val H1_LINE = 64.8f
    const val H1_MB = 10f
    const val SUB = 26f                   // .catalog-heading p
    const val SUB_LINE = 35.1f
    const val FILTERS_MT = 28f            // .catalog-filters
    const val FILTERS_MB = 36f
    const val FILTER_GAP = 12f
    const val COLUMNS = 5
    const val TILE_W = 300f               // .catalog-tile; kart 300x450
    const val CARD_H = 450f
    const val GRID_GAP = 60f
    const val ROW_MT = 24f
    const val ROW_MB = 42f
    const val TITLE = 26f                 // .catalog-title 26 px/700, üst 14
    const val TITLE_LINE = 35.1f
    const val TITLE_MT = 14f
    const val META = 22f                  // .catalog-meta, üst 6
    const val META_LINE = 29.7f
    const val META_MT = 6f
    const val STATUS = 21f                // .catalog-status #e1c576, üst 5
    const val STATUS_LINE = 28.35f
    const val STATUS_MT = 5f
    const val TAG = 20f                   // arama kartı kaynak etiketi (source_options): küçük hap, üst 8
    const val TAG_LINE = 26f
    const val TAG_MT = 8f
    const val TAG_GAP = 8f
    const val TAG_PAD_H = 10f
    const val TAG_PAD_V = 3f
    const val FOOTER = 22f                // arama: "Bazı kaynaklar yanıt vermedi: ..." silik satır
    const val FOOTER_LINE = 29.7f
    const val FOOTER_MT = 24f
    const val FOCUS_SCALE = 1.04f         // .catalog-tile .card.focused
    const val CARD_RADIUS = 4f
    const val IMG_W = 300
    const val IMG_H = 450

    /** nav.js scrollPage: odaktaki satır ekranda y=140'a oturur; üst menü (120) ayrı olduğundan içerikte 20. */
    const val PIN_Y = 20f
    const val LOADING_PAD_V = 180f        // .catalog-loading
    const val LOADING_TEXT = 32f
    const val EMPTY_PAD_V = 100f
    const val SPINNER = 46f
    const val SPINNER_BORDER = 6f
    const val SCROLL_ANIM_MS = 200
}

sealed interface TvGridPos {
    data class Filters(val col: Int) : TvGridPos
    data class Grid(val index: Int) : TvGridPos
}

sealed interface TvGridMove {
    data class To(val pos: TvGridPos) : TvGridMove
    data object ToTopBar : TvGridMove
    data object Stay : TvGridMove
}

object TvCatalogLogic {
    const val EMPTY_HINT = "Aramak için bir film veya dizi adı girin."
    const val LOADING_TEXT = "Katalog yükleniyor…"
    const val SEARCH_WAIT = "Aranıyor…"
    const val SEARCH_EMPTY = "Sonuç bulunamadı."
    const val FILTERS_EMPTY = "Bu filtrelerde içerik bulunamadı."
    const val MYLIST_EMPTY = "Listeniz boş. Bir yapımın ayrıntısından \"Listeme Ekle\" ile ekleyebilirsiniz."

    fun rowCount(itemCount: Int, columns: Int = TvCatalogSpec.COLUMNS): Int =
        if (itemCount <= 0) 0 else ceil(itemCount.toDouble() / columns).toInt()

    /** "N yapım" (+ ` · “sorgu”`); aramada ` · Kaynakta aranıyor…` ve sunucu notu eklenir. */
    fun subtitle(total: Int, query: String?, searching: Boolean = false, note: String? = null): String {
        val sb = StringBuilder("$total yapım")
        if (!query.isNullOrEmpty()) sb.append(" · “").append(query).append('”')
        if (searching) sb.append(" · Kaynakta aranıyor…")
        if (!note.isNullOrEmpty()) sb.append(" · ").append(note)
        return sb.toString()
    }

    /** Kartın altındaki meta: "2021 · Dizi" ("Yıl bilinmiyor · Film"). */
    fun tileMeta(item: Item): String =
        (item.year?.toString() ?: "Yıl bilinmiyor") + " · " + if (item.isSeries) "Dizi" else "Film"

    /**
     * Öğeyi (kendi üst boşluğuyla) LazyColumn'da sabit üst yuvaya oturtmak için `scrollOffset` (Tizen px): öğenin
     * üst kenarı `PIN_Y - marginTop`'ta durur ki içeriği (boşluk hariç) y=[TvCatalogSpec.PIN_Y]'de başlasın.
     */
    fun pinOffset(marginTop: Float): Float = marginTop - TvCatalogSpec.PIN_Y

    /** Odak listenin sonuna iki satır kala sonraki sayfa istenir. */
    fun shouldLoadMore(focusRow: Int, rows: Int, loaded: Int, total: Int): Boolean =
        loaded in 1 until total && focusRow >= rows - 2

    /** Izgara + filtre satırı gezinmesi (sütun korunur; eksik son satırda son kart). */
    fun move(
        pos: TvGridPos,
        dir: TvDir,
        filterCount: Int,
        itemCount: Int,
        lastGrid: Int,
        filtersCol: Int,
        columns: Int = TvCatalogSpec.COLUMNS,
    ): TvGridMove = when (pos) {
        is TvGridPos.Filters -> when (dir) {
            TvDir.Left -> if (pos.col > 0) TvGridMove.To(TvGridPos.Filters(pos.col - 1)) else TvGridMove.Stay
            TvDir.Right -> if (pos.col < filterCount - 1) TvGridMove.To(TvGridPos.Filters(pos.col + 1)) else TvGridMove.Stay
            TvDir.Up -> TvGridMove.ToTopBar
            TvDir.Down -> if (itemCount > 0) TvGridMove.To(TvGridPos.Grid(lastGrid.coerceIn(0, itemCount - 1))) else TvGridMove.Stay
        }
        is TvGridPos.Grid -> {
            val i = pos.index
            val col = i % columns
            when (dir) {
                TvDir.Left -> if (col > 0) TvGridMove.To(TvGridPos.Grid(i - 1)) else TvGridMove.Stay
                TvDir.Right -> if (col < columns - 1 && i + 1 < itemCount) TvGridMove.To(TvGridPos.Grid(i + 1)) else TvGridMove.Stay
                TvDir.Up -> when {
                    i >= columns -> TvGridMove.To(TvGridPos.Grid(i - columns))
                    filterCount > 0 -> TvGridMove.To(TvGridPos.Filters(filtersCol.coerceIn(0, filterCount - 1)))
                    else -> TvGridMove.ToTopBar
                }
                TvDir.Down -> when {
                    i + columns < itemCount -> TvGridMove.To(TvGridPos.Grid(i + columns))
                    (i / columns + 1) * columns < itemCount -> TvGridMove.To(TvGridPos.Grid(itemCount - 1))
                    else -> TvGridMove.Stay
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------- Oynatıcı

object TvPlayerSpec {
    const val SAFE = 60f
    const val TOP_TITLE = 44f             // .pl-title 44 px/900, en çok 1500
    const val TOP_TITLE_MAX_W = 1500f
    const val TOP_EP = 30f                // .pl-ep 30 px/700 #e5e5e5, üst 6
    const val TOP_EP_MT = 6f
    const val TOP_SUB = 24f               // .pl-sub 24 px dim, üst 8
    const val TOP_SUB_MT = 8f
    const val BOTTOM_PAD_TOP = 120f       // .pl-bottom padding: 120 60 56
    const val BOTTOM_PAD_BOTTOM = 56f
    const val BAR_H = 8f
    const val KNOB = 24f
    const val TIMES = 24f
    const val TIMES_MT = 16f
    const val STATE = 24f                 // .pl-state 24 px, harf aralığı .08em, üst 22, en az 32
    const val STATE_MT = 22f
    const val STATE_MIN_H = 32f
    const val CTL_BTN_H = 56f             // .pl-tbtn / .pl-qbtn
    const val CTL_BTN_TEXT = 26f
    const val CTL_BTN_PAD_H = 26f
    const val CTL_BTN_RADIUS = 6f
    const val CTL_BTN_BORDER = 4f
    const val CTL_GAP = 16f
    const val QMENU_MIN_W = 220f          // .pl-qmenu
    const val QMENU_BOTTOM = 70f
    const val QITEM_TEXT = 26f
    const val QITEM_PAD_V = 12f
    const val QITEM_PAD_H = 28f
    const val NEXT_W = 560f               // .pl-next
    const val NEXT_PAD_V = 26f
    const val NEXT_PAD_H = 32f
    const val NEXT_TITLE = 30f
    const val NEXT_LABEL = 22f
    const val NEXT_HINT = 20f
    const val TP_LEFT = 360f              // .pl-tp-box
    const val TP_TOP = 200f
    const val TP_W = 1200f
    const val TP_PAD_TOP = 40f
    const val TP_PAD_H = 56f
    const val TP_PAD_BOTTOM = 32f
    const val TP_TITLE = 38f
    const val TP_HEAD = 24f
    const val TP_ITEM = 28f
    const val TP_ITEM_PAD_V = 12f
    const val TP_ITEM_PAD_H = 24f
    const val TP_BADGE = 20f
    const val TP_COL_MIN_H = 320f
    const val TP_NOTE = 24f
    const val TP_HINT = 22f
    const val LOAD_SPINNER = 96f          // .loading-spinner
    const val LOAD_SPINNER_BORDER = 10f
    const val LOAD_SPINNER_MB = 60f
    const val LOAD_BOX_W = 1200f
    const val LOAD_PROVERB = 40f          // .loading-proverb 40 px/700, satır 1.4, en az 112
    const val LOAD_PROVERB_MIN_H = 112f
    const val LOAD_STAGE = 24f
    const val LOAD_STAGE_MT = 40f
    const val LOAD_STAGE_MIN_H = 34f
    const val HIDE_MS = 3_000L
    const val NEXT_WINDOW_S = 45
    const val NEXT_MIN_DURATION_S = 600
}

object TvPlayerLogic {
    /** İlerleme oranı (0..1). */
    fun fraction(positionMs: Long, durationMs: Long): Float =
        if (durationMs > 0L) (positionMs.toFloat() / durationMs.toFloat()).coerceIn(0f, 1f) else 0f

    /** Sağ zaman: "-12:30" (süre bilinmiyorsa boş). */
    fun remainingText(positionMs: Long, durationMs: Long): String =
        if (durationMs > 0L) "-" + Format.clock((durationMs - positionMs).coerceAtLeast(0L)) else ""

    /** Başlık altı bölüm satırı ("S04 B02 · Ad"); film/fragmanda boş. */
    fun episodeLine(episodeLabel: String): String = episodeLabel

    /** Sarma göstergesi (Tizen `>> 10 sn` / `<< 10 sn`): yön oku + süre ("1 dk" tam dakikada). */
    fun seekHint(deltaMs: Long): String {
        val secs = kotlin.math.abs(deltaMs) / 1000L
        val amount = if (secs >= 60L && secs % 60L == 0L) "${secs / 60L} dk" else "$secs sn"
        return (if (deltaMs >= 0L) ">> " else "<< ") + amount
    }

    fun tracksButtonLabel(subLabel: String): String = "Ses ve Altyazılar · $subLabel"

    fun sourceButtonLabel(label: String): String = "Kaynak / kalite: ${label.ifBlank { "-" }}"

    /** "Sonraki bölüm" kartındaki bölüm satırı: "S04 B02 · Ad". */
    fun nextLabel(ep: Episode): String {
        val tag = TvDetailLogic.epTag(ep.season, ep.episode)
        return if (ep.title.isBlank()) tag else "$tag · ${ep.title}"
    }

    /**
     * Bölümün son [TvPlayerSpec.NEXT_WINDOW_S] sn'sine girildi mi (Tizen `checkNext`): süre en az
     * [TvPlayerSpec.NEXT_MIN_DURATION_S]; geri sarılıp pencere dışına çıkılınca (5 sn pay) teklif kapanır.
     * Dönüş: true = göster, false = gizle, null = mevcut durumu koru.
     */
    fun upcomingDecision(durationMs: Long, positionMs: Long): Boolean? {
        if (durationMs < TvPlayerSpec.NEXT_MIN_DURATION_S * 1000L) return null
        val remainS = (durationMs - positionMs) / 1000.0
        return when {
            remainS > 0 && remainS <= TvPlayerSpec.NEXT_WINDOW_S -> true
            remainS > TvPlayerSpec.NEXT_WINDOW_S + 5 -> false
            else -> null
        }
    }

    const val NEXT_TITLE = "Sonraki bölüm"
    const val NEXT_NOW = "Şimdi oynat"
    const val NEXT_HINT_WINDOW = "Bölüm bitince otomatik başlar · GERİ: iptal"
    const val NEXT_HINT_ENDED = "Birazdan başlıyor · GERİ: iptal"

    fun nextNowLabel(countdown: Int?): String = if (countdown != null) "$NEXT_NOW ($countdown)" else NEXT_NOW

    const val STATE_PAUSED = "DURAKLATILDI"
    const val TRACKS_HINT = "◄ ► sütun · ▲ ▼ seç · OK uygula · GERİ kapat"

    /** Seçilebilir hiç seçenek yokken (tek ses / yalnız "Kapalı"): odak "Kapat" düğmesindedir. */
    const val TRACKS_HINT_EMPTY = "OK veya GERİ: kapat"
    const val CLOSE_LABEL = "Kapat"
    const val PANEL_TITLE = "Ses ve Altyazılar"
    const val SUB_OFF = "Kapalı"
}

/** Oynatıcı tuş eylemleri (Tizen player.js `key`): Yukarı = kaynak/kalite menüsü, Aşağı/Sarı = Ses ve Altyazılar paneli. */
enum class TvPlayerAction { TogglePlay, Play, Pause, SeekBack, SeekForward, OpenSources, OpenTracks, ShowControls, Ignore }

object TvPlayerKeys {
    const val KEYCODE_PROG_YELLOW = 185

    fun actionFor(keyCode: Int): TvPlayerAction = when (keyCode) {
        PlayerKeys.KEYCODE_DPAD_CENTER, PlayerKeys.KEYCODE_ENTER, PlayerKeys.KEYCODE_NUMPAD_ENTER,
        PlayerKeys.KEYCODE_MEDIA_PLAY_PAUSE -> TvPlayerAction.TogglePlay
        PlayerKeys.KEYCODE_MEDIA_PLAY -> TvPlayerAction.Play
        PlayerKeys.KEYCODE_MEDIA_PAUSE -> TvPlayerAction.Pause
        PlayerKeys.KEYCODE_DPAD_LEFT, PlayerKeys.KEYCODE_MEDIA_REWIND -> TvPlayerAction.SeekBack
        PlayerKeys.KEYCODE_DPAD_RIGHT, PlayerKeys.KEYCODE_MEDIA_FAST_FORWARD -> TvPlayerAction.SeekForward
        PlayerKeys.KEYCODE_DPAD_UP -> TvPlayerAction.OpenSources
        PlayerKeys.KEYCODE_DPAD_DOWN, KEYCODE_PROG_YELLOW -> TvPlayerAction.OpenTracks
        PlayerKeys.KEYCODE_BACK -> TvPlayerAction.Ignore
        else -> TvPlayerAction.ShowControls
    }
}

/** Kaynak/kalite menüsü (`.pl-qmenu`): Yukarı/Aşağı sarmalı gezinir (Tizen `moveQ`). */
object TvSourceMenu {
    fun move(hover: Int, dir: Int, count: Int): Int =
        if (count <= 0) 0 else ((hover + dir) % count + count) % count
}

/** "Ses ve Altyazılar" panelindeki bir seçenek. */
data class TvPanelItem(val label: String, val current: Boolean)

/** Panel durumu: hangi sütun (0 = SES, 1 = ALTYAZI) ve sütun başına odak indeksi. */
data class TvTracksPanelState(val col: Int, val audioIndex: Int, val subIndex: Int)

sealed interface TvTracksPanelKey {
    data object Close : TvTracksPanelKey
    data class Select(val column: Int, val index: Int) : TvTracksPanelKey
}

/** `ui/tracks_panel.js` eşdeğeri (saf): tek (ya da hiç) seçenekli sütun pasif (gri, odaklanmaz). */
object TvTracksPanel {
    fun isActive(items: List<TvPanelItem>, dim: Boolean): Boolean = !dim && items.isNotEmpty()

    private fun currentIndex(items: List<TvPanelItem>): Int = items.indexOfFirst { it.current }.coerceAtLeast(0)

    /** Açılış: odak indeksleri seçili seçeneklerde; ALTYAZI sütunu aktifse onda, değilse SES, ikisi de değilse ALTYAZI. */
    fun open(audio: List<TvPanelItem>, audioDim: Boolean, subs: List<TvPanelItem>, subsDim: Boolean): TvTracksPanelState {
        val subActive = isActive(subs, subsDim)
        val audioActive = isActive(audio, audioDim)
        return TvTracksPanelState(
            col = if (subActive) 1 else if (audioActive) 0 else 1,
            audioIndex = currentIndex(audio),
            subIndex = currentIndex(subs),
        )
    }

    /** Seçim uygulandıktan sonra liste boyu değiştiyse indeksleri sıkıştırır. */
    fun clamp(state: TvTracksPanelState, audioCount: Int, subCount: Int): TvTracksPanelState = state.copy(
        audioIndex = state.audioIndex.coerceIn(0, (audioCount - 1).coerceAtLeast(0)),
        subIndex = state.subIndex.coerceIn(0, (subCount - 1).coerceAtLeast(0)),
    )

    /** Yukarı/Aşağı: etkin sütunda bir öğe (sınırlı). */
    fun move(state: TvTracksPanelState, dir: Int, audioCount: Int, subCount: Int, audioActive: Boolean, subActive: Boolean): TvTracksPanelState {
        val active = if (state.col == 0) audioActive else subActive
        if (!active) return state
        return if (state.col == 0) {
            state.copy(audioIndex = (state.audioIndex + dir).coerceIn(0, (audioCount - 1).coerceAtLeast(0)))
        } else {
            state.copy(subIndex = (state.subIndex + dir).coerceIn(0, (subCount - 1).coerceAtLeast(0)))
        }
    }

    /** Sol/Sağ: hedef sütun aktifse geçer. */
    fun toColumn(state: TvTracksPanelState, col: Int, audioActive: Boolean, subActive: Boolean): TvTracksPanelState {
        if (state.col == col) return state
        val active = if (col == 0) audioActive else subActive
        return if (active) state.copy(col = col) else state
    }

    /** Tamam: etkin sütundaki odaklı seçeneği uygula (sütun pasifse null). */
    fun select(state: TvTracksPanelState, audioActive: Boolean, subActive: Boolean): TvTracksPanelKey.Select? {
        val active = if (state.col == 0) audioActive else subActive
        if (!active) return null
        return TvTracksPanelKey.Select(state.col, if (state.col == 0) state.audioIndex else state.subIndex)
    }
}

/**
 * "Ses ve Altyazılar" panelinde odaklanabilir bir seçenek VAR mı (en az bir sütun etkin)? Yoksa odak panelin "Kapat"
 * düğmesine düşer: tek ses + yalnız "Kapalı" altyazı gibi durumlarda panel odaksız (ve çıkışsız) kalmasın.
 */
object TvTracksPanelFocus {
    fun hasOption(audioActive: Boolean, subActive: Boolean): Boolean = audioActive || subActive

    /** Panel açıkken iz listesi değişip odaktaki sütun pasifleştiyse etkin olana geçer (hiçbiri yoksa aynı kalır). */
    fun normalize(state: TvTracksPanelState, audioActive: Boolean, subActive: Boolean): TvTracksPanelState {
        val current = if (state.col == 0) audioActive else subActive
        if (current) return state
        return when {
            state.col == 0 && subActive -> state.copy(col = 1)
            state.col == 1 && audioActive -> state.copy(col = 0)
            else -> state
        }
    }

    /** Tamam: etkin sütunda odaklı seçenek varsa uygula; yoksa ("Kapat" odakta) paneli kapat. */
    fun activate(state: TvTracksPanelState, audioActive: Boolean, subActive: Boolean): TvTracksPanelKey =
        TvTracksPanel.select(normalize(state, audioActive, subActive), audioActive, subActive) ?: TvTracksPanelKey.Close
}

/** Oynatıcıda açık katman: kaynak/kalite menüsü ya da ses/altyazı paneli (ikisi birlikte açık olmaz). */
enum class TvOverlay { None, Sources, Tracks }

/** Açık bir katmanda bir tuş olayının sonucu. */
enum class TvOverlayKey {
    Up, Down, Left, Right,
    /** Tamam / oynat-duraklat: odaktaki seçeneği uygula. */
    Select,
    /** Geri (ve panelde Sarı): katmanı kapat. */
    Close,
    /** Olayı tüket ama bir şey yapma (bırakma olayları, tekrarlar, katmanın anlamsız tuşları). */
    Swallow,
    /** Katmanın işi yok: olay dokunulmadan sistem/Activity'ye geçsin (ses tuşları vb.). */
    PassThrough,
}

/**
 * Katman açıkken tuş yönlendirme tablosu (saf). Geçmiş kusur: katman her tuşu (Geri dâhil) tüketiyordu; Geri'nin
 * KeyDown'u yutulunca Activity/BackHandler hiç tetiklenmiyor, panel kapanmıyordu. Artık Geri açıkça [TvOverlayKey.Close].
 */
object TvPlayerOverlayKeys {
    const val KEYCODE_ESCAPE = 111

    private fun isSelect(code: Int) = TvPlayerKeys.actionFor(code).let { it == TvPlayerAction.TogglePlay || it == TvPlayerAction.Play }

    private fun isTransport(code: Int) =
        TvPlayerKeys.actionFor(code).let { it == TvPlayerAction.Pause || it == TvPlayerAction.SeekBack || it == TvPlayerAction.SeekForward }

    fun route(overlay: TvOverlay, keyCode: Int, isDown: Boolean, repeatCount: Int = 0): TvOverlayKey {
        if (overlay == TvOverlay.None) return TvOverlayKey.PassThrough
        val closeKey = keyCode == PlayerKeys.KEYCODE_BACK || keyCode == KEYCODE_ESCAPE ||
            (overlay == TvOverlay.Tracks && keyCode == TvPlayerKeys.KEYCODE_PROG_YELLOW)
        return when {
            closeKey -> if (isDown && repeatCount == 0) TvOverlayKey.Close else TvOverlayKey.Swallow
            keyCode == PlayerKeys.KEYCODE_DPAD_UP -> if (isDown) TvOverlayKey.Up else TvOverlayKey.Swallow
            keyCode == PlayerKeys.KEYCODE_DPAD_DOWN -> if (isDown) TvOverlayKey.Down else TvOverlayKey.Swallow
            keyCode == PlayerKeys.KEYCODE_DPAD_LEFT -> if (isDown) TvOverlayKey.Left else TvOverlayKey.Swallow
            keyCode == PlayerKeys.KEYCODE_DPAD_RIGHT -> if (isDown) TvOverlayKey.Right else TvOverlayKey.Swallow
            isSelect(keyCode) -> if (isDown && repeatCount == 0) TvOverlayKey.Select else TvOverlayKey.Swallow
            // Sarma/duraklatma tuşları katmanın arkasındaki videoyu oynatmasın: yutulur. Diğerleri (ses vb.) geçer.
            isTransport(keyCode) -> TvOverlayKey.Swallow
            else -> TvOverlayKey.PassThrough
        }
    }
}

/** Açık katmanın durumu: ses/altyazı paneli ([panel]) YA DA kaynak/kalite menüsü ([menuHover]); ikisi de null = katman yok. */
data class TvOverlayModel(val panel: TvTracksPanelState? = null, val menuHover: Int? = null) {
    val overlay: TvOverlay
        get() = when {
            panel != null -> TvOverlay.Tracks
            menuHover != null -> TvOverlay.Sources
            else -> TvOverlay.None
        }
}

/** Katman tuşu yönlendirmesi için gereken liste durumu (panel/menü sütun etkinlikleri ve sayıları). */
data class TvOverlayContext(
    val audioCount: Int,
    val subCount: Int,
    val audioActive: Boolean,
    val subActive: Boolean,
    val streamCount: Int,
)

/** Katman tuşunun oynatıcıda tetiklediği iş (varsa). */
sealed interface TvOverlayEffect {
    data object None : TvOverlayEffect
    data class SelectAudio(val index: Int) : TvOverlayEffect
    /** [index]: altyazı listesindeki dizin; 0 = "Kapalı" (ExoPlayer iz dizini = index - 1). */
    data class SelectSubtitle(val index: Int) : TvOverlayEffect
    data class SelectStream(val index: Int) : TvOverlayEffect
}

data class TvOverlayResult(
    val model: TvOverlayModel,
    /** Olay tüketildi mi (false = sistem/Activity'ye geçsin). */
    val consumed: Boolean,
    val effect: TvOverlayEffect = TvOverlayEffect.None,
    /** Katman bu tuşla kapandı. */
    val closed: Boolean = false,
)

/**
 * Açık katmanın tuş denetleyicisi (saf; oynatıcı composable'ı yalnızca sonucu uygular): [TvPlayerOverlayKeys] tablosu +
 * panel/menü gezinmesi. Her durumda kapatma yolu vardır (Geri, Sarı; seçenek yoksa Tamam).
 */
object TvPlayerOverlayController {
    fun onKey(model: TvOverlayModel, ctx: TvOverlayContext, keyCode: Int, isDown: Boolean, repeatCount: Int = 0): TvOverlayResult {
        val key = TvPlayerOverlayKeys.route(model.overlay, keyCode, isDown, repeatCount)
        val panel = model.panel?.let { TvTracksPanelFocus.normalize(it, ctx.audioActive, ctx.subActive) }
        val hover = model.menuHover
        fun keep(consumed: Boolean = true, next: TvOverlayModel = model) = TvOverlayResult(next, consumed)
        return when (key) {
            TvOverlayKey.PassThrough -> keep(consumed = false)
            TvOverlayKey.Swallow -> keep()
            TvOverlayKey.Close -> TvOverlayResult(TvOverlayModel(), consumed = true, closed = true)
            TvOverlayKey.Up, TvOverlayKey.Down -> {
                val dir = if (key == TvOverlayKey.Up) -1 else 1
                when {
                    panel != null -> keep(next = TvOverlayModel(panel = TvTracksPanel.move(panel, dir, ctx.audioCount, ctx.subCount, ctx.audioActive, ctx.subActive)))
                    hover != null -> keep(next = TvOverlayModel(menuHover = TvSourceMenu.move(hover, dir, ctx.streamCount)))
                    else -> keep()
                }
            }
            TvOverlayKey.Left, TvOverlayKey.Right -> if (panel != null) {
                keep(next = TvOverlayModel(panel = TvTracksPanel.toColumn(panel, if (key == TvOverlayKey.Left) 0 else 1, ctx.audioActive, ctx.subActive)))
            } else {
                keep()
            }
            TvOverlayKey.Select -> when {
                panel != null -> when (val pick = TvTracksPanelFocus.activate(panel, ctx.audioActive, ctx.subActive)) {
                    TvTracksPanelKey.Close -> TvOverlayResult(TvOverlayModel(), consumed = true, closed = true)
                    // seçim sonrası panel açık kalır (● yeni seçime taşınır)
                    is TvTracksPanelKey.Select -> TvOverlayResult(
                        TvOverlayModel(panel = panel),
                        consumed = true,
                        effect = if (pick.column == 0) TvOverlayEffect.SelectAudio(pick.index) else TvOverlayEffect.SelectSubtitle(pick.index),
                    )
                }
                hover != null -> TvOverlayResult(TvOverlayModel(), consumed = true, effect = TvOverlayEffect.SelectStream(hover), closed = true)
                else -> keep()
            }
        }
    }
}

/** Geri tuşunun bir sonraki adımı (sırayla: katman, sonraki-bölüm önerisi, kontroller, çıkış). */
enum class TvBackTarget { ClosePanel, CloseMenu, DismissUpcoming, HideControls, Leave }

object TvPlayerBack {
    fun target(overlay: TvOverlay, hasUpcoming: Boolean, controlsVisible: Boolean): TvBackTarget = when {
        overlay == TvOverlay.Tracks -> TvBackTarget.ClosePanel
        overlay == TvOverlay.Sources -> TvBackTarget.CloseMenu
        hasUpcoming -> TvBackTarget.DismissUpcoming
        controlsVisible -> TvBackTarget.HideControls
        else -> TvBackTarget.Leave
    }

    /** Oynatıcıya özgü bir Geri işi var mı (yoksa ekran yığınından çıkılır)? */
    fun handlesBack(overlay: TvOverlay, hasUpcoming: Boolean, controlsVisible: Boolean): Boolean =
        target(overlay, hasUpcoming, controlsVisible) != TvBackTarget.Leave
}

