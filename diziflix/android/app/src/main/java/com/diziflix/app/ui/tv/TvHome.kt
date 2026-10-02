package com.diziflix.app.ui.tv

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.layout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.zIndex
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.CatalogLogic
import com.diziflix.app.domain.ContinueLogic
import com.diziflix.app.domain.TvBrandSpec
import com.diziflix.app.domain.LayoutLogic
import com.diziflix.app.domain.LongPressTracker
import com.diziflix.app.domain.PlayerKeys
import com.diziflix.app.domain.TvCardLogic
import com.diziflix.app.domain.TvDir
import com.diziflix.app.domain.TvFocusExpansion
import com.diziflix.app.domain.TvFocusMode
import com.diziflix.app.domain.TvHomeNavigation
import com.diziflix.app.domain.TvHomeScroll
import com.diziflix.app.domain.TvMove
import com.diziflix.app.domain.TvRowMetrics
import com.diziflix.app.domain.TvSpec
import com.diziflix.app.ui.common.LocalBaseUrl
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.home.HomeState
import com.diziflix.app.ui.home.HomeViewModel
import com.diziflix.app.ui.nav.Routes
import kotlinx.coroutines.delay
import kotlin.math.roundToInt

/*
 * Android TV ANA SAYFASI — Tizen istemcisinin (css/home.css, js/screens/home.js, ui/{hero,row,card}.js, js/nav.js) birebir
 * karşılığı. Tablet `HomeScreen`'inden AYRI bir ekrandır (yalnızca ViewModel/veri katmanı ortak).
 *
 * Mimari: Tizen `nav.js` gibi SANAL odak. Tek bir Compose odak hedefi (kök) tuşları alır; "hangi satır/sütun odakta" durumu
 * [TvHomeFocus]'ta tutulur ve saf mantıkla ([TvHomeNavigation], [TvHomeScroll]) güncellenir. Kaydırma Compose'un lazy/scroll
 * mekanizmasına DEĞİL, açık `translationY`/`translationX` animasyonuna bağlıdır -> odak değişince "Compose'un kendi
 * bringIntoView kaydırması" ile çakışma olmaz (hero'nun yukarı kaymadan geri gelmemesi hatası bu yüzden yapısal olarak yok):
 *  - hero ya da üst menü odaktayken sayfa y=0 (hero TAM görünür);
 *  - satır odaktayken o satır sabit üst yuvaya (sayfa y=140) oturur;
 *  - yatay: odaktaki kart ikinci yuvada durur, solundaki kart tam görünür.
 */

// ------------------------------------------------------------------------------------------ odak durumu

/** Sanal odak: satır kimliği + sütun + sütun hafızası; [suspended]: odak üst menüde (kartlarda odak halkası yok, sayfa y=0). */
@Stable
class TvHomeFocus {
    var rowId by mutableStateOf<String?>(null)
    var col by mutableIntStateOf(0)
    var rowIndex by mutableIntStateOf(0)
    var suspended by mutableStateOf(false)
    val memory = mutableStateMapOf<String, Int>()

    /** Odak halkası/genişlemesi gösterilen satır (üst menüdeyken null). */
    val activeRow: String? get() = if (suspended) null else rowId

    fun set(id: String, column: Int, index: Int) {
        rowId = id
        col = column
        rowIndex = index
        memory[id] = column
        suspended = false
    }

    companion object {
        private const val SEP = "\u0001"

        val Saver: Saver<TvHomeFocus, List<Any>> = Saver(
            save = { f ->
                listOf<Any>(f.rowId ?: "", f.col, f.rowIndex, f.suspended) + f.memory.map { "${it.key}$SEP${it.value}" }
            },
            restore = { list ->
                TvHomeFocus().also { f ->
                    f.rowId = (list[0] as String).ifEmpty { null }
                    f.col = list[1] as Int
                    f.rowIndex = list[2] as Int
                    f.suspended = list[3] as Boolean
                    for (i in 4 until list.size) {
                        val parts = (list[i] as String).split(SEP)
                        if (parts.size == 2) parts[1].toIntOrNull()?.let { f.memory[parts[0]] = it }
                    }
                }
            },
        )
    }
}

// ------------------------------------------------------------------------------------------ ekran

@Composable
fun TvHomeScreen(
    profileId: String,
    onOpenDetail: (itemId: String, episodeId: String?) -> Unit,
    onOpenTab: (route: String) -> Unit,
    onOpenSettings: () -> Unit,
    onSwitchProfile: () -> Unit,
    topBarFocus: FocusRequester,
    barFocus: TvBarFocus? = null,
    onMessage: (String) -> Unit = {},
) {
    val container = LocalContainer.current
    val vm = containerViewModel(key = "home:$profileId") { HomeViewModel(it, profileId) }
    val state by vm.state.collectAsStateWithLifecycle()
    OnResumeEffect { vm.onResume() }
    val currentOnMessage by rememberUpdatedState(onMessage)
    LaunchedEffect(vm) { vm.messages.collect { currentOnMessage(it) } }
    RefreshWhenBackOnline(container, isStale = vm::needsRefresh, refresh = vm::refreshAfterReconnect)

    TvTheme {
        Box(Modifier.fillMaxSize().background(TvColors.Background)) {
            val s = state
            when {
                s.loading && s.rows.isEmpty() && s.heroes.isEmpty() -> TvHomeSkeleton()
                s.error != null -> TvHomeError(
                    message = s.error,
                    onRetry = vm::retry,
                    onSettings = onOpenSettings,
                    onSwitchProfile = onSwitchProfile,
                )
                else -> TvHomeContent(
                    state = s,
                    topBarFocus = topBarFocus,
                    barFocus = barFocus,
                    onNeedRows = vm::ensureRowsLoaded,
                    onRetryRow = vm::retryRow,
                    onRemoveContinue = vm::removeFromContinue,
                    onOpenDetail = onOpenDetail,
                    onOpenTab = onOpenTab,
                )
            }
            TvHomeNotes(s)
        }
    }
}

/** Sunucu/önbellek bildirimleri: Tizen `.banner` (üst sarı şerit) ve `.offline-note` (sol-alt). */
@Composable
private fun BoxScope.TvHomeNotes(state: HomeState) {
    val dims = LocalTvDims.current
    val notice = state.notice
    if (notice != null) {
        Box(
            Modifier.align(Alignment.TopStart).fillMaxWidth().height(dims.dp(56)).background(TvColors.Accent),
            contentAlignment = Alignment.Center,
        ) {
            TvText(notice, size = 22f, color = TvColors.Ink, weight = FontWeight.Bold, align = TextAlign.Center)
        }
    }
    val offline = state.offline
    if (offline != null) {
        val label = remember(offline) { offline.label(System.currentTimeMillis()) }
        TvText(
            label,
            size = 22f,
            color = Color(0xFFF0D98A),
            weight = FontWeight.Bold,
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(start = dims.dp(TvSpec.SAFE), bottom = dims.dp(40))
                .background(Color(0xFF5A4A12), RoundedCornerShape(dims.dp(6)))
                .padding(horizontal = dims.dp(20), vertical = dims.dp(8)),
        )
    }
}

// ------------------------------------------------------------------------------------------ hata / iskelet

/** Tizen `.errscreen`: büyük wordmark + başlık + açıklama + düğmeler. */
@Composable
private fun TvHomeError(message: String, onRetry: () -> Unit, onSettings: () -> Unit, onSwitchProfile: () -> Unit) {
    val dims = LocalTvDims.current
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { first.requestFocusWhenReady() }
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        TvMascotError()
        Spacer(Modifier.height(dims.dp(TvBrandSpec.MASCOT_ERR_MB)))
        TvText("DIZIFLIX", size = 56f, color = TvColors.Accent, weight = FontWeight.Black, letterSpacingEm = 0.26f)
        Spacer(Modifier.height(dims.dp(40)))
        TvText("İçerik yüklenemedi", size = 48f, weight = FontWeight.Bold, align = TextAlign.Center)
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
            TvButton("Tekrar dene", onRetry, primary = true, focusRequester = first)
            TvButton("Adresi değiştir", onSettings)
            TvButton("Profil değiştir", onSwitchProfile)
        }
    }
}

/**
 * Anında çizilen iskelet (Tizen `skeleton.home`): hero + 5 shimmer poster satırı; odaklanamaz. Üstünde ortada marka logosu
 * (Tizen `bootLogo()`): veri gelince ana sayfa kurulur ve logo kalkar.
 */
@Composable
private fun TvHomeSkeleton() {
    val dims = LocalTvDims.current
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().clipToBounds().wrapContentHeight(Alignment.Top, unbounded = true)) {
            TvShimmer(Modifier.fillMaxWidth().height(dims.dp(TvSpec.HERO_H)))
            Spacer(Modifier.height(dims.dp(TvSpec.HERO_GAP)))
            repeat(5) { index ->
                TvRow(
                    model = TvRowModel("skeleton-$index", "", TvRowKind.Skeleton, emptyList(), null, 6),
                    focusedCol = null,
                    memCol = 0,
                    imagesOn = false,
                )
            }
        }
        TvBootLogo(Modifier.align(Alignment.Center))
    }
}

// ------------------------------------------------------------------------------------------ içerik

private sealed interface TvTarget {
    data class Hero(val item: Item) : TvTarget
    data class Card(val row: TvRowModel, val item: Item) : TvTarget
    data class End(val action: TvEndAction) : TvTarget
    data class Retry(val row: TvRowModel) : TvTarget
}

private class TvMenuState(val item: Item, val held: Boolean)

@Composable
private fun TvHomeContent(
    state: HomeState,
    topBarFocus: FocusRequester,
    barFocus: TvBarFocus?,
    onNeedRows: (Int) -> Unit,
    onRetryRow: (String) -> Unit,
    onRemoveContinue: (Item) -> Unit,
    onOpenDetail: (String, String?) -> Unit,
    onOpenTab: (String) -> Unit,
) {
    val dims = LocalTvDims.current
    val heroes = state.heroes
    val hasHero = heroes.isNotEmpty()
    val models = remember(state.rows) { TvHomeModel.build(state.rows) }
    val navRows = remember(models, hasHero) { TvHomeModel.navRows(models, hasHero) }
    val rowIds = remember(models) { models.map { it.id } }

    val focus = rememberSaveable(saver = TvHomeFocus.Saver) { TvHomeFocus() }
    var heroIndex by rememberSaveable { mutableIntStateOf(0) }
    val heroPos = if (heroes.isEmpty()) 0 else heroIndex.coerceIn(0, heroes.lastIndex)
    var menu by remember { mutableStateOf<TvMenuState?>(null) }
    val rootFocus = remember { FocusRequester() }
    val tracker = remember { LongPressTracker() }
    var selectArmed by remember { mutableStateOf(false) }

    // İlk odak (hero varsa hero, yoksa ilk odaklanabilir satır) ve veri değişince (satır kalktı/kısaldı) onarım.
    LaunchedEffect(navRows) {
        val focusable = navRows.filter { it.size > 0 }
        val current = focus.rowId
        if (current == null) {
            TvHomeNavigation.initialRow(navRows)?.let { focus.set(it, 0, 0) }
        } else {
            val fixed = TvHomeNavigation.repair(navRows, current, focus.col, focus.rowIndex)
            if (fixed != null) {
                val index = focusable.indexOfFirst { it.id == fixed.first }.coerceAtLeast(0)
                if (fixed.first != current || fixed.second != focus.col || index != focus.rowIndex) {
                    val wasSuspended = focus.suspended
                    focus.set(fixed.first, fixed.second, index)
                    focus.suspended = wasSuspended
                }
            }
        }
    }

    // Üst menüden içeriğe dönüş (Aşağı) / ekrana yeniden giriş: Tizen gibi ilk satıra, satırın hafızadaki sütunuyla.
    fun resume() {
        val first = TvHomeNavigation.initialRow(navRows)
        if (first != null) focus.set(first, focus.memory[first] ?: 0, 0) else focus.suspended = false
    }

    // Ekrana girişte klavye odağı köke (üst menü bu sırada bileşimden çıkmış/odağı kaybetmiş olabilir). İSTİSNA: ekrandan
    // üst menü öğesiyle (Ayarlar/Listem/arama) ayrılıp Geri ile dönüldüyse odak o öğeye geri gelir (hero'ya/ilk satıra değil).
    LaunchedEffect(Unit) {
        val back = barFocus?.returns?.take(Routes.HOME)
        if (back != null) {
            focus.suspended = true
            barFocus.requester(back).requestFocusWhenReady()
            return@LaunchedEffect
        }
        if (focus.suspended) resume()
        rootFocus.requestFocusWhenReady()
    }

    // Dikey kaydırma hedefi: hero/üst menü -> 0; satır -> sabit üst yuva.
    val targetPx = dims.px(TvHomeScroll.targetY(focus.activeRow, rowIds, hasHero))
    val scrollY = remember { Animatable(targetPx) }
    LaunchedEffect(targetPx) {
        scrollY.animateTo(targetPx, tween(TvSpec.SCROLL_ANIM_MS, easing = TvEase))
    }

    // Kaydırdıkça satır verisi (odak satırı + 2 alt).
    val vmIndex = state.rows.indexOfFirst { it.id == focus.rowId }.coerceAtLeast(0)
    LaunchedEffect(vmIndex, state.rows.size) {
        onNeedRows(LayoutLogic.lastRowToLoad(vmIndex + 1, state.rows.size))
    }

    fun targetAt(rowId: String?, col: Int): TvTarget? {
        if (rowId == null) return null
        if (rowId == TvHomeNavigation.HERO_ID) return heroes.getOrNull(heroPos)?.let { TvTarget.Hero(it) }
        val row = models.firstOrNull { it.id == rowId } ?: return null
        return when (row.kind) {
            TvRowKind.Cards -> row.items.getOrNull(col)?.let { TvTarget.Card(row, it) }
                ?: row.endAction?.takeIf { col == row.items.size }?.let { TvTarget.End(it) }
            TvRowKind.Failed -> TvTarget.Retry(row)
            TvRowKind.Skeleton -> null
        }
    }

    fun activate() {
        when (val target = targetAt(focus.rowId, focus.col)) {
            is TvTarget.Hero -> onOpenDetail(target.item.id, null)
            is TvTarget.Card -> onOpenDetail(target.item.id, CatalogLogic.cardEpisodeId(target.item, target.row.id))
            is TvTarget.End -> onOpenTab(target.action.route)
            is TvTarget.Retry -> onRetryRow(target.row.id)
            null -> Unit
        }
    }

    fun move(dir: TvDir): Boolean {
        val id = focus.rowId
        if (id == null) {
            if (dir == TvDir.Up && topBarFocus.tryFocus()) focus.suspended = true
            return true
        }
        when (val m = TvHomeNavigation.move(navRows, id, focus.col, dir, focus.memory, heroes.size)) {
            is TvMove.To -> {
                val index = navRows.filter { it.size > 0 }.indexOfFirst { it.id == m.rowId }.coerceAtLeast(0)
                focus.set(m.rowId, m.col, index)
            }
            is TvMove.HeroPage -> heroIndex = TvHomeNavigation.heroStep(heroPos, heroes.size, m.step)
            TvMove.ToTopBar -> {
                focus.suspended = true
                if (!topBarFocus.tryFocus()) focus.suspended = false
            }
            TvMove.Stay -> Unit
        }
        return true
    }

    fun onSelect(event: KeyEvent): Boolean {
        val target = targetAt(focus.rowId, focus.col) ?: return true
        val time = event.nativeKeyEvent.eventTime
        val longCapable = target is TvTarget.Card && ContinueLogic.hasRemoveMenu(target.row.id)
        if (!longCapable) {
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
        // "İzlemeye Devam Et": kısa basış = detay, uzun basış (~600 ms) = "Listeden kaldır" penceresi.
        val card = target as TvTarget.Card
        when (event.type) {
            KeyEventType.KeyDown -> {
                if (event.nativeKeyEvent.repeatCount == 0) {
                    tracker.cancel()
                    selectArmed = true
                }
                if (tracker.onDown(time) == LongPressTracker.Result.LongPress && selectArmed) menu = TvMenuState(card.item, held = true)
            }
            KeyEventType.KeyUp -> {
                val armed = selectArmed
                selectArmed = false
                when (tracker.onUp(time)) {
                    LongPressTracker.Result.Click -> if (armed) activate()
                    LongPressTracker.Result.LongPress -> if (armed) menu = TvMenuState(card.item, held = false)
                    LongPressTracker.Result.None -> Unit
                }
            }
        }
        return true
    }

    fun onKey(event: KeyEvent): Boolean {
        if (PlayerKeys.isSelect(event.nativeKeyEvent.keyCode)) return onSelect(event)
        if (event.type != KeyEventType.KeyDown) return false
        val dir = when (event.key) {
            Key.DirectionUp -> TvDir.Up
            Key.DirectionDown -> TvDir.Down
            Key.DirectionLeft -> TvDir.Left
            Key.DirectionRight -> TvDir.Right
            else -> return false
        }
        return move(dir)
    }

    val rowWindow = TvRowMetrics.rowWindow(rowIds.indexOf(focus.rowId).coerceAtLeast(0), models.size)
    val imageRows = run {
        val f = rowIds.indexOf(focus.rowId).coerceAtLeast(0)
        (f - 1)..(f + 1)
    }

    Box(
        Modifier
            .fillMaxSize()
            .clipToBounds()
            .focusRequester(rootFocus)
            .onFocusChanged { if (it.hasFocus && focus.suspended) resume() }
            .onPreviewKeyEvent { onKey(it) }
            .focusable(),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .wrapContentHeight(Alignment.Top, unbounded = true)
                .graphicsLayer { translationY = -scrollY.value },
        ) {
            if (hasHero) {
                TvHeroBlock(heroes = heroes, pos = heroPos, focused = focus.activeRow == TvHomeNavigation.HERO_ID)
                Spacer(Modifier.height(dims.dp(TvSpec.HERO_GAP)))
            }
            models.forEachIndexed { index, model ->
                androidx.compose.runtime.key(model.id) {
                    if (index in rowWindow) {
                        val memCol = focus.memory[model.id] ?: 0
                        TvRow(
                            model = model,
                            focusedCol = if (focus.activeRow == model.id) focus.col else null,
                            memCol = memCol,
                            imagesOn = index in imageRows,
                        )
                    } else {
                        Spacer(Modifier.fillMaxWidth().height(dims.dp(TvSpec.ROW_H)))
                    }
                }
            }
        }
    }

    menu?.let { m ->
        TvModal(
            title = m.item.title,
            message = m.item.episodeLabel,
            buttons = listOf(
                TvModalButton(ContinueLogic.MENU_REMOVE, primary = true) {
                    menu = null
                    onRemoveContinue(m.item)
                },
                TvModalButton(ContinueLogic.MENU_CANCEL) { menu = null },
            ),
            onDismiss = { menu = null },
            swallowHeldKey = m.held,
        )
    }
}

// ------------------------------------------------------------------------------------------ hero

/** Tizen `.hero`: 600 px, tam genişlik backdrop + yatay/dikey koyu gradyan, büyük başlık, meta, özet, sayfa noktaları, odak çerçevesi. */
@Composable
private fun TvHeroBlock(heroes: List<Item>, pos: Int, focused: Boolean) {
    val dims = LocalTvDims.current
    Box(Modifier.fillMaxWidth().height(dims.dp(TvSpec.HERO_H)).background(Color(0xFF0B0B0B))) {
        Crossfade(targetState = pos, modifier = Modifier.fillMaxSize(), animationSpec = tween(200), label = "tvHero") { p ->
            TvHeroPage(heroes[p.coerceIn(0, heroes.lastIndex)])
        }
        if (heroes.size > 1) {
            Row(
                Modifier.align(Alignment.BottomCenter).padding(bottom = dims.dp(30)),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                heroes.indices.forEach { i ->
                    val active = i == pos
                    Box(
                        Modifier
                            .padding(horizontal = dims.dp(6))
                            .size(width = dims.dp(if (active) 38 else 12), height = dims.dp(12))
                            .background(if (active) TvColors.Accent else Color(0x4DFFFFFF), RoundedCornerShape(dims.dp(20))),
                    )
                }
            }
        }
        if (focused) {
            Box(
                Modifier
                    .fillMaxSize()
                    .padding(dims.dp(8))
                    .border(dims.dp(4), TvColors.Accent, RoundedCornerShape(dims.dp(8))),
            )
        }
    }
}

private val HeroFadeHorizontal = Brush.horizontalGradient(
    0f to Color(0xF2141414),       // rgba(20,20,20,.95) 0
    0.32f to Color(0xBF141414),    // .75 @ 614.4 / 1920
    0.62f to Color(0x00141414),    // 0 @ 1190.4 / 1920
)
private val HeroFadeVertical = Brush.verticalGradient(
    0f to Color(0x00141414),
    0.45f to Color(0x00141414),
    0.78f to Color(0x8C141414),    // .55 @ 22% (alttan)
    1f to Color(0xFF141414),
)

@Composable
private fun TvHeroPage(item: Item) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    val url = remember(base, item) {
        UrlUtil.sizedTo(base, if (item.hasBackdrop) item.backdrop else (item.portrait ?: item.backdrop), 1280, 720)
    }
    Box(Modifier.fillMaxSize()) {
        // Hero görseli degrade bantlanmasın diye RGB565 değil; 1280x720 izinli boyut.
        TvImage(url, 1280, 720, Modifier.fillMaxSize(), alignment = Alignment.TopCenter, fadeMs = 200)
        Box(Modifier.fillMaxSize().background(HeroFadeHorizontal))
        Box(Modifier.fillMaxSize().background(HeroFadeVertical))
        Column(
            Modifier
                .align(Alignment.BottomStart)
                .padding(start = dims.dp(TvSpec.SAFE), bottom = dims.dp(96))
                .width(dims.dp(1500)),
        ) {
            TvText(
                item.logoText ?: item.title,
                size = 64f,
                weight = FontWeight.Black,
                line = 67f,
                maxLines = 2,
                modifier = Modifier.width(dims.dp(1400)),
            )
            Spacer(Modifier.height(dims.dp(16)))
            val tagline = item.tagline
            if (!tagline.isNullOrBlank()) {
                TvText(tagline, size = 24f, color = TvColors.Accent, letterSpacingEm = 0.06f)
                Spacer(Modifier.height(dims.dp(14)))
            }
            TvText(TvCardLogic.heroMeta(item), size = 22f, color = TvColors.Dim)
            Spacer(Modifier.height(dims.dp(16)))
            if (item.overview.isNotBlank()) {
                TvText(
                    item.overview,
                    size = 24f,
                    color = TvColors.Overview,
                    line = 33f,
                    maxLines = 2,
                    modifier = Modifier.width(dims.dp(1000)),
                )
            }
            Spacer(Modifier.height(dims.dp(32)))
        }
    }
}

// ------------------------------------------------------------------------------------------ satır

/** Tek ölçüm: genişlik her kare ölçüm aşamasında okunur (animasyonda yeniden kompozisyon yok). */
private fun Modifier.widthPx(provider: () -> Float): Modifier = layout { measurable, constraints ->
    val w = provider().roundToInt().coerceAtLeast(0)
    val placeable = measurable.measure(Constraints(minWidth = w, maxWidth = w, minHeight = 0, maxHeight = constraints.maxHeight))
    layout(w, placeable.height) { placeable.place(0, 0) }
}

/**
 * Tizen `.row`: başlık (30 px/700) + 26 px üst dolgulu görünüm penceresi (yatay şerit). Satır yüksekliği SABİT
 * ([TvSpec.ROW_H]); üstündeki 20 px boşluk, odaktaki satırın başlığını sayfa y=140 yuvasına oturtur.
 */
@Composable
internal fun TvRow(model: TvRowModel, focusedCol: Int?, memCol: Int, imagesOn: Boolean) {
    val dims = LocalTvDims.current
    val count = when (model.kind) {
        TvRowKind.Cards -> model.focusSize
        else -> 0
    }
    val imageRange = remember(memCol, count) { TvRowMetrics.imageWindow(memCol, count) }
    val targetX = dims.px(TvRowMetrics.stripOffset(focusedCol ?: memCol))
    val stripX = remember { Animatable(targetX) }
    LaunchedEffect(targetX) { stripX.animateTo(targetX, tween(TvSpec.SCROLL_ANIM_MS, easing = TvEase)) }

    Column(Modifier.fillMaxWidth().height(dims.dp(TvSpec.ROW_H))) {
        Spacer(Modifier.height(dims.dp(TvSpec.ROW_PAD_TOP)))
        Box(Modifier.height(dims.dp(TvSpec.ROW_TITLE_LINE)).padding(horizontal = dims.dp(TvSpec.SAFE))) {
            TvText(
                model.title,
                size = TvSpec.ROW_TITLE_SIZE,
                color = TvColors.Overview,
                weight = FontWeight.Bold,
                line = TvSpec.ROW_TITLE_LINE,
            )
        }
        Spacer(Modifier.height(dims.dp(TvSpec.ROW_TITLE_MB)))
        Box(Modifier.fillMaxWidth().height(dims.dp(TvSpec.VIEWPORT_H)).clipToBounds()) {
            Row(
                Modifier
                    .wrapContentWidth(Alignment.Start, unbounded = true)
                    .graphicsLayer { translationX = -stripX.value }
                    .padding(start = dims.dp(TvSpec.SAFE), top = dims.dp(TvSpec.VIEWPORT_PAD_TOP)),
                horizontalArrangement = Arrangement.spacedBy(dims.dp(TvSpec.GAP)),
            ) {
                when (model.kind) {
                    TvRowKind.Cards -> {
                        // Görünen pencerenin dışındaki kartlar aynı genişlikte boş yer tutucudur (Tizen: DOM'da durur,
                        // görseli yoktur) -> uzun satırda bileşim + bellek sabit kalır; konumlar değişmez.
                        model.items.forEachIndexed { i, item ->
                            androidx.compose.runtime.key(item.listKey) {
                                if (i in imageRange) {
                                    TvPosterTile(item, focused = focusedCol == i, loadImages = imagesOn)
                                } else {
                                    Spacer(Modifier.size(dims.dp(TvSpec.POSTER_W), dims.dp(1)))
                                }
                            }
                        }
                        val end = model.endAction
                        if (end != null) {
                            if (model.items.size in imageRange) {
                                TvEndTile(end.label, focused = focusedCol == model.items.size)
                            } else {
                                Spacer(Modifier.size(dims.dp(TvSpec.POSTER_W), dims.dp(1)))
                            }
                        }
                    }
                    TvRowKind.Skeleton -> repeat(model.skeletonCount) { TvSkeletonTile() }
                    TvRowKind.Failed -> TvRetryTile(focused = focusedCol == 0)
                }
            }
        }
    }
}

private val CardPlaceholder = Brush.linearGradient(listOf(Color(0xFF232323), Color(0xFF2E2E2E), Color(0xFF1D1D1D)))

/**
 * Poster kutusu (Tizen `.row-tile.poster` + `.card`): dinlenirken 240x360 poster; odakta yatay afişi varsa 640x360'a
 * GENİŞLER (afiş/still + başlık + yıl • ★ puan önizleme; komşular sağa itilir), yoksa 270'e büyür (%112). Altında başlık
 * + "★ Puan 7.2" ya da "S07 B05 · Ad"; sol-altta ilerleme çubuğu; sol-üstte rozet.
 */
@Composable
private fun TvPosterTile(item: Item, focused: Boolean, loadImages: Boolean) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    val posterUrl = remember(base, item) { TvCardLogic.posterArtUrl(base, item) }
    val focusUrl = remember(base, item) { TvCardLogic.focusArtUrl(base, item) }
    val canExpand = focusUrl.isNotEmpty()
    val mode = TvFocusExpansion.posterMode(focused, canExpand)
    val unit = dims.px(1f)
    val tileW = animateFloatAsState(dims.px(TvFocusExpansion.tileWidth(mode)), tween(TvSpec.FOCUS_ANIM_MS, easing = TvEase), label = "tvTileW")
    val scale = animateFloatAsState(TvFocusExpansion.cardScale(mode), tween(TvSpec.FOCUS_ANIM_MS, easing = TvEase), label = "tvTileScale")
    var posterFailed by remember(posterUrl) { mutableStateOf(false) }
    var showFocusArt by remember { mutableStateOf(false) }
    LaunchedEffect(mode) {
        if (mode == TvFocusMode.Expanded) {
            showFocusArt = true
        } else if (showFocusArt) {
            delay(TvSpec.FOCUS_ANIM_MS + 60L)
            showFocusArt = false
        }
    }
    val cardShape = RoundedCornerShape(dims.dp(TvSpec.CARD_RADIUS))
    val expandFraction = { if (canExpand) TvFocusExpansion.expandFraction(tileW.value / unit) else 0f }

    Column(Modifier.widthPx { tileW.value }.zIndex(if (focused) 3f else 0f)) {
        Box(
            Modifier
                .widthPx { if (canExpand) tileW.value else dims.px(TvSpec.POSTER_W) }
                .height(dims.dp(TvSpec.POSTER_H))
                .graphicsLayer {
                    scaleX = scale.value
                    scaleY = scale.value
                    transformOrigin = TransformOrigin(0f, 0.5f)
                }
                .clip(cardShape)
                .background(CardPlaceholder),
        ) {
            if (loadImages && posterUrl.isNotEmpty() && !posterFailed) {
                TvImage(
                    url = posterUrl,
                    widthPx = dims.pxInt(TvSpec.POSTER_W),
                    heightPx = dims.pxInt(TvSpec.POSTER_H),
                    rgb565 = true,
                    onFailed = { posterFailed = true },
                    modifier = Modifier.fillMaxSize().graphicsLayer { alpha = 1f - expandFraction() },
                )
            }
            if (posterUrl.isEmpty() || posterFailed) {
                TvText(
                    item.title,
                    size = 22f,
                    color = Color(0xFFD5D5D5),
                    weight = FontWeight.Bold,
                    line = 25f,
                    maxLines = 4,
                    modifier = Modifier.align(Alignment.BottomStart).padding(dims.dp(14)),
                )
            }
            if (canExpand && loadImages && showFocusArt) {
                TvImage(
                    url = focusUrl,
                    widthPx = dims.pxInt(TvSpec.FOCUS_W),
                    heightPx = dims.pxInt(TvSpec.FOCUS_H),
                    rgb565 = true,
                    modifier = Modifier.fillMaxSize().graphicsLayer { alpha = expandFraction() },
                )
            }
            if (focused && !canExpand) {
                // .card-focus-effect:after — sarı parıltı (125deg)
                Box(Modifier.matchParentSize().drawWithCache { onDrawBehind { drawDiagonalGlow() } })
            }
            if (canExpand) {
                TvPreviewInfo(item, expandFraction)
            }
            val badge = item.badge
            if (!badge.isNullOrBlank()) {
                TvText(
                    badge,
                    size = 18f,
                    color = TvColors.Ink,
                    weight = FontWeight.Black,
                    letterSpacingEm = 0.08f,
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(dims.dp(10))
                        .background(TvColors.Accent, RoundedCornerShape(dims.dp(3)))
                        .padding(horizontal = dims.dp(10), vertical = dims.dp(3)),
                )
            }
            val progress = TvCardLogic.progressFraction(item)
            if (progress > 0f) {
                Box(
                    Modifier
                        .align(Alignment.BottomStart)
                        .fillMaxWidth()
                        .height(dims.dp(TvSpec.PROGRESS_H))
                        .background(Color(0x40FFFFFF)),
                ) {
                    Box(Modifier.fillMaxHeight().fillMaxWidth(progress).background(TvColors.Accent))
                }
            }
            if (focused) {
                Box(Modifier.matchParentSize().border(dims.dp(TvSpec.FOCUS_BORDER), TvColors.Accent, cardShape))
            }
        }
        Spacer(Modifier.height(dims.dp(TvFocusExpansion.captionTop(mode))))
        TvText(
            item.title,
            size = TvSpec.CAPTION_TITLE_SIZE,
            weight = FontWeight.Bold,
            line = TvSpec.CAPTION_TITLE_LINE,
            modifier = Modifier.fillMaxWidth(),
        )
        val subtitle = TvCardLogic.subtitle(item)
        if (subtitle != null) {
            Spacer(Modifier.height(dims.dp(TvSpec.CAPTION_SCORE_MT)))
            TvText(
                subtitle,
                size = TvSpec.CAPTION_SCORE_SIZE,
                color = TvColors.Score,
                line = TvSpec.CAPTION_SCORE_LINE,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

/** Genişlemiş kartın alt önizlemesi: koyu gradyan üstünde başlık + "yıl • ★ puan" (ya da bölüm etiketi). */
@Composable
private fun BoxScope.TvPreviewInfo(item: Item, fraction: () -> Float) {
    val dims = LocalTvDims.current
    val rise = dims.px(8f)
    Column(
        Modifier
            .align(Alignment.BottomStart)
            .fillMaxWidth()
            .graphicsLayer {
                val f = fraction()
                alpha = f
                translationY = (1f - f) * rise
            }
            .background(
                Brush.verticalGradient(
                    0f to Color.Transparent,
                    0.42f to Color(0x94000000),
                    1f to Color(0xEB000000),
                ),
            )
            .padding(start = dims.dp(18), end = dims.dp(18), top = dims.dp(46), bottom = dims.dp(15)),
    ) {
        TvText(item.title, size = 22f, weight = FontWeight.ExtraBold, line = 26f)
        val meta = TvCardLogic.previewMeta(item)
        if (meta != null) {
            Spacer(Modifier.height(dims.dp(3)))
            TvText(meta, size = 17f, color = Color(0xFFE6E6E6), line = 22f)
        }
    }
}

private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawDiagonalGlow() {
    // linear-gradient(125deg, rgba(245,197,24,0) 35%, rgba(245,197,24,.18) 55%, rgba(255,255,255,0) 72%)
    val angle = Math.toRadians(125.0)
    val dx = Math.sin(angle).toFloat()
    val dy = (-Math.cos(angle)).toFloat()
    val length = kotlin.math.abs(size.width * dx) + kotlin.math.abs(size.height * dy)
    val center = Offset(size.width / 2f, size.height / 2f)
    val start = Offset(center.x - dx * length / 2f, center.y - dy * length / 2f)
    val end = Offset(center.x + dx * length / 2f, center.y + dy * length / 2f)
    drawRect(
        Brush.linearGradient(
            0.35f to Color(0x00F5C518),
            0.55f to Color(0x2EF5C518),
            0.72f to Color(0x00FFFFFF),
            start = start,
            end = end,
        ),
    )
}

/** Satır sonundaki "Tüm Diziler / Tüm Filmler" kartı (Tizen `.all-items-card`): sarı ok dairesi + etiket. */
@Composable
private fun TvEndTile(label: String, focused: Boolean) {
    val dims = LocalTvDims.current
    val scale = animateFloatAsState(if (focused) TvSpec.FOCUS_POSTER_SCALE else 1f, tween(TvSpec.FOCUS_ANIM_MS, easing = TvEase), label = "tvEndScale")
    val shape = RoundedCornerShape(dims.dp(TvSpec.CARD_RADIUS))
    Box(
        Modifier
            .size(dims.dp(TvSpec.POSTER_W), dims.dp(TvSpec.POSTER_H))
            .zIndex(if (focused) 3f else 0f)
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
                transformOrigin = TransformOrigin(0f, 0.5f)
            }
            .clip(shape)
            .background(
                Brush.linearGradient(
                    if (focused) listOf(Color(0xFF383838), Color(0xFF202020), Color(0xFF111111))
                    else listOf(Color(0xFF292929), Color(0xFF171717), Color(0xFF0C0C0C)),
                ),
            ),
        contentAlignment = Alignment.Center,
    ) {
        // inset:18px; border:2px solid rgba(255,255,255,.12); border-radius:50% (elips)
        val ringWidth = dims.px(2f)
        Box(
            Modifier
                .matchParentSize()
                .padding(dims.dp(18))
                .drawBehind { drawOval(Color(0x1FFFFFFF), style = Stroke(width = ringWidth)) },
        )
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.size(dims.dp(112)).background(TvColors.Accent, CircleShape), contentAlignment = Alignment.Center) {
                TvText("→", size = 72f, color = TvColors.Ink, align = TextAlign.Center)
            }
            Spacer(Modifier.height(dims.dp(30)))
            TvText(
                label,
                size = 28f,
                weight = FontWeight.ExtraBold,
                line = 34f,
                maxLines = 2,
                align = TextAlign.Center,
                overflow = androidx.compose.ui.text.style.TextOverflow.Clip,
                modifier = Modifier.width(dims.dp(190)),
            )
        }
        if (focused) {
            Box(Modifier.matchParentSize().border(dims.dp(TvSpec.FOCUS_BORDER), TvColors.Accent, shape))
        }
    }
}

/** Satır yükleme hatası: odaklanabilir "Tekrar dene" kartı (Tizen `.card-retry`). */
@Composable
private fun TvRetryTile(focused: Boolean) {
    val dims = LocalTvDims.current
    val scale = animateFloatAsState(if (focused) TvSpec.FOCUS_POSTER_SCALE else 1f, tween(TvSpec.FOCUS_ANIM_MS, easing = TvEase), label = "tvRetryScale")
    val shape = RoundedCornerShape(dims.dp(TvSpec.CARD_RADIUS))
    Box(
        Modifier
            .size(dims.dp(TvSpec.CARD_W), dims.dp(TvSpec.CARD_H))
            .zIndex(if (focused) 3f else 0f)
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .clip(shape)
            .background(Brush.linearGradient(listOf(Color(0xFF2A2222), Color(0xFF1D1D1D))))
            .padding(horizontal = dims.dp(18), vertical = dims.dp(14)),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            TvText("Yüklenemedi", size = 26f, weight = FontWeight.ExtraBold, align = TextAlign.Center)
            Spacer(Modifier.height(dims.dp(6)))
            TvText("Bağlantıyı kontrol edin", size = 18f, color = TvColors.Dim, line = 22f, align = TextAlign.Center)
            Spacer(Modifier.height(dims.dp(12)))
            TvText(
                "Tekrar dene",
                size = 22f,
                color = TvColors.Ink,
                weight = FontWeight.ExtraBold,
                modifier = Modifier
                    .background(TvColors.Accent, RoundedCornerShape(dims.dp(4)))
                    .padding(horizontal = dims.dp(22), vertical = dims.dp(6)),
            )
        }
        if (focused) {
            Box(Modifier.matchParentSize().border(dims.dp(TvSpec.FOCUS_BORDER), TvColors.Accent, shape))
        }
    }
}

// ------------------------------------------------------------------------------------------ iskelet

@Composable
private fun TvSkeletonTile() {
    val dims = LocalTvDims.current
    TvShimmer(
        Modifier
            .size(dims.dp(TvSpec.POSTER_W), dims.dp(TvSpec.POSTER_H))
            .clip(RoundedCornerShape(dims.dp(TvSpec.CARD_RADIUS))),
    )
}

/** Tizen `.sk`: #1c1c1c -> #2b2b2b -> #1c1c1c kayan parlama (yalnızca çizim aşamasında). */
@Composable
internal fun TvShimmer(modifier: Modifier) {
    val dims = LocalTvDims.current
    val transition = rememberInfiniteTransition(label = "tvShimmer")
    val phase = transition.animateFloat(
        initialValue = -300f,
        targetValue = 600f,
        animationSpec = infiniteRepeatable(tween(1200, easing = LinearEasing)),
        label = "tvShimmerPhase",
    )
    Box(
        modifier.drawBehind {
            val span = dims.px(600f)
            val start = dims.px(phase.value)
            drawRect(
                Brush.horizontalGradient(
                    0f to Color(0xFF1C1C1C),
                    0.4f to Color(0xFF2B2B2B),
                    0.8f to Color(0xFF1C1C1C),
                    1f to Color(0xFF1C1C1C),
                    startX = start,
                    endX = start + span,
                    tileMode = TileMode.Repeated,
                ),
            )
        },
    )
}
