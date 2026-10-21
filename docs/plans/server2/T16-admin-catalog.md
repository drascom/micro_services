# T16: Admin Katalog ekranı + panel içi oynatma

**Bağımlı:** T03, T04a, T04b, T10 (T09 sonrası anlamlı: kaynak satırları o zaman dolar). **Açar:** yok. **Oku:** README K5, §5.8;
`inventory/02 §4` (`video_sources` yaşam döngüsü, tanı fonksiyonları); `server/app/static/admin/library.js` ve
`server/app/routers/ops_library.py` (eski Kütüphane ekranı, fikir için); `tizen-client/js/vendor/hls.min.js` (+ LICENSE).

## Amaç
Kullanıcı, tarama ve çözümlemenin çalıştığını **istemciyi açmadan** admin panelinden doğrulayabilmeli: bulunan medyayı gezmek, sezon/bölüm
ayrıntısını ve bulunan video kaynaklarını görmek, kaynağı panelin içinde oynatmak.

## Yapılacaklar
1. **Katalog listesi** (`#/catalog`, `static/js/catalog.js`): afişli ızgara ya da tablo (geçiş düğmesi). Süzgeçler: site, tür (film/dizi),
   durum (`görünür / kaynak bekliyor / halka kapalı / kaynakları bozuk`), metin araması, sıralama (eklenme, ad). Her öğede: afiş, ad, yıl,
   tür, geldiği site(ler), kaynak özeti (`12 bölüm · 10 sağlam · 2 bilinmiyor`), geldiği yer (`source_origins`: bölüm/liste adı), TMDB eşleşti mi.
   **Görünmeyen başlıklar da listelenir** (K5 istemci içindir; admin her şeyi görür) ve neden görünmediği yazılır.
   Sayfalama sunucu tarafında (50'şer).
2. **Başlık ayrıntısı** (`#/catalog/<id>`): üstte meta (ad, yıl, türler, özet, afiş/arka plan, TMDB kimliği, kanonik kimlik, kaynak site
   kayıtları ve her birinin adresi: bağlantı), alanların hangi kaynaktan geldiği (`field_provenance`).
   - Dizi: sezon sekmeleri -> bölüm tablosu (no, ad, yayın tarihi, kaynak sayısı, sağlık rozeti). Bölüm satırı açılınca kaynak listesi.
   - Film: doğrudan kaynak listesi.
   - **Kaynak satırı:** site, bölüm sayfası adresi (bağlantı), durum (`unknown/healthy/suspect/broken/blocked/disabled`), son hata, son
     kontrol, son tanı (`public_diagnosis`), vekil gerekiyor mu ve neden. Eylemler: `[çöz]`, `[oynat]`, `[yeniden çöz (önbelleksiz)]`,
     `[devre dışı bırak / etkinleştir]`.
3. **Çöz** (`POST /api/control/catalog/sources/{sid}/resolve {force}`): `core.play.follow` + çözümleme; yanıt adım adım iz:
   sayfa getirildi mi -> hangi resolver hangi adayı buldu (aday adresi, host) -> hangi provider çözdü -> akışlar. Her akış için:
   tür (`hls/mp4/embed`), kalite/çözünürlük, etiket, vekilden mi geçiyor, **yoklama** (`streamprobe.summarize`: HTTP kodu, süre dk,
   varyant sayısı, şifreli mi, yanıt süresi ms), altyazı ve ses izleri listesi (dil, etiket). Hata varsa hangi adımda kaldığı vurgulanır
   (`page / discover / provider / stream`). Bu çağrı kaynak sağlığını DEĞİŞTİRMEZ ve sorun kaydı AÇMAZ (inceleme amaçlı; `events.emit` yok).
4. **Panel içi oynatıcı** (`static/js/player.js`, modal): `<video>` + `hls.min.js` (istemciden `app/admin/static/vendor/`'a KOPYA, lisans
   dosyasıyla; CDN yok, LAN kapalı çalışmalı). Motor seçimi akışın `type` alanına göre (uzantıya göre değil): `hls` -> hls.js (tarayıcı
   yerel HLS destekliyorsa yerel), `mp4` -> yerel, `embed` -> `<iframe>` (sandbox öznitelikli). Vekil gereken akış `/api/stream-proxy/<token>`
   üzerinden. Arayüz: akış/kalite seçici (birden çok akış ve hls.js seviyeleri), altyazı seçici (sunucunun döndürdüğü altyazılar `<track>`),
   ses izi seçici (hls.js `audioTracks`), oynatma hızı, hata olursa hls.js hata türü + ayrıntısı ekranda. Oynatıcı altında canlı bilgi satırı:
   çözünürlük, bit hızı, tampon, motor.
   Admin oynatması istemci telemetrisini KİRLETMEZ: `progress` yazılmaz, `playback-report` gönderilmez, izleyici/sorun oluşmaz. Akışlar
   `GET /api/control/catalog/streams/{id}?episode=` ucundan alınır (istemci `/api/streams` ucu değil; aynı çözümleme, yan etkisiz).
   Altında tek düğme: `[bu kaynak çalışmıyor: sorun olarak kaydet]` -> `admin` aktörlü sorun (T13 varsa; yoksa düğme gizli).
5. **Site sayfasından geçiş:** Site sayfasına "Katalog" bağlantısı (o site süzgeciyle) ve Tarama sekmesindeki sayılar (aktarılan, halka
   kapalı, başarısız) ilgili süzgeçle Katalog'a bağlanır. Aşama kartlarındaki örnek adresler için de `[bu örneği çöz]` (A5/A6 kartlarında).
6. Uçlar (`routes_catalog.py`): `GET /api/control/catalog?site=&type=&status=&q=&sort=&page=`, `GET /api/control/catalog/{id}` (meta +
   sezonlar + bölümler + kaynaklar), `GET /api/control/catalog/streams/{id}?episode=&source=`, `POST .../sources/{sid}/resolve`,
   `POST .../sources/{sid}/disable|enable`, `POST /api/control/catalog/{id}/tmdb-refresh`, `DELETE /api/control/catalog/{id}?site=`
   (o sitenin kaydını kaldır; kullanıcı verisi kalır). Üst şerit menüsüne "Katalog" eklenir.

## Kapsam dışı
İstemci arayüzü, kategori/ana ekran düzenleme, kimlik birleştirme ekranı, TMDB toplu doldurma ekranı.

## Kabul
- Liste ucu: süzgeçler ve sayfalama doğru; görünmeyen başlık `status` nedeni ile döner (kaynaksız, halka kapalı, tümü bozuk ayrı ayrı).
- Ayrıntı ucu: fixture dizisi için sezon -> bölüm -> kaynak ağacı eksiksiz; `source_origins` ve `field_provenance` yanıtta.
- `resolve`: sahte provider ile iz adımları ve akış özellikleri (kalite, altyazı listesi, yoklama alanları) döner; aday olmayan sayfada
  `stage="discover"`; çağrı öncesi ve sonrası `video_sources.status/failures` aynı; `problems` tablosu boş kalır.
- Admin akış ucu `playback_attempts` belirteci üretmez, `progress` yazmaz, olay yaymaz (abone testte 0 olay görür).
- Vekil gereken akış admin ucunda vekil adresiyle döner ve imzalı belirteç doğrulanır.
- `hls.min.js` panelde yerelden sunulur (`/admin/vendor/hls.min.js` 200), lisans dosyası yanında; sayfada dış alan adına istek yok
  (`index.html` ve js içinde `http(s)://` ile başlayan kaynak adresi taraması).
- `embed` iframe'i `sandbox` özniteliği taşır.
- `node --check` temiz.

## Elle kontrol (kullanıcı)
Bir dizi açıp bölümü panelde oynatma; kalite/altyazı/ses seçimi; bozuk kaynakta hatanın hangi adımda olduğunun okunabilirliği; görünmeyen
başlıkların nedenlerinin anlaşılırlığı.
