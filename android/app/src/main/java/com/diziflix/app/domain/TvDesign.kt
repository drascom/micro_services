package com.diziflix.app.domain

import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.UrlUtil
import java.util.Locale
import kotlin.math.ceil
import kotlin.math.floor

/*
 * Android TV modu tasarım mantığı (saf Kotlin; birim testli). TASARIMIN KAYNAĞI Tizen istemcisidir
 * (tizen-client/css/base.css + home.css, js/ui/{card,row,hero,navigation}.js, js/nav.js). Tizen sahnesi 1920x1080
 * "piksel"dir; buradaki tüm ölçüler o birimle (Tizen px) yazılır ve ekrana [tvDp] ile orantılı çevrilir.
 * Tablet/telefon yolu bunlardan hiçbir şey kullanmaz.
 */

/**
 * Tizen pikselini ekran dp'sine çevirir: `n * (ekranGenişliğiPx / 1920) / density`.
 * 1920x1080 piksel + density 320 (2.0) ekranda 1 Tizen px = 0,5 dp = 1 gerçek piksel; 1080p dışı ekranlarda
 * yerleşim genişliğe orantılı kalır (yükseklik sahne oranına göre kendiliğinden uyar).
 */
fun tvDp(tizenPx: Float, screenWidthPx: Int, density: Float): Float {
    val width = if (screenWidthPx > 0) screenWidthPx.toFloat() else TvSpec.STAGE_W
    val dens = if (density > 0f) density else 1f
    return tizenPx * (width / TvSpec.STAGE_W) / dens
}

/** Tizen CSS değerleri (Tizen px). Kaynak satırları yorumlarda. */
object TvSpec {
    const val STAGE_W = 1920f
    const val STAGE_H = 1080f

    /** base.css `--safe`: sol/sağ güvenli kenar boşluğu. */
    const val SAFE = 60f

    /** base.css `.topbar{height:120px}`. */
    const val TOPBAR_H = 120f

    // ---- kart (base.css :root + home.css .card)
    const val POSTER_W = 240f      // --poster-w
    const val POSTER_H = 360f      // --poster-h
    const val CARD_W = 342f        // --card-w
    const val CARD_H = 192f        // --card-h
    const val GAP = 12f            // --gap
    const val FOCUS_W = 640f       // --focus-card-w
    const val FOCUS_H = 360f       // --focus-card-h
    const val FOCUS_POSTER_W = 270f            // .row-tile.poster.focus-poster{width:270px}
    const val FOCUS_POSTER_SCALE = 1.12f       // .card-poster.focused{transform:scale(1.12)}
    const val FOCUS_LANDSCAPE_SCALE = 1.02f    // .card-focus-landscape.focused{transform:scale(1.02)}
    const val FOCUS_BORDER = 7f                // .card.focused:before{border:7px solid accent}
    const val CARD_RADIUS = 4f
    const val FOCUS_ANIM_MS = 220              // .card{transition:width .22s ...}
    const val SCROLL_ANIM_MS = 200             // #page / .row-strip transition .2s
    const val PROGRESS_H = 6f                  // .card-progress

    // ---- satır (home.css .row / .row-title / .row-viewport / .row-card-*)
    const val ROW_TITLE_SIZE = 30f
    const val ROW_TITLE_LINE = 41f             // 30px * 1.35 (gövde line-height) ~ 40,5
    const val ROW_TITLE_MB = 14f
    const val VIEWPORT_PAD_TOP = 26f
    const val VIEWPORT_PAD_BOTTOM = 32f
    const val CAPTION_TITLE_SIZE = 24f         // .row-card-title
    const val CAPTION_TITLE_LINE = 30f
    const val CAPTION_TITLE_MT = 16f
    const val CAPTION_TITLE_MT_FOCUS_POSTER = 38f
    const val CAPTION_SCORE_SIZE = 22f         // .row-card-score
    const val CAPTION_SCORE_LINE = 28f
    const val CAPTION_SCORE_MT = 5f

    /** Poster kutusu + başlık + puan satırı: Tizen `.row-tile` yüksekliği (360+16+30+5+28). */
    const val TILE_H = 439f
    const val VIEWPORT_H = VIEWPORT_PAD_TOP + TILE_H + VIEWPORT_PAD_BOTTOM

    /**
     * nav.js `scrollPage` TARGET: odaktaki satır, sayfa y=140'a oturur. Sabit üst menü 120 olduğundan içerik
     * koordinatında (üst menünün altından başlayan) 20'dir -> satırın ÜSTÜNDE [ROW_PAD_TOP] boşluk bırakılır.
     */
    const val ROW_SLOT_PAGE_Y = 140f
    const val ROW_PAD_TOP = ROW_SLOT_PAGE_Y - TOPBAR_H
    const val ROW_PAD_BOTTOM = 32f             // .row{margin-bottom:52px} - ROW_PAD_TOP

    /** Bir satır öğesinin toplam yüksekliği (içerik koordinatı). */
    const val ROW_H = ROW_PAD_TOP + ROW_TITLE_LINE + ROW_TITLE_MB + VIEWPORT_H + ROW_PAD_BOTTOM

    // ---- hero (home.css .hero)
    const val HERO_H = 600f                    // .hero{height:600px}
    const val HERO_GAP = 4f                    // .has-hero > .rows{margin-top:24px} - ROW_PAD_TOP
    const val HERO_BLOCK = HERO_H + HERO_GAP

    // ---- üst menü (base.css)
    const val SEARCH_W = 510f
    const val SEARCH_H = 54f
}

/** Yatay satır (strip) hesapları: nav.js `scrollStrip` + row.js pencere mantığı. */
object TvRowMetrics {
    /** Bir poster kutusu + boşluk: kaydırma adımı (nav.js `step`). */
    const val STEP = TvSpec.POSTER_W + TvSpec.GAP

    /** row.js PAD: görünen pencerenin iki yanında ek yüklenen kart sayısı. */
    const val IMAGE_PAD = 4

    /**
     * Şerit kaydırması (Tizen px, sola): ilk kart hariç odaktaki kart her zaman İKİNCİ yuvada durur; hemen solundaki kart
     * normal sol boşlukta tam görünür (`x = tileLeft - step - safeLeft`, en az 0).
     */
    fun stripOffset(col: Int): Float = if (col <= 1) 0f else (col - 1) * STEP

    /** Sahne genişliğine sığan TAM poster sayısı (sol güvenli boşluktan sonra). 1920'de 7. */
    fun fullyVisibleCards(stageW: Float = TvSpec.STAGE_W): Int =
        floor((stageW - TvSpec.SAFE + TvSpec.GAP) / STEP).toInt().coerceAtLeast(1)

    /** Kısmen görünenler dahil poster sayısı (row.js `VISIBLE`). 1920'de 8. */
    fun visibleCards(stageW: Float = TvSpec.STAGE_W): Int =
        ceil((stageW - TvSpec.SAFE) / STEP).toInt().coerceAtLeast(1)

    /** Görsel penceresi [odak-PAD, odak+görünen+PAD] (row.js `dzUpdateWindow`), [count] ile sınırlı; boşsa boş aralık. */
    fun imageWindow(focusCol: Int, count: Int, stageW: Float = TvSpec.STAGE_W): IntRange {
        if (count <= 0) return IntRange.EMPTY
        val start = (focusCol - IMAGE_PAD).coerceAtLeast(0)
        val end = (focusCol + visibleCards(stageW) + IMAGE_PAD).coerceAtMost(count - 1)
        return start..end
    }

    /**
     * Dikeyde bileşime alınan satır penceresi: odak satırı, bir üstü ve iki altı (alt satırın yarısı ekranda görünür);
     * dışı aynı yükseklikte boş yer tutucu olur.
     */
    fun rowWindow(focusRowIndex: Int, rowCount: Int): IntRange {
        if (rowCount <= 0) return IntRange.EMPTY
        val f = focusRowIndex.coerceIn(0, rowCount - 1)
        return (f - 1).coerceAtLeast(0)..(f + 2).coerceAtMost(rowCount - 1)
    }
}

/** Kartın odak halindeki biçimi. */
enum class TvFocusMode {
    /** Odaksız. */
    None,

    /** Yatay afişi olmayan poster: 270 genişlik + %112 büyüme + altın parıltı (`.focus-poster`). */
    Poster,

    /** Yatay afişli poster: 640x360'a genişler, yatay görsel + başlık/yıl-puan önizleme (`.focus-expanded`). */
    Expanded,

    /** Yalnız %112 büyüme, kutu genişliği aynı (Tümünü Gör kartı). */
    ScaleOnly,
}

object TvFocusExpansion {
    /** Odaktaki poster kutusu genişliği (Tizen px). */
    fun tileWidth(mode: TvFocusMode): Float = when (mode) {
        TvFocusMode.Expanded -> TvSpec.FOCUS_W
        TvFocusMode.Poster -> TvSpec.FOCUS_POSTER_W
        else -> TvSpec.POSTER_W
    }

    /** Kart üstündeki ölçek (sol-orta eksende). */
    fun cardScale(mode: TvFocusMode): Float = when (mode) {
        TvFocusMode.Expanded -> TvSpec.FOCUS_LANDSCAPE_SCALE
        TvFocusMode.Poster, TvFocusMode.ScaleOnly -> TvSpec.FOCUS_POSTER_SCALE
        TvFocusMode.None -> 1f
    }

    /** Altındaki başlığın üst boşluğu (büyüyen poster başlığa binmesin: 38). */
    fun captionTop(mode: TvFocusMode): Float =
        if (mode == TvFocusMode.Poster) TvSpec.CAPTION_TITLE_MT_FOCUS_POSTER else TvSpec.CAPTION_TITLE_MT

    /**
     * Odaktaki poster kartın biçimi: yatay afişi varsa [TvFocusMode.Expanded], yoksa [TvFocusMode.Poster];
     * odakta değilse [TvFocusMode.None].
     */
    fun posterMode(focused: Boolean, hasLandscape: Boolean): TvFocusMode = when {
        !focused -> TvFocusMode.None
        hasLandscape -> TvFocusMode.Expanded
        else -> TvFocusMode.Poster
    }

    /** Genişlemenin ilerleme oranı (0..1): poster 240 -> afiş 640 arası; yatay görsel geçişinin opaklığı. */
    fun expandFraction(currentWidth: Float): Float =
        ((currentWidth - TvSpec.POSTER_W) / (TvSpec.FOCUS_W - TvSpec.POSTER_W)).coerceIn(0f, 1f)
}

/** Kart görselleri ve altyazı metinleri (Tizen card.js + row.js ile aynı kurallar). */
object TvCardLogic {
    // /img izinli boyutları (server/app/images.py SIZES): poster 300x450, still 454x254 / 640x360, card 342x192.
    const val POSTER_IMG_W = 300
    const val POSTER_IMG_H = 450
    const val EP_W = 454
    const val EP_H = 254
    const val CARD_IMG_W = 342
    const val CARD_IMG_H = 192
    const val FOCUS_IMG_W = 640
    const val FOCUS_IMG_H = 360

    fun isEpisode(item: Item): Boolean = item.cardKind == "episode"

    /** Gerçek still var (has_still false değil ve adres dolu). */
    fun hasStill(item: Item): Boolean = item.hasStill != false && !item.stillUrl.isNullOrBlank()

    /**
     * Dinlenme görseli (poster kutusu). Bölüm kartı: önce dizinin posteri; yoksa still; yoksa yatay afiş. Diğerleri:
     * poster, yoksa kart görseli. Adres yoksa "" (başlık metni yer tutucu olur).
     */
    fun posterArtUrl(base: String, item: Item): String {
        val poster = item.portrait
        if (isEpisode(item)) {
            if (!poster.isNullOrBlank()) return UrlUtil.sized(base, poster, POSTER_IMG_W, POSTER_IMG_H)
            if (hasStill(item)) return UrlUtil.sizedTo(base, item.stillUrl, EP_W, EP_H)
            return UrlUtil.sized(base, item.card, CARD_IMG_W, CARD_IMG_H)
        }
        val path = if (!poster.isNullOrBlank()) poster else item.card
        return UrlUtil.sized(base, path, POSTER_IMG_W, POSTER_IMG_H)
    }

    /**
     * Odak (yatay önizleme) görseli; "" = genişleme yok. Bölümde gerçek still öncelikli; yoksa gerçek yatay afişi
     * (has_backdrop) olanlarda backdrop (Tizen `focusArt`: sorgu atılır, 640x360 istenir, sunucu izinli boyuta yuvarlar).
     */
    fun focusArtUrl(base: String, item: Item): String {
        if (isEpisode(item) && hasStill(item)) return UrlUtil.sizedTo(base, item.stillUrl, FOCUS_IMG_W, FOCUS_IMG_H)
        if (!item.hasBackdrop) return ""
        val path = (item.backdrop ?: item.card).orEmpty().substringBefore('?')
        if (path.isBlank()) return ""
        return UrlUtil.sized(base, path, FOCUS_IMG_W, FOCUS_IMG_H)
    }

    /** Genişleyebilir mi (yatay görseli var mı). */
    fun canExpand(item: Item): Boolean = focusArtUrl("", item).isNotEmpty()

    /** "★ Puan 7.2" (Tizen `row-card-score`). */
    fun scoreText(rating: Double): String = "★ Puan " + String.format(Locale.ROOT, "%.1f", rating)

    /** Kartın altındaki ikinci satır: bölüm etiketi ("S07 B05 · Ad") yoksa puan. */
    fun subtitle(item: Item): String? {
        val label = item.episodeLabel
        if (!label.isNullOrBlank()) return label
        val rating = item.rating
        if (rating != null && rating > 0.0) return scoreText(rating)
        return null
    }

    /** Genişlemiş kartın içindeki meta: bölümde etiket; diğerlerinde "yıl  •  ★ 7.2". */
    fun previewMeta(item: Item): String? {
        val bits = ArrayList<String>(2)
        if (isEpisode(item)) {
            val label = item.episodeLabel
            if (!label.isNullOrBlank()) bits.add(label)
        } else {
            item.year?.let { bits.add(it.toString()) }
            val rating = item.rating
            if (rating != null && rating > 0.0) bits.add("★ " + String.format(Locale.ROOT, "%.1f", rating))
        }
        return if (bits.isEmpty()) null else bits.joinToString("  •  ")
    }

    /** İlerleme çubuğu oranı (0..1); ilerleme yoksa 0. */
    fun progressFraction(item: Item): Float {
        val pct = item.progress?.pct ?: 0.0
        return (pct / 100.0).toFloat().coerceIn(0f, 1f)
    }

    /** Hero meta satırı (Tizen hero.js `metaLine`): yıl · Puan · türler (en çok 3) · Dizi/Film, üç boşlukla ayrılmış. */
    fun heroMeta(item: Item): String {
        val parts = ArrayList<String>(4)
        item.year?.let { parts.add(it.toString()) }
        val rating = item.rating
        if (rating != null && rating > 0.0) parts.add("Puan " + String.format(Locale.ROOT, "%.1f", rating))
        if (item.genres.isNotEmpty()) parts.add(item.genres.take(3).joinToString(" - "))
        parts.add(if (item.isSeries) "Dizi" else "Film")
        return parts.joinToString("   ·   ")
    }
}

// ------------------------------------------------------------------------------------------ ana sayfa gezinmesi

enum class TvDir { Up, Down, Left, Right }

/** Odaklanabilir satır: [size] = satırdaki odaklanabilir öğe sayısı (0 = atlanır: iskelet/boş). */
data class TvNavRow(val id: String, val size: Int)

sealed interface TvMove {
    /** Odak başka öğeye geçer. */
    data class To(val rowId: String, val col: Int) : TvMove

    /** Hero'da sol/sağ: slayt değişir ([step] -1/+1). */
    data class HeroPage(val step: Int) : TvMove

    /** En üstten Yukarı: üst menüye çık. */
    data object ToTopBar : TvMove

    /** Sınır: hiçbir şey olmaz (tuş yine de tüketilir). */
    data object Stay : TvMove
}

/** Ana sayfa odak gezinmesi (nav.js `move`/`apply`/`refresh` eşdeğeri; saf). */
object TvHomeNavigation {
    const val HERO_ID = "hero"

    /**
     * [rows]: görünen satırlar sırasıyla (hero dahil, varsa ilk). [memory]: satır başına son sütun (sütun hafızası).
     * Yukarı/Aşağı: komşu ODAKLANABİLİR satıra hafızadaki sütunla geçer; Sol/Sağ: satır içi; hero'da slayt değişir.
     */
    fun move(
        rows: List<TvNavRow>,
        rowId: String,
        col: Int,
        dir: TvDir,
        memory: Map<String, Int>,
        heroCount: Int,
    ): TvMove {
        val focusable = rows.filter { it.size > 0 }
        val index = focusable.indexOfFirst { it.id == rowId }
        if (index < 0) return TvMove.Stay
        val row = focusable[index]
        return when (dir) {
            TvDir.Left, TvDir.Right -> {
                val step = if (dir == TvDir.Right) 1 else -1
                if (row.id == HERO_ID) {
                    if (heroCount > 1) TvMove.HeroPage(step) else TvMove.Stay
                } else {
                    val next = col + step
                    if (next < 0 || next > row.size - 1) TvMove.Stay else TvMove.To(row.id, next)
                }
            }
            TvDir.Up, TvDir.Down -> {
                val targetIndex = index + if (dir == TvDir.Down) 1 else -1
                when {
                    targetIndex < 0 -> TvMove.ToTopBar
                    targetIndex > focusable.lastIndex -> TvMove.Stay
                    else -> {
                        val target = focusable[targetIndex]
                        TvMove.To(target.id, (memory[target.id] ?: 0).coerceIn(0, target.size - 1))
                    }
                }
            }
        }
    }

    /** Açılış odağı: ilk odaklanabilir satır (hero varsa hero), yoksa null. Sütun 0. */
    fun initialRow(rows: List<TvNavRow>): String? = rows.firstOrNull { it.size > 0 }?.id

    /**
     * Veri değişti (satır kalktı/boşaldı/kısaldı): odağı geçerli bir yere toplar. Satır duruyorsa sütunu sıkıştırır;
     * kalktıysa eski konumundan ([oldIndex]) bir üstteki odaklanabilir satıra (yoksa ilkine) gider. Hiç yoksa null.
     */
    fun repair(rows: List<TvNavRow>, rowId: String?, col: Int, oldIndex: Int): Pair<String, Int>? {
        val focusable = rows.filter { it.size > 0 }
        if (focusable.isEmpty()) return null
        val same = focusable.firstOrNull { it.id == rowId }
        if (same != null) return same.id to col.coerceIn(0, same.size - 1)
        val idx = (oldIndex - 1).coerceIn(0, focusable.lastIndex)
        return focusable[idx].id to 0
    }

    /** Hero slayt değişimi: Tizen `moveHero` gibi dairesel. */
    fun heroStep(current: Int, count: Int, step: Int): Int {
        if (count <= 1) return 0
        return ((current + step) % count + count) % count
    }
}

/** Dikey sayfa kaydırması (nav.js `scrollPage`): odaktaki satır sabit üst yuvaya oturur; hero/üst menü odaktayken y=0. */
object TvHomeScroll {
    /**
     * Sayfanın yukarı kayma miktarı (içerik koordinatı, Tizen px). [focusRowId]: hero, satır kimliği ya da null (üst menü/
     * odak yok) -> 0 (hero TAM görünür). Satır odaktayken: hero bloğu + satır sırası * [TvSpec.ROW_H]; böylece satır başlığı
     * sayfa y=140 yuvasında durur. [rowIds]: hero hariç, ekranda yer kaplayan TÜM satırlar (iskelet dahil) sırasıyla.
     */
    fun targetY(focusRowId: String?, rowIds: List<String>, hasHero: Boolean): Float {
        if (focusRowId == null || focusRowId == TvHomeNavigation.HERO_ID) return 0f
        val index = rowIds.indexOf(focusRowId)
        if (index < 0) return 0f
        return (if (hasHero) TvSpec.HERO_BLOCK else 0f) + index * TvSpec.ROW_H
    }
}
