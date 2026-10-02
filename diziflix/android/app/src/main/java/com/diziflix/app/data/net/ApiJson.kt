package com.diziflix.app.data.net

import kotlinx.serialization.json.Json

/**
 * Tüm ayrıştırma bu örnekle yapılır.
 *  - ignoreUnknownKeys: sunucu alan ekleyebilir (geriye uyumlu genişleme sözleşmesi).
 *  - coerceInputValues: null/eksik-tip değerler, varsayılanı olan alanlarda varsayılana çevrilir.
 *  - isLenient: tırnaksız/esnek JSON'a tolerans.
 */
object ApiJson {
    val instance: Json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        isLenient = true
    }
}
