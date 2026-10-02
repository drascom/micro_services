package com.diziflix.app.domain

import com.diziflix.app.data.net.ApiException
import com.diziflix.app.data.net.userMessage
import java.io.IOException

/** Hatanın kullanıcıya gösterilecek nedeni. */
enum class FailureKind { Offline, ServerUnreachable, BadAddress, Other }

data class Failure(val kind: FailureKind, val message: String)

/**
 * Önbellekten gösterilen içeriğin üstündeki şerit bilgisi ("Çevrimdışı · son güncelleme: 5 dk önce").
 * [savedAtMs]: önbellekteki kopyanın ağdan alındığı zaman (epoch ms).
 */
data class OfflineNotice(val kind: FailureKind, val savedAtMs: Long) {
    fun label(nowMs: Long): String {
        val head = if (kind == FailureKind.Offline) "Çevrimdışı" else "Sunucuya ulaşılamıyor"
        return head + " · son güncelleme: " + Format.relativeTime(nowMs, savedAtMs)
    }
}

/** Ağ hatalarını nedene göre ayırır (saf Kotlin, birim testli). */
object ErrorLogic {
    const val OFFLINE_MESSAGE = "Ağa bağlı değilsiniz"

    /** Oynat/Fragman'a çevrimdışıyken basılınca (yükleme ekranına/akış isteğine girmeden) gösterilir. */
    const val OFFLINE_PLAY_MESSAGE = "Oynatmak için internet gerekli"

    /** Ağ/zaman aşımı (bağlantı) hatası mı? HTTP/ayrıştırma hataları değildir. */
    fun isConnectivityError(error: Throwable): Boolean {
        val api = error as? ApiException
        val code = api?.code
        return code == "network" || code == "timeout" || (api == null && error is IOException)
    }

    /**
     * Önbellekten gösterilirken ağ yenilemesi [error] ile başarısız olduysa şerit bilgisi; hata bağlantı
     * hatası değilse (ör. 400) null (o durumda ayrı bir uyarı gösterilir).
     */
    fun offlineNotice(error: Throwable, online: Boolean, savedAtMs: Long): OfflineNotice? {
        if (!isConnectivityError(error)) return null
        return OfflineNotice(if (online) FailureKind.ServerUnreachable else FailureKind.Offline, savedAtMs)
    }

    /**
     * [online]: cihazın ağ bağlantısı var mı; [baseUrl]: ayarlı sunucu adresi.
     * Ağ/zaman aşımı hatası + cihaz çevrimdışı -> "Ağa bağlı değilsiniz"; çevrimiçiyse
     * "Sunucuya ulaşılamıyor: <adres>". Diğer hatalar kendi mesajıyla kalır.
     */
    fun classify(error: Throwable, online: Boolean, baseUrl: String): Failure {
        val api = error as? ApiException
        val code = api?.code
        val connectivity = isConnectivityError(error)
        return when {
            connectivity && !online -> Failure(FailureKind.Offline, OFFLINE_MESSAGE)
            code == "timeout" -> Failure(FailureKind.ServerUnreachable, "Sunucuya ulaşılamıyor: $baseUrl (zaman aşımı)")
            connectivity -> Failure(FailureKind.ServerUnreachable, "Sunucuya ulaşılamıyor: $baseUrl")
            code == "bad_base" -> Failure(FailureKind.BadAddress, error.userMessage())
            else -> Failure(FailureKind.Other, error.userMessage())
        }
    }
}
