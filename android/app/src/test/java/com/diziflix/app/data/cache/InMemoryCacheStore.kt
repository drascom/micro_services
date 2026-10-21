package com.diziflix.app.data.cache

/** Testler için bellek içi depolama (ekleme sırası korunur). */
class InMemoryCacheStore : CacheStore {
    val files = LinkedHashMap<String, String>()

    override suspend fun read(name: String): String? = files[name]

    override suspend fun write(name: String, text: String) {
        files[name] = text
    }

    override suspend fun delete(name: String) {
        files.remove(name)
    }

    override suspend fun names(): List<String> = files.keys.toList()
}
