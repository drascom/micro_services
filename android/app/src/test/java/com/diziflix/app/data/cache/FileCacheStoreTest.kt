package com.diziflix.app.data.cache

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class FileCacheStoreTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun writeReadDeleteAndNamesRoundTrip() = runBlocking {
        val store = FileCacheStore(tmp.newFolder("cache"), Dispatchers.Unconfined)
        store.write("detail:p1:tmdb_tv_95350:s1:e3", """{"a":1}""")
        store.write("profiles", "[]")
        store.write("ş/ğ yol", "x")

        assertEquals("""{"a":1}""", store.read("detail:p1:tmdb_tv_95350:s1:e3"))
        assertEquals(setOf("detail:p1:tmdb_tv_95350:s1:e3", "profiles", "ş/ğ yol"), store.names().toSet())

        // üzerine yazma atomiktir ve tek dosya bırakır (geçici dosya kalmaz)
        store.write("profiles", "[1]")
        assertEquals("[1]", store.read("profiles"))
        store.delete("profiles")
        assertNull(store.read("profiles"))
        assertEquals(2, store.names().size)
    }

    @Test
    fun noTemporaryFilesRemainAfterWrite() = runBlocking {
        val dir = tmp.newFolder("c2")
        val store = FileCacheStore(dir, Dispatchers.Unconfined)
        repeat(5) { store.write("k", "v$it") }
        assertTrue(dir.listFiles()!!.all { it.name.endsWith(".json") })
        assertEquals("v4", store.read("k"))
    }

    @Test
    fun nameEncodingIsReversibleAndFileSystemSafe() {
        for (name in listOf("a", "boot:p1", "row:p_dbecf70ba9:continue", "ÇğŞ ı/..\\x", "tmdb_tv_1:s4:e10")) {
            val encoded = FileCacheStore.encodeName(name)
            assertTrue(encoded, encoded.all { it.isLetterOrDigit() || it == '_' || it == '-' || it == '~' })
            assertEquals(name, FileCacheStore.decodeName(encoded))
        }
        assertNull(FileCacheStore.decodeName("bozuk~Z"))
        assertNull(FileCacheStore.decodeName("bozuk~4"))
    }
}
