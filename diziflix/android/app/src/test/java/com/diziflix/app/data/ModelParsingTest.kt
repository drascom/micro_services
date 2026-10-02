package com.diziflix.app.data

import com.diziflix.app.Fixtures
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.CatalogResponse
import com.diziflix.app.data.model.FinderStatus
import com.diziflix.app.data.model.GenresResponse
import com.diziflix.app.data.model.Health
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.MyListResponse
import com.diziflix.app.data.model.NotificationsResponse
import com.diziflix.app.data.model.ProfilesResponse
import com.diziflix.app.data.model.RowResponse
import com.diziflix.app.data.model.SearchResponse
import com.diziflix.app.data.model.StreamsResponse
import com.diziflix.app.data.net.ApiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Gerçek sunucu yanıtlarıyla model ayrıştırma testleri. */
class ModelParsingTest {

    private val json = ApiJson.instance

    @Test
    fun profiles_parseWithKidsFlagAndAvatar() {
        val res = Fixtures.parse("profiles.json", ProfilesResponse.serializer())
        assertEquals(3, res.profiles.size)
        val first = res.profiles[0]
        assertEquals("p1", first.id)
        assertEquals("ayhan", first.name)
        assertEquals("a1", first.avatarSeed)
        assertEquals("/img/avatar/a1?w=200&h=200", first.avatar)
        assertFalse(first.isKids)
        val kid = res.profiles[1]
        assertEquals("p2", kid.id)
        assertTrue(kid.isKids)
        assertEquals("a16", kid.avatarSeed)
    }

    @Test
    fun boot_parsesHeroesAndLoadedRows() {
        val boot = Fixtures.parse("boot.json", BootResponse.serializer())
        assertEquals("tv-v1", boot.layout)
        assertEquals(101, boot.catalogTotal)

        val hero = requireNotNull(boot.hero)
        assertEquals("tmdb_tv_95350", hero.id)
        assertEquals("Lanterns", hero.title)
        assertEquals("Lanterns", hero.logoText)
        assertEquals("Öne Çıkanlar", hero.tagline)
        assertTrue(hero.isSeries)
        assertTrue(hero.availability.hasTrailer)
        assertEquals("ready", hero.availability.state)
        assertEquals(listOf("tmdb_tv_95350", "tmdb_tv_615"), boot.heroList.map { it.id })

        assertEquals(
            listOf("continue", "trending", "latest_episodes", "series", "movies", "noteworthy_movies"),
            boot.rows.map { it.id },
        )
        val cont = boot.rows[0]
        assertEquals("İzlemeye Devam Et", cont.title)
        assertTrue(cont.loaded)
        assertEquals(5, cont.total)
        assertEquals(3, cont.items.size)
        assertEquals(52, boot.rows[3].total)
    }

    @Test
    fun boot_continueItemCarriesEpisodeProgress() {
        val boot = Fixtures.parse("boot.json", BootResponse.serializer())
        val item = boot.rows[0].items[0]
        assertEquals("tmdb_tv_103516", item.id)
        assertEquals("/img/tmdb_tv_103516/portrait?w=300&h=450", item.portrait)
        assertEquals("/img/tmdb_tv_103516/card?w=342&h=192", item.card)
        assertTrue(item.hasBackdrop)
        val progress = requireNotNull(item.progress)
        assertEquals("tmdb_tv_103516:s4:e1", progress.episodeId)
        assertEquals(56.0, progress.position, 0.0)
        assertEquals(3559.0, progress.duration, 0.0)
        assertEquals(2.0, progress.pct, 0.0)
    }

    @Test
    fun row_parsesPagedResponse() {
        val row = Fixtures.parse("row_series.json", RowResponse.serializer())
        assertEquals("series", row.id)
        assertEquals("Diziler", row.title)
        assertEquals(52, row.total)
        assertEquals(0, row.offset)
        assertEquals(20, row.limit)
        assertEquals(4, row.items.size)
        assertEquals("tmdb_tv_95480", row.items[0].id)
        assertEquals("Slow Horses", row.items[0].title)
        assertEquals(2022, row.items[0].year)
    }

    @Test
    fun streams_episodeSharesOneAttemptToken() {
        val res = Fixtures.parse("streams_episode.json", StreamsResponse.serializer())
        assertEquals(3, res.streams.size)
        assertEquals(56.0, res.resumePosition, 0.0)
        assertEquals(0.0, res.duration, 0.0)
        assertTrue(res.subtitles.isEmpty())
        for (s in res.streams) {
            assertEquals("hls", s.type)
            assertEquals("auto", s.quality)
            assertEquals("VidMolly · auto", s.label)
            assertEquals("VidMolly", s.provider)
            assertEquals("episode", s.kind)
            assertEquals("00000000000000000000000000000001", s.attemptToken)
            assertTrue(s.url.contains("REDACTED"))
        }
        assertTrue(res.streams[0].url.startsWith("https://box-1500-u.vmeas.cloud/"))
    }

    @Test
    fun streams_trailerIsEmbed() {
        val res = Fixtures.parse("streams_trailer.json", StreamsResponse.serializer())
        assertEquals(1, res.streams.size)
        val s = res.streams[0]
        assertEquals("embed", s.type)
        assertEquals("trailer", s.kind)
        assertEquals("https://www.youtube.com/embed/2um-VUapiJY", s.url)
        assertEquals("00000000000000000000000000000002", s.attemptToken)
        assertEquals(0.0, res.resumePosition, 0.0)
    }

    @Test
    fun streams_emptyForMovieWithoutSources() {
        val res = Fixtures.parse("streams_movie_empty.json", StreamsResponse.serializer())
        assertTrue(res.streams.isEmpty())
        assertTrue(res.subtitles.isEmpty())
    }

    @Test
    fun streams_externalSubtitlesAreParsedWhenServerSendsThem() {
        // subtitles[] şekli API.md sözleşmesinden (sunucu şimdilik boş döndürüyor).
        val text = """{"streams":[],"subtitles":[{"lang":"tr","label":"Türkçe","url":"https://x/tr.vtt"}],
            "resume_position":12.5,"duration":2700}"""
        val res = json.decodeFromString(StreamsResponse.serializer(), text)
        assertEquals(1, res.subtitles.size)
        assertEquals("tr", res.subtitles[0].lang)
        assertEquals("Türkçe", res.subtitles[0].label)
        assertEquals("https://x/tr.vtt", res.subtitles[0].url)
        assertEquals(12.5, res.resumePosition, 0.0)
        assertEquals(2700.0, res.duration, 0.0)
    }

    @Test
    fun streams_trackContractFields_areParsedWhenServerSendsThem() {
        // Alanlar API.md "Ses / altyazı izleri" örneğinden (canlı sunucuda henüz yok; yeni alanlar isteğe bağlıdır).
        val res = json.decodeFromString(StreamsResponse.serializer(), TRACK_CONTRACT_JSON)
        assertEquals(2, res.streams.size)
        val soft = res.streams[0]
        assertEquals("v_en", soft.variantId)
        assertEquals("en", soft.audioLang)
        assertEquals("soft", soft.subMode)
        assertNull(soft.hardLang)
        val hard = res.streams[1]
        assertEquals("hard", hard.subMode)
        assertEquals("tr", hard.hardLang)
        assertNull(hard.audioLang)

        assertEquals(1, res.subtitles.size)
        val sub = res.subtitles[0]
        assertEquals("9f8e7d6c5b4a39281706", sub.id)
        assertEquals("en", sub.lang)
        assertEquals("İngilizce", sub.label)
        assertEquals("captions", sub.kind)
        assertEquals("vtt", sub.format)
        assertEquals("/api/subtitles/9f8e7d6c5b4a39281706.vtt", sub.url)
        assertEquals(listOf("v_en"), sub.streamIds)
        assertTrue(sub.isDefault)
        assertEquals("soft", sub.origin)

        assertEquals(1, res.audio.size)
        assertEquals("a_en", res.audio[0].id)
        assertEquals(listOf("v_en", "v_tr"), res.audio[0].streamIds)
        assertTrue(res.audio[0].isDefault)
    }

    @Test
    fun streams_oldServerWithoutTrackFields_stillParses() {
        val res = Fixtures.parse("streams_episode.json", StreamsResponse.serializer())
        assertNull(res.streams[0].variantId)
        assertNull(res.streams[0].subMode)
        assertTrue(res.audio.isEmpty())
    }

    @Test
    fun catalog_parsesFiltersAndTotals() {
        val res = Fixtures.parse("catalog_movies.json", CatalogResponse.serializer())
        assertEquals(49, res.total)
        assertEquals(0, res.offset)
        assertEquals(6, res.limit)
        assertEquals(6, res.items.size)
        assertEquals(19, res.genres.size)
        assertEquals("action", res.genres[0].id)
        assertEquals("Aksiyon", res.genres[0].name)
        assertEquals(2026, res.years[0])
        assertTrue(res.items.all { !it.isSeries })
    }

    @Test
    fun catalog_unavailableItemsOnlyHaveTrailer() {
        val res = Fixtures.parse("catalog_unavailable.json", CatalogResponse.serializer())
        assertEquals(2, res.total)
        for (item in res.items) {
            assertTrue(item.availability.isUnavailable)
            assertEquals("no_video_source", item.availability.reason)
            assertTrue(item.availability.hasTrailer)
            assertEquals("trailer", item.playback)
        }
    }

    @Test
    fun search_localResultShape() {
        val res = Fixtures.parse("search.json", SearchResponse.serializer())
        assertEquals(5, res.total)
        assertFalse(res.remote)
        assertNull(res.remoteError)
        assertEquals("Star Trek: Strange New Worlds", res.items[0].title)
    }

    @Test
    fun health_genres_mylist() {
        val health = Fixtures.parse("health.json", Health.serializer())
        assertEquals("ok", health.status)
        assertEquals("library", health.source)
        assertEquals(101, health.items)

        val genres = Fixtures.parse("genres.json", GenresResponse.serializer())
        assertEquals(19, genres.genres.size)
        assertEquals("Aksiyon", genres.genres[0].name)

        val mylist = Fixtures.parse("mylist_empty.json", MyListResponse.serializer())
        assertTrue(mylist.items.isEmpty())
    }

    // ------------------------------------------------------------ tolerans (sunucu alan ekleyebilir/eksik bırakabilir)

    @Test
    fun unknownFieldsAreIgnored() {
        val text = """{"id":"x","type":"movie","title":"T","brand_new":{"a":1},
            "availability":{"state":"ready","future_flag":true}}"""
        val item = json.decodeFromString(Item.serializer(), text)
        assertEquals("x", item.id)
        assertEquals("T", item.title)
        assertEquals("ready", item.availability.state)
    }

    @Test
    fun missingAndNullFieldsFallBackToDefaults() {
        val text = """{"id":"y","overview":null,"genres":null,"rating":null,"availability":null,"progress":null}"""
        val item = json.decodeFromString(Item.serializer(), text)
        assertEquals("y", item.id)
        assertEquals("", item.overview)
        assertTrue(item.genres.isEmpty())
        assertNull(item.rating)
        assertNull(item.progress)
        assertEquals("ready", item.availability.state)
        assertFalse(item.hasBackdrop)
        assertFalse(item.inMylist)
    }

    @Test
    fun lazyRowHasCountButNoItems() {
        // API.md: loaded:false satırlar yalnızca başlık + count taşır.
        val text = """{"hero":null,"rows":[{"id":"mylist","title":"Listem","loaded":false,"count":7}]}"""
        val boot = json.decodeFromString(BootResponse.serializer(), text)
        assertTrue(boot.heroList.isEmpty())
        assertEquals(1, boot.rows.size)
        assertFalse(boot.rows[0].loaded)
        assertEquals(7, boot.rows[0].count)
        assertTrue(boot.rows[0].items.isEmpty())
    }

    @Test
    fun episodeCardFieldsUseCardKeyForListIdentity() {
        val text = """{"id":"s1","type":"series","card_kind":"episode","card_key":"episode:e1",
            "episode_id":"e1","episode_label":"S01 B03 · Fırtına"}"""
        val item = json.decodeFromString(Item.serializer(), text)
        assertEquals("episode", item.cardKind)
        assertEquals("episode:e1", item.listKey)
        assertEquals("e1", item.episodeId)
        assertEquals("S01 B03 · Fırtına", item.episodeLabel)
        assertEquals("s1", Item(id = "s1").listKey)
    }

    // ---------------------------------------------------------------- sunucu turu: kaynak bulucu / bildirim / arama kaynakları

    @Test
    fun streams_finderHintIsOptional() {
        val searching = json.decodeFromString(StreamsResponse.serializer(), """{"streams":[],"finder":{"state":"searching"}}""")
        assertEquals("searching", searching.finder?.state)
        // eski sunucu / alan yok / null -> null (genel mesaj)
        assertNull(json.decodeFromString(StreamsResponse.serializer(), """{"streams":[]}""").finder)
        assertNull(json.decodeFromString(StreamsResponse.serializer(), """{"streams":[],"finder":null}""").finder)
        assertNull(Fixtures.parse("streams_movie_empty.json", StreamsResponse.serializer()).finder)
    }

    @Test
    fun streams_proxiedFlagIsIgnoredByTheModel() {
        // `proxied`: oynatıcı url'yi aynen kullanır; modelde alan yok, bilinmeyen anahtar yok sayılır
        val res = json.decodeFromString(
            StreamsResponse.serializer(),
            """{"streams":[{"url":"https://srv/api/stream-proxy/tok","type":"mp4","proxied":true,"label":"OK.ru"}]}""",
        )
        assertEquals("https://srv/api/stream-proxy/tok", res.streams.single().url)
    }

    @Test
    fun search_sourceOptionsAndRemoteSites() {
        val text = """{"items":[{"id":"a","title":"A","sources":["yabancidizi"],
              "source_options":[{"site":"yabancidizi","name":"Yabancı Dizi","kind":"series","episodes":7,"status":"ok"},
                                {"site":"sinemalar","name":"Sinemalar","kind":"series","episodes":0,"status":"broken"}]},
              {"id":"b","title":"B"}],
            "total":2,"remote":true,"remote_error":"sinemalar: zaman aşımı",
            "remote_sites":{"yabancidizi":{"ok":true,"count":13,"ms":420},
                            "sinemalar":{"ok":false,"count":0,"ms":8000,"error":"zaman aşımı"},
                            "x":{"ok":false,"count":0,"ms":0,"skipped":"breaker"}}}"""
        val res = json.decodeFromString(SearchResponse.serializer(), text)
        val options = res.items[0].sourceOptions
        assertEquals(listOf("yabancidizi", "sinemalar"), options.map { it.site })
        assertEquals("Yabancı Dizi", options[0].name)
        assertEquals(7, options[0].episodes)
        assertEquals("broken", options[1].status)
        assertTrue(res.items[1].sourceOptions.isEmpty())            // alan yok -> boş (eski sunucu)
        assertEquals(setOf("yabancidizi", "sinemalar", "x"), res.remoteSites.keys)
        assertTrue(res.remoteSites.getValue("yabancidizi").ok)
        assertFalse(res.remoteSites.getValue("sinemalar").ok)
        assertEquals("zaman aşımı", res.remoteSites.getValue("sinemalar").error)
        assertEquals("breaker", res.remoteSites.getValue("x").skipped)
        // eski yanıt: remote_sites yok
        assertTrue(Fixtures.parse("search.json", SearchResponse.serializer()).remoteSites.isEmpty())
    }

    @Test
    fun notifications_parse() {
        val text = """{"items":[{"id":12,"kind":"source_found","canonical_id":"tmdb_tv_103516",
            "episode_id":"tmdb_tv_103516:s1:e3","title":"Dizi adı","season":1,"episode":3,"site":"yabancidizi",
            "method":"retry","created_at":1790889094},
            {"id":13,"kind":"source_found","canonical_id":"tmdb_m_1","episode_id":"","title":"Film","season":null,"episode":null,
             "site":"x","method":"search","created_at":1790889100}],"last_id":13}"""
        val res = json.decodeFromString(NotificationsResponse.serializer(), text)
        assertEquals(13L, res.lastId)
        val series = res.items[0]
        assertEquals(12L, series.id)
        assertEquals("tmdb_tv_103516", series.canonicalId)
        assertEquals("tmdb_tv_103516:s1:e3", series.episodeId)
        assertEquals(1, series.season)
        assertEquals(3, series.episode)
        assertEquals("retry", series.method)
        val movie = res.items[1]
        assertNull(movie.season)
        assertNull(movie.episode)
        assertTrue(json.decodeFromString(NotificationsResponse.serializer(), "{}").items.isEmpty())
    }

    @Test
    fun sourceFinderStatus_parses() {
        val text = """{"state":"searching","steps":[{"name":"retry","ok":false,"ms":1800,"note":"2 kaynak denendi, akış yok"}],"updated_at":1790889094}"""
        val st = json.decodeFromString(FinderStatus.serializer(), text)
        assertEquals("searching", st.state)
        assertEquals("retry", st.steps.single().name)
        assertEquals(1790889094L, st.updatedAt)
        assertEquals("idle", json.decodeFromString(FinderStatus.serializer(), "{}").state)
    }

    private companion object {
        val TRACK_CONTRACT_JSON = """
            {"streams":[
              {"url":"https://cdn.example/en/master.m3u8","type":"hls","quality":"auto","label":"VidMolly · İngilizce",
               "variant_id":"v_en","audio_lang":"en","sub_mode":"soft","attempt_token":"t1"},
              {"url":"https://cdn.example/tr/master.m3u8","type":"hls","quality":"auto","label":"VidMolly · Türkçe altyazı",
               "variant_id":"v_tr","audio_lang":null,"sub_mode":"hard","hard_lang":"tr","attempt_token":"t1"}],
             "subtitles":[{"id":"9f8e7d6c5b4a39281706","lang":"en","label":"İngilizce","kind":"captions","format":"vtt",
               "url":"/api/subtitles/9f8e7d6c5b4a39281706.vtt","stream_ids":["v_en"],"default":true,"origin":"soft"}],
             "audio":[{"id":"a_en","lang":"en","label":"İngilizce","stream_ids":["v_en","v_tr"],"default":true}],
             "resume_position":1240,"duration":2700}
        """.trimIndent()
    }
}
