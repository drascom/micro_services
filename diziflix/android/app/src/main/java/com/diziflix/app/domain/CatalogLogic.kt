package com.diziflix.app.domain

import com.diziflix.app.data.model.Item

/** Kart/katalog mantığı (saf Kotlin, birim testli). */
object CatalogLogic {

    /** İzlenebilirlik süzgeci seçenekleri (kimlik, ad) — tizen-client catalog.js `available` ile aynı. */
    val AVAILABILITY_OPTIONS: List<Pair<String, String>> = listOf(
        "" to "Tümü",
        "ready" to "İzlenebilir",
        "check_required" to "Kontrol gereken",
        "unavailable" to "İzleme kaynağı yok",
    )

    /** Sıralama seçenekleri (kimlik, ad) — tizen-client catalog.js `order` ile aynı. */
    val SORT_OPTIONS: List<Pair<String, String>> = listOf(
        "new" to "Yeni eklenen",
        "year" to "Yıl",
        "title" to "Ad",
    )

    /**
     * Sıralama seçenekleri, görünümün varsayılan sırasıyla ("Tümü" kartıyla açılan trend/popüler katalog): varsayılan
     * `trending`/`popular` ise o seçenek en başa eklenir ([SORT_OPTIONS] değişmez); aksi halde [SORT_OPTIONS].
     */
    fun sortOptionsFor(defaultSort: String): List<Pair<String, String>> = when (defaultSort) {
        "trending" -> listOf("trending" to "Haftanın trendleri") + SORT_OPTIONS
        "popular" -> listOf("popular" to "Popüler") + SORT_OPTIONS
        else -> SORT_OPTIONS
    }

    /**
     * Kartın açacağı bölüm: bölüm kartı (card_kind=episode) ya da "Devam Et" satırındaki dizi kartı dizinin
     * normal özet sayfasını ilgili bölüme odaklanmış açar (tizen-client home.js cardParams). Yoksa null.
     */
    fun cardEpisodeId(item: Item, rowId: String): String? {
        if (item.cardKind == "episode" && !item.episodeId.isNullOrBlank()) return item.episodeId
        if (rowId == "continue" && item.isSeries) {
            val ep = item.progress?.episodeId
            if (!ep.isNullOrBlank()) return ep
        }
        return null
    }

    /** Kaynak durumu notu (tizen-client catalog.js ile aynı metinler); not gerekmiyorsa null. */
    fun statusNote(item: Item, isSearch: Boolean): String? {
        val availability = item.availability
        return when {
            availability.isUnavailable -> when {
                isSearch && item.isSeries -> "Açınca bölümler yüklenecek"
                availability.hasTrailer -> "Yalnızca fragman"
                else -> "İzleme kaynağı yok"
            }
            availability.needsCheck -> "Kaynak kontrol ediliyor"
            else -> null
        }
    }
}
