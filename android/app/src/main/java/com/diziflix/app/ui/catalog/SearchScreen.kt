package com.diziflix.app.ui.catalog

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.CatalogQuery
import com.diziflix.app.data.net.apiCall
import com.diziflix.app.data.net.userMessage
import com.diziflix.app.domain.SearchSourceLogic
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

@Immutable
data class SearchUiState(
    val query: String = "",
    /** Yerel sonuçlar bekleniyor. */
    val loading: Boolean = false,
    /** Yerel sonuçlar geldi, kaynak sitede canlı aranıyor. */
    val searching: Boolean = false,
    val items: List<Item> = emptyList(),
    val total: Int = 0,
    val error: String? = null,
    val note: String? = null,
    /** Canlı aramada yanıt vermeyen kaynakların adları (`remote_sites` ok:false, atlanmamış); boş = hepsi yanıt verdi/bilgi yok. */
    val failedSources: List<String> = emptyList(),
    /** En az bir arama tamamlandı (boş sonuç mesajı için). */
    val done: Boolean = false,
) {
    /** Sonuç ızgarasının altındaki silik tek satır ("Bazı kaynaklar yanıt vermedi: ..."); yoksa null. */
    val failedNote: String? get() = SearchSourceLogic.failedNote(failedSources)
}

/**
 * TV istemcisiyle aynı iki aşama: önce yerel katalog (anında), en az 3 karakterde ardından /api/search
 * (kaynak siteyi canlı sorgular; yavaş olabilir, hata olursa yerel sonuçlar kalır).
 */
class SearchViewModel(container: AppContainer, private val profileId: String) : ViewModel() {
    companion object {
        const val DEBOUNCE_MS = 450L
        const val MIN_LOCAL = 2
        const val MIN_REMOTE = 3
    }

    private val repo = container.repository

    private val _state = MutableStateFlow(SearchUiState())
    val state: StateFlow<SearchUiState> = _state.asStateFlow()

    private var job: Job? = null

    fun onQueryChange(value: String) {
        _state.update { it.copy(query = value) }
        job?.cancel()
        val q = value.trim()
        if (q.length < MIN_LOCAL) {
            _state.update { SearchUiState(query = value) }
            return
        }
        job = viewModelScope.launch {
            delay(DEBOUNCE_MS)
            performSearch(q)
        }
    }

    /** Klavyeden "Ara": bekleme olmadan hemen. */
    fun submit() {
        val q = _state.value.query.trim()
        if (q.length < MIN_LOCAL) return
        job?.cancel()
        job = viewModelScope.launch { performSearch(q) }
    }

    fun clear() {
        job?.cancel()
        _state.value = SearchUiState()
    }

    private suspend fun performSearch(q: String) {
        _state.update { it.copy(loading = true, searching = false, error = null, note = null, failedSources = emptyList()) }

        val local = apiCall { repo.catalog(CatalogQuery(profile = profileId, q = q, limit = 20)) }
        var haveLocal = false
        local.onSuccess { res ->
            haveLocal = true
            _state.update {
                it.copy(
                    loading = false,
                    searching = q.length >= MIN_REMOTE,
                    items = res.items.filter { item -> item.id.isNotBlank() },
                    total = res.total,
                    done = q.length < MIN_REMOTE,
                )
            }
        }

        if (q.length < MIN_REMOTE) {
            local.onFailure { e -> _state.update { it.copy(loading = false, error = e.userMessage(), done = true) } }
            return
        }

        apiCall { repo.search(q, profileId, 20) }.fold(
            onSuccess = { res ->
                val items = res.items.filter { item -> item.id.isNotBlank() }
                // Site bazında bilgi varsa "Bazı kaynaklar yanıt vermedi: ..." satırı; yoksa (eski sunucu) genel not.
                val failed = SearchSourceLogic.failedSources(res.remoteSites, items)
                _state.update {
                    it.copy(
                        loading = false,
                        searching = false,
                        items = items,
                        total = res.total,
                        error = null,
                        note = if (failed.isEmpty() && !res.remoteError.isNullOrBlank()) {
                            "Canlı kaynak şu an yanıt vermedi, yerel sonuçlar gösteriliyor"
                        } else {
                            null
                        },
                        failedSources = failed,
                        done = true,
                    )
                }
            },
            onFailure = { e ->
                _state.update {
                    if (haveLocal) {
                        it.copy(
                            loading = false,
                            searching = false,
                            note = "Canlı kaynak şu an yanıt vermedi, yerel sonuçlar gösteriliyor",
                            done = true,
                        )
                    } else {
                        it.copy(loading = false, searching = false, error = e.userMessage(), done = true)
                    }
                }
            },
        )
    }
}

@Composable
fun SearchScreen(profileId: String, onOpenDetail: (itemId: String) -> Unit) {
    val vm = containerViewModel(key = "search:$profileId") { SearchViewModel(it, profileId) }
    val state by vm.state.collectAsStateWithLifecycle()
    val focusManager = LocalFocusManager.current
    val s = state

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = s.query,
            onValueChange = vm::onQueryChange,
            singleLine = true,
            placeholder = { Text("Film veya dizi ara") },
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            trailingIcon = {
                if (s.query.isNotEmpty()) {
                    IconButton(onClick = { vm.clear() }) {
                        Icon(Icons.Filled.Close, contentDescription = "Temizle")
                    }
                }
            },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = {
                vm.submit()
                focusManager.clearFocus()
            }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = DzColors.Primary,
                unfocusedBorderColor = Color(0xFF3A3A42),
                cursorColor = DzColors.Primary,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
        )

        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                s.error != null && s.items.isEmpty() -> Text(
                    s.error,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(24.dp),
                )
                s.items.isEmpty() && (s.loading || s.searching) -> Row(
                    Modifier.align(Alignment.Center),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(color = DzColors.Primary, modifier = Modifier.size(22.dp))
                    Text("  Aranıyor…", color = DzColors.Muted)
                }
                s.items.isEmpty() -> Text(
                    if (s.query.trim().length < SearchViewModel.MIN_LOCAL) {
                        "Aramak için bir film veya dizi adı girin."
                    } else if (s.done) {
                        "Sonuç bulunamadı."
                    } else {
                        ""
                    },
                    color = DzColors.Muted,
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(24.dp),
                )
                else -> SearchResults(state = s, onOpenDetail = onOpenDetail)
            }
        }
    }
}

@Composable
private fun SearchResults(state: SearchUiState, onOpenDetail: (String) -> Unit) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = 110.dp),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        item(span = { GridItemSpan(maxLineSpan) }) {
            Column {
                var text = "${state.total} yapım"
                if (state.searching) text += " · Kaynakta aranıyor…"
                Text(text, color = DzColors.Muted, style = MaterialTheme.typography.labelMedium)
                val note = state.note
                if (note != null) {
                    Text(note, color = DzColors.Warning, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
        itemsIndexed(state.items, key = { index, item -> "$index:${item.id}" }) { _, item ->
            CatalogTile(
                item = item,
                isSearch = true,
                onClick = { onOpenDetail(item.id) },
            )
        }
        val failedNote = state.failedNote
        if (failedNote != null) {
            item(span = { GridItemSpan(maxLineSpan) }, key = "failed-sources") {
                Text(
                    failedNote,
                    color = DzColors.Muted.copy(alpha = 0.6f),
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}
