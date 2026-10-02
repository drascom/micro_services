package com.diziflix.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.ui.common.BrandBoot
import com.diziflix.app.ui.common.LocalBaseUrl
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.nav.MainNav
import com.diziflix.app.ui.profiles.ProfilesScreen
import com.diziflix.app.ui.settings.SettingsScreen
import com.diziflix.app.ui.theme.DiziflixTheme
import com.diziflix.app.ui.theme.DzColors
import com.diziflix.app.ui.tv.LocalIsTv
import com.diziflix.app.ui.tv.detectTv
import com.diziflix.app.ui.tv.LocalTvToaster
import com.diziflix.app.ui.tv.TvBootLogo
import com.diziflix.app.ui.tv.TvColors
import com.diziflix.app.ui.tv.TvProfilesScreen
import com.diziflix.app.ui.tv.TvSettingsScreen
import com.diziflix.app.ui.tv.TvTheme
import com.diziflix.app.ui.tv.TvToastHost
import com.diziflix.app.ui.tv.rememberTvToaster
import kotlinx.coroutines.launch

/** Tek Activity: Compose içerik + Navigation-Compose. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as DiziflixApp).container
        // Android TV algılaması çalışma zamanında (tek APK): TV'ye özgü tüm davranış LocalIsTv'ye bağlıdır.
        val isTv = detectTv(this)
        setContent {
            CompositionLocalProvider(LocalIsTv provides isTv) {
                DiziflixTheme {
                    AppRoot(container)
                }
            }
        }
    }
}

/**
 * Kök: ayarlar yüklenene kadar boş; profil seçilmemişse profil ekranı (seçim DataStore'da hatırlanır);
 * seçiliyse ana gezinme. Profil değişince gezinme yığını sıfırlanır.
 */
@Composable
private fun AppRoot(container: AppContainer) {
    val settings by container.settings.settings.collectAsStateWithLifecycle()
    val current = settings
    val baseUrl = current?.baseUrl ?: UrlUtil.DEFAULT_BASE_URL
    val isTv = LocalIsTv.current
    val toaster = if (isTv) rememberTvToaster() else null

    CompositionLocalProvider(
        LocalContainer provides container,
        LocalBaseUrl provides baseUrl,
        LocalTvToaster provides toaster,
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = if (isTv) TvColors.Background else DzColors.Background,
            contentColor = DzColors.OnSurface,
        ) {
            Box(Modifier.fillMaxSize()) {
                if (current == null) {
                    // Ayarlar (DataStore) yüklenene kadar: ortada kare marka logosu (Tizen `bootLogo()`).
                    if (isTv) TvTheme { TvBootLogo(Modifier.align(Alignment.Center)) } else BrandBoot()
                } else {
                    val profileId = current.profileId
                    if (profileId == null) {
                        ProfileGate()
                    } else {
                        MainNav(
                            profileId = profileId,
                            onSwitchProfile = {
                                container.appScope.launch { container.settings.setProfileId(null) }
                            },
                        )
                    }
                }
                // TV bildirimleri (küçük toast + zengin kart) her ekranın ÜSTÜNDE; odaklanmaz.
                if (toaster != null) TvTheme { TvToastHost(toaster) }
            }
        }
    }
}

/** Profil seçilmemişken: profil ekranı; oradan Ayarlar (sunucu adresi) açılabilir. */
@Composable
private fun ProfileGate() {
    var showSettings by remember { mutableStateOf(false) }
    if (showSettings) BackHandler { showSettings = false }
    if (LocalIsTv.current) {
        // TV: Tizen profil seçimi / ayarlar (kendi güvenli alanlarıyla).
        if (showSettings) TvSettingsScreen(showSwitchProfile = false) else TvProfilesScreen(onOpenSettings = { showSettings = true })
        return
    }
    Box(Modifier.fillMaxSize()) {
        if (showSettings) {
            SettingsScreen(onBack = { showSettings = false }, showSwitchProfile = false)
        } else {
            ProfilesScreen(onOpenSettings = { showSettings = true })
        }
    }
}
