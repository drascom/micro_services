package com.diziflix.app.play

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class ProverbPickerTest {

    @Test
    fun defaultList_hasAboutTwentyNonBlankUniqueProverbs() {
        val all = Proverbs.ALL
        assertTrue(all.size in 15..30)
        assertTrue(all.all { it.isNotBlank() })
        assertEquals(all.size, all.toSet().size)
    }

    @Test
    fun noRepeatWithinARound() {
        val picker = ProverbPicker(random = Random(1234))
        val round = List(picker.size) { picker.next() }
        assertEquals(picker.size, round.toSet().size)
        assertEquals(Proverbs.ALL.toSet(), round.toSet())
    }

    @Test
    fun secondRoundIsAlsoComplete_andDoesNotStartWithPreviousLast() {
        for (seed in 0..200) {
            val picker = ProverbPicker(random = Random(seed))
            val first = List(picker.size) { picker.next() }
            val second = List(picker.size) { picker.next() }
            assertEquals("tur 2 eksik (seed=$seed)", first.toSet(), second.toSet())
            assertNotEquals("tur sınırında tekrar (seed=$seed)", first.last(), second.first())
        }
    }

    @Test
    fun emptyList_returnsEmptyStringWithoutCrashing() {
        val picker = ProverbPicker(source = emptyList())
        assertEquals("", picker.next())
        assertEquals("", picker.next())
    }

    @Test
    fun singleItemList_repeatsThatItem() {
        val picker = ProverbPicker(source = listOf("Tek."))
        assertEquals("Tek.", picker.next())
        assertEquals("Tek.", picker.next())
    }

    @Test
    fun sameSeedGivesSameSequence() {
        val a = ProverbPicker(random = Random(7))
        val b = ProverbPicker(random = Random(7))
        assertEquals(List(10) { a.next() }, List(10) { b.next() })
    }
}
