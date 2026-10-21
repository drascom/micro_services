package com.diziflix.app.ui.tv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.zIndex
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.model.Item
import com.diziflix.app.domain.CatalogLogic
import com.diziflix.app.domain.PlayerKeys
import com.diziflix.app.domain.SearchSourceLogic
import com.diziflix.app.domain.SourceTag
import com.diziflix.app.domain.TvCardLogic
import com.diziflix.app.domain.TvCatalogLogic
import com.diziflix.app.domain.TvCatalogSpec
import com.diziflix.app.domain.TvDir
import com.diziflix.app.domain.TvGridMove
import com.diziflix.app.domain.TvGridPos
import com.diziflix.app.ui.catalog.CatalogUiState
import com.diziflix.app.ui.catalog.CatalogView
import com.diziflix.app.ui.catalog.CatalogViewModel
import com.diziflix.app.ui.catalog.SearchViewModel
import com.diziflix.app.ui.common.LocalBaseUrl
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.containerViewModel
import kotlin.math.roundToInt

/*
 * Android TV KATALOG (Filmler / Diziler / Listem) ve ARAMA — Tizen `css/home.css` (.catalog-*) + `js/screens/catalog.js`
 * karşılığı. Tablet `CatalogScreen` / `SearchScreen`'inden AYRI; ViewModel'ler ortak (filtre, sonsuz kaydırma ve yazarken
 * arama mantığı aynı). Ana sayfa gibi SANAL odak: tek kök odak hedefi tuşları alır; konum [TvGridFocus]'ta, kararlar saf
 * mantıkta ([TvCatalogLogic.move]). Odaktaki satır sabit üst yuvaya (ekranda y=140) oturur.
 *
 * Yerleşim (Tizen px): 60 kenar, başlık 48 px + "N yapım" 26 px; süzgeç düğmeleri (`.btn.small`); 5 sütun 300x450 poster
 * (aralık 60), altında başlık 26 px + "2021 · Dizi" 22 px (+ durum notu 21 px).
 */

// ------------------------------------------------------------------------------------------ odak durumu

@Stable
class TvGridFocus {
    var inFilters by mutableStateOf(false)
    var index by mutableIntStateOf(0)
    var lastGrid by mutableIntStateOf(0)
    var filtersCol by mutableIntStateOf(0)

    /** Odak üst menüde (kartlarda halka yok, liste en üste döner). */
    var suspended by mutableStateOf(false)

    val pos: TvGridPos get() = if (inFilters) TvGridPos.Filters(index) else TvGridPos.Grid(index)

    fun set(pos: TvGridPos) {
        when (pos) {
            is TvGridPos.Filters -> {
                inFilters = true
                index = pos.col
                filtersCol = pos.col
            }
            is TvGridPos.Grid -> {
                inFilters = false
                index = pos.index
                lastGrid = pos.index
            }
        }
        suspended = false
    }

    companion object {
        val Saver: Saver<TvGridFocus, List<Any>> = Saver(
            save = { listOf(it.inFilters, it.index, it.lastGrid, it.filtersCol, it.suspended) },
            restore = { l ->
                TvGridFocus().also {
                    it.inFilters = l[0] as Boolean
                    it.index = l[1] as Int
                    it.lastGrid = l[2] as Int
                    it.filtersCol = l[3] as Int
                    it.suspended = l[4] as Boolean
                }
            },
        )
    }
}

// ------------------------------------------------------------------------------------------ Katalog ekranı

private class FilterPick(
    val title: String,
    val options: List<Pair<String, String>>,
    val selectedId: String?,
    val onPick: (String) -> Unit,
)

@Composable
fun TvCatalogScreen(
    view: CatalogView,
    profileId: String,
    onOpenDetail: (itemId: String) -> Unit,
    barFocus: TvBarFocus,
    route: String,
) {
    val topBarFocus = barFocus.entry
    val vm = containerViewModel(key = "catalog:${view.name}:$profileId") { CatalogViewModel(it, profileId, view) }
    val state by vm.state.collectAsStateWithLifecycle()
    OnResumeEffect { vm.onResume() }
    val s = state
    val focus = rememberSaveable(saver = TvGridFocus.Saver) { TvGridFocus() }
    val rootFocus = remember { FocusRequester() }
    var pick by remember { mutableStateOf<FilterPick?>(null) }

    // Üst menü öğesiyle (Ayarlar/Listem/arama) ayrılıp Geri ile dönüldüyse odak o öğeye geri gelir, ızgaraya değil.
    var entryChecked by remember { mutableStateOf(false) }
    var restoringBar by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        val back = barFocus.returns.take(route)
        if (back != null) {
            restoringBar = true
            focus.suspended = true
            barFocus.requester(back).requestFocusWhenReady()
        }
        entryChecked = true
    }

    val hasItems = s.items.isNotEmpty()
    val blocked = !hasItems && (s.loading || s.error != null)
    val chips = if (blocked) emptyList() else filterLabels(s, view)

    TvTheme {
        Box(Modifier.fillMaxSize().background(TvColors.Background)) {
            TvGridBody(
                title = view.title,
                subtitle = if (s.total > 0 || hasItems) TvCatalogLogic.subtitle(s.total, null) else null,
                items = s.items,
                isSearch = false,
                chips = chips,
                message = when {
                    s.loading && !hasItems -> ({ TvLoadingMessage(TvCatalogLogic.LOADING_TEXT) })
                    s.error != null && !hasItems -> ({ TvErrorMessage(s.error, vm::retry) })
                    !hasItems -> ({
                        TvEmptyMessage(if (view.mine) TvCatalogLogic.MYLIST_EMPTY else TvCatalogLogic.FILTERS_EMPTY)
                    })
                    else -> null
                },
                interactive = hasItems || chips.isNotEmpty(),
                autoFocus = entryChecked && !restoringBar,
                loadingMore = s.loadingMore,
                total = s.total,
                focus = focus,
                rootFocus = rootFocus,
                topBarFocus = topBarFocus,
                onOpen = { onOpenDetail(it.id) },
                onChip = { index -> pick = filterPick(s, vm, view, index) },
                onLoadMore = vm::loadMore,
            )
            pick?.let { p ->
                TvPickModal(
                    title = p.title,
                    options = p.options,
                    selectedId = p.selectedId,
                    onPick = { id ->
                        pick = null
                        p.onPick(id)
                    },
                    onDismiss = { pick = null },
                )
            }
        }
    }
}

private fun filterLabels(s: CatalogUiState, view: CatalogView): List<String> {
    val f = s.filters
    val genreName = s.genres.firstOrNull { it.id == f.genre }?.name ?: "Tümü"
    val availabilityName = CatalogLogic.AVAILABILITY_OPTIONS.firstOrNull { it.first == f.availability }?.second ?: "Tümü"
    val sortName = CatalogLogic.sortOptionsFor(view.defaultSort).firstOrNull { it.first == f.sort }?.second ?: "Yeni eklenen"
    val isFiltered = f.genre.isNotEmpty() || f.year != null || f.availability.isNotEmpty() || f.sort != view.defaultSort
    val list = arrayListOf("Tür: $genreName", "Yıl: " + (f.year?.toString() ?: "Tümü"), availabilityName, "Sıra: $sortName")
    if (isFiltered) list.add("Filtreleri temizle")
    return list
}

/** Süzgeç düğmesi [index] seçilince açılacak pencere (son düğme "Filtreleri temizle" doğrudan uygular -> null options). */
private fun filterPick(s: CatalogUiState, vm: CatalogViewModel, view: CatalogView, index: Int): FilterPick? {
    val f = s.filters
    return when (index) {
        0 -> FilterPick("Tür seçimi", listOf("" to "Tümü") + s.genres.map { it.id to it.name }, f.genre) { vm.setGenre(it) }
        1 -> FilterPick("Yıl seçimi", listOf("" to "Tümü") + s.years.map { it.toString() to it.toString() }, f.year?.toString() ?: "") {
            vm.setYear(it.toIntOrNull())
        }
        2 -> FilterPick("İzlenebilirlik", CatalogLogic.AVAILABILITY_OPTIONS, f.availability) { vm.setAvailability(it) }
        3 -> FilterPick("Sıralama", CatalogLogic.sortOptionsFor(view.defaultSort), f.sort) { vm.setSort(it) }
        else -> {
            vm.clearFilters()
            null
        }
    }
}

// ------------------------------------------------------------------------------------------ Arama ekranı

@Composable
fun TvSearchScreen(
    profileId: String,
    onOpenDetail: (itemId: String) -> Unit,
    bar: TvBarActions,
) {
    val vm = containerViewModel(key = "search:$profileId") { SearchViewModel(it, profileId) }
    val state by vm.state.collectAsStateWithLifecycle()
    val s = state
    val focus = rememberSaveable(saver = TvGridFocus.Saver) { TvGridFocus() }
    val rootFocus = remember { FocusRequester() }
    val inputFocus = remember { FocusRequester() }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current

    // Açılışta arama alanına odak (sistem ekran klavyesini açar); sonuç kartlarına Aşağı ile inilir.
    LaunchedEffect(Unit) { inputFocus.requestFocusWhenReady() }

    val idle = s.query.trim().length < SearchViewModel.MIN_LOCAL
    val hasItems = s.items.isNotEmpty()

    TvTheme {
        Column(Modifier.fillMaxSize().background(TvColors.Background)) {
            TvTopBar(
                selected = TvTopBarTab.Search,
                onHome = bar.onHome,
                onSearch = bar.onSearch,
                onMyList = bar.onMyList,
                onProfile = bar.onProfile,
                onSettings = bar.onSettings,
                searchFocus = inputFocus,
                searchSlot = {
                    TvSearchInputBox(
                        value = s.query,
                        onValueChange = vm::onQueryChange,
                        onSubmit = {
                            vm.submit()
                            // TV: klavyeyi kapat, sonuçlar varsa ilk karta in (yoksa alanda kal).
                            keyboard?.hide()
                            focusManager.moveFocus(FocusDirection.Down)
                        },
                        onClear = {
                            vm.clear()
                            inputFocus.tryFocus()
                        },
                        focusRequester = inputFocus,
                        // yazarken liste başa döner (sonuç kartlarında halka yok); Aşağı ile sonuçlara inilince devam
                        onFocusChange = { if (it) focus.suspended = true },
                    )
                },
            )
            Box(Modifier.weight(1f).fillMaxWidth()) {
                TvGridBody(
                    title = "Arama Sonuçları",
                    subtitle = if (!idle && !s.loading) TvCatalogLogic.subtitle(s.total, s.query.trim(), s.searching, s.note) else null,
                    items = s.items,
                    isSearch = true,
                    chips = emptyList(),
                    message = when {
                        s.error != null && !hasItems -> ({ TvErrorMessage(s.error, vm::submit) })
                        !hasItems && (s.loading || s.searching) -> ({ TvSearchWait() })
                        !hasItems -> ({
                            TvEmptyMessage(
                                when {
                                    idle -> TvCatalogLogic.EMPTY_HINT
                                    s.done -> TvCatalogLogic.SEARCH_EMPTY
                                    else -> ""
                                },
                            )
                        })
                        else -> null
                    },
                    interactive = hasItems,
                    autoFocus = false,
                    loadingMore = false,
                    total = s.total,
                    focus = focus,
                    rootFocus = rootFocus,
                    topBarFocus = inputFocus,
                    onOpen = { onOpenDetail(it.id) },
                    onChip = {},
                    onLoadMore = {},
                    footer = s.failedNote,
                )
            }
        }
    }
}

// ------------------------------------------------------------------------------------------ ortak gövde

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TvGridBody(
    title: String,
    subtitle: String?,
    items: List<Item>,
    isSearch: Boolean,
    chips: List<String>,
    message: (@Composable () -> Unit)?,
    interactive: Boolean,
    autoFocus: Boolean,
    loadingMore: Boolean,
    total: Int,
    focus: TvGridFocus,
    rootFocus: FocusRequester,
    topBarFocus: FocusRequester,
    onOpen: (Item) -> Unit,
    onChip: (Int) -> Unit,
    onLoadMore: () -> Unit,
    /** Izgaranın altında silik tek satır (arama: "Bazı kaynaklar yanıt vermedi: ..."); odaklanmaz. */
    footer: String? = null,
) {
    val dims = LocalTvDims.current
    val u = dims.px(1f)
    val listState = rememberLazyListState()
    val columns = TvCatalogSpec.COLUMNS
    val rows = TvCatalogLogic.rowCount(items.size)
    val filterCount = chips.size
    val headCount = 1 + (if (chips.isNotEmpty()) 1 else 0) + (if (message != null) 1 else 0)
    val filtersItem = 1
    val currentItems by rememberUpdatedState(items)
    val currentChips by rememberUpdatedState(filterCount)

    // geçerli odak konumu: veri değişince (kısaldı/boşaldı/filtre kalktı) toparla; ilk açılışta ilk kart (yoksa süzgeç)
    LaunchedEffect(items.size, filterCount) {
        val pos = focus.pos
        when {
            items.isEmpty() && filterCount > 0 && pos is TvGridPos.Grid -> focus.set(TvGridPos.Filters(focus.filtersCol.coerceIn(0, filterCount - 1)))
            items.isNotEmpty() && filterCount == 0 && pos is TvGridPos.Filters -> focus.set(TvGridPos.Grid(focus.lastGrid.coerceIn(0, items.size - 1)))
            pos is TvGridPos.Grid && items.isNotEmpty() && pos.index > items.lastIndex -> focus.set(TvGridPos.Grid(items.lastIndex))
            pos is TvGridPos.Filters && pos.col > filterCount - 1 && filterCount > 0 -> focus.set(TvGridPos.Filters(filterCount - 1))
        }
    }
    LaunchedEffect(interactive, autoFocus) { if (interactive && autoFocus) rootFocus.requestFocusWhenReady() }

    // odaktaki satır sabit üst yuvaya oturur; üst menüdeyken liste en üste döner
    LaunchedEffect(focus.suspended, focus.inFilters, focus.index / columns, headCount, chips.isNotEmpty()) {
        when {
            focus.suspended -> listState.animateScrollToItem(0)
            focus.inFilters && chips.isNotEmpty() ->
                listState.animateScrollToItem(filtersItem, (TvCatalogLogic.pinOffset(TvCatalogSpec.FILTERS_MT) * u).roundToInt())
            !focus.inFilters && items.isNotEmpty() ->
                listState.animateScrollToItem(headCount + focus.index / columns, (TvCatalogLogic.pinOffset(TvCatalogSpec.ROW_MT) * u).roundToInt())
        }
    }
    // odak listenin sonuna yaklaşınca sonraki sayfa
    LaunchedEffect(focus.index / columns, focus.inFilters, items.size, total) {
        if (!focus.inFilters && TvCatalogLogic.shouldLoadMore(focus.index / columns, rows, items.size, total)) onLoadMore()
    }

    fun resume() {
        val target: TvGridPos = if (chips.isNotEmpty()) {
            TvGridPos.Filters(focus.filtersCol.coerceIn(0, chips.size - 1))
        } else if (items.isNotEmpty()) {
            TvGridPos.Grid(focus.lastGrid.coerceIn(0, items.size - 1))
        } else {
            focus.suspended = false
            return
        }
        focus.set(target)
    }

    fun activate() {
        when (val pos = focus.pos) {
            is TvGridPos.Filters -> if (pos.col < filterCount) onChip(pos.col)
            is TvGridPos.Grid -> currentItems.getOrNull(pos.index)?.let { onOpen(it) }
        }
    }

    var selectArmed by remember { mutableStateOf(false) }
    fun onKey(event: KeyEvent): Boolean {
        if (!interactive) return false
        if (PlayerKeys.isSelect(event.nativeKeyEvent.keyCode)) {
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
        when (val move = TvCatalogLogic.move(focus.pos, dir, currentChips, currentItems.size, focus.lastGrid, focus.filtersCol)) {
            is TvGridMove.To -> focus.set(move.pos)
            TvGridMove.ToTopBar -> {
                focus.suspended = true
                if (!topBarFocus.tryFocus()) focus.suspended = false
            }
            TvGridMove.Stay -> Unit
        }
        return true
    }

    Box(
        Modifier
            .fillMaxSize()
            .focusRequester(rootFocus)
            .onFocusChanged { if (it.hasFocus && focus.suspended) resume() }
            .onPreviewKeyEvent { onKey(it) }
            .then(if (interactive) Modifier.focusable() else Modifier),
    ) {
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(bottom = dims.dp(TvCatalogSpec.PAGE_PAD_BOTTOM)),
        ) {
            item(key = "head") {
                Column(Modifier.padding(start = dims.dp(TvCatalogSpec.PAGE_PAD_H), end = dims.dp(TvCatalogSpec.PAGE_PAD_H), top = dims.dp(TvCatalogSpec.CONTENT_TOP_PAD))) {
                    TvText(title, size = TvCatalogSpec.H1, weight = FontWeight.Bold, line = TvCatalogSpec.H1_LINE)
                    Spacer(Modifier.height(dims.dp(TvCatalogSpec.H1_MB)))
                    // alt satır yer tutar (yüksekliği sabit: yükleme <-> sonuç geçişinde başlık kaymasın)
                    TvText(subtitle.orEmpty().ifEmpty { " " }, size = TvCatalogSpec.SUB, color = TvColors.Dim, line = TvCatalogSpec.SUB_LINE)
                }
            }
            if (chips.isNotEmpty()) {
                item(key = "filters") {
                    FlowRow(
                        Modifier
                            .fillMaxWidth()
                            .padding(horizontal = dims.dp(TvCatalogSpec.PAGE_PAD_H))
                            .padding(top = dims.dp(TvCatalogSpec.FILTERS_MT), bottom = dims.dp(TvCatalogSpec.FILTERS_MB)),
                        horizontalArrangement = Arrangement.spacedBy(dims.dp(TvCatalogSpec.FILTER_GAP)),
                        verticalArrangement = Arrangement.spacedBy(dims.dp(TvCatalogSpec.FILTER_GAP)),
                    ) {
                        chips.forEachIndexed { i, label ->
                            TvButtonFace(label = label, focused = !focus.suspended && focus.inFilters && focus.index == i, small = true)
                        }
                    }
                }
            }
            if (message != null) item(key = "message") { message() }
            items(rows, key = { it }) { r ->
                Row(
                    Modifier.padding(start = dims.dp(TvCatalogSpec.PAGE_PAD_H), top = dims.dp(TvCatalogSpec.ROW_MT), bottom = dims.dp(TvCatalogSpec.ROW_MB)),
                    horizontalArrangement = Arrangement.spacedBy(dims.dp(TvCatalogSpec.GRID_GAP)),
                ) {
                    for (c in 0 until columns) {
                        val i = r * columns + c
                        val item = items.getOrNull(i) ?: break
                        TvCatalogTile(item, isSearch, focused = !focus.suspended && !focus.inFilters && focus.index == i)
                    }
                }
            }
            if (loadingMore) {
                item(key = "more") {
                    Box(Modifier.fillMaxWidth().padding(vertical = dims.dp(24)), contentAlignment = Alignment.Center) {
                        TvSpinner(TvCatalogSpec.SPINNER, TvCatalogSpec.SPINNER_BORDER, periodMs = 800)
                    }
                }
            }
            if (footer != null && items.isNotEmpty()) {
                item(key = "footer") {
                    TvText(
                        footer,
                        size = TvCatalogSpec.FOOTER,
                        color = TvColors.Dim.copy(alpha = 0.6f),
                        line = TvCatalogSpec.FOOTER_LINE,
                        maxLines = 2,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = dims.dp(TvCatalogSpec.PAGE_PAD_H))
                            .padding(top = dims.dp(TvCatalogSpec.FOOTER_MT)),
                    )
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------ durum blokları

@Composable
private fun TvLoadingMessage(text: String) {
    val dims = LocalTvDims.current
    TvText(
        text,
        size = TvCatalogSpec.LOADING_TEXT,
        color = TvColors.Dim,
        modifier = Modifier.padding(horizontal = dims.dp(TvCatalogSpec.PAGE_PAD_H), vertical = dims.dp(TvCatalogSpec.LOADING_PAD_V)),
    )
}

@Composable
private fun TvEmptyMessage(text: String) {
    val dims = LocalTvDims.current
    TvText(
        text,
        size = TvCatalogSpec.LOADING_TEXT,
        color = TvColors.Dim,
        maxLines = 3,
        modifier = Modifier.padding(horizontal = dims.dp(TvCatalogSpec.PAGE_PAD_H), vertical = dims.dp(TvCatalogSpec.EMPTY_PAD_V)),
    )
}

/** `.catalog-loading` hata hali: mesaj + odaklanabilir "Tekrar dene" (küçük düğme). */
@Composable
private fun TvErrorMessage(message: String, onRetry: () -> Unit) {
    val dims = LocalTvDims.current
    val retry = remember { FocusRequester() }
    LaunchedEffect(Unit) { retry.requestFocusWhenReady() }
    Column(Modifier.padding(horizontal = dims.dp(TvCatalogSpec.PAGE_PAD_H), vertical = dims.dp(TvCatalogSpec.LOADING_PAD_V))) {
        TvText(message, size = TvCatalogSpec.LOADING_TEXT, color = TvColors.Dim, maxLines = 3)
        Spacer(Modifier.height(dims.dp(28)))
        TvButton("Tekrar dene", onRetry, small = true, focusRequester = retry)
    }
}

/** `.search-loading`: ortada dönen halka + "Aranıyor…" (kalın, beyaz). */
@Composable
private fun TvSearchWait() {
    val dims = LocalTvDims.current
    Row(
        Modifier.fillMaxWidth().padding(vertical = dims.dp(TvCatalogSpec.LOADING_PAD_V)),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TvSpinner(TvCatalogSpec.SPINNER, TvCatalogSpec.SPINNER_BORDER, periodMs = 800)
        Spacer(Modifier.width(dims.dp(22)))
        TvText(TvCatalogLogic.SEARCH_WAIT, size = TvCatalogSpec.LOADING_TEXT, weight = FontWeight.Bold)
    }
}

// ------------------------------------------------------------------------------------------ kart

private val TilePlaceholder = Brush.linearGradient(listOf(Color(0xFF232323), Color(0xFF2E2E2E), Color(0xFF1D1D1D)))

/** `.catalog-tile`: 300x450 poster (odakta %104 + 7 px sarı çerçeve) + başlık + "yıl · tür" + durum notu. */
@Composable
private fun TvCatalogTile(item: Item, isSearch: Boolean, focused: Boolean) {
    val dims = LocalTvDims.current
    val base = LocalBaseUrl.current
    val scale = animateFloatAsState(if (focused) TvCatalogSpec.FOCUS_SCALE else 1f, tween(220, easing = TvEase), label = "tvCatalogTileScale")
    val shape = RoundedCornerShape(dims.dp(TvCatalogSpec.CARD_RADIUS))
    val url = remember(base, item) { TvCardLogic.posterArtUrl(base, item) }
    var failed by remember(url) { mutableStateOf(false) }
    Column(Modifier.width(dims.dp(TvCatalogSpec.TILE_W)).zIndex(if (focused) 2f else 0f)) {
        Box(
            Modifier
                .size(dims.dp(TvCatalogSpec.TILE_W), dims.dp(TvCatalogSpec.CARD_H))
                .graphicsLayer {
                    scaleX = scale.value
                    scaleY = scale.value
                    transformOrigin = TransformOrigin(0.5f, 0.5f)
                }
                .clip(shape)
                .background(TilePlaceholder),
        ) {
            if (url.isNotEmpty() && !failed) {
                TvImage(
                    url,
                    dims.pxInt(TvCatalogSpec.TILE_W),
                    dims.pxInt(TvCatalogSpec.CARD_H),
                    Modifier.fillMaxSize(),
                    rgb565 = true,
                    fadeMs = 200,
                    onFailed = { failed = true },
                )
            } else {
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
                Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(dims.dp(6)).background(Color(0x40FFFFFF))) {
                    Box(Modifier.fillMaxHeight().fillMaxWidth(progress).background(TvColors.Accent))
                }
            }
            if (focused) Box(Modifier.matchParentSize().border(dims.dp(7), TvColors.Accent, shape))
        }
        TvText(
            item.title,
            size = TvCatalogSpec.TITLE,
            weight = FontWeight.Bold,
            line = TvCatalogSpec.TITLE_LINE,
            modifier = Modifier.padding(top = dims.dp(TvCatalogSpec.TITLE_MT)).fillMaxWidth(),
        )
        TvText(
            TvCatalogLogic.tileMeta(item),
            size = TvCatalogSpec.META,
            color = TvColors.Dim,
            line = TvCatalogSpec.META_LINE,
            modifier = Modifier.padding(top = dims.dp(TvCatalogSpec.META_MT)).fillMaxWidth(),
        )
        val note = CatalogLogic.statusNote(item, isSearch)
        if (note != null) {
            TvText(
                note,
                size = TvCatalogSpec.STATUS,
                color = TvColors.Score,
                line = TvCatalogSpec.STATUS_LINE,
                maxLines = 2,
                modifier = Modifier.padding(top = dims.dp(TvCatalogSpec.STATUS_MT)).fillMaxWidth(),
            )
        }
        if (isSearch) TvSourceTags(item)
    }
}

/** Arama kartının kaynak etiketleri (`source_options`): ilk 2 + "+N"; `broken` uyarı işaretli, `unknown` soluk. Yalnızca bilgi. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TvSourceTags(item: Item) {
    val dims = LocalTvDims.current
    val tags = remember(item.sourceOptions) { SearchSourceLogic.tags(item.sourceOptions) }
    if (tags.isEmpty()) return
    FlowRow(
        Modifier.padding(top = dims.dp(TvCatalogSpec.TAG_MT)).fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(dims.dp(TvCatalogSpec.TAG_GAP)),
        verticalArrangement = Arrangement.spacedBy(dims.dp(TvCatalogSpec.TAG_GAP)),
    ) {
        tags.forEach { tag ->
            val color = when (tag.kind) {
                SourceTag.Kind.Warn -> TvColors.Accent
                SourceTag.Kind.Faint -> TvColors.Dim.copy(alpha = 0.55f)
                SourceTag.Kind.More -> TvColors.Dim
                SourceTag.Kind.Normal -> TvColors.Foreground
            }
            TvText(
                tag.text,
                size = TvCatalogSpec.TAG,
                color = color,
                line = TvCatalogSpec.TAG_LINE,
                modifier = Modifier
                    .background(TvColors.Surface2, RoundedCornerShape(dims.dp(4)))
                    .padding(horizontal = dims.dp(TvCatalogSpec.TAG_PAD_H), vertical = dims.dp(TvCatalogSpec.TAG_PAD_V)),
            )
        }
    }
}
