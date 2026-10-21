package com.diziflix.app

import com.diziflix.app.data.net.ApiJson
import kotlinx.serialization.DeserializationStrategy

/** src/test/resources altındaki GERÇEK sunucu yanıtlarını (token/imzalı URL değerleri sansürlü) okur. */
object Fixtures {
    fun text(name: String): String {
        val loader = checkNotNull(Fixtures::class.java.classLoader) { "sınıf yükleyici yok" }
        val stream = loader.getResourceAsStream(name) ?: error("Fixture bulunamadı: $name")
        return stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
    }

    fun <T> parse(name: String, serializer: DeserializationStrategy<T>): T =
        ApiJson.instance.decodeFromString(serializer, text(name))
}
