# client — diziflix web istemcisi (tek istemci)

Tek web istemcisi: tarayıcıda açılır, Tizen TV için `.wgt` olarak paketlenir (`./build-wgt.sh` -> `../dist/diziflix.wgt`, `config.xml`
app id `dzfLX00001.DiziFlix`), Android'de WebView kabuğu (`android/`) aynı kodu yükler. Vanilla ES5 HTML/CSS/JS, build adımı yok,
canlı diziflix API'si. Testler: `node tests/player.js`, `node tests/search.js`, `node tests/exitconfirm.js`, `node tests/update.js`, `node tests/server_error.js`.

## Açma

```bash
cd client && python3 -m http.server 8001
./build-wgt.sh                     # Tizen paketi: ../dist/diziflix.wgt (version.json -> config.xml + js/version.js)
# http://localhost:8001            -> ilk açılışta "Sunucu adresi" ekranı (öneri: http://<sayfanın host'u>:8090)
# http://localhost:8001/?server=192.168.0.61:8090
```

Sunucu adresi sırası: kayıtlı (`dzproto.server`, ilk açılış / Ayarlar > Sunucu) → `?server=` → sayfa sunucunun kendisinden
(port 8090) açıldıysa o origin → yoksa "Sunucu adresi" ekranı. URL parametreleri: `?server=` sunucu, `?profile=p1` (test kısayolu: profil ekranını atlar).

Tuşlar: oklar, Enter/OK, Geri (Esc/Backspace, Tizen Return; Android kabuğu Geri'yi Escape olarak verir). Kök ekranlarda (ana ekran menü başı,
soğuk açılış profil seçimi, ilk açılış sunucu adresi) Geri = cam "Uygulamadan çıkılsın mı?" onayı (`js/exitconfirm.js`; Hayır varsayılan, ←→ gez,
OK seç, Geri = Hayır); Evet → Tizen `tizen.application.getCurrentApplication().exit()`, Android `DZAndroid.exit()`, tarayıcıda yalnız kapanır. Fareyle de gezilebilir. Oynatıcıda ayrıca Tizen medya tuşları
(Play/Pause/PlayPause/Stop/FF/RW), Sarı = Altyazı (masaüstü Y), Kırmızı = kaynağı bozuk bildir (masaüstü F1), Yeşil = hata ayıklama
paneli gizle/göster (masaüstü G; yalnız Hata ayıklama açıkken); Canlı TV'de
ChannelUp/ChannelDown (masaüstü PageUp/PageDown) ve rakamlar.

## Sürüm ve güncelleme (`version.json`, `js/update.js`)

- Tek sürüm kaynağı `version.json` (`{"version":"X.Y.Z"}`): `build-wgt.sh` `config.xml` widget sürümünü ve `js/version.js`'i
  (`DZ_VERSION` + `DZ_BUILD` damgası) yazar; Android gradle `versionName`/`versionCode` (X*10000+Y*100+Z) buradan okur.
  Ayarlar ekranının altında "Sürüm X.Y.Z · <build>".
- Release: proje kökünden `tools/release.sh <X.Y.Z>` (önce `--dry-run`) -> GitHub release `diziflix-vX.Y.Z`
  (`drascom/micro_services`) + `diziflix-X.Y.Z.wgt`, `diziflix-X.Y.Z.apk`, `SHA256SUMS`.
- Denetim istemcide (sunucu yok): `api.github.com/repos/drascom/micro_services/releases`, yalnız taslak/ön sürüm olmayan
  `diziflix-v*`; en çok 6 saatte bir (açılışta + aralık), ağ hatası sessiz.
- Tizen (markette değil): yeni sürüm varsa ana ekranda sürüm başına BİR KEZ bildirim "Yeni sürüm X hazır — bilgisayardan kur";
  kurulum kullanıcıda. Ayarlar > "Güncellemeleri denetle" (OK = elle denetim, sonuç satırda).
- Android: kabuk indirir + SHA-256 doğrular, `DZUpdate.ready(ver)` ile bildirir; istemci ana ekranda (oynatmada asla)
  "Güncelleme hazır · X" Sonra / Şimdi kur penceresi açar, Şimdi kur -> `DZAndroid.installUpdate()` (ayrıntı `android/README.md`).
- Tarayıcı: hiçbir şey gösterilmez.

## Ekranlar

- **Sunucu adresi** (kayıtlı adres yoksa): IP ya da URL. Kurallar (`api.serverCandidates`): şemalı URL aynen
  (`https://diziflix.ornek.com`, portsuz); şemasız alan adı → önce `https://<ad>`, olmazsa `http://<ad>:8090`; IP / localhost /
  tek etiket → `http://<host>:8090`; port verilmişse `http://<host:port>`. Bağlan adayları sırayla `/api/health` ile dener,
  ilk başarılıyı kaydeder → profil ekranı; hepsi başarısızsa "Bağlanılamadı, kaydedilmedi — <adres>: <neden> · …".
  Girdi satırında OK = ekran klavyesi, klavyede Done = Bağlan.
- **Bağlantı hatası** (profil ya da ana ekran yüklenemedi): aynı ekran hata kipinde: "Sunucuya bağlanılamadı (<adres>): <neden>",
  mevcut adresle dolu kutu (odak kutuda) + tek Bağlan. Aynı adresle Bağlan = yeniden dene (kaldığı yerden); yeni adres
  denenip kaydedilir → profil ekranı. Geri: kök ekranda çıkış onayı (Ayarlar/ana sayfadan açılan profil ekranında oraya döner).
- **Profil seçimi** (her soğuk açılışta; arka plandan dönüşte değil): ortalanmış kartlar, odak son seçilen profilde; alt satır
  Misafir (`guest: true`, kart değil, düzenlenemez) · Profil ekle · Profilleri düzenle (kart OK = Ad/Avatar/Çocuk/Kaydet/Sil).
  Sunucu adresi Ayarlar > Sunucu'dan ya da bağlantı hatası ekranından değişir (aynı kurallar; başarısızsa kaydedilmez).

- **Ana ekran** (Liste ya da Dock): sağ üstte saat (yerel) + karşılama; büyük menü = bölümler (`/api/sections`: Öne Çıkanlar ·
  Diziler · Filmler · Canlı TV · Animeler · DVR); sağda vitrin yazısı (kart yok, tüm arka plan modlarında; List'te sağ sütun
  x=920, selamlama ile Devam Et arasında dikey ortalı; odaktaki bölümün öne çıkanları, 7 sn'de bir döner; "Yakında" bölümlerde metin); açıklamanın altında **Oynat** düğmesi (ilerleme varsa "Devam et", dizide "S01 B03"; List: menüden →, Dock: dock/Devam Et'ten ↑; ← / Geri menü, ↓ Devam Et; odaktayken vitrin dönmez) → detay açılır ve İzle kendiliğinden başlar (oynatıcıda Geri → detay, detayda Geri → ana ekran, odak düğmede); "İzlemeye Devam Et" şeridi (`/api/row/continue`, boşsa yok): List'te vitrin altında (menüden →, ↓ kontroller),
  Dock'ta dock'un üstünde (dock'tan ↑, ↑ kontroller); kart OK → detay. Kontrol menüsü: List'te Ara · Listem · Ayarlar sağ alt sütunda,
  Profil hapı (avatar + ad / Misafir) en üstte saatin solunda (sütunun başından ↑, menünün başından ↑ ya da Oynat'tan ↑; hapta ↓ Oynat, ← menü);
  Dock'ta sağ üstte tek sıra Ara · Listem · Ayarlar · Profil. Profil OK = hızlı profil değiştirici (cam açılır menü, iki düzende de hapın altında): profiller (seçilide ✓) · Misafir · Profiller (tam ekran profil sayfası). ↑↓ gez, OK = anında geçiş (menü/Devam Et
  yenilenir, eski yanıtlar yok sayılır; odak pill'de kalır), Geri / başka yön = kapat. Liste önbellekten, açılınca `/api/profiles` tazelenir.
- **Bölüm** (menüde OK): `/api/section/<id>` satırları; ↑↓ satır, ←→ kart (satır başına odak saklanır), sanal pencere
  (odak satırı ±1, satır içinde görünen ±1 kart), `/api/row` ile 40'arlı tembel yükleme; Geri → menü (aynı öğe).
  DVR: "Yakında" sayfası.
- **Canlı TV** (`live_tv`, bölüm ekranıyla aynı): "Tüm kanallar" + kategori satırları; kanal kartı (`card_kind:"channel"`, görsel yok) =
  kategoriye göre sabit tonlu cam yüzey + baş harf + kanal no + CANLI. Kart OK = doğrudan oynatıcı (detay yok; kanal sırası = o satır).
  Vitrinde kanal adı, arka plan Görsellerim / gradyan; vitrin İzle de doğrudan oynatır (sıra = "Tüm kanallar" `live_all`, tembel yükleme).
- **Ara** (kontrol hapı, büyüteç; oynatıcı dışında Kırmızı / masaüstü F1 kısayol): cam arama kutusu (OK = ekran klavyesi, Enter/Done = ara;
  tarayıcıda açılınca odak kutuda, yazdıkça ~300 ms debounce). <2 karakter temizler; ≥2 `/api/catalog?q=` (Kütüphanede), ≥3 ayrıca canlı
  `/api/search` (Sitelerde; kütüphanede olanlar tekrar gösterilmez); yeni sorgu eskisini iptal eder (`js/search.js`). Yanıt vermeyen
  siteler / sonuç yok notu sağ altta. Kart OK → detay (canlı sonucun ilk açılışı uzun sürebilir: detay zaman aşımı 180 sn). Geri:
  sonuçlar → kutu → ana ekran (odak Ara); detaydan Geri → arama (sorgu + odak korunur). Test: `node client/tests/search.js`.
- **Ekran klavyesi**: bir girdi odaktayken (Tizen'de her zaman) pencere yüksekliği azalırsa sahne ölçeği değişmez (son klavyesiz ölçü);
  klavye kapanınca normal ölçek.
- **Listem** (kontrol): 5 sütunlu ızgara, sanal pencere, `/api/row/mylist`.
- **Detay** (OK): iki bölmeli; İzle / Fragman / Sezonlar (‹ ›) / Listem. İzle / Fragman → oynatıcı (aşağıda).
- **Ayarlar**: Arka plan (Afişler (bölüme göre) · Görsellerim · Siyah; aşağıda), Yerleşim (Liste/Dock), Odak (Cam/Yazı),
  Malzeme (Yumuşak/Buzlu/Saydam; varsayılan Afişler · Liste · Cam · Buzlu), Görsel yükle (yalnız tarayıcıda, TV'de gizli), Profil, Sunucu (OK = adres ekranı; Geri = Ayarlar),
  Oynatıcı (yalnız Tizen: Otomatik = HTML5 `<video>` / AVPlay; `dzproto.engine`), Hata ayıklama (Kapalı/Özet/Ayrıntılı; aşağıda);
  sağda canlı küçük önizleme.

## Oynatıcı (`js/player.js`)

Eski istemcinin (git geçmişi) oynatıcı/akış/kaynak bulucu mantığı taşındı; `js/tracks.js`, `js/subs.js`,
`js/vendor/hls.min.js` oradan kopyalandı (subs: metin iç `<span>`'da; hls.js kökeni `THIRD_PARTY_NOTICES.md`). Görünüm yeni tasarım.
- Giriş: Detay > İzle (bölüm kartında o bölüm, yoksa `actions[]`'in devam/ilk bölümü; film) / Fragman (`kind=trailer`), Devam Et kartı
  (`primary_action`). `GET /api/streams` → kaldığı yerden (`resume_position`). Akış gelene kadar afiş + "Açılıyor · <ad>" perdesi.
- Motor `streams[].type`'a göre: varsayılan (TV dahil) `<video>` (eski istemcinin sahada doğrulanmış davranışı; Tizen doğal HLS oynatır);
  AVPlay yalnız Ayarlar > Oynatıcı = AVPlay seçiliyse ve `webapis.avplay` varsa (HLS dahil; `<object>` body'de, `html.av-active` saydam zincir;
  `destroy()`'da stop/close rapordan sonra `setTimeout 0`, yeni AVPlay açılırken bekleyen kapatma önce çalışır). `hls` + (doğal HLS yok ya da
  `preferHlsJs()`: MSE var, Safari/TV değil) → hls.js tembel, bir kez. `embed` → iframe (Geri çıkar).
- Başlama zamanlayıcısı: 20 sn (vekilli mp4 30 sn), ilk karede (`playing` / ilerleyen `timeupdate`, AVPlay ilk `oncurrentplaytime`) silinir;
  başlamadan tampon olayı gelirse bir kez +10 sn.
- Akış düşerse `POST /api/playback-report` (failure + `detail` ≤120, `hlsjs`, `variant_id`, `stream_url_host`, `stream_type`) ve döngüsel
  sıradaki akış: idx+1..n-1, sonra 0..idx-1 (elle seçilen akış düşerse önceden oynayan / denenmemiş akışa döner, kaldığı konumdan); her akış
  oturumda en çok bir kez düşmüş sayılır, "Denenecek akış kalmadı" yalnız hepsi düşünce. Takılma: oynatma başladıktan sonra tek bekleme >15 sn
  ya da son 60 sn'de toplam bekleme >30 sn → failure `stall` (detail süreler) + sıradaki akış (konumdan; Canlı TV'de de); Özet'te
  "✗ VidHide · takılma (bekleme 18 sn)". ≥10 sn oynayınca success. Hepsi düşerse son
  raporun yanıtı ≤2.5 sn beklenir: `finder` varsa "Kaynak aranıyor" (5 sn'de bir `/api/source-finder`, bulununca kendiliğinden yeniden),
  yoksa "Oynatılamadı" (Geri / Yeniden dene). Akışsız yanıtta da aynı.
- İlerleme `POST /api/progress`: 20 sn'de bir + duraklat / seek / bölüm geçişi / çıkış / bitiş (misafirde de çağrılır, sunucu yazmaz).
  Dizide son ~45 sn (bölüm ≥10 dk) sağ üstte "Sonraki bölüm" kartı (OK = geç, Geri = kal); bitince 5 sn geri sayım; film bitince çıkar.
- Katman (4 sn hareketsizlikte gizlenir): başlık + "S01 B03 · Ad" + akış etiketi; cam panelde ilerleme çubuğu + süre, ▶/❚❚, −10/+10, Altyazı,
  Ses (birden çok ses varsa), Kaynak (birden çok akış), Bölümler (dizide; ‹ › sezon). ↑↓ çubuk / düğme satırı, çubukta ←→ arama (basılı tut:
  adım büyür, 350 ms sonra uygulanır), OK oynat/duraklat; düğmede ←→ gez, OK seç. Katman gizliyken ←→ arar, OK duraklatır. Geri: açık panel /
  katman kapanır, katman gizliyse oynatıcıdan çıkılır (detaya, odak İzle; Devam Et'ten ana ekrana, şerit yenilenir).
- Altyazı: `subtitles[]` VTT (`/api/subtitles/<id>.vtt`), seçim modeli + profil tercihi `js/tracks.js` (Türkçe soft > Türkçe gömülü >
  İngilizce soft > Kapalı); indirilemezse sessizce Kapalı. Metin hafif koyu hap + gölge; katman açıkken panelin üstüne çıkar.
- **Canlı TV** (yanıtta `live:true`): ilerleme çubuğu / süre / ±10 / sonraki bölüm / `POST /api/progress` YOK; katmanda "CANLI" + `no · ad`,
  düğmeler ▶/❚❚ · Kaynak · Kanallar. ↑ = sonraki, ↓ = önceki kanal; Tizen ChannelUp/ChannelDown (masaüstü/Android PageUp/PageDown) aynı (sonraki/önceki);
  sıra kanalın açıldığı satır (gerekirse `/api/row` ile devamı, uçlarda sarar); hızlı basışta son seçim 400 ms sonra açılır, sağ üstte
  ~3 sn numara + ad bandı. Rakamlar = numaraya git (1.5 sn bekleme). Katman gizliyken OK = sağda Kanallar paneli (↑↓, OK aç, Geri kapat).
  Akış düşerse sıradaki (m3u8'lerden sonra YouTube); hepsi düşerse "Yayın yok" (Geri / Yeniden dene; finder yok), kanal tuşları yine
  çalışır. `embed` (YouTube) kanalda da tuşlar yakalanır (iframe odak alırsa sayfaya geri alınır). Geri: panel / katman, yoksa çıkış →
  bölüm ekranı, odak son oynatılan kanalın kartında.
- Oynarken `.is-play`: arka plan + ekran DOM'u gizli, afiş/Görsellerim dönmesi durur; çıkışta geri gelir.
- Test (başsız, sahte XHR/DOM/zaman; sunucu yok; kanal modu dahil): `node client/tests/player.js`.

## Hata ayıklama (`js/debug.js`)

Ayarlar > Hata ayıklama: **Kapalı / Özet / Ayrıntılı** (`dzproto.debug` = `summary` | `detail`, localStorage, try/catch; eski `'1'`
kaydı = Özet). Kapalıyken hiçbir şey çizilmez, istekler `debug=1` göndermez. Tek modül: `DZDebug.mode()`, `log()` (yalnız Ayrıntılı),
`sum(area, text, cls, key)` (yalnız Özet; `key` = satır yerinde güncellenir, ard arda aynı satır `×N`), `trace()` sunucu izini çevirir.

**Uzak log**: Özet/Ayrıntılı açıkken panel satırları ayrıca `POST /api/debug-log`'a gider (2 sn'de bir toplu, ≤50 satır/istek, kuyruk ≤200,
ağ hatasında sessizce atılır; `device` = model ya da platform + kalıcı kısa id `dzproto.devid`). Okuma: `GET /api/ops/debug-log[?device=&since=&limit=]`
(sunucu bellekte cihaz başına son 1000 satır; API.md "Uzak hata ayıklama logu").

**Özet** (TV'de takip için): sol altta küçük, sakin panel, en çok 8 satır, zaman damgası yok, sonuç rengi yalnız baştaki ikonda.
- Başlık: model · Tizen sürümü.
- Kaynak araması: provider başına TEK satır (en iyi sonuç): "✓ VidMolly · 1 akış", "✗ OK.ru · kaynak yok", "– Abyss · atlandı (engelli sunucu)";
  site sayfası / kaynak satırı yalnız hata/zaman aşımında. `info`/sayfa/tekrar/sıralama/önbellek adımları yok.
- Her akış denemesi TEK satır, sonuçta yerinde güncellenir: "▶ 1/3 OK.ru mp4 (vekil, paralel) … deneniyor" → "✓ 1/3 OK.ru · oynuyor ·
  ilk kare 4.2 sn" / "✗ 1/3 OK.ru · zaman aşımı 20 sn" / "✗ … · PLAYER_ERROR_…". Hatalar (sunucu, playback-report, akış kalmadı) kırmızı;
  kaynak bulucu yalnız başladı/sonuç. Tampon, izler, onevent, prepare ara adımları, playback-report gönderildi yalnız Ayrıntılı'da.
- Detay ekranında tek satır: "Detay 820 ms" (+ "· yükleniyor" hydrating iken).

**Ayrıntılı** (her olay):
- **Oynatıcı**: sol altta yarı saydam panel (son 12 satır, `+1.2 s` zaman damgalı, renk: yeşil başarı / kırmızı hata / sarı atlandı).
  İlk satır cihaz: model + firmware (`webapis.productinfo`), Tizen sürümü (`tizen.systeminfo`), AVPlay; tarayıcıda UA özeti.
  Sonra `/api/streams?debug=1` yanıtının `debug_trace`'i (API.md): "OK.ru (ok.ru) inceleniyor → kaynak yok · 840 ms",
  "VidMolly → atlandı (engelli sunucu)", "Akış 3/10 mp4 · vekil (ua) · paralel 6×1 MiB · vd1.okcdn.ru"; ardından canlı istemci olayları:
  "Akış 1/3 · … (mp4, AVPlay) deneniyor", AVPlay `open` / `prepareAsync` (süre, hata adı) / izler (`getTotalTrackInfo`) /
  `onbufferingstart`/`complete` (ms) / `onerror` / `onevent` / ilk kare (ms), hls.js manifest/uyarı/hata/kurtarma, `<video>` olayları,
  "→ zaman aşımı 20 sn" / "→ hata: <kod>" / "→ oynuyor", playback-report ve kaynak bulucu yoklaması (`/api/source-finder?debug=1`).
  Katmandan bağımsızdır (katman gizlense de görünür); Yeşil / G gizler-gösterir. Canlı TV'de de çalışır.
- **Detay**: sağ altta 3 satır: detay isteğinin süresi + `hydrating` durumu.

## Arka plan (`js/app.js` + `js/backgrounds.js`)

Ayarlar > Arka plan (`dzproto.look.bg`; kayıtlı eski mod — gün döngüsü sahnesi, sabit görsel — Afişler'e çekilir):
- **Afişler (bölüme göre)** (varsayılan): vitrin öğesinin 1280x720 backdrop'u (`/img` vekili) tam ekran; yazı görselle eşzamanlı,
  opacity crossfade (bölüm değişiminde 400 ms, vitrin döngüsünde 1 sn); bölüm odağı → 100 ms (görsel hazırsa) / 150 ms sonra.
  Açılışta her bölümün ilk 2 görseli ön-yüklenir (`img.decode()`); her bölümün İLK görseli kalıcı tutulur (son 8 Image sınırından
  muaf). Hazır değilse aynı görselin 320x180 kopyası önce gösterilir, büyüğü gelince üstüne geçer. Cam paneller için canvas
  ön-bulanık kopya geçişi bekletmez: boşta üretilir, gelince cam yumuşakça güncellenir. Görsel yoksa (Canlı TV kanalları, DVR, hata):
  Görsellerim'den biri, o da yoksa koyu gradyan.
- **Görsellerim**: sunucuya yüklenen görseller (`GET /api/backgrounds`, API.md "Arka planlar"); karışık sıra, ~60 sn'de bir 2 sn
  crossfade. Cam paneller sunucunun `blur_url` kopyasını kullanır (canlı blur yok). Boşsa koyu gradyan + Ayarlar notu
  "Görselleri yönetim panelinden yükle (http://<sunucu>/admin → Arka planlar)".
- **Siyah**: düz koyu zemin.

Liste açılışta, sunucu değişince, Ayarlar açılınca ve 10 dk'da bir yenilenir (ETag). Tarayıcıda (Tizen değil) Ayarlar >
"Görsel yükle": dosya seçici (jpg/png/webp, çoklu) → sırayla `POST /api/backgrounds` (multipart `file`) → liste yenilenir.

## Marka (`img/`)

Logo dosyaları eski istemci/Android ile aynı (`logo-wide.png`, `logo-square.png`, `mascot.png`, `mascot-sm.png`, `favicon.png`):
Sunucu adresi + Profil seçimi ekranında ortada üstte geniş logo; açılış/yüklemede ortada kare logo; ana ekranda sol üstte küçük
maskot (Dock'ta saat/selamlama sağa kayar); oynatıcı "Açılıyor" perdesinde afiş yoksa maskot; favicon `index.html`.

## Tasarım kaynağı ve lisans

Tasarım: MagicPath "Google TV Home — C · Clean Frame" + CleanFrame (MIT, Nathan Clark). Ölçüler (1920 sahne, sol 140,
liste 236/128/6 görünür, dock 208/676/1504x232, saat 48px, kontroller 64px hap), renkler (`#F6F1E7`, cam tonları),
geçişler (`cubic-bezier(.2,.8,.2,1)`, 140–320 ms, ekran geçişi opacity+scale 1.01), odak davranışı (List: ↑↓ gez, → kontroller;
Dock: ←→, ↑ kontroller; Geri döner) tasarımdan alındı; kod ES5 olarak yeniden yazıldı. Ayrıntı: `THIRD_PARTY_NOTICES.md`.
