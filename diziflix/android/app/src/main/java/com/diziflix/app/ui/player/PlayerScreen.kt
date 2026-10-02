package com.diziflix.app.ui.player

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.os.Build
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Forward10
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Replay10
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.diziflix.app.data.model.VideoStream
import com.diziflix.app.domain.Format
import com.diziflix.app.play.PlayerFactory
import com.diziflix.app.play.StreamLogic
import com.diziflix.app.ui.common.BrandMascot
import com.diziflix.app.ui.common.ErrorBox
import com.diziflix.app.ui.common.containerViewModel
import com.diziflix.app.ui.theme.DzColors
import kotlinx.coroutines.delay

/**
 * Tam ekran oynatıcı. Önce yükleme ekranı (dönen gösterge + atasözü), ilk akış gerçekten oynayınca
 * oynatıcı; doğrulanamayan (embed) akışta WebView yedeği; hepsi başarısızsa hata + Tekrar dene.
 */
@Composable
fun PlayerScreen(
    itemId: String,
    episodeId: String?,
    kind: String,
    profileId: String,
    onBack: () -> Unit,
) {
    val vm = containerViewModel(key = "player:$itemId:${episodeId ?: ""}:$kind") {
        PlayerViewModel(it, profileId, itemId, episodeId, kind)
    }
    val state by vm.state.collectAsStateWithLifecycle()

    ImmersiveLandscapeEffect()
    OnPauseEffect { vm.onHostPaused() }
    BackHandler {
        vm.leave()
        onBack()
    }
    LaunchedEffect(vm) {
        vm.exit.collect { onBack() }
    }

    val s = state
    val phase = s.phase
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        if (phase is PlayerPhase.Embed) {
            EmbedPane(
                stream = phase.stream,
                onBack = {
                    vm.leave()
                    onBack()
                },
                onReportBroken = { vm.reportEmbedBroken() },
            )
        } else {
            VideoSurface(vm.player)
            if (phase is PlayerPhase.Playing) {
                val leave = {
                    vm.leave()
                    onBack()
                }
                PlayerControls(s = s, vm = vm, onBack = leave)
            }
            if (phase is PlayerPhase.Ended) {
                NextEpisodeCard(s = s, vm = vm)
            }
            if (phase is PlayerPhase.Loading) {
                LoadingPane(
                    s = s,
                    onCancel = {
                        vm.leave()
                        onBack()
                    },
                )
            }
            if (phase is PlayerPhase.Error) {
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(Color.Black),
                ) {
                    ErrorBox(
                        title = "Kaynak çalışmıyor",
                        message = phase.message,
                        actions = listOf(
                            "Tekrar dene" to { vm.retry() },
                            "Geri" to {
                                vm.leave()
                                onBack()
                            },
                        ),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------- yükleme ekranı

@Composable
private fun LoadingPane(s: PlayerUiState, onCancel: () -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(32.dp),
        ) {
            BrandMascot(height = 64.dp)
            Spacer(Modifier.size(16.dp))
            CircularProgressIndicator(color = DzColors.Primary)
            Spacer(Modifier.size(24.dp))
            Crossfade(targetState = s.proverb, label = "proverb") { text ->
                Text(
                    text,
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                    fontStyle = FontStyle.Italic,
                    textAlign = TextAlign.Center,
                )
            }
            Spacer(Modifier.size(16.dp))
            Text(s.stage, color = DzColors.Muted, style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.size(20.dp))
            TextButton(onClick = onCancel) { Text("Vazgeç", color = DzColors.Muted) }
        }
    }
}

// ---------------------------------------------------------------------- video yüzeyi

@Composable
private fun VideoSurface(exo: ExoPlayer) {
    AndroidView(
        factory = { context ->
            PlayerView(context).apply {
                useController = false   // kontroller Compose'ta çizilir
                player = exo
            }
        },
        modifier = Modifier.fillMaxSize(),
    )
}

// ---------------------------------------------------------------------- kontroller

@Composable
private fun PlayerControls(s: PlayerUiState, vm: PlayerViewModel, onBack: () -> Unit) {
    var visible by remember { mutableStateOf(true) }
    var interaction by remember { mutableStateOf(0) }
    var showSources by remember { mutableStateOf(false) }
    var showTracks by remember { mutableStateOf(false) }
    val dialogOpen = showSources || showTracks

    // 3 sn hareketsizlikte (oynarken) kontroller gizlenir.
    LaunchedEffect(visible, s.isPlaying, interaction, dialogOpen) {
        if (visible && s.isPlaying && !dialogOpen) {
            delay(3_000L)
            visible = false
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectTapGestures(
                    onTap = {
                        visible = !visible
                        interaction++
                    },
                    onDoubleTap = { offset ->
                        vm.seekBy(if (offset.x < size.width / 2f) -10_000L else 10_000L)
                        visible = true
                        interaction++
                    },
                )
            },
    ) {
        AnimatedVisibility(visible = visible, enter = fadeIn(), exit = fadeOut()) {
            ControlsLayer(
                s = s,
                vm = vm,
                onBack = onBack,
                onInteract = { interaction++ },
                onOpenSources = { showSources = true },
                onOpenTracks = { showTracks = true },
            )
        }
        if (s.buffering) {
            CircularProgressIndicator(
                color = Color.White,
                modifier = Modifier.align(Alignment.Center),
            )
        }
    }

    if (showSources) {
        AlertDialog(
            onDismissRequest = { showSources = false },
            title = { Text("Kaynak / kalite") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    s.streamLabels.forEachIndexed { index, label ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clickable {
                                    showSources = false
                                    vm.selectStream(index)
                                }
                                .padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = index == s.currentStream, onClick = null)
                            Spacer(Modifier.width(8.dp))
                            Text(label)
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showSources = false }) { Text("Kapat") } },
        )
    }

    if (showTracks) {
        AlertDialog(
            onDismissRequest = { showTracks = false },
            title = { Text("Ses ve altyazı") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    val hardNote = s.hardSubNote
                    if (hardNote != null) {
                        Text(hardNote, color = DzColors.Warning, style = MaterialTheme.typography.bodySmall)
                        Spacer(Modifier.size(12.dp))
                    }
                    if (s.audioTracks.isNotEmpty()) {
                        Text("Ses", color = DzColors.Muted, style = MaterialTheme.typography.labelLarge)
                        s.audioTracks.forEachIndexed { index, option ->
                            TrackRow(label = option.label, selected = option.selected) {
                                vm.selectAudio(index)
                                showTracks = false
                            }
                        }
                        Spacer(Modifier.size(12.dp))
                    }
                    Text("Altyazı", color = DzColors.Muted, style = MaterialTheme.typography.labelLarge)
                    TrackRow(label = "Kapalı", selected = s.textTracks.none { it.selected }) {
                        vm.selectText(-1)
                        showTracks = false
                    }
                    s.textTracks.forEachIndexed { index, option ->
                        TrackRow(label = option.label, selected = option.selected) {
                            vm.selectText(index)
                            showTracks = false
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showTracks = false }) { Text("Kapat") } },
        )
    }
}

@Composable
private fun TrackRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(8.dp))
        Text(label)
    }
}

@Composable
private fun ControlsLayer(
    s: PlayerUiState,
    vm: PlayerViewModel,
    onBack: () -> Unit,
    onInteract: () -> Unit,
    onOpenSources: () -> Unit,
    onOpenTracks: () -> Unit,
) {
    var dragging by remember { mutableStateOf(false) }
    var dragValue by remember { mutableStateOf(0f) }
    val duration = s.durationMs
    val position = if (dragging) dragValue.toLong() else s.positionMs

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.45f)),
    ) {
        // üst çubuk
        Row(
            Modifier
                .align(Alignment.TopStart)
                .fillMaxWidth()
                .padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Geri", tint = Color.White)
            }
            Column(Modifier.weight(1f)) {
                Text(
                    s.title,
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val sub = listOf(s.episodeLabel, s.streamLabels.getOrNull(s.currentStream).orEmpty())
                    .filter { it.isNotBlank() }
                    .joinToString("  ·  ")
                if (sub.isNotEmpty()) {
                    Text(
                        sub,
                        color = Color.White.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.labelMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (s.streamLabels.size > 1) {
                FilledTonalButton(onClick = {
                    onInteract()
                    onOpenSources()
                }) { Text("Kaynak") }
            }
            if (s.audioTracks.size > 1 || s.textTracks.isNotEmpty() || s.hardSubNote != null) {
                Spacer(Modifier.width(6.dp))
                FilledTonalButton(onClick = {
                    onInteract()
                    onOpenTracks()
                }) { Text("Ses / Altyazı") }
            }
        }

        // orta düğmeler
        Row(
            Modifier.align(Alignment.Center),
            horizontalArrangement = Arrangement.spacedBy(28.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = {
                    vm.seekBy(-10_000L)
                    onInteract()
                },
                modifier = Modifier.size(56.dp),
            ) {
                Icon(Icons.Filled.Replay10, contentDescription = "10 sn geri", tint = Color.White, modifier = Modifier.size(38.dp))
            }
            IconButton(
                onClick = {
                    vm.togglePlay()
                    onInteract()
                },
                modifier = Modifier.size(72.dp),
            ) {
                Icon(
                    if (s.isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                    contentDescription = if (s.isPlaying) "Duraklat" else "Oynat",
                    tint = Color.White,
                    modifier = Modifier.size(56.dp),
                )
            }
            IconButton(
                onClick = {
                    vm.seekBy(10_000L)
                    onInteract()
                },
                modifier = Modifier.size(56.dp),
            ) {
                Icon(Icons.Filled.Forward10, contentDescription = "10 sn ileri", tint = Color.White, modifier = Modifier.size(38.dp))
            }
        }

        // alt çubuk: arama çubuğu + süreler
        Column(
            Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
        ) {
            Slider(
                value = position.toFloat().coerceIn(0f, if (duration > 0) duration.toFloat() else 1f),
                onValueChange = {
                    dragging = true
                    dragValue = it
                    onInteract()
                },
                onValueChangeFinished = {
                    vm.seekTo(dragValue.toLong())
                    dragging = false
                },
                valueRange = 0f..(if (duration > 0) duration.toFloat() else 1f),
                enabled = duration > 0,
                colors = SliderDefaults.colors(
                    thumbColor = Color.White,
                    activeTrackColor = DzColors.Primary,
                    inactiveTrackColor = Color.White.copy(alpha = 0.3f),
                    disabledThumbColor = Color.White.copy(alpha = 0.5f),
                    disabledActiveTrackColor = Color.White.copy(alpha = 0.3f),
                    disabledInactiveTrackColor = Color.White.copy(alpha = 0.2f),
                ),
            )
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(Format.clock(position), color = Color.White, style = MaterialTheme.typography.labelMedium)
                Spacer(Modifier.weight(1f))
                Text(
                    if (duration > 0) Format.clock(duration) else "",
                    color = Color.White,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
        }
    }
}

// ---------------------------------------------------------------------- sonraki bölüm

@Composable
private fun NextEpisodeCard(s: PlayerUiState, vm: PlayerViewModel) {
    val next = s.nextEpisode ?: return
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.6f)),
        contentAlignment = Alignment.BottomEnd,
    ) {
        Column(
            Modifier
                .padding(24.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(DzColors.Surface)
                .padding(16.dp),
        ) {
            Text("Sonraki Bölüm", color = DzColors.Muted, style = MaterialTheme.typography.labelLarge)
            Text(
                (if (next.episode > 0) "${next.episode}. " else "") + next.title,
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.size(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { vm.playNext() }) { Text("Şimdi Oynat (${s.countdown})") }
                FilledTonalButton(onClick = { vm.cancelNext() }) { Text("İptal") }
            }
        }
    }
}

// ---------------------------------------------------------------------- embed (WebView) yedeği

/**
 * type=embed akışlar (ör. YouTube fragman) doğrulanamaz: rapor yazılmadan WebView'da açılır.
 * "Sorun bildir" elle failure raporu gönderir (TV'deki kırmızı tuş karşılığı).
 */
@Composable
private fun EmbedPane(stream: VideoStream, onBack: () -> Unit, onReportBroken: () -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        EmbedWebView(stream = stream, modifier = Modifier.fillMaxSize())
        Row(
            Modifier
                .align(Alignment.TopStart)
                .fillMaxWidth()
                .padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = onBack,
                modifier = Modifier.background(Color.Black.copy(alpha = 0.5f), RoundedCornerShape(50)),
            ) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Geri", tint = Color.White)
            }
            Spacer(Modifier.weight(1f))
            FilledTonalButton(onClick = onReportBroken) { Text("Sorun bildir") }
        }
    }
}

/**
 * Embed akışının WebView'ı (tablet ve TV arayüzü ortak kullanır). [focusable]=false: TV'de sayfa Android görünüm odağı
 * almasın, kumanda Compose düğmelerinde kalsın.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun EmbedWebView(stream: VideoStream, modifier: Modifier = Modifier, focusable: Boolean = true) {
    val context = LocalContext.current
    val webView = remember(stream.url) {
        WebView(context).apply {
            setBackgroundColor(0xFF000000.toInt())
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.userAgentString = PlayerFactory.USER_AGENT
            webChromeClient = WebChromeClient()
            webViewClient = WebViewClient()
            if (!focusable) {
                isFocusable = false
                isFocusableInTouchMode = false
            }
            val url = StreamLogic.withAutoplay(stream.url)
            if (StreamLogic.isYouTubeEmbed(stream.url)) {
                // YouTube gömme oynatıcısı geçerli bir Referer/origin ister: sayfayı youtube.com tabanıyla yükle.
                val html = "<html><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
                    "<style>html,body{margin:0;height:100%;background:#000}" +
                    "iframe{position:absolute;left:0;top:0;width:100%;height:100%;border:0}</style></head><body>" +
                    "<iframe src=\"$url\" allow=\"autoplay; encrypted-media; fullscreen\" allowfullscreen></iframe>" +
                    "</body></html>"
                loadDataWithBaseURL("https://www.youtube.com", html, "text/html", "utf-8", null)
            } else {
                loadUrl(url)
            }
        }
    }
    DisposableEffect(webView) {
        onDispose {
            (webView.parent as? ViewGroup)?.removeView(webView)
            webView.stopLoading()
            webView.destroy()
        }
    }
    AndroidView(factory = { webView }, modifier = modifier)
}

// ---------------------------------------------------------------------- pencere/yaşam döngüsü

/** Oynatıcıda: yatay yönelim, tam ekran (sistem çubukları gizli), ekran açık kalır; çıkışta geri alınır. */
@Composable
internal fun ImmersiveLandscapeEffect() {
    val context = LocalContext.current
    DisposableEffect(Unit) {
        val activity = context.findActivity()
        val window = activity?.window
        val controller = window?.let { WindowInsetsControllerCompat(it, it.decorView) }

        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        if (window != null) {
            WindowCompat.setDecorFitsSystemWindows(window, false)
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                val lp = window.attributes
                lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                window.attributes = lp
            }
        }
        controller?.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        controller?.hide(WindowInsetsCompat.Type.systemBars())

        onDispose {
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            if (window != null) {
                WindowCompat.setDecorFitsSystemWindows(window, true)
                window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    val lp = window.attributes
                    lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT
                    window.attributes = lp
                }
            }
            controller?.show(WindowInsetsCompat.Type.systemBars())
        }
    }
}

@Composable
internal fun OnPauseEffect(onPause: () -> Unit) {
    val owner = LocalLifecycleOwner.current
    val current by rememberUpdatedState(onPause)
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_PAUSE) current()
        }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}
