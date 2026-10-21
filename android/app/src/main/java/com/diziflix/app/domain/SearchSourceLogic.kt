package com.diziflix.app.domain

import com.diziflix.app.data.model.Item
import com.diziflix.app.data.model.RemoteSite
import com.diziflix.app.data.model.SourceOption

/** Arama sonuç kartındaki küçük kaynak etiketi. [kind]: normal (ok) | warn (broken: uyarı işaretli) | faint (unknown: soluk) | more (+N). */
data class SourceTag(val text: String, val kind: Kind) {
    enum class Kind { Normal, Warn, Faint, More }
}

/** Arama kaynak seçimi (API.md `/api/search`: `source_options`, `remote_sites`) için saf mantık (birim testli). */
object SearchSourceLogic {
    /** Karttaki en çok etiket sayısı; fazlası "+N". */
    const val MAX_TAGS = 2

    const val WARN_MARK = "⚠"

    /** Etiketler: ilk [MAX_TAGS] kaynak (sunucu en iyiden başlayarak sıralar) + kalan varsa "+N". Boş ad -> site kimliği. */
    fun tags(options: List<SourceOption>): List<SourceTag> {
        val usable = options.filter { it.name.isNotBlank() || it.site.isNotBlank() }
        val shown = usable.take(MAX_TAGS).map { option ->
            val name = option.name.ifBlank { option.site }
            when (option.status) {
                "broken" -> SourceTag("$WARN_MARK $name", SourceTag.Kind.Warn)
                "ok" -> SourceTag(name, SourceTag.Kind.Normal)
                else -> SourceTag(name, SourceTag.Kind.Faint)   // unknown (ve bilinmeyen değerler): soluk
            }
        }
        val rest = usable.size - shown.size
        return if (rest > 0) shown + SourceTag("+$rest", SourceTag.Kind.More) else shown
    }

    /**
     * Yanıt vermeyen kaynakların adları: yalnız `ok == false` ve `skipped` boş olanlar. Ad, sonuçlardaki `source_options`'tan
     * (site kimliği -> ad) alınır, yoksa site kimliği. Sıra kararlı (kimliğe göre).
     */
    fun failedSources(remoteSites: Map<String, RemoteSite>, items: List<Item>): List<String> {
        val failed = remoteSites.filter { (_, site) -> !site.ok && site.skipped.isNullOrBlank() }.keys.sorted()
        if (failed.isEmpty()) return emptyList()
        val names = HashMap<String, String>()
        for (item in items) for (option in item.sourceOptions) {
            if (option.site.isNotBlank() && option.name.isNotBlank()) names.putIfAbsent(option.site, option.name)
        }
        return failed.map { names[it] ?: it }
    }

    /** "Bazı kaynaklar yanıt vermedi: A, B" (silik tek satır); hepsi yanıt verdiyse null. */
    fun failedNote(failed: List<String>): String? =
        if (failed.isEmpty()) null else "Bazı kaynaklar yanıt vermedi: " + failed.joinToString(", ")
}
