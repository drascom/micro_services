package com.diziflix.app.data.repo

import com.diziflix.app.Fixtures
import com.diziflix.app.data.cache.InMemoryCacheStore
import com.diziflix.app.data.cache.LocalCache
import com.diziflix.app.data.cache.ProgressQueue
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiJson
import com.diziflix.app.domain.ErrorLogic
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Repository + gerçek ApiClient (MockWebServer) + sahte depolama: önce-önbellek/arkada-yenile, profil bazlı
 * boot önbelleği, ağ hatasında korunma, detay LRU'su ve çevrimdışı ilerleme kuyruğu.
 */
class RepositoryOfflineTest {

    private lateinit var server: MockWebServer
    private lateinit var store: InMemoryCacheStore
    private lateinit var repo: DiziflixRepository
    private lateinit var queue: ProgressQueue
    private var base = ""
    private var baseForRepo = ""
    private var now = 1_000L

    /** true: sunucu isteği alır almaz bağlantıyı koparır (ağ yok benzetimi; istemci IOException görür). */
    @Volatile private var offline = false
    private val requests = ArrayList<RecordedRequest>()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                synchronized(requests) { requests += request }
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                val path = request.requestUrl!!.encodedPath
                return when {
                    path == "/api/profiles" -> MockResponse().setBody(Fixtures.text("profiles.json"))
                    path == "/api/boot" -> MockResponse().setBody(Fixtures.text("boot.json"))
                    path.startsWith("/api/row/") -> MockResponse().setBody(Fixtures.text("row_series.json"))
                    path.startsWith("/api/detail/") -> MockResponse().setBody(Fixtures.text("detail_movie.json"))
                    path == "/api/progress" -> MockResponse().setBody("""{"ok":true}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"error":{"code":"not_found","message":"yok"}}""")
                }
            }
        }
        server.start()
        base = server.url("/").toString().trimEnd('/')
        baseForRepo = base
        store = InMemoryCacheStore()
        val api = ApiClient(OkHttpClient(), { baseForRepo }, ApiJson.instance)
        queue = ProgressQueue(store, ApiJson.instance, { now })
        repo = DiziflixRepository(
            api = api,
            cache = LocalCache(store, ApiJson.instance, { now }),
            queue = queue,
            baseUrl = { baseForRepo },
            clock = { now },
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun progressBodies(): List<Pair<String, Int>> = synchronized(requests) {
        requests.filter { it.requestUrl!!.encodedPath == "/api/progress" }.map {
            val o = ApiJson.instance.parseToJsonElement(it.body.clone().readUtf8()).jsonObject
            o["episode_id"]!!.jsonPrimitive.content + "@" + o["profile"]!!.jsonPrimitive.content to o["position"]!!.jsonPrimitive.int
        }
    }

    @Test
    fun bootEmitsCacheFirstThenRefreshesFromNetwork() = runBlocking {
        val cold = repo.bootFlow("p1").toList()
        assertEquals(1, cold.size)                     // önbellek yok: yalnızca ağ
        assertFalse(cold[0].fromCache)

        now = 50_000L
        val warm = repo.bootFlow("p1").toList()
        assertEquals(2, warm.size)
        assertTrue(warm[0].fromCache)                  // ÖNCE yerel veri
        assertEquals(1_000L, warm[0].savedAtMs)
        assertFalse(warm[1].fromCache)                 // sonra ağdan yenilenmiş
        assertEquals(50_000L, warm[1].savedAtMs)
        assertEquals(warm[0].value.rows.size, warm[1].value.rows.size)
    }

    @Test
    fun networkFailureKeepsCachedBootAndReportsConnectivityError() = runBlocking {
        repo.bootFlow("p1").toList()                   // önbelleği doldur
        offline = true

        val emissions = repo.bootFlow("p1").toList()
        val last = emissions.last()
        assertTrue(last.fromCache)
        assertTrue(last.value.rows.isNotEmpty())       // yerel veri korunur
        val error = last.refreshError
        assertNotNull(error)
        assertTrue(ErrorLogic.isConnectivityError(error!!))   // çevrimdışı şeridi için
    }

    @Test
    fun noCacheAndNoNetworkFailsWithClearError() = runBlocking {
        offline = true
        var failure: Throwable? = null
        val emissions = repo.profilesFlow().catch { failure = it }.toList()
        assertTrue(emissions.isEmpty())
        assertNotNull(failure)
        assertTrue(ErrorLogic.isConnectivityError(failure!!))
        val classified = ErrorLogic.classify(failure!!, online = false, baseUrl = base)
        assertEquals("Ağa bağlı değilsiniz", classified.message)
        assertEquals("Sunucuya ulaşılamıyor: $base", ErrorLogic.classify(failure!!, online = true, baseUrl = base).message)
    }

    @Test
    fun bootCacheIsPerProfile() = runBlocking {
        repo.bootFlow("p1").toList()
        offline = true

        val p1 = repo.bootFlow("p1").toList()
        assertTrue(p1.last().fromCache)                    // p1 yerelden gelir
        assertNotNull(p1.last().refreshError)

        var failure: Throwable? = null
        val p2 = repo.bootFlow("p2").catch { failure = it }.toList()
        assertTrue("p2'nin önbelleği yok: p1'inki gösterilmemeli", p2.isEmpty())
        assertNotNull(failure)
    }

    @Test
    fun rowsAndProfilesAreCachedAndServedOffline() = runBlocking {
        repo.profilesFlow().toList()
        repo.rowFlow("series", "p1").toList()
        offline = true

        val profiles = repo.profilesFlow().toList().last()
        assertTrue(profiles.fromCache)
        assertEquals(listOf("p1", "p2"), profiles.value.take(2).map { it.id })

        val row = repo.rowFlow("series", "p1").toList().last()
        assertTrue(row.fromCache)
        assertTrue(row.value.items.isNotEmpty())
    }

    @Test
    fun warmProfilesFillsCacheWithoutProfilesScreen() = runBlocking {
        // Profil zaten seçili açılış: Profiller ekranı hiç açılmadı, yalnızca arka plan ön-doldurma çalıştı.
        assertTrue(repo.warmProfiles())
        assertTrue(store.files.containsKey("profiles"))

        offline = true
        val emissions = repo.profilesFlow().toList()         // "Profil değiştir" çevrimdışı
        val last = emissions.last()
        assertTrue(last.fromCache)
        assertEquals(listOf("p1", "p2"), last.value.take(2).map { it.id })
        assertNotNull(last.refreshError)                     // çevrimdışı şeridi için
        assertTrue(ErrorLogic.isConnectivityError(last.refreshError!!))
    }

    @Test
    fun warmProfilesOfflineIsSilentAndKeepsExistingCache() = runBlocking {
        offline = true
        assertFalse(repo.warmProfiles())                     // önbellek yok + ağ yok: hata fırlatmaz
        assertFalse(store.files.containsKey("profiles"))

        offline = false
        assertTrue(repo.warmProfiles())
        val saved = store.files["profiles"]
        offline = true
        now = 9_000L
        assertFalse(repo.warmProfiles())                     // ağ hatası mevcut kopyayı bozmaz
        assertEquals(saved, store.files["profiles"])
        assertTrue(repo.profilesFlow().toList().last().fromCache)
    }

    @Test
    fun warmProfilesRefreshesStaleCacheFromNetwork() = runBlocking {
        repo.warmProfiles()
        now = 77_000L
        assertTrue(repo.warmProfiles())
        offline = true
        assertEquals(77_000L, repo.profilesFlow().toList().last().savedAtMs)
    }

    @Test
    fun warmProfilesWithoutCacheIsNoOp() = runBlocking {
        val noCache = DiziflixRepository(api = ApiClient(OkHttpClient(), { base }, ApiJson.instance), cache = null)
        assertFalse(noCache.warmProfiles())
        assertTrue(synchronized(requests) { requests.isEmpty() })
    }

    @Test
    fun detailCacheIsLru30AndServedOffline() = runBlocking {
        for (i in 1..31) repo.detailFlow("item$i", "p1").toList()
        offline = true

        var failure: Throwable? = null
        repo.detailFlow("item1", "p1").catch { failure = it }.toList()   // en eski atıldı
        assertNotNull("item1 LRU ile atılmış olmalı", failure)

        val recent = repo.detailFlow("item31", "p1").toList().last()
        assertTrue(recent.fromCache)
        assertEquals("tmdb_1322562", recent.value.item.id)
    }

    @Test
    fun serverAddressChangeClearsOfflineCache() = runBlocking {
        repo.profilesFlow().toList()
        offline = true
        assertTrue(repo.profilesFlow().toList().last().fromCache)

        // başka adres: önceki sunucunun kopyası gösterilmemeli
        baseForRepo = "http://127.0.0.1:1"
        var failure: Throwable? = null
        val emissions = repo.profilesFlow().catch { failure = it }.toList()
        assertTrue(emissions.isEmpty())
        assertNotNull(failure)
    }

    @Test
    fun progressIsQueuedOfflineAndSentInOrderWhenOnline() = runBlocking {
        offline = true
        repo.saveProgress("p1", "s1", "s1:s1:e1", 100, 3000)
        now += 10
        repo.saveProgress("p1", "s1", "s1:s1:e1", 400, 3000)      // aynı bölüm: tek kayıt, son konum
        now += 10
        repo.saveProgress("p1", "s2", "s2:s1:e2", 50, 2000)
        now += 10
        repo.saveProgress("p2", "s1", "s1:s1:e1", 7, 3000)        // başka profil: karışmaz
        assertEquals(3, queue.pending().size)

        offline = false
        synchronized(requests) { requests.clear() }
        val result = repo.flushProgress()!!

        assertEquals(3, result.sent)
        assertEquals(0, result.remaining)
        assertEquals(
            listOf("s1:s1:e1@p1" to 400, "s2:s1:e2@p1" to 50, "s1:s1:e1@p2" to 7),
            progressBodies(),
        )
    }

    @Test
    fun successfulSaveDiscardsStaleQueuedRecordAndFlushesRest() = runBlocking {
        offline = true
        repo.saveProgress("p1", "s1", "e1", 100, 3000)
        repo.saveProgress("p1", "s2", "e2", 200, 3000)
        offline = false
        synchronized(requests) { requests.clear() }

        repo.saveProgress("p1", "s1", "e1", 900, 3000)   // daha yeni konum gitti -> eski kayıt silinir, kalan gönderilir

        assertEquals(listOf("e1@p1" to 900, "e2@p1" to 200), progressBodies())
        assertEquals(0, repo.flushProgress()!!.sent)
    }

    @Test
    fun nonConnectivityProgressErrorIsNotQueued() = runBlocking {
        val failing = DiziflixRepository(
            api = ApiClient(OkHttpClient(), { base + "/yok" }, ApiJson.instance),
            cache = null,
            queue = ProgressQueue(store, ApiJson.instance, { now }),
        )
        try {
            failing.saveProgress("p1", "s1", "e1", 1, 10)
            throw AssertionError("404 hatası yukarı iletilmeli")
        } catch (e: com.diziflix.app.data.net.ApiException) {
            assertEquals(404, e.status)
        }
        assertEquals(0, failing.flushProgress()!!.sent + failing.flushProgress()!!.remaining)
    }
}
