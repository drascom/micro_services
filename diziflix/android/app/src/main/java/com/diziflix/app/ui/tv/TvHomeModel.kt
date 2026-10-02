package com.diziflix.app.ui.tv

import com.diziflix.app.data.model.Item
import com.diziflix.app.domain.TvNavRow
import com.diziflix.app.domain.TvRowMetrics
import com.diziflix.app.ui.home.HomeRow
import com.diziflix.app.ui.nav.Routes

/** TV ana sayfa satırının türü: kartlar / yükleniyor (iskelet) / yüklenemedi ("Tekrar dene" kartı). */
enum class TvRowKind { Cards, Skeleton, Failed }

/**
 * Satır sonundaki "Tümünü Gör" kartı (Tizen `catalogEndAction`): hangi katalog rotasına gider. Trend/dikkate değer
 * satırlarının kartı trend/popüler sıralı kataloğa gider (`/api/catalog?sort=trending|popular`).
 */
enum class TvEndAction(val label: String, val route: String) {
    AllSeries("Tüm Diziler", Routes.SERIES),
    AllMovies("Tüm Filmler", Routes.MOVIES),
    TrendingSeries("Tümünü Gör", Routes.catalog("trending_series")),
    TrendingMovies("Tümünü Gör", Routes.catalog("trending_movies")),
    NoteworthyMovies("Tümünü Gör", Routes.catalog("noteworthy_movies")),
}

/** Ekranda yer kaplayan bir satırın TV modeli. */
class TvRowModel(
    val id: String,
    val title: String,
    val kind: TvRowKind,
    val items: List<Item>,
    val endAction: TvEndAction?,
    val skeletonCount: Int,
) {
    /** Odaklanabilir öğe sayısı: kartlar (+ sondaki kart), yeniden deneme kartı; iskelet odaklanamaz. */
    val focusSize: Int
        get() = when (kind) {
            TvRowKind.Cards -> items.size + if (endAction != null) 1 else 0
            TvRowKind.Failed -> 1
            TvRowKind.Skeleton -> 0
        }
}

/** Ana sayfa satır listesi -> TV satır modelleri (saf; birim testli). */
object TvHomeModel {
    /** Tizen row.js `MAX_DOM_CARDS`. */
    const val MAX_CARDS = 20

    /** Yüklenmiş ve boş satır Tizen'de gizlenir (yer kaplamaz); diğerleri sırasıyla kalır. */
    fun build(rows: List<HomeRow>): List<TvRowModel> = rows.mapNotNull { row ->
        when {
            row.loaded && row.items.isEmpty() -> null
            row.loaded -> TvRowModel(
                id = row.id,
                title = row.title,
                kind = TvRowKind.Cards,
                items = row.items.distinctBy { it.listKey }.take(MAX_CARDS),
                endAction = when (row.id) {
                    "series" -> TvEndAction.AllSeries
                    "movies" -> TvEndAction.AllMovies
                    "trending_series" -> TvEndAction.TrendingSeries
                    "trending_movies" -> TvEndAction.TrendingMovies
                    "noteworthy_movies" -> TvEndAction.NoteworthyMovies
                    else -> null
                },
                skeletonCount = 0,
            )
            row.failed -> TvRowModel(row.id, row.title, TvRowKind.Failed, emptyList(), null, 0)
            else -> TvRowModel(
                id = row.id,
                title = row.title,
                kind = TvRowKind.Skeleton,
                items = emptyList(),
                endAction = null,
                skeletonCount = (row.count ?: SKELETON_DEFAULT).coerceIn(1, TvRowMetrics.visibleCards()),
            )
        }
    }

    private const val SKELETON_DEFAULT = 6

    /** Gezinme satırları: hero (varsa) + satırlar; `size` 0 olanlar atlanır. */
    fun navRows(models: List<TvRowModel>, hasHero: Boolean): List<TvNavRow> {
        val list = ArrayList<TvNavRow>(models.size + 1)
        if (hasHero) list.add(TvNavRow(com.diziflix.app.domain.TvHomeNavigation.HERO_ID, 1))
        models.forEach { list.add(TvNavRow(it.id, it.focusSize)) }
        return list
    }
}
