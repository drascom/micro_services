package com.diziflix.app.ui.detail

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.Season
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.data.net.apiCall
import com.diziflix.app.data.net.userMessage
import com.diziflix.app.domain.DetailLogic
import com.diziflix.app.domain.EpisodeState
import com.diziflix.app.domain.Format
import com.diziflix.app.domain.LayoutLogic
import com.diziflix.app.domain.OfflineNotice
import com.diziflix.app.ui.common.ErrorBox
import com.diziflix.app.ui.common.LoadingBox
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.OfflineBanner
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.PosterRow
import com.diziflix.app.ui.common.ProgressBar
import com.diziflix.app.ui.common.RemoteImage
import com.diziflix.app.ui.common.SectionHeader
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.Locale

@Immutable
data class DetailUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val detail: Detail? = null,
    /** Sezonlar numara sırasında (0. sezon sonda). */
    val seasons: List<Season> = emptyList(),
    val selectedSeason: Int = 0,
    val inMyList: Boolean = false,
    /**
     * Bölüm listesinde VURGULANAN bölüm (karttan gelinen / devam edilecek). Yalnızca vurgu ve ilk sezon
     * seçimi içindir; sayfa ASLA otomatik kaydırılmaz.
     */
    val highlightEpisodeId: String? = null,
    val message: String? = null,
    /** Önbellekten gösterilirken ağ yoksa/sunucuya ulaşılamıyorsa "Çevrimdışı" şeridi. */
    val offline: OfflineNotice? = null,
    /**
     * Son AĞ yanıtı `hydrating: true` dedi (sunucu arka planda sezon/bölüm/özet dolduruyor). Önbellek kopyası
     * bu bilgiyi taşımaz; yalnızca ağ yanıtı günceller.
     */
    val serverHydrating: Boolean = false,
    /**
     * Ekranda "Detaylar yükleniyor…" gösterilsin mi: [serverHydrating] VE izleyici hâlâ yoklıyor. İzleyici
     * vazgeçerse (süre doldu / ağ hatası) gösterge kalkar, takılı kalmaz.
     */
    val hydrating: Boolean = false,
)

class DetailViewModel(
    private val container: AppContainer,
    private val profileId: String,
    private val itemId: String,
    private val initialEpisodeId: String?,
) : ViewModel() {
    private val repo = container.repository

    private val watcher = container.hydrateWatcher

    private val _state = MutableStateFlow(DetailUiState())
    val state: StateFlow<DetailUiState> = combine(_state, watcher.active) { s, active ->
        val shown = s.serverHydrating && itemId in active
        if (shown == s.hydrating) s else s.copy(hydrating = shown)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, _state.value)

    private var started = false
    private var loadJob: Job? = null

    init {
        // Global izleyici bu yapım için bitti dedi (Ready ya da Unavailable): yeni veriyi ağ isteği yapmadan uygula.
        // (Yeniden ağ isteği, başarısız hidrasyonu sunucuda tekrar tetikleyip döngüye sokabilirdi.)
        viewModelScope.launch {
            watcher.events.filter { it.id == itemId }.collect { onHydrationFinished() }
        }
    }

    fun onResume() {
        if (!started) {
            started = true
            load(initial = true)
        } else {
            load(initial = false)   // oynatıcıdan dönüş: ilerlemeler değişmiş olabilir
        }
    }

    fun retry() = load(initial = true)

    /** Önbellekten/çevrimdışı gösteriliyor ya da hata ekranında mı (ağ gelince yenilenmeli mi)? */
    fun needsRefresh(): Boolean = _state.value.let {
        it.offline != null || it.error != null ||
            // yükleniyor denmişti ama izleyici sessizce bıraktı (çevrimdışı/süre): ağ gelince yeniden sor
            (it.serverHydrating && itemId !in watcher.active.value)
    }

    fun refreshAfterReconnect() {
        if (started) load(initial = false)
    }

    /**
     * Önce yerel kopya (varsa anında gösterilir), sonra ağdan yenileme (stale-while-revalidate).
     * Aynı yüklemedeki ikinci yayın (taze veri) kullanıcının sezon seçimini/vurgusunu BOZMAZ.
     */
    private fun load(initial: Boolean) {
        loadJob?.cancel()
        if (initial) _state.update { it.copy(loading = true, error = null) }
        loadJob = viewModelScope.launch {
            var applied = false
            repo.detailFlow(itemId, profileId, readCache = _state.value.detail == null)
                .catch { e ->
                    val message = container.describeFailure(e).message
                    _state.update { current ->
                        if (current.detail == null) current.copy(loading = false, error = message)
                        else current.copy(loading = false, message = "Yenilenemedi: $message")
                    }
                }
                .collect { snapshot ->
                    val offline = container.offlineNotice(snapshot)
                    val failure = snapshot.refreshError
                    val sameLoad = applied
                    applied = true
                    // Hidrasyon durumunu yalnızca AĞ yanıtı bilir (önbelleğe hydrating=true yazılmaz).
                    val fromNetwork = !snapshot.fromCache
                    val hydrating = snapshot.value.hydrating
                    // İzleyiciye ÖNCE kaydol: durum güncellenince `active` zaten dolu olsun (gösterge titremesin).
                    if (fromNetwork && hydrating) {
                        watcher.watch(itemId, snapshot.value.item.title, profileId)
                    }
                    _state.update { current ->
                        val withDetail = applyDetail(current, snapshot.value, sameLoad)
                        val next = if (fromNetwork) withDetail.copy(serverHydrating = hydrating) else withDetail
                        if (failure != null && offline == null) {
                            next.copy(offline = null, message = "Yenilenemedi: ${failure.userMessage()}")
                        } else {
                            next.copy(offline = offline)
                        }
                    }
                }
        }
    }

    private fun applyDetail(current: DetailUiState, detail: Detail, sameLoad: Boolean): DetailUiState {
        val seasons = DetailLogic.sortSeasons(detail.seasons)
        // "İlk" uygulama: ilk detay ya da sezonlar sonradan (hidrasyon bitince) ilk kez geldi; karttan gelinen
        // bölümün sezonu/vurgusu ancak şimdi bilinir.
        val first = current.detail == null || (current.seasons.isEmpty() && seasons.isNotEmpty())
        var selected = current.selectedSeason
        val highlight: String?
        if (first) {
            // ilk seçim: bölüm kartı/devam bölümü -> o bölümün sezonu; yoksa ilk sezon. Kaydırma YOK.
            val pos = DetailLogic.focusPosition(seasons, detail, initialEpisodeId)
            selected = pos?.seasonIndex ?: 0
            highlight = DetailLogic.highlightEpisodeId(seasons, detail, initialEpisodeId)
        } else if (sameLoad) {
            // önbellek -> taze yayın (aynı yükleme): kullanıcının gördüğü seçim/vurgu korunur
            highlight = current.highlightEpisodeId
        } else {
            // oynatıcıdan dönüş: sezon seçimi korunur, vurgu güncel devam bölümüne kayar
            highlight = DetailLogic.resumeEpisodeId(seasons, detail) ?: current.highlightEpisodeId
        }
        return current.copy(
            loading = false,
            error = null,
            detail = detail,
            seasons = seasons,
            selectedSeason = selected.coerceIn(0, (seasons.size - 1).coerceAtLeast(0)),
            inMyList = detail.item.inMylist,
            highlightEpisodeId = highlight,
        )
    }

    /**
     * İzleyici bu yapımın hidrasyonunun bittiğini bildirdi. Repository yeni detayı zaten önbelleğe yazdı:
     * açık ekran onu doğrudan uygular (sezon seçimi korunur, liste kaydırılmaz). Bellekte yoksa normal yenileme.
     */
    private fun onHydrationFinished() {
        val fresh = repo.cachedDetail(itemId)
        if (fresh == null || fresh.hydrating) {
            if (started) load(initial = false)
            return
        }
        _state.update { current ->
            applyDetail(current, fresh, sameLoad = false).copy(serverHydrating = false)
        }
    }

    fun selectSeason(index: Int) {
        _state.update { it.copy(selectedSeason = index) }
    }

    fun consumeMessage() {
        _state.update { it.copy(message = null) }
    }

    /** İyimser güncelleme; hata olursa geri alınır. */
    fun toggleMyList() {
        val was = _state.value.inMyList
        _state.update { it.copy(inMyList = !was) }
        viewModelScope.launch {
            apiCall { repo.setInMyList(profileId, itemId, add = !was) }.onFailure { e ->
                _state.update { it.copy(inMyList = was, message = "Listem güncellenemedi: ${e.userMessage()}") }
            }
        }
    }
}

@Composable
fun DetailScreen(
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

    val snackbar = remember { SnackbarHostState() }
    val message = state.message
    LaunchedEffect(message) {
        if (message != null) {
            snackbar.showSnackbar(message)
            vm.consumeMessage()
        }
    }

    val s = state
    val detail = s.detail
    Box(Modifier.fillMaxSize()) {
        when {
            detail != null -> DetailContent(
                state = s,
                detail = detail,
                onSelectSeason = vm::selectSeason,
                onToggleMyList = vm::toggleMyList,
                onOpenDetail = onOpenDetail,
                onPlay = onPlay,
            )
            s.error != null -> ErrorBox(
                title = "Detay yüklenemedi",
                message = s.error,
                actions = listOf("Tekrar dene" to { vm.retry() }, "Geri" to onBack),
            )
            else -> LoadingBox(label = "Yükleniyor…")
        }
        BackButton(onBack, Modifier.align(Alignment.TopStart))
        // Geri düğmesinin sağında, üstte ince şerit (önbellekten gösteriliyorsa).
        OfflineBanner(s.offline, Modifier.align(Alignment.TopCenter).padding(start = 56.dp))
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
    }
}

@Composable
private fun BackButton(onBack: () -> Unit, modifier: Modifier = Modifier) {
    IconButton(
        onClick = onBack,
        modifier = modifier
            .padding(8.dp)
            .size(40.dp)
            .background(Color.Black.copy(alpha = 0.5f), CircleShape),
    ) {
        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Geri", tint = Color.White)
    }
}

/** Artalan alt degradesi: tek örnek (her kompozisyonda yeniden oluşturulmaz). */
private val DetailScrim = Brush.verticalGradient(listOf(Color.Transparent, DzColors.Background))

/** LazyColumn sırası: 0 = başlık bloğu, 1 = yapışkan sezon seçici. */
private const val SEASON_BAR_INDEX = 1

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun DetailContent(
    state: DetailUiState,
    detail: Detail,
    onSelectSeason: (Int) -> Unit,
    onToggleMyList: () -> Unit,
    onOpenDetail: (String) -> Unit,
    onPlay: (String, String?, String) -> Unit,
) {
    val media = detail.item
    val today = remember { DetailLogic.todayIso() }
    val seasons = state.seasons
    val season = seasons.getOrNull(state.selectedSeason)
    val episodes = season?.episodes ?: emptyList()
    val listState = rememberLazyListState()
    var dialog by remember { mutableStateOf<Pair<String, String>?>(null) }

    // Sayfa HER ZAMAN en üstten açılır: açılışta hiçbir kaydırma yok (devam bölümü yalnızca vurgulanır).
    // Sezon değişince: kullanıcı bölüm listesine inmişse yeni sezonun ilk bölümü sezon seçicinin hemen
    // altından başlar (liste kısalıp konum sona yapışmaz, seçici görünür kalır); en üstteyse konum korunur.
    val hasSeasonBar = media.isSeries && seasons.isNotEmpty()
    var shownSeason by remember { mutableIntStateOf(state.selectedSeason) }
    LaunchedEffect(state.selectedSeason) {
        if (shownSeason != state.selectedSeason) {
            shownSeason = state.selectedSeason
            if (hasSeasonBar && listState.firstVisibleItemIndex > SEASON_BAR_INDEX) {
                listState.scrollToItem(SEASON_BAR_INDEX)
            }
        }
    }
    val resumeEpisodeId = remember(seasons, detail) { DetailLogic.resumeEpisodeId(seasons, detail) }
    val similar = remember(detail) { detail.similar }

    val d = dialog
    if (d != null) {
        AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text(d.first) },
            text = { Text(d.second) },
            confirmButton = { TextButton(onClick = { dialog = null }) { Text("Tamam") } },
        )
    }

    LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
        item(key = "header") {
            DetailHeader(
                state = state,
                detail = detail,
                onToggleMyList = onToggleMyList,
                onPlay = onPlay,
            )
        }
        if (hasSeasonBar) {
            // Sezon seçici yapışkan: bölümler kaydırılırken üstte görünür kalır.
            stickyHeader(key = "seasons") {
                SeasonBar(seasons = seasons, selected = state.selectedSeason, onSelect = onSelectSeason)
            }
        }
        if (media.isSeries && seasons.isNotEmpty() && episodes.isEmpty()) {
            item(key = "no-episodes") {
                Text(
                    "Bu sezonda bölüm yok",
                    color = DzColors.Muted,
                    modifier = Modifier.padding(16.dp),
                )
            }
        }
        itemsIndexed(episodes, key = { index, ep -> "$index:${ep.id}" }, contentType = { _, _ -> "episode" }) { _, ep ->
            val epState = DetailLogic.episodeState(ep, today)
            val highlightLabel = when {
                ep.id != state.highlightEpisodeId -> null
                ep.id == resumeEpisodeId -> "Kaldığınız bölüm"
                else -> "Seçilen bölüm"
            }
            EpisodeRow(
                episode = ep,
                state = epState,
                highlightLabel = highlightLabel,
                onClick = {
                    when (epState) {
                        EpisodeState.Unaired -> {
                            val when_ = Format.date(ep.airDate)
                            dialog = "Henüz yayınlanmadı" to
                                (if (when_.isNotEmpty()) "$when_ tarihinde yayınlanacak." else "Bu bölüm henüz yayınlanmadı.")
                        }
                        EpisodeState.Unavailable ->
                            dialog = "Bölüm kaynağı yok" to "Bu bölümün izleme kaynağı henüz mevcut değil."
                        else -> onPlay(media.id, ep.id, "video")
                    }
                },
            )
        }
        if (media.isSeries && seasons.isEmpty() && !state.hydrating) {
            item(key = "no-seasons") {
                Text(
                    DetailLogic.emptySeasonsText(detail),
                    color = DzColors.Muted,
                    modifier = Modifier.padding(16.dp),
                )
            }
        }
        if (similar.isNotEmpty()) {
            item(key = "similar") {
                Column(Modifier.padding(top = 16.dp, bottom = 24.dp)) {
                    SectionHeader("Benzer Yapımlar")
                    PosterRow(items = similar, onItemClick = { onOpenDetail(it.id) })
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DetailHeader(
    state: DetailUiState,
    detail: Detail,
    onToggleMyList: () -> Unit,
    onPlay: (String, String?, String) -> Unit,
) {
    val item = detail.item
    var overviewExpanded by remember { mutableStateOf(false) }

    val configuration = LocalConfiguration.current
    val density = LocalDensity.current
    val widthDp = configuration.screenWidthDp.toFloat()
    val backdropHeight = LayoutLogic.detailBackdropHeightDp(widthDp, configuration.screenHeightDp.toFloat()).dp
    val backdropSize = remember(widthDp, density) { UrlUtil.backdropSizeFor((widthDp * density.density).toInt()) }
    Column(Modifier.fillMaxWidth()) {
        // Artalan ekran genişliğine oturur; geniş/yatay ekranda yüksekliği ekranın yarısıyla sınırlı (kırpılır).
        // BoxWithConstraints (alt-kompozisyon) yerine yapılandırma genişliği; küçük izinli boyut + forceSize.
        Box(Modifier.fillMaxWidth().height(backdropHeight)) {
            RemoteImage(
                path = item.backdrop ?: item.portrait,
                width = backdropSize.first,
                height = backdropSize.second,
                forceSize = true,
                modifier = Modifier.matchParentSize(),
                fallbackText = item.title,
            )
            Box(Modifier.matchParentSize().background(DetailScrim))
        }

        Column(Modifier.widthIn(max = 720.dp).padding(horizontal = 16.dp)) {
            Text(
                item.title,
                color = Color.White,
                fontSize = 26.sp,
                fontWeight = FontWeight.Black,
            )
            Spacer(Modifier.height(6.dp))
            MetaLine(detail)

            val availability = item.availability
            if (availability.isUnavailable) {
                Spacer(Modifier.height(6.dp))
                Text(
                    when {
                        availability.hasTrailer -> "Tam izleme kaynağı yok · Fragman mevcut"
                        DetailLogic.isNoSource(detail) -> DetailLogic.noSourceText(item.isSeries)
                        else -> "İzleme kaynağı henüz mevcut değil"
                    },
                    color = DzColors.Warning,
                    style = MaterialTheme.typography.bodySmall,
                )
            } else if (availability.needsCheck) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "Kaynakta sorun bildirildi · Yeniden deneyebilirsiniz",
                    color = DzColors.Warning,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(Modifier.height(12.dp))
            // Düğmeler dar ekranda alt satıra sarılır (yatay kaydırma/taşma yok).
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                if (DetailLogic.canPlayFull(detail)) {
                    val label = DetailLogic.playLabel(detail, state.seasons)
                    Button(onClick = {
                        val episode = if (item.isSeries) DetailLogic.resumeTarget(detail) else null
                        onPlay(item.id, episode, "video")
                    }) {
                        Icon(Icons.Filled.PlayArrow, contentDescription = null)
                        Text(label)
                    }
                }
                if (DetailLogic.hasTrailer(detail)) {
                    FilledTonalButton(onClick = { onPlay(item.id, null, "trailer") }) { Text("Fragmanı Oynat") }
                }
                FilledTonalButton(onClick = onToggleMyList) {
                    Icon(
                        if (state.inMyList) Icons.Filled.Check else Icons.Filled.Add,
                        contentDescription = null,
                    )
                    Text(if (state.inMyList) "Listemden Çıkar" else "Listeme Ekle")
                }
            }

            if (item.overview.isNotBlank()) {
                Spacer(Modifier.height(12.dp))
                Text(
                    item.overview,
                    color = Color.White.copy(alpha = 0.9f),
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = if (overviewExpanded) Int.MAX_VALUE else 4,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.clickable { overviewExpanded = !overviewExpanded },
                )
                if (item.overview.length > 220) {
                    Text(
                        if (overviewExpanded) "Daha az göster" else "Devamını oku",
                        color = DzColors.Muted,
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier
                            .clickable { overviewExpanded = !overviewExpanded }
                            .padding(vertical = 4.dp),
                    )
                }
            }

            val director = detail.extras.director
            if (!director.isNullOrBlank()) {
                Spacer(Modifier.height(8.dp))
                Text("Yönetmen: $director", color = DzColors.Muted, style = MaterialTheme.typography.bodySmall)
            }
            if (detail.extras.cast.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Oyuncular: " + detail.extras.cast.take(5).joinToString(", "),
                    color = DzColors.Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (state.hydrating) {
                Spacer(Modifier.height(16.dp))
                HydratingRow()
            }
        }

        if (item.isSeries && state.seasons.isNotEmpty()) {
            // Sezon seçici bu bloğun hemen altında YAPIŞKAN öğe olarak durur (DetailContent).
            Spacer(Modifier.height(16.dp))
            SectionHeader("Sezonlar ve Bölümler")
        }
    }
}

/** Sunucu arka planda sezon/bölüm/özet doldururken: küçük gösterge + metin (boş alan mesajı yerine). */
@Composable
private fun HydratingRow() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(
            modifier = Modifier.size(16.dp),
            strokeWidth = 2.dp,
            color = DzColors.Primary,
        )
        Spacer(Modifier.width(10.dp))
        Text(
            "Detaylar yükleniyor…",
            color = DzColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

/** Yapışkan sezon çubuğu: opak zemin (altından geçen bölümler görünmesin). */
@Composable
private fun SeasonBar(seasons: List<Season>, selected: Int, onSelect: (Int) -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .background(DzColors.Background)
            .padding(vertical = 6.dp),
    ) {
        SeasonSelector(seasons = seasons, selected = selected, onSelect = onSelect)
    }
}

@Composable
private fun MetaLine(detail: Detail) {
    val item = detail.item
    val bits = ArrayList<String>()
    item.year?.let { bits.add(it.toString()) }
    if (item.genres.isNotEmpty()) bits.add(item.genres.take(3).joinToString(", "))
    item.country?.takeIf { it.isNotBlank() }?.let { bits.add(it) }
    val runtime = Format.minutes(detail.extras.runtime)
    if (runtime.isNotEmpty()) bits.add(if (item.isSeries) "$runtime / bölüm" else runtime)
    item.followers?.takeIf { it > 0 }?.let { bits.add("$it takipçi") }
    item.rating?.takeIf { it > 0.0 }?.let { bits.add("Puan " + String.format(Locale.ROOT, "%.1f", it)) }
    if (bits.isNotEmpty()) {
        Text(
            bits.joinToString("  ·  "),
            color = DzColors.Muted,
            style = MaterialTheme.typography.labelLarge,
        )
    }
}

/** Sezon seçici (üstte): gerçek sezon posteri varsa küçük poster, yoksa yalnızca metin. */
@Composable
private fun SeasonSelector(seasons: List<Season>, selected: Int, onSelect: (Int) -> Unit) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        itemsIndexed(seasons, key = { index, s -> "$index:${s.season}" }) { index, s ->
            val isSelected = index == selected
            Row(
                Modifier
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (isSelected) DzColors.WarningSoft else DzColors.SurfaceHigh)
                    .clickable { onSelect(index) }
                    .padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // has_poster=false iken poster_url dizinin afişidir: sezon posteri olarak GÖSTERİLMEZ.
                if (s.hasPoster && !s.posterUrl.isNullOrBlank()) {
                    RemoteImage(
                        path = s.posterUrl,
                        width = UrlUtil.SEASON_POSTER_W,
                        height = UrlUtil.SEASON_POSTER_H,
                        forceSize = true,
                        rgb565 = true,
                        modifier = Modifier
                            .width(34.dp)
                            .aspectRatio(2f / 3f)
                            .clip(RoundedCornerShape(4.dp)),
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Column {
                    Text(
                        DetailLogic.seasonLabel(s),
                        color = Color.White,
                        style = MaterialTheme.typography.labelLarge,
                        maxLines = 1,
                    )
                    Text(
                        "${DetailLogic.episodeCount(s)} bölüm",
                        color = if (isSelected) Color.White else Color.White.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
    }
}

@Composable
private fun EpisodeRow(
    episode: Episode,
    state: EpisodeState,
    highlightLabel: String?,
    onClick: () -> Unit,
) {
    val dim = if (state == EpisodeState.Unaired) 0.55f else 1f
    val highlighted = highlightLabel != null
    Row(
        Modifier
            .fillMaxWidth()
            .alpha(dim)
            .background(if (highlighted) DzColors.Primary.copy(alpha = 0.14f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Box(
            Modifier
                .width(128.dp)
                .aspectRatio(16f / 9f)
                .clip(RoundedCornerShape(6.dp))
                .background(DzColors.SurfaceHigh),
        ) {
            // has_still=false: gerçek görsel yok, sunucunun yer tutucusu indirilmez.
            val still = episode.stillPath
            if (episode.hasStill && still != null) {
                RemoteImage(
                    path = still,
                    width = UrlUtil.STILL_W,
                    height = UrlUtil.STILL_H,
                    forceSize = true,
                    rgb565 = true,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            val pct = episode.progress?.pct ?: 0.0
            if (pct > 0.0) {
                ProgressBar(
                    fraction = (pct / 100.0).toFloat(),
                    modifier = Modifier.align(Alignment.BottomCenter),
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            if (highlightLabel != null) {
                Text(
                    highlightLabel,
                    color = Color.White,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .clip(RoundedCornerShape(4.dp))
                        .background(DzColors.Primary)
                        .padding(horizontal = 6.dp, vertical = 2.dp),
                )
                Spacer(Modifier.height(3.dp))
            }
            Text(
                "${episode.episode}. ${episode.title}".trim(),
                color = Color.White,
                style = MaterialTheme.typography.titleSmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            val flag: Pair<String, Color>? = when (state) {
                EpisodeState.Unaired -> {
                    val date = Format.date(episode.airDate)
                    ("Yakında" + (if (date.isNotEmpty()) " · $date" else "")) to DzColors.Warning
                }
                EpisodeState.Unavailable -> "Kaynak yok" to MaterialTheme.colorScheme.error
                EpisodeState.Check -> "Kaynak kontrol ediliyor" to DzColors.Warning
                EpisodeState.Ready -> null
            }
            if (flag != null) {
                Text(flag.first, color = flag.second, style = MaterialTheme.typography.labelSmall)
            }
            val overview = Format.truncate(episode.overview, 180)
            if (overview.isNotEmpty()) {
                Text(
                    overview,
                    color = DzColors.Muted,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            val meta = ArrayList<String>()
            val runtime = Format.minutes(episode.runtime)
            if (runtime.isNotEmpty()) meta.add(runtime)
            val date = Format.date(episode.airDate)
            if (state != EpisodeState.Unaired && date.isNotEmpty()) meta.add(date)
            if (meta.isNotEmpty()) {
                Text(meta.joinToString(" · "), color = DzColors.Muted, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
