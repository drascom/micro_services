package com.diziflix.app.domain

import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Season
import java.util.Locale
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/*
 * Android TV DETAY ekranı tasarım mantığı (saf Kotlin; birim testli) — Tizen `css/detail.css` + `js/screens/detail.js`
 * karşılığı. Ölçüler Tizen px (ekrana `tvDp` ile çevrilir). Tablet detayı bunlardan hiçbir şey kullanmaz.
 */
object TvDetailSpec {
    // ---- hero (.detail-hero / .detail-body)
    const val HERO_H = 760f
    const val BODY_LEFT = 60f
    const val BODY_BOTTOM = 70f
    const val BODY_W = 1000f
    const val TITLE_SIZE = 64f              // .detail-title 64 px/900, satır 1.05, en çok 2 satır
    const val TITLE_LINE = 67.2f
    const val TITLE_MB = 14f
    const val META_SIZE = 24f               // .detail-meta 24 px dim, alt 18
    const val META_LINE = 32.4f
    const val META_MB = 18f
    const val META_DOT_MARGIN = 12f
    const val ACTIONS_MB = 20f
    const val BTN_GAP = 20f                 // .btn{margin-right:20px}
    const val ACTIONS_MAX_W = 1800f         // eylem satırı gövdeden (1000) taşabilir: 1920 - 2x60 güvenli alan
    const val BTN_ROW_GAP = 16f             // düğmeler ikinci satıra sarınca satır arası
    const val OVERVIEW_SIZE = 24f           // .detail-overview 24 px, satır 1.35, 4 satır, en çok 960, dolgu 6/12
    const val OVERVIEW_LINE = 32.4f
    const val OVERVIEW_LINES = 4
    const val OVERVIEW_MAX_W = 960f
    const val OVERVIEW_PAD_V = 6f
    const val OVERVIEW_PAD_H = 12f
    const val OVERVIEW_OUTLINE = 4f
    const val MORE_SIZE = 20f               // .ov-more
    const val MORE_MT = 4f
    const val MORE_MB = 14f
    const val MORE_ML = 12f
    const val CREDITS_SIZE = 22f            // .detail-credits

    // ---- sezon / bölüm tarayıcısı (.browser)
    const val BROWSER_PAD_H = 60f
    const val BROWSER_MB = 40f
    const val SEASONS_W = 440f
    const val SEASONS_GAP = 40f
    const val EPISODES_MAX_W = 1800f
    const val BR_TITLE_SIZE = 30f
    const val BR_TITLE_H = 60f
    const val SEASON_PITCH = 124f
    const val SEASON_ROW = 114f
    const val SEASON_ACCENT_BAR = 6f
    const val SEASON_POSTER_W = 68f
    const val SEASON_POSTER_H = 102f
    const val SEASON_NAME = 28f
    const val SEASON_COUNT = 21f
    const val EP_PITCH = 172f
    const val EP_ROW = 162f
    const val EP_STILL_W = 240f
    const val EP_STILL_H = 135f
    const val EP_PAD = 12f
    const val EP_NUM = 30f
    const val EP_TITLE = 28f
    const val EP_FLAG = 20f
    const val EP_OV = 21f
    const val EP_OV_LINE = 28f
    const val EP_META = 20f

    // ---- liste penceresi / sayfa kaydırması
    const val BASE_STAGE_H = 1080f
    const val VIEW_MAX = 900f
    const val WIN_ABOVE = 3
    const val WIN_BELOW = 3
    const val PAGE_ANCHOR = 96f             // liste alanı ekranda ustten bu kadar asagida
    const val PAGE_TARGET_FRACTION = 0.28f  // nav.js scrollPage detay: satır üstü ekran yüksekliğinin %28'i
    const val SEASON_DEBOUNCE_MS = 150L
    const val SCROLL_ANIM_MS = 200

    // ---- benzer yapımlar (.detail-similar)
    const val SIMILAR_MT = 37.8f
    const val SIMILAR_PT = 21.6f

    /** Hero arka plan görseli (izinli boyut). */
    const val BACKDROP_W = 1280
    const val BACKDROP_H = 720
}

/** Eylem düğmesi türü (Tizen `actionButtons`): oynatma, fragman, ölü fragman (gri, eylemsiz), Listem. */
enum class TvActionKind { PlayMovie, ResumeMovie, PlayEpisode, ResumeEpisode, PlayTrailer, TrailerDead, MyList }

/** [episodeId]: oynatılacak bölüm (film için yapım kimliği, fragman için null). */
data class TvActionSpec(
    val kind: TvActionKind,
    val label: String,
    val episodeId: String?,
    val primary: Boolean = false,
    val disabled: Boolean = false,
)

enum class TvTrailerState { Live, Dead, None }

/** Bölüm satırındaki durum etiketi (.ep-flag). */
enum class TvEpisodeFlagKind { Soon, Off, Check }

data class TvEpisodeFlag(val kind: TvEpisodeFlagKind, val text: String)

object TvDetailLogic {
    const val HYDRATING_TEXT = "Detaylar yükleniyor…"
    const val NO_EPISODES_TEXT = DetailLogic.NO_EPISODES_TEXT
    const val NO_SEASON_EPISODES_TEXT = "Bu sezonda bölüm yok"
    const val TRAILER_DEAD_TOAST = "Bu yapımın fragmanı artık izlenemiyor."
    const val MORE_TEXT = "Devamını oku ▸"
    const val ACT_FIRST_EPISODE = "İlk Bölümden Başla"
    const val ACT_TRAILER = "Fragmanı Oynat"
    const val ACT_TRAILER_DEAD = "Fragman yok"
    const val ACT_ADD_LIST = "Listeme Ekle"
    const val ACT_REMOVE_LIST = "Listemden Çıkar"

    // ------------------------------------------------------------------ liste yüksekliği / kaydırma (detail.js)

    /** Liste görüntü alanı üst sınırı: sahne yüksekliğine bağlı (1080 -> 900), en az 3 bölüm satırı. */
    fun viewMax(stageH: Float = TvDetailSpec.BASE_STAGE_H): Float =
        max(TvDetailSpec.EP_PITCH * 3f, stageH - (TvDetailSpec.BASE_STAGE_H - TvDetailSpec.VIEW_MAX))

    /** Görüntü alanı yüksekliği: içerik ihtiyacı üst sınırla kırpılır, en az bir bölüm satırı. */
    fun computeViewH(need: Float, stageH: Float = TvDetailSpec.BASE_STAGE_H): Float =
        max(TvDetailSpec.EP_PITCH, min(viewMax(stageH), need))

    /** İhtiyaç: en uzun sezon ya da sezon sayısı kadar satır. */
    fun viewNeed(seasons: List<Season>): Float {
        val maxEpisodes = seasons.maxOfOrNull { it.episodes.size } ?: 0
        return max(maxEpisodes * TvDetailSpec.EP_PITCH, seasons.size * TvDetailSpec.SEASON_PITCH)
    }

    /** Odaklı satır görüntü alanında kalsın: üstten en az 1, alttan en az 2 satır boşluk (detail.js `fitScroll`). */
    fun fitScroll(scroll: Float, index: Int, pitch: Float, viewH: Float, total: Int): Float {
        val top = index * pitch
        val minY = top - (viewH - 2 * pitch)
        val maxY = top - pitch
        var s = scroll
        if (s < minY) s = minY else if (s > maxY) s = maxY
        val maxScroll = max(0f, total * pitch - viewH)
        if (s > maxScroll) s = maxScroll
        if (s < 0f) s = 0f
        return s
    }

    fun clampScroll(scroll: Float, pitch: Float, viewH: Float, total: Int): Float {
        val maxScroll = max(0f, total * pitch - viewH)
        return max(0f, min(maxScroll, scroll))
    }

    /** Pencereli render: görünen aralık ±[TvDetailSpec.WIN_ABOVE]/[TvDetailSpec.WIN_BELOW] satır. */
    fun window(scroll: Float, viewH: Float, pitch: Float, total: Int): IntRange {
        if (total <= 0) return IntRange.EMPTY
        val first = max(0, (scroll / pitch).toInt() - TvDetailSpec.WIN_ABOVE)
        val last = min(total - 1, kotlin.math.ceil((scroll + viewH) / pitch).toInt() - 1 + TvDetailSpec.WIN_BELOW)
        return first..last
    }

    /** Sayfa kaydırması: sezon/bölüm odaklıyken tarayıcı ekranda üstten [TvDetailSpec.PAGE_ANCHOR] aşağıda durur. */
    fun pageYForBrowser(browserTop: Float): Float = max(0f, browserTop - TvDetailSpec.PAGE_ANCHOR)

    /** Diğer satırlar (düğmeler, özet, benzerler): satır üstü ekran yüksekliğinin %28'ine oturur; en üstte 0. */
    fun pageYForRow(rowTop: Float, stageH: Float = TvDetailSpec.BASE_STAGE_H): Float =
        max(0f, rowTop - (stageH * TvDetailSpec.PAGE_TARGET_FRACTION).roundToInt())

    // ------------------------------------------------------------------ eylem düğmeleri

    /** "S04 B02" (özel bölüm: "Özel B02") — Tizen `epTag`. */
    fun epTag(season: Int, episode: Int): String {
        val b = "B%02d".format(Locale.ROOT, episode)
        return if (season == 0) "Özel $b" else "S%02d %s".format(Locale.ROOT, season, b)
    }

    /** Bölüm kimliği -> "S04 B02": listedeki kayıttan, yoksa ":sN:eM" ekinden; bulunamazsa "". */
    fun episodeTag(seasons: List<Season>, episodeId: String?): String {
        val pos = DetailLogic.findEpisode(seasons, episodeId)
        if (pos != null) {
            val season = seasons[pos.seasonIndex]
            val ep = season.episodes[pos.episodeIndex]
            return epTag(if (ep.season > 0 || season.season == 0) ep.season else season.season, ep.episode)
        }
        val m = Regex(":s(\\d+):e(\\d+)$", RegexOption.IGNORE_CASE).find(episodeId.orEmpty()) ?: return ""
        return epTag(m.groupValues[1].toInt(), m.groupValues[2].toInt())
    }

    /** İlk oynatılabilir bölüm: önce normal sezonlar (numara sırası), özel bölümler en sona ([seasons] sıralı verilir). */
    fun firstPlayableEpisode(seasons: List<Season>, today: String): Episode? {
        for (season in seasons) {
            for (ep in season.episodes) if (DetailLogic.isPlayable(DetailLogic.episodeState(ep, today))) return ep
        }
        return null
    }

    fun trailerState(detail: Detail): TvTrailerState {
        val item = detail.item
        val a = item.availability
        if (a.trailer == "dead") return TvTrailerState.Dead
        val hasAction = detail.extras.actions?.any { it.kind == "play_trailer" } == true
        if (a.hasTrailer || item.playback == "trailer" || hasAction) return TvTrailerState.Live
        if (!a.trailer.isNullOrEmpty()) return TvTrailerState.Dead
        return TvTrailerState.None
    }

    private class Play(val kind: TvActionKind, val episodeId: String?)

    /**
     * Detay eylem düğmeleri (Listem HARİÇ; UI sona ekler). `actions[]` varsa ONDAN (sunucu hedefi belirler),
     * yoksa (eski sunucu / eski önbellek) eski alanlardan: resume / progress / playback / availability.
     * [seasons]: numara sırasında (özel sezon sonda). Sıra: Oynat/Devam Et, [İlk Bölümden Başla], Fragman.
     */
    fun actionButtons(detail: Detail, seasons: List<Season>, today: String): List<TvActionSpec> {
        val item = detail.item
        val status = item.availability
        val series = item.isSeries
        var play: Play? = null
        val acts = detail.extras.actions
        if (acts != null) {
            val hit = acts.firstOrNull {
                it.kind == "resume_movie" || it.kind == "play_movie" || it.kind == "resume_episode" || it.kind == "play_episode"
            }
            if (hit != null) {
                val kind = when (hit.kind) {
                    "resume_movie" -> TvActionKind.ResumeMovie
                    "play_movie" -> TvActionKind.PlayMovie
                    "resume_episode" -> TvActionKind.ResumeEpisode
                    else -> TvActionKind.PlayEpisode
                }
                play = Play(kind, hit.episodeId?.takeIf { it.isNotBlank() })
            }
        } else if (!series) {
            if (status.state != Availability.STATE_UNAVAILABLE && item.playback != "unavailable" && item.playback != "trailer") {
                val resumable = DetailLogic.hasProgress(detail)
                play = Play(if (resumable) TvActionKind.ResumeMovie else TvActionKind.PlayMovie, null)
            }
        } else if (seasons.isNotEmpty() && status.state != Availability.STATE_UNAVAILABLE) {
            val pos = DetailLogic.findEpisode(seasons, detail.extras.resume?.episodeId)
            var target: Episode? = null
            if (pos != null) {
                val cand = seasons[pos.seasonIndex].episodes[pos.episodeIndex]
                if (DetailLogic.isPlayable(DetailLogic.episodeState(cand, today))) target = cand
            }
            if (target == null) target = firstPlayableEpisode(seasons, today)
            if (target != null) {
                val pr = item.progress
                val again = pr != null && pr.episodeId == target.id && pr.pct > 0.0 && pr.pct < DetailLogic.DONE_PCT
                play = Play(if (again) TvActionKind.ResumeEpisode else TvActionKind.PlayEpisode, target.id)
            }
        }

        // Sunucu "izleme kaynağı yok" diyorsa (unavailable + no_video_source) Oynat/Devam Et pasif: düğme hiç çizilmez.
        if (DetailLogic.isNoSource(detail)) play = null
        val out = ArrayList<TvActionSpec>(3)
        if (play != null) {
            val isEpisode = play.kind == TvActionKind.PlayEpisode || play.kind == TvActionKind.ResumeEpisode
            val resume = play.kind == TvActionKind.ResumeMovie || play.kind == TvActionKind.ResumeEpisode
            var label = when {
                resume -> "Devam Et"
                status.state == Availability.STATE_CHECK_REQUIRED && !isEpisode -> "Yeniden Dene"
                else -> "Oynat"
            }
            val tag = if (isEpisode) episodeTag(seasons, play.episodeId) else ""
            if (tag.isNotEmpty()) label += " · $tag"
            out.add(TvActionSpec(play.kind, label, play.episodeId ?: if (isEpisode) null else item.id, primary = true))
            if (play.kind == TvActionKind.ResumeEpisode) {
                val first = firstPlayableEpisode(seasons, today)
                if (first != null && first.id != play.episodeId) {
                    out.add(TvActionSpec(TvActionKind.PlayEpisode, ACT_FIRST_EPISODE, first.id))
                }
            }
        }
        when (trailerState(detail)) {
            TvTrailerState.Live -> out.add(TvActionSpec(TvActionKind.PlayTrailer, ACT_TRAILER, null))
            TvTrailerState.Dead -> out.add(TvActionSpec(TvActionKind.TrailerDead, ACT_TRAILER_DEAD, null, disabled = true))
            TvTrailerState.None -> Unit
        }
        return out
    }

    fun myListSpec(inList: Boolean): TvActionSpec =
        TvActionSpec(TvActionKind.MyList, if (inList) ACT_REMOVE_LIST else ACT_ADD_LIST, null)

    // ------------------------------------------------------------------ hero metinleri

    /** Meta satırı parçaları (nokta ile ayrılır): yıl, türler (en çok 3), ülke, süre, takipçi. */
    fun metaBits(detail: Detail): List<String> {
        val item = detail.item
        val bits = ArrayList<String>(5)
        item.year?.let { bits.add(it.toString()) }
        if (item.genres.isNotEmpty()) bits.add(item.genres.take(3).joinToString(", "))
        item.country?.takeIf { it.isNotBlank() }?.let { bits.add(it) }
        val runtime = Format.minutes(detail.extras.runtime)
        if (runtime.isNotEmpty()) bits.add(if (item.isSeries) "$runtime / bölüm" else runtime)
        item.followers?.takeIf { it > 0 }?.let { bits.add("$it takipçi") }
        return bits
    }

    /** "Puan 8.4" (vurgu renkli, kalın); puan yoksa null. */
    fun scoreText(detail: Detail): String? =
        detail.item.rating?.takeIf { it > 0.0 }?.let { "Puan " + String.format(Locale.ROOT, "%.1f", it) }

    /** Kaynak durumu notu (meta satırı altında). */
    fun availabilityNote(detail: Detail): String? {
        val a = detail.item.availability
        return when {
            a.isUnavailable -> when {
                a.hasTrailer -> "Tam izleme kaynağı yok · Fragman mevcut"
                DetailLogic.isNoSource(detail) -> DetailLogic.noSourceText(detail.item.isSeries)
                else -> "İzleme kaynağı henüz mevcut değil"
            }
            a.needsCheck -> "Kaynakta sorun bildirildi · Yeniden deneyebilirsiniz"
            else -> null
        }
    }

    /** "Yönetmen: X    Oyuncular: a, b, c" (en çok 5 oyuncu); ikisi de boşsa null. */
    fun credits(detail: Detail): String? {
        val parts = ArrayList<String>(2)
        detail.extras.director?.takeIf { it.isNotBlank() }?.let { parts.add("Yönetmen: $it") }
        if (detail.extras.cast.isNotEmpty()) parts.add("Oyuncular: " + detail.extras.cast.take(5).joinToString(", "))
        return if (parts.isEmpty()) null else parts.joinToString("    ")
    }

    // ------------------------------------------------------------------ sezon / bölüm satırı metinleri

    fun seasonCountText(season: Season): String = "${DetailLogic.episodeCount(season)} bölüm"

    /** Gerçek sezon posteri var mı (has_poster=false iken poster_url dizinin afişidir: gösterilmez). */
    fun hasSeasonPoster(season: Season): Boolean = season.hasPoster && !season.posterUrl.isNullOrBlank()

    fun episodeFlag(ep: Episode, state: EpisodeState): TvEpisodeFlag? = when (state) {
        EpisodeState.Unaired -> {
            val date = Format.date(ep.airDate)
            TvEpisodeFlag(TvEpisodeFlagKind.Soon, "Yakında" + if (date.isNotEmpty()) " · $date" else "")
        }
        EpisodeState.Unavailable -> TvEpisodeFlag(TvEpisodeFlagKind.Off, "Kaynak yok")
        EpisodeState.Check -> TvEpisodeFlag(TvEpisodeFlagKind.Check, "Kaynak kontrol ediliyor")
        EpisodeState.Ready -> null
    }

    /** Bölüm numarası (yoksa sıra + 1). */
    fun episodeNumber(ep: Episode, index: Int): String = (if (ep.episode > 0) ep.episode else index + 1).toString()

    /** "45 dk · 24 Tem 2026" (yayınlanmamış bölümde tarih bayrakta olduğundan yazılmaz). */
    fun episodeMeta(ep: Episode, state: EpisodeState): String? {
        val bits = ArrayList<String>(2)
        val runtime = Format.minutes(ep.runtime)
        if (runtime.isNotEmpty()) bits.add(runtime)
        val date = Format.date(ep.airDate)
        if (state != EpisodeState.Unaired && date.isNotEmpty()) bits.add(date)
        return if (bits.isEmpty()) null else bits.joinToString(" · ")
    }

    /** Bölüm özeti (en çok 180 karakter, kelime sınırında kesilir). */
    fun episodeOverview(ep: Episode): String = Format.truncate(ep.overview, 180)

    /** Bölüm etkinleştirilince gösterilecek pencere (oynatılamıyorsa) ya da null (oynat). */
    fun episodeNotice(ep: Episode, state: EpisodeState): Pair<String, String>? = when (state) {
        EpisodeState.Unaired -> {
            val date = Format.date(ep.airDate)
            "Henüz yayınlanmadı" to (if (date.isNotEmpty()) "$date tarihinde yayınlanacak." else "Bu bölüm henüz yayınlanmadı.")
        }
        EpisodeState.Unavailable -> "Bölüm kaynağı yok" to "Bu bölümün izleme kaynağı henüz mevcut değil."
        else -> null
    }
}

// ---------------------------------------------------------------------------------------------- gezinme

enum class TvDetailSection { Actions, Overview, Seasons, Episodes, Similar }

/** Sanal odak konumu: bölüm + bölüm içi indeks (düğme / sezon / bölüm / benzer kart). */
data class TvDetailPos(val section: TvDetailSection, val index: Int)

/**
 * Düzen: hangi bölümler var ve kaç öğe. [shownSeason]: sağda gösterilen sezon; [episodeIndex]: o sezondaki hedef bölüm
 * (oynatılabilir ilk izlenmemiş; yoksa 0; bölüm yoksa -1).
 */
data class TvDetailLayout(
    val actionCount: Int,
    val hasOverview: Boolean,
    val seasonCount: Int,
    val episodeCount: Int,
    val similarCount: Int,
    val shownSeason: Int,
    val episodeIndex: Int,
) {
    val hasBrowser: Boolean get() = seasonCount > 0
}

/**
 * Detay odak gezinmesi (detail.js `key` + nav.js `move` eşdeğeri; saf):
 *  - Düğmeler yatay; Aşağı -> özet (varsa), sonra sezonlar (gösterilen sezona) ya da benzerler.
 *  - Sezonlar dikey; Sağ -> bölümler; Yukarı/Aşağı uçlarında özet/düğmeler ya da benzerler.
 *  - Bölümler dikey; Sol -> gösterilen sezon; uçlarda özet/düğmeler ya da benzerler.
 *  - Benzerler yatay; Yukarı -> bölümler (yoksa sezon / özet / düğmeler).
 * Sütun hafızası: düğmeler ve benzerler son sütunlarını hatırlar ([memory]).
 */
object TvDetailNavigation {
    fun initial(layout: TvDetailLayout, episodeFocusIndex: Int?): TvDetailPos {
        if (layout.hasBrowser && episodeFocusIndex != null && episodeFocusIndex in 0 until layout.episodeCount) {
            return TvDetailPos(TvDetailSection.Episodes, episodeFocusIndex)
        }
        return TvDetailPos(TvDetailSection.Actions, 0)
    }

    /** Bir pozisyonu düzene sığdırır (veri değişti: bölüm kalktı, liste kısaldı...). Geçersiz bölüm -> en yakın üst. */
    fun clamp(layout: TvDetailLayout, pos: TvDetailPos): TvDetailPos {
        fun fix(section: TvDetailSection, count: Int, index: Int): TvDetailPos? =
            if (count > 0) TvDetailPos(section, index.coerceIn(0, count - 1)) else null
        val fixed = when (pos.section) {
            TvDetailSection.Actions -> fix(pos.section, layout.actionCount, pos.index)
            TvDetailSection.Overview -> if (layout.hasOverview) TvDetailPos(pos.section, 0) else null
            TvDetailSection.Seasons -> fix(pos.section, layout.seasonCount, pos.index)
            TvDetailSection.Episodes -> fix(pos.section, layout.episodeCount, pos.index)
            TvDetailSection.Similar -> fix(pos.section, layout.similarCount, pos.index)
        }
        if (fixed != null) return fixed
        if (pos.section == TvDetailSection.Episodes && layout.hasBrowser) return TvDetailPos(TvDetailSection.Seasons, layout.shownSeason.coerceIn(0, layout.seasonCount - 1))
        return TvDetailPos(TvDetailSection.Actions, 0)
    }

    private fun above(layout: TvDetailLayout, memory: Map<TvDetailSection, Int>): TvDetailPos =
        if (layout.hasOverview) TvDetailPos(TvDetailSection.Overview, 0)
        else TvDetailPos(TvDetailSection.Actions, (memory[TvDetailSection.Actions] ?: 0).coerceIn(0, (layout.actionCount - 1).coerceAtLeast(0)))

    private fun similarPos(layout: TvDetailLayout, memory: Map<TvDetailSection, Int>): TvDetailPos? =
        if (layout.similarCount > 0) {
            TvDetailPos(TvDetailSection.Similar, (memory[TvDetailSection.Similar] ?: 0).coerceIn(0, layout.similarCount - 1))
        } else {
            null
        }

    /** Düğmeler/özetten aşağı: sezonlar (gösterilen sezona), yoksa benzerler. */
    private fun belowHero(layout: TvDetailLayout, memory: Map<TvDetailSection, Int>): TvDetailPos? =
        if (layout.hasBrowser) TvDetailPos(TvDetailSection.Seasons, layout.shownSeason.coerceIn(0, layout.seasonCount - 1))
        else similarPos(layout, memory)

    /** Yeni konum; hareket yoksa null (tuş yine de tüketilir). */
    fun move(layout: TvDetailLayout, pos: TvDetailPos, dir: TvDir, memory: Map<TvDetailSection, Int>): TvDetailPos? {
        val i = pos.index
        return when (pos.section) {
            TvDetailSection.Actions -> when (dir) {
                TvDir.Left -> if (i > 0) TvDetailPos(pos.section, i - 1) else null
                TvDir.Right -> if (i < layout.actionCount - 1) TvDetailPos(pos.section, i + 1) else null
                TvDir.Up -> null
                TvDir.Down -> if (layout.hasOverview) TvDetailPos(TvDetailSection.Overview, 0) else belowHero(layout, memory)
            }
            TvDetailSection.Overview -> when (dir) {
                TvDir.Up -> TvDetailPos(TvDetailSection.Actions, (memory[TvDetailSection.Actions] ?: 0).coerceIn(0, (layout.actionCount - 1).coerceAtLeast(0)))
                TvDir.Down -> belowHero(layout, memory)
                else -> null
            }
            TvDetailSection.Seasons -> when (dir) {
                TvDir.Up -> if (i > 0) TvDetailPos(pos.section, i - 1) else above(layout, memory)
                TvDir.Down -> if (i < layout.seasonCount - 1) TvDetailPos(pos.section, i + 1) else similarPos(layout, memory)
                TvDir.Right -> if (layout.episodeCount > 0) TvDetailPos(TvDetailSection.Episodes, layout.episodeIndex.coerceIn(0, layout.episodeCount - 1)) else null
                TvDir.Left -> null
            }
            TvDetailSection.Episodes -> when (dir) {
                TvDir.Up -> if (i > 0) TvDetailPos(pos.section, i - 1) else above(layout, memory)
                TvDir.Down -> if (i < layout.episodeCount - 1) TvDetailPos(pos.section, i + 1) else similarPos(layout, memory)
                TvDir.Left -> TvDetailPos(TvDetailSection.Seasons, layout.shownSeason.coerceIn(0, layout.seasonCount - 1))
                TvDir.Right -> null
            }
            TvDetailSection.Similar -> when (dir) {
                TvDir.Left -> if (i > 0) TvDetailPos(pos.section, i - 1) else null
                TvDir.Right -> if (i < layout.similarCount - 1) TvDetailPos(pos.section, i + 1) else null
                TvDir.Up -> when {
                    layout.hasBrowser && layout.episodeCount > 0 ->
                        TvDetailPos(TvDetailSection.Episodes, (if (layout.episodeIndex >= 0) layout.episodeIndex else 0).coerceIn(0, layout.episodeCount - 1))
                    layout.hasBrowser -> TvDetailPos(TvDetailSection.Seasons, layout.shownSeason.coerceIn(0, layout.seasonCount - 1))
                    else -> above(layout, memory)
                }
                TvDir.Down -> null
            }
        }
    }
}
