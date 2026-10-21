package com.diziflix.app.ui.profiles

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Profile
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.OfflineNotice
import com.diziflix.app.ui.common.BrandLogoWide
import com.diziflix.app.ui.common.ErrorBox
import com.diziflix.app.ui.common.LoadingBox
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.common.OfflineBanner
import com.diziflix.app.ui.common.RefreshWhenBackOnline
import com.diziflix.app.ui.common.RemoteImage
import com.diziflix.app.ui.common.UiState
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

@Immutable
data class ProfilesState(
    val content: UiState<List<Profile>> = UiState.Loading,
    /** Önbellekten gösterilirken ağ yoksa/sunucuya ulaşılamıyorsa üstteki "Çevrimdışı" şeridi. */
    val offline: OfflineNotice? = null,
)

class ProfilesViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(ProfilesState())
    val state: StateFlow<ProfilesState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        load()
    }

    /**
     * Önce yerel kopya (profil ekranı ağ beklemeden anında açılır), sonra ağdan yenileme. Ağ başarısızsa
     * yerel liste korunur ve çevrimdışı şeridi çıkar; yerel kopya da yoksa net hata + "Tekrar dene".
     */
    fun load() {
        loadJob?.cancel()
        val hadData = _state.value.content is UiState.Data
        if (!hadData) _state.value = ProfilesState(UiState.Loading)
        loadJob = viewModelScope.launch {
            container.repository.profilesFlow(readCache = !hadData)
                .catch { e -> _state.value = ProfilesState(UiState.Error(container.describeFailure(e).message)) }
                .collect { snapshot ->
                    _state.value = ProfilesState(UiState.Data(snapshot.value), container.offlineNotice(snapshot))
                }
        }
    }

    /** Ağ geri geldi: çevrimdışı şeridi/hata varsa yeniler. */
    fun needsRefresh(): Boolean = _state.value.let { it.offline != null || it.content is UiState.Error }

    fun refreshAfterReconnect() = load()

    /** Seçimi DataStore'a yazar ("seçimi hatırla"); kök bileşen ana ekrana geçer. */
    fun select(profile: Profile) {
        container.appScope.launch { container.settings.setProfileId(profile.id) }
    }
}

/** Açılışta "Kim izliyor?" ekranı. Seçim hatırlanır; Ayarlar'dan "Profil Değiştir" ile tekrar gelinir. */
@Composable
fun ProfilesScreen(onOpenSettings: () -> Unit) {
    val vm = containerViewModel { ProfilesViewModel(it) }
    val state by vm.state.collectAsStateWithLifecycle()
    RefreshWhenBackOnline(LocalContainer.current, isStale = vm::needsRefresh, refresh = vm::refreshAfterReconnect)


    Column(Modifier.fillMaxSize()) {
        OfflineBanner(state.offline)
        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            BrandLogoWide(width = 110.dp)
            Spacer(Modifier.height(8.dp))
            Text("Kim izliyor?", style = MaterialTheme.typography.headlineSmall, color = Color.White)
            Spacer(Modifier.height(20.dp))

            Box(Modifier.weight(1f).fillMaxWidth()) {
                when (val s = state.content) {
                    is UiState.Loading -> LoadingBox(label = "Profiller yükleniyor…")
                    is UiState.Error -> ErrorBox(
                        title = "Profiller yüklenemedi",
                        message = s.message,
                        actions = listOf("Tekrar dene" to { vm.load() }, "Adresi değiştir" to onOpenSettings),
                    )
                    is UiState.Data -> {
                        if (s.value.isEmpty()) {
                            ErrorBox(
                                title = "Profil yok",
                                message = "Sunucuda henüz profil bulunmuyor.",
                                actions = listOf("Tekrar dene" to { vm.load() }, "Ayarlar" to onOpenSettings),
                            )
                        } else {
                            LazyVerticalGrid(
                                columns = GridCells.Adaptive(minSize = 120.dp),
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                verticalArrangement = Arrangement.spacedBy(20.dp),
                                modifier = Modifier.fillMaxSize(),
                            ) {
                                itemsIndexed(s.value, key = { _, profile -> profile.id }) { _, profile ->
                                    ProfileTile(profile = profile, onClick = { vm.select(profile) })
                                }
                            }
                        }
                    }
                }
            }

            TextButton(onClick = onOpenSettings) {
                Text("Ayarlar", color = DzColors.Muted)
            }
        }
    }
}

@Composable
private fun ProfileTile(profile: Profile, onClick: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        RemoteImage(
            path = profile.avatar,
            width = UrlUtil.AVATAR,
            height = UrlUtil.AVATAR,
            forceSize = true,
            rgb565 = true,
            fallbackText = profile.name.trim().take(1).uppercase(),
            fallbackFontSize = 36.sp,
            modifier = Modifier
                .size(96.dp)
                .clip(CircleShape),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            profile.name,
            color = Color.White,
            style = MaterialTheme.typography.bodyLarge,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
        )
        if (profile.isKids) {
            Text("ÇOCUK", color = DzColors.Warning, style = MaterialTheme.typography.labelSmall)
        }
    }
}
