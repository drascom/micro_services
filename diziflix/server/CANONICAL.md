# Tek katalog ve video kaynakları

Katalog öğesi bir siteyi temsil etmez. `library_items` filmin/dizinin kalıcı uygulama
kimliğini, `external_ids` ise tür ile birlikte TMDB/IMDb kimliklerini tutar. TMDB film
ve TV kimlikleri ayrı ad alanlarıdır. Scraper kayıtları `source_items`, oynatılabilir
sağlayıcılar `video_sources` içinde tutulur.

Kimlik eşleşmesi önceliklidir. Kimlik yoksa tek ve çelişkisiz ad/yıl/tür eşleşmesi
kullanılır. Belirsiz veya kimliksiz kayıtlar `identity_reviews` üzerinden görünür.
Sonradan doğrulanan ortak kimlikler birleştirilirken listeler, ilerleme ve video
kaynakları taşınır; eski uygulama kimlikleri `catalogue_aliases` ile çalışmaya devam
eder. İki farklı harici kimlik sessizce ezilmez. Dashboard'da doğrulanmış kimlik
bağlamak da aynı birleştirme kurallarını uygular.

TMDB araması için `.env` içinde `TMDB_TOKEN` veya `TMDB_API_KEY` gerekir. Anahtar
olmadan kimlikler scraper verisi veya yönetici girişiyle bağlanabilir; katalog
çalışmaya devam eder. Canlı sunucuda geçiş anında anahtar tanımlı değildi; mevcut
439 kayda TMDB kimliği atanmış gibi davranılmaz.

Resmî uç noktalar: [kimlikle arama](https://developer.themoviedb.org/reference/find-by-id),
[film harici kimlikleri](https://developer.themoviedb.org/reference/movie-external-ids),
[dizi harici kimlikleri](https://developer.themoviedb.org/reference/tv-series-external-ids).
Arama sonuçlarının ilki körlemesine kabul edilmez; tam ad, yıl ve tek sonuç gerekir.

## Scraper sözleşmesi

Normalizasyon `tmdb_id`, `imdb_id` ve `video_sources` alanlarını taşır. Örnek:

```json
{"title":"Örnek", "year":2026, "tmdb_id":123,
 "video_sources":[{"key":"tr-dub", "url":"https://example.org/video.mp4",
 "kind":"movie", "resolver":"direct", "type":"mp4", "language":"tr", "label":"Türkçe"}]}
```

`kind`: `movie`, `episode`, `trailer`. Bölümler ayrıca `season` ve `episode`
sayılarını içerir. `resolver`: `direct`, `embed`, `page`. `page` kaynağı önce
`app/scraper/site_extractors/<site>.py` ile sayfadaki gerçek player/handoff
adresini bulur; sonra `app/scraper/providers/` ortak kayıt defteri, adresin
sağlayıcısına (ör. VidMolly) göre gerçek medya adresini çözer. Site extractor'ı
yalnızca kendi HTML'ini tanır; sağlayıcı çözümleyicisi hiçbir katalog sitesini
tanımaz. Bilinmeyen sağlayıcılar eski `detail_fields` / `stream_resolver`
tarifine geri düşer. Süresi dolabilecek medya URL'si yerine kalıcı sayfa adresini
ve `page` çözümleyicisini tercih edin. Sabit `key` URL yenilense de sağlayıcı
geçmişini korur. Yeni kaynak, var olan türlerden birini ürettiğinde TV kodunun
değişmesi gerekmez.

Sinemalar mevcut fragman sağlayıcısı olarak taşınır. YabancıDizi'nin katalog
sayfası `#video-area` altındaki kendi handoff iframe'ini bulur; ortak zincir
çözümleyicisi bunun ardından VidMolly gibi sağlayıcıları çözer. Handoff veya
sağlayıcı çalışmazsa kayıt oynatılabilir diye işaretlenmez; sağlık izleme bunu
ayrı kaynak bazında takip eder.

## Oynatma ve sağlık

`GET /api/streams/{id}` sözleşmesi korunur. Her akışa `source_id`, `source`, `kind`
ve bir günlük `attempt_token` eklenir. Aynı sağlayıcının kalite seçenekleri bir
belirteç paylaşır. Tam film kaynakları varsa fragmanlar yedek olarak sunulmaz.
Bölüm kaynakları yalnızca ilgili bölümün isteğine döner.

`POST /api/playback-report`:

```json
{"attempt_token":"sunucunun-verdiği-32-karakter", "event":"failure", "code":"network", "engine":"html5"}
```

İlk başarısız denemede `suspect`, üç ayrı denemede `broken` olur. Aynı denemenin
tekrar gönderilmesi sayacı artırmaz. Bir kaynağın tüm akışları tek `attempt_token`
paylaşır; istemci hatadan sonra sıradaki akışı deneyip o çalışırsa aynı token ile `success` raporlar:
bu, aynı denemenin sayılmış hatasını geri alır (`healthy`, sayaç düşer; daha yeni bir denemenin
hatası varsa yalnızca bu denemenin katkısı geri alınır, o hata ezilmez). Cihazın bildirdiği açık codec/otomatik oynatma/
iptal/çevrimdışı hataları kaynağı kırık yapmaz. Önbellekteki çözülmüş adres hatada
silinir. Başarılı oynatma yaklaşık 10 saniye ilerleme gözlendikten sonra bildirilir.
Başarılı adres çözümlemesi oynatma başarısı sayılmaz. Devre dışı kaynakları yeni
scraper taraması veya gecikmiş oynatma bildirimi yeniden açamaz.

TV oynatıcısı hatada diğer akışı dener; yukarı tuşu kaynak/kalite menüsünü açar,
kırmızı tuş çalışmayan kaynağı bildirir. Çapraz alan iframe'ler gerçek oynatma
olaylarını paylaşmadığından otomatik başarı tespiti yapılmaz; `Kaynak çalışmıyor`
düğmesi veya kırmızı tuş kullanılır. Kaynaklar kalıcı olarak silinmez; dashboard'dan
devre dışı bırakılabilir, etkinleştirilebilir veya adresleri yeniden çözülebilir.

Bu geçiş için TV paketi bir kez güncellenmelidir: yeni hata bildirimi eski
istemcide yoktur. Sonraki scraper eklemeleri bu ortak sözleşme içinde client
sürümü gerektirmez.

Doğrulama: `venv/bin/python -m unittest discover -s tests -q` (server klasöründe),
`node tests/player_health.js` (proje kökünde).

## Catalogue enrichment verification (2026-09-05)

All scrapers feed `source_items` and the shared identity/merge pipeline. A source
list only supplies records it actually discovers; it does not automatically search
other sites for every missing field. Sinemalar now also scans its newest-films list
and configurable `detail_pages`. These detail pages use the same schema,
normalizer, identity resolution, provenance and video-provider registration.

Verified live: Drama, Mayday, Jester 2, Cesur Yaga ve Sihirli Dünya and Uzaylı Dostum
Flik retain their existing Yabancı Dizi catalogue IDs while receiving Sinemalar
synopses and trailer providers. Client detail already reads these shared fields.
Koşucu, McVeigh and Valhalla still require verified matching/discovery. Missing
TMDB credentials mean this is currently the conservative unique title/year/type
fallback, not verified TMDB identification. Configured detail URLs are discovery
seeds, not manual canonical-ID bindings or a general automatic site search.

Description policy: every matched title prefers a nonempty Sinemalar description.
Without one, use the earliest discovering scraper with a nonempty description.
Blank fields never clear existing text. Trailer options prefer usable Sinemalar
providers, with other providers retained as fallbacks; movie/episode ordering is
unchanged. Client reads the merged description and the common trailer endpoint.
