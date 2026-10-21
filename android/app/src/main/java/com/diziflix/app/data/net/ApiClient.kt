package com.diziflix.app.data.net

import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.CatalogResponse
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.DetailExtras
import com.diziflix.app.data.model.FinderStatus
import com.diziflix.app.data.model.GenresResponse
import com.diziflix.app.data.model.Genre
import com.diziflix.app.data.model.Health
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.MyListResponse
import com.diziflix.app.data.model.NotificationsResponse
import com.diziflix.app.data.model.Profile
import com.diziflix.app.data.model.ProfilesResponse
import com.diziflix.app.data.model.RowResponse
import com.diziflix.app.data.model.SearchResponse
import com.diziflix.app.data.model.StreamsResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.CacheControl
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.io.InterruptedIOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** /api/catalog sorgusu. Boş değerler istekten çıkarılır. */
data class CatalogQuery(
    val profile: String,
    val type: String = "",
    val genre: String = "",
    val year: Int? = null,
    val availability: String = "",
    val sort: String = "new",
    val q: String = "",
    val mine: Boolean = false,
    val offset: Int = 0,
    val limit: Int = 20,
)

/**
 * Diziflix sunucusu için OkHttp istemcisi. tizen-client/js/api.js'nin uçlarını birebir izler:
 * `profile` parametresi, `layout=tv-v1` boot, `kind=trailer`, `attempt_token` raporu, ilerleme...
 *
 * [baseUrlProvider] her istekte çağrılır: Ayarlar'da adres değişince anında geçerli olur.
 * Hatalar her zaman [ApiException] olarak fırlatılır (iptal hariç).
 */
class ApiClient(
    private val http: OkHttpClient,
    private val baseUrlProvider: suspend () -> String,
    private val json: Json = ApiJson.instance,
) {
    companion object {
        /** Ana ekran düzeni: hero(lar) + hazır satırlar (TV istemcisiyle aynı). */
        const val BOOT_LAYOUT = "tv-v1"

        const val TIMEOUT_DEFAULT_MS = 12_000L
        const val TIMEOUT_HEALTH_MS = 6_000L
        const val TIMEOUT_PROGRESS_MS = 8_000L

        /** Canlı aramayla ilk bulunan dizinin sezon kataloğu yavaş gelebilir. */
        const val TIMEOUT_DETAIL_MS = 180_000L
        const val TIMEOUT_STREAMS_MS = 90_000L
        const val TIMEOUT_SEARCH_MS = 90_000L

        /** Kaynak bulucu durumu ve bildirim yoklaması ucuz uçlardır: kısa zaman aşımı. */
        const val TIMEOUT_POLL_MS = 8_000L

        private const val JSON_TYPE = "application/json; charset=utf-8"
    }

    // ------------------------------------------------------------------ uçlar

    suspend fun health(): Health =
        getJson(listOf("api", "health"), emptyList(), TIMEOUT_HEALTH_MS, Health.serializer())

    suspend fun profiles(): List<Profile> =
        getJson(listOf("api", "profiles"), emptyList(), TIMEOUT_DEFAULT_MS, ProfilesResponse.serializer())
            .profiles.filter { it.id.isNotBlank() }

    suspend fun boot(profileId: String): BootResponse = getJson(
        listOf("api", "boot"),
        listOf("profile" to profileId, "layout" to BOOT_LAYOUT),
        TIMEOUT_DEFAULT_MS,
        BootResponse.serializer(),
    )

    suspend fun row(rowId: String, profileId: String, offset: Int = 0, limit: Int = 20): RowResponse = getJson(
        listOf("api", "row", rowId),
        listOf("profile" to profileId, "offset" to offset.toString(), "limit" to limit.toString()),
        TIMEOUT_DEFAULT_MS,
        RowResponse.serializer(),
    )

    /**
     * [poll] = true: `?poll=1` — aynı gövde ama sunucu ASLA iş başlatmaz (hidrasyon/sezon/fragman kontrolü yok);
     * `hydrating` yalnızca süren işi yansıtır. Arka plan yoklaması için (yoklama hidrasyonu yeniden tetiklememeli).
     * Yoklama ucuzdur: kısa zaman aşımı.
     */
    suspend fun detail(itemId: String, profileId: String, poll: Boolean = false): Detail {
        val query = ArrayList<Pair<String, String>>()
        query += "profile" to profileId
        if (poll) query += "poll" to "1"
        val text = getText(
            listOf("api", "detail", itemId),
            query,
            if (poll) TIMEOUT_DEFAULT_MS else TIMEOUT_DETAIL_MS,
        )
        return parseDetail(text)
    }

    /** `episodeId` dizide bölüm, `kind` = video | trailer (fragman için yalnızca fragman kaynakları döner). */
    suspend fun streams(itemId: String, profileId: String, episodeId: String? = null, kind: String? = null): StreamsResponse {
        val query = ArrayList<Pair<String, String>>()
        query += "profile" to profileId
        if (!episodeId.isNullOrBlank()) query += "episode" to episodeId
        if (!kind.isNullOrBlank()) query += "kind" to kind
        return getJson(listOf("api", "streams", itemId), query, TIMEOUT_STREAMS_MS, StreamsResponse.serializer())
    }

    /**
     * Kaynak bulucu durumu (`GET /api/source-finder/{id}?episode=`): idle | searching | found | not_found.
     * [episodeId] film için boş bırakılır (film kendisidir).
     */
    suspend fun sourceFinder(itemId: String, episodeId: String? = null): FinderStatus {
        val query = ArrayList<Pair<String, String>>()
        if (!episodeId.isNullOrBlank()) query += "episode" to episodeId
        return getJson(listOf("api", "source-finder", itemId), query, TIMEOUT_POLL_MS, FinderStatus.serializer())
    }

    /** Profilin okunmamış bildirimleri (`id > since`, eskiden yeniye, en çok 50) + sonraki yoklama için `last_id`. */
    suspend fun notifications(profileId: String, since: Long): NotificationsResponse = getJson(
        listOf("api", "notifications"),
        listOf("profile" to profileId, "since" to since.toString()),
        TIMEOUT_POLL_MS,
        NotificationsResponse.serializer(),
    )

    /** `upto`'ya kadar (dahil) bildirimleri okundu yapar (idempotent). */
    suspend fun markNotificationsRead(profileId: String, upto: Long) {
        postJson(
            listOf("api", "notifications", "read"),
            buildJsonObject { put("upto", upto) },
            TIMEOUT_POLL_MS,
            query = listOf("profile" to profileId),
        )
    }

    /**
     * Akış başına ayrı attempt_token ile başarı/hata bildirimi. engine: "", "avplay", "html5", "embed".
     * [detail]: hata ayrıntısı (en çok 120 karakter), yalnızca doluysa gönderilir; sunucuda alan yoksa zararsız.
     */
    suspend fun playbackReport(attemptToken: String, event: String, code: String, engine: String, detail: String = "") {
        postJson(
            listOf("api", "playback-report"),
            buildJsonObject {
                put("attempt_token", attemptToken)
                put("event", event)
                put("code", code)
                put("engine", engine)
                if (detail.isNotBlank()) put("detail", detail.take(120))
            },
            TIMEOUT_DEFAULT_MS,
        )
    }

    /** Konum/süre saniye cinsindendir. */
    suspend fun progress(profileId: String, itemId: String, episodeId: String, positionSec: Int, durationSec: Int) {
        postJson(
            listOf("api", "progress"),
            buildJsonObject {
                put("profile", profileId)
                put("item_id", itemId)
                put("episode_id", episodeId)
                put("position", positionSec)
                put("duration", durationSec)
            },
            TIMEOUT_PROGRESS_MS,
        )
    }

    suspend fun mylist(profileId: String): List<Item> =
        getJson(listOf("api", "mylist"), listOf("profile" to profileId), TIMEOUT_DEFAULT_MS, MyListResponse.serializer())
            .items.filter { it.id.isNotBlank() }

    suspend fun addToMyList(profileId: String, itemId: String) {
        postJson(
            listOf("api", "mylist"),
            buildJsonObject {
                put("profile", profileId)
                put("item_id", itemId)
            },
            TIMEOUT_DEFAULT_MS,
        )
    }

    suspend fun removeFromMyList(profileId: String, itemId: String) {
        val url = buildUrl(listOf("api", "mylist", itemId), listOf("profile" to profileId))
        execute(Request.Builder().url(url).delete().header("Accept", "application/json").build(), TIMEOUT_DEFAULT_MS)
    }

    /**
     * "İzlemeye Devam Et" satırından kaldırır (`DELETE /api/continue/{item_id}`): YUMUŞAK gizleme, ilerleme
     * kayıtları silinmez. [itemId] yapım id'sidir (bölüm kartında da `id`; `episode_id` DEĞİL). Idempotent:
     * yanıt `{"ok":true,"removed":bool}`, bilinmeyen/zaten gizli öğe hata değildir.
     */
    suspend fun removeFromContinue(itemId: String, profileId: String) {
        val url = buildUrl(listOf("api", "continue", itemId), listOf("profile" to profileId))
        execute(Request.Builder().url(url).delete().header("Accept", "application/json").build(), TIMEOUT_DEFAULT_MS)
    }

    suspend fun catalog(query: CatalogQuery): CatalogResponse {
        val params = ArrayList<Pair<String, String>>()
        params += "profile" to query.profile
        if (query.type.isNotEmpty()) params += "type" to query.type
        if (query.genre.isNotEmpty()) params += "genre" to query.genre
        if (query.year != null) params += "year" to query.year.toString()
        if (query.availability.isNotEmpty()) params += "availability" to query.availability
        if (query.sort.isNotEmpty()) params += "sort" to query.sort
        if (query.q.isNotEmpty()) params += "q" to query.q
        if (query.mine) params += "mine" to "true"
        params += "offset" to query.offset.toString()
        params += "limit" to query.limit.toString()
        return getJson(listOf("api", "catalog"), params, TIMEOUT_DEFAULT_MS, CatalogResponse.serializer())
    }

    suspend fun genres(type: String = ""): List<Genre> {
        val params = if (type.isNotEmpty()) listOf("type" to type) else emptyList()
        return getJson(listOf("api", "genres"), params, TIMEOUT_DEFAULT_MS, GenresResponse.serializer()).genres
    }

    /** En az 3 karakterde sunucu kaynak siteyi canlı sorgular (yavaş olabilir). */
    suspend fun search(q: String, profileId: String, limit: Int = 20): SearchResponse = getJson(
        listOf("api", "search"),
        listOf("q" to q, "profile" to profileId, "limit" to limit.toString()),
        TIMEOUT_SEARCH_MS,
        SearchResponse.serializer(),
    )

    // ------------------------------------------------------------------ ayrıştırma

    /** Detay yanıtı: aynı JSON'dan Item alanları + detay ekleri ayrı ayrı okunur. */
    internal fun parseDetail(text: String): Detail {
        try {
            val element = json.parseToJsonElement(text)
            val item = json.decodeFromJsonElement(Item.serializer(), element)
            val extras = json.decodeFromJsonElement(DetailExtras.serializer(), element)
            return Detail(item, extras)
        } catch (e: IllegalArgumentException) {
            // SerializationException, IllegalArgumentException'ın alt sınıfıdır.
            throw ApiException("bad_json", "Geçersiz sunucu yanıtı", 0, e)
        }
    }

    // ------------------------------------------------------------------ altyapı

    private suspend fun buildUrl(segments: List<String>, query: List<Pair<String, String>>): HttpUrl {
        val base = baseUrlProvider()
        val root = base.toHttpUrlOrNull() ?: throw ApiException("bad_base", "Geçersiz sunucu adresi: $base")
        val builder = root.newBuilder()
        for (segment in segments) builder.addPathSegment(segment)
        for ((key, value) in query) builder.addQueryParameter(key, value)
        return builder.build()
    }

    private suspend fun <T> getJson(
        segments: List<String>,
        query: List<Pair<String, String>>,
        timeoutMs: Long,
        serializer: KSerializer<T>,
    ): T {
        val text = getText(segments, query, timeoutMs)
        try {
            return json.decodeFromString(serializer, text)
        } catch (e: IllegalArgumentException) {
            throw ApiException("bad_json", "Geçersiz sunucu yanıtı", 0, e)
        }
    }

    private suspend fun getText(segments: List<String>, query: List<Pair<String, String>>, timeoutMs: Long): String {
        val url = buildUrl(segments, query)
        // max-age=0: her seferinde sunucuya sorulur ama ETag varsa koşullu (If-None-Match/304) gider.
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .cacheControl(CacheControl.Builder().maxAge(0, TimeUnit.SECONDS).build())
            .build()
        return execute(request, timeoutMs)
    }

    private suspend fun postJson(
        segments: List<String>,
        body: JsonObject,
        timeoutMs: Long,
        query: List<Pair<String, String>> = emptyList(),
    ): String {
        val url = buildUrl(segments, query)
        val requestBody: RequestBody = body.toString().toRequestBody(JSON_TYPE.toMediaType())
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .post(requestBody)
            .build()
        return execute(request, timeoutMs)
    }

    private suspend fun execute(request: Request, timeoutMs: Long): String {
        // Uç başına zaman aşımı: havuz ve disk önbelleği ortak, yalnızca süreler değişir.
        val client = http.newBuilder()
            .callTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .build()
        try {
            val (code, text) = client.newCall(request).await()
            if (code !in 200..299) throw parseError(code, text, json)
            return text
        } catch (e: ApiException) {
            throw e
        } catch (e: CancellationException) {
            throw e
        } catch (e: InterruptedIOException) {
            // SocketTimeoutException dahil (Call zaman aşımı da InterruptedIOException fırlatır).
            throw ApiException("timeout", "Sunucu zaman aşımı", 0, e)
        } catch (e: IOException) {
            throw ApiException("network", "Sunucuya ulaşılamıyor: ${request.url.host}:${request.url.port}", 0, e)
        }
    }
}

/** {"error":{"code","message"}} biçimini okur; olmazsa http_NNN. */
internal fun parseError(status: Int, body: String, json: Json): ApiException {
    var code = "http_$status"
    var message = "Sunucu hatası ($status)"
    try {
        val error = (json.parseToJsonElement(body) as? JsonObject)?.get("error") as? JsonObject
        val c = error?.get("code")?.jsonPrimitive?.contentOrNull
        val m = error?.get("message")?.jsonPrimitive?.contentOrNull
        if (!c.isNullOrBlank()) code = c
        if (!m.isNullOrBlank()) message = m
    } catch (e: Exception) {
        // gövde JSON değil: varsayılan mesaj kalır
    }
    return ApiException(code, message, status)
}

/** Yanıt gövdesini okuyup (kod, metin) döndürür; iptalde çağrı iptal edilir. */
private suspend fun Call.await(): Pair<Int, String> = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation {
        try {
            cancel()
        } catch (e: Exception) {
            // yoksay
        }
    }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (continuation.isActive) continuation.resumeWithException(e)
        }

        override fun onResponse(call: Call, response: Response) {
            try {
                val code = response.code
                val text = response.use { it.body?.string().orEmpty() }
                if (continuation.isActive) continuation.resume(code to text)
            } catch (e: IOException) {
                if (continuation.isActive) continuation.resumeWithException(e)
            }
        }
    })
}
