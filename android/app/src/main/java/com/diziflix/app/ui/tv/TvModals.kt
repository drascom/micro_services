package com.diziflix.app.ui.tv

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
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
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.diziflix.app.domain.PlayerKeys
import com.diziflix.app.domain.TvBrandSpec
import com.diziflix.app.domain.TvPlayerSpec

/*
 * Android TV pencereleri ve tam ekran durumları (Tizen `ui/modal.js` + `css/base.css` .modal/.errscreen/.loading-*).
 */

/**
 * `DZ.modal.text`: kaydırılabilir tam-metin penceresi. 1200x760 panel, 40 px başlık, 28 px gövde (satır 1,5); Yukarı/Aşağı
 * gövdeyi 60 px kaydırır (sınırda durur); Geri/Tamam kapatır.
 */
@Composable
fun TvTextModal(title: String, body: String, onClose: () -> Unit) {
    val dims = LocalTvDims.current
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        val focus = remember { FocusRequester() }
        var y by remember { mutableFloatStateOf(0f) }
        var viewportPx by remember { mutableIntStateOf(0) }
        var contentPx by remember { mutableIntStateOf(0) }
        val step = dims.px(60f)
        LaunchedEffect(Unit) { focus.requestFocusWhenReady() }
        Box(
            Modifier
                .fillMaxSize()
                .background(Color(0xBF000000))
                .focusRequester(focus)
                .onPreviewKeyEvent { event ->
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    val max = (contentPx - viewportPx).coerceAtLeast(0).toFloat()
                    when {
                        event.key == Key.DirectionUp -> { y = (y - step).coerceIn(0f, max); true }
                        event.key == Key.DirectionDown -> { y = (y + step).coerceIn(0f, max); true }
                        PlayerKeys.isSelect(event.nativeKeyEvent.keyCode) -> { onClose(); true }
                        else -> false
                    }
                }
                .focusable(),
            contentAlignment = Alignment.Center,
        ) {
            Column(
                Modifier
                    .width(dims.dp(1200))
                    .height(dims.dp(760))
                    .background(TvColors.Surface, RoundedCornerShape(dims.dp(6)))
                    .padding(horizontal = dims.dp(56), vertical = dims.dp(48)),
            ) {
                TvText(title, size = 40f, weight = FontWeight.Bold, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(dims.dp(24)))
                Box(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .clipToBounds()
                        .onSizeChanged { viewportPx = it.height },
                ) {
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .wrapContentHeight(Alignment.Top, unbounded = true)
                            .onSizeChanged { contentPx = it.height }
                            .graphicsLayer { translationY = -y },
                    ) {
                        TvText(
                            body,
                            size = 28f,
                            color = TvColors.Overview,
                            line = 42f,
                            maxLines = Int.MAX_VALUE,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }
        }
    }
}

/**
 * `DZ.modal.open({vertical:true})`: dikey düğmeli seçim penceresi (filtre seçimi). Seçili seçenek beyaz (primary) gelir
 * ve açılışta odaklıdır; Yukarı/Aşağı gezinir; Geri kapatır.
 */
@Composable
fun TvPickModal(
    title: String,
    options: List<Pair<String, String>>,
    selectedId: String?,
    onPick: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val dims = LocalTvDims.current
    val entry = remember(options, selectedId) { options.indexOfFirst { it.first == selectedId }.coerceAtLeast(0) }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        val listState = rememberLazyListState()
        val entryRequester = remember { FocusRequester() }
        LaunchedEffect(Unit) {
            listState.scrollToItem((entry - 2).coerceAtLeast(0))
            entryRequester.requestFocusWhenReady()
        }
        Box(Modifier.fillMaxSize().background(Color(0xBF000000)), contentAlignment = Alignment.Center) {
            Column(
                Modifier
                    .width(dims.dp(900))
                    .background(TvColors.Surface, RoundedCornerShape(dims.dp(6)))
                    .padding(horizontal = dims.dp(56), vertical = dims.dp(48)),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                TvText(title, size = 36f, weight = FontWeight.Bold, line = 44f, align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(dims.dp(36)))
                LazyColumn(
                    state = listState,
                    modifier = Modifier.heightIn(max = dims.dp(560)).fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(dims.dp(10)),
                ) {
                    itemsIndexed(options, key = { index, _ -> index }) { index, (id, name) ->
                        TvButton(
                            label = name,
                            onClick = { onPick(id) },
                            primary = id == selectedId,
                            focusRequester = if (index == entry) entryRequester else null,
                            modifier = Modifier.fillMaxWidth().padding(horizontal = dims.dp(10)),
                        )
                    }
                }
            }
        }
    }
}

/** Tizen `.errscreen`: büyük wordmark + başlık + açıklama + düğmeler (ilk düğme vurgulu, açılışta odaklı). */
@Composable
fun TvErrorScreen(
    title: String,
    message: String,
    actions: List<Pair<String, () -> Unit>>,
    modifier: Modifier = Modifier,
) {
    val dims = LocalTvDims.current
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { if (actions.isNotEmpty()) first.requestFocusWhenReady() }
    Column(
        modifier.fillMaxSize().background(TvColors.Background),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        TvMascotError()
        Spacer(Modifier.height(dims.dp(TvBrandSpec.MASCOT_ERR_MB)))
        TvText("DIZIFLIX", size = 56f, color = TvColors.Accent, weight = FontWeight.Black, letterSpacingEm = 0.26f)
        Spacer(Modifier.height(dims.dp(40)))
        TvText(title, size = 48f, weight = FontWeight.Bold, align = TextAlign.Center)
        Spacer(Modifier.height(dims.dp(18)))
        TvText(
            message,
            size = 24f,
            color = TvColors.Dim,
            line = 32f,
            maxLines = 3,
            align = TextAlign.Center,
            modifier = Modifier.width(dims.dp(1100)),
        )
        Spacer(Modifier.height(dims.dp(40)))
        Row(horizontalArrangement = Arrangement.spacedBy(dims.dp(20))) {
            actions.forEachIndexed { index, (label, onClick) ->
                TvButton(label, onClick, primary = index == 0, focusRequester = if (index == 0) first else null)
            }
        }
    }
}

/** `.loading-spinner`: 96 px halka (10 px, %16 beyaz) + dönen üst dilim (vurgu rengi), 0,9 sn/tur. */
@Composable
fun TvSpinner(sizeTizen: Float, borderTizen: Float, modifier: Modifier = Modifier, periodMs: Int = 900) {
    val dims = LocalTvDims.current
    val transition = rememberInfiniteTransition(label = "tvSpinner")
    val angle = transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(periodMs, easing = LinearEasing), RepeatMode.Restart),
        label = "tvSpinnerAngle",
    )
    Canvas(modifier.size(dims.dp(sizeTizen))) {
        val stroke = dims.px(borderTizen)
        val inset = stroke / 2f
        val arcSize = Size(size.width - stroke, size.height - stroke)
        drawArc(
            color = Color(0x29FFFFFF),
            startAngle = 0f,
            sweepAngle = 360f,
            useCenter = false,
            topLeft = Offset(inset, inset),
            size = arcSize,
            style = Stroke(width = stroke),
        )
        // border-top-color: üst kenarın 90 derecelik dilimi (-135..-45), tüm halka döner
        drawArc(
            color = TvColors.Accent,
            startAngle = -135f + angle.value,
            sweepAngle = 90f,
            useCenter = false,
            topLeft = Offset(inset, inset),
            size = arcSize,
            style = Stroke(width = stroke, cap = StrokeCap.Butt),
        )
    }
}

/**
 * Tam ekran yükleme (`DZ.modal.loading`): neredeyse opak koyu zemin, ortada 96 px dönen halka + dönen Türk atasözü
 * (40 px/700, yumuşak geçiş) + küçük aşama satırı. Odaklanabilir öğe yok (Geri iptal eder).
 */
@Composable
fun TvLoadingPane(proverb: String, stage: String, modifier: Modifier = Modifier) {
    val dims = LocalTvDims.current
    Box(modifier.fillMaxSize().background(Color(0xF50A0A0A)), contentAlignment = Alignment.Center) {
        Column(Modifier.width(dims.dp(TvPlayerSpec.LOAD_BOX_W)), horizontalAlignment = Alignment.CenterHorizontally) {
            TvMascotLoading()
            Spacer(Modifier.height(dims.dp(TvBrandSpec.MASCOT_LOAD_MB)))
            TvSpinner(TvPlayerSpec.LOAD_SPINNER, TvPlayerSpec.LOAD_SPINNER_BORDER)
            Spacer(Modifier.height(dims.dp(TvPlayerSpec.LOAD_SPINNER_MB)))
            Box(Modifier.height(dims.dp(TvPlayerSpec.LOAD_PROVERB_MIN_H)).fillMaxWidth(), contentAlignment = Alignment.TopCenter) {
                Crossfade(targetState = proverb, animationSpec = tween(400), label = "tvProverb") { text ->
                    TvText(
                        text,
                        size = TvPlayerSpec.LOAD_PROVERB,
                        color = Color(0xFFF0F0F0),
                        weight = FontWeight.Bold,
                        line = 56f,
                        maxLines = 2,
                        align = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
            Spacer(Modifier.height(dims.dp(TvPlayerSpec.LOAD_STAGE_MT)))
            Box(Modifier.height(dims.dp(TvPlayerSpec.LOAD_STAGE_MIN_H)).fillMaxWidth(), contentAlignment = Alignment.TopCenter) {
                TvText(stage, size = TvPlayerSpec.LOAD_STAGE, color = TvColors.Dim, align = TextAlign.Center)
            }
        }
    }
}

/** `.offline-note`: sol altta kalıcı çevrimdışı/önbellek etiketi (odaklanmaz). */
@Composable
fun TvOfflineChip(label: String, modifier: Modifier = Modifier) {
    val dims = LocalTvDims.current
    TvText(
        label,
        size = 22f,
        color = Color(0xFFF0D98A),
        weight = FontWeight.Bold,
        modifier = modifier
            .background(Color(0xFF5A4A12), RoundedCornerShape(dims.dp(6)))
            .padding(horizontal = dims.dp(20), vertical = dims.dp(8)),
    )
}
