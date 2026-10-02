package com.diziflix.app.domain

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.BootRow
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.RowResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** "İzlemeye Devam Et" kaldırma: menü yalnız continue satırında, kimlik kuralı, önbellek düzeltmesi, metinler. */
class ContinueLogicTest {

    private fun item(id: String, cardKind: String? = null, episodeId: String? = null) =
        Item(id = id, type = "series", title = id, cardKind = cardKind, episodeId = episodeId, cardKey = episodeId ?: id)

    @Test
    fun menuExistsOnlyOnContinueRow() {
        assertTrue(ContinueLogic.hasRemoveMenu("continue"))
        for (other in listOf("trending", "latest_episodes", "series", "movies", "mylist", "noteworthy_movies", "")) {
            assertFalse(other, ContinueLogic.hasRemoveMenu(other))
        }
    }

    @Test
    fun episodeCardSendsProductionIdNotEpisodeId() {
        val card = item("tmdb_tv_5920", cardKind = "episode", episodeId = "tmdb_tv_5920:s1:e3")
        assertEquals("tmdb_tv_5920", ContinueLogic.removalId(card))
    }

    @Test
    fun withoutItemRemovesEveryCardOfTheProductionAndKeepsIdentityWhenNothingMatches() {
        val items = listOf(
            item("a", "episode", "a:s1:e1"),
            item("b"),
            item("a", "episode", "a:s1:e2"),
        )
        assertEquals(listOf("b"), ContinueLogic.withoutItem(items, "a").map { it.id })
        assertSame(items, ContinueLogic.withoutItem(items, "yok"))
    }

    @Test
    fun emptyLoadedContinueRowIsHiddenOthersAreNot() {
        assertTrue(ContinueLogic.isHiddenRow("continue", loaded = true, itemCount = 0))
        assertFalse(ContinueLogic.isHiddenRow("continue", loaded = true, itemCount = 1))
        assertFalse("yüklenmemiş satır iskelet olarak kalır", ContinueLogic.isHiddenRow("continue", loaded = false, itemCount = 0))
        assertFalse(ContinueLogic.isHiddenRow("series", loaded = true, itemCount = 0))
    }

    @Test
    fun rowWithoutDropsItemAndDecrementsTotal() {
        val row = RowResponse(id = "continue", items = listOf(item("a"), item("b")), total = 2)
        val next = ContinueLogic.rowWithout(row, "a")
        assertEquals(listOf("b"), next.items.map { it.id })
        assertEquals(1, next.total)
        assertSame(row, ContinueLogic.rowWithout(row, "yok"))
    }

    @Test
    fun bootWithoutTouchesOnlyContinueRow() {
        val boot = Fixtures.parse("boot.json", BootResponse.serializer())
        val continueRow = boot.rows.first { it.id == "continue" }
        val victim = continueRow.items.first().id
        val next = ContinueLogic.bootWithout(boot, victim)

        val nextContinue = next.rows.first { it.id == "continue" }
        assertEquals(continueRow.items.size - 1, nextContinue.items.size)
        assertTrue(nextContinue.items.none { it.id == victim })
        assertEquals(continueRow.total!! - 1, nextContinue.total)
        // diğer satırlar ve hero aynen
        val others = boot.rows.filter { it.id != "continue" }
        assertEquals(others, next.rows.filter { it.id != "continue" })
        assertEquals(boot.hero, next.hero)
        assertEquals(boot.heroes, next.heroes)
    }

    @Test
    fun bootWithoutDropsLoadedContinueRowWhenItEmpties() {
        val boot = BootResponse(
            rows = listOf(
                BootRow(id = "continue", loaded = true, items = listOf(item("a")), total = 1),
                BootRow(id = "series", loaded = true, items = listOf(item("a"))),
            ),
        )
        val next = ContinueLogic.bootWithout(boot, "a")
        assertEquals(listOf("series"), next.rows.map { it.id })
        assertEquals(listOf("a"), next.rows[0].items.map { it.id })   // aynı yapım başka satırda kalır
    }

    @Test
    fun bootWithoutLeavesUnloadedContinueRowAlone() {
        val boot = BootResponse(rows = listOf(BootRow(id = "continue", loaded = false, count = 3, total = 3)))
        assertSame(boot, ContinueLogic.bootWithout(boot, "a"))
    }

    @Test
    fun messagesAreShortTurkish() {
        assertEquals("Listeden kaldır", ContinueLogic.MENU_REMOVE)
        assertEquals("Vazgeç", ContinueLogic.MENU_CANCEL)
        assertEquals("Listeden kaldırıldı", RemoveOutcome.Removed.message)
        assertEquals("Kaldırılamadı, bağlantıyı kontrol edin", RemoveOutcome.Failed.message)
        assertEquals("İnternet bağlantısı gerekli", RemoveOutcome.Offline.message)
    }
}
