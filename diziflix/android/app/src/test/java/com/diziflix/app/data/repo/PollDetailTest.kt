package com.diziflix.app.data.repo

import com.diziflix.app.Fixtures
import com.diziflix.app.data.cache.InMemoryCacheStore
import com.diziflix.app.data.cache.LocalCache
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiJson
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** `?poll=1` parametresi, hydrating alanı ve yoklama sonucunun önbelleklere yazılması (MockWebServer + sahte depo). */
class PollDetailTest {

    private lateinit var server: MockWebServer
    private lateinit var store: InMemoryCacheStore
    private lateinit var repo: DiziflixRepository
    private lateinit var api: ApiClient
    private lateinit var local: LocalCache

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        api = ApiClient(OkHttpClient(), { base }, ApiJson.instance)
        store = InMemoryCacheStore()
        local = LocalCache(store, ApiJson.instance, { 1_000L })
        repo = DiziflixRepository(api = api, cache = local, baseUrl = { base })
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** detail_series.json'a `hydrating` alanı ekler (alan None ise eklemez = eski sunucu). */
    private fun seriesBody(hydrating: Boolean?, dropSeasons: Boolean = false): String {
        val base = ApiJson.instance.parseToJsonElement(Fixtures.text("detail_series.json")).jsonObject
        val map = LinkedHashMap(base)
        if (dropSeasons) map["seasons"] = kotlinx.serialization.json.JsonArray(emptyList())
        if (hydrating != null) map["hydrating"] = JsonPrimitive(hydrating)
        return JsonObject(map).toString()
    }

    private fun enqueue(body: String) = server.enqueue(MockResponse().setBody(body))

    @Test
    fun pollAddsPollParameterOnlyWhenAsked() = runBlocking {
        enqueue(seriesBody(null)); enqueue(seriesBody(null))
        api.detail("tmdb_tv_103516", "p1")
        api.detail("tmdb_tv_103516", "p1", poll = true)

        val normal = server.takeRequest().requestUrl!!
        assertEquals("/api/detail/tmdb_tv_103516", normal.encodedPath)
        assertEquals("p1", normal.queryParameter("profile"))
        assertNull("normal istek poll göndermemeli", normal.queryParameter("poll"))
        val polled = server.takeRequest().requestUrl!!
        assertEquals("p1", polled.queryParameter("profile"))
        assertEquals("1", polled.queryParameter("poll"))
    }

    @Test
    fun hydratingParsedFromResponseAndFalseWhenAbsent() = runBlocking {
        enqueue(seriesBody(null))
        assertFalse(api.detail("x", "p1").hydrating)         // eski sunucu: alan yok -> false
        enqueue(seriesBody(true))
        assertTrue(api.detail("x", "p1").hydrating)
        enqueue(seriesBody(false))
        assertFalse(api.detail("x", "p1").hydrating)
    }

    @Test
    fun stillHydratingPollDoesNotTouchCaches() = runBlocking {
        enqueue(seriesBody(true, dropSeasons = true))
        val d = repo.pollDetail("tmdb_tv_103516", "p1")

        assertTrue(d.hydrating)
        assertNull(repo.cachedDetail("tmdb_tv_103516"))
        assertTrue("yerel önbelleğe yazılmamalı", store.files.keys.none { it.startsWith("detail:") })
    }

    @Test
    fun finishedPollUpdatesMemoryAndLocalCache() = runBlocking {
        enqueue(seriesBody(false))
        val d = repo.pollDetail("tmdb_tv_103516", "p1")

        assertFalse(d.hydrating)
        val mem = repo.cachedDetail("tmdb_tv_103516")
        assertNotNull(mem)
        assertTrue(mem!!.seasons.isNotEmpty())
        val stored = local.readDetail("p1", "tmdb_tv_103516")
        assertNotNull("son 30 detay LRU'suna yazılmalı", stored)
        assertEquals(d.seasons.size, stored!!.value.seasons.size)
        assertEquals(listOf("detail:p1:tmdb_tv_103516"), local.detailKeys())
    }

    @Test
    fun hydratingTrueIsNeverPersistedInLocalCache() = runBlocking {
        enqueue(seriesBody(true))
        val emissions = repo.detailFlow("tmdb_tv_103516", "p1").toList()
        assertTrue(emissions.last().value.hydrating)          // taze ağ yanıtı durumu taşır

        val stored: Detail = local.readDetail("p1", "tmdb_tv_103516")!!.value
        assertFalse("çevrimdışı açılışta sonsuz 'yükleniyor' olmamalı", stored.hydrating)
        assertFalse(store.files.getValue("detail:p1:tmdb_tv_103516").contains("hydrating\":true"))
    }

    @Test
    fun oldCacheFilesWithoutFieldStillReadable() = runBlocking {
        // şema sürümü 1 kalır: alan varsayılanlı olduğundan eski (alansız) kayıt okunur.
        local.bindServer(server.url("/").toString().trimEnd('/'))
        local.writeDetail("p1", "x", ApiJson.instance.let {
            val el = it.parseToJsonElement(Fixtures.text("detail_movie.json"))
            Detail(
                it.decodeFromJsonElement(com.diziflix.app.data.model.Item.serializer(), el),
                it.decodeFromJsonElement(com.diziflix.app.data.model.DetailExtras.serializer(), el),
            )
        })
        assertEquals(1, LocalCache.SCHEMA_VERSION)
        val read = local.readDetail("p1", "x")
        assertNotNull(read)
        assertFalse(read!!.value.hydrating)
    }
}
