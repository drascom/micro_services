package com.diziflix.app.ui.nav

import android.net.Uri
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Tv
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.diziflix.app.ui.catalog.CatalogScreen
import com.diziflix.app.ui.catalog.CatalogView
import com.diziflix.app.ui.catalog.SearchScreen
import com.diziflix.app.ui.common.LocalContainer
import com.diziflix.app.ui.detail.DetailScreen
import com.diziflix.app.ui.home.HomeScreen
import com.diziflix.app.ui.player.PlayerScreen
import com.diziflix.app.ui.settings.SettingsScreen
import com.diziflix.app.ui.theme.DzColors
import com.diziflix.app.ui.tv.LocalIsTv
import com.diziflix.app.ui.tv.LocalTvToaster
import com.diziflix.app.ui.tv.TvBarActions
import com.diziflix.app.ui.tv.TvBarFocus
import com.diziflix.app.ui.tv.TvCatalogScreen
import com.diziflix.app.ui.tv.TvDetailScreen
import com.diziflix.app.ui.tv.TvHomeScreen
import com.diziflix.app.ui.tv.TvNavFrame
import com.diziflix.app.ui.tv.TvPlayerScreen
import com.diziflix.app.ui.tv.TvSearchScreen
import com.diziflix.app.ui.tv.TvSettingsScreen
import com.diziflix.app.ui.tv.TvTopBarTab
import com.diziflix.app.domain.TvBarItem
import com.diziflix.app.domain.TvRichToasts
import com.diziflix.app.domain.ErrorLogic
import com.diziflix.app.domain.HydrateLogic
import com.diziflix.app.domain.NotificationLogic
import com.diziflix.app.domain.NotificationToast
import com.diziflix.app.domain.TvToastSpec
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Rota sabitleri ve güvenli rota üreticileri (kimlikler URL-kodlanır; bölüm kimliklerinde ':' vardır). */
object Routes {
    const val HOME = "home"
    const val MOVIES = "movies"
    const val SERIES = "series"
    const val MYLIST = "mylist"
    const val SEARCH = "search"
    const val SETTINGS = "settings"

    /** Ana sayfa trend/dikkate değer satırlarının "Tümü" kataloğu (`trending_series` | `trending_movies` | `noteworthy_movies`). */
    const val CATALOG = "catalog/{row}"

    const val DETAIL = "detail/{id}?episode={episode}"
    const val PLAYER = "player/{id}?episode={episode}&kind={kind}"

    val TOP_LEVEL = setOf(HOME, MOVIES, SERIES, MYLIST, SEARCH)

    /** Satır kimlikleri sabit küçük harf/alt çizgi belirteçleridir: kodlama gerekmez. */
    fun catalog(rowId: String): String = "catalog/$rowId"

    fun detail(id: String, episodeId: String? = null): String {
        val base = "detail/" + Uri.encode(id)
        return if (episodeId.isNullOrBlank()) base else base + "?episode=" + Uri.encode(episodeId)
    }

    fun player(id: String, episodeId: String?, kind: String): String {
        val base = "player/" + Uri.encode(id) + "?kind=" + Uri.encode(kind)
        return if (episodeId.isNullOrBlank()) base else base + "&episode=" + Uri.encode(episodeId)
    }
}

private data class Tab(val route: String, val label: String, val icon: ImageVector)

private val TABS = listOf(
    Tab(Routes.HOME, "Ana Sayfa", Icons.Filled.Home),
    Tab(Routes.MOVIES, "Filmler", Icons.Filled.Movie),
    Tab(Routes.SERIES, "Diziler", Icons.Filled.Tv),
    Tab(Routes.MYLIST, "Listem", Icons.Filled.Favorite),
    Tab(Routes.SEARCH, "Ara", Icons.Filled.Search),
)

/**
 * Profil seçildikten sonraki tek NavHost. Tablet/telefonda alt gezinme çubuğu yalnızca üst düzey sekmelerde görünür;
 * TV'de Tizen üst menüsü ([TvNavFrame]) kullanılır. Detay ve oynatıcı tam ekrandır. Ekranlar TV'de AYRI TV
 * composable'larıyla açılır (ViewModel/veri katmanı ortak); tablet ekranlarının içeriği TV'den etkilenmez.
 */
@Composable
fun MainNav(profileId: String, onSwitchProfile: () -> Unit) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val route = backStackEntry?.destination?.route
    val isTv = LocalIsTv.current
    val toaster = LocalTvToaster.current

    // Bildirimler: tablet/telefonda Snackbar, TV'de Tizen toast'ı (alt orta küçük kart).
    val container = LocalContainer.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val notify: (String) -> Unit = remember(toaster, isTv) {
        { message ->
            if (isTv) {
                toaster?.show(message)
            } else {
                // Var olan bildirimin yerine geçer (üst üste binmesin/sıraya girmesin).
                scope.launch {
                    snackbar.currentSnackbarData?.dismiss()
                    snackbar.showSnackbar(message = message, duration = SnackbarDuration.Short)
                }
            }
        }
    }
    // Çevrimdışıyken Oynat/Fragman: yükleme ekranına ve akış isteğine HİÇ girmeden net mesaj.
    val play: (String, String?, String) -> Unit = remember(navController, container, notify) {
        { id, episode, kind ->
            if (container.isOnline()) navController.navigate(Routes.player(id, episode, kind)) else notify(ErrorLogic.OFFLINE_PLAY_MESSAGE)
        }
    }

    // Global hidrasyon bildirimi: hangi ekranda olursa olsun «Başlık» izlemeye hazır / alınamadı.
    // Odak çalmaz (TV kumandası gezinmesi bozulmaz). Süre dolması/ağ hatasında olay yok.
    LaunchedEffect(container, toaster, isTv) {
        container.hydrateWatcher.events.collect { event ->
            if (isTv) {
                toaster?.showRich(TvRichToasts.forHydrate(event))
            } else {
                launch { snackbar.showSnackbar(message = HydrateLogic.message(event), duration = SnackbarDuration.Short) }
            }
        }
    }

    // Kaynak bulucu bildirimleri ("Kaynak bulundu: ..."): uygulama açıkken 30 sn'de bir yoklanır; oynatıcıdayken DURUR,
    // çıkınca hemen bir kez sorulur (efekt rotaya göre yeniden başlar). Toast'lar sırayla gösterilir; tablet/telefonda
    // Snackbar'daki "Aç" ilgili detaya gider, TV'de toast yalnızca bilgidir (odak çalmaz).
    val toastQueue = remember { Channel<NotificationToast>(Channel.UNLIMITED) }
    LaunchedEffect(toastQueue, toaster, isTv) {
        for (toast in toastQueue) {
            val target = toast.target
            if (isTv) {
                toaster?.show(toast.message)
                delay(TvToastSpec.TOAST_MS + 300L)
            } else {
                val result = snackbar.showSnackbar(
                    message = toast.message,
                    actionLabel = if (target != null) "Aç" else null,
                    duration = SnackbarDuration.Long,
                )
                if (result == SnackbarResult.ActionPerformed && target != null) {
                    navController.navigate(Routes.detail(target.canonicalId, target.episodeId))
                }
            }
        }
    }
    val polling = NotificationLogic.shouldPoll(route)
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(container, profileId, polling) {
        if (!polling) return@LaunchedEffect
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                try {
                    container.notificationPoller.pollOnce(profileId) { items ->
                        NotificationLogic.toasts(items).forEach { toastQueue.trySend(it) }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    // ağ yok / sunucu hatası: sessizce sonraki turda yeniden denenir (imleç ilerlemedi)
                }
                delay(NotificationLogic.POLL_INTERVAL_MS)
            }
        }
    }

    if (isTv) {
        val focusManager = LocalFocusManager.current
        // Üst menü öğelerinin odak hedefleri + "öğeyle ayrılıp Geri ile dönünce o öğeye odak" belleği. İçerikten Yukarı ile
        // girilen ilk öğe arama kutusudur (Tizen navigation.js INPUT_COL).
        val barFocus = remember { TvBarFocus() }
        val currentRoute = route ?: Routes.HOME
        LaunchedEffect(currentRoute) { barFocus.returns.onRoute(currentRoute) }
        val bar = remember(navController, route, onSwitchProfile) {
            TvBarActions(
                // Logo = ana sayfa; ana sayfadaysa içeriğe (hero) iner.
                onHome = {
                    barFocus.returns.clear()
                    if (currentRoute == Routes.HOME) focusManager.moveFocus(FocusDirection.Down) else navigateTab(navController, Routes.HOME)
                },
                onSearch = {
                    barFocus.returns.arm(TvBarItem.Search, currentRoute, Routes.SEARCH)
                    navigateTab(navController, Routes.SEARCH)
                },
                onMyList = {
                    barFocus.returns.arm(TvBarItem.MyList, currentRoute, Routes.MYLIST)
                    navigateTab(navController, Routes.MYLIST)
                },
                onProfile = onSwitchProfile,
                onSettings = {
                    barFocus.returns.arm(TvBarItem.Settings, currentRoute, Routes.SETTINGS)
                    navController.navigate(Routes.SETTINGS)
                },
            )
        }
        // Başlangıç hedefi (ana sayfa) ilk karede de bilinir: üst menü sonradan belirip içeriği kaydırmasın.
        val tvRoute = route ?: Routes.HOME
        TvNavFrame(
            // Arama sayfası üst menüsünü (gerçek yazı alanıyla) kendisi çizer.
            showBar = (tvRoute in Routes.TOP_LEVEL || tvRoute == Routes.CATALOG) && tvRoute != Routes.SEARCH,
            selected = if (tvRoute == Routes.MYLIST) TvTopBarTab.MyList else null,
            bar = bar,
            barFocus = barFocus,
        ) {
            MainNavGraph(
                navController = navController,
                profileId = profileId,
                onSwitchProfile = onSwitchProfile,
                play = play,
                notify = notify,
                isTv = true,
                tvBar = bar,
                barFocus = barFocus,
                modifier = Modifier,
            )
        }
        return
    }

    Scaffold(
        containerColor = DzColors.Background,
        snackbarHost = { SnackbarHost(snackbar) },
        // Sistem çubukları temaya bırakıldı (kenardan kenara değil); Scaffold ek boşluk eklemesin.
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        bottomBar = {
            if (route in Routes.TOP_LEVEL || route == Routes.CATALOG) {
                NavigationBar(containerColor = DzColors.Surface) {
                    TABS.forEach { tab ->
                        NavigationBarItem(
                            selected = route == tab.route,
                            onClick = { navigateTab(navController, tab.route) },
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label, maxLines = 1) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = Color.White,
                                selectedTextColor = Color.White,
                                indicatorColor = DzColors.Primary,
                                unselectedIconColor = DzColors.Muted,
                                unselectedTextColor = DzColors.Muted,
                            ),
                        )
                    }
                }
            }
        },
    ) { padding ->
        MainNavGraph(
            navController = navController,
            profileId = profileId,
            onSwitchProfile = onSwitchProfile,
            play = play,
            notify = notify,
            isTv = false,
            tvBar = null,
            barFocus = null,
            modifier = Modifier.padding(padding),
        )
    }
}

@Composable
private fun MainNavGraph(
    navController: NavHostController,
    profileId: String,
    onSwitchProfile: () -> Unit,
    play: (String, String?, String) -> Unit,
    notify: (String) -> Unit,
    isTv: Boolean,
    tvBar: TvBarActions?,
    barFocus: TvBarFocus?,
    modifier: Modifier,
) {
    NavHost(
        navController = navController,
        startDestination = Routes.HOME,
        modifier = modifier,
    ) {
        composable(Routes.HOME) {
            // TV: Tizen ana sayfası (ayrı ekran); tablet/telefon: mevcut HomeScreen.
            if (isTv && barFocus != null) {
                TvHomeScreen(
                    profileId = profileId,
                    onOpenDetail = { id, episode -> navController.navigate(Routes.detail(id, episode)) },
                    onOpenTab = { tabRoute -> navigateTab(navController, tabRoute) },
                    onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                    onSwitchProfile = onSwitchProfile,
                    topBarFocus = barFocus.entry,
                    barFocus = barFocus,
                    onMessage = notify,
                )
                return@composable
            }
            HomeScreen(
                profileId = profileId,
                onOpenDetail = { id, episode -> navController.navigate(Routes.detail(id, episode)) },
                onPlay = play,
                onOpenTab = { tabRoute -> navigateTab(navController, tabRoute) },
                onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                onSwitchProfile = onSwitchProfile,
                onMessage = notify,
            )
        }
        composable(Routes.MOVIES) {
            if (isTv && barFocus != null) {
                TvCatalogScreen(CatalogView.Movies, profileId, { id -> navController.navigate(Routes.detail(id)) }, barFocus, Routes.MOVIES)
                return@composable
            }
            CatalogScreen(
                view = CatalogView.Movies,
                profileId = profileId,
                onOpenDetail = { id -> navController.navigate(Routes.detail(id)) },
            )
        }
        composable(Routes.SERIES) {
            if (isTv && barFocus != null) {
                TvCatalogScreen(CatalogView.Series, profileId, { id -> navController.navigate(Routes.detail(id)) }, barFocus, Routes.SERIES)
                return@composable
            }
            CatalogScreen(
                view = CatalogView.Series,
                profileId = profileId,
                onOpenDetail = { id -> navController.navigate(Routes.detail(id)) },
            )
        }
        composable(Routes.MYLIST) {
            if (isTv && barFocus != null) {
                TvCatalogScreen(CatalogView.MyList, profileId, { id -> navController.navigate(Routes.detail(id)) }, barFocus, Routes.MYLIST)
                return@composable
            }
            CatalogScreen(
                view = CatalogView.MyList,
                profileId = profileId,
                onOpenDetail = { id -> navController.navigate(Routes.detail(id)) },
            )
        }
        composable(
            route = Routes.CATALOG,
            arguments = listOf(navArgument("row") { type = NavType.StringType }),
        ) { entry ->
            // Trend/dikkate değer satırının "Tümü" kartı: sunucu `sort=trending|popular` ile (başlık satırınkiyle aynı).
            val view = CatalogView.forRow(entry.arguments?.getString("row"))
            if (view == null) {
                LaunchedEffect(Unit) { navController.popBackStack() }
                return@composable
            }
            if (isTv && barFocus != null) {
                TvCatalogScreen(view, profileId, { id -> navController.navigate(Routes.detail(id)) }, barFocus, Routes.CATALOG)
                return@composable
            }
            CatalogScreen(
                view = view,
                profileId = profileId,
                onOpenDetail = { id -> navController.navigate(Routes.detail(id)) },
            )
        }
        composable(Routes.SEARCH) {
            if (isTv && tvBar != null) {
                TvSearchScreen(profileId, { id -> navController.navigate(Routes.detail(id)) }, tvBar)
                return@composable
            }
            SearchScreen(
                profileId = profileId,
                onOpenDetail = { id -> navController.navigate(Routes.detail(id)) },
            )
        }
        composable(Routes.SETTINGS) {
            if (isTv) {
                TvSettingsScreen(showSwitchProfile = true)
                return@composable
            }
            SettingsScreen(
                onBack = { navController.popBackStack() },
                showSwitchProfile = true,
            )
        }
        composable(
            route = Routes.DETAIL,
            arguments = listOf(
                navArgument("id") { type = NavType.StringType },
                navArgument("episode") {
                    type = NavType.StringType
                    nullable = true
                    defaultValue = null
                },
            ),
        ) { entry ->
            val id = entry.arguments?.getString("id").orEmpty()
            val episode = entry.arguments?.getString("episode")
            if (isTv) {
                TvDetailScreen(
                    itemId = id,
                    initialEpisodeId = episode,
                    profileId = profileId,
                    onBack = { navController.popBackStack() },
                    onOpenDetail = { other -> navController.navigate(Routes.detail(other)) },
                    onPlay = play,
                )
                return@composable
            }
            DetailScreen(
                itemId = id,
                initialEpisodeId = episode,
                profileId = profileId,
                onBack = { navController.popBackStack() },
                onOpenDetail = { other -> navController.navigate(Routes.detail(other)) },
                onPlay = play,
            )
        }
        composable(
            route = Routes.PLAYER,
            arguments = listOf(
                navArgument("id") { type = NavType.StringType },
                navArgument("episode") {
                    type = NavType.StringType
                    nullable = true
                    defaultValue = null
                },
                navArgument("kind") {
                    type = NavType.StringType
                    defaultValue = "video"
                },
            ),
        ) { entry ->
            val id = entry.arguments?.getString("id").orEmpty()
            val episode = entry.arguments?.getString("episode")
            val kind = entry.arguments?.getString("kind") ?: "video"
            if (isTv) {
                TvPlayerScreen(itemId = id, episodeId = episode, kind = kind, profileId = profileId, onBack = { navController.popBackStack() })
                return@composable
            }
            PlayerScreen(
                itemId = id,
                episodeId = episode,
                kind = kind,
                profileId = profileId,
                onBack = { navController.popBackStack() },
            )
        }
    }
}

/** Alt çubuk sekmeleri: ana sayfaya kadar geri sar, durumu koru, tek örnek. */
private fun navigateTab(navController: NavHostController, tabRoute: String) {
    navController.navigate(tabRoute) {
        popUpTo(Routes.HOME) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
