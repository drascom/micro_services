package com.diziflix.app.domain

import java.util.Locale

/** Biçimlendirme yardımcıları (saf Kotlin). */
object Format {
    private val MONTHS = listOf("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")

    /** Oynatıcı zamanı: 1:05 ya da 1:02:03. */
    fun clock(ms: Long): String {
        val total = if (ms < 0) 0L else ms / 1000
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) "%d:%02d:%02d".format(Locale.ROOT, h, m, s) else "%d:%02d".format(Locale.ROOT, m, s)
    }

    /** "45 dk" ya da "1 sa 30 dk". 0/negatif -> "". */
    fun minutes(min: Double?): String {
        if (min == null || min <= 0.0) return ""
        val total = Math.round(min).toInt()
        val h = total / 60
        val m = total % 60
        return if (h > 0) "$h sa $m dk" else "$m dk"
    }

    /** ISO tarih (YYYY-MM-DD...) -> "24 Tem 2026"; geçersizse "". */
    fun date(iso: String?): String {
        if (!isIsoDate(iso)) return ""
        val s = iso!!
        val month = s.substring(5, 7).toInt()
        val name = MONTHS.getOrNull(month - 1) ?: return ""
        return "${s.substring(8, 10).toInt()} $name ${s.substring(0, 4)}"
    }

    fun isIsoDate(v: String?): Boolean = v != null && Regex("^\\d{4}-\\d{2}-\\d{2}").containsMatchIn(v)

    /** Göreli zaman: "az önce", "5 dk önce", "3 sa önce", "2 gün önce" (gelecek/negatif -> "az önce"). */
    fun relativeTime(nowMs: Long, thenMs: Long): String {
        val minutes = (nowMs - thenMs) / 60_000L
        return when {
            minutes < 1 -> "az önce"
            minutes < 60 -> "$minutes dk önce"
            minutes < 24 * 60 -> "${minutes / 60} sa önce"
            else -> "${minutes / (24 * 60)} gün önce"
        }
    }

    /** Satır içi kısaltma: boşlukları sadeleştirir, kelime sınırında keser. */
    fun truncate(text: String?, limit: Int): String {
        val value = (text ?: "").replace(Regex("\\s+"), " ").trim()
        if (value.length <= limit) return value
        val head = value.substring(0, limit - 1)
        val cut = head.replace(Regex("\\s+\\S*$"), "")
        return (cut.ifEmpty { head }) + "…"
    }
}
