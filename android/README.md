# android — diziflix WebView kabuğu

Android (TV kutusu, telefon, tablet) uygulaması = web istemcisini (`../client/`) yükleyen ince WebView kabuğu. Eski native
(Compose) istemci kaldırıldı (git geçmişinde). Özellikler web istemcisinden gelir; burada yalnız kabuk kodu var:
`app/src/main/java/com/diziflix/app/MainActivity.kt`.

- Paket `com.diziflix.app`, minSdk 21 (WebView güncellenebilir, MSE/hls.js), targetSdk 34, Kotlin, tek bağımlılık `androidx.webkit`.
- Web varlıkları derlemede `../client/` → `app/src/main/assets/client/` kopyalanır (`syncClientAssets`; tests/, README,
  build-wgt.sh, config.xml, gizli dosyalar hariç). Bu dizin git dışıdır.
- Yükleme: `WebViewAssetLoader`, köken `http://appassets.androidplatform.net/assets/client/index.html` (http: LAN sunucusuna XHR
  karışık içerik değil; sunucu CORS `*` verir). localStorage bu kökende kalıcı (sunucu adresi, profil).
- Tam ekran immersive, yatay, ekran açık; cleartext HTTP izinli; iframe/YouTube tam ekranı `onShowCustomView`.
  Android TV leanback launcher + banner, normal launcher da var. WebView uzaktan hata ayıklama (chrome://inspect) yalnız debug
  derlemede; JS konsolu logcat'te `DZWeb` etiketiyle.

## Derleme

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home   # JDK 17 (bootstrap-android-toolchain.sh kurar)
cd android && ./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ../dist/diziflix.apk
```

Sürüm `../client/version.json`'dan gelir (`versionName`, `versionCode` = X*10000+Y*100+Z); assets'teki `js/version.js`'i
`syncClientAssets` yazar.

### Release imzası (keystore — YEDEKLE)

`assembleRelease` kalıcı release anahtarıyla imzalanır: `~/.diziflix/release.keystore` + şifreler `~/.diziflix/keystore.properties`
(`storeFile`, `storePassword`, `keyAlias`, `keyPassword`; git DIŞI). Dosya yoksa release APK imzasız çıkar (uyarı), debug etkilenmez.
**Bu iki dosyayı güvenli bir yere yedekle**: kaybolursa yeni APK eski kurulumun üstüne kurulamaz, otomatik güncelleme zinciri kırılır
(herkes kaldır + kur yapmak zorunda kalır). Debug ve release artık farklı anahtarla imzalı: debug ↔ release geçişi ve eski
debug-imzalı kurulumdan ilk release'e geçiş bir kez kaldır + kur ister (`adb uninstall com.diziflix.app`; yerel veri — sunucu adresi,
profil — silinir).

Tam release (wgt + APK + GitHub release): proje kökünden `tools/release.sh [--dry-run] <X.Y.Z>`.

## Otomatik güncelleme (`Updater.kt`, `ApkProvider.kt`)

- Açılışta (5 sn sonra) + 6 saatte bir (en çok 6 saatte bir; API sınırı 60/saat/IP): GitHub
  `repos/drascom/micro_services/releases?per_page=20` -> taslak/ön sürüm olmayan en yüksek `diziflix-v*`; mevcut `versionName`'den
  büyükse `diziflix-<ver>.apk` uygulamanın `cache/updates/`'ine indirilir, release'in `SHA256SUMS` asset'iyle doğrulanır. Ağ hatası sessiz.
- Hazır olunca web'e `DZUpdate.ready(ver)`; istemci ana ekranda "Güncelleme hazır · X" Sonra / Şimdi kur sorar (oynatmada açılmaz).
  Şimdi kur -> `DZAndroid.installUpdate()` -> sistem kurulum ekranı (`ACTION_VIEW`; API 24+ `content://com.diziflix.app.apk/…`,
  altı dış önbellekten `file://`). `REQUEST_INSTALL_PACKAGES`; "bilinmeyen kaynak" izni yoksa (API 26+) ayarlar ekranına gider,
  izin verilip dönülürse kurulum kendiliğinden sürer.
- Köprü: `DZAndroid.version()`, `pendingUpdate()`, `checkUpdate()` (Ayarlar > Güncellemeleri denetle; sonuç `DZUpdate.status`),
  `installUpdate()`.

## Yükleme

```bash
adb connect <kutu-ip>:5555 && adb install -r dist/diziflix.apk
adb shell monkey -p com.diziflix.app -c android.intent.category.LEANBACK_LAUNCHER 1
adb logcat -d | grep -E "DZWeb|AndroidRuntime"
```

macOS'ta `adb connect` "No route to host" verirse: `adb kill-server` sonra tekrar dene.

## Tuş eşlemesi (client/js/app.js KEYMAP/KEYNAME ile birebir)

| Android | JS'e giden (key / keyCode) |
|---|---|
| D-pad oklar, DPAD_CENTER, rakamlar | doğal (DPAD_CENTER → `Enter` / 13) |
| BACK | `Escape` / 27 (kök ekranda istemci `DZAndroid.exit()` çağırır → uygulama kapanır); tam ekran iframe açıksa önce onu kapatır |
| CHANNEL_UP / PAGE_UP | `PageUp` / 33 (sonraki kanal) |
| CHANNEL_DOWN / PAGE_DOWN | `PageDown` / 34 (önceki kanal) |
| MEDIA_PLAY_PAUSE, HEADSETHOOK | `MediaPlayPause` / 10252 |
| MEDIA_PLAY / PAUSE / STOP | `MediaPlay` / 415, `MediaPause` / 19, `MediaStop` / 413 |
| MEDIA_FAST_FORWARD / REWIND | `MediaFastForward` / 417, `MediaRewind` / 412 |
| PROG_RED | `ColorF0Red` / 403 (oynatıcıda kaynak bozuk bildir, dışında arama) |
| PROG_YELLOW, CAPTIONS | `ColorF2Yellow` / 405 (altyazı) |

JS köprüsü: `window.DZAndroid.exit()`, güncelleme için `version()`, `pendingUpdate()`, `checkUpdate()`, `installUpdate()`.
