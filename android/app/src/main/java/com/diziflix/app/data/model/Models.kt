package com.diziflix.app.data.model

import androidx.compose.runtime.Immutable
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Sunucu sözleşmesi (API.md, docs/DATA-CONTRACT-V1.md). Sunucu alan ekleyebilir: hiçbir model
 * bilinmeyen alanda kırılmaz (ApiJson ignoreUnknownKeys) ve neredeyse tüm alanlar varsayılanlıdır.
 * Süre/konum/puan gibi sayılar Double okunur (sunucu tam sayı da ondalık da yollayabilir).
 *
 * Tüm modeller @Immutable: List alanları yüzünden Compose bunları "kararsız" sayıp composable'ları
 * atlatmıyordu. Listeler oluşturulduktan sonra asla değiştirilmez (yalnızca copy ile yenisi üretilir).
 * Bu sınıflar ayrıca yerel önbelleğe (çevrimdışı açılış) JSON olarak yazılır.
 */

@Immutable
@Serializable
data class Availability(
    /** ready | check_required | unavailable */
    val state: String = "ready",
    val reason: String? = null,
    @SerialName("has_trailer") val hasTrailer: Boolean = false,
    /** ok | dead | unknown; yalnızca detay yanıtında ve fragman kaynağı olan yapımlarda gelir. */
    val trailer: String? = null,
) {
    val isUnavailable: Boolean get() = state == STATE_UNAVAILABLE
    val needsCheck: Boolean get() = state == STATE_CHECK_REQUIRED

    companion object {
        const val STATE_READY = "ready"
        const val STATE_CHECK_REQUIRED = "check_required"
        const val STATE_UNAVAILABLE = "unavailable"

        /** `reason`: yapımın hiç video kaynağı kaydı yok (gerçek bir getirme hatası değil). */
        const val REASON_NO_VIDEO_SOURCE = "no_video_source"
    }
}

/** Kart/detay üzerindeki "devam et" bilgisi (dizide hangi bölümde kalındığı dahil). */
@Immutable
@Serializable
data class Progress(
    @SerialName("episode_id") val episodeId: String? = null,
    val position: Double = 0.0,
    val duration: Double = 0.0,
    val pct: Double = 0.0,
)

/** Ortak kart şeması (Item). Film ve dizi kartı aynı yapıdadır. */
@Immutable
@Serializable
data class Item(
    val id: String = "",
    /** series | movie */
    val type: String = "movie",
    val title: String = "",
    val year: Int? = null,
    val card: String? = null,
    val portrait: String? = null,
    val backdrop: String? = null,
    @SerialName("has_backdrop") val hasBackdrop: Boolean = false,
    val overview: String = "",
    val genres: List<String> = emptyList(),
    val rating: Double? = null,
    val country: String? = null,
    val followers: Int? = null,
    val badge: String? = null,
    val availability: Availability = Availability(),
    val progress: Progress? = null,
    /** video | trailer | unavailable */
    val playback: String? = null,
    @SerialName("tmdb_id") val tmdbId: Int? = null,
    @SerialName("imdb_id") val imdbId: String? = null,
    // Bölüm kartı alanları (docs/DATA-CONTRACT-V1.md §5); sunucu henüz göndermeyebilir.
    @SerialName("card_kind") val cardKind: String? = null,
    @SerialName("card_key") val cardKey: String? = null,
    @SerialName("episode_id") val episodeId: String? = null,
    @SerialName("episode_label") val episodeLabel: String? = null,
    /** Bölüm kartının yatay görseli (API.md «Bölüm kartı»); yalnızca TV ana sayfasında odak önizlemesi için okunur. */
    @SerialName("still_url") val stillUrl: String? = null,
    @SerialName("has_still") val hasStill: Boolean? = null,
    // Yalnızca hero öğelerinde.
    @SerialName("logo_text") val logoText: String? = null,
    val tagline: String? = null,
    @SerialName("in_mylist") val inMylist: Boolean = false,
    /** Yalnızca `/api/search` sonuçlarında: bu yapımın bulunduğu siteler (kaynak etiketleri). Eski sunucuda yok -> boş. */
    @SerialName("source_options") val sourceOptions: List<SourceOption> = emptyList(),
) {
    val isSeries: Boolean get() = type == "series"

    /** Liste anahtarı: aynı dizinin iki bölüm kartı karışmasın diye card_key tercih edilir. */
    val listKey: String get() = cardKey?.takeIf { it.isNotBlank() } ?: id
}

@Immutable
@Serializable
data class BootRow(
    val id: String = "",
    val title: String = "",
    /** false: yalnızca başlık + count gelir, öğeler /api/row/{id} ile tembel çekilir. */
    val loaded: Boolean = false,
    val count: Int? = null,
    val total: Int? = null,
    val offset: Int? = null,
    val items: List<Item> = emptyList(),
)

@Immutable
@Serializable
data class BootResponse(
    val hero: Item? = null,
    val heroes: List<Item> = emptyList(),
    val rows: List<BootRow> = emptyList(),
    val layout: String? = null,
    @SerialName("catalog_total") val catalogTotal: Int? = null,
) {
    /** `heroes` doluysa o, değilse tekil `hero`. */
    val heroList: List<Item> get() = heroes.ifEmpty { listOfNotNull(hero) }
}

@Immutable
@Serializable
data class RowResponse(
    val id: String = "",
    val title: String = "",
    val items: List<Item> = emptyList(),
    val offset: Int = 0,
    val limit: Int = 20,
    val total: Int = 0,
)

@Immutable
@Serializable
data class Profile(
    val id: String = "",
    val name: String = "",
    @SerialName("avatar_seed") val avatarSeed: String? = null,
    val avatar: String? = null,
    @SerialName("is_kids") val isKids: Boolean = false,
)

@Immutable
@Serializable
data class ProfilesResponse(val profiles: List<Profile> = emptyList())

@Immutable
@Serializable
data class Health(
    val status: String = "",
    val source: String? = null,
    val items: Int? = null,
    @SerialName("cache_age") val cacheAge: Int? = null,
)

@Immutable
@Serializable
data class Genre(val id: String = "", val name: String = "")

@Immutable
@Serializable
data class GenresResponse(val genres: List<Genre> = emptyList())

@Immutable
@Serializable
data class CatalogResponse(
    val items: List<Item> = emptyList(),
    val total: Int = 0,
    val offset: Int = 0,
    val limit: Int = 20,
    val genres: List<Genre> = emptyList(),
    val years: List<Int> = emptyList(),
)

@Immutable
@Serializable
data class SearchResponse(
    val items: List<Item> = emptyList(),
    val total: Int = 0,
    val remote: Boolean = false,
    @SerialName("remote_error") val remoteError: String? = null,
    /** Site kimliği -> o sitenin canlı arama sonucu (yanıt vermeyen kaynakları göstermek için). Eski sunucuda yok -> boş. */
    @SerialName("remote_sites") val remoteSites: Map<String, RemoteSite> = emptyMap(),
)

/** `/api/search` öğesindeki kaynak seçeneği: yapım hangi sitede, kaç bölümü var, sağlığı ne (`status`: ok | unknown | broken). */
@Immutable
@Serializable
data class SourceOption(
    val site: String = "",
    val name: String = "",
    /** series | movie */
    val kind: String = "",
    val episodes: Int = 0,
    val status: String = "unknown",
)

/** `/api/search` kökündeki site başına canlı arama özeti. `skipped` doluysa site sorgulanmadı (hata sayılmaz). */
@Immutable
@Serializable
data class RemoteSite(
    val ok: Boolean = true,
    val count: Int = 0,
    val ms: Int = 0,
    val error: String? = null,
    /** breaker | unsupported | short_query */
    val skipped: String? = null,
)

@Immutable
@Serializable
data class MyListResponse(val items: List<Item> = emptyList())

// ---------------------------------------------------------------- detay

@Immutable
@Serializable
data class EpisodeProgress(
    val position: Double = 0.0,
    val duration: Double = 0.0,
    val pct: Double = 0.0,
)

@Immutable
@Serializable
data class Episode(
    val id: String = "",
    val season: Int = 0,
    val episode: Int = 0,
    val title: String = "",
    val overview: String = "",
    /** Dakika. */
    val runtime: Double? = null,
    val still: String? = null,
    @SerialName("still_url") val stillUrl: String? = null,
    /** false: gerçek görsel yok, /img üretilmiş yer tutucu döner; istemci kendi yer tutucusunu çizer. */
    @SerialName("has_still") val hasStill: Boolean = true,
    @SerialName("air_date") val airDate: String? = null,
    val availability: Availability = Availability(),
    val progress: EpisodeProgress? = null,
) {
    /** still_url öncelikli (still ile aynı adres). */
    val stillPath: String? get() = stillUrl?.takeIf { it.isNotBlank() } ?: still?.takeIf { it.isNotBlank() }
}

@Immutable
@Serializable
data class Season(
    val season: Int = 0,
    val title: String = "",
    val name: String = "",
    val overview: String = "",
    @SerialName("air_date") val airDate: String? = null,
    @SerialName("poster_url") val posterUrl: String? = null,
    /** false: poster_url dizinin afişidir, sezon posteri olarak GÖSTERİLMEZ. */
    @SerialName("has_poster") val hasPoster: Boolean = false,
    @SerialName("episode_count") val episodeCount: Int = 0,
    val episodes: List<Episode> = emptyList(),
)

/** Hedefli detay eylemi (API.md `actions[]`): play_movie | resume_movie | play_episode | resume_episode | play_trailer. */
@Immutable
@Serializable
data class DetailAction(
    val kind: String = "",
    @SerialName("episode_id") val episodeId: String? = null,
    /** Saniye; yalnızca resume_* eylemlerinde. */
    val position: Double = 0.0,
)

@Immutable
@Serializable
data class Resume(
    @SerialName("episode_id") val episodeId: String? = null,
    val position: Double = 0.0,
)

/** Detay yanıtının Item dışındaki alanları. Item alanları aynı JSON'dan ayrıca okunur. */
@Immutable
@Serializable
data class DetailExtras(
    val cast: List<String> = emptyList(),
    val director: String? = null,
    /** Dakika (dizide bölüm başına). 0/null = bilinmiyor. */
    val runtime: Double? = null,
    val seasons: List<Season> = emptyList(),
    val similar: List<Item> = emptyList(),
    val resume: Resume? = null,
    /**
     * true: sunucu bu yapım için arka plan hidrasyonunu (dizi: sezon/bölüm envanteri, film: özet/metadata)
     * şu an çalıştırıyor ya da bu istekle başlattı. Eski sunucuda alan yoktur -> false. Geçicidir:
     * yerel önbelleğe ASLA true yazılmaz ([LocalCache.writeDetail]).
     */
    val hydrating: Boolean = false,
    /**
     * Sunucunun hedefli eylemleri (en önemlisi başta). null = eski sunucu / alan yok (istemci eski alanlardan
     * türetir); boş liste = oynatılacak bir şey yok. Yalnızca TV detay ekranı okur.
     */
    val actions: List<DetailAction>? = null,
)

@Immutable
@Serializable
data class Detail(val item: Item, val extras: DetailExtras) {
    val id: String get() = item.id
    val hydrating: Boolean get() = extras.hydrating
    val seasons: List<Season> get() = extras.seasons
    val similar: List<Item> get() = extras.similar.filter { it.id.isNotBlank() }
}

// ---------------------------------------------------------------- akışlar

@Immutable
@Serializable
data class VideoStream(
    val url: String = "",
    /** hls | mp4 | embed | ... */
    val type: String = "",
    val quality: String = "",
    val label: String = "",
    val provider: String? = null,
    @SerialName("source_id") val sourceId: String? = null,
    val source: String? = null,
    /** episode | movie | trailer */
    val kind: String? = null,
    @SerialName("attempt_token") val attemptToken: String? = null,
    // Ses/altyazı izi alanları (API.md "Ses / altyazı izleri"); eski sunucu göndermeyebilir.
    /** Sağlayıcının tek video dosyasının kararlı kimliği; subtitles[].stream_ids ile eşleşir. */
    @SerialName("variant_id") val variantId: String? = null,
    /** Ses dili kodu; null = bilinmiyor/orijinal. */
    @SerialName("audio_lang") val audioLang: String? = null,
    /** "hard" (altyazı görüntüye gömülü, kapatılamaz) | "soft" (ayrı iz, subtitles[]'te) | "none". */
    @SerialName("sub_mode") val subMode: String? = null,
    /** Yalnız sub_mode == hard: gömülü altyazının dili. */
    @SerialName("hard_lang") val hardLang: String? = null,
)

/** Yanıt kökündeki AYRI (soft) altyazı izi. url sunucu yoludur (/api/subtitles/<id>.vtt): taban adres eklenir. */
@Immutable
@Serializable
data class Subtitle(
    val id: String = "",
    val lang: String? = null,
    val label: String = "",
    /** captions | subtitles */
    val kind: String? = null,
    val format: String? = null,
    val url: String = "",
    /** İzin uygulanabildiği variant_id listesi; null = tüm akışlar. */
    @SerialName("stream_ids") val streamIds: List<String>? = null,
    @SerialName("default") val isDefault: Boolean = false,
    val origin: String? = null,
)

/** Yanıt kökündeki ses izi bilgisi (fragman/embed listelenmez). ExoPlayer kendi ses izlerini de bulur. */
@Immutable
@Serializable
data class AudioInfo(
    val id: String = "",
    val lang: String? = null,
    val label: String = "",
    @SerialName("stream_ids") val streamIds: List<String>? = null,
    @SerialName("default") val isDefault: Boolean = false,
)

@Immutable
@Serializable
data class StreamsResponse(
    val streams: List<VideoStream> = emptyList(),
    val subtitles: List<Subtitle> = emptyList(),
    val audio: List<AudioInfo> = emptyList(),
    /** Saniye. */
    @SerialName("resume_position") val resumePosition: Double = 0.0,
    /** Saniye (katalog runtime'ı dakikadır; karıştırma). */
    val duration: Double = 0.0,
    /** Akış çıkmadığında kaynak bulucunun durumu (yalnız akış yokken; alan yoksa null = genel mesaj). */
    val finder: FinderHint? = null,
)

/** `streams` yanıtındaki kaynak bulucu ipucu: `searching` | `not_found`. */
@Immutable
@Serializable
data class FinderHint(val state: String = "")

// ---------------------------------------------------------------- kaynak bulucu / bildirimler

@Immutable
@Serializable
data class FinderStep(
    /** retry | search | heal */
    val name: String = "",
    val ok: Boolean = false,
    val ms: Int = 0,
    val note: String = "",
)

/** `GET /api/source-finder/{id}?episode=`: `state` idle | searching | found | not_found. */
@Immutable
@Serializable
data class FinderStatus(
    val state: String = "idle",
    val steps: List<FinderStep> = emptyList(),
    @SerialName("updated_at") val updatedAt: Long = 0L,
)

/** Profilin okunmamış bildirimi (`kind` şimdilik yalnızca `source_found`). */
@Immutable
@Serializable
data class AppNotification(
    val id: Long = 0L,
    val kind: String = "",
    @SerialName("canonical_id") val canonicalId: String = "",
    /** Film için boş. */
    @SerialName("episode_id") val episodeId: String? = null,
    val title: String = "",
    /** Film için null. */
    val season: Int? = null,
    val episode: Int? = null,
    val site: String? = null,
    /** retry | search | heal */
    val method: String? = null,
    @SerialName("created_at") val createdAt: Long = 0L,
)

@Immutable
@Serializable
data class NotificationsResponse(
    val items: List<AppNotification> = emptyList(),
    /** Verilen en büyük id (öğe yoksa gönderilen `since`): sonraki yoklamada `since` olarak yollanır. */
    @SerialName("last_id") val lastId: Long = 0L,
)
