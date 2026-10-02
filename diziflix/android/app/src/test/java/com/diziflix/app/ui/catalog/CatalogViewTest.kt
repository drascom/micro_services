package com.diziflix.app.ui.catalog

import com.diziflix.app.domain.CatalogLogic
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Ana sayfa "Tümü" kartının katalog görünümleri: tür + sunucu `sort` (trending | popular) + başlık. */
class CatalogViewTest {

    @Test
    fun trendingAndNoteworthyRows_openSortedCatalogOfTheirType() {
        val ts = requireNotNull(CatalogView.forRow("trending_series"))
        assertEquals("series", ts.type)
        assertEquals("trending", ts.defaultSort)
        assertEquals("Haftanın Trendleri · Diziler", ts.title)

        val tm = requireNotNull(CatalogView.forRow("trending_movies"))
        assertEquals("movie", tm.type)
        assertEquals("trending", tm.defaultSort)

        val nm = requireNotNull(CatalogView.forRow("noteworthy_movies"))
        assertEquals("movie", nm.type)
        assertEquals("popular", nm.defaultSort)
        assertEquals("Dikkate Değer Filmler", nm.title)
    }

    @Test
    fun otherRows_haveNoSortedCatalog_andPlainViewsKeepNewSort() {
        assertNull(CatalogView.forRow("series"))
        assertNull(CatalogView.forRow("continue"))
        assertNull(CatalogView.forRow(null))
        assertEquals("new", CatalogView.Movies.defaultSort)
        assertEquals("new", CatalogView.Series.defaultSort)
        assertEquals("new", CatalogView.MyList.defaultSort)
    }

    @Test
    fun sortOptions_includeTheViewsOwnSort() {
        assertEquals(listOf("new", "year", "title"), CatalogLogic.sortOptionsFor("new").map { it.first })
        assertEquals(listOf("trending", "new", "year", "title"), CatalogLogic.sortOptionsFor("trending").map { it.first })
        assertEquals(listOf("popular", "new", "year", "title"), CatalogLogic.sortOptionsFor("popular").map { it.first })
        // ortak liste değişmedi
        assertEquals(listOf("new", "year", "title"), CatalogLogic.SORT_OPTIONS.map { it.first })
    }
}
