# diziflix — API sözleşmesi (v1)

Server: `http://192.168.0.61:8090`  ·  Tüm yanıtlar JSON, `Access-Control-Allow-Origin: *`
Hata: `{"error": {"code": "...", "message": "..."}}` + uygun HTTP status.

## Ortak şemalar

Kural (geriye uyum): yanıtlara ALAN EKLENİR, alan silinmez/yeniden adlandırılmaz/türü değişmez. İstemci bilmediği alanı
yok sayar. `null` = bilinmiyor; boş metin yerine `null` değil `""` dönen alanlar ayrıca belirtilmiştir
(`overview`, `episodes[].overview`). Süreler: `runtime`/`episodes[].runtime` DAKİKA (bilinmiyorsa `0`, `null` değil),
`progress.position/duration` ve `streams.duration` SANİYE.

Çalışan, sansürlü örnek yanıtlar: `docs/api-samples/*.json` (üretici: `server/tools/gen_api_samples.py`;
`server/tests/test_client_contract.py` istemcinin okuduğu alanları uç nokta başına doğrular ve örneklerin
bozulmasını yakalar).

### Item (kart)
```json
{
  "id": "tmdb_tv_103516",        // HER ZAMAN yapım kimliği (bölüm kartında da)
  "card_kind": "title",          // "title" | "episode" (bkz. Bölüm kartı)
  "card_key": "title:tmdb_tv_103516",   // satır içinde benzersiz odak/liste anahtarı; bölüm kartında "episode:<episode_id>"
  "type": "series",              // "series" | "movie"
  "title": "Kayıp Sinyal",
  "year": 2021,                  // null olabilir
  "card":     "/img/s_0042/card?w=342&h=192",     // yatay kart
  "portrait": "/img/s_0042/portrait?w=300&h=450",
  "backdrop": "/img/s_0042/backdrop?w=1280&h=720",
  "has_backdrop": true,          // gerçek yatay afiş varsa true (poster/backdrop yoksa /img üretilmiş yer tutucu döner)
  "overview": "Kısa özet...",    // "" olabilir
  "genres": ["Dram", "Gerilim"], // [] olabilir
  "rating": 8.1,                 // null olabilir (0 = bilinmiyor DEĞİL, null = bilinmiyor)
  "badge": "YENİ",               // null olabilir
  "country": "US", "followers": 1172,   // null olabilir
  "sources": ["yabancidizi"], "tmdb_id": 103516, "imdb_id": "tt12327578",   // null olabilir
  "availability": {"state":"ready","reason":null,"has_trailer":true},
                                 // state: ready | check_required | unavailable; reason: null | no_video_source | sources_unavailable
  "playback": "video",           // video | trailer | unavailable (eski alan; eylemler için availability + actions)
  "progress": {                  // yoksa null; dizide profilin son bitmemiş bölümü
    "episode_id": "tmdb_tv_103516:s4:e1",
    "position": 1240, "duration": 2700, "pct": 46
  }
}
```

### Bölüm kartı (`card_kind: "episode"`)
"İzlemeye Devam Et" (dizi için) ve `new_episodes` satırı (artık ana ekranda yok, `/api/row/new_episodes` ile çekilir) bölüm kartıdır: Item'ın tüm alanları (`id` = yapım
kimliği, `type: "series"`) + şunlar. Bölüm kartı ayrı bir sayfa açmaz: dizinin normal sayfası (`id`) açılır,
`episode_id` ilgili sezon/bölümü seçer.
```json
{ "card_kind": "episode",
  "card_key": "episode:tmdb_tv_103516:s4:e10",
  "episode_id": "tmdb_tv_103516:s4:e10",
  "episode_label": "S04 B10 · Başlık",     // özel bölüm (sezon 0): "Özel B02 · Başlık"; "10. Bölüm" gibi genel ad tekrarlanmaz: "S04 B10"
  "season": 4, "episode": 10,
  "still_url": "/img/tmdb_tv_103516:s4:e10/still?w=320&h=180", "has_still": true,
  "availability": {"state":"ready","reason":null,"has_trailer":true},   // HEDEF BÖLÜMÜN durumu (fragman yapıma ait)
  "progress": {"episode_id":"tmdb_tv_103516:s4:e10","position":56,"duration":3559,"pct":2},   // bu bölümün ilerlemesi ya da null
  "primary_action": {"kind":"resume_episode","item_id":"tmdb_tv_103516","episode_id":"tmdb_tv_103516:s4:e10","position":56}
}
```
`primary_action.kind`: `resume_episode` (bitmemiş ilerleme var, `position` saniye) | `play_episode`. Bir bölüm kartının
varsayılan eylemi ASLA dizi kimliğini oynatılacak bölüm gibi kullanmaz.

### Episode
```json
{ "id":"tmdb_tv_103516:s1:e3", "season":1, "episode":3, "title":"Yankı",
  "overview":"...", "runtime":45, "still":"/img/tmdb_tv_103516:s1:e3/still?w=320&h=180",
  "still_url":"/img/tmdb_tv_103516:s1:e3/still?w=320&h=180",   // `still` ile aynı adres
  "has_still": true,             // gerçek (TMDB/kaynak) görsel var; false ise /img üretilmiş yer tutucu döner (istemci indirmez)
  "air_date": "2021-06-18",      // yayın tarihi ya da null (gelecekte + availability.state != ready = "Yakında")
  "availability": {"state":"ready","reason":null,"has_trailer":false},   // YALNIZCA bu bölümün kaynakları
  "progress": {"position":1240,"duration":2700,"pct":46} }   // yoksa null
```
`title` / `overview` / `runtime`: kaynak doluysa kaynak, boşsa (ya da "3. Bölüm" gibi salt etiketse) TMDB'den
tamamlanır. Görsel: TMDB still öncelikli, kaynak still yedek, ikisi de yoksa yer tutucu.
Bölüm kimliği `<yapım kimliği>:s<sezon>:e<bölüm>` biçimindedir; `/img/{kimlik}/still` ve `?episode=` aynısını kullanır.

## Uçlar

### `GET /api/health`
`{"status":"ok","source":"mock","items":120,"cache_age":12}`

### `GET /api/profiles`
`{"profiles":[{"id":"p1","name":"İsmet","avatar_seed":"a3","avatar":"/img/avatar/a3?w=200&h=200","is_kids":false}]}`
- `avatar_seed`: profilin seçili avatarının kimliği (avatar kataloğundan). `avatar` url'i bu seed ile üretilir.

### `GET /api/avatars`   ← seçilebilir avatar kataloğu (profil düzenleme ekranı için)
```json
{"avatars":[
  {"seed":"a1","url":"/img/avatar/a1?w=200&h=200"},
  {"seed":"a2","url":"/img/avatar/a2?w=200&h=200"}
]}
```
- Deterministik, sabit bir set (≥16 avatar). Her `seed` `/img/avatar/{seed}` ile farklı renk+motif üretir.

### `POST /api/profiles`  body `{"name":"...","is_kids":false,"avatar_seed":"a3"}` → oluşturulan profil
- `avatar_seed` opsiyonel; verilmezse katalogdan deterministik bir tane atanır.

### `PUT /api/profiles/{id}`  body `{"name":"...","avatar_seed":"a5","is_kids":false}` → güncellenen profil
- Tüm alanlar opsiyonel; sadece gönderilenler güncellenir. Bilinmeyen `id` → 404, geçersiz `avatar_seed` → 400.

### `DELETE /api/profiles/{id}`

### `GET /api/boot?profile={pid}[&layout=tv-v1]`   ← **KADEME 1, <200ms**
İki yerleşim vardır. TV istemcisi `layout=tv-v1` ister; `layout` yoksa ESKİ (legacy) yerleşim döner ve eski istemciler
için aynen korunur.

**`layout=tv-v1`** (güncel TV istemcisi ve Android). Ana ekran yukarıdan aşağı: slider (hero carousel) ->
"İzlemeye Devam Et" -> "Haftanın Trendleri · Diziler" -> "Tüm Diziler" -> "Haftanın Trendleri · Filmler" ->
"Dikkate Değer Filmler" -> "Tüm Filmler" -> "Listem" (yalnız doluysa):
```json
{
  "hero": { "...Item...", "logo_text": "Kayıp Sinyal", "tagline": "Haftanın Dizisi", "in_mylist": false },
  "heroes": [ { "...hero..." } ],           // slider: en çok 6 kart (3 dizi + 3 film, dizi/film dönüşümlü); hero == heroes[0]
  "rows": [
    {"id":"continue","title":"İzlemeye Devam Et","loaded":true,"items":[...],"total":5,"offset":0},
    {"id":"trending_series","title":"Haftanın Trendleri · Diziler","loaded":true,"items":[...],"total":20,"offset":0},
    {"id":"series","title":"Tüm Diziler","loaded":true,"items":[...],"total":84,"offset":0},
    {"id":"trending_movies","title":"Haftanın Trendleri · Filmler","loaded":true,"items":[...],"total":20,"offset":0},
    {"id":"noteworthy_movies","title":"Dikkate Değer Filmler","loaded":true,"items":[...],"total":37,"offset":0},
    {"id":"movies","title":"Tüm Filmler","loaded":true,"items":[...],"total":17,"offset":0},
    {"id":"mylist","title":"Listem","loaded":true,"items":[...],"total":3,"offset":0}
  ],
  "layout": "tv-v1", "catalog_total": 101, "source": ""
}
```
- Bu yerleşimde tüm satırlar `loaded:true` ve boş değil (boş satır hiç gönderilmez; `mylist` yalnız Listem doluysa, hep sonda); her
  satırda en çok 20 item, `total` tam uzunluk. Katalog boşsa `hero:null, heroes:[], rows:[]`, `catalog_total:0`. Satır sırası ve
  listesi sunucuda tek yerde belirlenir (`homelayout.layout(profil)`, `HOME_LAYOUT` env; ileride izleme alışkanlığına göre tür
  satırları `genre_<slug>` buraya eklenecek): istemci satırları kimliğe bakmadan GENEL çizer, bilinmeyen/yeni kimlikler de çizilir;
  yalnız `continue` (uzun basış menüsü, bölüm odağı), `series`/`movies` (sondaki "Tümü" kartı) ve `mylist` özeldir.
- **`HOME_ONLY_READY`** (varsayılan açık): ana ekran satırları ve slider YALNIZ oynatılabilir (`availability.state == "ready"`)
  başlıkları gösterir; kaynağı olmayan/şüpheli/ölü başlıklar ana ekranda yoktur ama arama, `/api/catalog` ve `/api/detail`'de
  durur. İstisna: `continue` ve `mylist` profilin kendi listeleridir, olduğu gibi gelir. `HOME_ONLY_READY=0` ile kapatılır.
- **Slider** (`heroes[]`): en popüler VE güncel `HERO_SERIES` (3) dizi + `HERO_MOVIES` (3) film (`trend_score`, aşağıda); adaylar
  `has_backdrop:true` ve oynatılabilir olmalı; sıra dizi, film, dizi, film, dizi, film (bir türden yetmezse diğerinden tamamlanır,
  toplam en çok 6); eşitlikte `rating`, sonra `id`. `tagline`: sitenin trend listesindeyse "Haftanın Dizisi"/"Haftanın Filmi",
  yeniyse "Yeni", değilse "Popüler". Eski klasik filmler (aşağıya bak) yalnız bir sitenin trend listesindeyse slider'a girer.
- **Sınıflandırma:** tüm sitelerin birleşik kanonik başlıkları `type`'ına göre havuzlara ayrılır. `series` / `movies` ("Tüm
  Diziler/Filmler") = o türün TÜM başlıkları, en son eklenen (`added_at`) önce, eşitlikte `id` (site `latest_*` listeleri artık satır
  KISITLAMAZ). `trending_series` / `trending_movies` = o türün sitelerdeki `trending_<site>` listeleri (siteler arası dönüşümlü,
  tekrarsız) + 20'den kısaysa `trend_score` ile en yüksekten doldurma (skor 0 olan eklenmez; klasikler doldurmaya girmez).
  `noteworthy_movies` = sitelerin `noteworthy_movies` listelerindeki filmler (liste sırası, dönüşümlü) + KLASİKLER (oynatılabilir
  film, `rating >= CLASSIC_MIN_RATING` (7.5) ve yayın yılı `<= bu yıl - CLASSIC_MIN_AGE_YEARS` (15); yılı olmayan klasik sayılmaz),
  klasikler `rating` azalan sonra `id`, tekrarsız. Rol listeleri (`latest_*`, `featured`, `noteworthy_movies`, `trending`) ayrıca
  `trend_score` sinyalidir.
- **`trend_score`** (deterministik, `homelayout.trend_score`; eksik alan 0 katkı, hata vermez): sitenin trend listesinde `50 - konum`
  (en az 5, en iyi site) + diğer rol listelerinde üyelik (liste başına 20, toplam en çok 40) + `added_at` 14 gün içinde 15 / 45 gün
  içinde 8 + yayın yılı >= bu yıl-1 için 10 + dizide en yeni yayınlanmış bölüm 14 gün içinde 15 + `rating` >= 6 için
  `(rating-6)*5` (en çok 20) + takipçi (log ölçekli, en çok 10). Klasik olmak puan VERMEZ.
- `continue`: profilin son izlemesine göre; dizide BÖLÜM kartı (`card_kind:"episode"`), film başlık kartı. `progress`
  eskisi gibi (`episode_id` dahil) kalır.
- Ana ekranda artık GÖNDERİLMEYEN eski satırlar (`trending`, `new_series`, `new_episodes`) yine de `/api/row/{id}` ile aynen
  çekilebilir: `new_series` (kaynakların `latest_series_<site>` dizi kartları, başlık kartı),
  `new_episodes` (her dizinin en yeni YAYINLANMIŞ ve `ready` bölümü, BÖLÜM kartı; eski `latest_episodes` takma ad), `trending`
  (tüm türler). `new_movies` = `movies` takma adıdır.
- Her satır kimliği `/api/row/{id}` ile de çekilebilir (ana ekranla aynı havuz ve sıra, sayfalanır).

**Legacy yerleşim** (`layout` yok):
```json
{
  "hero": { "...Item...", "logo_text": "Kayıp Sinyal", "tagline": "..." },
  "rows": [
    {"id":"continue","title":"İzlemeye Devam Et","loaded":true,"items":[...]},
    {"id":"new","title":"Yeni Eklenenler","loaded":true,"items":[...]},
    {"id":"mylist","title":"Listem","loaded":false,"count":7},
    {"id":"genre_drama","title":"Dram","loaded":false,"count":34}
  ],
  "source": "", "sources": [ {"id":"yabancidizi","name":"Yabancı Dizi","count":101} ]
}
```
- `loaded:true` satırlar item'larıyla gelir (max 20 item).
- `loaded:false` satırlar sadece başlık + `count` → client iskelet çizer, lazy çeker.
- `continue` boşsa satır hiç gönderilmez. Genel `new` satırı eski istemciler için uyumluluk takma adıdır
  (`new_movies` / `new_episodes` DATA-CONTRACT-V1 adlarıdır).

### `GET /api/row/{row_id}?profile={pid}&offset=0&limit=20`   ← **KADEME 2**
`{"id":"genre_drama","title":"Dram","items":[...],"offset":0,"limit":20,"total":34}`
Satır kimlikleri: ana ekran (`tv-v1`): `continue`, `trending_series`, `series`, `trending_movies`, `noteworthy_movies`, `movies`, `mylist`;
ana ekranda olmayan ama çalışan eskiler: `new_series` (`latest_series` takma adı), `new_episodes` (`latest_episodes` takma adı),
`new_movies` (= `movies`), `trending`, `new`, `top10`, `yakinda`, `genre_<slug>`. Bilinmeyen kimlik → 404. `limit` 1..100.

### `GET /api/detail/{item_id}?profile={pid}`   ← **KADEME 3**
```json
{ "...Item...",
  "cast":["..."], "director":"...", "runtime":45, "in_mylist":false,
  "seasons":[{"season":1,"title":"1. Sezon","episodes":[Episode,...],
              "name":"1. Sezon",             // ek: TMDB sezon adı (genel "Sezon 1" ise `title`)
              "overview":"...",              // ek: TMDB sezon özeti ("" olabilir)
              "air_date":"2021-06-18",       // ek: null olabilir
              "poster_url":"/img/s_0042:s1/portrait?w=300&h=450",  // ek: sezon posteri
              "has_poster":true,             // ek: false ise poster_url dizinin afişidir
              "episode_count":10}],          // ek: episodes uzunluğu
  "similar":[Item,...],
  "resume": {"episode_id":"...","position":1240},
  "actions": [ {"kind":"resume_episode","item_id":"...","episode_id":"...","position":1240},
               {"kind":"play_trailer","item_id":"..."} ]
}
```
Film ise `seasons: []` ve `resume.episode_id == item_id`.
- `seasons`: sezon numarasına göre artan; özel bölümler (sezon 0) LİSTENİN BAŞINDA gelir, istemci sona alır. `episodes`
  bölüm numarasına göre artan. Bölüm listesi yalnızca kaynağı olan bölümleri içerir; sezon/bölüm listesi henüz gelmemiş
  (yalnız katalog/fragman) dizide `seasons: []`.
- `resume` (eski alan): dizide profilin son bitmemiş bölümü; yoksa İLK İZLENEBİLİR normal bölüm (özel bölüm yalnız başka
  bir şey oynamıyorsa); dizide hiç bölüm yoksa `episode_id` dizi kimliğidir (oynatılacak bölüm DEĞİL: `actions`a bak).
- `actions` (ek, hedefli eylemler; boş olabilir, en önemlisi başta): `play_movie` | `resume_movie` (film, tam kaynak
  `ready`/`check_required`), `play_episode` | `resume_episode` (dizi: bitmemiş bölüm, yoksa ilk izlenebilir bölüm; yalnızca o
  bölümün kaynağı varsa), `play_trailer` (canlı fragman var). `resume_*` `position` (saniye) taşır. Kaynağı olmayan yapım
  için oynatma eylemi UYDURULMAZ. Fragman `GET /api/streams/{id}?kind=trailer` ile istenir; film isteği fragmana kendiliğinden dönmez
  (`kind=video`).
- `runtime` yapım/bölüm süresi DAKİKA; bilinmiyorsa `0`. `director` `null` olabilir, `cast` `[]` olabilir.
- `hydrating` (ek, bool, HER detay yanıtında; varsayılan `false`): `true` = bu yapım için arka plan hidrasyonu (dizi: sezon/bölüm
  envanteri, film: özet/metadata) şu an çalışıyor ya da bu istekle başlatıldı; yanıttaki veri henüz eksik olabilir. Sunucu bitince
  katalog yenilenir, SONRA `false` olur. İstemci "Detaylar yükleniyor…" gösterip yoklar; hazır = `hydrating:false` VE (film ya da
  `seasons` dolu). `hydrating:false` ama dizide `seasons: []` = hidrasyon bitti, veri alınamadı (hata dahil).
- `?poll=1` (isteğe bağlı, ek): aynı tam detay gövdesi, ama ASLA iş başlatmaz (hidrasyon, TMDB sezon geçişi, fragman doğrulaması,
  ön çözümleme yok; `actions` yine hesaplanır); `hydrating` yalnızca süren işi yansıtır. Yoklama (öneri: 3 sn, en çok 120 sn) için:
  normal istek yoklamada hidrasyonu yeniden tetikler, başarısız hidrasyon döngüye girerdi. Fragman canlılığı yalnızca normal istekte doğrulanır.

**Fragman doğrulaması (ek alan, geriye uyumlu).** Detay açılırken kaynak sitenin verdiği YouTube fragmanı
YouTube oEmbed ile doğrulanır (`200` = var, `404` = silinmiş/özel, `401/403` = gömme kapalı). Yanıttaki
`availability` (`{"state","reason","has_trailer"}`) şu ek alanı alır:
`availability.trailer` = `"ok"` (YouTube fragmanı doğrulandı) | `"dead"` (tüm fragman kaynakları ölü) | `"unknown"`
(YouTube dışı kaynak, geçici hata/zaman aşımı ya da ~2.5 sn'lik bütçe aşıldı: sağlam sayılır, arka planda doğrulanıp
önbelleğe yazılır). Alan yalnızca fragman kaynağı olan yapımlarda bulunur. `"dead"` iken `has_trailer=false` olur ve
`playback` yalnızca fragman olduğu için `"trailer"` idiyse `"unavailable"`a döner (tam izleme kaynağı varsa
`"video"` kalır); istemci canlı fragmanda "Fragmanı Oynat" çizer, `availability.trailer` alanı doluyken `has_trailer` yoksa
(ölü fragman) gri/devre dışı "Fragman yok" düğmesi çizer (oynatma denemez), hiç fragman kaydı (alan yok) yoksa düğme çizmez.
Doğrulama sonucu sunucuda kalıcı
önbelleklenir (sağlam 24 sa, ölü 6 sa; geçici hata damgalanmaz). Liste/katalog kartlarındaki `availability.has_trailer`
ve `playback` da aynı sonucu (bir sonraki katalog yenilemesinde) yansıtır; `availability.trailer` yalnızca detayda gelir.

### `GET /api/streams/{item_id}?episode={episode_id}`
```json
{ "streams":[{"url":"https://.../master.m3u8","type":"hls","quality":"1080p","label":"VidMolly · Türkçe altyazı · 1080p",
              "variant_id":"v_1a2b3c4d5e6f","audio_lang":"en","sub_mode":"hard","hard_lang":"tr",
              "sub_known":true,"site_lang_hint":"tr","mirror_of":null,"proxied":false}],
  "subtitles":[{"id":"9f8e7d6c5b4a39281706","lang":"en","label":"İngilizce","kind":"captions","format":"vtt",
                "url":"/api/subtitles/9f8e7d6c5b4a39281706.vtt","stream_ids":["v_7f6e5d4c3b2a"],
                "default":true,"origin":"soft"}],
  "audio":[{"id":"a_en","lang":"en","label":"İngilizce","stream_ids":["v_1a2b3c4d5e6f","v_7f6e5d4c3b2a"],"default":true}],
  "resume_position": 1240,
  "duration": 2700 }
```

`streams[].label` istemcide kaynak/kalite menüsünde olduğu gibi gösterilir: `<sağlayıcı> · <dil> · <kalite>` (örn. `VidMolly · Türkçe altyazı · auto`).
Dil parçası YALNIZCA sağlayıcının altyazı durumu ölçülmüşse (`sub_known:true`: VidMolly) ya da sayfa bir dublaj sekmesiyse (`Türkçe dublaj`) yazılır;
altyazı durumu bilinmeyen sağlayıcıda (OK.ru) sitenin sekme iddiası etikete konmaz: `OK.ru · 1080p`. Sayfa dili söylemiyorsa dil parçası yoktur.
Aynı dosyanın (aynı sağlayıcı + host + yol; yalnız imza/sorgu farklı) iki çözümlemesi TEK akış olarak listelenir. Aynı dosyanın (aynı `variant_id`, aynı
kalite) başka bir host/yoldaki gerçek yedeği korunur: etiketi ana akışınkine ` · yedek` (ikinci yedek ` · yedek 2`) eklenerek ayrılır ve ana akışın
HEMEN arkasına konur; ana etiketle çakışan başka bir dosyanın etiketine son çare olarak ` (2)` eklenir. Sıra: altyazı durumu ölçülmüş Türkçe dosyalar →
altyazı durumu bilinmeyen dosyalar (OK.ru) → İngilizce dosyalar. Alan adları/şekil değişmez.

**Ses / altyazı izleri (ek alanlar, geriye uyumlu: mevcut alanların adı/anlamı değişmedi, eski istemci yok sayar).**
Bir *varyant* = sağlayıcının tek video dosyası. Aynı bölümün iki dosyası yalnızca altyazıda ayrışabilir (yabancidizi: Türkçe sekme =
altyazısı GÖRÜNTÜYE GÖMÜLÜ dosya, İngilizce sekme = temiz dosya + sağlayıcının ayrı VTT'si). Kaynağın söylemediği bilgi UYDURULMAZ:
bilinmeyen alan `null` / `"none"` kalır.
- `streams[]` +: `variant_id` (kararlı dosya kimliği; imzalı CDN adresi değişse de aynı kalır), `audio_lang` (ses dili kodu ya da
  `null` = bilinmiyor/orijinal), `sub_mode` = `"hard"` (altyazı görüntüye gömülü, kapatılamaz) | `"soft"` (ayrı iz var, `subtitles[]`'te)
  | `"none"` (bilinen altyazı yok), `hard_lang` (yalnız `hard`: gömülü altyazının dili, örn. `"tr"`). Yanıttaki HER akışta bu dört alan bulunur.
  VidMolly: İngilizce dosya = `audio_lang:"en"`, `sub_mode:"soft"`; Türkçe dosya = `sub_mode:"hard"`, `hard_lang:"tr"`, `audio_lang` aynı
  kaynaktaki temiz dosyadan (yoksa `null`); dublaj sayfası = `audio_lang:"tr"`. OK.ru: gömülü altyazısı olup olmadığı ÖLÇÜLMEDİ (OK.ru
  metadata'sında altyazı/dil alanı yok; "Türkçe" yalnız sitenin sekmesinin iddiası) ⇒ `sub_mode:"none"` bırakılır, dil uydurulmaz.
- `streams[]` ++ (her akışta bulunur, eski istemci yok sayar): `sub_known` (bool) = sunucu bu dosyanın altyazı durumunu ÖLÇTÜ mü (VidMolly, ya da
  ayrı iz bulundu: `true`); `false` iken `sub_mode:"none"` "altyazı yok" DEĞİL "bilinmiyor" demektir (OK.ru, doğrudan/gömme kaynaklar),
  `site_lang_hint` (`"tr"`/`"en"`/`null`) = katalog sayfasındaki dil sekmesinin söylediği dil (altyazı ya da dublaj sekmesi): sitenin iddiası, ölçüm
  değil; istemci isterse ipucu olarak kullanır, etiket/seçim buna dayanmaz, `mirror_of` (`null` | metin) = doluysa bu akış, aynı dosyanın
  (aynı `variant_id` + `quality`) başka host/yoldaki YEDEĞİDİR ve değer ana akışın `"<variant_id>:<quality>"` anahtarıdır (ana akış listede
  hemen önde, kendi `mirror_of` değeri `null`). Yedek akış ana akışla AYNI `variant_id`'yi taşır: ses/altyazı paneli `variant_id`'ye göre gruplar,
  yedek ikinci bir "Türkçe (gömülü)" satırı doğurmaz. İstemci yedeği kalite/kaynak menüsünde gizleyip yalnızca ana akış düşünce deneyebilir.
- `subtitles[]` (yanıt kökü; yalnızca AYRI/soft izler): `id`, `lang` (kod ya da `null`), `label` (Türkçe dil adı ya da sağlayıcının etiketi),
  `kind` (`captions`|`subtitles`), `format:"vtt"`, `url` (`/api/subtitles/<id>.vtt`, sunucu yolu: istemci taban adresi ekler; sağlayıcı adresi ASLA
  görünmez), `stream_ids` (izin uygulanabildiği `variant_id` listesi; `null` = tüm akışlar), `default`, `origin:"soft"`. Gömülü altyazı iz
  DEĞİLDİR: `streams[].sub_mode:"hard"` ile anlatılır.
- `audio[]` (yanıt kökü): `id`, `lang`, `label`, `stream_ids`, `default` (ilk = varsayılan); dili bilinmeyen tek giriş `lang:null`, `label:"Orijinal"`.
  Fragman/embed akışları listelenmez.

**Akış vekili (ek alanlar `proxied` + `proxy_reason`, geriye uyumlu).** Bazı akış bağlantıları yalnızca onu çözen istemcinin başlıklarıyla ya da
sunucunun IP'siyle çalışır (OK.ru mp4: imzalı adres çözümlemedeki `User-Agent`'a bağlı; googlevideo `videoplayback?ip=...`: sunucunun adresine bağlı;
bazı HLS sunucuları IP/oturuma bağlı). Sunucu bu akışları kendisi vekiller:
- `streams[].proxied` (bool, her akışta bulunur): `true` ise `url` sunucunun imzalı vekil adresidir; istemci onu başka bir akış gibi oynatır (eski
  istemci yok sayar, kod değişmez). `false` ise `url` doğrudan kaynak adresidir. Vekillenen akışın `type`/`quality`/`label`/`variant_id`/`attempt_token`
  alanları aynıdır, `embed` akışlar asla vekillenmez. Kaynağın istediği başlık değerleri (`request_headers`) yanıta ASLA girmez.
- **URL biçimi**: dosya (`type:"mp4"`) için `<istemcinin kullandığı taban>/api/stream-proxy/<token>`; HLS (`type:"hls"`, tür DEĞİŞMEZ) için
  `<taban>/api/stream-proxy/<token>/index.m3u8` (sondaki ad yalnızca uzantıya bakan oynatıcılar içindir, sunucu yok sayar; `/<token>` de çalışır).
  Oynatma listesindeki bölüm / anahtar / alt liste adreslerini sunucu yeniden yazar, istemci başka bir şey yapmaz.
- `streams[].proxy_reason` (isteğe bağlı, YALNIZCA `proxied:true` akışta; metin): neden vekillendiği. `ua` = dosya, kaynağın User-Agent'ına bağlı
  (OK.ru); `ip` = adres sunucunun IP'sine bağlı (googlevideo `videoplayback` + `ip=`/`ipbits=`); `recipe` = sağlayıcı tarifi istedi (`stream_proxy: true`);
  `learned` = bu kaynak için bir istemci başarısız oldu ve sunucunun kendi yoklaması vekilin işe yarayacağını gösterdi (`POST /api/playback-report`,
  aşağıda); `env` = sunucu ayarı (`STREAM_PROXY_FORCE` hepsini, `STREAM_PROXY_HOSTS` listelenen host'ları vekiller). İstemci değeri göstermek/loglamak
  dışında kullanmaz; yeni değerler eklenebilir.
- Taban adres: istemcinin sunucuya eriştiği adres. Tünel / ters vekil arkasında `X-Forwarded-Proto` + `X-Forwarded-Host` (yoksa `Forwarded`),
  değilse `Host` başlığı + istek şeması (LAN istemcisi LAN'da kalır). Geçersiz başlık değeri yok sayılır; `STREAM_PROXY_BASE_URL` yalnızca
  elle belirlenen tek taban içindir. Taban token'da yoktur: uç hangi adresten gelinirse gelinsin çalışır. Taban belirlenemezse akış vekillenmez.
- Token `STREAM_PROXY_TTL` (6 sa) geçerlidir; süresi dolunca vekil `410` verir (akışlar oynatmadan hemen önce istendiği için pratikte görülmez;
  istemci `/api/streams`'i yeniden ister). Yeniden yazılan oynatma listesindeki adresler de her liste isteğinde yeni token alır (canlı akış sürer).
  `POST /api/playback-report` değişmez: `attempt_token` ile eşleşir, URL'ye bakmaz.

### `GET|HEAD /api/stream-proxy/{token}` · `/{token}/{name}`
`token` yalnızca `/api/streams`'in verdiği imzalı belirteçtir (HMAC; açık vekil değildir; HLS'te oynatma listesinden türeyen alt belirteçler de
sunucunun kendi çektiği listeden gelir). `{name}` (örn. `index.m3u8`, `seg-1.ts`) yok sayılır. Sunucu dosyayı belirteçteki başlıklarla
(`User-Agent`/`Referer`/`Cookie`/`Origin`; istemcinin kendi UA'sı yok sayılır, `Cookie` başka host'a gitmez) yukarı akıştan çeker ve akıtır:
istemcinin `Range` (ve `If-Range`) başlığı aktarılır, yanıt durumu `200`/`206`/`416` ve `Content-Type`, `Content-Length`, `Content-Range`,
`Accept-Ranges` aynen döner (başka yukarı akış başlığı geçmez). `HEAD` gövdesiz aynı başlıkları verir. Hedef her adımda SSRF denetiminden
geçer (özel/yerel adres, 80/443 dışı port reddedilir; yönlendirmeler elle ve en çok 3 kez izlenir).
- **HLS**: oynatma listesi isteğinde sunucu listeyi kaynaktan çeker (`#EXTM3U` doğrulanır; uzantı `.txt` olabilir; en çok `STREAM_PROXY_PLAYLIST_MAX` bayt),
  TÜM adresleri (`#EXT-X-KEY`/`-MAP`/`-MEDIA`/`-I-FRAME-STREAM-INF` içindeki `URI="..."`, bölüm satırları, master içindeki alt listeler; göreli adresler
  listenin adresine göre çözülür) yeniden imzalı vekil adresleriyle değiştirip `application/vnd.apple.mpegurl` olarak verir (`Cache-Control: no-store`);
  bölüm / anahtar istekleri dosya gibi akıtılır. Liste isteğinde `Range` aktarılmaz.
- Hatalar (hata zarfı): `403` biçim/imza bozuk ya da hedef yasak (`forbidden_target`), `410` süre doldu (`token_expired`), `404` dosya yok,
  `502` yukarı akış hatası ya da liste değil (`not_a_playlist`: HTML "security error" sayfası gibi), `504` yukarı akış zaman aşımı, `503` eşzamanlı vekil
  sınırı aşıldı (`proxy_busy`): dosyada `STREAM_PROXY_MAX` (8) bağlantı; HLS'te SINIR AKIŞ BAŞINADIR (`STREAM_PROXY_HLS_MAX`, 24; oynatıcı çok bölüm
  ister) ve dolu grup 503 vermez, `STREAM_PROXY_QUEUE_WAIT` (8 sn) kuyrukta bekler. Dosyanın baytları sunucudan geçer (bant genişliği sunucuda).

### `GET /api/subtitles/{id}.vtt`
Bir akışın soft altyazısı, TEMİZ WebVTT olarak (`text/vtt; charset=utf-8`). `id` sunucuda kayıtlı kaynak adresinin özetidir (kalıcı; yeniden
başlatmada sürer). Sunucu kaynağı indirirken host allow-list'i (`srt.vidmoly.*`; `SUBTITLE_HOSTS` env ile genişler; her yönlendirmede yeniden
denetlenir), 1 MB / 5 sn sınırı uygular; BOM/UTF-8 temizler, SRT ise VTT'ye çevirir (`,`→`.`, `WEBVTT` başlığı), `MM:SS.mmm` zamanlarını
`HH:MM:SS.mmm` yapar, `data/subcache/`de önbellekler (kaynak düşse bile bayat kopya sunulur). Yanıt: `ETag` + `Cache-Control: public, max-age=86400`
+ `Access-Control-Allow-Origin: *`; `If-None-Match` ile `304`. Kaynak ölü/yasak/altyazı değil ise `404` (hata zarfı): istemci sessizce "Kapalı"ya geçer.

`?kind=video|trailer` (isteğe bağlı): `kind=trailer` yalnızca fragman kaynaklarını döndürür; YouTube videosu ölü
(silinmiş/özel/gömülemez) bir fragman için `streams: []` gelir (istemci bunu "Bu içerik için kullanılabilir video
kaynağı bulunamadı" olarak gösterir).

### Kaynak bulucu ve bildirimler (yalnız EKLEME)
Oynatılacak bir video (fragman değil) için `GET /api/streams/{id}` hiç akış çıkaramazsa (kaynak kaydı yok / hepsi çözülemedi / hepsi `broken`)
yanıt AYNEN eskisi gibidir (`streams: []`, hata kodu yok); sunucu ek olarak ARKA PLANDA bir **kaynak bulucu** işi başlatır ve yanıta isteğe
bağlı `"finder":{"state":"searching"|"not_found"}` ekler (yalnız akış yokken; kaynak bulucu kapalıysa ya da iş başlamadıysa alan YOKTUR).
İstemci bu durumda "kaynak aranıyor" gösterip sayfada kalabilir ya da gezebilir; bulununca bildirim gelir.

İş (başlık+bölüm başına tek-uçuş; aynı bölüm için `SOURCEFINDER_COOLDOWN` (3600 sn) içinde yeni iş açılmaz, `found` sonrası da): (1) `retry`: bölümün
bilinen kaynakları (kırık sayılanlar dahil) ZORLA yeniden çözülür; (2) `search`: başlık, kaynağı henüz olmayan en çok `SOURCEFINDER_MAX_SITES` (4)
arama-yetenekli sitede aranır, aynı kanonik kimliğe çözülen sonuçta o dizinin bölüm envanteri okunup aranan bölümün kaynağı çözülür; (3) `heal`: çözümleme
izi "aday var ama akış yok / bilinmeyen host" ise ve LLM hesabı sağlıklı, heal açık, günlük bütçe (`SOURCEFINDER_DAILY_BUDGET`, 10 ajan koşusu) uygunsa onarım
ajanı. İlk akış veren adım işi bitirir; bulunan kaynak `video_sources`ta kayıtlıdır ve çözülmüş bağlantısı önbelleğe yazılıdır (sonraki oynatma doğrudan).

**Çözülen ama OYNATILAMAYAN akış**: `POST /api/playback-report?profile={pid}` (isteğe bağlı `profile`, bildirimin gideceği profil) sunucu tarafına yazılabilecek bir
oynatma hatası (`playback_failed|timeout|network`; `aborted|offline|autoplay|unsupported|decode` ve tarayıcı-HLS hatası hariç) bildirirse sunucu aynı kaynak
bulucuyu o bölüm için başlatır (`trigger: playback_failed`; tek-uçuş / cooldown / bütçe kuralları aynı) ve yanıta isteğe bağlı
`"finder":{"state":"searching"|"not_found"}` ekler (yoksa alan YOKTUR; yanıt `{"ok":true}` olarak kalır). `retry` adımı, son oynatılamayan AYNI akışı
(aynı host+yol) "bulundu" saymaz; yeni/başka akış ya da sunucunun öğrendiği Referer/vekil bulunduysa sayar. İstemci davranışı `/api/streams` `finder` ile aynıdır.

#### `GET /api/source-finder/{item_id}?episode={episode_id}`
→ `{"state":"idle"|"searching"|"found"|"not_found","steps":[{"name":"retry","ok":false,"ms":1800,"note":"2 kaynak denendi, akış yok (...)"}],"updated_at":1790889094}`.
`steps` en çok 6 kısa kayıt (`name` = `retry|search|heal`; `ok` = bu adım akış buldu; `note` kısa Türkçe açıklama), `updated_at` epoch saniye (`idle`'da 0).
`episode` verilmezse film kendisidir; dizide verilmezse başlığın en yeni işinin durumu döner. `idle` = hiç iş yok (ya da yeniden başlatmada yarıda kesildi).
Bilinmeyen yapım/bölüm 404. `not_found` yalnızca bu uçta görünür: kullanıcıya bildirim GİTMEZ.

#### `GET /api/notifications?profile={pid}&since={id}` · `POST /api/notifications/read?profile={pid}` `{"upto":12}`
`GET` → `{"items":[{"id":12,"kind":"source_found","canonical_id":"tmdb_tv_103516","episode_id":"tmdb_tv_103516:s1:e3","title":"Dizi adı","season":1,"episode":3,
"site":"yabancidizi","method":"retry|search|heal","created_at":1790889094}],"last_id":12}`: profilin OKUNMAMIŞ bildirimleri, `id > since` (varsayılan 0),
eskiden yeniye, en çok 50. `last_id` = verilen en büyük `id` (öğe yoksa gönderilen `since`): istemci bir sonraki yoklamada `since=last_id` yollar. `kind` şimdilik
yalnız `source_found`; `episode_id` film için boş, `season`/`episode` film için `null`; `method` kaynağın nasıl bulunduğu (`retry` yeniden çözüldü, `search` başka
sitede bulundu, `heal` ajan onarımı). Toast'tan oynatma: `GET /api/streams/{canonical_id}?episode={episode_id}&kind=video`. `POST ... /read` → `{"ok":true,"marked":n}`:
`upto`'ya kadar (dahil) bildirimleri okundu yapar (idempotent; negatif/sayı olmayan `upto` 422). Profil verilmezse profilsiz bildirim listesi (boş) döner.
Hata zarfı olağan `{"error":{"code","message"}}`. Admin Olay defterinde her biten iş `kind=finder` olaydır (bulundu ya da bulunamadı, yöntem, adımlar).

### `POST /api/progress`
body: `{"profile":"p1","item_id":"tmdb_tv_103516","episode_id":"tmdb_tv_103516:s1:e3","position":1240,"duration":2700}`
→ `{"ok":true,"watched":false,"next_episode":null}`.  Client oynatırken **20 sn'de bir** ve pause/stop/exit anında gönderir.
Film için `episode_id == item_id` (ya da boş). `position/duration > 0.92` ise server kaydı "izlendi" (`watched:true`) işaretler,
`continue`'dan düşürüp aynı dizinin bir sonraki bölümünü sıraya koyar (`next_episode`: onun kimliği ya da `null`; kart olarak
`play_episode`, `position` 0). İstemcinin "izlendi" eşiği yuvarlanmış `pct >= 92`'dir. Hatalar: bilinmeyen profil 400, bilinmeyen
yapım/bölüm 404 (başka dizinin bölümü dahil), negatif süre 422.

### `DELETE /api/continue/{item_id}?profile={pid}`
"İzlemeye Devam Et" satırından kaldırır (yumuşak gizleme; `progress` silinmez: detaydaki kaldığı yer / izlendi durumu / "devam et"
düğmesi aynı kalır). `item_id` = yapım id'si (bölüm kartında da `id`, `episode_id` DEĞİL). → `{"ok":true,"removed":true}`;
idempotent: bilinmeyen öğe, hiç yarım izlenmemiş öğe ya da zaten gizli öğe hata değil, `removed:false` (200). Bilinmeyen/eksik
profil 400. Kural: öğe, `hidden_at >= o öğenin profildeki en son progress.updated_at` iken `continue`'dan (boot, `/api/row/continue`,
legacy boot) çıkar; sonra `POST /api/progress` ile yeniden izlenirse (`updated_at > hidden_at`) kendiliğinden satıra geri döner.
Diğer satırlar, `detail.progress` ve `POST /api/progress` değişmez. Profil silinince gizleme kayıtları da silinir.

### `GET /api/mylist?profile={pid}` · `POST /api/mylist` `{"profile","item_id"}` · `DELETE /api/mylist/{item_id}?profile={pid}`
`GET` → `{"items":[Item,...]}` (son eklenen önce). `POST` → 201 `{"ok":true,"in_mylist":true}` (bilinmeyen yapım 404, bilinmeyen profil 400);
`DELETE` → `{"ok":true,"in_mylist":false}`. `in_mylist` detayda ve `tv-v1` hero'sunda gelir, kartlarda YOKTUR.

### `GET /api/catalog?profile={pid}&type=movie|series&genre=&year=&availability=&sort=new|year|title|trending|popular&q=&mine=false&offset=0&limit=20`
`{"items":[Item,...],"total":34,"offset":0,"limit":20,"genres":[{"id":"drama","name":"Dram"}],"years":[2026,2025]}`
Filtre sayfalamadan önce uygulanır; `genres`/`years` seçilebilen değerlerdir (tür kimliği `genre` parametresidir; `availability`:
`ready|check_required|unavailable`). `mine=true` yalnızca Listem. `limit` 1..50. `sort`: `new` (varsayılan: oynatılabilirler önce, en son eklenen),
`year`, `title`; EK: `trending` = `trend_score` azalan (popülerlik + yenilik), `popular` = aynı skor ama zamana bağlı parçalar (yeni eklenme,
yayın yılı, yeni bölüm) hariç; ikisinde de oynatılabilirler önce, eşitlikte `rating`, `id`. Katalog `HOME_ONLY_READY`'den etkilenmez. `GET /api/genres?type=` → `{"genres":[{"id","name"}]}`.

### `GET /api/search?q=...&profile={pid}&limit=20&source=`
En az 3 karakterde arama ucu olan TÜM sitelerin (şimdilik Yabancı Dizi; yaml `search:` bloğu olan her yeni site
otomatik katılır) arama uçlarını kullanıcı isteğiyle canlı ve paralel sorgular (site başına ~8 sn, toplam ~12 sn).
Bulunan en fazla 20 yapım/site ortak şemaya çevrilip kanonik kimliğe çözülür (iki sitede bulunan aynı yapım TEK karttır)
ve yerel arama önbelleğine alınır; ana sayfa koleksiyonlarına eklenmez. Bir site yanıt vermezse (hata, zaman aşımı, art
arda hatada geçici "devre kesici" atlaması) diğer siteler ve yerel eşleşmeler yine döner. Sıra: uzak sonuçlar önce (siteler
arası dönüşümlü: her sitenin en iyi sonucu önde), sonra yerel eşleşmeler, tekrarsız, `limit`e kırpılmış.
`{"items":[...],"total":13,"remote":true,"remote_error":null,"remote_sites":{"yabancidizi":{"ok":true,"count":13,"ms":420}}}`
`remote_error` HER yanıtta bulunur (`null` ya da metin; birden çok site sorgulandıysa hatalı olanlar `"site: mesaj; ..."`);
3 karakterden kısa sorguda `remote:false, remote_error:null, remote_sites:{}` ve yalnızca yerel eşleşme.
`source=<site id>`: o site arama yeteneklidir ise uzak arama yalnız o siteye gider; arama ucu olmayan bir site yalnız yerel
(süzülmüş) arama yapar (`remote:false`); bilinmeyen kaynak 400.

EK alanlar (eski istemciler yok sayar):
- kökte `remote_sites`: `{<site id>: {"ok": bool, "count": int, "ms": int, "error"?: "kısa metin", "skipped"?: "breaker"|"unsupported"|"short_query"}}`
  (`count` = o sitenin bulduğu yapım sayısı; `error`/`skipped` yalnız doluysa vardır; `breaker` = art arda hata nedeniyle geçici atlandı).
- her öğede `source_options`: `[{"site": "yabancidizi", "name": "Yabancı Dizi", "kind": "series"|"movie", "episodes": 7, "status": "ok"|"unknown"|"broken"}]`
  = bu yapımın hangi sitelerde bulunduğu (kaynak seçimi). `episodes`: o sitede video kaynağı olan bölüm sayısı (dizi; bölüm
  envanteri henüz okunmadıysa 0) ya da film için 1/0. `status` sitenin fragman olmayan, devre dışı olmayan kaynaklarından:
  `broken` = hepsi kırık, `unknown` = hiçbiri denenmemiş (ya da hiç yok), aksi `ok`. En iyi kaynak önce (ok, unknown, broken; sonra bölüm sayısı).
  Eski `sources` (site kimlikleri listesi, `["yabancidizi"]`) AYNEN kalır; `source_options` onun ayrıntılı halidir.

Canlı aramayla ilk kez bulunan bir dizi açıldığında `GET /api/detail/{item_id}`
sezon/bölüm kataloğunu o anda getirir ve sonraki açılışlar için önbelleğe alır.

### `GET /img/{item_id}/{kind}?w=&h=`
`kind`: `card` | `portrait` | `backdrop` | `still` | (`/img/avatar/{pid}`)
İstenen boyutta JPEG döner, ETag + `Cache-Control: public, max-age=86400`.
Boyut whitelist dışıysa en yakın izinli boyuta yuvarla.
`item_id` bir yapım, bir bölüm (`{yapım}:s1:e3`, gerçek görsel `still`'de) ya da bir sezon (`{yapım}:s1`, gerçek görsel
`portrait`'te; sezon posteri yoksa dizinin afişi) olabilir. Gerçek görsel yoksa ya da indirilemezse üretilmiş yer tutucu döner.

### Hatalar
Her hata `{"error":{"code","message"}}` zarfıdır: `400 bad_request` (eksik/bilinmeyen `profile`, bilinmeyen `source`, geçersiz
`avatar_seed`), `404 not_found` (bilinmeyen yapım/bölüm/satır/görsel türü), `422 validation_error` (geçersiz parametre/gövde),
`500` (`?fail=1` ya da beklenmeyen). Silinmiş bir profil kimliğiyle `boot`/`row`/`catalog`/`mylist` 400 döner (istemci hata ekranı
gösterir; Ayarlar'dan profil değiştirilir).

## Geliştirme kolaylıkları
- Her uca `?delay=800` → yapay gecikme (kademeli yükleme / skeleton testi için).
- `?fail=1` → o uç 500 döner (client hata ekranı testi).

### Çoklu kaynak seçimi

`GET /api/sources` → `{ "sources": [{ "id": "sinemalar", "name": "Sinemalar.com", "count": 387 }] }`.
Sayılar canlı kataloğa göre değişir. `GET /api/boot`, `/api/row/{id}` ve `/api/search`
istekleri isteğe bağlı `source` parametresini kabul eder. Boş veya eksik değer ortak
kataloğu gösterir; bilinmeyen kaynak 400 döndürür. Filtreleme sayfalama öncesinde
uygulanır. Yapım kimlikleri, profiller ve listeler kaynaklar arasında ortaktır.
Boot yanıtında `source` ve `sources`; kart/detay yanıtlarında `sources` (kaynak
kimlikleri) ve `playback` (`video`, `trailer`, `unavailable`) bulunur. `trailer`,
fragman sağlayıcısını belirtir; her fragmanın çözüleceğini garanti etmez.

`GET /api/detail/{item_id}` yanıtında EK alan `source_names`: `[{"id":"yabancidizi","name":"Yabancı Dizi"}]` = `sources`
kimliklerinin görünen adları (aynı sıra; site yaml'ının `display_name`'i, site kayıtsız/silinmişse `name` = `id`).
İstemci detayda "Kaynak: A · B" etiketi gösterir; alan yoksa (eski sunucu) hiçbir şey çizmez.

### Tarama raporları

Tarama/heal geçmişi `GET /api/ops/runs` ve `GET /api/ops/heals` ile okunur (her biri
son 200 kayıt, `?site=`, `?limit=`). `/admin` tek sayfalık operasyon paneli; ayrıntı
`server/README.md` (Admin bölümü). `run_id`, `status` (`success`, `partial`, `error`),
`collections`, `added`, `updated`, `unchanged`, `duplicates`, `rejected` alanları ingest
sonucunda (`last_ingest`) kalır.

### Tek katalog / sağlayıcı tabanlı oynatma

Yeni TV istemcisi site filtresi sunmaz. Liste film/dizi/tür bazında ortaktır.
`source` parametresi eski istemciler için geriye uyumlu tutulur.
`tmdb_id` / `imdb_id` kart ve detay yanıtlarına eklenmiştir.
Akışlar `source_id`, `source`, `kind`, `attempt_token` taşır.
`POST /api/playback-report` başarı/hata bildirimini yalnızca ilgili sağlayıcıya
uygular. İsteğe bağlı EK alan `detail` (en çok 120 karakter, yalnızca `event: "failure"` ile; istemci hata ayrıntısı:
`hls:<tür>/<ayrıntı>[/<http kodu>]` hls.js, `video.error.code=N`, `avplay:<hata>`, `exo:<errorCodeName>[/<neden>][/http<kod>]`,
`start-timeout`, `user-report`) — alanı bilmeyen sunucu için zararsız. İsteğe bağlı EK alan `hlsjs` (bool, yalnız `true` ile gönderilir):
html5 motoru akışı hls.js ile oynatıyordu; sunucu bu hatayı "tarayıcı HLS'i oynatamıyor" saymaz (gerçek oynatma hatası: tanı kodu + akış
host'u ile oynatma sorunu defterine girer, ağ hatası kaynak sağlığına yazılır; `detail` `hls:<tür>/…` ile başlıyorsa `hlsjs` yokken de aynı). Tam sözleşme ve kimlik birleştirme kuralları: `server/CANONICAL.md`.
İstemci motor seçimi `streams[].type`'a (`hls|mp4|embed`) göre yapılır, URL uzantısına DEĞİL (vekil mp4 adresi uzantısız,
doğrudan HLS adresi `.txt` olabilir); uzantı yalnız `type` yoksa yedektir.
Yönetim: `/api/admin/video-sources`, `/api/admin/identities`.
