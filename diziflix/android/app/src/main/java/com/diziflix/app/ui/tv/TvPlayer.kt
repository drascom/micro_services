package com.diziflix.app.ui.tv

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.VideoStream
import com.diziflix.app.domain.Format
import com.diziflix.app.domain.SeekAccelerator
import com.diziflix.app.domain.TvBackTarget
import com.diziflix.app.domain.TvOverlay
import com.diziflix.app.domain.TvOverlayContext
import com.diziflix.app.domain.TvOverlayEffect
import com.diziflix.app.domain.TvOverlayModel
import com.diziflix.app.domain.TvPanelItem
import com.diziflix.app.domain.TvPlayerAction
import com.diziflix.app.domain.TvPlayerBack
import com.diziflix.app.domain.TvPlayerKeys
import com.diziflix.app.domain.TvPlayerLogic
import com.diziflix.app.domain.TvPlayerOverlayController
import com.diziflix.app.domain.TvPlayerSpec
import com.diziflix.app.domain.TvSourceMenu
import com.diziflix.app.domain.TvTracksPanel
import com.diziflix.app.domain.TvTracksPanelFocus
import com.diziflix.app.domain.TvTracksPanelState
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.player.EmbedWebView
import com.diziflix.app.ui.player.ImmersiveLandscapeEffect
import com.diziflix.app.ui.player.OnPauseEffect
import com.diziflix.app.ui.player.PlayerPhase
import com.diziflix.app.ui.player.PlayerUiState
import com.diziflix.app.ui.player.PlayerViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/*
 * Android TV OYNATICI ARAYÜZÜ — Tizen `css/player.css` + `js/screens/player.js` + `ui/tracks_panel.js` + `ui/modal.js`
 * (yükleme) karşılığı. Tablet `PlayerScreen`'inden AYRI; ViewModel/oynatma mantığı (PlayFlow, ExoPlayer, konum kaydı)
 * ORTAK. Kumanda: Tamam = oynat/duraklat, Sol/Sağ = ±10 sn (basılı tutunca hızlanır), Yukarı = "Kaynak / kalite" menüsü,
 * Aşağı/Sarı = "Ses ve Altyazılar" paneli, Geri = önce panel/menü/öneri/kontroller, sonra çıkış. Kontroller 3 sn sonra gizlenir.
 */
@Composable
fun TvPlayerScreen(
    itemId: String,
    episodeId: String?,
    kind: String,
    profileId: String,
    onBack: () -> Unit,
) {
    val vm = containerViewModel(key = "player:$itemId:${episodeId ?: ""}:$kind") {
        PlayerViewModel(it, profileId, itemId, episodeId, kind)
    }
    val state by vm.state.collectAsStateWithLifecycle()

    ImmersiveLandscapeEffect()
    OnPauseEffect { vm.onHostPaused() }
    val leave = {
        vm.leave()
        onBack()
    }
    BackHandler { leave() }
    LaunchedEffect(vm) { vm.exit.collect { onBack() } }

    val s = state
    val phase = s.phase
    TvTheme {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            if (phase is PlayerPhase.Embed) {
                TvEmbedPane(stream = phase.stream, onBack = leave, onReportBroken = { vm.reportEmbedBroken() })
            } else {
                TvVideoSurface(vm.player)
                when (phase) {
                    is PlayerPhase.Playing -> TvPlayerControls(s = s, vm = vm)
                    is PlayerPhase.Ended -> TvEndedLayer(s = s, vm = vm)
                    is PlayerPhase.Loading -> TvLoadingPane(proverb = s.proverb, stage = s.stage)
                    is PlayerPhase.Error -> TvModal(
                        title = "Kaynak çalışmıyor",
                        message = phase.message,
                        fullscreen = true,
                        buttons = listOf(
                            TvModalButton("Tekrar dene", primary = true) { vm.retry() },
                            TvModalButton("Geri") { leave() },
                        ),
                        onDismiss = { leave() },
                    )
                    else -> Unit
                }
            }
        }
    }
}

@Composable
private fun TvVideoSurface(exo: ExoPlayer) {
    AndroidView(
        factory = { context ->
            PlayerView(context).apply {
                useController = false   // kontroller Compose'ta çizilir
                player = exo
                // TV: video yüzeyi Android görünüm odağı almasın; tuşlar Compose odak tutucusunda işlenir.
                isFocusable = false
                isFocusableInTouchMode = false
            }
        },
        modifier = Modifier.fillMaxSize(),
    )
}

// ------------------------------------------------------------------------------------------ kontroller

@Composable
private fun TvPlayerControls(s: PlayerUiState, vm: PlayerViewModel) {
    val dims = LocalTvDims.current
    var visible by remember { mutableStateOf(true) }
    var interaction by remember { mutableIntStateOf(0) }
    var menuHover by remember { mutableStateOf<Int?>(null) }
    var panel by remember { mutableStateOf<TvTracksPanelState?>(null) }
    var seekHint by remember { mutableStateOf<String?>(null) }
    var seekCount by remember { mutableIntStateOf(0) }
    val rootFocus = remember { FocusRequester() }
    val accelerator = remember { SeekAccelerator() }
    val dialogOpen = menuHover != null || panel != null
    val overlay = when {
        panel != null -> TvOverlay.Tracks
        menuHover != null -> TvOverlay.Sources
        else -> TvOverlay.None
    }

    fun reveal() {
        visible = true
        interaction++
    }

    // panel/menü öğeleri (durumdan türetilir: seçim değişince ● kendiliğinden taşınır)
    val audioItems = s.audioTracks.map { TvPanelItem(it.label, it.selected) }
    val subItems = listOf(TvPanelItem(TvPlayerLogic.SUB_OFF, s.textTracks.none { it.selected })) + s.textTracks.map { TvPanelItem(it.label, it.selected) }
    val audioDim = s.audioTracks.size <= 1
    val subsDim = s.textTracks.isEmpty()
    val audioActive = TvTracksPanel.isActive(audioItems, audioDim)
    val subActive = TvTracksPanel.isActive(subItems, subsDim)
    val tracksAvailable = s.audioTracks.size > 1 || s.textTracks.isNotEmpty() || s.hardSubNote != null

    // Oynarken HIDE_MS hareketsizlikte kontroller gizlenir (panel/menü açıkken gizlenmez).
    LaunchedEffect(visible, s.isPlaying, interaction, dialogOpen) {
        if (visible && s.isPlaying && !dialogOpen) {
            delay(TvPlayerSpec.HIDE_MS)
            visible = false
        }
    }
    // Klavye odağı HER ZAMAN oynatıcı kökünde kalır (panel/menü sanal odaklıdır, kendi odak hedefi yoktur): katman açılınca
    // ve kapanınca odak kökte yeniden doğrulanır; böylece odak boşa düşse bile tuşlar köke ulaşır.
    LaunchedEffect(overlay) { rootFocus.requestFocusWhenReady() }
    // Sarma bitince (900 ms tuş yok) son konum kaydedilir, ipucu kaybolur.
    LaunchedEffect(seekCount) {
        if (seekCount > 0) {
            delay(900L)
            vm.commitProgress()
            seekHint = null
        }
    }
    // Geri: önce panel/menü, sonra "sonraki bölüm" önerisi, sonra kontroller; hiçbiri yoksa ekran çıkar (üstteki BackHandler).
    // Katman açıkken Geri ayrıca kök tuş işleyicisinde de (aşağıda) doğrudan işlenir: tuşu o yutmaz.
    BackHandler(enabled = TvPlayerBack.handlesBack(overlay, s.upcoming != null, visible)) {
        when (TvPlayerBack.target(overlay, s.upcoming != null, visible)) {
            TvBackTarget.ClosePanel -> panel = null
            TvBackTarget.CloseMenu -> menuHover = null
            TvBackTarget.DismissUpcoming -> {
                vm.dismissUpcoming()
                reveal()
            }
            TvBackTarget.HideControls -> visible = false
            TvBackTarget.Leave -> Unit
        }
    }

    fun openSources() {
        if (s.streamLabels.isEmpty()) return
        panel = null
        menuHover = s.currentStream.coerceIn(0, s.streamLabels.lastIndex)
        reveal()
    }

    fun openTracks() {
        if (!tracksAvailable) return
        menuHover = null
        panel = TvTracksPanel.open(audioItems, audioDim, subItems, subsDim)
        reveal()
    }

    /**
     * Açık katmanın tuş işleyicisi: karar saf denetleyicide ([TvPlayerOverlayController]); burada yalnızca sonuç uygulanır.
     * Yalnızca katmanın kullandığı tuşlar tüketilir, kullanılmayanlar (ses tuşları vb.) geçer. (Eski kusur: her tuş -Geri
     * dâhil- `true` ile yutuluyordu; Geri'nin KeyDown'u yutulunca BackHandler hiç tetiklenmiyor, panel kapanmıyordu.)
     */
    fun onOverlayKey(event: KeyEvent): Boolean {
        val native = event.nativeKeyEvent
        val ctx = TvOverlayContext(audioItems.size, subItems.size, audioActive, subActive, s.streamLabels.size)
        val r = TvPlayerOverlayController.onKey(
            TvOverlayModel(panel, menuHover), ctx, native.keyCode, event.type == KeyEventType.KeyDown, native.repeatCount,
        )
        panel = r.model.panel
        menuHover = r.model.menuHover
        when (val e = r.effect) {
            TvOverlayEffect.None -> Unit
            is TvOverlayEffect.SelectAudio -> vm.selectAudio(e.index)
            is TvOverlayEffect.SelectSubtitle -> vm.selectText(e.index - 1)
            is TvOverlayEffect.SelectStream -> vm.selectStream(e.index)
        }
        if (r.closed) reveal()
        return r.consumed
    }

    val onRootKey: (KeyEvent) -> Boolean = { event ->
        if (overlay != TvOverlay.None) {
            onOverlayKey(event)
        } else if (event.type != KeyEventType.KeyDown) {
            false
        } else {
            val native = event.nativeKeyEvent
            when (val action = TvPlayerKeys.actionFor(native.keyCode)) {
                TvPlayerAction.TogglePlay -> {
                    // "Sonraki bölüm" kartı görünürken (oynuyorsa) Tamam = şimdi oynat
                    if (native.repeatCount == 0) {
                        if (s.upcoming != null && s.isPlaying) vm.playNext() else vm.togglePlay()
                    }
                    reveal()
                    true
                }
                TvPlayerAction.Play -> {
                    vm.player.play()
                    reveal()
                    true
                }
                TvPlayerAction.Pause -> {
                    vm.player.pause()
                    reveal()
                    true
                }
                TvPlayerAction.SeekBack, TvPlayerAction.SeekForward -> {
                    val direction = if (action == TvPlayerAction.SeekBack) -1 else 1
                    val delta = accelerator.onKey(native.eventTime, native.repeatCount > 0, direction)
                    if (delta != 0L) {
                        vm.seekQuiet(delta)
                        seekHint = TvPlayerLogic.seekHint(delta)
                        seekCount++
                    }
                    reveal()
                    true
                }
                TvPlayerAction.OpenSources -> {
                    openSources()
                    true
                }
                TvPlayerAction.OpenTracks -> {
                    openTracks()
                    true
                }
                TvPlayerAction.ShowControls -> {
                    reveal()
                    false
                }
                TvPlayerAction.Ignore -> false
            }
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .focusRequester(rootFocus)
            .onPreviewKeyEvent(onRootKey)
            .focusable(),
    ) {
        AnimatedVisibility(visible = visible, enter = fadeIn(), exit = fadeOut()) {
            Box(Modifier.fillMaxSize()) {
                TvTopInfo(s)
                TvBottomBar(
                    s = s,
                    seekHint = seekHint,
                    modifier = Modifier.align(Alignment.BottomCenter),
                )
                Row(
                    Modifier.align(Alignment.BottomEnd).padding(end = dims.dp(TvPlayerSpec.SAFE), bottom = dims.dp(TvPlayerSpec.BOTTOM_PAD_BOTTOM)),
                    horizontalArrangement = Arrangement.spacedBy(dims.dp(TvPlayerSpec.CTL_GAP)),
                    verticalAlignment = Alignment.Bottom,
                ) {
                    if (tracksAvailable) {
                        val subLabel = s.textTracks.firstOrNull { it.selected }?.label
                            ?: if (s.hardSubNote != null) "Gömülü" else TvPlayerLogic.SUB_OFF
                        TvCtlButton(TvPlayerLogic.tracksButtonLabel(subLabel), focused = panel != null)
                    }
                    if (s.streamLabels.isNotEmpty()) {
                        TvCtlButton(TvPlayerLogic.sourceButtonLabel(s.streamLabels.getOrNull(s.currentStream).orEmpty()), focused = menuHover != null)
                    }
                }
            }
        }
        if (s.buffering) {
            TvSpinner(72f, 8f, Modifier.align(Alignment.Center))
        }
        if (s.upcoming != null) {
            TvNextCard(
                ep = s.upcoming,
                countdown = null,
                hint = TvPlayerLogic.NEXT_HINT_WINDOW,
                modifier = Modifier.align(Alignment.TopEnd).padding(top = dims.dp(TvPlayerSpec.SAFE), end = dims.dp(TvPlayerSpec.SAFE)),
            )
        }
        menuHover?.let { hover ->
            TvSourceMenuPopup(
                labels = s.streamLabels,
                current = s.currentStream,
                hover = hover,
                modifier = Modifier.align(Alignment.BottomEnd).padding(end = dims.dp(TvPlayerSpec.SAFE), bottom = dims.dp(TvPlayerSpec.BOTTOM_PAD_BOTTOM + TvPlayerSpec.QMENU_BOTTOM)),
            )
        }
        panel?.let { p ->
            TvTracksPanelView(
                state = TvTracksPanelFocus.normalize(p, audioActive, subActive),
                audio = audioItems,
                subs = subItems,
                audioDim = audioDim,
                subsDim = subsDim,
                note = s.hardSubNote,
            )
        }
    }
}

/** `.pl-top`: sol üstte başlık 44 px/900, altında "S04 B02 · Ad" 30 px/700 ve geçerli kaynak etiketi 24 px; üstten koyu gradyan. */
@Composable
private fun TvTopInfo(s: PlayerUiState) {
    val dims = LocalTvDims.current
    Column(
        Modifier
            .fillMaxWidth()
            .background(Brush.verticalGradient(listOf(Color(0xD9000000), Color.Transparent)))
            .padding(dims.dp(TvPlayerSpec.SAFE)),
    ) {
        TvText(s.title, size = TvPlayerSpec.TOP_TITLE, weight = FontWeight.Black, modifier = Modifier.widthIn(max = dims.dp(TvPlayerSpec.TOP_TITLE_MAX_W)))
        if (s.episodeLabel.isNotBlank()) {
            TvText(
                TvPlayerLogic.episodeLine(s.episodeLabel),
                size = TvPlayerSpec.TOP_EP,
                color = TvColors.Overview,
                weight = FontWeight.Bold,
                modifier = Modifier.padding(top = dims.dp(TvPlayerSpec.TOP_EP_MT)).widthIn(max = dims.dp(TvPlayerSpec.TOP_TITLE_MAX_W)),
            )
        }
        val sub = s.streamLabels.getOrNull(s.currentStream).orEmpty()
        if (sub.isNotBlank()) {
            TvText(sub, size = TvPlayerSpec.TOP_SUB, color = TvColors.Dim, modifier = Modifier.padding(top = dims.dp(TvPlayerSpec.TOP_SUB_MT)))
        }
    }
}

/** `.pl-bottom`: ilerleme çubuğu (8 px, sarı dolgu + top), süreler ve durum satırı; alttan koyu gradyan. */
@Composable
private fun TvBottomBar(s: PlayerUiState, seekHint: String?, modifier: Modifier) {
    val dims = LocalTvDims.current
    val fraction = TvPlayerLogic.fraction(s.positionMs, s.durationMs)
    Column(
        modifier
            .fillMaxWidth()
            .background(Brush.verticalGradient(listOf(Color.Transparent, Color(0xE6000000))))
            .padding(
                start = dims.dp(TvPlayerSpec.SAFE),
                end = dims.dp(TvPlayerSpec.SAFE),
                top = dims.dp(TvPlayerSpec.BOTTOM_PAD_TOP),
                bottom = dims.dp(TvPlayerSpec.BOTTOM_PAD_BOTTOM),
            ),
    ) {
        Box(
            Modifier
                .fillMaxWidth()
                .height(dims.dp(TvPlayerSpec.BAR_H))
                .drawBehind {
                    val r = dims.px(TvPlayerSpec.BAR_H / 2f)
                    drawRoundRect(Color(0x4DFFFFFF), cornerRadius = CornerRadius(r))
                    drawRoundRect(TvColors.Accent, size = Size(size.width * fraction, size.height), cornerRadius = CornerRadius(r))
                    drawCircle(TvColors.Accent, radius = dims.px(TvPlayerSpec.KNOB / 2f), center = Offset(size.width * fraction, size.height / 2f))
                },
        )
        Row(Modifier.fillMaxWidth().padding(top = dims.dp(TvPlayerSpec.TIMES_MT))) {
            TvText(Format.clock(s.positionMs), size = TvPlayerSpec.TIMES, color = TvColors.Overview)
            Spacer(Modifier.weight(1f))
            TvText(TvPlayerLogic.remainingText(s.positionMs, s.durationMs), size = TvPlayerSpec.TIMES, color = TvColors.Overview)
        }
        Box(Modifier.padding(top = dims.dp(TvPlayerSpec.STATE_MT)).heightIn(min = dims.dp(TvPlayerSpec.STATE_MIN_H))) {
            val text = when {
                seekHint != null -> seekHint
                !s.isPlaying && !s.buffering -> TvPlayerLogic.STATE_PAUSED
                else -> ""
            }
            if (text.isNotEmpty()) TvText(text, size = TvPlayerSpec.STATE, color = TvColors.Accent, letterSpacingEm = 0.08f)
        }
    }
}

/** `.pl-tbtn` / `.pl-qbtn`: yarı saydam koyu düğme, 4 px beyaz-soluk çerçeve; panel/menü açıkken sarı dolgu. */
@Composable
private fun TvCtlButton(label: String, focused: Boolean) {
    val dims = LocalTvDims.current
    val shape = RoundedCornerShape(dims.dp(TvPlayerSpec.CTL_BTN_RADIUS))
    Box(
        Modifier
            .height(dims.dp(TvPlayerSpec.CTL_BTN_H))
            .background(if (focused) TvColors.Accent else Color(0x8C000000), shape)
            .border(dims.dp(TvPlayerSpec.CTL_BTN_BORDER), if (focused) TvColors.Accent else Color(0x59FFFFFF), shape)
            .padding(horizontal = dims.dp(TvPlayerSpec.CTL_BTN_PAD_H - TvPlayerSpec.CTL_BTN_BORDER)),
        contentAlignment = Alignment.Center,
    ) {
        TvText(label, size = TvPlayerSpec.CTL_BTN_TEXT, color = if (focused) TvColors.Ink else Color.White, weight = FontWeight.Bold)
    }
}

/** `.pl-qmenu`: düğmenin üstünde açılan kaynak/kalite listesi; geçerli kaynak "●", gezinilen satır sarı. */
@Composable
private fun TvSourceMenuPopup(labels: List<String>, current: Int, hover: Int, modifier: Modifier) {
    val dims = LocalTvDims.current
    val listState = rememberLazyListState()
    LaunchedEffect(hover) { listState.animateScrollToItem((hover - 3).coerceAtLeast(0)) }
    val shape = RoundedCornerShape(dims.dp(8))
    Box(
        modifier
            .widthIn(min = dims.dp(TvPlayerSpec.QMENU_MIN_W))
            .background(Color(0xF5141414), shape)
            .border(dims.dp(2), Color(0x33FFFFFF), shape)
            .padding(vertical = dims.dp(8)),
    ) {
        LazyColumn(state = listState, modifier = Modifier.heightIn(max = dims.dp(640))) {
            itemsIndexed(labels) { index, label ->
                val focused = index == hover
                val mark = if (index == current) "● " else ""
                Row(
                    Modifier
                        .fillMaxWidth()
                        .background(if (focused) TvColors.Accent else Color.Transparent)
                        .padding(horizontal = dims.dp(TvPlayerSpec.QITEM_PAD_H), vertical = dims.dp(TvPlayerSpec.QITEM_PAD_V)),
                ) {
                    if (mark.isNotEmpty()) TvText(mark, size = TvPlayerSpec.QITEM_TEXT, color = TvColors.Accent)
                    TvText(
                        label,
                        size = TvPlayerSpec.QITEM_TEXT,
                        color = if (focused) TvColors.Ink else if (index == current) Color.White else TvColors.Overview,
                    )
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------ ses ve altyazılar paneli

/** `.pl-tp`: iki sütunlu (SES | ALTYAZI) panel; tek/hiç seçenekli sütun soluk ve odaksız. */
@Composable
private fun TvTracksPanelView(
    state: TvTracksPanelState,
    audio: List<TvPanelItem>,
    subs: List<TvPanelItem>,
    audioDim: Boolean,
    subsDim: Boolean,
    note: String?,
) {
    val dims = LocalTvDims.current
    // Seçilebilir hiç seçenek yoksa (tek ses + yalnız "Kapalı"): odak "Kapat" düğmesinde (panel odaksız/çıkışsız kalmaz).
    val noOption = !TvTracksPanelFocus.hasOption(TvTracksPanel.isActive(audio, audioDim), TvTracksPanel.isActive(subs, subsDim))
    Box(Modifier.fillMaxSize().background(Color(0x8C000000))) {
        val shape = RoundedCornerShape(dims.dp(8))
        Column(
            Modifier
                .padding(start = dims.dp(TvPlayerSpec.TP_LEFT), top = dims.dp(TvPlayerSpec.TP_TOP))
                .width(dims.dp(TvPlayerSpec.TP_W))
                .background(Color(0xF7141414), shape)
                .border(dims.dp(2), Color(0x33FFFFFF), shape)
                .padding(
                    start = dims.dp(TvPlayerSpec.TP_PAD_H),
                    end = dims.dp(TvPlayerSpec.TP_PAD_H),
                    top = dims.dp(TvPlayerSpec.TP_PAD_TOP),
                    bottom = dims.dp(TvPlayerSpec.TP_PAD_BOTTOM),
                ),
        ) {
            TvText(TvPlayerLogic.PANEL_TITLE, size = TvPlayerSpec.TP_TITLE, weight = FontWeight.Black, modifier = Modifier.padding(bottom = dims.dp(28)))
            Row(Modifier.fillMaxWidth()) {
                PanelColumn(
                    head = "SES",
                    items = audio,
                    dim = audioDim,
                    focusIndex = if (state.col == 0 && TvTracksPanel.isActive(audio, audioDim)) state.audioIndex else -1,
                    modifier = Modifier.weight(1f),
                )
                PanelColumn(
                    head = "ALTYAZI",
                    items = subs,
                    dim = subsDim,
                    focusIndex = if (state.col == 1 && TvTracksPanel.isActive(subs, subsDim)) state.subIndex else -1,
                    modifier = Modifier.weight(1f),
                )
            }
            Box(Modifier.padding(top = dims.dp(18)).heightIn(min = dims.dp(34))) {
                if (!note.isNullOrEmpty()) TvText(note, size = TvPlayerSpec.TP_NOTE, color = TvColors.Accent, maxLines = 2)
            }
            if (noOption) {
                Box(Modifier.padding(top = dims.dp(12), start = dims.dp(8), bottom = dims.dp(8))) {
                    TvButtonFace(TvPlayerLogic.CLOSE_LABEL, focused = true, primary = true, small = true)
                }
            }
            TvText(
                if (noOption) TvPlayerLogic.TRACKS_HINT_EMPTY else TvPlayerLogic.TRACKS_HINT,
                size = TvPlayerSpec.TP_HINT,
                color = TvColors.Dim,
                modifier = Modifier.padding(top = dims.dp(8)),
            )
        }
    }
}

@Composable
private fun PanelColumn(head: String, items: List<TvPanelItem>, dim: Boolean, focusIndex: Int, modifier: Modifier) {
    val dims = LocalTvDims.current
    Column(
        modifier
            .heightIn(min = dims.dp(TvPlayerSpec.TP_COL_MIN_H))
            .padding(end = dims.dp(32))
            .alpha(if (dim) 0.4f else 1f),
    ) {
        TvText(head, size = TvPlayerSpec.TP_HEAD, color = TvColors.Dim, letterSpacingEm = 0.12f, modifier = Modifier.padding(bottom = dims.dp(14)))
        items.forEachIndexed { i, item ->
            val focused = i == focusIndex
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(if (focused) TvColors.Accent else Color.Transparent)
                    .padding(horizontal = dims.dp(TvPlayerSpec.TP_ITEM_PAD_H), vertical = dims.dp(TvPlayerSpec.TP_ITEM_PAD_V)),
            ) {
                if (item.current) TvText("● ", size = TvPlayerSpec.TP_ITEM, color = if (focused) TvColors.Ink else TvColors.Accent)
                TvText(item.label, size = TvPlayerSpec.TP_ITEM, color = if (focused) TvColors.Ink else TvColors.Overview)
            }
        }
    }
}

// ------------------------------------------------------------------------------------------ sonraki bölüm

/** `.pl-next`: sağ üst kart — "Sonraki bölüm", bölüm satırı, vurgulu "Şimdi oynat" ve ipucu. */
@Composable
private fun TvNextCard(ep: Episode, countdown: Int?, hint: String, modifier: Modifier) {
    val dims = LocalTvDims.current
    val shape = RoundedCornerShape(dims.dp(6))
    Column(
        modifier
            .width(dims.dp(TvPlayerSpec.NEXT_W))
            .background(Color(0xF0141414), shape)
            .border(dims.dp(2), Color(0x38FFFFFF), shape)
            .padding(horizontal = dims.dp(TvPlayerSpec.NEXT_PAD_H), vertical = dims.dp(TvPlayerSpec.NEXT_PAD_V)),
        horizontalAlignment = Alignment.End,
    ) {
        TvText(TvPlayerLogic.NEXT_TITLE, size = TvPlayerSpec.NEXT_TITLE, weight = FontWeight.Bold, align = TextAlign.End, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(dims.dp(8)))
        TvText(TvPlayerLogic.nextLabel(ep), size = TvPlayerSpec.NEXT_LABEL, color = TvColors.Dim, align = TextAlign.End, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(dims.dp(18)))
        TvButtonFace(TvPlayerLogic.nextNowLabel(countdown), focused = true, primary = true)
        Spacer(Modifier.height(dims.dp(14)))
        TvText(hint, size = TvPlayerSpec.NEXT_HINT, color = TvColors.Dim, align = TextAlign.End, modifier = Modifier.fillMaxWidth())
    }
}

/** Bölüm bitti: koyu perde + geri sayımlı "Sonraki bölüm" kartı. Tamam = şimdi oynat, Geri = iptal (çıkış). */
@Composable
private fun TvEndedLayer(s: PlayerUiState, vm: PlayerViewModel) {
    val dims = LocalTvDims.current
    val next = s.nextEpisode ?: return
    val root = remember { FocusRequester() }
    LaunchedEffect(Unit) { root.requestFocusWhenReady() }
    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0x99000000))
            .focusRequester(root)
            .onPreviewKeyEvent { event ->
                if (event.type == KeyEventType.KeyDown &&
                    TvPlayerKeys.actionFor(event.nativeKeyEvent.keyCode).let { it == TvPlayerAction.TogglePlay || it == TvPlayerAction.Play }
                ) {
                    if (event.nativeKeyEvent.repeatCount == 0) vm.playNext()
                    true
                } else {
                    false
                }
            }
            .focusable(),
    ) {
        TvNextCard(
            ep = next,
            countdown = s.countdown,
            hint = TvPlayerLogic.NEXT_HINT_ENDED,
            modifier = Modifier.align(Alignment.TopEnd).padding(top = dims.dp(TvPlayerSpec.SAFE), end = dims.dp(TvPlayerSpec.SAFE)),
        )
    }
}

// ------------------------------------------------------------------------------------------ embed (WebView) yedeği

/**
 * type=embed akışlar (ör. YouTube fragman) doğrulanamaz: rapor yazılmadan WebView'da açılır. Tizen `.pl-back`: solda
 * "← Geri", sağda "Sorun bildir" (elle failure raporu); ikisi de kumandayla odaklanır (odakta sarı dolgu).
 */
@Composable
private fun TvEmbedPane(stream: VideoStream, onBack: () -> Unit, onReportBroken: () -> Unit) {
    val dims = LocalTvDims.current
    val backFocus = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { scope.launch { backFocus.requestFocusWhenReady() } }
    // Güvence: sayfa/WebView odağı çalarsa ya da odak boşa düşerse "← Geri" düğmesine geri verilir (Geri tuşu BackHandler'dadır).
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .onFocusChanged { if (!it.hasFocus) scope.launch { backFocus.requestFocusWhenReady() } },
    ) {
        EmbedWebView(stream = stream, modifier = Modifier.fillMaxSize(), focusable = false)
        Row(
            Modifier
                .align(Alignment.TopStart)
                .fillMaxWidth()
                .padding(start = dims.dp(40), end = dims.dp(40), top = dims.dp(36)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TvBackPill("← Geri", onBack, Modifier.focusRequester(backFocus))
            Spacer(Modifier.weight(1f))
            TvBackPill("Sorun bildir", onReportBroken, Modifier)
        }
    }
}

/** `.pl-back`: 64 px, yarı saydam koyu, 4 px soluk çerçeve; odakta sarı dolgu + %106. */
@Composable
private fun TvBackPill(label: String, onClick: () -> Unit, modifier: Modifier) {
    val dims = LocalTvDims.current
    var focused by remember { mutableStateOf(false) }
    val shape = RoundedCornerShape(dims.dp(6))
    Box(
        modifier
            .graphicsLayer {
                val scale = if (focused) 1.06f else 1f
                scaleX = scale
                scaleY = scale
            }
            .onFocusChanged { focused = it.isFocused }
            .height(dims.dp(64))
            .background(if (focused) TvColors.Accent else Color(0x9E000000), shape)
            .border(dims.dp(4), if (focused) TvColors.Accent else Color(0x59FFFFFF), shape)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick)
            .padding(horizontal = dims.dp(26)),
        contentAlignment = Alignment.Center,
    ) {
        TvText(label, size = 28f, color = if (focused) TvColors.Ink else Color.White, weight = FontWeight.Bold)
    }
}
