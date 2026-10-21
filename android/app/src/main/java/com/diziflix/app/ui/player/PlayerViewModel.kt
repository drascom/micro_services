package com.diziflix.app.ui.player

import androidx.compose.runtime.Immutable
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.C
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.exoplayer.ExoPlayer
import com.diziflix.app.AppContainer
import com.diziflix.app.data.model.Detail
import com.diziflix.app.data.model.Episode
import com.diziflix.app.data.model.VideoStream
import com.diziflix.app.data.net.UrlUtil
import com.diziflix.app.data.net.apiCall
import com.diziflix.app.data.net.userMessage
import com.diziflix.app.domain.DetailLogic
import com.diziflix.app.domain.ErrorLogic
import com.diziflix.app.domain.PlayPrefs
import com.diziflix.app.domain.SeekLogic
import com.diziflix.app.domain.TvPlayerLogic
import com.diziflix.app.play.ExoStreamProbe
import com.diziflix.app.play.PlayFlow
import com.diziflix.app.play.PlayFlowConfig
import com.diziflix.app.play.PlayOutcome
import com.diziflix.app.play.PlayStage
import com.diziflix.app.play.PlaybackErrors
import com.diziflix.app.play.PlaybackReporter
import com.diziflix.app.play.reportDetail
import com.diziflix.app.play.SourceFinderLogic
import com.diziflix.app.play.PlayerFactory
import com.diziflix.app.play.ProverbPicker
import com.diziflix.app.play.StreamLogic
import com.diziflix.app.play.TrackLabels
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlin.math.abs

@Immutable
data class TrackOption(val label: String, val selected: Boolean)

sealed interface PlayerPhase {
    /** Yükleme ekranı: akışlar sırayla deneniyor. */
    data object Loading : PlayerPhase

    /** İlk akış gerçekten oynamaya başladı. */
    data object Playing : PlayerPhase

    /** Bölüm bitti, sonraki bölüm geri sayımı. */
    data object Ended : PlayerPhase

    /** Doğrulanamayan iframe akışı: WebView yedeği. */
    data class Embed(val stream: VideoStream) : PlayerPhase

    /**
     * Tüm akışlar başarısız / kaynak yok. [finder]: sunucunun kaynak bulucu durumu (`searching` | `not_found`; null = genel hata);
     * [message] buna göre yazılmıştır ([SourceFinderLogic.message]).
     */
    data class Error(val message: String, val finder: String? = null) : PlayerPhase
}

@Immutable
data class PlayerUiState(
    val phase: PlayerPhase = PlayerPhase.Loading,
    val title: String = "",
    val episodeLabel: String = "",
    val stage: String = "",
    val proverb: String = "",
    val streamLabels: List<String> = emptyList(),
    val currentStream: Int = 0,
    val isPlaying: Boolean = false,
    val buffering: Boolean = false,
    val positionMs: Long = 0L,
    val durationMs: Long = 0L,
    val audioTracks: List<TrackOption> = emptyList(),
    val textTracks: List<TrackOption> = emptyList(),
    val nextEpisode: Episode? = null,
    /**
     * Bölümün son ~45 sn'sinde önerilen sonraki bölüm (Tizen "Sonraki bölüm" kartı; yalnızca TV arayüzü okur). Bölüm
     * bitince [nextEpisode] + [countdown] devralır.
     */
    val upcoming: Episode? = null,
    val countdown: Int = 0,
    /** Oynayan akışta altyazı görüntüye gömülüyse açıklama (kapatılamaz); yoksa null. */
    val hardSubNote: String? = null,
    val notice: String? = null,
)

/**
 * Oynatıcı ekranının beyni. TV istemcisiyle aynı mantık: Oynat'a basılınca yükleme ekranı (atasözü),
 * [PlayFlow] akışları sırayla dener ve her akışın başarı/hatasını attempt_token + engine ile bildirir;
 * ilk gerçekten oynayan akışta ekran oynatıcıya geçer (aynı ExoPlayer örneği devralınır).
 * Konum kaydı: 20 sn'de bir + duraklatma/arama/çıkış/bitiş.
 */
class PlayerViewModel(
    private val container: AppContainer,
    private val profileId: String,
    private val itemId: String,
    initialEpisodeId: String?,
    private val kind: String,
) : ViewModel() {

    private val repo = container.repository

    val player: ExoPlayer = PlayerFactory.create(container.app)
    private val probe = ExoStreamProbe(player)
    private val picker = ProverbPicker()

    private val _state = MutableStateFlow(PlayerUiState(proverb = picker.next()))
    val state: StateFlow<PlayerUiState> = _state.asStateFlow()

    private val _exit = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    /** Oynatma bitti ve sonraki bölüm yok: ekran kapanmalı. */
    val exit: SharedFlow<Unit> = _exit.asSharedFlow()

    private var currentEpisodeId: String? = initialEpisodeId
    private var detail: Detail? = repo.cachedDetail(itemId)
    private var flow: PlayFlow? = null
    private var activeIndex = -1
    private var flowJob: Job? = null
    private var finderJob: Job? = null
    private var tickerJob: Job? = null
    private var countdownJob: Job? = null
    private var durationFallbackSec = 0.0
    private var lastSentSec = -999
    private var audioRefs: List<Pair<Tracks.Group, Int>> = emptyList()
    private var textRefs: List<Pair<Tracks.Group, Int>> = emptyList()
    private var cleared = false

    /** Kullanıcı "sonraki bölüm" önerisini iptal etti: bu oynatmada geri gelmez. */
    private var upcomingDismissed = false

    /** Kaynak bulucu `found` deyince otomatik yeniden deneme hakkı (kullanıcı elle yeniden denedikçe 1'e döner; sonsuz döngü olmasın). */
    private var finderAutoRetries = 1

    private val reporter = PlaybackReporter { token, event, code, engine, detail ->
        container.appScope.launch {
            try {
                repo.reportPlayback(token, event, code, engine, detail)
            } catch (e: Exception) {
                // sağlık raporu fire-and-forget: hata oynatmayı etkilemez
            }
        }
    }

    private val playerListener = object : Player.Listener {
        override fun onPlaybackStateChanged(playbackState: Int) {
            _state.update { it.copy(buffering = playbackState == Player.STATE_BUFFERING) }
            if (playbackState == Player.STATE_ENDED) handleEnded()
        }

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            _state.update { it.copy(isPlaying = isPlaying) }
            // Kullanıcı duraklattı: pause anında konumu kaydet.
            if (!isPlaying && !player.playWhenReady && _state.value.phase is PlayerPhase.Playing) {
                sendProgress(force = true)
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            // Akış denenirken hatayı probe yönetir; yalnızca oynarken kopmayı ele al.
            if (_state.value.phase is PlayerPhase.Playing) recover(error)
        }

        override fun onTracksChanged(tracks: Tracks) {
            publishTracks(tracks)
        }
    }

    init {
        player.addListener(playerListener)
        // Yükleme ekranındaki atasözü ~5 sn'de bir değişir.
        viewModelScope.launch {
            while (true) {
                delay(PROVERB_MS)
                _state.update { if (it.phase is PlayerPhase.Loading) it.copy(proverb = picker.next()) else it }
            }
        }
        // Başlık ve sonraki bölüm için detay (önbellekte yoksa arka planda).
        if (detail == null) {
            viewModelScope.launch {
                apiCall { repo.detail(itemId, profileId) }.onSuccess { d ->
                    detail = d
                    refreshHeader()
                }
            }
        }
        startPlayback(currentEpisodeId)
    }

    // ------------------------------------------------------------------ akış başlatma

    /** [auto]: kaynak bulucu `found` dediği için otomatik yeniden deneme (elle yeniden deneme hakkını sıfırlamaz). */
    private fun startPlayback(episodeId: String?, auto: Boolean = false) {
        currentEpisodeId = episodeId
        upcomingDismissed = false
        if (!auto) finderAutoRetries = 1
        flowJob?.cancel()
        finderJob?.cancel()
        countdownJob?.cancel()
        tickerJob?.cancel()
        // Çevrimdışı: akış isteğine hiç girmeden net mesaj (Tekrar dene ağ gelince çalışır).
        if (!container.isOnline()) {
            showError(ErrorLogic.OFFLINE_PLAY_MESSAGE)
            return
        }
        setLoading(if (auto) PlayStage.SourceFound else PlayStage.Searching)
        flowJob = viewModelScope.launch {
            val response = try {
                repo.streams(itemId, profileId, if (kind == "trailer") null else episodeId, kind)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                showError(e.userMessage().ifBlank { "Kaynak bilgisi alınamadı." })
                return@launch
            }
            durationFallbackSec = response.duration
            // Akış yok + sunucu kaynak bulucuyu başlattı: genel hata yerine "aranıyor / bulunamadı" paneli.
            val finderState = response.finder?.state
            if (kind != "trailer" && response.streams.isEmpty() && SourceFinderLogic.message(finderState) != null) {
                showFinder(checkNotNull(finderState))
                return@launch
            }
            // Altyazı adresleri sunucu yoludur (/api/subtitles/<id>.vtt): taban adres eklenir.
            val settings = container.settings.awaitLoaded()
            val base = settings.baseUrl
            probe.subtitles = response.subtitles.map { it.copy(url = UrlUtil.absolute(base, it.url)) }
            probe.subtitlePref = settings.subPref
            val qualityCap = PlayPrefs.qualityCap(PlayPrefs.effectiveQuality(settings.qualityPref, container.isTv))
            val f = PlayFlow(
                probe = probe,
                reporter = reporter,
                isOnline = container::isOnline,
                config = PlayFlowConfig(),
                onStage = ::onStage,
            )
            flow = f
            activeIndex = -1
            val resumeMs = (response.resumePosition * 1000.0).toLong().coerceAtLeast(0L)
            handle(f, f.start(response.streams, resumeMs, qualityCap))
        }
    }

    private fun setLoading(stage: PlayStage) {
        refreshHeader()
        _state.update {
            it.copy(
                phase = PlayerPhase.Loading,
                stage = stageText(stage),
                proverb = picker.next(),
                isPlaying = false,
                buffering = false,
                nextEpisode = null,
                upcoming = null,
            )
        }
    }

    private fun onStage(stage: PlayStage) {
        _state.update { it.copy(stage = stageText(stage)) }
    }

    private fun stageText(stage: PlayStage): String = when (stage) {
        PlayStage.Searching -> "Kaynak aranıyor…"
        PlayStage.Preparing -> "Video hazırlanıyor…"
        PlayStage.TryingOther -> "Başka kaynak deneniyor…"
        PlayStage.SourceFound -> SourceFinderLogic.MSG_FOUND + "…"
    }

    private fun showError(message: String, finder: String? = null) {
        player.stop()
        player.clearMediaItems()
        _state.update { it.copy(phase = PlayerPhase.Error(message, finder), isPlaying = false, buffering = false) }
    }

    /**
     * Kaynak bulucu paneli (`searching` | `not_found`). `searching` iken panelde kalınırsa durum ucu 5 sn'de bir yoklanır:
     * `found` -> akış otomatik yeniden istenir ("Kaynak bulundu, yeniden deneniyor…"); `not_found` -> panel metni güncellenir.
     * Yoklama hatası/zaman aşımı sessizce sürer ya da durur; bildirim uygulamada yine gelir.
     */
    private fun showFinder(state: String) {
        showError(checkNotNull(SourceFinderLogic.message(state)), state)
        if (state != SourceFinderLogic.STATE_SEARCHING || finderAutoRetries <= 0) return
        val episode = if (kind == "trailer") null else currentEpisodeId
        finderJob?.cancel()
        finderJob = viewModelScope.launch {
            when (SourceFinderLogic.awaitResult(poll = { repo.sourceFinder(itemId, episode).state })) {
                SourceFinderLogic.STATE_FOUND -> {
                    finderAutoRetries--
                    startPlayback(currentEpisodeId, auto = true)
                }
                SourceFinderLogic.STATE_NOT_FOUND -> {
                    if (_state.value.phase is PlayerPhase.Error) showError(SourceFinderLogic.MSG_NOT_FOUND, SourceFinderLogic.STATE_NOT_FOUND)
                }
                else -> Unit
            }
        }
    }

    private fun handle(f: PlayFlow, outcome: PlayOutcome) {
        val labels = StreamLogic.uniqueLabels(f.streams)
        when (outcome) {
            is PlayOutcome.Playing -> {
                activeIndex = outcome.index
                _state.update {
                    it.copy(
                        phase = PlayerPhase.Playing,
                        streamLabels = labels,
                        currentStream = outcome.index,
                        isPlaying = player.isPlaying,
                        hardSubNote = StreamLogic.hardSubNote(outcome.stream),
                    )
                }
                publishTracks(player.currentTracks)
                startTicker()
            }
            is PlayOutcome.Embed -> {
                activeIndex = outcome.index
                tickerJob?.cancel()
                player.stop()
                player.clearMediaItems()
                _state.update {
                    it.copy(
                        phase = PlayerPhase.Embed(outcome.stream),
                        streamLabels = labels,
                        currentStream = outcome.index,
                        isPlaying = false,
                        buffering = false,
                    )
                }
            }
            is PlayOutcome.Failed -> {
                tickerJob?.cancel()
                player.stop()
                player.clearMediaItems()
                _state.update {
                    it.copy(
                        phase = PlayerPhase.Error(outcome.message),
                        streamLabels = labels,
                        isPlaying = false,
                        buffering = false,
                    )
                }
            }
        }
    }

    /** Oynarken akış koptu: hata raporlanır, konumdan denenmemiş sonraki doğrudan akışa geçilir. */
    private fun recover(error: PlaybackException) {
        val f = flow ?: return
        val code = PlaybackErrors.toReportCode(error.errorCode)
        val resumeMs = player.currentPosition
        val failedIndex = activeIndex
        flowJob?.cancel()
        tickerJob?.cancel()
        setLoading(PlayStage.TryingOther)
        flowJob = viewModelScope.launch {
            handle(f, f.recover(failedIndex, code, PlaybackErrors.userMessage(code), resumeMs, error.reportDetail()))
        }
    }

    // ------------------------------------------------------------------ kullanıcı eylemleri

    fun retry() {
        startPlayback(currentEpisodeId)
    }

    /** Kaynak/kalite menüsü: seçilen akış konumdan denenir, olmazsa denenmemişlere geçilir. */
    fun selectStream(index: Int) {
        val f = flow ?: return
        if (index == activeIndex && _state.value.phase is PlayerPhase.Playing) return
        val resumeMs = if (_state.value.phase is PlayerPhase.Playing) player.currentPosition else 0L
        flowJob?.cancel()
        tickerJob?.cancel()
        setLoading(PlayStage.Preparing)
        flowJob = viewModelScope.launch { handle(f, f.select(index, resumeMs)) }
    }

    fun togglePlay() {
        if (player.isPlaying) {
            player.pause()
        } else {
            if (player.playbackState == Player.STATE_ENDED) player.seekTo(0L)
            player.play()
        }
    }

    fun seekTo(positionMs: Long) {
        val duration = currentDurationMs()
        val target = if (duration > 0) positionMs.coerceIn(0L, duration) else positionMs.coerceAtLeast(0L)
        player.seekTo(target)
        _state.update { it.copy(positionMs = target) }
        sendProgress(force = true)
    }

    fun seekBy(deltaMs: Long) {
        seekTo(player.currentPosition + deltaMs)
    }

    /**
     * TV kumandası: Sol/Sağ basılı tutulunca art arda sarma. Her adımda sunucuya ilerleme YAZILMAZ
     * (istek fırtınası olmasın); sarma bitince arayüz [commitProgress] çağırır.
     */
    fun seekQuiet(deltaMs: Long) {
        val target = SeekLogic.target(player.currentPosition, deltaMs, currentDurationMs())
        player.seekTo(target)
        _state.update { it.copy(positionMs = target) }
    }

    /** Sarma bitti: son konumu kaydeder (yalnızca oynatma aşamasında). */
    fun commitProgress() {
        if (_state.value.phase is PlayerPhase.Playing) sendProgress(force = true)
    }

    fun selectAudio(index: Int) {
        val ref = audioRefs.getOrNull(index) ?: return
        player.trackSelectionParameters = player.trackSelectionParameters
            .buildUpon()
            .setOverrideForType(TrackSelectionOverride(ref.first.mediaTrackGroup, ref.second))
            .build()
    }

    /** [index] = -1: altyazı kapalı. */
    fun selectText(index: Int) {
        val builder = player.trackSelectionParameters.buildUpon()
        if (index < 0) {
            builder.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
        } else {
            val ref = textRefs.getOrNull(index) ?: return
            builder.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
            builder.setOverrideForType(TrackSelectionOverride(ref.first.mediaTrackGroup, ref.second))
        }
        player.trackSelectionParameters = builder.build()
    }

    /** iframe akışında başarı/hata gözlenemez: kullanıcı elle bildirir. */
    fun reportEmbedBroken() {
        flow?.reportFailure(activeIndex, "playback_failed", "embed", "user-report")
        _state.update { it.copy(notice = "Kaynak sorunu bildirildi") }
    }

    fun consumeNotice() {
        _state.update { it.copy(notice = null) }
    }

    /** Uygulama arka plana gidince duraklat ve konumu kaydet. */
    fun onHostPaused() {
        if (_state.value.phase is PlayerPhase.Playing) {
            sendProgress(force = true)
            player.pause()
        }
    }

    /** Geri/çıkış: konumu kaydet, denemeyi durdur (rapor yazılmaz). */
    fun leave() {
        flowJob?.cancel()
        finderJob?.cancel()
        countdownJob?.cancel()
        if (_state.value.phase is PlayerPhase.Playing) sendProgress(force = true)
        player.pause()
    }

    // ------------------------------------------------------------------ bitiş / sonraki bölüm

    private fun handleEnded() {
        if (_state.value.phase !is PlayerPhase.Playing) return
        tickerJob?.cancel()
        sendProgress(force = true, atEnd = true)
        val d = detail
        val next = if (kind == "trailer" || d == null) null else DetailLogic.nextEpisode(d.seasons, effectiveEpisodeId())
        if (next == null) {
            _exit.tryEmit(Unit)
            return
        }
        _state.update { it.copy(phase = PlayerPhase.Ended, nextEpisode = next, upcoming = null, countdown = NEXT_COUNTDOWN_S, isPlaying = false) }
        countdownJob?.cancel()
        countdownJob = viewModelScope.launch {
            var left = NEXT_COUNTDOWN_S
            while (left > 0) {
                delay(1_000L)
                left--
                _state.update { it.copy(countdown = left) }
            }
            playNext()
        }
    }

    fun playNext() {
        val next = _state.value.nextEpisode ?: _state.value.upcoming ?: return
        countdownJob?.cancel()
        startPlayback(next.id)
    }

    /** "Sonraki bölüm" önerisini kapat (oynatma sürer, otomatik geçiş olmaz). */
    fun dismissUpcoming() {
        upcomingDismissed = true
        _state.update { it.copy(upcoming = null) }
    }

    fun cancelNext() {
        countdownJob?.cancel()
        _exit.tryEmit(Unit)
    }

    // ------------------------------------------------------------------ durum yayını

    private fun startTicker() {
        tickerJob?.cancel()
        tickerJob = viewModelScope.launch {
            var sinceSendMs = 0L
            while (true) {
                delay(TICK_MS)
                val positionMs = player.currentPosition.coerceAtLeast(0L)
                val durationMs = currentDurationMs()
                val upcomingShown = when (TvPlayerLogic.upcomingDecision(durationMs, positionMs)) {
                    true -> if (upcomingDismissed || kind == "trailer") null else detail?.let { d ->
                        DetailLogic.nextEpisode(d.seasons, effectiveEpisodeId())
                    }
                    false -> null
                    null -> _state.value.upcoming
                }
                _state.update { it.copy(positionMs = positionMs, durationMs = durationMs, upcoming = upcomingShown) }
                if (player.isPlaying) {
                    sinceSendMs += TICK_MS
                    if (sinceSendMs >= PROGRESS_MS) {
                        sinceSendMs = 0L
                        sendProgress(force = false)
                    }
                }
            }
        }
    }

    private fun currentDurationMs(): Long {
        val d = player.duration
        return if (d != C.TIME_UNSET && d > 0L) d else (durationFallbackSec * 1000.0).toLong()
    }

    private fun refreshHeader() {
        val d = detail
        val title = d?.item?.title.orEmpty()
        val label = if (d != null && kind != "trailer") DetailLogic.episodeLabel(d.seasons, currentEpisodeId).orEmpty() else ""
        _state.update { it.copy(title = title, episodeLabel = label) }
    }

    private fun effectiveEpisodeId(): String {
        val explicit = currentEpisodeId
        if (!explicit.isNullOrBlank()) return explicit
        val d = detail
        if (d != null && d.item.isSeries) return DetailLogic.resumeTarget(d)
        return itemId
    }

    /** Konum/süre saniye; aynı konuma <5 sn fark varsa (force değilse) tekrar gönderilmez; fragmanda hiç. */
    private fun sendProgress(force: Boolean, atEnd: Boolean = false) {
        if (kind == "trailer" || cleared) return
        val stream = flow?.streams?.getOrNull(activeIndex)
        if (stream?.kind == "trailer") return
        val durationSec = (currentDurationMs() / 1000L).toInt()
        if (durationSec <= 0) return
        val positionSec = if (atEnd) durationSec else (player.currentPosition / 1000L).toInt().coerceIn(0, durationSec)
        if (!force && abs(positionSec - lastSentSec) < 5) return
        lastSentSec = positionSec
        val episode = effectiveEpisodeId()
        container.appScope.launch {
            try {
                repo.saveProgress(profileId, itemId, episode, positionSec, durationSec)
            } catch (e: Exception) {
                // ilerleme kaydı en iyi çaba
            }
        }
    }

    private fun publishTracks(tracks: Tracks) {
        val aRefs = ArrayList<Pair<Tracks.Group, Int>>()
        val tRefs = ArrayList<Pair<Tracks.Group, Int>>()
        val aLabels = ArrayList<String>()
        val tLabels = ArrayList<String>()
        val aSelected = ArrayList<Boolean>()
        val tSelected = ArrayList<Boolean>()
        for (group in tracks.groups) {
            val type = group.type
            if (type != C.TRACK_TYPE_AUDIO && type != C.TRACK_TYPE_TEXT) continue
            for (i in 0 until group.length) {
                if (!group.isTrackSupported(i)) continue
                val format = group.getTrackFormat(i)
                if (type == C.TRACK_TYPE_AUDIO) {
                    aLabels.add(TrackLabels.label(format.language, format.label, aRefs.size, "Ses"))
                    aRefs.add(Pair(group, i))
                    aSelected.add(group.isTrackSelected(i))
                } else {
                    tLabels.add(TrackLabels.label(format.language, format.label, tRefs.size, "Altyazı"))
                    tRefs.add(Pair(group, i))
                    tSelected.add(group.isTrackSelected(i))
                }
            }
        }
        audioRefs = aRefs
        textRefs = tRefs
        val audio = TrackLabels.uniquify(aLabels).mapIndexed { i, label -> TrackOption(label, aSelected[i]) }
        val text = TrackLabels.uniquify(tLabels).mapIndexed { i, label -> TrackOption(label, tSelected[i]) }
        _state.update { it.copy(audioTracks = audio, textTracks = text) }
    }

    override fun onCleared() {
        if (_state.value.phase is PlayerPhase.Playing) sendProgress(force = true)
        cleared = true
        player.removeListener(playerListener)
        player.release()
    }

    private companion object {
        const val PROVERB_MS = 5_000L
        const val TICK_MS = 500L
        const val PROGRESS_MS = 20_000L
        const val NEXT_COUNTDOWN_S = 5
    }
}
