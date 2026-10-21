package com.diziflix.app.data.net

import kotlinx.coroutines.CancellationException

/**
 * Sunucu/ağ hatası. [code] sunucudan gelen hata kodu ya da istemci kodu
 * (network, timeout, bad_json, bad_base, http_NNN, not_found, bad_request...).
 */
class ApiException(
    val code: String,
    message: String,
    val status: Int = 0,
    cause: Throwable? = null,
) : Exception(message, cause)

/** Kullanıcıya gösterilecek Türkçe/sunucu mesajı. */
fun Throwable.userMessage(): String {
    val m = message
    return if (m.isNullOrBlank()) "Bilinmeyen hata" else m
}

/**
 * Coroutine iptalini yutmadan hatayı Result'a çevirir (runCatching CancellationException'ı da yakalar;
 * bu yardımcı yakalamaz).
 */
suspend fun <T> apiCall(block: suspend () -> T): Result<T> {
    return try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Result.failure(e)
    }
}
