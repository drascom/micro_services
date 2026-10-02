package com.diziflix.app.ui.tv

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Season
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.DetailLogic
import com.diziflix.app.domain.EpisodeState
import com.diziflix.app.domain.PlayerKeys
import com.diziflix.app.domain.TvActionKind
import com.diziflix.app.domain.TvActionSpec
import com.diziflix.app.domain.TvDetailLayout
import com.diziflix.app.domain.TvDetailLogic
import com.diziflix.app.domain.TvDetailNavigation
import com.diziflix.app.domain.TvDetailPos
import com.diziflix.app.domain.TvDetailSection
import com.diziflix.app.domain.TvDetailSpec
import com.diziflix.app.domain.TvDir
import com.diziflix.app.domain.TvEpisodeFlagKind
import com.diziflix.app.domain.TvRowMetrics
import com.diziflix.app.ui.common.LocalBaseUrl
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.detail.DetailUiState
import com.diziflix.app.ui.detail.DetailViewModel
import kotlin.math.roundToInt

/*
 * Android TV DETAY ekranı — Tizen `css/detail.css` + `js/screens/detail.js` karşılığı. Tablet `DetailScreen`'inden AYRI bir
 * ekrandır (yalnızca ViewModel/veri katmanı ortak). Ana sayfa gibi SANAL odak: tek kök odak hedefi tuşları alır; "hangi bölüm
 * ve öğe odakta" durumu [TvDetailFocus]'ta tutulur, kararlar saf mantıkta ([TvDetailNavigation]). Sayfa ve listeler açık
 * `translationY` animasyonuyla kayar (200 ms).
 *
 * Yerleşim (Tizen px): 760 px hero (arka plan + gradyan; sol-altta 1000 px gövde: başlık, meta, eylem düğmeleri, özet, ekip);
 * dizide altında SOLDA 440 px sezon listesi + SAĞDA bölüm listesi; en altta "Benzer Yapımlar" satırı.
 */

// ------------------------------------------------------------------------------------------ odak durumu

/** Sanal odak: bölüm + öğe + sütun hafızası + (gösterilen sezonun) bölüm hedefi. Ekran yığınından dönünce korunur. */
@Stable
class TvDetailFocus {
    var section by mutableStateOf(TvDetailSection.Actions)
    var index by mutableIntStateOf(0)
    var initialized by mutableStateOf(false)
    var epSeason by mutableIntStateOf(-1)
    var epIdx by mutableIntStateOf(-1)
    val memory = mutableStateMapOf<TvDetailSection, Int>()

    fun set(pos: TvDetailPos) {
        section = pos.section
        index = pos.index
        if (pos.section == TvDetailSection.Actions || pos.section == TvDetailSection.Similar) memory[pos.section] = pos.index
    }

    val pos: TvDetailPos get() = TvDetailPos(section, index)

    companion object {
        val Saver: Saver<TvDetailFocus, List<Any>> = Saver(
            save = { f ->
                listOf(
                    f.section.name, f.index, f.initialized, f.epSeason, f.epIdx,
                    f.memory[TvDetailSection.Actions] ?: -1, f.memory[TvDetailSection.Similar] ?: -1,
                )
            },
            restore = { l ->
                TvDetailFocus().also { f ->
                    f.section = TvDetailSection.valueOf(l[0] as String)
                    f.index = l[1] as Int
                    f.initialized = l[2] as Boolean
                    f.epSeason = l[3] as Int
                    f.epIdx = l[4] as Int
                    (l[5] as Int).takeIf { it >= 0 }?.let { f.memory[TvDetailSection.Actions] = it }
                    (l[6] as Int).takeIf { it >= 0 }?.let { f.memory[TvDetailSection.Similar] = it }
                }
            },
        )
    }
}

// ------------------------------------------------------------------------------------------ ekran

@Composable
fun TvDetailScreen(
    itemId: String,
    initialEpisodeId: String?,
    profileId: String,
    onBack: () -> Unit,
    onOpenDetail: (itemId: String) -> Unit,
    onPlay: (itemId: String, episodeId: String?, kind: String) -> Unit,
) {
    val vm = containerViewModel(key = "detail:$itemId:${initialEpisodeId ?: ""}") {
        DetailViewModel(it, profileId, itemId, initialEpisodeId)
    }
    val state by vm.state.collectAsStateWithLifecycle()
    OnResumeEffect { vm.onResume() }
    RefreshWhenBackOnline(LocalContainer.current, isStale = vm::needsRefresh, refresh = vm::refreshAfterReconnect)

    val toaster = LocalTvToaster.current
    val message = state.message
    LaunchedEffect(message) {
        if (message != null) {
            toaster?.show(message)
            vm.consumeMessage()
        }
    }

    TvTheme {
        Box(Modifier.fillMaxSize().background(TvColors.Background)) {
            val s = state
            val detail = s.detail
            when {
                detail != null -> TvDetailContent(
                    state = s,
                    detail = detail,
                    initialEpisodeId = initialEpisodeId,
                    onSelectSeason = vm::selectSeason,
                    onToggleMyList = vm::toggleMyList,
                    onOpenDetail = onOpenDetail,
                    onPlay = onPlay,
                    onToast = { toaster?.show(it) },
                )
                s.error != null -> TvErrorScreen(
                    title = "Detay yüklenemedi",
                    message = s.error,
                    actions = listOf("Tekrar dene" to { vm.retry() }, "Geri" to onBack),
                )
                else -> TvDetailSkeleton()
            }
            val offline = s.offline
            if (offline != null) {
                val dims = LocalTvDims.current
                val label = remember(offline) { offline.label(System.currentTimeMillis()) }
                TvOfflineChip(
                    label,
                    Modifier.align(Alignment.BottomStart).padding(start = dims.dp(TvDetailSpec.BODY_LEFT), bottom = dims.dp(40)),
                )
            }
        }
    }
}

/** Anında çizilen iskelet (`DZ.skeleton.detail`): hero yüzeyi + başlık/satır blokları. */
@Composable
private fun TvDetailSkeleton() {
    val dims = LocalTvDims.current
    Box(Modifier.fillMaxSize()) {
        TvShimmer(Modifier.fillMaxWidth().height(dims.dp(TvDetailSpec.HERO_H)))
        Box(Modifier.fillMaxWidth().height(dims.dp(TvDetailSpec.HERO_H)).background(DetailFadeHorizontal))
        Column(
            Modifier
                .align(Alignment.TopStart)
                .padding(start = dims.dp(TvDetailSpec.BODY_LEFT), top = dims.dp(TvDetailSpec.HERO_H - TvDetailSpec.BODY_BOTTOM - 330f)),
        ) {
            TvShimmer(Modifier.size(dims.dp(700), dims.dp(64)).clip(RoundedCornerShape(dims.dp(4))))
            Spacer(Modifier.height(dims.dp(20)))
            TvShimmer(Modifier.size(dims.dp(520), dims.dp(26)).clip(RoundedCornerShape(dims.dp(4))))
            Spacer(Modifier.height(dims.dp(20)))
            TvShimmer(Modifier.size(dims.dp(900), dims.dp(24)).clip(RoundedCornerShape(dims.dp(4))))
            Spacer(Modifier.height(dims.dp(12)))
            TvShimmer(Modifier.size(dims.dp(780), dims.dp(24)).clip(RoundedCornerShape(dims.dp(4))))
            Spacer(Modifier.height(dims.dp(34)))
            Row {
                TvShimmer(Modifier.size(dims.dp(220), dims.dp(64)).clip(RoundedCornerShape(dims.dp(4))))
                Spacer(Modifier.width(dims.dp(20)))
                TvShimmer(Modifier.size(dims.dp(260), dims.dp(64)).clip(RoundedCornerShape(dims.dp(4))))
            }
        }
    }
}

// ------------------------------------------------------------------------------------------ içerik

private val DetailFadeHorizontal = Brush.horizontalGradient(
    0f to Color(0xF5141414),        // rgba(20,20,20,.96) 0
    0.38f to Color(0xB8141414),     // .72 @ 729.6 / 1920
    0.68f to Color(0x00141414),     // 0 @ 1305.6 / 1920
)
private val DetailFadeVertical = Brush.verticalGradient(
    0f to Color(0x00141414),
    0.40f to Color(0x00141414),     // to top: 0 @ %60
    0.76f to Color(0x80141414),     // .5 @ %24
    1f to Color(0xFF141414),
)

/**
 * Eylem satırı 1000 px'lik gövdeden TAŞABİLİR (Tizen `.btn` satırı da öyle taşar): içerik [maxPx]'e kadar genişliğiyle ölçülür,
 * üst öğeye ise gövde genişliği bildirilir (yerleşim değişmez). Bu sınırı da aşarsa [FlowRow] ikinci satıra sarar.
 */
private fun Modifier.allowWidthUpTo(maxPx: Int): Modifier = layout { measurable, constraints ->
    val wide = constraints.copy(maxWidth = maxOf(constraints.maxWidth, maxPx))
    val placeable = measurable.measure(wide)
    layout(placeable.width.coerceAtMost(constraints.maxWidth), placeable.height) { placeable.place(0, 0) }
}

private fun Modifier.onPlacedY(set: (Int) -> Unit): Modifier =
    onGloballyPositioned { set(it.positionInParent().y.roundToInt()) }

@Composable
private fun TvDetailContent(
    state: DetailUiState,
    detail: Detail,
    initialEpisodeId: String?,
    onSelectSeason: (Int) -> Unit,
    onToggleMyList: () -> Unit,
    onOpenDetail: (String) -> Unit,
    onPlay: (String, String?, String) -> Unit,
    onToast: (String) -> Unit,
) {
    val dims = LocalTvDims.current
    val u = dims.px(1f)
    val item = detail.item
    val today = remember { DetailLogic.todayIso() }
    val seasons = state.seasons
    val shown = state.selectedSeason.coerceIn(0, (seasons.size - 1).coerceAtLeast(0))
    val episodes = seasons.getOrNull(shown)?.episodes ?: emptyList()
    val similar = remember(detail) { detail.similar }
    val specs = remember(detail, seasons) { TvDetailLogic.actionButtons(detail, seasons, today) }
    val allActions = remember(specs, state.inMyList) { specs + TvDetailLogic.myListSpec(state.inMyList) }
    val hasOverview = item.overview.isNotBlank()

    val focus = rememberSaveable(saver = TvDetailFocus.Saver) { TvDetailFocus() }
    val rootFocus = remember { FocusRequester() }
    var selectArmed by remember { mutableStateOf(false) }
    var textModal by remember { mutableStateOf<Pair<String, String>?>(null) }

    // bölüm hedefi: odaklanılan bölüm bu sezondaysa o, değilse oynatılabilir ilk izlenmemiş
    val episodeIndex = if (focus.epSeason == shown && focus.epIdx in episodes.indices) {
        focus.epIdx
    } else {
        DetailLogic.targetEpisodeIndex(episodes, today)
    }
    val layout = TvDetailLayout(
        actionCount = allActions.size,
        hasOverview = hasOverview,
        seasonCount = seasons.size,
        episodeCount = episodes.size,
        similarCount = similar.size,
        shownSeason = shown,
        episodeIndex = episodeIndex,
    )
    val currentLayout by rememberUpdatedState(layout)

    // ilk odak: bölüm kartından gelindiyse o bölüm, yoksa Oynat/Devam Et; veri değişince geçersiz konum toparlanır
    LaunchedEffect(layout) {
        if (!focus.initialized) {
            focus.initialized = true
            var epFocus: Int? = null
            val pos = if (initialEpisodeId.isNullOrBlank()) null else DetailLogic.findEpisode(seasons, initialEpisodeId)
            if (pos != null && pos.seasonIndex == shown) {
                epFocus = pos.episodeIndex
                focus.epSeason = shown
                focus.epIdx = pos.episodeIndex
            }
            focus.set(TvDetailNavigation.initial(layout, epFocus))
        } else if (focus.section == TvDetailSection.Episodes && focus.epSeason != shown) {
            // sezon geçişi sürüyor (Sağ ile bölümlere girildi, liste bir sonraki karede yenilenir): bekle
        } else {
            val fixed = TvDetailNavigation.clamp(layout, focus.pos)
            if (fixed != focus.pos) focus.set(fixed)
        }
    }
    LaunchedEffect(Unit) { rootFocus.requestFocusWhenReady() }

    // ---- sezon listesi: odak sezonda gezinirken bölüm listesi 150 ms sonra o sezona geçer
    LaunchedEffect(focus.section, focus.index) {
        if (focus.section == TvDetailSection.Seasons && focus.index != shown) {
            kotlinx.coroutines.delay(TvDetailSpec.SEASON_DEBOUNCE_MS)
            onSelectSeason(focus.index)
        }
    }

    // ---- kaydırma: liste görüntü alanı + pencere
    val viewH = remember(seasons) { TvDetailLogic.computeViewH(TvDetailLogic.viewNeed(seasons)) }
    var eTarget by remember { mutableFloatStateOf(0f) }
    var sTarget by remember { mutableFloatStateOf(0f) }
    val eAnim = remember { Animatable(0f) }
    val sAnim = remember { Animatable(0f) }
    LaunchedEffect(eTarget) { eAnim.animateTo(eTarget * u, tween(TvDetailSpec.SCROLL_ANIM_MS, easing = TvEase)) }
    LaunchedEffect(sTarget) { sAnim.animateTo(sTarget * u, tween(TvDetailSpec.SCROLL_ANIM_MS, easing = TvEase)) }
    // yeni sezonun listesi eski konumdan "ucarak" gelmesin: hedefe hemen yerleş
    LaunchedEffect(shown, episodes.size) {
        val start = if (episodeIndex >= 0) TvDetailLogic.clampScroll(episodeIndex * TvDetailSpec.EP_PITCH, TvDetailSpec.EP_PITCH, viewH, episodes.size) else 0f
        eTarget = start
        eAnim.snapTo(start * u)
    }
    LaunchedEffect(focus.section, focus.index) {
        when (focus.section) {
            TvDetailSection.Episodes ->
                eTarget = TvDetailLogic.fitScroll(eTarget, focus.index, TvDetailSpec.EP_PITCH, viewH, episodes.size)
            TvDetailSection.Seasons ->
                sTarget = TvDetailLogic.fitScroll(sTarget, focus.index, TvDetailSpec.SEASON_PITCH, viewH, seasons.size)
            else -> Unit
        }
    }

    // ---- sayfa kaydırması (Tizen px): düğmeler/özet/benzerler -> satır üstü ekranın %28'ine; liste -> tarayıcı 96 px altına
    var bodyH by remember { mutableIntStateOf(0) }
    var actionsY by remember { mutableIntStateOf(0) }
    var overviewY by remember { mutableIntStateOf(0) }
    val hasBrowser = seasons.isNotEmpty()
    val placeholderH = 102.9f + TvDetailSpec.BROWSER_MB
    val browserBlock = when {
        hasBrowser -> TvDetailSpec.BR_TITLE_H + viewH + TvDetailSpec.BROWSER_MB
        item.isSeries -> placeholderH
        else -> 0f
    }
    val bodyTop = TvDetailSpec.HERO_H - TvDetailSpec.BODY_BOTTOM - bodyH / u
    val similarTop = TvDetailSpec.HERO_H + browserBlock + TvDetailSpec.SIMILAR_MT + 1f
    val targetPageY = when (focus.section) {
        TvDetailSection.Actions -> TvDetailLogic.pageYForRow(bodyTop + actionsY / u)
        TvDetailSection.Overview -> TvDetailLogic.pageYForRow(bodyTop + overviewY / u)
        TvDetailSection.Seasons, TvDetailSection.Episodes -> TvDetailLogic.pageYForBrowser(TvDetailSpec.HERO_H)
        TvDetailSection.Similar -> TvDetailLogic.pageYForRow(similarTop)
    }
    val pageY = remember { Animatable(0f) }
    var pageSnapped by remember { mutableStateOf(false) }
    val pageReady = bodyH > 0
    LaunchedEffect(targetPageY, pageReady) {
        // Gövde ölçülene kadar bekle; ilk yerleşim animasyonsuz (açılışta "kayarak gelme" olmasın), sonrası 200 ms.
        if (!pageReady) return@LaunchedEffect
        if (!pageSnapped) {
            pageSnapped = true
            pageY.snapTo(targetPageY * u)
        } else {
            pageY.animateTo(targetPageY * u, tween(TvDetailSpec.SCROLL_ANIM_MS, easing = TvEase))
        }
    }

    // ---- eylemler
    fun activateEpisode(ep: Episode) {
        val notice = TvDetailLogic.episodeNotice(ep, DetailLogic.episodeState(ep, today))
        if (notice != null) textModal = notice else onPlay(item.id, ep.id, "video")
    }

    fun runAction(spec: TvActionSpec) {
        when (spec.kind) {
            TvActionKind.TrailerDead -> onToast(TvDetailLogic.TRAILER_DEAD_TOAST)
            TvActionKind.PlayTrailer -> onPlay(item.id, null, "trailer")
            TvActionKind.MyList -> onToggleMyList()
            else -> onPlay(item.id, spec.episodeId?.takeIf { it != item.id }, "video")
        }
    }

    fun enterEpisodes() {
        val idx = focus.index.coerceIn(0, (seasons.size - 1).coerceAtLeast(0))
        val eps = seasons.getOrNull(idx)?.episodes ?: return
        if (eps.isEmpty()) return
        onSelectSeason(idx)
        val target = if (focus.epSeason == idx && focus.epIdx in eps.indices) focus.epIdx else DetailLogic.targetEpisodeIndex(eps, today)
        if (target < 0) return
        focus.epSeason = idx
        focus.epIdx = target
        focus.set(TvDetailPos(TvDetailSection.Episodes, target))
    }

    fun activate() {
        when (focus.section) {
            TvDetailSection.Actions -> allActions.getOrNull(focus.index)?.let { runAction(it) }
            TvDetailSection.Overview -> textModal = item.title to item.overview
            TvDetailSection.Seasons -> enterEpisodes()
            TvDetailSection.Episodes -> episodes.getOrNull(focus.index)?.let { activateEpisode(it) }
            TvDetailSection.Similar -> similar.getOrNull(focus.index)?.let { onOpenDetail(it.id) }
        }
    }

    fun move(dir: TvDir) {
        if (focus.section == TvDetailSection.Seasons && dir == TvDir.Right) {
            enterEpisodes()
            return
        }
        val next = TvDetailNavigation.move(currentLayout, focus.pos, dir, focus.memory) ?: return
        if (next.section == TvDetailSection.Episodes) {
            focus.epSeason = shown
            focus.epIdx = next.index
        }
        focus.set(next)
    }

    fun onKey(event: KeyEvent): Boolean {
        if (PlayerKeys.isSelect(event.nativeKeyEvent.keyCode)) {
            // Kısa basış: basış bu ekranda başladıysa bırakınca çalışır (önceki ekrandan artakalan bırakma tetiklemez).
            when (event.type) {
                KeyEventType.KeyDown -> if (event.nativeKeyEvent.repeatCount == 0) selectArmed = true
                KeyEventType.KeyUp -> if (selectArmed) {
                    selectArmed = false
                    activate()
                }
            }
            return true
        }
        if (event.type != KeyEventType.KeyDown) return false
        val dir = when (event.key) {
            Key.DirectionUp -> TvDir.Up
            Key.DirectionDown -> TvDir.Down
            Key.DirectionLeft -> TvDir.Left
            Key.DirectionRight -> TvDir.Right
            else -> return false
        }
        move(dir)
        return true
    }

    val base = LocalBaseUrl.current
    val hairline = with(androidx.compose.ui.platform.LocalDensity.current) { dims.px(1f).coerceAtLeast(1f).toDp() }
    val activeSeason = if (focus.section == TvDetailSection.Seasons) -1 else shown

    Box(
        Modifier
            .fillMaxSize()
            .clipToBounds()
            .focusRequester(rootFocus)
            .onPreviewKeyEvent { onKey(it) }
            .focusable(),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .wrapContentHeight(Alignment.Top, unbounded = true)
                .graphicsLayer { translationY = -pageY.value },
        ) {
            // ------------------------------------------------ hero
            Box(Modifier.fillMaxWidth().height(dims.dp(TvDetailSpec.HERO_H)).background(Color(0xFF0B0B0B))) {
                val backdrop = remember(base, item) {
                    UrlUtil.sized(base, item.backdrop ?: item.portrait, TvDetailSpec.BACKDROP_W, TvDetailSpec.BACKDROP_H)
                }
                TvImage(backdrop, TvDetailSpec.BACKDROP_W, TvDetailSpec.BACKDROP_H, Modifier.fillMaxSize(), alignment = Alignment.TopCenter, fadeMs = 200)
                Box(Modifier.fillMaxSize().background(DetailFadeHorizontal))
                Box(Modifier.fillMaxSize().background(DetailFadeVertical))
                TvDetailBody(
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(start = dims.dp(TvDetailSpec.BODY_LEFT), bottom = dims.dp(TvDetailSpec.BODY_BOTTOM))
                        .width(dims.dp(TvDetailSpec.BODY_W))
                        .onSizeChanged { bodyH = it.height },
                    detail = detail,
                    hydratingNote = state.hydrating && !(item.isSeries && seasons.isEmpty()),
                    actions = allActions,
                    focusSection = focus.section,
                    focusIndex = focus.index,
                    onActionsY = { actionsY = it },
                    onOverviewY = { overviewY = it },
                )
            }

            // ------------------------------------------------ sezon + bölüm tarayıcısı
            if (hasBrowser) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = dims.dp(TvDetailSpec.BROWSER_PAD_H))
                        .padding(bottom = dims.dp(TvDetailSpec.BROWSER_MB)),
                ) {
                    Column(Modifier.width(dims.dp(TvDetailSpec.SEASONS_W))) {
                        BrowserTitle("Sezonlar")
                        Box(Modifier.fillMaxWidth().height(dims.dp(viewH)).clipToBounds()) {
                            Box(Modifier.fillMaxWidth().height(dims.dp(viewH)).graphicsLayer { translationY = -sAnim.value }) {
                                seasons.forEachIndexed { i, season ->
                                    TvSeasonRow(
                                        season = season,
                                        focused = focus.section == TvDetailSection.Seasons && focus.index == i,
                                        active = activeSeason == i,
                                        modifier = Modifier.offset { IntOffset(0, (i * TvDetailSpec.SEASON_PITCH * u).roundToInt()) },
                                    )
                                }
                            }
                        }
                    }
                    Spacer(Modifier.width(dims.dp(TvDetailSpec.SEASONS_GAP)))
                    Column(Modifier.weight(1f)) {
                        BrowserTitle("Bölümler")
                        Box(Modifier.fillMaxWidth().height(dims.dp(viewH)).clipToBounds()) {
                            Box(Modifier.fillMaxWidth().height(dims.dp(viewH)).graphicsLayer { translationY = -eAnim.value }) {
                                val window = TvDetailLogic.window(eTarget, viewH, TvDetailSpec.EP_PITCH, episodes.size)
                                for (i in window) {
                                    val ep = episodes[i]
                                    androidx.compose.runtime.key(ep.id, i) {
                                        TvEpisodeRow(
                                            episode = ep,
                                            index = i,
                                            today = today,
                                            focused = focus.section == TvDetailSection.Episodes && focus.index == i,
                                            modifier = Modifier.offset { IntOffset(0, (i * TvDetailSpec.EP_PITCH * u).roundToInt()) },
                                        )
                                    }
                                }
                            }
                            if (episodes.isEmpty()) {
                                TvText(
                                    TvDetailLogic.NO_SEASON_EPISODES_TEXT,
                                    size = 26f,
                                    color = TvColors.Dim,
                                    modifier = Modifier.padding(vertical = dims.dp(24)),
                                )
                            }
                        }
                    }
                }
            } else if (item.isSeries) {
                // bölüm bilgisi olmayan dizi yer tutucusu
                Column(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = dims.dp(TvDetailSpec.BROWSER_PAD_H))
                        .padding(bottom = dims.dp(TvDetailSpec.BROWSER_MB)),
                ) {
                    TvText("Bölümler", size = TvDetailSpec.BR_TITLE_SIZE, weight = FontWeight.Bold, line = 40.5f)
                    Spacer(Modifier.height(dims.dp(12)))
                    TvText(
                        if (state.hydrating) TvDetailLogic.HYDRATING_TEXT else DetailLogic.emptySeasonsText(detail),
                        size = TvDetailSpec.META_SIZE,
                        color = TvColors.Dim,
                        line = TvDetailSpec.META_LINE,
                        fontStyle = if (state.hydrating) FontStyle.Italic else FontStyle.Normal,
                        maxLines = 2,
                        modifier = Modifier.padding(bottom = dims.dp(TvDetailSpec.META_MB)),
                    )
                }
            }

            // ------------------------------------------------ benzer yapımlar
            if (similar.isNotEmpty()) {
                Spacer(Modifier.height(dims.dp(TvDetailSpec.SIMILAR_MT)))
                Box(Modifier.fillMaxWidth().height(hairline).background(Color(0x1FFFFFFF)))
                val mem = focus.memory[TvDetailSection.Similar] ?: 0
                TvRow(
                    model = TvRowModel("similar", "Benzer Yapımlar", TvRowKind.Cards, similar, null, 0),
                    focusedCol = if (focus.section == TvDetailSection.Similar) focus.index else null,
                    memCol = mem,
                    imagesOn = true,
                )
            }
            Spacer(Modifier.height(dims.dp(60)))
        }
    }

    textModal?.let { (title, body) ->
        TvTextModal(title = title, body = body, onClose = { textModal = null })
    }
}

@Composable
private fun BrowserTitle(text: String) {
    val dims = LocalTvDims.current
    Box(Modifier.height(dims.dp(TvDetailSpec.BR_TITLE_H)), contentAlignment = Alignment.CenterStart) {
        TvText(text, size = TvDetailSpec.BR_TITLE_SIZE, weight = FontWeight.Bold)
    }
}

// ------------------------------------------------------------------------------------------ hero gövdesi

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TvDetailBody(
    modifier: Modifier,
    detail: Detail,
    hydratingNote: Boolean,
    actions: List<TvActionSpec>,
    focusSection: TvDetailSection,
    focusIndex: Int,
    onActionsY: (Int) -> Unit,
    onOverviewY: (Int) -> Unit,
) {
    val dims = LocalTvDims.current
    val item = detail.item
    Column(modifier) {
        TvText(
            item.title,
            size = TvDetailSpec.TITLE_SIZE,
            weight = FontWeight.Black,
            line = TvDetailSpec.TITLE_LINE,
            maxLines = 2,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(dims.dp(TvDetailSpec.TITLE_MB)))

        // meta satırı: yıl · tür · ülke · süre · takipçi · Puan
        val bits = remember(detail) { TvDetailLogic.metaBits(detail) }
        val score = remember(detail) { TvDetailLogic.scoreText(detail) }
        if (bits.isNotEmpty() || score != null) {
            Row(Modifier.padding(bottom = dims.dp(TvDetailSpec.META_MB)), verticalAlignment = Alignment.CenterVertically) {
                bits.forEachIndexed { i, bit ->
                    if (i > 0) MetaDot()
                    TvText(bit, size = TvDetailSpec.META_SIZE, color = TvColors.Dim, line = TvDetailSpec.META_LINE)
                }
                if (score != null) {
                    if (bits.isNotEmpty()) MetaDot()
                    TvText(score, size = TvDetailSpec.META_SIZE, color = TvColors.Accent, weight = FontWeight.Bold, line = TvDetailSpec.META_LINE)
                }
            }
        }
        TvDetailLogic.availabilityNote(detail)?.let {
            TvText(
                it,
                size = TvDetailSpec.META_SIZE,
                color = TvColors.Dim,
                line = TvDetailSpec.META_LINE,
                modifier = Modifier.padding(bottom = dims.dp(TvDetailSpec.META_MB)),
            )
        }
        if (hydratingNote) {
            TvText(
                TvDetailLogic.HYDRATING_TEXT,
                size = TvDetailSpec.META_SIZE,
                color = TvColors.Dim,
                line = TvDetailSpec.META_LINE,
                fontStyle = FontStyle.Italic,
                modifier = Modifier.padding(bottom = dims.dp(TvDetailSpec.META_MB)),
            )
        }

        // eylem düğmeleri: içeriğe göre genişler (en az 200 px), etiket ASLA "…" ile kesilmez; 1000 px gövdeye sığmazsa
        // (4 düğme: "Devam Et · S04 B05" + "İlk Bölümden Başla" + "Fragman yok" + "Listeme Ekle") ikinci satıra sarar.
        FlowRow(
            Modifier
                .padding(bottom = dims.dp(TvDetailSpec.ACTIONS_MB))
                .allowWidthUpTo(dims.pxInt(TvDetailSpec.ACTIONS_MAX_W))
                .onPlacedY(onActionsY),
            horizontalArrangement = Arrangement.spacedBy(dims.dp(TvDetailSpec.BTN_GAP)),
            verticalArrangement = Arrangement.spacedBy(dims.dp(TvDetailSpec.BTN_ROW_GAP)),
        ) {
            actions.forEachIndexed { i, spec ->
                TvButtonFace(
                    label = spec.label,
                    focused = focusSection == TvDetailSection.Actions && focusIndex == i,
                    primary = spec.primary,
                    enabled = !spec.disabled,
                )
            }
        }

        // özet: 4 satır; odaklanabilir (Tamam -> tam metin penceresi)
        val overview = item.overview
        if (overview.isNotBlank()) {
            val focused = focusSection == TvDetailSection.Overview
            Column(Modifier.onPlacedY(onOverviewY)) {
                TvOverview(overview, focused)
                Box(
                    Modifier
                        .padding(start = dims.dp(TvDetailSpec.MORE_ML), top = dims.dp(TvDetailSpec.MORE_MT), bottom = dims.dp(TvDetailSpec.MORE_MB))
                        .alpha(if (focused) 1f else 0f),
                ) {
                    TvText(TvDetailLogic.MORE_TEXT, size = TvDetailSpec.MORE_SIZE, color = TvColors.Accent)
                }
            }
        }

        TvDetailLogic.credits(detail)?.let {
            TvText(it, size = TvDetailSpec.CREDITS_SIZE, color = TvColors.Dim, maxLines = 2, modifier = Modifier.fillMaxWidth())
        }
    }
}

@Composable
private fun MetaDot() {
    val dims = LocalTvDims.current
    TvText("·", size = TvDetailSpec.META_SIZE, color = Color(0xFF555555), modifier = Modifier.padding(horizontal = dims.dp(TvDetailSpec.META_DOT_MARGIN)))
}

/** `.detail-overview`: odakta sarı dış çerçeve (outline 4 px, kutunun DIŞINDA) + hafif zemin + beyaz yazı. */
@Composable
private fun TvOverview(text: String, focused: Boolean) {
    val dims = LocalTvDims.current
    val radius = dims.px(4f)
    val stroke = dims.px(TvDetailSpec.OVERVIEW_OUTLINE)
    Box(
        Modifier
            .widthIn(max = dims.dp(TvDetailSpec.OVERVIEW_MAX_W))
            .drawBehind {
                if (focused) {
                    drawRoundRect(Color(0x0FFFFFFF), cornerRadius = CornerRadius(radius))
                    drawRoundRect(
                        TvColors.Accent,
                        topLeft = Offset(-stroke / 2f, -stroke / 2f),
                        size = Size(size.width + stroke, size.height + stroke),
                        cornerRadius = CornerRadius(radius + stroke / 2f),
                        style = Stroke(width = stroke),
                    )
                }
            }
            .padding(horizontal = dims.dp(TvDetailSpec.OVERVIEW_PAD_H), vertical = dims.dp(TvDetailSpec.OVERVIEW_PAD_V)),
    ) {
        TvText(
            text,
            size = TvDetailSpec.OVERVIEW_SIZE,
            color = if (focused) Color.White else TvColors.Overview,
            line = TvDetailSpec.OVERVIEW_LINE,
            maxLines = TvDetailSpec.OVERVIEW_LINES,
        )
    }
}

// ------------------------------------------------------------------------------------------ sezon satırı

@Composable
private fun TvSeasonRow(season: Season, focused: Boolean, active: Boolean, modifier: Modifier) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    val bg by animateColorAsState(
        when {
            focused -> TvColors.Accent
            active -> TvColors.AccentSoft
            else -> TvColors.Surface
        },
        tween(280),
        label = "tvSeasonBg",
    )
    val bar = if (focused || active) TvColors.Accent else Color.Transparent
    val shape = RoundedCornerShape(dims.dp(4))
    val poster = remember(base, season) {
        if (TvDetailLogic.hasSeasonPoster(season)) UrlUtil.sizedTo(base, season.posterUrl, 200, 300) else ""
    }
    Row(
        modifier
            .fillMaxWidth()
            .height(dims.dp(TvDetailSpec.SEASON_ROW))
            .clip(shape)
            .background(bg, shape)
            .drawBehind { drawRect(bar, size = Size(dims.px(TvDetailSpec.SEASON_ACCENT_BAR), size.height)) }
            .padding(start = dims.dp(TvDetailSpec.SEASON_ACCENT_BAR + 6f), top = dims.dp(6), bottom = dims.dp(6), end = dims.dp(16)),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (poster.isNotEmpty()) {
            Box(
                Modifier
                    .size(dims.dp(TvDetailSpec.SEASON_POSTER_W), dims.dp(TvDetailSpec.SEASON_POSTER_H))
                    .clip(RoundedCornerShape(dims.dp(3)))
                    .background(Brush.linearGradient(listOf(Color(0xFF232323), Color(0xFF2E2E2E)))),
            ) {
                TvImage(poster, dims.pxInt(TvDetailSpec.SEASON_POSTER_W), dims.pxInt(TvDetailSpec.SEASON_POSTER_H), Modifier.fillMaxSize(), rgb565 = true, fadeMs = 200)
            }
            Spacer(Modifier.width(dims.dp(18)))
        } else {
            Spacer(Modifier.width(dims.dp(12)))
        }
        Column(Modifier.weight(1f)) {
            TvText(
                DetailLogic.seasonLabel(season),
                size = TvDetailSpec.SEASON_NAME,
                color = if (focused) TvColors.Ink else Color.White,
                weight = FontWeight.Bold,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(dims.dp(4)))
            TvText(
                TvDetailLogic.seasonCountText(season),
                size = TvDetailSpec.SEASON_COUNT,
                color = when {
                    focused -> Color(0xFF3A3A3A)
                    active -> Color.White
                    else -> TvColors.Dim
                },
            )
        }
    }
}

// ------------------------------------------------------------------------------------------ bölüm satırı

@Composable
private fun TvEpisodeRow(episode: Episode, index: Int, today: String, focused: Boolean, modifier: Modifier) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    val state = remember(episode, today) { DetailLogic.episodeState(episode, today) }
    val shape = RoundedCornerShape(dims.dp(4))
    val outline = dims.px(4f)
    Row(
        modifier
            .fillMaxWidth()
            .height(dims.dp(TvDetailSpec.EP_ROW))
            .clip(shape)
            .background(if (focused) TvColors.Surface2 else TvColors.Surface, shape)
            .drawBehind {
                if (focused) {
                    drawRoundRect(
                        TvColors.Accent,
                        topLeft = Offset(outline / 2f, outline / 2f),
                        size = Size(size.width - outline, size.height - outline),
                        cornerRadius = CornerRadius(dims.px(4f) - outline / 2f),
                        style = Stroke(width = outline),
                    )
                }
            }
            .padding(dims.dp(TvDetailSpec.EP_PAD)),
    ) {
        // still 240x135 (has_still=false: gerçek görsel yok -> yerel yer tutucu)
        var failed by remember(episode.id) { mutableStateOf(false) }
        val stillUrl = remember(base, episode) {
            if (!episode.hasStill) "" else UrlUtil.sizedTo(base, episode.stillPath, 320, 180)
        }
        Box(
            Modifier
                .size(dims.dp(TvDetailSpec.EP_STILL_W), dims.dp(TvDetailSpec.EP_STILL_H))
                .clip(RoundedCornerShape(dims.dp(4)))
                .background(Brush.linearGradient(listOf(Color(0xFF232323), Color(0xFF2E2E2E)))),
            contentAlignment = Alignment.Center,
        ) {
            if (stillUrl.isNotEmpty() && !failed) {
                TvImage(
                    stillUrl,
                    dims.pxInt(TvDetailSpec.EP_STILL_W),
                    dims.pxInt(TvDetailSpec.EP_STILL_H),
                    Modifier.fillMaxSize(),
                    rgb565 = true,
                    fadeMs = 200,
                    onFailed = { failed = true },
                )
            } else {
                TvText("▶", size = 40f, color = Color(0x29FFFFFF), align = TextAlign.Center)
            }
            val pct = episode.progress?.pct ?: 0.0
            if (pct > 0.0) {
                Box(
                    Modifier
                        .align(Alignment.BottomStart)
                        .fillMaxWidth()
                        .height(dims.dp(6))
                        .background(Color(0x40FFFFFF)),
                ) {
                    Box(Modifier.fillMaxHeight().fillMaxWidth((pct / 100.0).toFloat().coerceIn(0f, 1f)).background(TvColors.Accent))
                }
            }
        }
        Spacer(Modifier.width(dims.dp(24)))
        Column(Modifier.weight(1f)) {
            Row(Modifier.padding(bottom = dims.dp(6)), verticalAlignment = Alignment.CenterVertically) {
                TvText(
                    TvDetailLogic.episodeNumber(episode, index),
                    size = TvDetailSpec.EP_NUM,
                    color = TvColors.Dim,
                    weight = FontWeight.Black,
                    modifier = Modifier.padding(end = dims.dp(16)),
                )
                TvText(
                    episode.title,
                    size = TvDetailSpec.EP_TITLE,
                    color = if (state == EpisodeState.Unaired) Color(0xFFC8C8C8) else Color.White,
                    weight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                val flag = TvDetailLogic.episodeFlag(episode, state)
                if (flag != null) {
                    val (bg, ink) = when (flag.kind) {
                        TvEpisodeFlagKind.Soon -> TvColors.Accent to TvColors.Ink
                        TvEpisodeFlagKind.Off -> Color(0xFF3A3A3A) to Color(0xFFDDDDDD)
                        TvEpisodeFlagKind.Check -> Color(0xFF5A4A12) to Color(0xFFF0D98A)
                    }
                    TvText(
                        flag.text,
                        size = TvDetailSpec.EP_FLAG,
                        color = ink,
                        weight = FontWeight.ExtraBold,
                        modifier = Modifier
                            .padding(start = dims.dp(16))
                            .background(bg, RoundedCornerShape(dims.dp(3)))
                            .padding(horizontal = dims.dp(12), vertical = dims.dp(2)),
                    )
                }
            }
            val overview = remember(episode) { TvDetailLogic.episodeOverview(episode) }
            if (overview.isNotEmpty()) {
                TvText(overview, size = TvDetailSpec.EP_OV, color = TvColors.Dim, line = TvDetailSpec.EP_OV_LINE, maxLines = 2, modifier = Modifier.fillMaxWidth())
            }
            val meta = TvDetailLogic.episodeMeta(episode, state)
            if (meta != null) {
                Spacer(Modifier.height(dims.dp(6)))
                TvText(meta, size = TvDetailSpec.EP_META, color = Color(0xFF8F8F8F))
            }
        }
    }
}
