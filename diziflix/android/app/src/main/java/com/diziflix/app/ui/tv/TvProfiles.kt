package com.diziflix.app.ui.tv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.model.Profile
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.TvBrandSpec
import com.diziflix.app.domain.TvProfilesLogic
import com.diziflix.app.domain.TvProfilesSpec
import com.diziflix.app.ui.common.LocalBaseUrl
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.UiState
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.profiles.ProfilesViewModel

/*
 * Android TV PROFİL SEÇİMİ — Tizen `css/profiles.css` + `js/screens/profiles.js`: ortada geniş DiziFlix logosu, "Kim izliyor?",
 * 200x200 avatar kutuları (odakta sarı çerçeve + %110), altta "Ayarlar" düğmesi. (Profil ekleme/düzenleme Android'de
 * yok; sunucu API'si bu istemcide kullanılmıyor.) Tablet `ProfilesScreen`'inden AYRI; ViewModel ortak.
 */
@Composable
fun TvProfilesScreen(onOpenSettings: () -> Unit) {
    val vm = containerViewModel { ProfilesViewModel(it) }
    val state by vm.state.collectAsStateWithLifecycle()
    RefreshWhenBackOnline(LocalContainer.current, isStale = vm::needsRefresh, refresh = vm::refreshAfterReconnect)

    TvTheme {
        Box(Modifier.fillMaxSize().background(TvColors.Background)) {
            when (val content = state.content) {
                is UiState.Loading -> TvProfilesSkeleton()
                is UiState.Error -> TvErrorScreen(
                    title = "Profiller yüklenemedi",
                    message = content.message,
                    actions = listOf("Tekrar dene" to { vm.load() }, "Ayarlar" to onOpenSettings),
                )
                is UiState.Data -> if (content.value.isEmpty()) {
                    TvErrorScreen(
                        title = "Profil yok",
                        message = "Sunucuda henüz profil bulunmuyor.",
                        actions = listOf("Tekrar dene" to { vm.load() }, "Ayarlar" to onOpenSettings),
                    )
                } else {
                    TvProfilesContent(content.value, onSelect = vm::select, onOpenSettings = onOpenSettings)
                }
            }
            val offline = state.offline
            if (offline != null) {
                val dims = LocalTvDims.current
                val label = remember(offline) { offline.label(System.currentTimeMillis()) }
                TvOfflineChip(label, Modifier.align(Alignment.BottomStart).padding(start = dims.dp(60), bottom = dims.dp(40)))
            }
        }
    }
}

@Composable
private fun TvProfilesSkeleton() {
    val dims = LocalTvDims.current
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        TvLogoWide()
        Spacer(Modifier.height(dims.dp(TvBrandSpec.WIDE_MB + TvProfilesSpec.WORDMARK_MB)))
        Row {
            repeat(3) {
                TvShimmer(
                    Modifier
                        .padding(horizontal = dims.dp(TvProfilesSpec.PROFILE_MX))
                        .size(dims.dp(TvProfilesSpec.AVATAR))
                        .clip(RoundedCornerShape(dims.dp(4))),
                )
            }
        }
    }
}

@Composable
private fun TvProfilesContent(profiles: List<Profile>, onSelect: (Profile) -> Unit, onOpenSettings: () -> Unit) {
    val dims = LocalTvDims.current
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { first.requestFocusWhenReady() }
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        // eski DIZIFLIX yazısının yerine geniş tam logo (Tizen profiles.js: img/logo-wide.png)
        TvLogoWide()
        Spacer(Modifier.height(dims.dp(TvBrandSpec.WIDE_MB)))
        TvText("Kim izliyor?", size = TvProfilesSpec.TITLE, color = TvColors.Overview, align = TextAlign.Center)
        Spacer(Modifier.height(dims.dp(TvProfilesSpec.TITLE_MB)))
        Row(verticalAlignment = Alignment.Top) {
            profiles.forEachIndexed { index, profile ->
                TvProfileTile(profile, onClick = { onSelect(profile) }, modifier = if (index == 0) Modifier.focusRequester(first) else Modifier)
            }
        }
        Spacer(Modifier.height(dims.dp(TvProfilesSpec.FOOT_MT)))
        TvButton("Ayarlar", onOpenSettings)
    }
}

/** `.profile`: 240 px sütun; avatar 200x200 (8 px köşe, 5 px kenar), ad 24 px; odakta %110 + sarı avatar kenarı + beyaz ad. */
@Composable
private fun TvProfileTile(profile: Profile, onClick: () -> Unit, modifier: Modifier) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) TvProfilesSpec.FOCUS_SCALE else 1f, tween(200, easing = TvEase), label = "tvProfileScale")
    val shape = RoundedCornerShape(dims.dp(TvProfilesSpec.AVATAR_RADIUS))
    val avatarUrl = remember(base, profile) { UrlUtil.sized(base, profile.avatar, TvProfilesSpec.AVATAR_IMG, TvProfilesSpec.AVATAR_IMG) }
    var failed by remember(avatarUrl) { mutableStateOf(false) }
    Column(
        modifier
            .padding(horizontal = dims.dp(TvProfilesSpec.PROFILE_MX))
            .width(dims.dp(TvProfilesSpec.PROFILE_W))
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .onFocusChanged { focused = it.isFocused }
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier
                .size(dims.dp(TvProfilesSpec.AVATAR))
                .clip(shape)
                .background(Brush.linearGradient(listOf(Color(0xFF2C2C2C), Color(0xFF3A3A3A))), shape),
            contentAlignment = Alignment.Center,
        ) {
            TvText(
                TvProfilesLogic.initials(profile.name),
                size = TvProfilesSpec.INITIAL,
                color = TvColors.Accent,
                weight = FontWeight.Black,
                align = TextAlign.Center,
            )
            if (avatarUrl.isNotEmpty() && !failed) {
                TvImage(
                    avatarUrl,
                    dims.pxInt(TvProfilesSpec.AVATAR),
                    dims.pxInt(TvProfilesSpec.AVATAR),
                    Modifier.fillMaxSize(),
                    rgb565 = true,
                    fadeMs = 200,
                    onFailed = { failed = true },
                )
            }
            Box(
                Modifier
                    .fillMaxSize()
                    .border(dims.dp(TvProfilesSpec.AVATAR_BORDER), if (focused) TvColors.Accent else Color.Transparent, shape),
            )
        }
        Spacer(Modifier.height(dims.dp(TvProfilesSpec.AVATAR_MB)))
        TvText(
            profile.name,
            size = TvProfilesSpec.NAME,
            color = if (focused) Color.White else TvColors.Dim,
            align = TextAlign.Center,
            modifier = Modifier.width(dims.dp(TvProfilesSpec.PROFILE_W)),
        )
        if (profile.isKids) {
            TvText(TvProfilesLogic.KIDS_BADGE, size = TvProfilesSpec.KIDS, color = TvColors.Accent, letterSpacingEm = 0.1f)
        }
    }
}
