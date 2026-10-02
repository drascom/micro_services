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
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.PlayPrefs
import com.diziflix.app.domain.TvSettingsLogic
import com.diziflix.app.domain.TvSettingsSpec
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.settings.SettingsViewModel

/*
 * Android TV AYARLAR ekranı — Tizen `css/settings.css` + `js/screens/settings.js` (yeni tasarım) karşılığı. Tablet
 * `SettingsScreen`'inden AYRI; ViewModel ortak. Üstte sağda "Profil değiştir"; "Sunucu adresi" kartı [girdi + içinde
 * "Bağlantıyı test et"] [sarı Kaydet]; yan yana iki yuvarlak kart ("Varsayılan altyazı dili" / "En yüksek kalite",
 * dikey radyo listeleri); altta "Önbelleği temizle" + soluk bilgi satırları. Logo yok. Açılış odağı "Kaydet".
 */
@Composable
fun TvSettingsScreen(showSwitchProfile: Boolean) {
    val vm = containerViewModel { SettingsViewModel(it) }
    val state by vm.state.collectAsStateWithLifecycle()
    TvTheme {
        TvSettingsBody(s = state, vm = vm, showSwitchProfile = showSwitchProfile)
    }
}

@Composable
private fun TvSettingsBody(s: com.diziflix.app.ui.settings.SettingsUiState, vm: SettingsViewModel, showSwitchProfile: Boolean) {
    val dims = LocalTvDims.current
    val context = LocalContext.current
    val toaster = LocalTvToaster.current
    val keyboard = LocalSoftwareKeyboardController.current
    val saveFocus = remember { FocusRequester() }
    var inputFocused by remember { mutableStateOf(false) }

    val versionName = remember {
        try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
        } catch (e: Exception) {
            "?"
        }
    }
    LaunchedEffect(Unit) {
        vm.probeServer()
        saveFocus.requestFocusWhenReady()
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(TvColors.Background)
            .verticalScroll(rememberScrollState())
            .padding(start = dims.dp(TvSettingsSpec.PAD_H), end = dims.dp(TvSettingsSpec.PAD_H), top = dims.dp(TvSettingsSpec.PAD_TOP)),
    ) {
        // ---- üst satır: sağda "Profil değiştir"
        Row(Modifier.fillMaxWidth().height(dims.dp(TvSettingsSpec.TOP_ROW_H)), verticalAlignment = Alignment.CenterVertically) {
            Spacer(Modifier.weight(1f))
            if (showSwitchProfile) {
                TvSetButton("Profil değiştir", onClick = { vm.switchProfile() }, variant = SetButtonVariant.Top)
            }
        }
        TvText(
            "Ayarlar",
            size = TvSettingsSpec.TITLE,
            weight = FontWeight.Black,
            modifier = Modifier.padding(top = dims.dp(TvSettingsSpec.TITLE_MT), bottom = dims.dp(TvSettingsSpec.TITLE_MB)),
        )

        // ---- sunucu kartı
        SetCard {
            TvText(TvSettingsSpec.SERVER_TITLE, size = TvSettingsSpec.CARD_TITLE, weight = FontWeight.Bold)
            Row(Modifier.fillMaxWidth().padding(top = dims.dp(12)), verticalAlignment = Alignment.CenterVertically) {
                val fieldShape = RoundedCornerShape(dims.dp(TvSettingsSpec.FIELD_RADIUS))
                Row(
                    Modifier
                        .weight(1f)
                        .height(dims.dp(TvSettingsSpec.FIELD_H))
                        .background(if (inputFocused) Color(0xFF252525) else TvColors.Surface2, fieldShape)
                        .border(dims.dp(TvSettingsSpec.FIELD_BORDER), if (inputFocused) TvColors.Accent else Color(0xFF444444), fieldShape),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TvTextField(
                        value = s.input,
                        onValueChange = vm::onInput,
                        placeholder = UrlUtil.DEFAULT_BASE_URL,
                        textSize = TvSettingsSpec.INPUT_TEXT,
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Done,
                        onImeAction = {
                            // Klavyeyi kapat, odağı aşağıdaki düğmelere taşı (alanda takılı kalmasın).
                            keyboard?.hide()
                            saveFocus.tryFocus()
                        },
                        onFocusChange = { inputFocused = it },
                        modifier = Modifier.weight(1f).padding(horizontal = dims.dp(22)),
                    )
                    TvSetButton(
                        "Bağlantıyı test et",
                        onClick = { vm.test() },
                        variant = SetButtonVariant.Inline,
                        enabled = !s.busy,
                        modifier = Modifier.padding(end = dims.dp(TvSettingsSpec.BTN_INLINE_MR)),
                    )
                }
                Spacer(Modifier.width(dims.dp(TvSettingsSpec.FIELD_MR)))
                TvSetButton("Kaydet", onClick = { vm.save() }, primary = true, focusRequester = saveFocus)
            }
            // durum satırı
            Box(Modifier.heightIn(min = dims.dp(TvSettingsSpec.STATUS_MIN_H)).padding(top = dims.dp(10))) {
                val status = s.status
                if (status != null) TvText(status, size = TvSettingsSpec.STATUS, color = TvColors.Accent, maxLines = 2)
            }
        }

        // ---- tercih kartları: yan yana, dikey radyo listeleri
        Row(Modifier.fillMaxWidth().padding(top = dims.dp(TvSettingsSpec.COLS_MT)), horizontalArrangement = Arrangement.spacedBy(dims.dp(TvSettingsSpec.COLS_GAP))) {
            SetCard(Modifier.weight(1f)) {
                TvText(TvSettingsSpec.SUB_TITLE, size = TvSettingsSpec.CARD_TITLE, weight = FontWeight.Bold)
                if (s.hasProfile) {
                    HintLine("")
                    PlayPrefs.SUB_OPTIONS.forEach { (value, label) ->
                        TvSetOption(label, selected = s.subPref == value, onClick = { vm.setSubPref(value) })
                    }
                } else {
                    TvText(
                        TvSettingsSpec.NEED_PROFILE,
                        size = 22f,
                        color = Color(0xFF8A8A8A),
                        modifier = Modifier.padding(top = dims.dp(14)),
                    )
                }
            }
            SetCard(Modifier.weight(1f)) {
                TvText(TvSettingsSpec.QUALITY_TITLE, size = TvSettingsSpec.CARD_TITLE, weight = FontWeight.Bold)
                HintLine(TvSettingsSpec.QUALITY_HINT)
                PlayPrefs.QUALITY_OPTIONS.forEach { (value, label) ->
                    TvSetOption(label, selected = s.qualityPref == value, onClick = { vm.setQualityPref(value) })
                }
            }
        }

        // ---- alt: önbelleği temizle + soluk bilgi satırları
        Row(Modifier.padding(top = dims.dp(TvSettingsSpec.FOOT_MT))) {
            TvSetButton("Önbelleği temizle", onClick = {
                vm.clearCache()
                toaster?.show("Önbellek temizlendi")
            })
        }
        Column(Modifier.padding(top = dims.dp(TvSettingsSpec.INFO_MT), bottom = dims.dp(24))) {
            InfoLine(TvSettingsLogic.buildInfo(versionName))
            InfoLine(TvSettingsLogic.addressInfo(s.input.ifBlank { UrlUtil.DEFAULT_BASE_URL }, UrlUtil.DEFAULT_BASE_URL))
            InfoLine(s.serverInfo)
        }
    }
}

@Composable
private fun InfoLine(text: String) {
    TvText(text, size = TvSettingsSpec.INFO, color = Color(0xFF7D7D7D), line = TvSettingsSpec.INFO_LINE, maxLines = 2)
}

@Composable
private fun HintLine(text: String) {
    val dims = LocalTvDims.current
    Box(Modifier.heightIn(min = dims.dp(TvSettingsSpec.HINT_MIN_H)).padding(top = dims.dp(2), bottom = dims.dp(12))) {
        if (text.isNotEmpty()) TvText(text, size = TvSettingsSpec.HINT, color = Color(0xFF8A8A8A), maxLines = 2)
    }
}

/** `.set-card`: koyu yüzey, 2 px #303030 kenar, 20 px köşe, 24/28 dolgu. */
@Composable
private fun SetCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val dims = LocalTvDims.current
    val shape = RoundedCornerShape(dims.dp(TvSettingsSpec.CARD_RADIUS))
    Column(
        modifier
            .fillMaxWidth()
            .background(TvColors.Surface, shape)
            .border(dims.dp(TvSettingsSpec.CARD_BORDER), TvColors.Divider, shape)
            .padding(horizontal = dims.dp(TvSettingsSpec.CARD_PAD_H), vertical = dims.dp(TvSettingsSpec.CARD_PAD_V)),
    ) { content() }
}

private enum class SetButtonVariant { Normal, Top, Inline }

/**
 * `.set-btn`: yarı saydam beyaz dolgu; `primary` sarı; odakta sarı dolgu + koyu yazı + sarı kenar + %105 büyüme (primary
 * odakta beyaz kenar). `Top` 52 px (Profil değiştir), `Inline` 52 px/10 px köşe (alan içi test düğmesi).
 */
@Composable
private fun TvSetButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    variant: SetButtonVariant = SetButtonVariant.Normal,
    primary: Boolean = false,
    enabled: Boolean = true,
    focusRequester: FocusRequester? = null,
) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) TvSettingsSpec.FOCUS_SCALE else 1f, tween(200, easing = TvEase), label = "tvSetBtnScale")
    val (height, text, padH, radius) = when (variant) {
        SetButtonVariant.Normal -> listOf(TvSettingsSpec.BTN_H, TvSettingsSpec.BTN_TEXT, TvSettingsSpec.BTN_PAD_H, TvSettingsSpec.BTN_RADIUS)
        SetButtonVariant.Top -> listOf(TvSettingsSpec.BTN_TOP_H, TvSettingsSpec.BTN_TOP_TEXT, TvSettingsSpec.BTN_TOP_PAD_H, TvSettingsSpec.BTN_RADIUS)
        SetButtonVariant.Inline -> listOf(TvSettingsSpec.BTN_INLINE_H, TvSettingsSpec.BTN_TOP_TEXT, TvSettingsSpec.BTN_INLINE_PAD_H, TvSettingsSpec.BTN_INLINE_RADIUS)
    }
    val shape = RoundedCornerShape(dims.dp(radius))
    val fill = when {
        focused -> TvColors.Accent
        primary -> TvColors.Accent
        else -> Color(0x1AFFFFFF)
    }
    val borderColor = when {
        focused && primary -> Color.White
        focused -> TvColors.Accent
        else -> Color.Transparent
    }
    Box(
        modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { focused = it.isFocused }
            .height(dims.dp(height))
            .background(fill, shape)
            .border(dims.dp(TvSettingsSpec.BTN_BORDER), borderColor, shape)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, enabled = true) { if (enabled) onClick() }
            .padding(horizontal = dims.dp(padH - TvSettingsSpec.BTN_BORDER)),
        contentAlignment = Alignment.Center,
    ) {
        TvText(
            label,
            size = text,
            color = if (focused || primary) TvColors.Ink else if (enabled) Color.White else Color(0xFF8A8A8A),
            weight = FontWeight.Bold,
        )
    }
}

/**
 * `.opt`: 68 px satır, 28 px yazı; işaret = 28 px halka (seçili: sarı halka + sarı nokta); seçili = hafif sarı zemin/kenar +
 * kalın yazı (odaktan bağımsız); odaklı = sarı halka çerçeve + #353535 zemin.
 */
@Composable
private fun TvSetOption(label: String, selected: Boolean, onClick: () -> Unit) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(dims.dp(TvSettingsSpec.OPT_RADIUS))
    val fill = when {
        selected && focused -> Color(0x2EF5C518)
        focused -> Color(0xFF353535)
        selected -> Color(0x1AF5C518)
        else -> Color(0x0AFFFFFF)
    }
    val borderColor = when {
        focused -> TvColors.Accent
        selected -> Color(0x73F5C518)
        else -> Color.Transparent
    }
    Row(
        Modifier
            .padding(bottom = dims.dp(TvSettingsSpec.OPT_MB))
            .fillMaxWidth()
            .height(dims.dp(TvSettingsSpec.OPT_H))
            .onFocusChanged { focused = it.isFocused }
            .background(fill, shape)
            .border(dims.dp(TvSettingsSpec.OPT_BORDER), borderColor, shape)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick)
            .padding(horizontal = dims.dp(TvSettingsSpec.OPT_PAD_H - TvSettingsSpec.OPT_BORDER)),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(dims.dp(TvSettingsSpec.MARK))
                .border(dims.dp(TvSettingsSpec.MARK_BORDER), if (selected) TvColors.Accent else Color(0xFF777777), CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            if (selected) Box(Modifier.size(dims.dp(TvSettingsSpec.MARK_DOT)).background(TvColors.Accent, CircleShape))
        }
        Spacer(Modifier.width(dims.dp(TvSettingsSpec.MARK_MR)))
        TvText(
            label,
            size = TvSettingsSpec.OPT_TEXT,
            color = if (selected || focused) Color.White else Color(0xFFD6D6D6),
            weight = if (selected) FontWeight.Bold else FontWeight.Normal,
        )
    }
}
