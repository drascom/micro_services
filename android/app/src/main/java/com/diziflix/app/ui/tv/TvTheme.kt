package com.diziflix.app.ui.tv

import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.diziflix.app.domain.tvDp
import kotlin.math.roundToInt

/**
 * Tizen istemcisinin renkleri (base.css :root). TV ekranları yalnızca bunları kullanır; tablet teması
 * ([com.diziflix.app.ui.theme.DzColors]) bu dosyadan etkilenmez.
 */
object TvColors {
    val Background = Color(0xFF141414)      // --bg
    val Foreground = Color(0xFFFFFFFF)      // --fg
    val Dim = Color(0xFFB3B3B3)             // --fg-dim
    val Accent = Color(0xFFF5C518)          // --accent
    val AccentSoft = Color(0xFF85752A)      // --accent-soft
    val Surface = Color(0xFF1F1F1F)         // --surface
    val Surface2 = Color(0xFF2A2A2A)        // --surface-2
    val Divider = Color(0xFF303030)         // .main-navigation border-bottom
    val Score = Color(0xFFE1C576)           // .row-card-score
    val Overview = Color(0xFFE5E5E5)        // .hero-overview / .row-title
    val Ink = Color(0xFF111111)             // odaktaki sarı yüzeydeki koyu yazı
    val ButtonFill = Color(0xB36D6D6E)      // .btn rgba(109,109,110,.7)
    val ButtonDisabled = Color(0x8C464646)  // .btn.disabled rgba(70,70,70,.55)
}

/**
 * Tizen px -> dp/sp/piksel dönüştürücü. [unitDp] = 1 Tizen px'in dp karşılığı
 * (`tvDp(1, ekranGenişliğiPx, density)`); 1080p + density 2'de 0,5.
 * Yazı boyutları sp değil "dp değerinde sp" verilir: [TvTheme] yazı ölçeğini 1'e sabitler, yani TV tasarımı sistem
 * yazı ölçeğinden bağımsız ve Tizen'deki gibi sabit piksel oranındadır.
 */
@Immutable
class TvDims(val unitDp: Float, val density: Float) {
    fun dp(tizenPx: Float): Dp = (tizenPx * unitDp).dp
    fun dp(tizenPx: Int): Dp = dp(tizenPx.toFloat())
    fun sp(tizenPx: Float): TextUnit = (tizenPx * unitDp).sp
    fun sp(tizenPx: Int): TextUnit = sp(tizenPx.toFloat())

    /** Gerçek ekran pikseli (float). */
    fun px(tizenPx: Float): Float = tizenPx * unitDp * density
    fun pxInt(tizenPx: Float): Int = px(tizenPx).roundToInt()
}

val LocalTvDims = compositionLocalOf { TvDims(unitDp = 0.5f, density = 2f) }

/**
 * TV ekranlarının kökü: Tizen ölçeğini ([TvDims]) sağlar ve yazı ölçeğini 1'e sabitler. Yalnızca TV yolunda çağrılır;
 * `MaterialTheme`'e dokunmaz.
 */
@Composable
fun TvTheme(content: @Composable () -> Unit) {
    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val widthPx = (configuration.screenWidthDp * density.density).roundToInt()
    val unit = tvDp(1f, widthPx, density.density)
    val dims = remember(unit, density.density) { TvDims(unit, density.density) }
    CompositionLocalProvider(
        LocalTvDims provides dims,
        LocalDensity provides Density(density.density, 1f),
        content = content,
    )
}

/**
 * Tizen yazısı: boyut/satır yüksekliği Tizen px. `BasicText` kullanılır (Material tipografisinin harf aralığı/renk
 * varsayılanları karışmasın).
 */
@Composable
fun TvText(
    text: String,
    size: Float,
    modifier: Modifier = Modifier,
    color: Color = TvColors.Foreground,
    weight: FontWeight = FontWeight.Normal,
    fontStyle: FontStyle = FontStyle.Normal,
    line: Float? = null,
    letterSpacingEm: Float? = null,
    maxLines: Int = 1,
    align: TextAlign = TextAlign.Start,
    overflow: TextOverflow = TextOverflow.Ellipsis,
) {
    val dims = LocalTvDims.current
    BasicText(
        text = text,
        modifier = modifier,
        style = TextStyle(
            color = color,
            fontSize = dims.sp(size),
            fontWeight = weight,
            fontStyle = fontStyle,
            lineHeight = if (line != null) dims.sp(line) else TextUnit.Unspecified,
            letterSpacing = if (letterSpacingEm != null) letterSpacingEm.em else TextUnit.Unspecified,
            textAlign = align,
        ),
        maxLines = maxLines,
        overflow = overflow,
    )
}
