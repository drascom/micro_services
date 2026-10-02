package com.diziflix.app.ui.tv

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import com.diziflix.app.R
import com.diziflix.app.domain.TvRichItem
import com.diziflix.app.domain.TvRichKind
import com.diziflix.app.domain.TvRichQueue
import com.diziflix.app.domain.TvToastSpec
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay

/*
 * Android TV bildirimleri — Tizen `ui/toast.js`: küçük toast (altta, 3,5 sn) ve zengin bildirim (üst-orta büyük kart,
 * sarı kenar + köşe süsü, 7 sn; tek kart, gelenler sırayla, en çok 3 bekleyen). Odağa girmez, tuş yutmaz.
 * Tablet/telefon Snackbar'ı aynen kalır; bu bileşenler yalnızca TV kökünde kurulur.
 */
@Stable
class TvToaster {
    internal val queue = TvRichQueue()
    internal val wake = Channel<Unit>(Channel.CONFLATED)

    /** Görünen küçük toast metni (null = yok) ve her gösterimde artan sayaç (aynı metin art arda gelse de süre yenilenir). */
    var simple by mutableStateOf<String?>(null)
        internal set
    var simpleSeq by mutableIntStateOf(0)
        internal set

    internal var richItem by mutableStateOf<TvRichItem?>(null)
    internal var richShown by mutableStateOf(false)

    /** `DZ.toast.show`: altta kısa süre görünen metin. */
    fun show(message: String) {
        simple = message
        simpleSeq++
    }

    /** `DZ.toast.showRich`: true = gösterildi/sıraya alındı, false = kuyruk dolu. */
    fun showRich(item: TvRichItem): Boolean {
        val accepted = queue.offer(item)
        if (accepted) wake.trySend(Unit)
        return accepted
    }
}

/** TV kökünde sağlanır; TV dışında null. */
val LocalTvToaster = staticCompositionLocalOf<TvToaster?> { null }

@Composable
fun rememberTvToaster(): TvToaster = remember { TvToaster() }

/** Bildirimleri çizer ve zamanlar. [TvTheme] altında, içeriğin ÜSTÜNDE (son çocuk) çağrılır. */
@Composable
fun TvToastHost(toaster: TvToaster, modifier: Modifier = Modifier) {
    // Zengin bildirim döngüsü: sıradaki kartı göster -> süre -> çıkış -> sıradaki.
    LaunchedEffect(toaster) {
        for (signal in toaster.wake) {
            while (true) {
                val item = toaster.queue.next() ?: break
                toaster.richItem = item
                toaster.richShown = true
                delay(item.ms)
                toaster.richShown = false
                delay(TvToastSpec.RICH_EXIT_MS)
            }
        }
    }
    LaunchedEffect(toaster.simpleSeq) {
        if (toaster.simple != null) {
            delay(TvToastSpec.TOAST_MS)
            toaster.simple = null
        }
    }
    Box(modifier.fillMaxSize()) {
        TvRichCard(toaster)
        val message = toaster.simple
        if (message != null) TvSimpleToast(message, Modifier.align(Alignment.BottomCenter))
    }
}

@Composable
private fun TvSimpleToast(message: String, modifier: Modifier) {
    val dims = LocalTvDims.current
    val shape = RoundedCornerShape(dims.dp(TvToastSpec.TOAST_RADIUS))
    TvText(
        message,
        size = TvToastSpec.TOAST_TEXT,
        weight = FontWeight.Bold,
        align = TextAlign.Center,
        maxLines = 2,
        modifier = modifier
            .padding(bottom = dims.dp(TvToastSpec.TOAST_BOTTOM))
            .width(dims.dp(TvToastSpec.TOAST_W))
            .background(Color(TvToastSpec.TOAST_BG_ARGB), shape)
            .border(dims.dp(2), Color(TvToastSpec.TOAST_BORDER_ARGB), shape)
            .padding(horizontal = dims.dp(TvToastSpec.TOAST_PAD_H), vertical = dims.dp(TvToastSpec.TOAST_PAD_V)),
    )
}

@Composable
private fun TvRichCard(toaster: TvToaster) {
    val dims = LocalTvDims.current
    val item = toaster.richItem ?: return
    val slide = dims.pxInt(24f)
    Box(Modifier.fillMaxSize()) {
        AnimatedVisibility(
            visible = toaster.richShown,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = dims.dp(1080f * TvToastSpec.RICH_TOP_FRACTION)),
            enter = fadeIn(tween(TvToastSpec.RICH_ENTER_MS, easing = TvEase)) +
                slideInVertically(tween(TvToastSpec.RICH_ENTER_MS, easing = TvEase)) { -slide },
            exit = fadeOut(tween(TvToastSpec.RICH_FADE_MS, easing = TvEase)) +
                slideOutVertically(tween(TvToastSpec.RICH_FADE_MS, easing = TvEase)) { -slide },
        ) {
            TvRichBody(item)
        }
    }
}

/** `.toast-rich`: koyu kart, 2 px sarı kenar (uyarıda soluk), sol-üst + sağ-alt (180°) köşe süsü, etiket + başlık + mesaj. */
@Composable
private fun TvRichBody(item: TvRichItem) {
    val dims = LocalTvDims.current
    val shape = RoundedCornerShape(dims.dp(TvToastSpec.RICH_RADIUS))
    val warn = item.kind == TvRichKind.Warn
    val border = if (warn) TvColors.AccentSoft else TvColors.Accent
    val ornAlpha = if (warn) TvToastSpec.ORN_WARN_ALPHA else 1f
    // Süs, kartın dolgu kutusuna göre -16 taşar (kenar 2 px dışarıda kalır)
    val out = dims.dp(-(TvToastSpec.ORN_OUT + TvToastSpec.RICH_BORDER))
    Box(
        Modifier
            .width(dims.dp(TvToastSpec.RICH_W))
            .background(Color(TvToastSpec.RICH_BG_ARGB), shape)
            .border(dims.dp(TvToastSpec.RICH_BORDER), border, shape),
    ) {
        Image(
            painter = painterResource(R.drawable.tv_ornament_corner),
            contentDescription = null,
            contentScale = ContentScale.Fit,
            alignment = Alignment.TopStart,
            modifier = Modifier
                .align(Alignment.TopStart)
                .offset(out, out)
                .size(dims.dp(TvToastSpec.ORN_W), dims.dp(TvToastSpec.ORN_H))
                .alpha(ornAlpha),
        )
        Image(
            painter = painterResource(R.drawable.tv_ornament_corner),
            contentDescription = null,
            contentScale = ContentScale.Fit,
            alignment = Alignment.TopStart,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .offset(-out, -out)
                .size(dims.dp(TvToastSpec.ORN_W), dims.dp(TvToastSpec.ORN_H))
                .graphicsLayer { rotationZ = 180f }
                .alpha(ornAlpha),
        )
        Column(
            Modifier.padding(
                start = dims.dp(TvToastSpec.RICH_PAD_H),
                end = dims.dp(TvToastSpec.RICH_PAD_H),
                top = dims.dp(TvToastSpec.RICH_PAD_TOP),
                bottom = dims.dp(TvToastSpec.RICH_PAD_BOTTOM),
            ),
        ) {
            TvText(
                item.label,
                size = TvToastSpec.RICH_LABEL,
                color = TvColors.Accent,
                weight = FontWeight.ExtraBold,
                letterSpacingEm = 0.12f,
            )
            Spacer(Modifier.height(dims.dp(TvToastSpec.RICH_LABEL_MB)))
            TvText(
                item.title,
                size = TvToastSpec.RICH_TITLE,
                weight = FontWeight.Black,
                line = TvToastSpec.RICH_TITLE_LINE,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth(),
            )
            if (item.hasMessage) {
                Spacer(Modifier.height(dims.dp(TvToastSpec.RICH_MSG_MT)))
                TvText(
                    item.message,
                    size = TvToastSpec.RICH_MSG,
                    color = TvColors.Dim,
                    weight = FontWeight.Medium,
                    line = TvToastSpec.RICH_MSG_LINE,
                    maxLines = 3,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}
