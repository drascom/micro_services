package com.diziflix.app.domain

import com.diziflix.app.data.model.Availability
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Season
import java.time.LocalDate
import java.util.Locale

/** Bölüm durumu (tizen-client detail.js epState). */
enum class EpisodeState { Ready, Check, Unavailable, Unaired }

/** Dizi/film detay ekranının saf mantığı (birim testli). */
object DetailLogic {
    /** Bu yüzdeden sonrası "izlendi". */
    const val DONE_PCT = 92.0

    fun todayIso(): String = LocalDate.now().toString()

    /**
     * 'Unaired' (yayın tarihi gelecekte ve kaynak hazır değil) | 'Unavailable' | 'Check' | 'Ready'.
     * Sunucunun availability.state alanı esastir; tarih yalnızca "yakında" işareti için.
     * ISO tarihler sözlük sırasıyla karşılaştırılır (YYYY-MM-DD).
     */
    fun episodeState(ep: Episode, today: String): EpisodeState {
        val state = ep.availability.state
        val air = ep.airDate
        if (Format.isIsoDate(air) && air!!.substring(0, 10) > today && state != Availability.STATE_READY) {
            return EpisodeState.Unaired
        }
        if (state == Availability.STATE_UNAVAILABLE) return EpisodeState.Unavailable
        if (state == Availability.STATE_CHECK_REQUIRED) return EpisodeState.Check
        return EpisodeState.Ready
    }

    fun isPlayable(state: EpisodeState): Boolean = state == EpisodeState.Ready || state == EpisodeState.Check

    fun isDone(ep: Episode): Boolean = (ep.progress?.pct ?: 0.0) >= DONE_PCT

    /**
     * Sezona geçince öne çıkarılacak bölüm: oynatılabilir İLK izlenmemiş bölüm; hepsi izlendiyse ilk
     * oynatılabilir; yoksa 0; bölüm yoksa -1.
     */
    fun targetEpisodeIndex(episodes: List<Episode>, today: String): Int {
        var firstPlayable = -1
        for (i in episodes.indices) {
            if (!isPlayable(episodeState(episodes[i], today))) continue
            if (firstPlayable < 0) firstPlayable = i
            if (!isDone(episodes[i])) return i
        }
        if (firstPlayable >= 0) return firstPlayable
        return if (episodes.isEmpty()) -1 else 0
    }

    /** Sezonlar numara sırasında; 0. sezon (özel bölümler) sona. Sunucu listesi değiştirilmez. */
    fun sortSeasons(seasons: List<Season>): List<Season> =
        seasons.sortedBy { if (it.season == 0) Int.MAX_VALUE else it.season }

    fun seasonLabel(season: Season): String {
        if (season.season == 0) return "Özel Bölümler"
        return season.title.ifBlank { "${season.season}. Sezon" }
    }

    fun episodeCount(season: Season): Int = if (season.episodeCount > 0) season.episodeCount else season.episodes.size

    /** Bulunan bölümün konumu: sezon dizini ve bölüm dizini (verilen liste sırasına göre). */
    data class EpisodePos(val seasonIndex: Int, val episodeIndex: Int)

    /**
     * Bölüm id'si (ya da ":sN:eM" son eki) -> konum. Önce tam id eşleşmesi, yoksa son ekteki
     * sezon/bölüm numaralarıyla eşleşme.
     */
    fun findEpisode(seasons: List<Season>, episodeId: String?): EpisodePos? {
        if (episodeId.isNullOrBlank()) return null
        for (si in seasons.indices) {
            val eps = seasons[si].episodes
            for (ei in eps.indices) if (eps[ei].id == episodeId) return EpisodePos(si, ei)
        }
        val m = Regex(":s(\\d+):e(\\d+)$", RegexOption.IGNORE_CASE).find(episodeId) ?: return null
        val sn = m.groupValues[1].toInt()
        val en = m.groupValues[2].toInt()
        for (si in seasons.indices) {
            if (seasons[si].season != sn) continue
            val eps = seasons[si].episodes
            for (ei in eps.indices) if (eps[ei].episode == en) return EpisodePos(si, ei)
        }
        return null
    }

    /** "Devam Et" gösterilsin mi (yarım kalmış ilerleme var). */
    fun hasProgress(detail: Detail): Boolean {
        val pct = detail.item.progress?.pct ?: 0.0
        val resumePos = detail.extras.resume?.position ?: 0.0
        return (pct > 0.0 && pct < DONE_PCT) || resumePos > 0.0
    }

    /**
     * Oynat/Devam Et hedef bölüm: sunucunun resume bölümü, yoksa ilerleme bölümü, yoksa ilk bölüm, yoksa
     * yapım id'si (film).
     */
    fun resumeTarget(detail: Detail): String {
        detail.extras.resume?.episodeId?.takeIf { it.isNotBlank() }?.let { return it }
        detail.item.progress?.episodeId?.takeIf { it.isNotBlank() }?.let { return it }
        val first = detail.seasons.firstOrNull { it.episodes.isNotEmpty() }?.episodes?.firstOrNull()
        return first?.id?.takeIf { it.isNotBlank() } ?: detail.item.id
    }

    /** Yapım tam izleme kaynağı sunuyor mu (Oynat düğmesi çizilsin mi). */
    fun canPlayFull(detail: Detail): Boolean {
        val item = detail.item
        return !item.availability.isUnavailable && item.playback != "unavailable" && item.playback != "trailer"
    }

    /**
     * Sunucu "izleme kaynağı yok" diyor: `availability.state == unavailable` VE `reason == no_video_source`
     * (gerçek bir getirme hatası DEĞİL: "alınamadı" metni bu durumda gösterilmez).
     */
    fun isNoSource(detail: Detail): Boolean {
        val a = detail.item.availability
        return a.isUnavailable && a.reason == Availability.REASON_NO_VIDEO_SOURCE
    }

    /** "Bu dizi için henüz izleme kaynağı yok." / "Bu film için ...". */
    fun noSourceText(isSeries: Boolean): String =
        if (isSeries) "Bu dizi için henüz izleme kaynağı yok." else "Bu film için henüz izleme kaynağı yok."

    const val NO_EPISODES_TEXT = "Bu dizinin bölüm bilgileri henüz eklenmedi."

    /** Bölümü olmayan dizinin yer tutucu metni: kaynak yoksa [noSourceText], aksi halde bölüm bilgisi henüz eklenmemiş. */
    fun emptySeasonsText(detail: Detail): String =
        if (isNoSource(detail)) noSourceText(detail.item.isSeries) else NO_EPISODES_TEXT

    /** Fragman düğmesi çizilsin mi. */
    fun hasTrailer(detail: Detail): Boolean = detail.item.availability.hasTrailer || detail.item.playback == "trailer"

    /** "S04 B01" (sezon/bölüm kodu); bölüm bulunamazsa null. */
    fun episodeCode(seasons: List<Season>, episodeId: String?): String? {
        val pos = findEpisode(seasons, episodeId) ?: return null
        val season = seasons[pos.seasonIndex]
        val ep = season.episodes[pos.episodeIndex]
        val seasonNo = if (ep.season > 0) ep.season else season.season
        return "S%02d B%02d".format(Locale.ROOT, seasonNo, ep.episode)
    }

    /** "S04 B01 · Valles Marineris" (sunucunun episode_label biçimi); bölüm bulunamazsa null. */
    fun episodeLabel(seasons: List<Season>, episodeId: String?): String? {
        val pos = findEpisode(seasons, episodeId) ?: return null
        val ep = seasons[pos.seasonIndex].episodes[pos.episodeIndex]
        val head = episodeCode(seasons, episodeId) ?: return null
        return if (ep.title.isBlank()) head else "$head · ${ep.title}"
    }

    /**
     * Ana eylem düğmesinin etiketi: ilerleme varsa "Devam Et" (dizide hedef bölümle: "Devam Et · S04 B02"),
     * kaynak sorunluysa "Yeniden Dene", değilse "Oynat".
     */
    fun playLabel(detail: Detail, seasons: List<Season>): String {
        if (hasProgress(detail)) {
            val code = if (detail.item.isSeries) episodeCode(seasons, resumeTarget(detail)) else null
            return if (code != null) "Devam Et · $code" else "Devam Et"
        }
        return if (detail.item.availability.needsCheck) "Yeniden Dene" else "Oynat"
    }

    /** Sunucunun devam/ilerleme bölümünün (listede bulunan) id'si; yoksa null. */
    fun resumeEpisodeId(seasons: List<Season>, detail: Detail): String? {
        val pos = findEpisode(seasons, detail.extras.resume?.episodeId)
            ?: findEpisode(seasons, detail.item.progress?.episodeId)
            ?: return null
        return seasons[pos.seasonIndex].episodes[pos.episodeIndex].id
    }

    /**
     * Sayfa açılırken öne çıkarılacak bölümün konumu: önce karttan gelen bölüm ([initialEpisodeId]), sonra
     * sunucunun devam bölümü, sonra ilerleme bölümü. YALNIZCA sezon seçimi ve vurgu içindir; liste
     * ASLA otomatik kaydırılmaz (sayfa her zaman en üstten açılır).
     */
    fun focusPosition(seasons: List<Season>, detail: Detail, initialEpisodeId: String?): EpisodePos? =
        findEpisode(seasons, initialEpisodeId)
            ?: findEpisode(seasons, detail.extras.resume?.episodeId)
            ?: findEpisode(seasons, detail.item.progress?.episodeId)

    /** Bölüm listesinde vurgulanacak bölümün id'si ([focusPosition] ile aynı öncelik); yoksa null. */
    fun highlightEpisodeId(seasons: List<Season>, detail: Detail, initialEpisodeId: String?): String? {
        val pos = focusPosition(seasons, detail, initialEpisodeId) ?: return null
        return seasons[pos.seasonIndex].episodes[pos.episodeIndex].id
    }

    /** Sonraki bölüm (sezon sırasıyla), yoksa null. Liste [Detail.seasons] sırasıdır. */
    fun nextEpisode(seasons: List<Season>, episodeId: String?): Episode? {
        val pos = findEpisode(sortSeasons(seasons), episodeId) ?: return null
        val sorted = sortSeasons(seasons)
        val eps = sorted[pos.seasonIndex].episodes
        if (pos.episodeIndex + 1 < eps.size) return eps[pos.episodeIndex + 1]
        for (si in pos.seasonIndex + 1 until sorted.size) {
            val next = sorted[si].episodes
            if (next.isNotEmpty()) return next.first()
        }
        return null
    }
}
