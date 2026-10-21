package com.diziflix.app.play

import kotlin.random.Random

/** Yükleme ekranında dönen Türkçe atasözleri. */
object Proverbs {
    val ALL: List<String> = listOf(
        "Damlaya damlaya göl olur.",
        "Sabreden derviş muradına ermiş.",
        "Ağaç yaşken eğilir.",
        "Bir elin nesi var, iki elin sesi var.",
        "Ak akçe kara gün içindir.",
        "Acele işe şeytan karışır.",
        "Bugünün işini yarına bırakma.",
        "Bin bilsen de bir bilene danış.",
        "Bir fincan kahvenin kırk yıllık hatırı vardır.",
        "Birlikten kuvvet doğar.",
        "Dost kara günde belli olur.",
        "Gülü seven dikenine katlanır.",
        "Görünen köy kılavuz istemez.",
        "İyilik yap denize at, balık bilmezse Halik bilir.",
        "Sakla samanı, gelir zamanı.",
        "Sütten ağzı yanan yoğurdu üfleyerek yer.",
        "Tatlı dil yılanı deliğinden çıkarır.",
        "Ne ekersen onu biçersin.",
        "Nerede birlik, orada dirlik.",
        "Demir tavında dövülür.",
        "Emek olmadan yemek olmaz.",
        "Kervan yolda düzülür.",
        "Söz gümüşse sükût altındır.",
        "Zaman her şeyin ilacıdır.",
    )
}

/**
 * Tekrarsız rastgele seçici: bir tur bitmeden hiçbir atasözü tekrar gelmez; tur bitince yeniden
 * karıştırılır ve yeni turun ilki bir önceki turun sonuncusu olmaz. Boş listede çökmez, "" döndürür.
 */
class ProverbPicker(
    private val source: List<String> = Proverbs.ALL,
    private val random: Random = Random.Default,
) {
    private val queue = ArrayDeque<String>()
    private var last: String? = null

    val size: Int get() = source.size

    fun next(): String {
        if (source.isEmpty()) return ""
        if (queue.isEmpty()) refill()
        val value = queue.removeFirst()
        last = value
        return value
    }

    private fun refill() {
        val shuffled = source.shuffled(random).toMutableList()
        if (shuffled.size > 1 && shuffled.first() == last) {
            val t = shuffled[0]
            shuffled[0] = shuffled[shuffled.size - 1]
            shuffled[shuffled.size - 1] = t
        }
        queue.addAll(shuffled)
    }
}
