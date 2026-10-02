# Scraper → katalog → TV sözleşmesi v1

Durum: hedef sözleşme. Bu belge henüz ingest girişinde uygulanan bir JSON
şeması değildir. Mevcut düz normalizasyon alanları aşağıdaki kurallara göre
adım adım uyarlanacaktır.

## 1. Veri sahipliği

| Varlık | Kimlik | Sahip olduğu alanlar |
|---|---|---|
| Yapım | Kalıcı uygulama ID; tür + TMDB/IMDb kimliği | Başlıklar, yıl, açıklama, türler, afiş, oyuncular, süre |
| Bölüm | Dizi ID + sezon + bölüm; varsa harici bölüm ID | Bölüm adı, açıklama, yayın tarihi, süre, görsel |
| Video sağlayıcısı | Kaynak site + sabit içerik anahtarı + sabit video anahtarı | Film/bölüm/fragman hedefi, kalıcı sayfa, çözümleme tarifi, dil |
| Akış sürümü | Sağlayıcı ID + sürüm anahtarı | Geçici URL, biçim, kalite, geçerlilik süresi |
| Kaynak listesi | Kaynak site + liste anahtarı | Kaynak içi sıralama ve üyelik; ortak katalog popülerliği değildir |
| Profil durumu | Profil ID + film/bölüm ID | İlerleme, izlendi, Listem; scraper yazamaz |

TMDB film ve dizi sayıları ayrı ad alanlarıdır. IMDb `tt...` yapım kimliği
bölümün IMDb kimliğiyle karıştırılmaz. Canonical ID scraper tarafından üretilmez.
Harici kimlik yoksa ad/yıl/tür tek ve çelişkisiz eşleşme sağlayabilir; yıl yokluğu,
çelişki veya çoklu aday inceleme gerektirir. Kimlik merge işlemi alias, profil
ilerlemesi, listeler ve bölüm kaynaklarıyla tek işlemde yapılır.

## 2. Alan bağlama tablosu

| Scraper alanı | Hedef / kural |
|---|---|
| `source`, `source_key`, `source_url` | Kaynağın kalıcı kaydı; TV kategori adı olmaz |
| `tmdb_id`, `imdb_id`, `type` | Yapım eşleştirme; tür doğrulanır |
| `title`, `original_title`, `year`, `overview` | Yapım metadata'sı; alanın kaynağı ve zamanı kaydedilir |
| `genres[]` | Merkezi tür ID'lerine map; bilinmeyen değer inceleme, ülke/format ayrı tutulur |
| `cast[]` / oyuncu fotoğrafları | İsim listesi `library_items.cast`; kaynağın oyuncu fotoğraf URL'leri yalnızca `normalized.cast_photos`'ta saklanır (API'de yok; fotoğraf/karakter için asıl kaynak sonraki iş: TMDB credits) |
| `poster_url`, `backdrop_url` | Yapım görselleri; server proxy/cache; video yokluğunu gizlemez |
| `ratings[]` | Puan, ölçek ve sağlayıcı beraber; site puanını IMDb puanı diye sunma |
| `runtime_minutes` | Yapım/bölüm metadata süresi, dakika |
| `episodes[].season`, `.episode` | İlgili dizinin tek bölümü; URL'den doğrulanamayan numara tahmin edilmez |
| `episodes[]` listesi (dizi sayfası envanteri) | Bölüm listesi kart değil dizi sayfasından gelir (`library/series_crawl.py`): kart yalnızca son bölümü verir. Yayınlanmamış (duyurulmuş) bölüm yazılmaz; video çözümlenmez, yalnızca bölüm sayfası URL'si (`resolver=page`); yokluk silme kanıtı değildir (envanter yalnızca eklenir/güncellenir) |
| `episodes[].air_date` (kaynak) | Kaynağın Türkçe tarihi ("24 Temmuz 2026") ISO `YYYY-MM-DD`'e çevrilir (`parse.turkish_date`, yaml `cast: date_tr`); `video_sources.episode_air_date`; TMDB tarihi yalnızca boşsa kullanılır |
| `episodes[].title`, `.overview`, `.air_date`, `.still_url` | Bölüm metadata kaydı; dizi metadata'sını ezmez. TMDB kaynağı `library_episodes`'a yazılır; kaynak değeri doluysa kaynak, boşsa TMDB (görselde TMDB öncelikli, kaynak yedek) |
| `seasons[].name`, `.overview`, `.air_date`, `.poster_url` | Sezon metadata kaydı (`library_seasons`, TMDB); yalnızca kütüphanede bulunan sezonlar için |
| Yapım `video_sources[]` | Film veya dizi fragmanı; diziye tam film videosu bağlanamaz |
| Bölüm `video_sources[]` | Yalnızca bu bölümün videosu; dizi geneline bağlanamaz |
| `collections[]` | Kaynak listesi üyeliği; Ana Sayfa satırına otomatik dönüşmez |
| HTTP/parse/erişim hatası | Tarama raporu; mevcut filmi veya kaynağı silmez |
| Oynatıcı başarı/hatası | Deneme belirteci üzerinden sağlayıcı sağlık kaydı |

Null = bilinmiyor. Eksik değer önceki dolu alanı silmez. Alanı temizlemek ayrı,
izlenen bir yönetici düzeltmesidir. `ratings[]` bulunmadığında puan gösterilmez;
0/10 ile bilinmiyor aynı değildir.

## 3. Sağlayıcı ve bölüm kuralları

`video_sources[].key` zorunlu ve sabittir. İmzalı/geçici URL sağlayıcı kimliğinin
parçası olmaz. Bir kaynak sağlayıcısının kalite sürümleri çözümlemede döner;
720p/1080p için iki ayrı sağlık kaydı yaratılmaz. Dil ses dili ve altyazı dili
olarak ayrılır; dublaj/altyazı bilgisi bilinmiyorsa tahmin edilmez.

- `kind=movie`: yalnızca film kaydı altında.
- `kind=trailer`: film veya dizi kaydı altında; izleme ilerlemesinden ayrı.
- `kind=episode`: yalnızca bölüm altında. Canonical bölüm ID'si ingest tarafından atanır.
- `resolver=page`: kalıcı içerik sayfası; siteye ait parser/çözümleyici kullanılır.
- `resolver=direct`: bilinen medya adresi; `expires_at` verilmişse zamanı geçmiş adres oynatılmaz.
- `resolver=embed`: yabancı oynatıcı; otomatik başlangıç/başarı gözlemi garanti edilmez.

Tek bir scraper kaydı bu varlıkların hepsini doldurmak zorunda değildir. Metadata
scraper'ı geçerli bir kayıttır. Dizi kaydı bölüm listesi olmadan saklanabilir. Bölüm
metadata'sı video olmadan saklanabilir. Film/bölüm varlığı video kaynağının varlığına
bağlı değildir. Aynı kaynak taramada görünmedi diye silinmez; scraper taramasının
kapsamı tam olmadığı sürece yokluk, kaldırılma kanıtı değildir.

## 4. Merkezi türler ve tarihler

Başlangıç tür kimlikleri: `action`, `comedy`, `drama`, `horror`, `science_fiction`,
`thriller`, `romance`, `animation`, `documentary`, `adventure`, `crime`, `fantasy`,
`family`, `war`, `history`, `music`, `western`, `mystery`, `biography`, `sport`,
`youth`, `children`. Etiketler Türkçedir; UI kimliğe göre çalışır. Yeni tür server
sözlüğüne eklenebilir; client'a hardcoded liste gömülmez. Anime bir site adından
üretilmez; merkezde tür/biçim kararı verilmeden Animasyon'a körlemesine eşlenmez.

| Tarih | Anlam |
|---|---|
| `first_seen_at` | Kataloğa ilk ekleniş; tekrar tarama değiştirmez |
| `first_available_at` | İlk tam video kaynağının kabulü; tekrar tarama/URL yenileme değiştirmez |
| `updated_at` | Metadata'nın son değişimi |
| `fetched_at` | Kaynağın son gözlemi; yeni film/bölüm tarihi değildir |
| `air_date` / yıl | Gerçek yayın tarihi; bulunamazsa null |

Yeni Eklenenler `first_available_at` kullanır. Yeni bir site mevcut filme yeni
sağlayıcı ekleyince filmi yeni çıkmış gibi yukarı taşımaz. Kaynağı kaldırılmış bir
filmin geri gelmesi ilk tarihi sıfırlamaz; gerekirse ileride ayrı "Yeniden mevcut"
listesi tanımlanabilir.

## 5. TV sunum sözleşmesi

Mevcut `/api/boot`, `/api/row/{id}`, `/api/detail/{id}`, `/api/streams/{id}` kalır.
Ek alanlar geriye uyumludur. Yeni client `capabilities` ile ekran desteğini öğrenir;
eski client bilmediği alanları yok sayabilir. `source` eski istemci uyumluluğu için
kalabilir; yeni istemci göndermez.

Hedef ek uçlar: `/api/catalog?type=movie|series&genre=&year=&availability=&sort=&offset=&limit=`
ve `/api/genres?type=...`. Mevcut aramaya aynı tip/izlenebilirlik filtreleri eklenir.
`/api/catalog` ve `/api/genres` ilk client uyarlamasında eklendi. Diğer ekler hedef davranıştır.

Kartta `id` her zaman yapım ID'sidir; yeni bölüm kartı için `episode_id` ve benzersiz
`card_key` eklenir. `card_kind=title|episode`; mevcut `type=movie|series` anlamı
korunur. Bir dizinin iki bölüm kartını render ederken anahtar olarak sadece `id`
kullanılmaz. Bölüm kartının varsayılan eylemi bölüm hedefini açar, dizi ID'sini
oynatılacak bölüm gibi kullanmaz.

Ek alanlar:

```json
{
  "id": "kalici-dizi-id",
  "type": "series",
  "card_kind": "episode",
  "card_key": "episode:kalici-bolum-id",
  "episode_id": "kalici-bolum-id",
  "episode_label": "S01 B03 · Fırtına",
  "availability": {
    "state": "ready",
    "reason": null,
    "has_trailer": true
  },
  "primary_action": {
    "kind": "play_episode",
    "item_id": "kalici-dizi-id",
    "episode_id": "kalici-bolum-id"
  }
}
```

**Uygulandı (sunucu, geriye uyumlu, ek alanlar):** her kartta `card_kind` (`title` varsayılan) ve `card_key` (`title:<id>` /
`episode:<episode_id>`); bölüm kartında ayrıca `episode_id`, `episode_label` (`S04 B10 · Başlık`, özel bölüm `Özel B02 · Başlık`,
genel "10. Bölüm" adı tekrarlanmaz), `season`, `episode`, `still_url`/`has_still`, HEDEF BÖLÜMÜN `availability`'si ve
`primary_action` (`play_episode` | `resume_episode`, `resume_*` `position` saniye taşır). Bölüm kartı olan satırlar: `continue`
(dizi için; `progress` eskisi gibi kalır, film başlık kartı) ve `new_episodes` (kaynağın yeni-bölümler listesindeki her dizinin en
yeni yayınlanmış `ready` bölümü, dizi başına tek kart, kaynak sırası; son bölümü kaynaksız/bozuk ya da bölümü olmayan dizi yok).
`latest_episodes` satır kimliği takma ad, `new_movies` = `movies`, genel `new` satırı legacy yerleşimde kalır. Ayrıntı ve
örnekler: `API.md`, `docs/api-samples/`, doğrulama: `server/tests/test_client_contract.py`.

*Ana ekran düzeni (ek not):* `tv-v1` ana ekranı artık `server/app/homelayout.py` belirler: slider (3 dizi + 3 film) ->
`continue` -> `trending_series` -> `series` -> `trending_movies` -> `noteworthy_movies` -> `movies` -> `mylist`. `new_episodes`/`new_series`/`trending`
ana ekrandan çıktı (`/api/row/{id}` çalışır); site `trending`/`latest_*`/`noteworthy_movies`/`featured` listeleri satır değil sıralama
sinyalidir (`trend_score`). Sözleşmedeki alanlar aynı; yalnız satır kimlikleri/başlıkları ve `heroes` içeriği değişti (`API.md`).

`availability.state`: `ready` (en az bir etkin healthy/unknown tam kaynak),
`check_required` (yalnız suspect tam kaynak), `unavailable` (tam kaynak yok veya
hepsi broken/disabled). `reason`: `no_video_source` veya `sources_unavailable`;
diğer durumlarda null. Fragman bu hesabı değiştirmez; `has_trailer` ayrıdır.

`availability.trailer` (ek, yalnızca detay yanıtında ve yalnızca fragman kaynağı olan yapımlarda; uygulandı):
`ok` | `dead` | `unknown`. Sunucu detay açılırken YouTube fragmanını oEmbed ile doğrular
(`server/app/library/trailer_check.py`); tüm fragman kaynakları ölüyse (silinmiş/özel/gömme kapalı) `dead` olur,
`has_trailer=false` yapılır ve yalnızca fragmanı olan yapımda `playback=trailer` → `unavailable`. `unknown` = doğrulanamadı
(YouTube dışı host, geçici hata, süre bütçesi); fragman sağlam sayılır ve `has_trailer` değişmez. Ölü fragman
`video_sources.trailer_dead` bayrağıyla işaretlenir; bu oynatma sağlığı (`status`/`failures`) DEĞİLDİR ve admin K/T
sayımına girmez. `GET /api/streams/{id}?kind=trailer` ölü fragman için boş liste döndürür.
Dizi kartı için en az bir bölümün durumu kullanılır; bölüm kartı/Devam Et için
yalnız hedef bölüm hesaplanır. `ready` garanti edilmiş kesintisiz oynatma değildir.

Detay yanıtı hedefli eylemler döndürür: `play_movie`, `resume_movie`, `play_episode`,
`resume_episode`, `play_trailer`, `open_catalog`. **Uygulandı:** detayda `actions[]` (ilk beşi; `open_catalog` yalnızca ana sayfa
odak alanı içindir, detayda üretilmez); dizide hedef = bitmemiş bölüm, yoksa ilk izlenebilir bölüm, kaynağı olmayan yapıma oynatma
eylemi eklenmez. Hedef sağlayıcı seçimi server'da
kalır. Fragman isteği için mevcut `/api/streams/{id}` uç noktasına açık
`kind=trailer` desteği ilk client uyarlamasında eklendi; mevcut otomatik seçim fragman düğmesini tek
başına desteklemeye yetmez. Film isteği fragmana kendiliğinden dönmez.

TV istemcisi davranışı (uygulandı): bölüm kartı (`card_kind=episode`) ve Devam Et kartı ayrı bir tek-bölümlük
sayfa açmaz; dizinin normal özet sayfası (`id` = yapım ID'si) açılır, `episode_id` (Devam Et'te `progress.episode_id`)
ile ilgili sezon seçili ve ilgili bölüm odaklı gelir; kart odak kimliği `card_key`'dir. Oynatma tek akıştır: tam ekran
yükleme modalı açılır, akışlar sırayla ekran dışı denenir, akış başına ayrı `attempt_token` ile hata (prepare hatası /
`onerror` / ~15 sn zaman aşımı) ve başarı (oynatma gerçekten başladığında) bildirilir; player ekranı yalnızca akış
oynamaya başladıktan sonra açılır. Dizi sayfasında sezon posteri yalnızca `has_poster=true` iken gösterilir, bölüm
görseli `still_url` (`has_still=false` iken yerel yer tutucu), yayın tarihi gelecekte ve kaynak `ready` değilse
"Yakında" işareti `air_date` + `availability.state`'ten çıkarılır. Ayrıntı: `tizen-client/README.md`.

**TV istemcisi: detay eylemleri, bölüm kartları, sonraki bölüm, kalite (uygulandı, `tizen-client/README.md`).**
- Detay düğmeleri `actions[]`'ten: `resume_movie`/`play_movie` → Devam Et / Oynat (`check_required`: Yeniden Dene),
  `resume_episode`/`play_episode` → "Devam Et · S04 B02" / "Oynat · S01 B01" (+ resume ise hedef ilk oynatılabilir bölüm değilse
  "İlk Bölümden Başla"), `play_trailer` → Fragmanı Oynat; `actions` alanı hiç yoksa eski alanlara (`resume`, `progress`, `playback`,
  `availability`) düşer, `actions: []` = oynatma düğmesi yok. Ölü fragman (`availability.trailer='dead'`, ya da `has_trailer=false`
  iken `availability.trailer` dolu) gizlenmez: gri/devre dışı "Fragman yok" (odak alır, Enter bilgi toast'ı, oynatma denenmez);
  hiç fragman kaydı yoksa düğme çizilmez.
- Bölüm kartı (`card_kind=episode`): yatay kart; görsel `still_url` (`has_still=false` ise dizi görseli: gerçek yatay afiş varsa `card`,
  yoksa `portrait`; üretilmiş still yer tutucusu indirilmez), altında dizi adı + `episode_label`, kartta `progress.pct` çubuğu,
  odak kimliği `card_key`. Başlık kartları poster kalır.
- Sonraki bölüm ARDIŞIK bölümdür (aynı sezonun sonraki bölümü, sezon sonunda sonraki normal sezonun ilki; özel bölümler kendi
  aralarında); kaynağı yok/yayınlanmamış ise teklif yoktur, sessizce atlanmaz. Bölümün son ~45 sn'sinde ve bitince gösterilir
  (istemci `detail.seasons` sırasından hesaplar; `progress.next_episode` yalnızca ilk kez izlendi işaretlenirken döner, hedef olarak kullanılmaz).
- `streams[].quality`/`label` cözünürlüğü (`1440p`, `1920x1080`, `4K`) ayrıştırılabiliyorsa istemcideki "En yüksek kalite" tercihi
  (1080p varsayılan / 1440p / otomatik) tercihi aşan doğrudan akışları doğrudan akışların sonuna atar; ayrıştırılamıyorsa
  (`auto`, HLS) akışa dokunmaz. Dil/altyazı tercihi (`dz_pref_sub_<profil>`) bundan sonra uygulanır. Sözleşme alanı eklenmedi.

**Ses / altyazı izleri (uygulandı, `/api/streams` ek alanları; ayrıntı `API.md`).** Dil bilgisi uydurulmaz: yalnızca
kaynağın söylediği şey yazılır. Sağlayıcı dosyası = *varyant* (`streams[].variant_id`); her akışta `audio_lang`,
`sub_mode` (`hard` gömülü | `soft` ayrı iz | `none`), `hard_lang`; kökte `subtitles[]` (yalnızca soft izler,
`/api/subtitles/<id>.vtt` sunucu vekili, sağlayıcı adresi gizli, `stream_ids` ile hangi varyantlarda geçerli) ve `audio[]`.
VidMolly gömme sayfasının JW Player `tracks` bloğu okunur (`thumbnails` yok sayılır); `RESOLVER_VERSION` 5. yabancidizi'de
Türkçe sekme dosyası altyazısı görüntüye gömülü (`hard`/`tr`), İngilizce sekme dosyası temiz + soft VTT; OK.ru için gömülü olduğu
ölçülmedi: `sub_mode:"none"` kalır, etikete sitenin sekme dilini KOYMAYIZ (`OK.ru · 1080p`), bilinmeyen durum `sub_known:false` ile,
sekmenin söylediği dil `site_lang_hint` ile ayrı verilir. Aynı dosyanın yeniden imzalanmış iki çözümlemesi (sitenin `/dl/` bağlantısı +
`/api/moly` yönlendirmesi aynı VidMolly dosyasını verir, her gömme sayfası isteği yeni imzalı `master.m3u8` üretir) tek akış sayılır
(sağlayıcı + host + yol); başka host/yoldaki gerçek yedek `mirror_of` ile işaretlenir, etiketi `… · yedek`, ana akışın hemen arkasındadır.
Sıra: ölçülmüş Türkçe → altyazı durumu bilinmeyen (OK.ru) → İngilizce. Vekil: host allow-list (`srt.vidmoly.*`, `SUBTITLE_HOSTS`), 1 MB / 5 sn, BOM/SRT→VTT/zaman
normalizasyonu, `data/subcache`, ölü kaynak 404.
TV istemcisi (uygulandı): oynatıcıda "Ses ve Altyazılar" paneli (Sarı tuş / Aşağı ok; SES | ALTYAZI iki sütun, Kapalı, gömülü rozeti);
soft iz motor-bağımsız DOM katmanıyla gösterilir (motor saati ~4 Hz), gömülü altyazılı akış = o dosyaya konum korunarak geçiş;
varsayılan Türkçe soft > Türkçe gömülü akış > İngilizce soft > kapalı, tercih `dz_pref_sub_<profil>` / `dz_pref_audio_<profil>`
(`localStorage`), oynatma akışı ilk akışı tercihe göre seçer; iz yüklenemezse sessizce Kapalı. Ayrıntı: `tizen-client/README.md`.

Oynatıcı mevcut `attempt_token` raporlamasını kullanır. Akış yanıtında `duration`
saniyedir; katalog `runtime_minutes` dakikadır. Birimleri dönüştürme normalizasyon
sınırında bir kere yapılır. Diziye devam/sonraki bölüm hedefi sunucu tarafından
hesaplanır. Sayfalama filtreleme ve kimlik tekilleştirmesinden sonra yapılır.

## 6. Mevcut koddan hedefe geçiş

| Mevcut | Hedef |
|---|---|
| Bölümler video_sources satırlarından türetiliyor | Sağlayıcıdan bağımsız `library_episodes` metadata kaydı. **Uygulandı (TMDB alanları):** `library_seasons` / `library_episodes` (başlık, özet, yayın tarihi, süre, still; sezon posteri) TMDB'den dolar ve API'de okurken kaynakla birleşir; bölüm *listesi* hâlâ video_sources'tan gelir (videosuz TMDB bölümü listeye eklenmez) |
| `playback=video/trailer/unavailable` | Geriye uyum için tutulur; eylemler `availability` + açık hedeflerden gelir |
| Genel `new` satırı | `new_movies`, `new_episodes`; eski `new` uyumluluk alias'ı olarak kalır. **Uygulandı:** `new_episodes` (bölüm kartları) `/api/row/new_episodes` ile çekilir (ana ekran düzeni değişti: ana ekranda yok, bkz. yukarıdaki not); `new_movies` `/api/row/` takma adıdır (TV'de `movies` = "Tüm Filmler" satırı); `new` legacy yerleşimde |
| Türler client davranışını etkileyecek metinlerle sınırlı | Merkezi genre ID/etiket sözlüğü ve endpoint |
| Runtime alanları farklı anlamlar taşıyabiliyor | Metadata dakika, oynatma/progress saniye; açık dönüşüm |
| Scraper düz film alanları | Batch şemasına adapter; `runtime` → `runtime_minutes`; episode videoları bölümün altına |
| Fragman otomatik fallback | Açık fragman eylemi ve isteği; film isteğinde fragman yok |
| Yeni olanlar `added_at` | Tekrarlı taramadan bağımsız ilk kullanılabilirlik tarihi |

Mevcut ID, alias, ilerleme ve Listem korunur. `server/CANONICAL.md` uygulamadaki
mevcut davranışı, bu belge hedef sözleşmeyi anlatır. İlk adapter uyarlaması
Sinemalar metadata+fragman; ikinci Yabancı Dizi metadata'dır. Yabancı Dizi'nin
bölüm/video çıkarımı doğrulanmadan boş video alanı uydurulmaz.
