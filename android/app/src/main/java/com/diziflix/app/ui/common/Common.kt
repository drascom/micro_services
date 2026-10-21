package com.diziflix.app.ui.common

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Item
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.domain.ContinueLogic
import com.diziflix.app.domain.LayoutLogic
import com.diziflix.app.domain.OfflineNotice
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.flow.distinctUntilChanged

/** Geçerli sunucu taban adresi (görsel adreslerini mutlaklaştırmak için). */
val LocalBaseUrl = staticCompositionLocalOf { UrlUtil.DEFAULT_BASE_URL }

val LocalContainer = staticCompositionLocalOf<AppContainer> { error("AppContainer sağlanmadı") }

/** Yükleme / hata / veri durumları. */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Error(val message: String) : UiState<Nothing>
    data class Data<T>(val value: T) : UiState<T>
}

/** AppContainer'dan ViewModel üretir (DI kütüphanesi yok). [key] farklı argümanlar için ayrı örnek sağlar. */
@Composable
inline fun <reified VM : ViewModel> containerViewModel(
    key: String? = null,
    crossinline builder: (AppContainer) -> VM,
): VM {
    val container = LocalContainer.current
    return viewModel(
        key = key,
        factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = builder(container) as T
        },
    )
}

/** Ekran her ÖN plana geldiğinde (ilk açılış dahil) çağrılır. */
@Composable
fun OnResumeEffect(onResume: () -> Unit) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val current by rememberUpdatedState(onResume)
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) current()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
}

@Composable
fun LoadingBox(modifier: Modifier = Modifier, label: String? = null) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = DzColors.Primary)
            if (label != null) {
                Spacer(Modifier.height(12.dp))
                Text(label, color = DzColors.Muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            }
        }
    }
}

/** Hata ekranı: mesaj + isteğe bağlı eylem düğmeleri (ilki vurgulu). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ErrorBox(
    message: String,
    modifier: Modifier = Modifier,
    title: String = "Bir sorun oluştu",
    actions: List<Pair<String, () -> Unit>> = emptyList(),
) {
    Box(modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            BrandMascot(height = 56.dp)
            Spacer(Modifier.height(10.dp))
            Text(title, style = MaterialTheme.typography.titleLarge, color = Color.White, textAlign = TextAlign.Center)
            Spacer(Modifier.height(8.dp))
            Text(message, color = DzColors.Muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(20.dp))
            // Dar ekranda 3 düğme yan yana sığmaz: satıra sarılır (yatay taşma olmaz).
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterHorizontally),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                actions.forEachIndexed { index, (label, onClick) ->
                    if (index == 0) {
                        Button(onClick = onClick) { Text(label) }
                    } else {
                        FilledTonalButton(onClick = onClick) { Text(label) }
                    }
                }
            }
        }
    }
}

@Composable
fun EmptyBox(message: String, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(message, color = DzColors.Muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
    }
}

/** Yuvarlak seçim/filtre etiketi (kararlı foundation bileşenleriyle). */
@Composable
fun Chip(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, selected: Boolean = false) {
    Box(
        modifier
            .clip(RoundedCornerShape(50))
            .background(if (selected) DzColors.Primary else DzColors.SurfaceHigh)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = Color.White, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** İnce ilerleme çubuğu; [fraction] 0..1. */
@Composable
fun ProgressBar(fraction: Float, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(3.dp).background(Color.Black.copy(alpha = 0.55f))) {
        Box(
            Modifier
                .fillMaxHeight()
                .fillMaxWidth(fraction.coerceIn(0f, 1f))
                .background(DzColors.Primary),
        )
    }
}

/**
 * Resim yükleyici. [width]x[height] hem /img isteğinin hem de Coil'in TAM hedef boyutudur (bkz. UrlUtil:
 * küçük izinli boyutlar); istek kararlı bir [ImageRequest] olarak hatırlanır, crossfade yok.
 * [rgb565]: afiş/bölüm görselleri için 2 bayt/piksel (yarı bellek); hero/artalanda kapalı (degrade bantlanmasın).
 * Yer tutucu yalnızca renk; [fallbackText] yalnızca adres yoksa ya da yükleme BAŞARISIZ olursa çizilir
 * (her karta baştan metin yerleşimi yapılmaz).
 */
@Composable
fun RemoteImage(
    path: String?,
    width: Int,
    height: Int,
    modifier: Modifier = Modifier,
    forceSize: Boolean = false,
    rgb565: Boolean = false,
    fallbackText: String? = null,
    fallbackFontSize: TextUnit = 12.sp,
) {
    val base = LocalBaseUrl.current
    val context = LocalContext.current
    val url = remember(base, path, width, height, forceSize) {
        if (forceSize) UrlUtil.sizedTo(base, path, width, height) else UrlUtil.sized(base, path, width, height)
    }
    var failed by remember(url) { mutableStateOf(false) }
    Box(modifier.background(DzColors.SurfaceHigh), contentAlignment = Alignment.Center) {
        if (!fallbackText.isNullOrBlank() && (url.isEmpty() || failed)) {
            Text(
                fallbackText,
                color = DzColors.Muted,
                fontSize = fallbackFontSize,
                textAlign = TextAlign.Center,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(6.dp),
            )
        }
        if (url.isNotEmpty()) {
            val request = remember(url, width, height, rgb565) {
                ImageRequest.Builder(context)
                    .data(url)
                    .size(width, height)
                    .crossfade(false)
                    .allowRgb565(rgb565)
                    .build()
            }
            AsyncImage(
                model = request,
                contentDescription = fallbackText,
                contentScale = ContentScale.Crop,
                onError = { failed = true },
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

private val PosterShape = RoundedCornerShape(8.dp)
private val BadgeShape = RoundedCornerShape(4.dp)

/**
 * Dikey poster kartı (2:3), rozet ve ilerleme çubuğuyla.
 * [onRemove] verilirse (yalnızca "İzlemeye Devam Et" satırı) uzun basış küçük bir menü açar: "Listeden kaldır" ve
 * "Vazgeç". Uzun basıştan sonra tıklama detayı AÇMAZ (combinedClickable uzun basışı tüketir); kısa dokunuş eskisi gibi.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun PosterCard(
    item: Item,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onRemove: (() -> Unit)? = null,
) {
    var menuOpen by remember { mutableStateOf(false) }
    val haptic = LocalHapticFeedback.current
    val clickModifier = if (onRemove == null) {
        Modifier.clickable(onClick = onClick)
    } else {
        Modifier.combinedClickable(
            onClick = onClick,
            onLongClick = {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                menuOpen = true
            },
        )
    }
    Column(modifier.then(clickModifier)) {
        Box(
            Modifier
                .fillMaxWidth()
                .aspectRatio(2f / 3f)
                .clip(PosterShape),
        ) {
            RemoteImage(
                path = item.portrait ?: item.card,
                width = UrlUtil.POSTER_W,
                height = UrlUtil.POSTER_H,
                forceSize = true,
                rgb565 = true,
                modifier = Modifier.fillMaxSize(),
                fallbackText = item.title,
            )
            val badge = item.badge
            if (!badge.isNullOrBlank()) {
                Text(
                    badge,
                    color = Color.White,
                    fontSize = 10.sp,
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(4.dp)
                        .clip(BadgeShape)
                        .background(DzColors.Primary)
                        .padding(horizontal = 5.dp, vertical = 2.dp),
                )
            }
            val pct = item.progress?.pct ?: 0.0
            if (pct > 0.0) {
                ProgressBar(
                    fraction = (pct / 100.0).toFloat(),
                    modifier = Modifier.align(Alignment.BottomCenter),
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            item.title,
            style = MaterialTheme.typography.labelMedium,
            color = Color.White,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        // Bölüm kartı (yeni bölümler / devam et): "S04 B10 · Başlık"
        val episodeLabel = item.episodeLabel
        if (item.cardKind == "episode" && !episodeLabel.isNullOrBlank()) {
            Text(
                episodeLabel,
                style = MaterialTheme.typography.labelSmall,
                color = DzColors.Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (onRemove != null) {
            DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                DropdownMenuItem(
                    text = { Text(ContinueLogic.MENU_REMOVE, fontWeight = FontWeight.Bold) },
                    onClick = {
                        menuOpen = false
                        onRemove()
                    },
                )
                DropdownMenuItem(
                    text = { Text(ContinueLogic.MENU_CANCEL, color = DzColors.Muted) },
                    onClick = { menuOpen = false },
                )
            }
        }
    }
}

/**
 * Yatay poster sırası (ana ekran satırları ve "Benzer yapımlar"). Başlangıçta en çok
 * [LayoutLogic.INITIAL_CARDS] kart verilir; kullanıcı sona yaklaştıkça [LayoutLogic.CARDS_STEP] kadar
 * eklenir. Kartlar kararlı anahtar (+ contentType: kaydırırken kompozisyon yeniden kullanımı) ile çizilir.
 */
@Composable
fun PosterRow(
    items: List<Item>,
    onItemClick: (Item) -> Unit,
    modifier: Modifier = Modifier,
    /** Verilirse kartlara uzun basış menüsü ("Listeden kaldır") eklenir; yalnızca Devam Et satırı için. */
    onItemRemove: ((Item) -> Unit)? = null,
) {
    val cardWidth = LayoutLogic.posterWidthDp(LocalConfiguration.current.screenWidthDp.toFloat()).dp
    val cardModifier = remember(cardWidth) { Modifier.width(cardWidth) }
    // Aynı anahtarlı yinelenen kart çökertmesin (listKey benzersiz olmalı).
    val unique = remember(items) { items.distinctBy { it.listKey } }
    var limit by remember(unique) { mutableIntStateOf(minOf(unique.size, LayoutLogic.INITIAL_CARDS)) }
    val listState = rememberLazyListState()
    if (unique.size > limit) {
        LaunchedEffect(listState, unique) {
            snapshotFlow { listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1 }
                .distinctUntilChanged()
                .collect { last -> limit = LayoutLogic.nextCardLimit(limit, unique.size, last) }
        }
    }
    val shown = remember(unique, limit) { if (limit >= unique.size) unique else unique.subList(0, limit) }
    LazyRow(
        state = listState,
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(shown, key = { it.listKey }, contentType = { POSTER_CONTENT_TYPE }) { item ->
            PosterCard(
                item = item,
                onClick = { onItemClick(item) },
                modifier = cardModifier,
                onRemove = onItemRemove?.let { remove -> { remove(item) } },
            )
        }
    }
}

private const val POSTER_CONTENT_TYPE = "poster"

/**
 * Önbellekten gösterilirken üstte küçük şerit: "Çevrimdışı · son güncelleme: 5 dk önce". [notice] null
 * ise hiçbir şey çizilmez (ağ gelince kaybolur).
 */
@Composable
fun OfflineBanner(notice: OfflineNotice?, modifier: Modifier = Modifier) {
    if (notice == null) return
    val text = remember(notice) { notice.label(System.currentTimeMillis()) }
    Text(
        text,
        color = DzColors.Warning,
        style = MaterialTheme.typography.labelMedium,
        textAlign = TextAlign.Center,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .fillMaxWidth()
            .background(DzColors.SurfaceHigh)
            .padding(horizontal = 12.dp, vertical = 4.dp),
    )
}

/**
 * Ağ geri geldiğinde ([ConnectivityMonitor.online] false -> true) ve ekran şu an önbellekten/çevrimdışı
 * gösteriliyorsa ([isStale]) [refresh] çağrılır.
 */
@Composable
fun RefreshWhenBackOnline(container: AppContainer, isStale: () -> Boolean, refresh: () -> Unit) {
    val currentStale by rememberUpdatedState(isStale)
    val currentRefresh by rememberUpdatedState(refresh)
    LaunchedEffect(container) {
        var previous = container.connectivity.online.value
        container.connectivity.online.collect { now ->
            if (now && !previous && currentStale()) currentRefresh()
            previous = now
        }
    }
}

@Composable
fun SectionHeader(title: String, modifier: Modifier = Modifier, actionLabel: String? = null, onAction: (() -> Unit)? = null) {
    Row(
        modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = Color.White,
            modifier = Modifier.weight(1f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (actionLabel != null && onAction != null) {
            Text(
                actionLabel,
                color = DzColors.Muted,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier
                    .clickable(onClick = onAction)
                    .padding(4.dp),
            )
        }
    }
}
