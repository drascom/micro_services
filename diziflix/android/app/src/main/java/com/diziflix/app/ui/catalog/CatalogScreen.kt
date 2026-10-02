package com.diziflix.app.ui.catalog

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Genre
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.CatalogQuery
import com.diziflix.app.data.net.apiCall
import com.diziflix.app.data.net.userMessage
import com.diziflix.app.domain.CatalogLogic
import com.diziflix.app.ui.common.Chip
import com.diziflix.app.ui.common.EmptyBox
import com.diziflix.app.ui.common.ErrorBox
import com.diziflix.app.ui.common.LoadingBox
import com.diziflix.app.ui.common.OnResumeEffect
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Katalog görünümü: movies | series | mylist; ana sayfa satırlarının "Tümü" kartıyla açılan trend/popüler görünümler
 * (sunucu `sort=trending|popular`: [defaultSort] ilk sıralamadır, süzgeçler temizlenince ona dönülür).
 */
enum class CatalogView(val title: String, val type: String, val mine: Boolean, val defaultSort: String = "new") {
    Movies("Filmler", "movie", false),
    Series("Diziler", "series", false),
    MyList("Listem", "", true),
    TrendingSeries("Haftanın Trendleri · Diziler", "series", false, "trending"),
    TrendingMovies("Haftanın Trendleri · Filmler", "movie", false, "trending"),
    NoteworthyMovies("Dikkate Değer Filmler", "movie", false, "popular"),
    ;

    companion object {
        /** Ana sayfa satır kimliği -> o satırın "Tümü" kataloğu (trend/popüler olmayan satır için null). */
        fun forRow(rowId: String?): CatalogView? = when (rowId) {
            "trending_series" -> TrendingSeries
            "trending_movies" -> TrendingMovies
            "noteworthy_movies" -> NoteworthyMovies
            else -> null
        }
    }
}

@Immutable
data class CatalogFilters(
    val genre: String = "",
    val year: Int? = null,
    val availability: String = "",
    val sort: String = "new",
)

@Immutable
data class CatalogUiState(
    val loading: Boolean = true,
    val loadingMore: Boolean = false,
    val error: String? = null,
    val items: List<Item> = emptyList(),
    val total: Int = 0,
    val genres: List<Genre> = emptyList(),
    val years: List<Int> = emptyList(),
    val filters: CatalogFilters = CatalogFilters(),
)

class CatalogViewModel(
    container: AppContainer,
    private val profileId: String,
    private val view: CatalogView,
) : ViewModel() {
    companion object {
        const val PAGE = 20
    }

    private val repo = container.repository

    private val _state = MutableStateFlow(CatalogUiState(filters = CatalogFilters(sort = view.defaultSort)))
    val state: StateFlow<CatalogUiState> = _state.asStateFlow()

    private var started = false
    private var job: Job? = null
    private var version = 0

    fun onResume() {
        if (!started) {
            started = true
            reload(silent = false)
        } else if (view.mine) {
            reload(silent = true)   // Listem başka ekranlarda değişmiş olabilir
        }
    }

    fun retry() = reload(silent = false)

    fun setGenre(id: String) = applyFilters { it.copy(genre = id) }
    fun setYear(year: Int?) = applyFilters { it.copy(year = year) }
    fun setAvailability(id: String) = applyFilters { it.copy(availability = id) }
    fun setSort(id: String) = applyFilters { it.copy(sort = id) }
    fun clearFilters() = applyFilters { CatalogFilters(sort = view.defaultSort) }

    private fun applyFilters(transform: (CatalogFilters) -> CatalogFilters) {
        _state.update { it.copy(filters = transform(it.filters)) }
        reload(silent = false)
    }

    private fun query(offset: Int): CatalogQuery {
        val f = _state.value.filters
        return CatalogQuery(
            profile = profileId,
            type = view.type,
            genre = f.genre,
            year = f.year,
            availability = f.availability,
            sort = f.sort,
            mine = view.mine,
            offset = offset,
            limit = PAGE,
        )
    }

    private fun reload(silent: Boolean) {
        job?.cancel()
        val mine = ++version
        if (!silent) _state.update { it.copy(loading = true, loadingMore = false, error = null) }
        job = viewModelScope.launch {
            apiCall { repo.catalog(query(0)) }.fold(
                onSuccess = { res ->
                    if (mine == version) {
                        _state.update {
                            it.copy(
                                loading = false,
                                loadingMore = false,
                                error = null,
                                items = res.items.filter { item -> item.id.isNotBlank() },
                                total = res.total,
                                genres = res.genres,
                                years = res.years,
                            )
                        }
                    }
                },
                onFailure = { e ->
                    if (mine == version) {
                        _state.update { current ->
                            if (silent && current.items.isNotEmpty()) current.copy(loading = false)
                            else current.copy(loading = false, error = e.userMessage())
                        }
                    }
                },
            )
        }
    }

    /** Listenin sonuna yaklaşınca sonraki sayfayı ekler (sonsuz kaydırma). */
    fun loadMore() {
        val s = _state.value
        if (s.loading || s.loadingMore || s.error != null || s.items.size >= s.total) return
        val mine = version
        _state.update { it.copy(loadingMore = true) }
        viewModelScope.launch {
            apiCall { repo.catalog(query(s.items.size)) }.fold(
                onSuccess = { res ->
                    if (mine == version) {
                        _state.update {
                            it.copy(
                                loadingMore = false,
                                items = it.items + res.items.filter { item -> item.id.isNotBlank() },
                                total = res.total,
                            )
                        }
                    }
                },
                onFailure = {
                    if (mine == version) _state.update { current -> current.copy(loadingMore = false) }
                },
            )
        }
    }
}

private val AVAILABILITY_OPTIONS = CatalogLogic.AVAILABILITY_OPTIONS

@Composable
fun CatalogScreen(
    view: CatalogView,
    profileId: String,
    onOpenDetail: (itemId: String) -> Unit,
) {
    val vm = containerViewModel(key = "catalog:${view.name}:$profileId") { CatalogViewModel(it, profileId, view) }
    val state by vm.state.collectAsStateWithLifecycle()
    OnResumeEffect { vm.onResume() }

    val s = state

    when {
        s.loading && s.items.isEmpty() -> Column(Modifier.fillMaxSize()) {
            CatalogHeader(view.title, s.total)
            LoadingBox(label = "Katalog yükleniyor…")
        }
        s.error != null && s.items.isEmpty() -> Column(Modifier.fillMaxSize()) {
            CatalogHeader(view.title, s.total)
            ErrorBox(message = s.error, actions = listOf("Tekrar dene" to { vm.retry() }))
        }
        else -> CatalogGrid(
            view = view,
            state = s,
            vm = vm,
            onOpenDetail = onOpenDetail,
        )
    }
}

@Composable
private fun CatalogHeader(title: String, total: Int) {
    Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 4.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall, color = Color.White)
        if (total > 0) {
            Text("$total yapım", style = MaterialTheme.typography.labelMedium, color = DzColors.Muted)
        }
    }
}

@Composable
private fun CatalogGrid(
    view: CatalogView,
    state: CatalogUiState,
    vm: CatalogViewModel,
    onOpenDetail: (String) -> Unit,
) {
    val gridState: LazyGridState = rememberLazyGridState()
    val nearEnd by remember(gridState) {
        derivedStateOf {
            val last = gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            last >= gridState.layoutInfo.totalItemsCount - 6
        }
    }
    LaunchedEffect(nearEnd, state.items.size, state.total) {
        if (nearEnd && state.items.size < state.total) vm.loadMore()
    }

    LazyVerticalGrid(
        state = gridState,
        columns = GridCells.Adaptive(minSize = 110.dp),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        item(span = { GridItemSpan(maxLineSpan) }) {
            Column {
                CatalogHeader(view.title, state.total)
                FilterRow(state = state, vm = vm, defaultSort = view.defaultSort)
            }
        }
        if (state.items.isEmpty()) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                Box(Modifier.fillMaxWidth().padding(vertical = 48.dp), contentAlignment = Alignment.Center) {
                    Text(
                        if (view.mine) "Listeniz boş. Bir yapımın ayrıntısından \"Listeme Ekle\" ile ekleyebilirsiniz."
                        else "Bu filtrelerde içerik bulunamadı.",
                        color = DzColors.Muted,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
        itemsIndexed(state.items, key = { index, item -> "$index:${item.id}" }) { _, item ->
            CatalogTile(
                item = item,
                isSearch = false,
                onClick = { onOpenDetail(item.id) },
            )
        }
        if (state.loadingMore) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = DzColors.Primary)
                }
            }
        }
    }
}

@Composable
private fun FilterRow(state: CatalogUiState, vm: CatalogViewModel, defaultSort: String) {
    val f = state.filters
    val sortOptions = remember(defaultSort) { CatalogLogic.sortOptionsFor(defaultSort) }
    val genreName = state.genres.firstOrNull { it.id == f.genre }?.name ?: "Tümü"
    val availabilityName = AVAILABILITY_OPTIONS.firstOrNull { it.first == f.availability }?.second ?: "Tümü"
    val sortName = sortOptions.firstOrNull { it.first == f.sort }?.second ?: "Yeni eklenen"
    val isFiltered = f.genre.isNotEmpty() || f.year != null || f.availability.isNotEmpty() || f.sort != defaultSort

    LazyRow(
        contentPadding = PaddingValues(vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            DropdownChip(
                label = "Tür: $genreName",
                selected = f.genre.isNotEmpty(),
                options = listOf("" to "Tümü") + state.genres.map { it.id to it.name },
                onPick = { vm.setGenre(it) },
            )
        }
        item {
            DropdownChip(
                label = "Yıl: " + (f.year?.toString() ?: "Tümü"),
                selected = f.year != null,
                options = listOf("" to "Tümü") + state.years.map { it.toString() to it.toString() },
                onPick = { vm.setYear(it.toIntOrNull()) },
            )
        }
        item {
            DropdownChip(
                label = availabilityName,
                selected = f.availability.isNotEmpty(),
                options = AVAILABILITY_OPTIONS,
                onPick = { vm.setAvailability(it) },
            )
        }
        item {
            DropdownChip(
                label = "Sıra: $sortName",
                selected = f.sort != defaultSort,
                options = sortOptions,
                onPick = { vm.setSort(it) },
            )
        }
        if (isFiltered) {
            item { Chip(text = "Filtreleri temizle", onClick = { vm.clearFilters() }) }
        }
    }
}

/** Etiket + açılır liste (kimlik, görünen ad) çiftleri. */
@Composable
private fun DropdownChip(
    label: String,
    selected: Boolean,
    options: List<Pair<String, String>>,
    onPick: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        Chip(text = label, onClick = { expanded = true }, selected = selected)
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (id, name) ->
                DropdownMenuItem(
                    text = { Text(name) },
                    onClick = {
                        expanded = false
                        onPick(id)
                    },
                )
            }
        }
    }
}
