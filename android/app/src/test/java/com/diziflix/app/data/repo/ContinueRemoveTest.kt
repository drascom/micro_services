package com.diziflix.app.data.repo

import com.diziflix.app.Fixtures
import com.diziflix.app.data.cache.CacheKeys
import com.diziflix.app.data.cache.InMemoryCacheStore
import com.diziflix.app.data.cache.LocalCache
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.RowResponse
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.ApiJson
import com.diziflix.app.domain.ContinueRemover
import com.diziflix.app.domain.RemoveOutcome
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * "İzlemeye Devam Et"ten kaldırma: gerçek ApiClient (MockWebServer) + sahte depolama. Başarıda öğe saklı boot/row
 * kopyalarından çıkar (çevrimdışı açılışta geri gelmez); hata/çevrimdışıda önbelleğe DOKUNULMAZ.
 */
class ContinueRemoveTest {

    private lateinit var server: MockWebServer
    private lateinit var store: InMemoryCacheStore
    private lateinit var repo: DiziflixRepository
    private var now = 1_000L
    private val requests = ArrayList<RecordedRequest>()

    /** Sıradaki DELETE /api/continue yanıtı. */
    @Volatile private var continueResponse: () -> MockResponse = { MockResponse().setBody("""{"ok":true,"removed":true}""") }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                synchronized(requests) { requests += request }
                val path = request.requestUrl!!.encodedPath
                return when {
                    path == "/api/boot" -> MockResponse().setBody(Fixtures.text("boot.json"))
                    path.startsWith("/api/row/") -> MockResponse().setBody(continueRowJson())
                    path.startsWith("/api/continue/") -> continueResponse()
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        store = InMemoryCacheStore()
        repo = DiziflixRepository(
            api = ApiClient(OkHttpClient(), { base }, ApiJson.instance),
            cache = LocalCache(store, ApiJson.instance, { now }),
            baseUrl = { base },
            clock = { now },
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** row/continue yanıtı: boot.json'daki continue satırının öğeleri. */
    private fun continueRowJson(): String {
        val boot = Fixtures.parse("boot.json", BootResponse.serializer())
        val row = boot.rows.first { it.id == "continue" }
        return ApiJson.instance.encodeToString(
            RowResponse.serializer(),
            RowResponse(id = "continue", title = row.title, items = row.items, total = row.items.size),
        )
    }

    private fun deleteRequests() = synchronized(requests) {
        requests.filter { it.method == "DELETE" }
    }

    private suspend fun warmCaches() {
        repo.bootFlow("p1").toList()
        repo.rowFlow("continue", "p1").toList()
    }

    private suspend fun cachedContinueIds(): Pair<List<String>, List<String>> {
        val cache = LocalCache(store, ApiJson.instance, { now })
        val boot = cache.read(CacheKeys.boot("p1"), BootResponse.serializer())!!.value
        val row = cache.read(CacheKeys.row("p1", "continue"), RowResponse.serializer())!!.value
        return boot.rows.first { it.id == "continue" }.items.map { it.id } to row.items.map { it.id }
    }

    @Test
    fun successSendsProductionIdAndRemovesItFromBothCachedCopies() = runBlocking {
        warmCaches()
        val before = cachedContinueIds()
        val victim = before.first.first()
        assertTrue(victim in before.second)
        val savedAt = now
        now = 99_000L

        repo.removeFromContinue(victim, "p1")

        val request = deleteRequests().single()
        assertEquals("/api/continue/$victim", request.requestUrl!!.encodedPath)
        assertEquals("p1", request.requestUrl!!.queryParameter("profile"))
        val after = cachedContinueIds()
        assertFalse(victim in after.first)
        assertFalse(victim in after.second)
        assertEquals(before.first.size - 1, after.first.size)
        assertEquals(before.second.size - 1, after.second.size)

        // "son güncelleme" zamanı KORUNUR: ağdan yenilenmedi
        val cache = LocalCache(store, ApiJson.instance, { now })
        assertEquals(savedAt, cache.read(CacheKeys.boot("p1"), BootResponse.serializer())!!.savedAtMs)
    }

    @Test
    fun removedItemStaysGoneWhenOpenedOfflineFromCache() = runBlocking {
        warmCaches()
        val victim = cachedContinueIds().first.first()
        repo.removeFromContinue(victim, "p1")

        server.shutdown()   // çevrimdışı açılış: yalnızca önbellek
        val cachedBoot = repo.bootFlow("p1").toList().first()
        assertTrue(cachedBoot.fromCache)
        assertTrue(cachedBoot.value.rows.first { it.id == "continue" }.items.none { it.id == victim })
    }

    @Test
    fun serverErrorThrowsAndLeavesCachesUntouched() = runBlocking {
        warmCaches()
        val snapshot = LinkedHashMap(store.files)
        continueResponse = { MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""") }   // uç henüz yok

        try {
            repo.removeFromContinue(cachedContinueIds().first.first(), "p1")
            fail("ApiException bekleniyordu")
        } catch (e: ApiException) {
            assertEquals(404, e.status)
        }
        assertEquals(snapshot, store.files)
    }

    @Test
    fun networkFailureThrowsAndLeavesCachesUntouched() = runBlocking {
        warmCaches()
        val snapshot = LinkedHashMap(store.files)
        continueResponse = { MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST) }

        try {
            repo.removeFromContinue(cachedContinueIds().first.first(), "p1")
            fail("ApiException bekleniyordu")
        } catch (e: ApiException) {
            assertEquals("network", e.code)
        }
        assertEquals(snapshot, store.files)
    }

    @Test
    fun removerReportsRemovedOnSuccessAndFailedOnServerError() = runBlocking {
        warmCaches()
        val remover = ContinueRemover(repo) { true }
        val card = Fixtures.parse("boot.json", BootResponse.serializer()).rows.first { it.id == "continue" }.items.first()

        assertEquals(RemoveOutcome.Removed, remover.remove("p1", card))
        continueResponse = { MockResponse().setResponseCode(500) }
        assertEquals(RemoveOutcome.Failed, remover.remove("p1", card))
    }

    @Test
    fun removerOfflineMakesNoRequestAndKeepsCaches() = runBlocking {
        warmCaches()
        val snapshot = LinkedHashMap(store.files)
        val card = Fixtures.parse("boot.json", BootResponse.serializer()).rows.first { it.id == "continue" }.items.first()

        assertEquals(RemoveOutcome.Offline, ContinueRemover(repo) { false }.remove("p1", card))
        assertTrue(deleteRequests().isEmpty())
        assertEquals(snapshot, store.files)
    }

    @Test
    fun episodeCardSendsProductionIdNotEpisodeId() = runBlocking {
        val card = Item(
            id = "tmdb_tv_5920", type = "series", title = "Dizi",
            cardKind = "episode", episodeId = "tmdb_tv_5920:s1:e3", cardKey = "tmdb_tv_5920:s1:e3",
        )
        assertEquals(RemoveOutcome.Removed, ContinueRemover(repo) { true }.remove("p1", card))

        val path = deleteRequests().single().requestUrl!!.encodedPath
        assertEquals("/api/continue/tmdb_tv_5920", path)
    }
}
