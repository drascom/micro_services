package com.diziflix.app.ui.tv

import androidx.compose.animation.core.CubicBezierEasing
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.diziflix.app.domain.PlayerKeys
import com.diziflix.app.domain.TvBarItem
import com.diziflix.app.domain.TvBrandSpec
import com.diziflix.app.domain.TvBarReturn
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/*
 * Android TV ortak bileşenleri — Tizen istemcisinin görünümü (base.css .btn / .tb-item / .wordmark / .top-search /
 * .modal). Material düğmeleri/hapları YOK: yarıçap 4-5 px, kare köşeli, odakta sarı dolgu + hafif büyüme. Aşama B ekranları
 * (detay, ayarlar, profil, arama, oynatıcı) aynı bileşenlerle kurulur.
 */

/** base.css `--ease`. */
val TvEase = CubicBezierEasing(0.2f, 0.6f, 0.2f, 1f)

private const val SWALLOW_MAX_MS = 2_000L

// ------------------------------------------------------------------------------------------ düğme (.btn)

/**
 * Tizen `.btn`: 64 px yükseklik, en az 200 genişlik, 4 px köşe, yarı saydam gri dolgu; `primary` beyaz; odakta sarı dolgu +
 * koyu yazı + %108 büyüme; [enabled]=false gri (odaklanabilir ama eylemsiz). [small]: 52 px.
 */
@Composable
fun TvButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    primary: Boolean = false,
    small: Boolean = false,
    enabled: Boolean = true,
    focusRequester: FocusRequester? = null,
) {
    var focused by remember { mutableStateOf(false) }
    TvButtonFace(
        label = label,
        focused = focused,
        primary = primary,
        small = small,
        enabled = enabled,
        modifier = modifier
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { focused = it.isFocused }
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick),
    )
}

/**
 * [TvButton]'un yalnızca görünümü (odak dışarıdan verilir): sanal odaklı ekranlar (detay eylem düğmeleri) ve
 * [TvButton] kullanır. [modifier] zincirin EN BAŞINA gelir (boyut/etkileşim çağırandan).
 */
@Composable
fun TvButtonFace(
    label: String,
    focused: Boolean,
    modifier: Modifier = Modifier,
    primary: Boolean = false,
    small: Boolean = false,
    enabled: Boolean = true,
) {
    val dims = LocalTvDims.current
    val scale = animateFloatAsState(if (focused) 1.08f else 1f, tween(200, easing = TvEase), label = "tvBtnScale")
    val shape = RoundedCornerShape(dims.dp(4))
    val fill = when {
        !enabled && focused -> Color(0xFF3C3C3C)
        focused -> TvColors.Accent
        !enabled -> TvColors.ButtonDisabled
        primary -> Color.White
        else -> TvColors.ButtonFill
    }
    val ink = when {
        !enabled && focused -> Color(0xFFD0D0D0)
        !enabled -> Color(0xFF9A9A9A)
        focused || primary -> TvColors.Ink
        else -> Color.White
    }
    Box(
        modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .height(dims.dp(if (small) 52 else 64))
            .widthIn(min = dims.dp(if (small) 0 else 200))
            .background(fill, shape)
            .border(dims.dp(4), if (focused) TvColors.Accent else Color.Transparent, shape)
            .padding(horizontal = dims.dp(if (small) 22 else 32)),
        contentAlignment = Alignment.Center,
    ) {
        TvText(label, size = if (small) 22f else 24f, color = ink, weight = FontWeight.Bold, align = TextAlign.Center)
    }
}

// ------------------------------------------------------------------------------------------ görsel

/**
 * Coil görseli. [widthPx]x[heightPx] Coil hedef boyutudur (decode bu boyuta iner; bellek). [rgb565]: afiş/still
 * (yarım bellek); hero/artalanda kapalı (degrade bantlanmasın). Adres boşsa hiçbir şey çizmez; yükleme başarısızsa [onFailed].
 */
@Composable
fun TvImage(
    url: String,
    widthPx: Int,
    heightPx: Int,
    modifier: Modifier = Modifier,
    rgb565: Boolean = false,
    alignment: Alignment = Alignment.Center,
    fadeMs: Int = 0,
    onFailed: () -> Unit = {},
) {
    if (url.isEmpty()) return
    val context = LocalContext.current
    val request = remember(url, widthPx, heightPx, rgb565, fadeMs) {
        ImageRequest.Builder(context)
            .data(url)
            .size(widthPx.coerceAtLeast(1), heightPx.coerceAtLeast(1))
            .allowRgb565(rgb565)
            .crossfade(fadeMs)
            .build()
    }
    AsyncImage(
        model = request,
        contentDescription = null,
        contentScale = ContentScale.Crop,
        alignment = alignment,
        onError = { onFailed() },
        modifier = modifier,
    )
}

// ------------------------------------------------------------------------------------------ üst menü

/** Üst menüde seçili öğe (yoksa null). */
enum class TvTopBarTab { Search, MyList }

/**
 * Üst menü öğelerinin odak hedefleri + "öğeyle ayrılıp dönünce o öğeye odak" belleği ([TvBarReturn]). [entry] (arama kutusu)
 * içerikten Yukarı ile girilen ilk öğedir. Gezinme grafiğinde bir kez kurulur; ekranlar yalnızca [requester]/[returns] kullanır.
 */
class TvBarFocus {
    private val requesters = TvBarItem.entries.associateWith { FocusRequester() }
    val returns = TvBarReturn()

    fun requester(item: TvBarItem): FocusRequester = requesters.getValue(item)

    val entry: FocusRequester get() = requester(TvBarItem.Search)
}

/**
 * Tizen üst menüsü (navigation.js + base.css `.topbar.main-navigation`): sol `DIZIFLIX`, arama kutusu, "Listem", boşluk,
 * "Profil", "Ayarlar". "Ana Sayfa" öğesi YOK. 120 px yükseklik, #141414 zemin, altta 1 px #303030 çizgi. Tüm öğeler
 * kumandayla odaklanır (odakta sarı dolgu); [searchFocus] arama kutusuna bağlıdır (içerikten Yukarı ile girilen ilk öğe).
 */
@Composable
fun TvTopBar(
    selected: TvTopBarTab?,
    onHome: () -> Unit,
    onSearch: () -> Unit,
    onMyList: () -> Unit,
    onProfile: () -> Unit,
    onSettings: () -> Unit,
    searchFocus: FocusRequester,
    modifier: Modifier = Modifier,
    /** Arama ekranında: kutunun yerine GERÇEK yazı alanı (Tizen `.top-search` girdisi); null = tıklanınca arama ekranını açan kutu. */
    searchSlot: (@Composable () -> Unit)? = null,
    /** Öğe başına odak hedefi (ekrandan dönüşte son kullanılan öğeye odak için); null = hedefsiz. */
    itemFocus: ((TvBarItem) -> FocusRequester)? = null,
) {
    val dims = LocalTvDims.current
    val line = dims.px(1f).coerceAtLeast(1f)
    Row(
        modifier
            .fillMaxWidth()
            .height(dims.dp(120))
            .background(TvColors.Background)
            .drawBehind {
                drawRect(TvColors.Divider, topLeft = Offset(0f, size.height - line), size = Size(size.width, line))
            }
            // wordmark 14 px iç boşluklu (odak kutusu); metin yine 60 px'te başlar
            .padding(start = dims.dp(46), end = dims.dp(60)),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TvWordmark(onClick = onHome, focusRequester = itemFocus?.invoke(TvBarItem.Wordmark))
        Spacer(Modifier.width(dims.dp(24)))
        if (searchSlot != null) searchSlot() else TvSearchBox(selected = selected == TvTopBarTab.Search, onClick = onSearch, focusRequester = searchFocus)
        Spacer(Modifier.width(dims.dp(4)))
        TvTopBarItem("Listem", selected = selected == TvTopBarTab.MyList, onClick = onMyList, focusRequester = itemFocus?.invoke(TvBarItem.MyList))
        Spacer(Modifier.weight(1f))
        TvTopBarItem("Profil", selected = false, onClick = onProfile, focusRequester = itemFocus?.invoke(TvBarItem.Profile))
        Spacer(Modifier.width(dims.dp(4)))
        TvTopBarItem("Ayarlar", selected = false, onClick = onSettings, focusRequester = itemFocus?.invoke(TvBarItem.Settings))
    }
}

@Composable
private fun TvWordmark(onClick: () -> Unit, focusRequester: FocusRequester? = null) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) 1.06f else 1f, tween(200, easing = TvEase), label = "tvWordmarkScale")
    Box(
        Modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { focused = it.isFocused }
            .background(if (focused) TvColors.Accent else Color.Transparent, RoundedCornerShape(dims.dp(4)))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick)
            .padding(horizontal = dims.dp(14), vertical = dims.dp(6)),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            // küçük karakter amblemi (Tizen `.brand-emblem`): yazının solunda, dekoratif; odak/yükseklik/sıra değişmez (tek odak hedefi = wordmark kutusu)
            TvEmblem()
            Spacer(Modifier.width(dims.dp(TvBrandSpec.EMBLEM_MR)))
            // .wordmark: 900 ağırlık, geniş harf aralığı, büyük harf, sarı (main-navigation: 30 px)
            TvText(
                "DIZIFLIX",
                size = 30f,
                color = if (focused) TvColors.Ink else TvColors.Accent,
                weight = FontWeight.Black,
                letterSpacingEm = 0.22f,
            )
        }
    }
}

/** `.top-search`: 510x54, 3 px #555 çerçeve, 5 px köşe; odakta sarı çerçeve + %103,5 büyüme. Tamam = arama ekranı. */
@Composable
private fun TvSearchBox(selected: Boolean, onClick: () -> Unit, focusRequester: FocusRequester) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) 1.035f else 1f, tween(150, easing = TvEase), label = "tvSearchScale")
    val shape = RoundedCornerShape(dims.dp(5))
    val lit = focused || selected
    Row(
        Modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .focusRequester(focusRequester)
            .onFocusChanged { focused = it.isFocused }
            .size(dims.dp(510), dims.dp(54))
            .background(if (lit) Color(0xFF202020) else Color(0xF01C1C1C), shape)
            .border(dims.dp(3), if (lit) TvColors.Accent else Color(0xFF555555), shape)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(dims.dp(48)), contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.Search, contentDescription = null, tint = Color(0xFFBBBBBB), modifier = Modifier.size(dims.dp(32)))
        }
        TvText("Film veya dizi ara", size = 23f, color = Color(0xFFAAAAAA), weight = FontWeight.SemiBold)
    }
}

/**
 * Arama ekranındaki üst menü arama girdisi (`.top-search` + `.top-search-input` + `.top-search-clear`): 510x54, 3 px çerçeve
 * (odakta/seçiliyken sarı), ⌕ simgesi, yazı alanı ve doluyken odaklanabilir ✕ düğmesi. Odakta %103,5 büyür.
 */
@Composable
fun TvSearchInputBox(
    value: String,
    onValueChange: (String) -> Unit,
    onSubmit: () -> Unit,
    onClear: () -> Unit,
    focusRequester: FocusRequester,
    onFocusChange: (Boolean) -> Unit = {},
) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) 1.035f else 1f, tween(150, easing = TvEase), label = "tvSearchInputScale")
    val shape = RoundedCornerShape(dims.dp(5))
    Row(
        Modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .size(dims.dp(510), dims.dp(54))
            .background(Color(0xFF202020), shape)
            .border(dims.dp(3), TvColors.Accent, shape),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(dims.dp(48)), contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.Search, contentDescription = null, tint = Color(0xFFBBBBBB), modifier = Modifier.size(dims.dp(32)))
        }
        TvTextField(
            value = value,
            onValueChange = onValueChange,
            placeholder = "Film veya dizi ara",
            placeholderColor = Color(0xFFAAAAAA),
            textSize = 23f,
            weight = FontWeight.SemiBold,
            imeAction = ImeAction.Search,
            onImeAction = onSubmit,
            focusRequester = focusRequester,
            onFocusChange = {
                focused = it
                onFocusChange(it)
            },
            modifier = Modifier.weight(1f).padding(end = dims.dp(16)),
        )
        if (value.isNotEmpty()) TvClearButton(onClick = onClear)
    }
}

/** `.top-search-clear`: 40x40 "✕"; odakta sarı dolgu + koyu yazı. */
@Composable
private fun TvClearButton(onClick: () -> Unit) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(dims.dp(4))
    Box(
        Modifier
            .padding(end = dims.dp(6))
            .size(dims.dp(40))
            .onFocusChanged { focused = it.isFocused }
            .background(if (focused) TvColors.Accent else Color.Transparent, shape)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        TvText("✕", size = 22f, color = if (focused) TvColors.Ink else Color(0xFFBBBBBB), align = TextAlign.Center)
    }
}

/** `.tb-item`: 24 px dim yazı, 4 px köşe; seçili = sarı yazı + alt çizgi; odakta sarı dolgu + koyu yazı + %106 büyüme. */
@Composable
private fun TvTopBarItem(label: String, selected: Boolean, onClick: () -> Unit, focusRequester: FocusRequester? = null) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val scale = animateFloatAsState(if (focused) 1.06f else 1f, tween(200, easing = TvEase), label = "tvItemScale")
    val shape = RoundedCornerShape(dims.dp(4))
    val underline = dims.px(3f)
    Box(
        Modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { focused = it.isFocused }
            .background(if (focused) TvColors.Accent else Color.Transparent, shape)
            .drawBehind {
                if (selected && !focused) {
                    drawRect(TvColors.Accent, topLeft = Offset(0f, size.height - underline), size = Size(size.width, underline))
                }
            }
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick)
            // 3 px şeffaf çerçeve + 10/16 iç boşluk
            .padding(horizontal = dims.dp(19), vertical = dims.dp(13)),
    ) {
        TvText(
            label,
            size = 24f,
            color = when {
                focused -> TvColors.Ink
                selected -> TvColors.Accent
                else -> TvColors.Dim
            },
            weight = FontWeight.Normal,
        )
    }
}

// ------------------------------------------------------------------------------------------ pencere (.modal)

/** Pencere düğmesi. */
class TvModalButton(val label: String, val primary: Boolean = false, val onClick: () -> Unit)

/**
 * Tizen `.modal`: koyu perde (%75), ortada 900 genişlik #1f1f1f panel (6 px köşe, 48/56 iç boşluk), ortalı başlık (36) +
 * açıklama (24, dim) + `.btn` düğmeleri (aralarında 20). Açılışta ilk düğmeye odaklanır; Geri kapatır. [swallowHeldKey]:
 * pencere Tamam basılıyken (uzun basış) açıldıysa tutulan tuşun tekrarları/bırakılması yutulur (ilk eylem yanlışlıkla
 * tetiklenmesin; en çok 2 sn).
 */
@Composable
fun TvModal(
    title: String,
    buttons: List<TvModalButton>,
    onDismiss: () -> Unit,
    message: String? = null,
    swallowHeldKey: Boolean = false,
    /** `.modal-full`: neredeyse opak koyu perde (oynatıcı hata penceresi). */
    fullscreen: Boolean = false,
) {
    val dims = LocalTvDims.current
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        val first = remember { FocusRequester() }
        var swallowing by remember { mutableStateOf(swallowHeldKey) }
        val scope = rememberCoroutineScope()
        LaunchedEffect(Unit) { first.requestFocusWhenReady() }
        LaunchedEffect(swallowHeldKey) {
            if (swallowHeldKey) {
                delay(SWALLOW_MAX_MS)
                swallowing = false
            }
        }
        Box(
            Modifier
                .fillMaxSize()
                .background(if (fullscreen) Color(0xF50A0A0A) else Color(0xBF000000))
                // Güvence: odak boşa düşerse ilk düğmeye geri verilir (düğmesiz kalan pencere olmaz; Geri zaten pencere kapatır).
                .onFocusChanged { if (!it.hasFocus) scope.launch { first.requestFocusWhenReady() } }
                .onPreviewKeyEvent { event ->
                    if (swallowing && PlayerKeys.isSelect(event.nativeKeyEvent.keyCode)) {
                        if (event.type == KeyEventType.KeyUp) swallowing = false
                        true
                    } else {
                        false
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            Column(
                Modifier
                    .width(dims.dp(900))
                    .background(TvColors.Surface, RoundedCornerShape(dims.dp(6)))
                    .padding(horizontal = dims.dp(56), vertical = dims.dp(48)),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                TvText(title, size = 36f, weight = FontWeight.Bold, line = 44f, maxLines = 2, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
                if (!message.isNullOrBlank()) {
                    Spacer(Modifier.height(dims.dp(18)))
                    TvText(message, size = 24f, color = TvColors.Dim, line = 32f, maxLines = 3, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
                }
                Spacer(Modifier.height(dims.dp(36)))
                Row(horizontalArrangement = Arrangement.spacedBy(dims.dp(20)), modifier = Modifier.fillMaxWidth()) {
                    Spacer(Modifier.weight(1f))
                    buttons.forEachIndexed { index, button ->
                        TvButton(
                            label = button.label,
                            onClick = button.onClick,
                            primary = button.primary,
                            focusRequester = if (index == 0) first else null,
                        )
                    }
                    Spacer(Modifier.weight(1f))
                }
            }
        }
    }
}
