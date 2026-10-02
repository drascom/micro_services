package com.diziflix.app.data.cache

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * Adlandırılmış metin kayıtları için çok küçük depolama soyutlaması (çevrimdışı açılış önbelleği).
 * Gerçek uygulama [FileCacheStore]; testlerde bellek içi sahte kullanılır.
 */
interface CacheStore {
    /** Kayıt yoksa ya da okunamıyorsa null. */
    suspend fun read(name: String): String?

    /** Atomik yazım: okuyan ya yeni ya eski içeriği görür, yarım dosya görmez. */
    suspend fun write(name: String, text: String)

    suspend fun delete(name: String)

    suspend fun names(): List<String>
}

/**
 * `dir/<kodlanmış ad>.json` dosyaları. Yazım: geçici dosya + atomik taşıma (güç kesintisinde yarım
 * dosya kalmaz). Ad kodlaması: [A-Za-z0-9_-] aynen, diğer her UTF-8 baytı `~HH` (ters çevrilebilir).
 */
class FileCacheStore(
    private val dir: File,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : CacheStore {

    override suspend fun read(name: String): String? = withContext(io) {
        val file = File(dir, encodeName(name) + EXT)
        try {
            if (file.isFile) file.readText(Charsets.UTF_8) else null
        } catch (e: IOException) {
            null
        }
    }

    override suspend fun write(name: String, text: String) = withContext(io) {
        dir.mkdirs()
        val encoded = encodeName(name)
        val target = File(dir, encoded + EXT)
        val tmp = File(dir, "$encoded.tmp")
        tmp.writeText(text, Charsets.UTF_8)
        try {
            Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } catch (e: AtomicMoveNotSupportedException) {
            Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
        Unit
    }

    override suspend fun delete(name: String) = withContext(io) {
        File(dir, encodeName(name) + EXT).delete()
        Unit
    }

    override suspend fun names(): List<String> = withContext(io) {
        dir.listFiles { f -> f.isFile && f.name.endsWith(EXT) }
            ?.mapNotNull { decodeName(it.name.removeSuffix(EXT)) }
            ?: emptyList()
    }

    companion object {
        private const val EXT = ".json"
        private const val HEX = "0123456789ABCDEF"

        fun encodeName(name: String): String {
            val out = StringBuilder(name.length + 8)
            for (b in name.toByteArray(Charsets.UTF_8)) {
                val c = (b.toInt() and 0xFF).toChar()
                if (c in 'a'..'z' || c in 'A'..'Z' || c in '0'..'9' || c == '_' || c == '-') {
                    out.append(c)
                } else {
                    val v = b.toInt() and 0xFF
                    out.append('~').append(HEX[v shr 4]).append(HEX[v and 0xF])
                }
            }
            return out.toString()
        }

        /** Geçersiz (bu kodlayıcıdan çıkmamış) ad için null. */
        fun decodeName(encoded: String): String? {
            val bytes = java.io.ByteArrayOutputStream(encoded.length)
            var i = 0
            while (i < encoded.length) {
                val c = encoded[i]
                if (c == '~') {
                    if (i + 2 >= encoded.length) return null
                    val hi = HEX.indexOf(encoded[i + 1])
                    val lo = HEX.indexOf(encoded[i + 2])
                    if (hi < 0 || lo < 0) return null
                    bytes.write((hi shl 4) or lo)
                    i += 3
                } else {
                    bytes.write(c.code)
                    i++
                }
            }
            return String(bytes.toByteArray(), Charsets.UTF_8)
        }
    }
}
