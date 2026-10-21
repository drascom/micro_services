package com.diziflix.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.diziflix.app.AppContainer
import com.diziflix.app.data.net.ApiClient
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.data.net.apiCall
import com.diziflix.app.data.net.userMessage
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.domain.PlayPrefs
import com.diziflix.app.domain.TvSettingsLogic
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class SettingsUiState(
    val input: String = UrlUtil.DEFAULT_BASE_URL,
    val status: String? = null,
    val statusIsError: Boolean = false,
    val busy: Boolean = false,
    // ---- yalnızca TV ayarlar ekranı okur (tablet arayüzü bunları göstermez)
    /** Profil seçili mi (altyazı tercihi profil başınadır). */
    val hasProfile: Boolean = false,
    /** "Varsayılan altyazı dili" ("tr" | "en" | "off"; kayıt yoksa "tr"). */
    val subPref: String = PlayPrefs.SUB_TR,
    /** "En yüksek kalite" ("1080" | "1440" | "auto"; kayıt yoksa TV'de 1080, diğerlerinde auto). */
    val qualityPref: String = PlayPrefs.QUALITY_AUTO,
    /** Alt bilgi satırı: "Sunucu: library · 121 içerik". */
    val serverInfo: String = TvSettingsLogic.SERVER_PENDING,
)

class SettingsViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(SettingsUiState())
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val saved = container.settings.awaitLoaded()
            _state.update { it.copy(input = saved.baseUrl) }
        }
        // Oynatma tercihleri (DataStore) durumla eşlenir.
        viewModelScope.launch {
            container.settings.settings.filterNotNull().collect { saved ->
                _state.update {
                    it.copy(
                        hasProfile = saved.profileId != null,
                        subPref = PlayPrefs.displaySub(saved.subPref),
                        qualityPref = PlayPrefs.effectiveQuality(saved.qualityPref, container.isTv),
                    )
                }
            }
        }
    }

    /** Sessiz ilk kontrol: alt bilgi satırını doldurur (durum satırına dokunmaz). TV ekranı açılışta çağırır. */
    fun probeServer() {
        viewModelScope.launch {
            val base = container.settings.awaitLoaded().baseUrl
            val probe = ApiClient(container.http, { base }, container.json)
            apiCall { probe.health() }.fold(
                onSuccess = { h -> _state.update { it.copy(serverInfo = TvSettingsLogic.serverInfo(h.source, h.items)) } },
                onFailure = { _state.update { it.copy(serverInfo = TvSettingsLogic.SERVER_DOWN) } },
            )
        }
    }

    fun setSubPref(value: String) {
        viewModelScope.launch {
            val profile = container.settings.awaitLoaded().profileId ?: return@launch
            container.settings.setSubPref(profile, value)
            _state.update { it.copy(status = TvSettingsLogic.subStatus(value), statusIsError = false) }
        }
    }

    fun setQualityPref(value: String) {
        viewModelScope.launch {
            container.settings.setQualityPref(value)
            _state.update { it.copy(status = TvSettingsLogic.qualityStatus(value), statusIsError = false) }
        }
    }

    fun onInput(value: String) {
        _state.update { it.copy(input = value, status = null) }
    }

    fun save() {
        val normalized = UrlUtil.normalizeBaseUrl(_state.value.input)
        if (normalized == null) {
            _state.update { it.copy(status = "Geçersiz adres. Örnek: ${UrlUtil.DEFAULT_BASE_URL}", statusIsError = true) }
            return
        }
        viewModelScope.launch {
            val changed = container.settings.awaitLoaded().baseUrl != normalized
            container.settings.setBaseUrl(normalized)
            // Başka sunucunun önbelleği (içerik + çevrimdışı kopya + görseller + bekleyen ilerleme) karışmasın.
            if (changed) {
                withContext(Dispatchers.IO) {
                    container.clearCaches()
                    container.localCache.bindServer(normalized)   // eski sunucunun kuyruğu/kopyası silinir
                }
            }
            _state.update { it.copy(input = normalized, status = "Kaydedildi: $normalized", statusIsError = false) }
        }
    }

    /** Yazılı adresi KAYDETMEDEN /api/health ile dener. */
    fun test() {
        val normalized = UrlUtil.normalizeBaseUrl(_state.value.input)
        if (normalized == null) {
            _state.update { it.copy(status = "Geçersiz adres. Örnek: ${UrlUtil.DEFAULT_BASE_URL}", statusIsError = true) }
            return
        }
        _state.update { it.copy(busy = true, status = "Test ediliyor…", statusIsError = false) }
        viewModelScope.launch {
            val probe = ApiClient(container.http, { normalized }, container.json)
            apiCall { probe.health() }.fold(
                onSuccess = { h ->
                    _state.update {
                        it.copy(
                            busy = false,
                            status = "Bağlantı OK · kaynak: ${h.source ?: "?"} · içerik: ${h.items ?: "?"}",
                            statusIsError = false,
                            serverInfo = TvSettingsLogic.serverInfo(h.source, h.items),
                        )
                    }
                },
                onFailure = { e ->
                    _state.update {
                        it.copy(busy = false, status = "Başarısız: ${e.userMessage()}", statusIsError = true, serverInfo = TvSettingsLogic.SERVER_DOWN)
                    }
                },
            )
        }
    }

    fun clearCache() {
        container.appScope.launch {
            container.clearCaches()   // çevrimdışı kopya + görseller + HTTP; bekleyen ilerleme kuyruğu kalır
            _state.update { it.copy(status = "Önbellek temizlendi", statusIsError = false) }
        }
    }

    fun switchProfile() {
        container.appScope.launch { container.settings.setProfileId(null) }
    }
}

/**
 * Sunucu adresi + bağlantı testi + profil değiştirme + önbellek. [showSwitchProfile] profil seçilmemişken
 * (zaten profil ekranındayken) gizlenir.
 */
@Composable
fun SettingsScreen(onBack: () -> Unit, showSwitchProfile: Boolean) {
    val vm = containerViewModel { SettingsViewModel(it) }
    val state by vm.state.collectAsStateWithLifecycle()
    val s = state

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Geri", tint = Color.White)
            }
            Text("Ayarlar", style = MaterialTheme.typography.headlineSmall, color = Color.White)
        }
        Spacer(Modifier.height(16.dp))

        Text("Sunucu adresi", color = DzColors.Muted, style = MaterialTheme.typography.labelLarge)
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = s.input,
            onValueChange = vm::onInput,
            singleLine = true,
            placeholder = { Text(UrlUtil.DEFAULT_BASE_URL) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = DzColors.Primary,
                unfocusedBorderColor = Color(0xFF3A3A42),
                cursorColor = DzColors.Primary,
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        val status = s.status
        if (status != null) {
            Spacer(Modifier.height(10.dp))
            Text(
                status,
                color = if (s.statusIsError) MaterialTheme.colorScheme.error else DzColors.Warning,
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.save() }) { Text("Kaydet") }
            FilledTonalButton(onClick = { vm.test() }, enabled = !s.busy) { Text("Bağlantıyı Test Et") }
        }

        Spacer(Modifier.height(24.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (showSwitchProfile) {
                FilledTonalButton(onClick = { vm.switchProfile() }) { Text("Profil Değiştir") }
            }
            FilledTonalButton(onClick = { vm.clearCache() }) { Text("Önbelleği Temizle") }
        }

        Spacer(Modifier.height(32.dp))
        Text(
            "Varsayılan: ${UrlUtil.DEFAULT_BASE_URL}\nDiziflix Android 1.0.0 · Oynatıcı: Media3 ExoPlayer",
            color = DzColors.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
