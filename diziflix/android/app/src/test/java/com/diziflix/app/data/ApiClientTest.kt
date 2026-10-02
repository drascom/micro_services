package com.diziflix.app.data

import com.diziflix.app.Fixtures
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.ApiJson
import com.diziflix.app.data.net.CatalogQuery
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** İstek yollarını/sorgu parametrelerini/gövdeleri MockWebServer ile doğrular (tizen-client api.js ile aynı uçlar). */
class ApiClientTest {

    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        api = ApiClient(OkHttpClient(), { base }, ApiJson.instance)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun enqueueFixture(name: String) {
        server.enqueue(MockResponse().setResponseCode(200).setBody(Fixtures.text(name)))
    }

    private fun bodyOf(request: okhttp3.mockwebserver.RecordedRequest): JsonObject =
        ApiJson.instance.parseToJsonElement(request.body.readUtf8()).jsonObject

    @Test
    fun boot_sendsProfileAndTvLayout() {
        runBlocking {
            enqueueFixture("boot.json")
            val boot = api.boot("p1")

            val request = server.takeRequest()
            assertEquals("GET", request.method)
            val url = requireNotNull(request.requestUrl)
            assertEquals("/api/boot", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("tv-v1", url.queryParameter("layout"))
            assertEquals("tmdb_tv_95350", boot.hero?.id)
            assertEquals(6, boot.rows.size)
        }
    }

    @Test
    fun profiles_parsesAndSkipsBlankIds() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"profiles":[{"id":"p1","name":"A"},{"id":"","name":"?"}]}"""))
            val profiles = api.profiles()
            assertEquals(listOf("p1"), profiles.map { it.id })
            assertEquals("/api/profiles", server.takeRequest().requestUrl!!.encodedPath)
        }
    }

    @Test
    fun row_usesRowIdPathAndPagingParameters() {
        runBlocking {
            enqueueFixture("row_series.json")
            val res = api.row("series", "p1", offset = 20, limit = 10)

            val url = server.takeRequest().requestUrl!!
            assertEquals("/api/row/series", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("20", url.queryParameter("offset"))
            assertEquals("10", url.queryParameter("limit"))
            assertEquals(52, res.total)
        }
    }

    @Test
    fun detail_parsesSeasonsAndSendsProfile() {
        runBlocking {
            enqueueFixture("detail_series.json")
            val detail = api.detail("tmdb_tv_103516", "p1")

            val url = server.takeRequest().requestUrl!!
            assertEquals("/api/detail/tmdb_tv_103516", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("Star Trek: Strange New Worlds", detail.item.title)
            assertEquals(4, detail.seasons.size)
            assertEquals("tmdb_tv_103516:s4:e1", detail.extras.resume?.episodeId)
        }
    }

    @Test
    fun streams_sendsEpisodeAndKindOnlyWhenGiven() {
        runBlocking {
            enqueueFixture("streams_episode.json")
            val res = api.streams("tmdb_tv_103516", "p1", episodeId = "tmdb_tv_103516:s4:e1")
            val withEpisode = server.takeRequest().requestUrl!!
            assertEquals("/api/streams/tmdb_tv_103516", withEpisode.encodedPath)
            assertEquals("tmdb_tv_103516:s4:e1", withEpisode.queryParameter("episode"))
            assertEquals("p1", withEpisode.queryParameter("profile"))
            assertNull(withEpisode.queryParameter("kind"))
            assertEquals(3, res.streams.size)

            enqueueFixture("streams_trailer.json")
            val trailer = api.streams("tmdb_tv_103516", "p1", kind = "trailer")
            val trailerUrl = server.takeRequest().requestUrl!!
            assertEquals("trailer", trailerUrl.queryParameter("kind"))
            assertNull(trailerUrl.queryParameter("episode"))
            assertEquals("embed", trailer.streams[0].type)
        }
    }

    @Test
    fun playbackReport_postsTokenEventCodeAndEngine() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"ok":true}"""))
            api.playbackReport("0123456789abcdef0123456789abcdef", "failure", "network", "html5")

            val request = server.takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/api/playback-report", request.requestUrl!!.encodedPath)
            assertTrue(request.getHeader("Content-Type")!!.startsWith("application/json"))
            val body = bodyOf(request)
            assertEquals("0123456789abcdef0123456789abcdef", body["attempt_token"]!!.jsonPrimitive.content)
            assertEquals("failure", body["event"]!!.jsonPrimitive.content)
            assertEquals("network", body["code"]!!.jsonPrimitive.content)
            assertEquals("html5", body["engine"]!!.jsonPrimitive.content)
        }
    }

    @Test
    fun playbackReport_sendsDetailOnlyWhenGivenAndClipsIt() {
        runBlocking {
            server.enqueue(MockResponse().setBody("{}"))
            api.playbackReport("0123456789abcdef0123456789abcdef", "failure", "network", "html5", "exo:ERROR_CODE_IO_BAD_HTTP_STATUS/http403")
            val withDetail = bodyOf(server.takeRequest())
            assertEquals("exo:ERROR_CODE_IO_BAD_HTTP_STATUS/http403", withDetail["detail"]!!.jsonPrimitive.content)

            server.enqueue(MockResponse().setBody("{}"))
            api.playbackReport("0123456789abcdef0123456789abcdef", "failure", "network", "html5", "z".repeat(300))
            assertEquals(120, bodyOf(server.takeRequest())["detail"]!!.jsonPrimitive.content.length)

            server.enqueue(MockResponse().setBody("{}"))
            api.playbackReport("0123456789abcdef0123456789abcdef", "success", "", "html5")
            assertNull(bodyOf(server.takeRequest())["detail"])
        }
    }

    @Test
    fun progress_postsPositionAndDurationInSeconds() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"ok":true}"""))
            api.progress("p1", "tmdb_tv_103516", "tmdb_tv_103516:s4:e1", positionSec = 1240, durationSec = 2700)

            val request = server.takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/api/progress", request.requestUrl!!.encodedPath)
            val body = bodyOf(request)
            assertEquals("p1", body["profile"]!!.jsonPrimitive.content)
            assertEquals("tmdb_tv_103516", body["item_id"]!!.jsonPrimitive.content)
            assertEquals("tmdb_tv_103516:s4:e1", body["episode_id"]!!.jsonPrimitive.content)
            assertEquals(1240, body["position"]!!.jsonPrimitive.int)
            assertEquals(2700, body["duration"]!!.jsonPrimitive.int)
        }
    }

    @Test
    fun mylist_addPostsBody_removeDeletesWithProfileQuery() {
        runBlocking {
            server.enqueue(MockResponse().setResponseCode(201).setBody("""{"ok":true,"in_mylist":true}"""))
            api.addToMyList("p1", "tmdb_1")
            val add = server.takeRequest()
            assertEquals("POST", add.method)
            assertEquals("/api/mylist", add.requestUrl!!.encodedPath)
            val body = bodyOf(add)
            assertEquals("p1", body["profile"]!!.jsonPrimitive.content)
            assertEquals("tmdb_1", body["item_id"]!!.jsonPrimitive.content)

            server.enqueue(MockResponse().setBody("""{"ok":true,"in_mylist":false}"""))
            api.removeFromMyList("p1", "tmdb_1")
            val remove = server.takeRequest()
            assertEquals("DELETE", remove.method)
            assertEquals("/api/mylist/tmdb_1", remove.requestUrl!!.encodedPath)
            assertEquals("p1", remove.requestUrl!!.queryParameter("profile"))

            enqueueFixture("mylist_empty.json")
            assertTrue(api.mylist("p1").isEmpty())
            assertEquals("GET", server.takeRequest().method)
        }
    }

    @Test
    fun continueRemove_deletesByProductionIdWithProfileQuery() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"ok":true,"removed":true}"""))
            api.removeFromContinue("tmdb_tv_103516", "p1")
            val request = server.takeRequest()
            assertEquals("DELETE", request.method)
            assertEquals("/api/continue/tmdb_tv_103516", request.requestUrl!!.encodedPath)
            assertEquals("p1", request.requestUrl!!.queryParameter("profile"))

            // idempotent: removed:false da hata değil
            server.enqueue(MockResponse().setBody("""{"ok":true,"removed":false}"""))
            api.removeFromContinue("bilinmeyen", "p1")
            assertEquals("DELETE", server.takeRequest().method)
        }
    }

    @Test
    fun continueRemove_missingEndpointAndServerErrorsBecomeApiException() {
        runBlocking {
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
            try {
                api.removeFromContinue("x", "p1")
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals(404, e.status)
            }
            server.enqueue(
                MockResponse().setResponseCode(400).setBody("""{"error":{"code":"bad_profile","message":"profil yok"}}"""),
            )
            try {
                api.removeFromContinue("x", "yok")
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("bad_profile", e.code)
            }
        }
    }

    @Test
    fun catalog_sendsOnlyNonEmptyFilters() {
        runBlocking {
            enqueueFixture("catalog_movies.json")
            val res = api.catalog(
                CatalogQuery(profile = "p1", type = "movie", genre = "action", year = 2024, availability = "ready", sort = "year", q = "star", offset = 20, limit = 20),
            )
            val url = server.takeRequest().requestUrl!!
            assertEquals("/api/catalog", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("movie", url.queryParameter("type"))
            assertEquals("action", url.queryParameter("genre"))
            assertEquals("2024", url.queryParameter("year"))
            assertEquals("ready", url.queryParameter("availability"))
            assertEquals("year", url.queryParameter("sort"))
            assertEquals("star", url.queryParameter("q"))
            assertEquals("20", url.queryParameter("offset"))
            assertNull(url.queryParameter("mine"))
            assertEquals(49, res.total)

            enqueueFixture("catalog_movies.json")
            api.catalog(CatalogQuery(profile = "p1", mine = true))
            val minimal = server.takeRequest().requestUrl!!
            assertEquals("true", minimal.queryParameter("mine"))
            assertNull(minimal.queryParameter("type"))
            assertNull(minimal.queryParameter("genre"))
            assertNull(minimal.queryParameter("year"))
            assertNull(minimal.queryParameter("q"))
        }
    }

    @Test
    fun search_encodesQuery() {
        runBlocking {
            enqueueFixture("search.json")
            val res = api.search("star trek & co", "p1", limit = 5)
            val url = server.takeRequest().requestUrl!!
            assertEquals("/api/search", url.encodedPath)
            assertEquals("star trek & co", url.queryParameter("q"))
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("5", url.queryParameter("limit"))
            assertEquals(5, res.total)
        }
    }

    @Test
    fun serverErrorEnvelope_becomesApiExceptionWithCodeAndMessage() {
        runBlocking {
            server.enqueue(
                MockResponse().setResponseCode(404).setBody("""{"error":{"code":"not_found","message":"row not found"}}"""),
            )
            try {
                api.row("genre_drama", "p1")
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("not_found", e.code)
                assertEquals("row not found", e.message)
                assertEquals(404, e.status)
            }
        }
    }

    @Test
    fun nonJsonServerError_becomesGenericHttpError() {
        runBlocking {
            server.enqueue(MockResponse().setResponseCode(502).setBody("<html>Bad gateway</html>"))
            try {
                api.health()
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("http_502", e.code)
                assertEquals(502, e.status)
            }
        }
    }

    @Test
    fun invalidJson_becomesBadJson() {
        runBlocking {
            server.enqueue(MockResponse().setBody("bu json değil"))
            try {
                api.profiles()
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("bad_json", e.code)
            }
        }
    }

    @Test
    fun unreachableServer_becomesNetworkError() {
        runBlocking {
            val offline = ApiClient(OkHttpClient(), { "http://127.0.0.1:1" }, ApiJson.instance)
            try {
                offline.health()
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("network", e.code)
            }
        }
    }

    @Test
    fun sourceFinder_usesItemPathAndEpisodeQueryOnlyWhenGiven() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"state":"found","steps":[],"updated_at":5}"""))
            val st = api.sourceFinder("tmdb_tv_1", "tmdb_tv_1:s1:e3")
            val url = server.takeRequest().requestUrl!!
            assertEquals("/api/source-finder/tmdb_tv_1", url.encodedPath)
            assertEquals("tmdb_tv_1:s1:e3", url.queryParameter("episode"))
            assertEquals("found", st.state)

            server.enqueue(MockResponse().setBody("""{"state":"idle","steps":[],"updated_at":0}"""))
            api.sourceFinder("film1")
            val film = server.takeRequest().requestUrl!!
            assertNull(film.queryParameter("episode"))
        }
    }

    @Test
    fun notifications_sendProfileAndSince() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"items":[],"last_id":4}"""))
            val res = api.notifications("p1", 4)
            val request = server.takeRequest()
            assertEquals("GET", request.method)
            val url = request.requestUrl!!
            assertEquals("/api/notifications", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals("4", url.queryParameter("since"))
            assertEquals(4L, res.lastId)
        }
    }

    @Test
    fun markNotificationsRead_postsUptoWithProfileQuery() {
        runBlocking {
            server.enqueue(MockResponse().setBody("""{"ok":true,"marked":2}"""))
            api.markNotificationsRead("p1", 12)
            val request = server.takeRequest()
            assertEquals("POST", request.method)
            val url = request.requestUrl!!
            assertEquals("/api/notifications/read", url.encodedPath)
            assertEquals("p1", url.queryParameter("profile"))
            assertEquals(12, bodyOf(request)["upto"]!!.jsonPrimitive.int)
        }
    }

    @Test
    fun catalog_forwardsTrendingAndPopularSort() {
        runBlocking {
            for (sort in listOf("trending", "popular")) {
                server.enqueue(MockResponse().setBody("""{"items":[],"total":0}"""))
                api.catalog(CatalogQuery(profile = "p1", type = "series", sort = sort))
                val url = server.takeRequest().requestUrl!!
                assertEquals("series", url.queryParameter("type"))
                assertEquals(sort, url.queryParameter("sort"))
            }
        }
    }

    @Test
    fun invalidBaseUrl_isReportedClearly() {
        runBlocking {
            val broken = ApiClient(OkHttpClient(), { "not a url" }, ApiJson.instance)
            try {
                broken.health()
                fail("ApiException bekleniyordu")
            } catch (e: ApiException) {
                assertEquals("bad_base", e.code)
            }
        }
    }

    @Test
    fun baseUrlProviderIsReadOnEveryRequest() {
        runBlocking {
            val second = MockWebServer()
            second.start()
            try {
                var useSecond = false
                val first = server.url("/").toString().trimEnd('/')
                val other = second.url("/").toString().trimEnd('/')
                val switching = ApiClient(OkHttpClient(), { if (useSecond) other else first }, ApiJson.instance)

                server.enqueue(MockResponse().setBody(Fixtures.text("health.json")))
                second.enqueue(MockResponse().setBody(Fixtures.text("health.json")))
                switching.health()
                useSecond = true
                switching.health()

                assertEquals(1, server.requestCount)
                assertEquals(1, second.requestCount)
            } finally {
                second.shutdown()
            }
        }
    }
}
