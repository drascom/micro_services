package com.diziflix.app.ui.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** Üst menü eylemleri (navigasyon grafiğinden gelir; tablet alt çubuğunun TV karşılığı). */
class TvBarActions(
    val onHome: () -> Unit,
    val onSearch: () -> Unit,
    val onMyList: () -> Unit,
    val onProfile: () -> Unit,
    val onSettings: () -> Unit,
)

/**
 * TV çerçevesi: Tizen üst menüsü ([TvTopBar]) üst düzey sayfalarda (Ana sayfa, Filmler, Diziler, Listem) sabit üstte durur;
 * içerik altında kalan alanı doldurur. Arama sayfası üst menüsünü (gerçek yazı alanıyla) kendisi çizer; ayrıntı, ayarlar ve
 * oynatıcı tam ekrandır. Güvenli alan/boşluklar ekranların kendi Tizen ölçüleriyle verilir (dış boşluk YOK).
 */
@Composable
fun TvNavFrame(
    showBar: Boolean,
    selected: TvTopBarTab?,
    bar: TvBarActions,
    barFocus: TvBarFocus,
    content: @Composable () -> Unit,
) {
    TvTheme {
        Column(Modifier.fillMaxSize().background(TvColors.Background)) {
            if (showBar) {
                TvTopBar(
                    selected = selected,
                    onHome = bar.onHome,
                    onSearch = bar.onSearch,
                    onMyList = bar.onMyList,
                    onProfile = bar.onProfile,
                    onSettings = bar.onSettings,
                    searchFocus = barFocus.entry,
                    itemFocus = barFocus::requester,
                )
            }
            Box(Modifier.weight(1f).fillMaxWidth()) { content() }
        }
    }
}
