package com.diziflix.app.ui.tv

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import com.diziflix.app.R
import com.diziflix.app.domain.TvBrandSpec

/*
 * Android TV marka görselleri (Tizen logo-wide / logo-square / mascot / mascot-sm kullanım yerleri): statik drawable
 * (`painterResource`; Coil/R8 kuralı gerekmez), odaklanmaz, gezinme sırasına girmez. Ölçüler Tizen px ([TvBrandSpec]).
 */
@Composable
private fun TvBrandImage(@DrawableRes res: Int, widthTizen: Float, heightTizen: Float, modifier: Modifier, description: String?) {
    val dims = LocalTvDims.current
    Image(
        painter = painterResource(res),
        contentDescription = description,
        contentScale = ContentScale.Fit,
        modifier = modifier.size(dims.dp(widthTizen), dims.dp(heightTizen)),
    )
}

/** Profil seçimi: geniş tam logo (eski `DIZIFLIX` yazısının yerine). */
@Composable
fun TvLogoWide(modifier: Modifier = Modifier) =
    TvBrandImage(R.drawable.brand_logo_wide, TvBrandSpec.WIDE_W, TvBrandSpec.WIDE_H, modifier, "DiziFlix")

/** Açılış/yükleme: kare tam logo, koyu yarı saydam plaka üstünde ortada (Tizen `.boot-logo`). */
@Composable
fun TvBootLogo(modifier: Modifier = Modifier) {
    val dims = LocalTvDims.current
    Box(
        modifier
            .size(dims.dp(TvBrandSpec.BOOT_PLATE_W), dims.dp(TvBrandSpec.BOOT_PLATE_H))
            .background(Color(0xE6141414), RoundedCornerShape(dims.dp(TvBrandSpec.BOOT_PLATE_RADIUS))),
        contentAlignment = Alignment.Center,
    ) {
        TvBrandImage(R.drawable.brand_logo_square, TvBrandSpec.SQUARE_W, TvBrandSpec.SQUARE_H, Modifier, "DiziFlix")
    }
}

/** Oynatma yükleniyor ekranında spinner'ın üstündeki küçük karakter. */
@Composable
fun TvMascotLoading(modifier: Modifier = Modifier) =
    TvBrandImage(R.drawable.brand_mascot, TvBrandSpec.MASCOT_LOAD_W, TvBrandSpec.MASCOT_LOAD_H, modifier, null)

/** Hata ekranlarında yazının üstündeki küçük karakter. */
@Composable
fun TvMascotError(modifier: Modifier = Modifier) =
    TvBrandImage(R.drawable.brand_mascot, TvBrandSpec.MASCOT_ERR_W, TvBrandSpec.MASCOT_ERR_H, modifier, null)

/** Üst menüde `DIZIFLIX` yazısının solundaki küçük karakter amblemi (dekoratif; odaklanmaz). */
@Composable
fun TvEmblem(modifier: Modifier = Modifier) =
    TvBrandImage(R.drawable.brand_mascot_sm, TvBrandSpec.EMBLEM_W, TvBrandSpec.EMBLEM_H, modifier, null)
