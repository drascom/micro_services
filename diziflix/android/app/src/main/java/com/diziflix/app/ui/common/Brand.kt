package com.diziflix.app.ui.common

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.diziflix.app.R
import com.diziflix.app.domain.TvBrandSpec

/*
 * Tablet/telefon marka görselleri (TV karşılığı: `ui/tv/TvBrand.kt`). Aynı statik PNG'ler (`res/drawable-nodpi/brand_xxx.png`,
 * docs/brand/make_brand.py ile üretilir); en-boy oranı PNG'den gelir, yalnızca bir kenar verilir. Dekoratiftir (odak/etkileşim yok).
 */
@Composable
private fun BrandImage(@DrawableRes res: Int, modifier: Modifier, description: String?) {
    Image(painter = painterResource(res), contentDescription = description, contentScale = ContentScale.Fit, modifier = modifier)
}

/** Profil seçimi: geniş tam logo ([width] dp genişlikte). */
@Composable
fun BrandLogoWide(width: Dp = 110.dp, modifier: Modifier = Modifier) {
    val height = width * (TvBrandSpec.WIDE_PX_H.toFloat() / TvBrandSpec.WIDE_PX_W)
    BrandImage(R.drawable.brand_logo_wide, modifier.width(width).height(height), "DiziFlix")
}

/** Küçük karakter ([height] dp yüksekliğinde): yükleme ve hata ekranlarında yazının/spinner'ın üstünde. */
@Composable
fun BrandMascot(height: Dp = 56.dp, modifier: Modifier = Modifier) {
    val width = height * (TvBrandSpec.MASCOT_PX_W.toFloat() / TvBrandSpec.MASCOT_PX_H)
    BrandImage(R.drawable.brand_mascot, modifier.width(width).height(height), null)
}

/** Açılış/boot ekranı: ortada kare tam logo. */
@Composable
fun BrandBoot(modifier: Modifier = Modifier, size: Dp = 168.dp) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        val height = size * (TvBrandSpec.SQUARE_PX_H.toFloat() / TvBrandSpec.SQUARE_PX_W)
        BrandImage(R.drawable.brand_logo_square, Modifier.width(size).height(height), "DiziFlix")
    }
}
