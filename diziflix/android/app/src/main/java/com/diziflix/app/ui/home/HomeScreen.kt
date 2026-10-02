package com.diziflix.app.ui.home

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.PagerState
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.BootResponse
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.CatalogLogic
import com.diziflix.app.domain.ContinueLogic
import com.diziflix.app.domain.ContinueRemover
import com.diziflix.app.domain.RemoveOutcome
import com.diziflix.app.domain.LayoutLogic
import com.diziflix.app.domain.OfflineNotice
import com.diziflix.app.ui.common.ErrorBox
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.LoadingBox
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.OfflineBanner
import com.diziflix.app.ui.common.PosterRow
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.RemoteImage
import com.diziflix.app.ui.common.SectionHeader
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.nav.Routes
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.Locale

@Immutable
data class HomeRow(
    val id: String,
    val title: String,
    val items: List<Item> = emptyList(),
    val loaded: Boolean = false,
    val loading: Boolean = false,
    val failed: Boolean = false,
    val count: Int? = null,
    val total: Int? = null,
)

@Immutable
data class HomeState(
    val loading: Boolean = true,
    val error: String? = null,
    val heroes: List<Item> = emptyList(),
    val rows: List<HomeRow> = emptyList(),
    /** Bağlantı dışı bir nedenle taze veri alınamadıysa (önbellek gösteriliyor) ince uyarı. */
    val notice: String? = null,
    /** Önbellekten gösterilirken ağ yoksa/sunucuya ulaşılamıyorsa üstteki "Çevrimdışı" şeridi. */
    val offline: OfflineNotice? = null,
)

class HomeViewModel(private val container: AppContainer, private val profileId: String) : ViewModel() {
    private val repo = container.repository

    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state.asStateFlow()

    /** Kısa bildirimler (Snackbar): "Listeden kaldırıldı" / hata. Ekran dinlemiyorsa düşer. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    private val continueRemover = ContinueRemover(repo) { container.connectivity.online.value }

    /** Şu an kaldırılmakta olan yapım kimlikleri (çift dokunuşta ikinci istek gitmesin). */
    private val removing = HashSet<String>()

    private var started = false
    private var loadJob: Job? = null

    /** Aynı anda en çok [MAX_PARALLEL_ROWS] satır isteği: zayıf cihazda/sunucuda istek fırtınası olmasın. */
    private val rowGate = Semaphore(MAX_PARALLEL_ROWS)

    /** Ekran her öne geldiğinde: ilkinde yükler, sonrakilerde (detay/oynatıcıdan dönüş) sessizce tazeler. */
    fun onResume() {
        if (!started) {
            started = true
            load(initial = true)
        } else {
            load(initial = false)
        }
    }

    fun retry() = load(initial = true)

    /** Önbellekten/çevrimdışı gösteriliyor ya da hata ekranında mı (ağ gelince yenilenmeli mi)? */
    fun needsRefresh(): Boolean = _state.value.let { it.offline != null || it.error != null || it.notice != null }

    /** Ağ geri geldi: içerik varsa sessizce, yoksa (hata ekranı) baştan dener. */
    fun refreshAfterReconnect() {
        if (started) load(initial = _state.value.error != null)
    }

    private fun load(initial: Boolean) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            val empty = _state.value.let { it.rows.isEmpty() && it.heroes.isEmpty() }
            if (initial) _state.update { it.copy(loading = true, error = null) }
            // Yerel kopya YALNIZCA ekran boşken okunur (resume'da gereksiz JSON çözümü yok); önce onu yayar, sonra ağdan yeniler.
            repo.bootFlow(profileId, readCache = empty)
                .catch { e ->
                    // önbellek de yok + ağ hatası: nedene göre net mesaj (ağ yok / sunucuya ulaşılamıyor)
                    val failure = container.describeFailure(e)
                    _state.update { current ->
                        if (current.rows.isEmpty() && current.heroes.isEmpty()) {
                            current.copy(loading = false, error = failure.message)
                        } else {
                            current.copy(loading = false, notice = "${failure.message} · önceki içerik gösteriliyor")
                        }
                    }
                }
                .collect { snapshot ->
                    val offline = container.offlineNotice(snapshot)
                    val failure = snapshot.refreshError
                    val notice = if (failure != null && offline == null) {
                        "${container.describeFailure(failure).message} · önceki içerik gösteriliyor"
                    } else {
                        null
                    }
                    _state.update { applyBoot(it, snapshot.value, offline, notice) }
                    // İlk satırlar hemen; kalanı kaydırdıkça (HomeContent snapshotFlow) yüklenir.
                    ensureRowsLoaded(LayoutLogic.INITIAL_ROWS - 1)
                }
        }
    }

    private fun applyBoot(current: HomeState, boot: BootResponse, offline: OfflineNotice?, notice: String?): HomeState {
        val previous = current.rows.associateBy { it.id }
        val rows = boot.rows.filter { it.id.isNotBlank() }.distinctBy { it.id }.map { r ->
            val old = previous[r.id]
            if (!r.loaded && old != null && (old.loaded || old.loading)) {
                // tembel yüklenmiş (ya da yüklenmekte olan) satırı taze boot'ta boş iskelete çevirme
                old
            } else {
                HomeRow(
                    id = r.id,
                    title = r.title,
                    items = r.items.filter { it.id.isNotBlank() },
                    loaded = r.loaded,
                    count = r.count,
                    total = r.total,
                )
            }
        }
        return HomeState(
            loading = false,
            error = null,
            heroes = boot.heroList.filter { it.id.isNotBlank() }.distinctBy { it.listKey },
            rows = HomeRows.pruneHidden(rows),
            notice = notice,
            offline = offline,
        )
    }

    /**
     * Dizini [upToIndex] ve öncesi olan, henüz yüklenmemiş satırları yükler (loaded=false + yüklenmiyor +
     * başarısız değil). Görünür alana yaklaşma kararı UI'dadır (HomeContent: snapshotFlow).
     */
    fun ensureRowsLoaded(upToIndex: Int) {
        val rows = _state.value.rows
        for (i in 0..minOf(upToIndex, rows.lastIndex)) {
            val row = rows[i]
            if (!row.loaded && !row.loading && !row.failed) loadRow(row.id)
        }
    }

    /** Başarısız satırı dokununca yeniden dener. */
    fun retryRow(rowId: String) = loadRow(rowId)

    /** loaded=false satırı /api/row/{id} ile çeker: önce yerel kopya (varsa), sonra ağ. */
    private fun loadRow(rowId: String) {
        val row = _state.value.rows.firstOrNull { it.id == rowId } ?: return
        if (row.loaded || row.loading) return
        updateRow(rowId) { it.copy(loading = true, failed = false) }
        viewModelScope.launch {
            rowGate.withPermit {
                repo.rowFlow(rowId, profileId, 0, ROW_LIMIT)
                    .catch { updateRow(rowId) { r -> r.copy(loading = false, failed = true) } }
                    .collect { snapshot ->
                        val res = snapshot.value
                        updateRow(rowId) { r ->
                            r.copy(
                                title = res.title.ifBlank { r.title },
                                items = res.items.filter { it.id.isNotBlank() },
                                loaded = true,
                                loading = snapshot.refreshing,
                                failed = false,
                                total = res.total,
                            )
                        }
                    }
            }
        }
    }

    /**
     * Devam Et kartını listeden kaldırır (uzun basış menüsü). Başarıda kart satırdan çıkar (satır boşalırsa
     * gizlenir) + "Listeden kaldırıldı"; çevrimdışı/hatada kart yerinde kalır ve hata bildirimi çıkar.
     * Sunucuya kartın `id`si (yapım id'si) gider, bölüm kartında da `episode_id` değil.
     */
    fun removeFromContinue(item: Item) {
        val id = ContinueLogic.removalId(item)
        if (id.isBlank() || !removing.add(id)) return
        viewModelScope.launch {
            try {
                val outcome = continueRemover.remove(profileId, item)
                if (outcome is RemoveOutcome.Removed) {
                    _state.update { s -> s.copy(rows = HomeRows.withoutContinueCard(s.rows, id)) }
                }
                _messages.tryEmit(outcome.message)
            } finally {
                removing.remove(id)
            }
        }
    }

    private fun updateRow(rowId: String, transform: (HomeRow) -> HomeRow) {
        _state.update { s ->
            // yalnızca hedef satır yeni nesne olur; diğerleri aynı örnek kalır (Compose atlar)
            s.copy(rows = HomeRows.pruneHidden(s.rows.map { if (it.id == rowId) transform(it) else it }))
        }
    }

    private companion object {
        const val MAX_PARALLEL_ROWS = 2
        const val ROW_LIMIT = 20
    }
}

@Composable
fun HomeScreen(
    profileId: String,
    onOpenDetail: (itemId: String, episodeId: String?) -> Unit,
    onPlay: (itemId: String, episodeId: String?, kind: String) -> Unit,
    onOpenTab: (route: String) -> Unit,
    onOpenSettings: () -> Unit,
    onSwitchProfile: () -> Unit,
    onMessage: (String) -> Unit = {},
) {
    val container = LocalContainer.current
    val vm = containerViewModel(key = "home:$profileId") { HomeViewModel(it, profileId) }
    val state by vm.state.collectAsStateWithLifecycle()
    OnResumeEffect { vm.onResume() }
    val currentOnMessage by rememberUpdatedState(onMessage)
    LaunchedEffect(vm) { vm.messages.collect { currentOnMessage(it) } }
    RefreshWhenBackOnline(container, isStale = vm::needsRefresh, refresh = vm::refreshAfterReconnect)

    val s = state
    Column(Modifier.fillMaxSize()) {
        OfflineBanner(s.offline)
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                s.loading && s.rows.isEmpty() && s.heroes.isEmpty() -> LoadingBox(label = "Yükleniyor…")
                s.error != null -> ErrorBox(
                    title = "İçerik yüklenemedi",
                    message = s.error,
                    actions = listOf(
                        "Tekrar dene" to { vm.retry() },
                        "Adresi değiştir" to onOpenSettings,
                        "Profil değiştir" to onSwitchProfile,
                    ),
                )
                else -> HomeContent(
                    state = s,
                    onNeedRows = vm::ensureRowsLoaded,
                    onRetryRow = vm::retryRow,
                    onRemoveContinue = vm::removeFromContinue,
                    onOpenDetail = onOpenDetail,
                    onPlay = onPlay,
                    onOpenTab = onOpenTab,
                    onOpenSettings = onOpenSettings,
                )
            }
        }
    }
}

@Composable
private fun HomeContent(
    state: HomeState,
    onNeedRows: (Int) -> Unit,
    onRetryRow: (String) -> Unit,
    onRemoveContinue: (Item) -> Unit,
    onOpenDetail: (String, String?) -> Unit,
    onPlay: (String, String?, String) -> Unit,
    onOpenTab: (String) -> Unit,
    onOpenSettings: () -> Unit,
) {
    val listState = rememberLazyListState()
    val hasHero = state.heroes.isNotEmpty()
    // "top" + (hero) öğelerinden sonra satırlar başlar.
    val headerCount = 1 + (if (hasHero) 1 else 0)
    val rowCount = state.rows.size
    val currentNeedRows by rememberUpdatedState(onNeedRows)

    // Kaydırma durumu BURADA (yalnızca bu efektte) okunur: composition'da okunmaz, ekran yeniden oluşmaz.
    // Görünen son satırın 1 ilerisine kadar yüklenir; ilk 3 satır ViewModel tarafından zaten hemen yüklenir.
    LaunchedEffect(listState, headerCount, rowCount) {
        snapshotFlow { listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1 }
            .distinctUntilChanged()
            .collect { lastVisible ->
                currentNeedRows(LayoutLogic.lastRowToLoad(lastVisible - headerCount, rowCount))
            }
    }

    LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
        item(key = "top", contentType = "top") {
            Row(
                Modifier
                    .fillMaxWidth()
                    .windowInsetsPadding(WindowInsets.displayCutout.only(WindowInsetsSides.Horizontal))
                    .padding(start = 16.dp, end = 4.dp, top = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "DIZIFLIX",
                    color = DzColors.Primary,
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Black,
                    modifier = Modifier.weight(1f),
                )
                IconButton(onClick = onOpenSettings) {
                    Icon(Icons.Filled.Settings, contentDescription = "Ayarlar", tint = Color.White)
                }
            }
        }
        val notice = state.notice
        if (notice != null) {
            item(key = "notice", contentType = "notice") {
                Text(
                    notice,
                    color = DzColors.Warning,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }
        if (hasHero) {
            item(key = "hero", contentType = "hero") {
                HeroPager(
                    heroes = state.heroes,
                    onOpenDetail = onOpenDetail,
                    onPlay = onPlay,
                )
            }
        }
        itemsIndexed(state.rows, key = { _, row -> row.id }, contentType = { _, _ -> ROW_CONTENT_TYPE }) { _, row ->
            HomeRowSection(
                row = row,
                onRetryRow = onRetryRow,
                onRemoveContinue = onRemoveContinue,
                onOpenDetail = onOpenDetail,
                onOpenTab = onOpenTab,
            )
        }
        item(key = "bottom", contentType = "bottom") { Spacer(Modifier.height(16.dp)) }
    }
}

private const val ROW_CONTENT_TYPE = "row"

private val SkeletonShape = RoundedCornerShape(8.dp)

@Composable
private fun HomeRowSection(
    row: HomeRow,
    onRetryRow: (String) -> Unit,
    onRemoveContinue: (Item) -> Unit,
    onOpenDetail: (String, String?) -> Unit,
    onOpenTab: (String) -> Unit,
) {
    val tab: String? = when (row.id) {
        "series" -> Routes.SERIES
        "movies" -> Routes.MOVIES
        "mylist" -> Routes.MYLIST
        // trend/dikkate değer satırları: trend/popüler sıralı katalog (sunucu sort=trending|popular)
        "trending_series", "trending_movies", "noteworthy_movies" -> Routes.catalog(row.id)
        else -> null
    }
    Column(Modifier.fillMaxWidth().padding(top = 8.dp)) {
        SectionHeader(
            title = row.title,
            actionLabel = if (tab != null) "Tümü" else null,
            onAction = if (tab != null) ({ onOpenTab(tab) }) else null,
        )
        when {
            row.loaded && row.items.isEmpty() -> Text(
                "Bu satırda içerik yok",
                color = DzColors.Muted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
            row.loaded -> PosterRow(
                items = row.items,
                onItemClick = { item -> onOpenDetail(item.id, CatalogLogic.cardEpisodeId(item, row.id)) },
                // Uzun basış menüsü yalnızca "İzlemeye Devam Et" satırında.
                onItemRemove = if (ContinueLogic.hasRemoveMenu(row.id)) onRemoveContinue else null,
            )
            row.failed -> Text(
                "Yüklenemedi · dokunup tekrar deneyin",
                color = DzColors.Warning,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .padding(horizontal = 16.dp, vertical = 12.dp)
                    .clickable { onRetryRow(row.id) }
                    .padding(4.dp),
            )
            else -> Box(
                Modifier
                    .fillMaxWidth()
                    .height(168.dp)
                    .padding(horizontal = 16.dp)
                    .background(DzColors.Surface, SkeletonShape),
            )
        }
    }
}

/** Hero alt degradesi: tek örnek (her kompozisyonda yeniden oluşturulmaz); boyut çizimde belirlenir. */
private val HeroScrim = Brush.verticalGradient(listOf(Color.Transparent, DzColors.Background))

/**
 * Hero: ekran genişliğine TAM oturur (yatay taşma yok), yüksekliği [LayoutLogic.heroHeightDp] ile
 * genişlik/ekran yüksekliğinden türetilir (telefon 4:3, geniş ekran 16:9 ama ekranın %60'ını aşmaz);
 * görsel ContentScale.Crop ile kırpılır. Sayfa noktaları hero'nun içinde altta durur.
 *
 * İzolasyon: sayfa durumu (pagerState) yalnızca [HeroDots]'un ÇİZİM aşamasında okunur; sayfa değişince
 * ne HeroPager ne de ana sayfa yeniden oluşur. Yalnızca görünen sayfa kompoze edilir (tek ağır görsel).
 * (Bu ekranda otomatik kaydırma zamanlayıcısı yoktur.) Ölçü için BoxWithConstraints (alt-kompozisyon)
 * yerine yapılandırma genişliği kullanılır.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HeroPager(
    heroes: List<Item>,
    onOpenDetail: (String, String?) -> Unit,
    onPlay: (String, String?, String) -> Unit,
) {
    val pagerState = rememberPagerState(pageCount = { heroes.size })
    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val widthDp = configuration.screenWidthDp.toFloat()
    val heroHeight = LayoutLogic.heroHeightDp(widthDp, configuration.screenHeightDp.toFloat()).dp
    val backdrop = remember(widthDp, density) { UrlUtil.backdropSizeFor((widthDp * density.density).toInt()) }
    val multiple = heroes.size > 1
    Box(Modifier.fillMaxWidth().height(heroHeight)) {
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxSize(),
            beyondBoundsPageCount = 0,
            key = { heroes[it].listKey },
        ) { page ->
            HeroPage(
                item = heroes[page],
                compact = heroHeight < 300.dp,
                bottomSpace = if (multiple) 28.dp else 16.dp,
                imageWidth = backdrop.first,
                imageHeight = backdrop.second,
                onOpenDetail = onOpenDetail,
                onPlay = onPlay,
            )
        }
        if (multiple) {
            HeroDots(
                pagerState = pagerState,
                count = heroes.size,
                modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 10.dp),
            )
        }
    }
}

/** Sayfa noktaları tek Canvas: seçili sayfa yalnızca çizimde okunur (yeniden kompozisyon yok). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HeroDots(pagerState: PagerState, count: Int, modifier: Modifier = Modifier) {
    val cell = 13.dp
    Canvas(modifier.size(cell * count, cell)) {
        val cellPx = cell.toPx()
        val radius = 3.5.dp.toPx()
        val current = pagerState.currentPage
        for (i in 0 until count) {
            drawCircle(
                color = if (i == current) DzColors.Primary else Color.White.copy(alpha = 0.35f),
                radius = radius,
                center = Offset(cellPx * i + cellPx / 2f, cellPx / 2f),
            )
        }
    }
}

@Composable
private fun HeroPage(
    item: Item,
    compact: Boolean,
    bottomSpace: Dp,
    imageWidth: Int,
    imageHeight: Int,
    onOpenDetail: (String, String?) -> Unit,
    onPlay: (String, String?, String) -> Unit,
) {
    Box(
        Modifier
            .fillMaxSize()
            .clickable { onOpenDetail(item.id, null) },
    ) {
        // Hero görseli ekran genişliğine uygun küçük izinli boyutta (780x439 / 1280x720); RGB565 YOK (degrade bantlanmasın).
        RemoteImage(
            path = if (item.hasBackdrop) item.backdrop else (item.portrait ?: item.backdrop),
            width = imageWidth,
            height = imageHeight,
            forceSize = true,
            modifier = Modifier.matchParentSize(),
        )
        Box(Modifier.matchParentSize().background(HeroScrim))
        // Metin/düğmeler güvenli alanda: kesme alanı (yatay) + tutarlı 16dp kenar; geniş ekranda genişlik sınırlı.
        Column(
            Modifier
                .align(Alignment.BottomStart)
                .windowInsetsPadding(WindowInsets.displayCutout.only(WindowInsetsSides.Horizontal))
                .widthIn(max = 640.dp)
                .padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = bottomSpace),
        ) {
            Text(
                item.logoText ?: item.title,
                color = Color.White,
                fontSize = if (compact) 22.sp else 26.sp,
                fontWeight = FontWeight.Black,
                maxLines = if (compact) 1 else 2,
                overflow = TextOverflow.Ellipsis,
            )
            val meta = remember(item) { heroMeta(item) }
            Text(
                meta,
                color = DzColors.Muted,
                style = MaterialTheme.typography.labelMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (!compact && item.overview.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    item.overview,
                    color = Color.White.copy(alpha = 0.85f),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val canPlay = !item.isSeries && !item.availability.isUnavailable && item.playback != "trailer"
                if (canPlay) {
                    Button(onClick = { onPlay(item.id, null, "video") }) {
                        Icon(Icons.Filled.PlayArrow, contentDescription = null)
                        Text("Oynat")
                    }
                }
                FilledTonalButton(onClick = { onOpenDetail(item.id, null) }) { Text("Ayrıntılar") }
            }
        }
    }
}

private fun heroMeta(item: Item): String {
    val parts = ArrayList<String>()
    item.year?.let { parts.add(it.toString()) }
    item.rating?.let { if (it > 0.0) parts.add("Puan " + String.format(Locale.ROOT, "%.1f", it)) }
    if (item.genres.isNotEmpty()) parts.add(item.genres.take(3).joinToString(" - "))
    parts.add(if (item.isSeries) "Dizi" else "Film")
    return parts.joinToString("  ·  ")
}
