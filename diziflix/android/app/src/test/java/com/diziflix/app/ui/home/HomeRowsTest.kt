package com.diziflix.app.ui.home

import com.diziflix.app.data.model.Item
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ana sayfa satır listesi: kaldırma yalnız continue satırını etkiler, boşalan satır gizlenir. */
class HomeRowsTest {

    private fun card(id: String) = Item(id = id, type = "series", title = id)

    private fun row(id: String, vararg ids: String, loaded: Boolean = true, total: Int? = null) =
        HomeRow(id = id, title = id, items = ids.map { card(it) }, loaded = loaded, total = total)

    @Test
    fun cardLeavesOnlyTheContinueRowOthersKeepTheSameProduction() {
        val rows = listOf(row("continue", "a", "b", total = 2), row("series", "a", "c"))
        val next = HomeRows.withoutContinueCard(rows, "a")

        assertEquals(listOf("b"), next[0].items.map { it.id })
        assertEquals(1, next[0].total)
        assertSame(rows[1], next[1])
        assertEquals(listOf("a", "c"), next[1].items.map { it.id })
    }

    @Test
    fun emptiedContinueRowIsHidden() {
        val rows = listOf(row("continue", "a"), row("series", "c"))
        val next = HomeRows.withoutContinueCard(rows, "a")
        assertEquals(listOf("series"), next.map { it.id })
    }

    @Test
    fun unknownItemChangesNothing() {
        val rows = listOf(row("continue", "a"), row("series", "c"))
        assertSame(rows, HomeRows.withoutContinueCard(rows, "yok"))
    }

    @Test
    fun pruneHiddenDropsOnlyLoadedEmptyContinueRow() {
        val rows = listOf(
            row("continue"),                       // yüklenmiş + boş: gizli
            row("trending"),                       // başka satır: boş da olsa kalır ("Bu satırda içerik yok")
            row("series", loaded = false),         // iskelet
        )
        assertEquals(listOf("trending", "series"), HomeRows.pruneHidden(rows).map { it.id })

        val skeleton = listOf(row("continue", loaded = false))
        assertSame(skeleton, HomeRows.pruneHidden(skeleton))
        assertTrue(HomeRows.pruneHidden(emptyList()).isEmpty())
    }
}
