package com.diziflix.app.play

import java.util.Locale

/** Ses/altyazı izi menü etiketleri (saf Kotlin). */
object TrackLabels {
    private val UNDETERMINED = setOf("", "und", "zxx", "mis", "mul")

    /**
     * Öncelik: iz etiketi > dilin Türkçe görünen adı > "Parça N". [index] 0 tabanlıdır; [prefix]
     * "Ses" ya da "Altyazı" olur.
     */
    fun label(language: String?, label: String?, index: Int, prefix: String): String {
        val explicit = label?.trim().orEmpty()
        if (explicit.isNotEmpty()) return explicit
        val lang = language?.trim()?.lowercase(Locale.ROOT).orEmpty()
        if (lang !in UNDETERMINED) return languageName(lang) ?: lang
        return "$prefix ${index + 1}"
    }

    /** Dil kodunun Türkçe görünen adı ("tr" -> "Türkçe"); kod boş/belirsizse null, ad bulunamazsa kodun kendisi. */
    fun languageName(language: String?): String? {
        val lang = language?.trim()?.lowercase(Locale.ROOT).orEmpty()
        if (lang in UNDETERMINED) return null
        val display = Locale.forLanguageTag(lang).getDisplayLanguage(Locale("tr"))
        if (display.isNotBlank() && !display.equals(lang, ignoreCase = true)) {
            return display.replaceFirstChar { c -> c.titlecase(Locale("tr")) }
        }
        return lang
    }

    /** Aynı etiketli izlerde " (2)"... ekleyerek benzersizleştirir. */
    fun uniquify(labels: List<String>): List<String> {
        val counts = HashMap<String, Int>()
        return labels.map { base ->
            val n = (counts[base] ?: 0) + 1
            counts[base] = n
            if (n == 1) base else "$base ($n)"
        }
    }
}
