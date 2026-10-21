package com.diziflix.app.domain

/**
 * Android TV modu için saf mantık (birim testli; Compose/Android sınıfı yok). Arayüz bu kararları uygular:
 * TV algılama, uzun basış süresi, uzaktan kumanda sarma adımı, oynatıcı tuş eşlemesi, odak belleği/ilk odak.
 * Tablet yolu bunlardan etkilenmez: hepsi yalnızca `LocalIsTv == true` iken kullanılır.
 */
object TvDetector {
    /** `Configuration.UI_MODE_TYPE_TELEVISION` (saf birim testinde android.jar gerekmesin diye sabit). */
    const val UI_MODE_TYPE_TELEVISION = 4

    /**
     * TV modu: sistem arayüz kipi televizyon, YA DA leanback özelliği var, YA DA dokunmatik ekran yok
     * (kumandayla kullanılan kutu). Dokunmatik ekranlı telefon/tablet her zaman false kalır.
     */
    fun isTv(uiModeType: Int, hasLeanback: Boolean, hasTouchscreen: Boolean): Boolean =
        uiModeType == UI_MODE_TYPE_TELEVISION || hasLeanback || !hasTouchscreen
}

/** TV yerleşim ölçüleri (dp). */
object TvLayout {
    /** Güvenli alan (overscan) kenar boşlukları: yatay / dikey. */
    const val OVERSCAN_H_DP = 48f
    const val OVERSCAN_V_DP = 27f

    /** 10 ayak okunabilirliği için yazı ölçeği çarpanı (yalnızca sp; dp ölçüleri aynı kalır). */
    const val FONT_SCALE_BOOST = 1.12f

    /** Odaktaki öğenin büyüme oranı ve çerçeve kalınlığı (dp). */
    const val FOCUS_SCALE = 1.06f
    const val FOCUS_BORDER_DP = 3f

    fun fontScale(baseFontScale: Float, isTv: Boolean): Float = if (isTv) baseFontScale * FONT_SCALE_BOOST else baseFontScale
}

/**
 * Tamam (DPAD_CENTER/Enter) tuşunun kısa/uzun basış ayrımı. Sistem tuş tekrarları (keydown, repeatCount>0) süre
 * eşiğini aşınca basılı TUTARKEN [Result.LongPress] döner; tekrar gelmeyen kumandalarda bırakınca süre ölçülür.
 * Zaman damgaları `KeyEvent.eventTime` (ms) olmalıdır.
 */
class LongPressTracker(private val thresholdMs: Long = LONG_PRESS_MS) {
    enum class Result { None, Click, LongPress }

    private var downAt = -1L
    private var fired = false

    /** Tuş aşağı (ilk basış ve sistem tekrarları). */
    fun onDown(timeMs: Long): Result {
        if (downAt < 0L) {
            downAt = timeMs
            fired = false
            return Result.None
        }
        if (!fired && timeMs - downAt >= thresholdMs) {
            fired = true
            return Result.LongPress
        }
        return Result.None
    }

    /** Tuş bırakıldı: kısa basış = [Result.Click]; uzun basış bırakırken tetiklenir (henüz tetiklenmediyse). */
    fun onUp(timeMs: Long): Result {
        if (downAt < 0L) return Result.None
        val start = downAt
        val alreadyFired = fired
        downAt = -1L
        fired = false
        if (alreadyFired) return Result.None
        return if (timeMs - start >= thresholdMs) Result.LongPress else Result.Click
    }

    fun cancel() {
        downAt = -1L
        fired = false
    }

    companion object {
        const val LONG_PRESS_MS = 600L
    }
}

/** Sarma (seek) hesapları. */
object SeekLogic {
    const val STEP_MS = 10_000L

    /** Basılı tutma süresine göre adım: 10 sn -> 30 sn -> 60 sn -> 120 sn. */
    fun holdStepMs(holdMs: Long): Long = when {
        holdMs < 1_500L -> STEP_MS
        holdMs < 3_500L -> 30_000L
        holdMs < 6_000L -> 60_000L
        else -> 120_000L
    }

    /** Hedef konum: [0, duration] aralığına sıkıştırılır (süre bilinmiyorsa yalnızca alt sınır). */
    fun target(currentMs: Long, deltaMs: Long, durationMs: Long): Long {
        val raw = currentMs + deltaMs
        return if (durationMs > 0L) raw.coerceIn(0L, durationMs) else raw.coerceAtLeast(0L)
    }

    /** Kısa gösterge: "+10 sn", "-1 dk" (etiket, oynatıcıdaki geçici ipucu). */
    fun label(deltaMs: Long): String {
        val sign = if (deltaMs >= 0) "+" else "-"
        val secs = kotlin.math.abs(deltaMs) / 1000L
        return if (secs >= 60L && secs % 60L == 0L) "$sign${secs / 60L} dk" else "$sign$secs sn"
    }
}

/**
 * Sol/Sağ tuşunu basılı tutunca hızlanan sarma. İlk basış [SeekLogic.STEP_MS]; sistem tekrarlarında en az
 * [REPEAT_GAP_MS] arayla, basılı kalma süresine göre büyüyen adım. Dönen değer işaretli ms (0 = şimdi sarma yok).
 */
class SeekAccelerator {
    private var start = 0L
    private var last = Long.MIN_VALUE

    fun onKey(timeMs: Long, isRepeat: Boolean, direction: Int): Long {
        val sign = if (direction >= 0) 1L else -1L
        // Uzun süre sessizlik sonrası gelen tekrar yeni bir oturumdur.
        if (!isRepeat || last == Long.MIN_VALUE || timeMs - last > SESSION_GAP_MS) {
            start = timeMs
            last = timeMs
            return sign * SeekLogic.STEP_MS
        }
        if (timeMs - last < REPEAT_GAP_MS) return 0L
        last = timeMs
        return sign * SeekLogic.holdStepMs(timeMs - start)
    }

    fun reset() {
        last = Long.MIN_VALUE
    }

    companion object {
        const val REPEAT_GAP_MS = 250L
        const val SESSION_GAP_MS = 1_000L
    }
}

/** Oynatıcıda kumanda tuşlarının eylem eşlemesi (Android `KeyEvent.KEYCODE_*` sabitleriyle). */
enum class PlayerKeyAction { TogglePlay, Play, Pause, SeekBack, SeekForward, FocusTopBar, ShowControls, Ignore }

object PlayerKeys {
    const val KEYCODE_DPAD_UP = 19
    const val KEYCODE_DPAD_DOWN = 20
    const val KEYCODE_DPAD_LEFT = 21
    const val KEYCODE_DPAD_RIGHT = 22
    const val KEYCODE_DPAD_CENTER = 23
    const val KEYCODE_BACK = 4
    const val KEYCODE_ENTER = 66
    const val KEYCODE_NUMPAD_ENTER = 160
    const val KEYCODE_MEDIA_PLAY_PAUSE = 85
    const val KEYCODE_MEDIA_PLAY = 126
    const val KEYCODE_MEDIA_PAUSE = 127
    const val KEYCODE_MEDIA_REWIND = 89
    const val KEYCODE_MEDIA_FAST_FORWARD = 90

    /** Tamam benzeri tuşlar (DPAD_CENTER/Enter/NumPad Enter). */
    fun isSelect(keyCode: Int): Boolean =
        keyCode == KEYCODE_DPAD_CENTER || keyCode == KEYCODE_ENTER || keyCode == KEYCODE_NUMPAD_ENTER

    /**
     * Kontrol katmanına odak YOKKEN (kök yüzeyde) tuşun anlamı. Yukarı: üst çubuğa (Geri/Kaynak/Ses) odaklan;
     * Aşağı: yalnızca kontrolleri göster; Geri tuşu burada değil BackHandler'dadır (kontroller açıksa önce kapatır).
     */
    fun actionFor(keyCode: Int): PlayerKeyAction = when (keyCode) {
        KEYCODE_DPAD_CENTER, KEYCODE_ENTER, KEYCODE_NUMPAD_ENTER, KEYCODE_MEDIA_PLAY_PAUSE -> PlayerKeyAction.TogglePlay
        KEYCODE_MEDIA_PLAY -> PlayerKeyAction.Play
        KEYCODE_MEDIA_PAUSE -> PlayerKeyAction.Pause
        KEYCODE_DPAD_LEFT, KEYCODE_MEDIA_REWIND -> PlayerKeyAction.SeekBack
        KEYCODE_DPAD_RIGHT, KEYCODE_MEDIA_FAST_FORWARD -> PlayerKeyAction.SeekForward
        KEYCODE_DPAD_UP -> PlayerKeyAction.FocusTopBar
        KEYCODE_DPAD_DOWN -> PlayerKeyAction.ShowControls
        KEYCODE_BACK -> PlayerKeyAction.Ignore
        else -> PlayerKeyAction.ShowControls
    }

    /** Kontroller bu eylemden sonra görünür olmalı mı (hepsi; Ignore hariç). */
    fun revealsControls(action: PlayerKeyAction): Boolean = action != PlayerKeyAction.Ignore
}

/**
 * Odak belleği: ekran yığından dönünce (ör. oynatıcıdan detaya, detaydan ana sayfaya) en son odaklanan öğeye
 * dönmek için. Saf durum; arayüz `rememberSaveable` ile sarar. Anahtarlar ekran içinde benzersizdir
 * (`"row:continue:<kart>"`, `"ep:<id>"`, `"play"` ...).
 */
class FocusMemoryState(var lastKey: String? = null) {
    /** Ekran yeniden kuruldu ve kayıtlı bir anahtar var: bir kez geri yükleme beklenir. */
    var restorePending: Boolean = lastKey != null
        private set

    /** İlk odak (geri yükleme ya da varsayılan) verildi mi. */
    var entered: Boolean = false
        private set

    fun onFocused(key: String) {
        // Geri yükleme beklenirken BAŞKA bir öğe odak aldıysa (kullanıcı kendisi hareket etti) bekleme geçersizdir.
        if (restorePending && key != lastKey) {
            restorePending = false
            entered = true
        }
        lastKey = key
    }

    /** Bu öğe şu anda geri yüklenmeli mi (bekleniyor ve anahtar eşleşiyor)? */
    fun shouldRestore(key: String): Boolean = restorePending && lastKey == key

    fun markRestored() {
        restorePending = false
        entered = true
    }

    /** Geri yükleme hedefi bulunamadı (süre doldu): varsayılan ilk odağa geç. */
    fun giveUpRestore() {
        restorePending = false
    }

    /** Varsayılan ilk odak verilmeli mi: henüz girilmedi ve bekleyen geri yükleme yok. */
    fun needsDefaultFocus(): Boolean = !entered && !restorePending

    fun markEntered() {
        entered = true
    }

    private var phase: Any? = null
    private var phaseSet = false

    /**
     * Ekranın aşaması değişti (yükleme -> içerik, hata -> içerik ...): eski odak öğeleri yok olduğundan ilk odak
     * yeniden verilir. İlk çağrı yalnızca aşamayı kaydeder (bekleyen geri yüklemeyi bozmaz).
     */
    fun enterPhase(newPhase: Any?) {
        if (phaseSet && newPhase != phase) {
            entered = false
            restorePending = false
        }
        phase = newPhase
        phaseSet = true
    }

    companion object {
        /** Geri yükleme hedefi bu sürede oluşmazsa varsayılan ilk odağa dönülür. */
        const val RESTORE_TIMEOUT_MS = 1_500L
    }
}

/** Ekran başına "ilk odak" kararları (hangi öğe). */
object TvFocusLogic {
    enum class DetailTarget { Play, Trailer, MyList }

    /** Detayda açılış odağı: Oynat/Devam Et; yoksa Fragman; o da yoksa Listem (her zaman var). */
    fun detailInitial(canPlay: Boolean, hasTrailer: Boolean): DetailTarget = when {
        canPlay -> DetailTarget.Play
        hasTrailer -> DetailTarget.Trailer
        else -> DetailTarget.MyList
    }

    enum class HomeTarget { Hero, FirstRow, None }

    /** Ana sayfa açılış odağı: hero varsa hero'nun ilk düğmesi; yoksa ilk yüklenmiş satır; yoksa yok. */
    fun homeInitial(hasHero: Boolean, firstLoadedRow: Int): HomeTarget = when {
        hasHero -> HomeTarget.Hero
        firstLoadedRow >= 0 -> HomeTarget.FirstRow
        else -> HomeTarget.None
    }

    /** İlk yüklenmiş ve boş olmayan satırın dizini; yoksa -1. */
    fun firstLoadedRow(loaded: List<Boolean>, counts: List<Int>): Int {
        for (i in loaded.indices) if (loaded[i] && counts.getOrElse(i) { 0 } > 0) return i
        return -1
    }

    /** Hero sayfası değişimi: [direction] -1/+1; sınır dışıysa null. */
    fun heroPage(current: Int, count: Int, direction: Int): Int? {
        val target = current + direction
        return if (target in 0 until count) target else null
    }

    /** Sezon değişince bölüm listesinde odak/kaydırma hedefi: liste boşsa -1, değilse 0. */
    fun episodeEntryIndex(episodeCount: Int): Int = if (episodeCount > 0) 0 else -1

    /** Bölüm anahtarından ("ep:<id>") bölüm dizinini bulur; yoksa -1. */
    fun episodeIndexForKey(key: String?, episodeIds: List<String>): Int {
        if (key == null || !key.startsWith("ep:")) return -1
        val id = key.removePrefix("ep:")
        return episodeIds.indexOf(id)
    }

    /**
     * Seçenek listesinde (filtre/kalite menüsü) açılışta odaklanacak dizin: seçili olan, yoksa 0.
     */
    fun choiceEntryIndex(optionIds: List<String>, selectedId: String?): Int {
        val i = optionIds.indexOf(selectedId)
        return if (i >= 0) i else 0
    }
}

/** Üst menü öğeleri (odak belleği ve odak isteği hedefleri). */
enum class TvBarItem { Wordmark, Search, MyList, Profile, Settings }

/**
 * Üst menü odak belleği: üst menüden bir öğeyle (Ayarlar, Listem, arama) bir ekrana gidip Geri ile dönünce odak o öğeye
 * geri gelir (eskiden dönüşte ilk satır/hero'ya düşüyordu). Saf durum; rota adlarıyla çalışır.
 *
 * Akış: öğeye basılınca [arm] (gidilen rota [to], dönülecek rota [from]); rota değiştikçe [onRoute]; dönülen ekran açılırken
 * [take] (yalnızca [to]'ya gidilmiş ve [from]'a dönülmüşse öğeyi verir ve belleği boşaltır). Başka yere sapılırsa
 * ([to]'dan sonra [from] dışı bir rota) bellek eskimiş sayılıp silinir: bayat bir odak geri yüklenmez.
 */
class TvBarReturn {
    var item: TvBarItem? = null
        private set
    private var from: String? = null
    private var to: String? = null
    private var away = false

    fun arm(item: TvBarItem, from: String, to: String) {
        this.item = item
        this.from = from
        this.to = to
        this.away = false
    }

    fun clear() {
        item = null
        from = null
        to = null
        away = false
    }

    /** Geçerli rota değişti. */
    fun onRoute(route: String) {
        if (item == null) return
        when {
            route == to -> away = true
            route == from -> Unit
            away -> clear()
        }
    }

    /** [route] ekranı açılıyor: geri yüklenecek üst menü öğesi (yoksa null). Verirse belleği boşaltır. */
    fun take(route: String): TvBarItem? {
        val current = item ?: return null
        if (!away || route != from) return null
        clear()
        return current
    }
}

