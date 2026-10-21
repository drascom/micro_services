package com.diziflix.app.ui.tv

import android.app.UiModeManager
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.Configuration
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.focus.FocusRequester
import com.diziflix.app.domain.TvDetector

/**
 * Android TV modu: tek APK, çalışma zamanında algılanır ([detectTv]). TV arayüzü ayrı composable'lardan kurulur
 * (`ui/tv/`); tablet/telefon composable'ları bu değere bağlı dal İÇERMEZ — yalnızca navigasyon grafiği girişinde
 * `if (isTv) { TvXxxScreen(...); return@composable }` yönlendirmesi vardır.
 */
val LocalIsTv = staticCompositionLocalOf { false }

/** UiModeManager / leanback / dokunmatik yok kararı ([TvDetector]). */
fun detectTv(context: Context): Boolean {
    val uiMode = (context.getSystemService(Context.UI_MODE_SERVICE) as? UiModeManager)?.currentModeType
        ?: Configuration.UI_MODE_TYPE_NORMAL
    val pm = context.packageManager
    return TvDetector.isTv(
        uiModeType = uiMode,
        hasLeanback = pm.hasSystemFeature(PackageManager.FEATURE_LEANBACK),
        hasTouchscreen = pm.hasSystemFeature(PackageManager.FEATURE_TOUCHSCREEN),
    )
}

/** Odak isteği: öğe henüz yerleşmemişse birkaç kare bekleyip yeniden dener (en çok [maxFrames]). */
suspend fun FocusRequester.requestFocusWhenReady(maxFrames: Int = 40): Boolean {
    repeat(maxFrames) {
        try {
            requestFocus()
            return true
        } catch (_: Exception) {
            // Henüz bir odak hedefine bağlanmadı (kompozisyon/yerleşim sürüyor): sonraki kareyi bekle.
        }
        withFrameNanos { }
    }
    return false
}

/** Tek seferlik, bekleme yok: bağlı değilse false (özel gezinme geri dönüşleri için). */
fun FocusRequester.tryFocus(): Boolean = try {
    requestFocus()
    true
} catch (_: Exception) {
    false
}
