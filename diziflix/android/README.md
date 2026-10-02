# Diziflix — Android istemcisi

Diziflix sunucusuna (`server/`) bağlanan basit, native bir Android uygulaması (telefon/tablet; aynı APK Android TV kutusunda kumandayla da çalışır — bkz. "Android TV modu").
Kotlin · Jetpack Compose (Material3, koyu tema) · Media3 ExoPlayer · minSdk 26 · targetSdk 34 ·
paket `com.diziflix.app` · tek modül `app`. Arayüz metinleri Türkçedir.

API sözleşmesi: `../API.md`, `../docs/DATA-CONTRACT-V1.md`. Davranış, `../tizen-client/` (TV istemcisi) ile
birebir aynı uç noktaları ve akışı izler.

> **Durum:** Kod, derleme araçları olmayan bir makinede yazıldı; **henüz derlenmedi/çalıştırılmadı**.
> Derleme ve testler ilk kez `./gradlew` ile çalıştırıldığında küçük düzeltmeler gerekebilir (bkz. "Bilinen riskler").

## Özellikler

- **Sunucu adresi ayarı**: varsayılan `http://192.168.0.61:8090`, DataStore'da saklanır, Ayarlar'dan değişir,
  "Bağlantıyı Test Et" (`/api/health`, yazılı adresi kaydetmeden dener).
- **Profil seçimi** (`/api/profiles`, avatarlar `/img/avatar/..`), seçim hatırlanır; Ayarlar → "Profil Değiştir".
- **Ana sayfa**: `/api/boot?layout=tv-v1` (hero pager + satırlar), `loaded:false` satırlar görününce
  `/api/row/{id}` ile tembel yüklenir, poster kartları (`/img` küçük boyutlar, Coil), "İzlemeye Devam Et"
  satırı (karta dokununca detay HER ZAMAN en üstten açılır; ilgili bölüm listede vurgulanır, otomatik kaydırma yok).
- **Detay**: film ve dizi; özet, tür, oyuncular, puan, süre; dizide üstte sezon seçici (gerçek sezon posteri
  varsa poster) ve bölüm listesi (`still_url`, numara, başlık, özet, süre, tarih, "Yakında"/"Kaynak yok"/"Kaynak
  kontrol ediliyor"); Listeme ekle/çıkar; Oynat/Devam Et; `has_trailer` ise Fragman; benzer yapımlar.
  Sunucu detayı arka planda doldururken (`hydrating: true`) "Detaylar yükleniyor…" gösterilir; uygulama kapsamlı
  `HydrateWatcher` her 3 sn `?poll=1` ile (en çok 120 sn, en çok 3 eşzamanlı, ağ hatasında sessizce durur) yoklar,
  bitince açık detay kendini günceller ve her ekranda "«Başlık» izlemeye hazır" / "Bölümler şu an alınamadı…" bildirimi çıkar.
  **Kaynak yok** (`availability.state == unavailable` + `reason == no_video_source`) gerçek bir getirme hatası DEĞİLDİR: detayda
  "Bu dizi için henüz izleme kaynağı yok." (film: "Bu film için …"), Oynat/Devam Et çizilmez ve hidrasyon bildirimi "alınamadı" yerine bu
  metni söyler (`HydrateOutcome.NoSource`); "alınamadı" yalnız gerçek ağ/sunucu hatasında ya da bölümsüz biten dizide kalır.
- **Filmler / Diziler / Listem** (`/api/catalog`, tür-yıl-izlenebilirlik-sıralama filtreleri, sonsuz kaydırma),
  **Arama** (önce yerel katalog, ≥3 karakterde `/api/search` canlı kaynak; hata olursa yerel sonuçlar kalır). Arama sonuç kartında küçük
  **kaynak etiketleri** (`source_options`: ilk 2 + "+N"; `broken` ⚠ işaretli, `unknown` soluk; yalnızca bilgi, kart davranışı aynı); ızgaranın
  altında `remote_sites`'ta yanıt vermeyen (`ok:false`, atlanmamış) kaynaklar için silik tek satır "Bazı kaynaklar yanıt vermedi: …".
- **Ana sayfa "Tümü"**: `series`/`movies` satırlarının yanında `trending_series`/`trending_movies`/`noteworthy_movies` satırları da trend/popüler
  sıralı kataloğa gider (`catalog/{satır}` rotası = `CatalogView.TrendingSeries|TrendingMovies|NoteworthyMovies`, sunucu
  `GET /api/catalog?type=…&sort=trending|popular`; "Sıra" süzgeci o sıralamayı da listeler, süzgeç temizlenince ona dönülür).
- **Oynatıcı** (Media3 ExoPlayer), TV istemcisiyle aynı mantık:
  - Oynat'a basınca yükleme ekranı: dönen gösterge + Türkçe atasözü (tekrarsız rastgele, ~5 sn'de bir değişir);
  - `/api/streams/...` akışları sırayla denenir (doğrudan hls/mp4 önce, embed sona); ilk **gerçekten oynayan**
    akışta oynatıcıya geçilir; her akışın başarı/hatası kendi `attempt_token`ı ve `engine` ile
    `/api/playback-report`a bildirilir; hepsi başarısızsa hata + "Tekrar dene";
  - `type=embed` akışlar (YouTube fragman) için WebView yedeği ("Sorun bildir" elle failure raporu yollar);
  - kaynak/kalite menüsü (sunucu `label`ları), ses/altyazı izi menüsü (ExoPlayer iz seçimi; sunucu ileride
    `subtitles[]` döndürürse harici altyazı otomatik eklenir, Türkçe olan varsayılan seçilir);
  - konum kaydı `/api/progress`: 20 sn'de bir + duraklatma/arama/çıkış/bitiş; devam konumu (`resume_position`);
  - akış hiç çıkmazsa sunucunun **kaynak bulucusu** devreye girer (`streams` yanıtında `finder.state`): hata panelinde `searching` ->
    "Kaynak aranıyor… Bulununca haber vereceğiz. Sayfada kalabilir ya da uygulamada gezinebilirsin.", `not_found` -> "Bu bölüm için kaynak
    bulunamadı."; `finder` yoksa genel mesaj. Panelde kalınırsa `GET /api/source-finder/{id}?episode=` 5 sn'de bir yoklanır; `found` olunca
    "Kaynak bulundu, yeniden deneniyor…" ile akış bir kez otomatik yeniden istenir (`PlayerViewModel.showFinder`, `SourceFinderLogic`);
    düğmeler Tekrar dene / Geri. `streams[].proxied` oynatıcıda yok sayılır (`url` aynen oynatılır);
  - oynarken akış koparsa konumdan sıradaki akışa geçer; bölüm bitince "Sonraki Bölüm" geri sayımı;
  - tam ekran + yatay yönelim, ekran açık kalır, çift dokunuşla ±10 sn, kontroller 3 sn sonra gizlenir.

- **Bildirimler** (`GET /api/notifications?profile=&since=`, `POST /api/notifications/read?profile=` `{upto}`): uygulama açıkken (ekran
  önde, oynatıcı DIŞINDAKİ her ekranda) 30 sn'de bir yoklanır; oynatıcıdayken durur, çıkınca hemen bir kez sorulur (`MainNav`,
  `NotificationPoller`). `last_id` imleci profil başına DataStore'da kalıcıdır (`notif_since_<profil>`). Yeni `source_found` -> "Kaynak
  bulundu: {başlık} S01 B03" (film: yalnız başlık; en çok 3 ayrı toast + "N yapım daha" özeti) ve gösterildikten sonra `read`. İlk çalıştırmada
  (imleç yok) eski bildirimler toast OLMAZ, yalnız imleç ilerler. Tablet/telefonda Snackbar'daki "Aç" ilgili detaya (bölüm odaklı) gider; TV'de
  toast yalnızca bilgidir (odak çalmaz).

## Mimari

```
android/
├── settings.gradle.kts · build.gradle.kts · gradle.properties
├── gradle/libs.versions.toml            sürüm kataloğu (tüm sürümler burada)
├── gradle/wrapper/gradle-wrapper.properties
├── bootstrap-android-toolchain.sh       JDK17 + Gradle + Android SDK kurulumu (macOS/Homebrew)
└── app/
    ├── build.gradle.kts · proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml      INTERNET, network_security_config (LAN HTTP)
        │   ├── res/                     tema, adaptive ikon (vektör), network_security_config.xml
        │   └── java/com/diziflix/app/
        │       ├── DiziflixApp.kt · MainActivity.kt · AppContainer.kt   elle DI, tek Activity
        │       ├── data/model/Models.kt   @Serializable modeller (bilinmeyen alan yok sayılır, varsayılanlı)
        │       ├── data/net/              ApiClient (OkHttp, disk önbelleği/ETag), ApiJson, ApiError, UrlUtil
        │       ├── data/settings/         DataStore ayarları (sunucu adresi, profil)
        │       ├── data/repo/             DiziflixRepository (önce-önbellek/arkada-yenile akışları + detay önbelleği)
        │       ├── data/cache/            çevrimdışı açılış: FileCacheStore (atomik JSON), LocalCache (sürümlü, sunucuya
        │       │                          bağlı, detay LRU 30), offlineFirst (SWR), ProgressQueue (ağ yokken ilerleme)
        │       ├── domain/                saf mantık: DetailLogic (bölüm durumu/hedef), CatalogLogic, Format
        │       ├── play/                  PlayFlow (akış deneme mantığı, oynatıcıdan bağımsız), StreamLogic,
        │       │                          Proverbs, TrackLabels, ExoPlayback (ExoPlayer gerçeklemesi)
        │       └── ui/                    theme · tv (TV modu: Tizen tasarımlı TÜM ekranlar — ana sayfa, detay, katalog/arama, ayarlar, profil,
        │                                  oynatıcı arayüzü, bildirimler — ve ortak bileşenler) · common · nav (Navigation-Compose) · profiles · home ·
        │                                  detail · catalog (katalog+arama) · settings · player   (tablet/telefon ekranları; TV dalı içermez)
        └── test/                          JVM birim testleri + resources/ gerçek JSON yanıtları
```

Katmanlar: **Compose ekranı → ViewModel (StateFlow) → Repository → ApiClient (OkHttp)**. Profil seçilmeden
`NavHost` hiç kurulmaz (kök bileşende kapı); seçilince alt çubuklu (Ana Sayfa/Filmler/Diziler/Listem/Ara) tek
`NavHost`, detay ve oynatıcı tam ekran rotalardır. `PlayFlow` ExoPlayer'ı bilmez: `StreamProbe` arayüzü
üzerinden çalışır, bu yüzden sahte oynatıcıyla test edilir.

Notlar:
- `engine` alanı: sunucu yalnızca `"" | avplay | html5 | embed` kabul eder (başka değer 422). ExoPlayer
  raporları `html5` (yerel oynatıcı), WebView `embed` olarak gider.
- `code` alanı da sunucu listesiyle sınırlıdır; ExoPlayer hata kodları `PlaybackErrors.toReportCode` ile eşlenir.
- `detail` (isteğe bağlı, yalnız `failure`, ≤120 karakter): ExoPlayer `exo:<errorCodeName>[/<neden sınıfı>][/http<kod>]` (`PlaybackException.reportDetail()`,
  saf kısmı `PlaybackErrors.exoDetail/clipDetail`), zaman aşımı `start-timeout`, elle bildirim `user-report`; alanı bilmeyen sunucu için zararsız.
- Motor seçimi `type`'a göre: `StreamLogic.mimeTypeFor` -> `hls` = `MimeTypes.APPLICATION_M3U8`, `mp4` = `MimeTypes.VIDEO_MP4`; `MediaItem` MIME'ı
  AÇIKÇA verilir (vekil adresi `.../index.m3u8`, doğrudan adres `.txt` olabilir; uzantıya güvenilmez). `proxied`/başlık davranışı aynı.
- LAN'da düz HTTP için `network_security_config.xml` cleartext'e izin verir (gerekçe dosyada yorumlu).

## Android TV modu

Tek APK; TV davranışı **çalışma zamanında** açılır, tablet/telefon yolu değişmez.

### Tizen tasarım eşleşmesi (Aşama A: tasarım dili + üst menü + ana sayfa; Aşama B: kalan tüm ekranlar)

TV arayüzü tablet composable'larının içine koşul serpiştirilerek DEĞİL, **ayrı TV composable'larıyla** kurulur
(`ui/tv/`: `TvHome.kt` ana sayfa/hero/satır/kart, `TvDetail.kt` detay, `TvCatalog.kt` katalog + arama, `TvSettings.kt`,
`TvProfiles.kt`, `TvPlayer.kt` oynatıcı arayüzü, `TvToast.kt` bildirimler, `TvModals.kt` pencere/tam ekran durumlar,
`TvFields.kt` metin alanı, `TvNavFrame.kt` çerçeve, `TvComponents.kt` düğme/üst menü/pencere/görsel, `TvTheme.kt` renk +
ölçek + yazı, `TvHomeModel.kt` satır modeli; saf mantık `domain/Tv{Design,DetailDesign,ScreensDesign,ToastDesign}.kt`).
Tablet ile ortak olan yalnız ViewModel/veri katmanı, `domain/` mantığı ve nav grafiğidir; nav grafiğinde her ekran için tek
yönlendirme satırı vardır (`composable(HOME) { if (isTv) { TvHomeScreen(...); return@composable } ... }`). Tablet
composable'ları (`HomeScreen`, `DetailScreen`, `CatalogScreen`, `SearchScreen`, `SettingsScreen`, `ProfilesScreen`, `PlayerScreen`,
`Common.kt`) hiçbir TV dalı/`LocalIsTv` içermez. Tasarımın kaynağı
`../tizen-client/css/{base,home}.css` ve `js/{nav,ui/*,screens/home}.js`'tir.

- **Ölçekleme (`tvDp`)**: Tizen sahnesi 1920x1080 "px"tir; TV'de tüm ölçüler o birimle yazılır ve
  `tvDp(n) = n * (ekranGenişliğiPx / 1920) / density` ile çevrilir (saf, birim testli: `domain/TvDesign.kt`). 1080p +
  density 320'de 1 Tizen px = 0,5 dp = 1 piksel; poster 240 px -> 120 dp, satırda 7 tam + 1 kısmi poster görünür.
  `TvTheme` yazı ölçeğini 1'e sabitler (TV tasarımı sistem yazı ölçeğinden bağımsız), `MaterialTheme`'e dokunmaz.
  Tizen değerleri `TvSpec`'te (kaynak CSS yorumlu): `--safe` 60, poster 240x360, boşluk 12, odak kartı 640x360, `focus-poster` 270
  (%112), odak çerçevesi 7, hero 600, üst menü 120, satır başlığı 30 px, altyazı 24/22 px; renkler `TvColors`
  (`#141414` zemin, `#F5C518` vurgu, `#85752A` soluk altın).
- **Üst menü (`TvTopBar`)**: sol küçük karakter amblemi (62x48 px, dekoratif, odaklanmaz; odak kutusu amblem + yazıdır) + `DIZIFLIX` (sarı, geniş harf aralığı; Tamam = ana sayfa / ana sayfadayken içeriğe iner),
  "⌕ Film veya dizi ara" kutusu (Tamam = arama ekranı; ilk odak durağı, içerikten Yukarı ile buraya çıkılır), "Listem",
  boşluk, "Profil", "Ayarlar". "Ana Sayfa" öğesi yok. Kare köşeli (4-5 px), odakta sarı dolgu + koyu yazı + hafif büyüme
  (Tizen `.tb-item`); Material hap/sekme çubuğu TV'de yok. Ortak düğme `TvButton` (Tizen `.btn`), pencere `TvModal`.
- **Ana sayfa satırları**: kartlar Tizen poster kutusu (240 px, 2:3); bölüm kartları da dizinin POSTER'ı. Odaktaki kart yatay
  afişi varsa (başlıkta `has_backdrop`, bölümde gerçek still -> yoksa backdrop) **640x360'a genişler**: poster yatay görsele
  geçer, içinde başlık + `yıl • ★ puan` (bölümde `S07 B05 · Ad`) önizlemesi, komşular sağa itilir; yatay görsel yoksa 270'e
  %112 büyür. Altında başlık + `★ Puan 7.2` / bölüm etiketi, `continue` kartlarında ilerleme çubuğu. Görseller /img izinli
  boyutlarda (poster 300x450, still 640x360 / 454x254, hero 1280x720; yatay afiş yalnız kart odaklanınca indirilir); görsel
  penceresi odak ±4 kart + görünenler, dikeyde odak satırı -1/+2 bileşime alınır (uzun satırlarda bellek sabit).
  Satır sonunda "Tüm Diziler / Tüm Filmler" kartı; trend/dikkate değer satırlarında (`trending_series`/`trending_movies`/`noteworthy_movies`)
  "Tümünü Gör" kartı trend/popüler sıralı kataloğa gider (TV'de üst menü de görünür); yükleme iskeleti shimmer; satır hatasında odaklanabilir "Tekrar dene" kartı.
- **Kaydırma modeli (Tizen `nav.js`)**: ana sayfa Compose'un lazy/scroll mekanizmasını KULLANMAZ; Tizen gibi SANAL odak:
  tek bir kök odak hedefi tuşları alır, "hangi satır/sütun odakta" durumu `TvHomeFocus`'ta (ekran yığınından dönünce
  `rememberSaveable` ile geri gelir), kararlar saf mantıkta (`TvHomeNavigation`, `TvHomeScroll`). Kaydırma açık
  `translationY`/`translationX` animasyonudur (200 ms) -> Compose'un odakla tetiklediği kendi kaydırması ile çakışma yok.
  Dikey: **hero ya da üst menü odaktayken sayfa y=0 (hero TAM görünür)**; bir satır odaktayken o satırın başlığı sabit üst
  yuvaya (sayfa y=140) oturur. Aşağı: satırlara inince hero yukarı kayar; Yukarı: ilk satırdan hero'ya çıkınca hero tamamen
  geri gelir, oradan Yukarı üst menüye çıkar, üst menüden Aşağı ilk satıra (hafızadaki sütun) döner. Yatay: odaktaki kart
  ikinci yuvada durur, hemen solundaki kart tam görünür (`x = (sütun-1)*252`). Her satır kendi son sütununu hatırlar.
- **Hero (Tizen `hero.js`)**: tam genişlik backdrop + yatay/dikey koyu gradyan, büyük başlık, meta (`yıl · Puan · tür · Dizi/Film`),
  2 satır özet, sayfa noktaları, odaktayken sarı çerçeve; Sol/Sağ slayt değiştirir (dairesel), Tamam = detay. Tizen'de olduğu
  gibi hero'da Oynat/Detay düğmesi YOKTUR (tek odak hedefi).

- **Algılama** (`ui/tv/Tv.kt` `detectTv`, karar `domain/TvLogic.kt` `TvDetector`): `UiModeManager` kipi televizyon **veya**
  `FEATURE_LEANBACK` **veya** dokunmatik ekran yok. Sonuç `LocalIsTv` (CompositionLocal); TV'ye özgü her şey yalnızca buna
  bağlıdır (false iken tüm TV modifier'ları boştur). Manifest: `LEANBACK_LAUNCHER` kategorisi (LAUNCHER yanında) +
  320x180dp `tv_banner`; `touchscreen`/`leanback` `uses-feature required=false` (tablet kurulumu engellenmez).
- **Aşama B — ekran ekran Tizen eşleşmesi** (kaynak: `../tizen-client/css/*.css`, `js/screens/*.js`, `js/ui/*.js`; tüm ölçüler
  Tizen px, `tvDp` ile çevrilir; hepsi kumandayla tam kullanılır):
  - **Detay** (`TvDetail.kt` <- `detail.css` + `screens/detail.js`): sanal odak, sayfa `translationY` ile kayar. 760 px hero
    (backdrop + yatay/dikey gradyan), sol-altta 1000 px gövde: başlık 64 px, meta (`yıl · tür · ülke · süre · takipçi · Puan`),
    kaynak notu, "Detaylar yükleniyor…" (italik, odaksız), eylem düğmeleri (`TvButtonFace` = Tizen `.btn`, içeriğe göre genişler ve etiketi
    ASLA "…" ile kesilmez: satır 1000 px gövdeden güvenli alana [1800 px] kadar taşabilir, onu da aşarsa `FlowRow` ikinci satıra sarar; sırası sunucunun
    `actions[]`'ı: Oynat/Devam Et · [İlk Bölümden Başla] · Fragmanı Oynat | ölü fragman = gri "Fragman yok" (Tamam: toast) · Listeme
    Ekle/Listemden Çıkar; `actions` yoksa eski alanlardan türetilir), 4 satırlık özet (odaklanabilir; Tamam = tam metin
    penceresi, "Devamını oku ▸"), ekip satırı. Dizide altında SOLDA 440 px dikey sezon listesi (gerçek poster varsa 68x102;
    aktif sezon `#85752A` soluk altın + sarı sol bant; odakta canlı sarı) + SAĞDA bölüm satırları (162 px: 240x135 yatay still,
    numara, başlık, "Yakında · tarih"/"Kaynak yok"/"Kaynak kontrol ediliyor" bayrağı, 2 satır özet, "45 dk · 24 Tem 2026",
    ilerleme çubuğu). Listeler sabit yükseklikli görüntü alanında kayar (en çok 900 px; pencereli: yalnız görünen ±3 satır
    bileşimde), odaklı satır üstten 1, alttan 2 satır boşlukla görünür kalır. Sezonda gezinirken bölüm listesi 150 ms sonra
    geçer; Sağ = bölümlere (hedef: oynatılabilir ilk izlenmemiş), Sol = sezonlara. En altta "Benzer Yapımlar" (ana sayfa satır
    bileşeni). Sayfa kaydırması: düğmeler/özet/benzerler satır üstü ekran yüksekliğinin %28'ine, sezon/bölüm odaklıyken
    tarayıcı ekranda üstten 96 px aşağıya oturur. Ana sayfadan bölüm kartıyla gelinirse ilgili bölüme odak.
  - **Ayarlar** (`TvSettings.kt` <- `settings.css` + `screens/settings.js`, yeni tasarım): üstte sağda "Profil değiştir", "Ayarlar"
    başlığı, "Sunucu adresi" kartı [girdi + içinde "Bağlantıyı test et"] [sarı Kaydet] + durum satırı, yan yana iki yuvarlak kart
    **"Varsayılan altyazı dili"** (Türkçe/İngilizce/Kapalı; profil başına) ve **"En yüksek kalite"** (1080p/1440p/Otomatik; cihaz geneli)
    dikey radyo listeleri (seçili = dolu nokta + soluk sarı zemin; odak = sarı halka), "Önbelleği temizle", soluk bilgi satırları
    (sürüm/motor, adres, sunucu kaynağı · içerik sayısı). Logo yok; açılış odağı Kaydet. **Tercihler gerçekten uygulanır**:
    kalite sınırı akış sıralamasında (`StreamLogic.order(streams, cap)`: sınırı aşan doğrudan akışlar embed'lerden önce sona
    atılır), altyazı dili ExoPlayer'a varsayılan iz seçiminde (`defaultSubtitleIndex(subs, pref)`). Kayıt yoksa TV'de kalite 1080p
    (Tizen varsayılanı), tablet/telefonda sınırsız (eski davranış); DataStore anahtarları `quality_pref`, `sub_pref_<profil>`.
  - **Profil seçimi** (`TvProfiles.kt` <- `profiles.css` + `screens/profiles.js`): ortada geniş DiziFlix logosu (eski DIZIFLIX yazısının yerine), "Kim izliyor?", 200x200
    avatar kutuları (odakta sarı kenar + %110; ÇOCUK rozeti), altta "Ayarlar". (Profil ekleme/düzenleme Android'de yok.)
  - **Arama / Katalog** (`TvCatalog.kt` <- `home.css` `.catalog-*` + `screens/catalog.js`; arama kartında kaynak etiketi hapları + altta silik
    "Bazı kaynaklar yanıt vermedi" satırı): 5 sütun 300x450 poster ızgarası (aralık 60),
    başlık 48 px + "N yapım", süzgeç düğmeleri (`.btn.small`: Tür / Yıl / İzlenebilirlik / Sıra / Filtreleri temizle; Tamam = dikey düğmeli
    seçim penceresi `TvPickModal`), kart altında başlık + "2021 · Dizi" + durum notu; odakta %104 + 7 px sarı çerçeve. Sanal odak;
    odaktaki satır ekranda y=140'a oturur; sonsuz kaydırma (sona 2 satır kala sonraki sayfa). Arama ekranında üst menüdeki kutu
    GERÇEK yazı alanıdır (`TvSearchInputBox`: 510x54, ✕ düğmesi); yazarken arama mantığı (450 ms bekleme, 2+ karakter yerel, 3+
    canlı kaynak) ViewModel'de değişmedi. "Aranıyor…" halkası, "Kaynakta aranıyor…" notu, boş/hata durumları Tizen metinleriyle.
  - **Oynatıcı arayüzü** (`TvPlayer.kt` <- `player.css` + `screens/player.js` + `ui/tracks_panel.js` + `ui/modal.js`): üst başlık 44 px +
    "S04 B02 · Ad" + kaynak etiketi (gradyan), alt ilerleme çubuğu (8 px, sarı dolgu + top) + süre/kalan süre + durum satırı
    ("DURAKLATILDI", ">> 10 sn"), sağ altta `[Ses ve Altyazılar · X] [Kaynak / kalite: X]` düğmeleri. **Yukarı = Kaynak/kalite
    menüsü** (düğmenin üstünde açılır liste, ● geçerli, sarı = gezinilen), **Aşağı / Sarı tuş = "Ses ve Altyazılar" paneli** (iki
    sütun SES | ALTYAZI, tek seçenekli sütun soluk ve odaksız, Sol/Sağ sütun, Tamam uygula, Geri kapat), bölümün son 45 sn'sinde sağ
    üstte "Sonraki bölüm" kartı (Tamam = şimdi oynat, Geri = iptal; bölüm bitince 5 sn geri sayım), yükleme = tam ekran
    halka + dönen Türk atasözü + aşama satırı (`TvLoadingPane`), hata = tam ekran "Kaynak çalışmıyor" penceresi (Tekrar dene/Geri),
    embed yedeğinde `← Geri` / `Sorun bildir` düğmeleri. Oynatma mantığı (PlayFlow, ExoPlayer, konum kaydı) tabletle ortaktır.
  - **Bildirimler** (`TvToast.kt` <- `toast.js` + `.toast`/`.toast-rich`): küçük toast (altta, 3,5 sn) ve **zengin bildirim** (üst-orta geniş
    koyu kart, 2 px sarı çerçeve, sol-üst + sağ-alt (180°) sarı köşe süsü `res/drawable-nodpi/tv_ornament_corner.png`, büyük beyaz başlık
    + sarı "İZLEMEYE HAZIR" etiketi, 7 sn; tek kart, gelenler sırayla, en çok 3 bekleyen). Hidrasyon "hazır/alınamadı" olayları TV'de bu
    kartla gösterilir ("izleme kaynağı yok" bilgisi de aynı kartta uyarı türüyle, "alınamadı" metni OLMADAN); kaynak bulucu bildirimleri küçük
    toast'tır; tablet Snackbar'ı aynen kalır. "Listeden kaldır / Vazgeç" menüsü Tizen `.modal` (`TvModal`).
- **Odak modeli**: TV'de odak halkası Tizen'dedir (sarı dolgu/çerçeve); tuşlar ekran başına tek kök odak hedefinde işlenir
  (ana sayfa, detay, katalog/arama, oynatıcı: sanal odak) ya da Compose'un 2B odak gezinmesi kullanılır (ayarlar, profil, pencereler).
  Gezinme yığınından dönünce son odak geri gelir (`rememberSaveable` odak durumları); hata/yükleme ekranlarında ilk düğmeye odak;
  metin alanına odak ekran klavyesini açar, kapatılınca Tamam yeniden açar, klavyenin "Bitti/Ara" eylemi odağı aşağı taşır.
- **Ekranlar**: Ana sayfa — hero Sol/Sağ ile slayt değiştirir; "İzlemeye Devam Et" kartında **Tamam'a uzun basış (~600 ms)**
  "Listeden kaldır / Vazgeç" penceresi açar, kısa basış detayı açar.
- **Oynatıcı kumandası**: Tamam/Oynat-Duraklat = oynat/duraklat; Sol/Sağ (veya Geri sar/İleri sar) = ±10 sn, basılı tutunca
  30 -> 60 -> 120 sn'ye hızlanır (sunucuya konum yalnızca sarma bitince yazılır); Yukarı = Kaynak/kalite menüsü; Aşağı / Sarı =
  Ses ve Altyazılar paneli; kontroller 3 sn sonra gizlenir. **Geri**: panel/menü açıksa önce onu, sonra "sonraki bölüm" önerisini,
  sonra kontrolleri kapatır; hiçbiri yoksa çıkar. **Katman tuş yönlendirmesi** (`TvPlayerOverlayController`/`TvPlayerOverlayKeys`, saf
  ve birim testli): panel/menü açıkken yalnızca katmanın kullandığı tuşlar tüketilir (yön, Tamam, Geri, panelde Sarı); sarma/duraklatma
  tuşları yutulur, ses tuşu vb. sisteme geçer. Geri artık katmanı doğrudan kapatır (eski kusur: her tuş `true` ile
  yutuluyor, BackHandler tetiklenmiyor, panelde seçilecek seçenek yoksa [tek ses + yalnız "Kapalı"] hiçbir tuş çalışmıyordu).
  Seçilebilir hiç seçenek yoksa panelde odaklı **"Kapat"** düğmesi çıkar (Tamam/Geri kapatır). Klavye odağı her zaman oynatıcı
  kök hedefindedir; katman açılıp kapanınca odak kökte yeniden doğrulanır. Hata penceresi (`TvModal`) ve embed yedeği odak boşa
  düşerse ilk düğmeye odağı geri alır.
- **Üst menü odak belleği**: Ayarlar / Listem / arama ile bir ekrana gidip Geri ile dönünce odak ilgili üst menü öğesine geri gelir
  (hero/ilk satıra değil): `TvBarFocus` (öğe başına `FocusRequester` + `TvBarReturn` belleği; rota adlarıyla, bayat bellek silinir).
  Ana sayfa ve katalog ekranları (Filmler/Diziler/Listem) giriş efektinde `take(route)` ile okur.
- **Geri tuşu**: Navigation yığını; ana sayfada kök = sistem varsayılanı (çıkış onayı yok). Bildirimler (toast) eylemsizdir,
  odak çalmaz.
- **Bilinen sınırlar**: görsel doğrulama cihazda kullanıcı tarafından yapılır (bu sürüm yalnızca derleme/birim testiyle
  doğrulandı); aşırı uzun satırlarda (16'dan çok kart) geri yükleme hedefi henüz çizilmediyse varsayılan ilk odağa
  düşer; YouTube gömme (WebView) sayfasında kumanda, sayfanın kendi odağına bağlıdır (Geri her zaman çıkar);
  ekran klavyesinin kendisi (Gboard/Leanback) sistemindir; TV ekranlarında fare/dokunma yok (yalnız kumanda); `tvDp` yerleşimi
  yatayda ekran genişliğine orantılıdır (16:9 dışı ekranda dikey pay değişir); profil ekleme/düzenleme ve altyazı dilinin oynatıcıdan
  hatırlanması (Tizen `remember`) Android'de yok; ses tercihi (`dz_pref_audio`) yok; katalogda sayfa düğmeleri yerine sonsuz kaydırma.

## Marka görselleri (logo, ikon, TV afişi)

Kaynak: `../docs/brand/logo-a.png` (kare, klaket sağda) ve `logo-b.png` (klaket solda); üretim betiği
`../docs/brand/make_brand.py` (Pillow: `server/venv/bin/python`). **Android çıktılarını** yeniden üretmek (Tizen dosyalarına dokunmaz):

```bash
Q=1 server/venv/bin/python docs/brand/make_brand.py android      # repo kökünden; tizen | android | all
```

| Dosya (`app/src/main/res/`) | Boyut | Kullanım |
|---|---|---|
| `mipmap-anydpi-v26/ic_launcher(.xml,_round.xml)` + `drawable-nodpi/ic_launcher_foreground.png` (432x432) + `drawable/ic_launcher_background.xml` | 108 dp | **Uygulama ikonu** (adaptive): ön plan = yazısız karakter (bilet + klaket + şerit; güvenli bölgede, tuvalin ~%60'ı), zemin = sıcak koyu kahve + yumuşak altın ışık (Tizen `icon.png` zemini, vektör radyal gradyan); `drawable-nodpi/ic_launcher_monochrome.png` = Android 13+ **tema ikonu** |
| `mipmap-{m,h,xh,xxh,xxxh}dpi/ic_launcher(_round).png` | 48..192 px | Adaptive ikon öncesi yedek (minSdk 26'da kullanılmaz; aynı karakter + zemin) |
| `drawable-xhdpi/tv_banner.png` · `drawable-xxhdpi/tv_banner.png` | 640x360 · 960x540 | **TV başlatıcı afişi** (320x180 dp): yazılı tam logo (LOGO_B) koyu zeminde; `application`/`activity` `android:banner` |
| `values-v31/themes.xml` | – | **Android 12+ açılış (splash)**: `windowSplashScreenAnimatedIcon` = karakter (`ic_launcher_foreground`), zemin `dz_background` |
| `drawable-nodpi/brand_logo_wide.png` | 720x710 | Profil seçimi (TV: 360x355 px, eski DIZIFLIX yazısının yerine; tablet: ~110 dp logo, `BrandLogoWide`) |
| `drawable-nodpi/brand_logo_square.png` | 630x640 | Açılış/boot: ortada kare logo (`AppRoot` ayarlar yüklenene kadar; TV ana sayfa iskeletinin üstünde plaka `TvBootLogo`) |
| `drawable-nodpi/brand_mascot.png` | 310x240 | Oynatma "yükleniyor" ekranında spinner'ın üstünde (TV 155x120 px, tablet 64 dp) ve hata ekranlarında yazının üstünde (TV 194x150 px `TvErrorScreen`/`TvHomeError`, tablet `ErrorBox` 56 dp) |
| `drawable-nodpi/brand_mascot_sm.png` | 124x96 | TV üst menüsü: `DIZIFLIX` yazısının solunda 62x48 px amblem (odak sırası/yükseklik değişmez) |

Hepsi statik drawable (`painterResource`; Coil/R8 için yeni kural yok), her PNG < 150 KB; release APK ~4,2 MB. Kullanım yerleri
`ui/tv/TvBrand.kt` (TV) ve `ui/common/Brand.kt` (tablet/telefon); ölçüler `TvBrandSpec`, kaynak/oran/boyut denetimi `TvBrandTest`.

## Sunucu adresi

Varsayılan `http://192.168.0.61:8090`. Uygulama içinde **Ayarlar** (ana sayfada dişli simgesi, profil
ekranında "Ayarlar" düğmesi) ekranından değiştirilir. Telefon ve sunucu aynı ağda olmalıdır.

## Derleme

Ön koşul: JDK 17 + Android SDK 34. macOS'ta hepsini kurmak için (yeniden çalıştırılabilir):

```bash
cd android
./bootstrap-android-toolchain.sh --build   # kurulum + testler + debug APK
```

Elle:

```bash
export JAVA_HOME=$(/usr/libexec/java_home -v 17)      # ya da brew openjdk@17 yolu
export ANDROID_HOME=$(brew --prefix)/share/android-commandlinetools
./gradlew :app:testDebugUnitTest      # JVM birim testleri
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Yayın (R8 küçültme + kaynak küçültme, debug anahtarıyla imzalı; aynı `applicationId` ve imza olduğundan
debug -> release `adb install -r` veriyi silmeden çalışır):

```bash
./gradlew :app:assembleRelease        # app/build/outputs/apk/release/app-release.apk
adb install -r --no-streaming app/build/outputs/apk/release/app-release.apk
# Compose kararlılık raporu (isteğe bağlı, depoya yazılmaz):
./gradlew :app:compileDebugKotlin -PcomposeReportsDir=/tmp/compose-reports
```

**`gradlew` ve `gradle/wrapper/gradle-wrapper.jar` depoda YOK** (araçlar olmadan üretilemedi; ikili dosya).
`bootstrap-android-toolchain.sh` bunları `gradle wrapper --gradle-version 8.7` ile üretir. Elle üretmek için:
`brew install gradle`, sonra boş bir klasörde `gradle wrapper --gradle-version 8.7` çalıştırıp
`gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar` dosyalarını bu dizine kopyalayın
(ya da Android Studio ile projeyi açmak yeterlidir: kendi Gradle'ını kullanır).

Seçilen sürümler (`gradle/libs.versions.toml`): AGP 8.5.2 · Gradle 8.7 · Kotlin 1.9.24 · Compose Compiler 1.5.14 ·
Compose BOM 2024.06.00 (Material3 1.2.1) · Navigation-Compose 2.7.7 · Lifecycle 2.7.0 · Activity-Compose 1.9.1 ·
Media3 1.3.1 · OkHttp 4.12.0 · kotlinx-serialization 1.6.3 · kotlinx-coroutines 1.8.1 · Coil 2.6.0 · DataStore 1.1.1 ·
Guava 33.2.1-android · JUnit 4.13.2.

## Testler

`app/src/test` (JVM, JUnit4; `./gradlew :app:testDebugUnitTest`). `resources/` altındaki JSON dosyaları canlı
sunucudan `GET` ile alınmış GERÇEK yanıtlardır (imzalı akış URL sorgu değerleri ve `attempt_token`lar sansürlü,
büyük listeler kırpılmış):

- `ModelParsingTest` — profiller, boot, satır, detay (dizi/film/kaynaksız), akışlar, katalog, arama, tolerans
- `PlayFlowTest` — akış deneme/raporlama/zaman aşımı/offline/iptal/kaynak seçimi/koparsa devam (sahte oynatıcı)
- `StreamLogicTest`, `ProverbPickerTest`, `TrackLabelsTest`, `DetailLogicTest`, `FormatAndCatalogLogicTest`, `UrlUtilTest`
- `ApiClientTest` — MockWebServer ile istek yolları/sorgu/gövde/hata eşleme (kaynak bulucu, bildirimler, katalog `sort`)
- `NotificationLogicTest`, `NotificationPollerTest` — bildirim toast metni/sınırı, ilk çalıştırmada toast yok, gösterim -> imleç -> read sırası, hata
- `SourceFinderLogicTest` — finder panel metinleri, 5 sn yoklama (sanal zaman), found/not_found/hata/üst sınır/iptal
- `SearchSourceLogicTest`, `CatalogViewTest` — arama kaynak etiketleri + "yanıt vermedi" satırı; trend/popüler katalog görünümleri
- `TvLogicTest` — TV algılama kararı, Tamam'a uzun/kısa basış süresi, sarma (hızlanma) hesabı, oynatıcı tuş eşlemesi, odak belleği/ilk odak seçimi
- `TvDesignTest`, `TvHomeModelTest` — Tizen ölçekleme (`tvDp`), ana sayfa satır/kart/hero ölçüleri, gezinme ve kaydırma kararları
- `TvDetailDesignTest` — detay: Tizen ölçüleri, eylem düğmeleri (`actions[]` + eski alanlar), ölü fragman, liste kaydırma/pencere,
  sayfa kaydırma kuralları, bölüm/sezon metinleri, odak gezinmesi
- `TvScreensDesignTest` — katalog ızgarası gezinmesi/sayfalama, ayarlar metinleri, profil, oynatıcı (süre/sarma metinleri, sonraki bölüm
  penceresi, tuş eşlemesi, kaynak menüsü, "Ses ve Altyazılar" paneli)
- `TvPlayerOverlayTest` — oynatıcı katmanları: tuş yönlendirme tablosu (panel/menü açık-kapalı), "Kapat" odağı, Geri sırası, panel gezinme senaryosu
- `TvBarReturnTest` — üst menü odak belleği (Ayarlar/Listem/arama dönüşü, bayat bellek)
- `TvBrandTest` — marka PNG'leri (boyut/oran/< 150 KB), ikon + TV afişi + splash kaynakları, manifest referansları
- `TvToastDesignTest` — bildirim ölçüleri/etiketleri, hidrasyon olayı -> zengin kart, tek kart + en çok 3 bekleyen kuyruğu
- `PlayPrefsTest`, `StreamPrefsTest`, `DetailActionsParsingTest` — kalite/altyazı tercihleri, kalite sınırlı akış sıralaması,
  `actions[]` ayrıştırma
- `OfflineFirstTest`, `LocalCacheTest`, `FileCacheStoreTest`, `ProgressQueueTest`, `RepositoryOfflineTest`,
  `OfflineLogicTest` — çevrimdışı açılış (önbellek önce, ağ hatasında korunma, sürüm/adres değişimi, LRU, ilerleme kuyruğu)

## Bilinen riskler / yapılacaklar

- Derlenmedi: ilk derlemede küçük tip/içe aktarma düzeltmeleri gerekebilir.
- `PlayerView` yalnızca video yüzeyi olarak kullanılır (`useController=false`); kontroller Compose'ta çizilir.
- Erişilemeyen/yavaş `/api/detail` (canlı aramayla ilk açılan dizi) için zaman aşımı 180 sn'dir.
- Yayın akışı adresleri kaynak sitede Referer/UA isteyebilir; ExoPlayer tarayıcı benzeri UA ile açar, başlık
  gerekiyorsa `PlayerFactory` içindeki `DefaultHttpDataSource.Factory`ya eklenir.
