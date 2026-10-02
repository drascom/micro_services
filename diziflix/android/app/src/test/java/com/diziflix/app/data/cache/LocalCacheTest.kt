package com.diziflix.app.data.cache

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.Profile
import com.diziflix.app.data.model.ProfilesResponse
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiJson
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalCacheTest {

    private val store = InMemoryCacheStore()
    private var now = 1_000L

    private fun cache(maxDetails: Int = LocalCache.DEFAULT_MAX_DETAILS) =
        LocalCache(store, ApiJson.instance, { now }, maxDetails)

    private fun detail(id: String): Detail {
        val base = ApiClient(okhttp3.OkHttpClient(), { "http://x" }, ApiJson.instance)
        val parsed = base.parseDetail(Fixtures.text("detail_movie.json"))
        return parsed.copy(item = parsed.item.copy(id = id))
    }

    @Test
    fun roundTripKeepsValueAndSavedTime() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        val profiles = ProfilesResponse(listOf(Profile("p1", "Ayhan", avatar = "/img/avatar/a1?w=200&h=200")))
        c.write(CacheKeys.PROFILES, ProfilesResponse.serializer(), profiles)
        now = 9_000L

        val hit = c.read(CacheKeys.PROFILES, ProfilesResponse.serializer())
        assertNotNull(hit)
        assertEquals(profiles, hit!!.value)
        assertEquals(1_000L, hit.savedAtMs)   // yazım zamanı (son başarılı ağ güncellemesi)
    }

    @Test
    fun serverAddressChangeClearsEverythingIncludingQueue() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        c.write(CacheKeys.PROFILES, ProfilesResponse.serializer(), ProfilesResponse(listOf(Profile("p1", "A"))))
        c.writeDetail("p1", "d1", detail("d1"))
        ProgressQueue(store, ApiJson.instance, { now }).enqueue(PendingProgress("p1", "d1", "d1", 10, 100))
        assertTrue(store.files.containsKey(LocalCache.PROGRESS_QUEUE))

        // aynı adres: hiçbir şey silinmez
        val same = cache()
        same.bindServer("http://a")
        assertNotNull(same.read(CacheKeys.PROFILES, ProfilesResponse.serializer()))

        // farklı sunucu: içerik + detaylar + bekleyen ilerleme silinir, yeni adrese bağlanır
        val other = cache()
        other.bindServer("http://b")
        assertNull(other.read(CacheKeys.PROFILES, ProfilesResponse.serializer()))
        assertNull(other.readDetail("p1", "d1"))
        assertFalse(store.files.containsKey(LocalCache.PROGRESS_QUEUE))
        assertEquals("http://b", ApiJson.instance.decodeFromString(CacheMeta.serializer(), store.files[LocalCache.META]!!).base)
    }

    @Test
    fun clearAllKeepsQueueAndServerBinding() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        c.write(CacheKeys.boot("p1"), ProfilesResponse.serializer(), ProfilesResponse(emptyList()))
        ProgressQueue(store, ApiJson.instance, { now }).enqueue(PendingProgress("p1", "d1", "d1", 10, 100))

        c.clearAll()

        assertNull(c.read(CacheKeys.boot("p1"), ProfilesResponse.serializer()))
        assertTrue(store.files.containsKey(LocalCache.PROGRESS_QUEUE))
        // temizlikten sonra aynı adrese yeniden bağlanmak kuyruğu silmez
        val again = cache()
        again.bindServer("http://a")
        assertTrue(store.files.containsKey(LocalCache.PROGRESS_QUEUE))
    }

    @Test
    fun entriesWithDifferentSchemaVersionAreIgnored() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        store.files[CacheKeys.PROFILES] = """{"v":${LocalCache.SCHEMA_VERSION + 1},"t":5,"data":{"profiles":[{"id":"p1","name":"A"}]}}"""
        assertNull(c.read(CacheKeys.PROFILES, ProfilesResponse.serializer()))

        store.files[CacheKeys.PROFILES] = """{"v":${LocalCache.SCHEMA_VERSION},"t":5,"data":{"profiles":[{"id":"p1","name":"A"}]}}"""
        assertEquals("p1", c.read(CacheKeys.PROFILES, ProfilesResponse.serializer())!!.value.profiles[0].id)

        store.files[CacheKeys.PROFILES] = "bozuk{json"
        assertNull(c.read(CacheKeys.PROFILES, ProfilesResponse.serializer()))
    }

    @Test
    fun schemaVersionChangeInMetaWipesContentButKeepsQueue() = runBlocking {
        store.files[LocalCache.META] = """{"v":${LocalCache.SCHEMA_VERSION - 1},"base":"http://a"}"""
        store.files[CacheKeys.PROFILES] = """{"v":0,"t":1,"data":{}}"""
        store.files[LocalCache.PROGRESS_QUEUE] = """{"v":1,"items":[]}"""

        cache().bindServer("http://a")

        assertFalse(store.files.containsKey(CacheKeys.PROFILES))
        assertTrue(store.files.containsKey(LocalCache.PROGRESS_QUEUE))
        assertTrue(store.files[LocalCache.META]!!.contains("\"v\":${LocalCache.SCHEMA_VERSION}"))
    }

    @Test
    fun detailCacheIsLruLimitedTo30() = runBlocking {
        val c = cache()   // varsayılan 30
        c.bindServer("http://a")
        for (i in 1..30) c.writeDetail("p1", "d$i", detail("d$i"))
        assertEquals(30, c.detailKeys().size)

        // d1'e erişmek onu "en yeni" yapar; 31. kayıt en eskiyi (d2) atar
        assertNotNull(c.readDetail("p1", "d1"))
        c.writeDetail("p1", "d31", detail("d31"))

        assertEquals(30, c.detailKeys().size)
        assertNotNull("d1 korunmalı (yakın zamanda okundu)", c.readDetail("p1", "d1"))
        assertNull("d2 en eski olarak atılmalı", c.readDetail("p1", "d2"))
        assertNotNull(c.readDetail("p1", "d31"))
        assertEquals(1, store.files.keys.count { it == CacheKeys.detail("p1", "d1") })
        assertFalse(store.files.containsKey(CacheKeys.detail("p1", "d2")))
    }

    @Test
    fun detailCacheIsPerProfile() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        c.writeDetail("p1", "d1", detail("d1"))
        assertNotNull(c.readDetail("p1", "d1"))
        assertNull(c.readDetail("p2", "d1"))   // başka profilin ilerleme/listem bilgisi karışmaz
    }

    @Test
    fun detailIsRestoredFromDiskAfterRestart() = runBlocking {
        val first = cache()
        first.bindServer("http://a")
        first.writeDetail("p1", "d1", detail("d1"))
        first.writeDetail("p1", "d2", detail("d2"))

        val restarted = cache(maxDetails = 2)
        restarted.bindServer("http://a")
        restarted.writeDetail("p1", "d3", detail("d3"))   // LRU sırası diskteki dizinden sürer: d1 atılır
        assertNull(restarted.readDetail("p1", "d1"))
        assertEquals("d2", restarted.readDetail("p1", "d2")!!.value.item.id)
        assertEquals("d3", restarted.readDetail("p1", "d3")!!.value.item.id)
    }

    @Test
    fun itemModelSurvivesJsonRoundTrip() = runBlocking {
        val c = cache()
        c.bindServer("http://a")
        val boot = Fixtures.parse("boot.json", com.diziflix.app.data.model.BootResponse.serializer())
        c.write(CacheKeys.boot("p1"), com.diziflix.app.data.model.BootResponse.serializer(), boot)
        val back = c.read(CacheKeys.boot("p1"), com.diziflix.app.data.model.BootResponse.serializer())!!.value
        assertEquals(boot, back)
        val item: Item = back.rows.first { it.items.isNotEmpty() }.items[0]
        assertTrue(item.id.isNotBlank())
    }
}
