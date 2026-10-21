package com.diziflix.app.domain

import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.RemoteSite
import com.diziflix.app.data.model.SourceOption
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Arama kaynak etiketleri (ilk 2 + "+N", durum işaretleri) ve "bazı kaynaklar yanıt vermedi" satırı. */
class SearchSourceLogicTest {

    private fun opt(site: String, name: String = site.uppercase(), status: String = "ok") =
        SourceOption(site = site, name = name, kind = "series", episodes = 3, status = status)

    @Test
    fun tags_showFirstTwoThenCount() {
        val tags = SearchSourceLogic.tags(listOf(opt("a", "A"), opt("b", "B"), opt("c", "C"), opt("d", "D")))
        assertEquals(listOf("A", "B", "+2"), tags.map { it.text })
        assertEquals(listOf(SourceTag.Kind.Normal, SourceTag.Kind.Normal, SourceTag.Kind.More), tags.map { it.kind })
    }

    @Test
    fun tags_markBrokenAndFaintUnknown() {
        val tags = SearchSourceLogic.tags(listOf(opt("a", "A", "broken"), opt("b", "B", "unknown")))
        assertEquals("⚠ A", tags[0].text)
        assertEquals(SourceTag.Kind.Warn, tags[0].kind)
        assertEquals("B", tags[1].text)
        assertEquals(SourceTag.Kind.Faint, tags[1].kind)
        assertEquals(2, tags.size)   // tam 2 kaynak: "+N" yok
    }

    @Test
    fun tags_emptyAndNameFallback() {
        assertEquals(emptyList<SourceTag>(), SearchSourceLogic.tags(emptyList()))
        assertEquals("yabancidizi", SearchSourceLogic.tags(listOf(opt("yabancidizi", name = "")))[0].text)
        assertEquals(1, SearchSourceLogic.tags(listOf(opt("a", "A"), SourceOption())).size)   // adı/sitesi boş kayıt atlanır
    }

    @Test
    fun failedSources_onlyOkFalseAndNotSkipped_namedFromOptions() {
        val sites = mapOf(
            "yabancidizi" to RemoteSite(ok = true, count = 5),
            "sinemalar" to RemoteSite(ok = false, error = "zaman aşımı"),
            "x" to RemoteSite(ok = false, skipped = "breaker"),
            "y" to RemoteSite(ok = false, skipped = "unsupported"),
            "zzz" to RemoteSite(ok = false),
        )
        val items = listOf(Item(id = "1", sourceOptions = listOf(opt("sinemalar", "Sinemalar"), opt("yabancidizi", "Yabancı Dizi"))))
        // "sinemalar" adı source_options'tan, "zzz" kimlik olarak; sıra kimliğe göre
        assertEquals(listOf("Sinemalar", "zzz"), SearchSourceLogic.failedSources(sites, items))
        assertEquals(emptyList<String>(), SearchSourceLogic.failedSources(emptyMap(), items))
        assertEquals(emptyList<String>(), SearchSourceLogic.failedSources(mapOf("a" to RemoteSite(ok = true)), items))
    }

    @Test
    fun failedNote_text() {
        assertEquals("Bazı kaynaklar yanıt vermedi: A, B", SearchSourceLogic.failedNote(listOf("A", "B")))
        assertNull(SearchSourceLogic.failedNote(emptyList()))
    }
}
