package com.diziflix.app.ui.tv

import com.diziflix.app.data.model.Item
import com.diziflix.app.domain.TvNavRow
import com.diziflix.app.ui.home.HomeRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** TV ana sayfa satır modeli: boş satır gizlenir, iskelet/hata odaklanamaz/tek kart, "Tümünü Gör" kartı odak sayısına eklenir. */
class TvHomeModelTest {

    private fun item(id: String) = Item(id = id, title = id)

    private fun row(id: String, loaded: Boolean, vararg ids: String, failed: Boolean = false, count: Int? = null) =
        HomeRow(id = id, title = id, items = ids.map { item(it) }, loaded = loaded, failed = failed, count = count)

    @Test
    fun loadedEmptyRowIsHidden() {
        val models = TvHomeModel.build(listOf(row("a", true), row("b", true, "x")))
        assertEquals(listOf("b"), models.map { it.id })
    }

    @Test
    fun skeletonIsNotFocusable_failedHasSingleRetryCard() {
        val models = TvHomeModel.build(listOf(row("load", false, count = 3), row("bad", false, failed = true), row("ok", true, "x", "y")))
        assertEquals(TvRowKind.Skeleton, models[0].kind)
        assertEquals(0, models[0].focusSize)
        assertEquals(3, models[0].skeletonCount)
        assertEquals(TvRowKind.Failed, models[1].kind)
        assertEquals(1, models[1].focusSize)
        assertEquals(2, models[2].focusSize)
    }

    @Test
    fun skeletonCountIsClampedToVisibleCards() {
        assertEquals(8, TvHomeModel.build(listOf(row("a", false, count = 50)))[0].skeletonCount)
        assertEquals(6, TvHomeModel.build(listOf(row("a", false)))[0].skeletonCount)
        assertEquals(1, TvHomeModel.build(listOf(row("a", false, count = 0)))[0].skeletonCount)
    }

    @Test
    fun seriesAndMoviesRowsGetAllItemsCard() {
        val models = TvHomeModel.build(listOf(row("series", true, "a", "b"), row("movies", true, "c"), row("trend", true, "d")))
        assertEquals(TvEndAction.AllSeries, models[0].endAction)
        assertEquals(3, models[0].focusSize)            // 2 kart + "Tüm Diziler"
        assertEquals(TvEndAction.AllMovies, models[1].endAction)
        assertEquals(2, models[1].focusSize)
        assertNull(models[2].endAction)
        assertEquals(1, models[2].focusSize)
    }

    @Test
    fun trendingAndNoteworthyRowsGetSortedCatalogCard() {
        val models = TvHomeModel.build(
            listOf(row("trending_series", true, "a"), row("trending_movies", true, "b"), row("noteworthy_movies", true, "c", "d")),
        )
        assertEquals(TvEndAction.TrendingSeries, models[0].endAction)
        assertEquals(TvEndAction.TrendingMovies, models[1].endAction)
        assertEquals(TvEndAction.NoteworthyMovies, models[2].endAction)
        assertEquals(3, models[2].focusSize)            // 2 kart + "Tümünü Gör"
        assertEquals("catalog/trending_series", TvEndAction.TrendingSeries.route)
        assertEquals("catalog/trending_movies", TvEndAction.TrendingMovies.route)
        assertEquals("catalog/noteworthy_movies", TvEndAction.NoteworthyMovies.route)
        // eski kartlar aynı rotalara gider
        assertEquals("series", TvEndAction.AllSeries.route)
        assertEquals("movies", TvEndAction.AllMovies.route)
    }

    @Test
    fun cardsAreCappedAndDeduplicated() {
        val ids = (1..30).map { "i$it" }.toTypedArray()
        val model = TvHomeModel.build(listOf(row("a", true, *ids)))[0]
        assertEquals(TvHomeModel.MAX_CARDS, model.items.size)
        val dup = TvHomeModel.build(listOf(row("a", true, "x", "x", "y")))[0]
        assertEquals(listOf("x", "y"), dup.items.map { it.id })
    }

    @Test
    fun navRowsPutHeroFirst() {
        val models = TvHomeModel.build(listOf(row("a", true, "x"), row("b", false)))
        assertEquals(listOf(TvNavRow("hero", 1), TvNavRow("a", 1), TvNavRow("b", 0)), TvHomeModel.navRows(models, hasHero = true))
        assertEquals(listOf(TvNavRow("a", 1), TvNavRow("b", 0)), TvHomeModel.navRows(models, hasHero = false))
    }
}

/** Sanal odak durumu ekran yığınından dönünce (rememberSaveable) aynen geri gelir. */
class TvHomeFocusTest {
    @Test
    fun saverRoundTripKeepsRowColumnMemoryAndSuspended() {
        val focus = TvHomeFocus()
        focus.set("continue", 3, 1)
        focus.set("trend", 5, 2)
        focus.suspended = true
        val saved = with(TvHomeFocus.Saver) { androidx.compose.runtime.saveable.SaverScope { true }.save(focus) }!!
        val restored = TvHomeFocus.Saver.restore(saved)!!
        assertEquals("trend", restored.rowId)
        assertEquals(5, restored.col)
        assertEquals(2, restored.rowIndex)
        assertEquals(true, restored.suspended)
        assertEquals(3, restored.memory["continue"])
        assertEquals(5, restored.memory["trend"])
        assertNull(restored.activeRow)        // üst menüdeyken kartlarda odak yok
        restored.suspended = false
        assertEquals("trend", restored.activeRow)
    }

    @Test
    fun emptyStateRestoresAsNoFocus() {
        val saved = with(TvHomeFocus.Saver) { androidx.compose.runtime.saveable.SaverScope { true }.save(TvHomeFocus()) }!!
        val restored = TvHomeFocus.Saver.restore(saved)!!
        assertNull(restored.rowId)
        assertNull(restored.activeRow)
    }
}
