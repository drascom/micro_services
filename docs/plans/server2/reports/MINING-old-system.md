# MINING: eski sistemin dersleri -> server2 genel yetenek açığı listesi

Tarih 2026-10-04. Salt-okur inceleme: `server/` (site_extractors, providers, resolvers, recipes, pi skill/references, docs/arch, HANDOFF-2026-10-02,
git log) ve `server2/app/**`. Kod değiştirilmedi. Diğer worker'ların konuları (AJAX kaynak sekmeleri, JS çerezi, dil sekmeleri, film gelmemesi,
yinelenen kategoriler) tabloda YOK; eski sistemdeki ayrıntıları sondaki "Ek bilgi" bölümünde.

## Özet

Toplam **55 bulgu**: **ele alınıyor 35**, **kısmen 10**, **yok 10**. server2 eski dersleri büyük ölçüde kopyalamış (Chrome-TLS, challenge
geri çekilmesi, Referer öğrenme + host kuralı + refpolicy, IP-bağlı vekil, HLS yeniden yazma, süre ölçümü, twimg dersi, telif kapısı, slug
temizliği, sezon sekmeleri/sayfaları, sayfalama, regresyon kapıları). Açıklar çoğunlukla **kenar veri biçimleri** (çift bölüm, Final/özel
bölüm, karakter kodlaması, başlıkta sezon eki) ve **sağlık sinyalleri** (fragman canlılığı, kapsama, CORS).

Ele alınanlar (kanıt kısa): Chrome TLS `core/fetch/http.py:34`; challenge + host geri çekilmesi `core/fetch/base.py:62`, `limiter.py:74`,
`control/crawl/run.py:33`; robots/aralık `core/fetch/robots.py`; POST liste `base.py` `form_body`; kök yönlendirme/canonical ipucu
`core/fetch/signals.py:62`; netguard her hop; lazy `data-src` `core/extract/cards.py:67`; `<base href>` + çift yol düzeltmesi
`core/extract/episodes.py:495`; favori bağlantısı (wpfp) `cards.py`; JSON-LD/`__NEXT_DATA__` `extract/detail.py`, `play/resolvers/embedded_json.py`;
sayfalama `extract/urlpatterns.py:374`; yer tutucu afiş `catalog/textnorm.py:23`; slug eki temizliği (`daha-17` korunur) `textnorm.py:76`;
TMDB sezon eki `catalog/tmdb.py:113`; bölüm kartı -> başlık `control/crawl/process.py:472`; `default_season`, sezon sekmesi/sayfası,
`unaired_classes`, ilk/son bölüm doğrulaması, seçici kayınca bağlantı taraması `extract/episodes.py:1-30`; devam eden dizi yeniden okuma
`control/crawl/plan.py:202`; geri çekilme merdiveni `crawl/frontier.py:20`; VidMoly yazımları/OK.ru `srcAg` UA vekili/`ondemandHls`
`play/providers/okru.py`, `play/streamproxy.py`; unpack/base64 `play/resolvers/_unpack.py`, `_decode.py`; Referer öğrenme + host kuralı +
"Referer gönderme" (Yandex dersi) `play/refpolicy.py:26`, `play/hostrules.py`; googlevideo `ip=` vekili `streamproxy.py:310`; `valid_until`
`play/streamlife.py`; HLS yeniden yazma + `master.txt` `play/hlsproxy.py:41`; tracker/medya ayrımı (twimg) `play/badhosts.py:22`; süre ölçümü
`play/streamprobe.py:244`; altyazı/izler/mirror `play/tracks.py`, `subtitles.py` (cp1254 dahil); telif kapısı + geçici hata=retry
`play/gate.py:8`; engelli yeniden denetimi `catalog/write.py` `BLOCKED_RECHECK`; suspect/broken + cihaz kodları `play/sources.py:8,580`;
hls.js ayrımı `streamdiag.py:105`; tüm kaynakları bozuk başlık -> sorun, 404/410 -> gone `control/problems/system.py:1-14`; çift ters bölü
`core/llm/parse.py:59`, ReDoS sınırı `play/blocked.py`; tarifin diğer sitelere regresyonu `control/stages/a6_sources.py:88`; ≥3-5 örnek
`control/sitemap/meta.py:10`; onarımda iyi örneklerin regresyonu `control/stages/engine.py:80`.

## Açıklar (kategori başına)

Biçim: `[durum] Konu — eski sistem — server2 — genel çözüm (efor, risk)`.

### Fetch
- [kısmen] Karakter kodlaması — eski de yalnız `Content-Type` charset'i okuyordu (`server/app/scraper/fetch.py:231`) — `core/fetch/http.py:40`
  aynı; `<meta charset>`/`http-equiv` yok, Türkçe eski siteler (windows-1254 / iso-8859-9) bozuk harf verir, başlık eşleşmesi kayar —
  `decode_body`: başlık yoksa ilk 4 KB'ta `<meta charset>` ara, utf-8 çözümü `�` üretirse cp1254 dene (S, risk düşük).
- [kısmen] JS ile kurulan sayfa — eski `fetch_mode: browser` (Obscura) taramada çalışıyordu — `core/fetch/browser.py:23` `GUARDED=False`:
  auto tarayıcıya hiç yükselmez, crawl `browser`'ı http'ye çevirir (`control/crawl/fetcher.py:25`) -> JS siteler taranamaz — netguard'lı
  yönlendirme vekili (yerel forward proxy, her istekte `netguard.check_url`) + `GUARDED=True` (M-L, risk orta: SSRF yüzeyi).
- [kısmen] `Retry-After` — eski yok; server2 429'da sabit katlanan geri çekilme (`limiter.py:74`) — 429/503 `Retry-After` saniyesini
  `penalize`'a taban olarak ver (S, düşük).
- [yok] Afiş hotlink koruması — eski `images.py` yalnız UA; server2 `core/catalog/images.py:430` aynı — 403/boş görselde bir kez
  `Referer: <site base_url>/` ile yeniden dene, başarılıysa host'u hatırla (S, düşük).

### Sayfa yapısı
- [kısmen] `srcset` / `background-image` afiş — eski yalnız `data-src` önceliği — `cards.py:67` `srcset`/`data-srcset`/`style=background-image`
  okumaz (yalnız `checks.py:17` teşhiste) — `_image_attr` sırasına `data-srcset`/`srcset` (en geniş `w`) + inline `background-image:url()`
  ekle (S, düşük).
- [yok] AJAX "daha fazla yükle" / sonsuz kaydırma listesi — eski yok (yalnız `item_limit`, POST koleksiyon) — server2 yalnız bağlantı
  sayfalaması (`urlpatterns.py:374`) — harita parametresi `listing.more: {url, method, data, page_param, format: html|json}` (POST/GET +
  sayfa sayacı, yanıtı aynı kart seçicisiyle okur); A2 keşfi `grep` ile `admin-ajax|load_more|page=` adayı önerir (M, orta).
- [yok] "Devamını Göster"/"Read more" kuyruğu ve `----` ile ayrılmış iki dilli özet — eski siteye özel (`site_extractors/yabancidizi.py:107-108`)
  — `extract/detail.py` yok — özet alanına genel son-işlem: sondaki "devamını (göster|oku)/read more/…" kırp, `-{4,}` ayracında ilk yarıyı
  al (S, düşük).

### Kimlik
- [kısmen] Başlıkta sezon/final eki — eski `clean_title` "N. Bölüm" kuyruğunu siler, " - 4. Sezon", "Sezon Finali", "Final Bölümü" kalır —
  `catalog/textnorm.py:95,116` aynı (yalnız TMDB aramasında `tmdb.py:113`) -> sezon kartları ayrı kanonik başlık olur — `_SEASON_TAIL`
  (`[-–]?\s*\d+\.\s*sezon(\s+final[i]?)?$`, `sezon\s+\d+$`, `\s+(sezon\s+)?final(i|\s+bölümü)?$` yalnız başka kelimeden sonra) (S, düşük;
  "Final Destination" gibi baştaki kelimeye dokunma).
- [kısmen] Bölüm kartından dizi sayfası, `title_link` yoksa — eski `library/series_dir.py:229` liste satırlarından başlık -> slug -> tek önek —
  server2 `process.py:479` `no_title_link` ile boş biter — yedek: frontier'daki `title_row` URL'lerinde `episodes.episode_prefix` + başlık
  `fold` eşleşmesi (tek aday şartı) (M, orta: yanlış bağ).
- [kısmen] Yılsız kimlik — eski/yeni ikisi de yalnız başlık+yıl tek adayda bağlar (`catalog/identity.py:77`), yıl vermeyen site her başlığı
  yeni kimlik yapar (TMDB yoksa birleşmez) — yıl bir tarafta yoksa: aynı tür + `fold(title|original_title)` TEK aday -> bağla, çoksa
  `identity_reviews` (S-M, orta).
- [kısmen] "Başka sitede ara" sorgusu — eski `routers/onboard_sandbox.py:2920` bölüm/sezon/Final eklerini atar, 0 sonuçta ilk 2 kelime —
  server2 `control/stages/search_run.py:354` ham başlık — eski `search_query` + `_first_words` yedeğini `textnorm`'a taşı, `find_elsewhere`
  kullansın (S, düşük).

### Bölümler
- [yok] Çift bölüm "17-18. Bölüm" / `-17-18-bolum` — eski yalnız slug'dan atıyordu (`library/normalize.py:484`) — `extract/episodes.py:317`
  tek numara alır, 18 kaybolur — `episode_url_regex`'e isteğe bağlı `episode_end` grubu + metin `\d+\s*[-–]\s*\d+\.?\s*bölüm`; aynı URL'yi
  iki bölüm anahtarına yaz (`s1e17`, `s1e18`, `mirror_of`) (S-M, düşük).
- [yok] Numarasız bölüm: "Final", "Özel Bölüm", "Sezon Finali" — eski bu satırları regex'e uymadığı için düşürüyordu (sessiz kayıp) —
  server2 aynı; okuma tarafı sezon 0'ı tanıyor (`api/rows.py:169`) — kural: satır regex'e uymaz ama `final` -> sezonun son numarası+1,
  `özel|special|ova` -> sezon 0 sıra numarasıyla; `numbers_from_text` yedeği; teşhise "numarasız N satır" uyarısı (M, orta).
- [yok] Bölüm boşluğu — eski yalnız ilk/son bağlantı tutarlılığı (`library/series_crawl.py:304`) — server2 aynı — envanter TMDB
  `episode_count` ile kıyas: eksik numara oranı > %20 ise site sorunu `missing_field(page_type=season)` (S, düşük).

### Oynatma
- [yok] Adres ailesi (IPv4/IPv6) bağlı akış — HANDOFF-2026-10-02:25 (googlevideo sunucunun IPv6'sına bağlı) — server2 vekil isteği
  çözümlemeyle aynı aileyi garanti etmez — `ip=` içeren akışta IP sürümünü oku, vekil oturumunu o aileye sabitle (curl `ipresolve`) (S, düşük).
- [yok] Tarayıcıda CORS reddi -> otomatik vekil — HANDOFF:76 "CORS/ağ hatası otomatik vekile çevrilmiyor" (açık kalmıştı) — server2
  `streamproxy.py:310` nedenlerinde `cors` yok — `streamprobe` yanıtın `Access-Control-Allow-Origin`'ını kaydetsin; yoksa ve engine
  `html5` ise `proxy_reason: cors` (host kuralına öğren) (S-M, düşük; bant genişliği).
- [yok] TV codec sınırı (HEVC/mkv) — eski yok; istemci turu ertelendi — `streamprobe` HLS `CODECS=` / mp4 `hvc1|hev1` okuyup akışa
  `codec` alanı ekle (yalnız alan; istemci turunda kullanılır) (S, düşük). Öncelik dışı.

### Kapı / sağlık
- [kısmen] Soft-404 / "içerik kaldırıldı" 200 sayfası — eski yalnız kapı (oynatıcı yok) + `blocked:` kuralı — server2 `gone` yalnız 404/410
  (`control/problems/system.py:8`) — `core/fetch/signals.py`'e `gone_hint`: başlık/h1'de "sayfa bulunamadı|404|kaldırıldı|not found" +
  ince gövde -> frontier `gone` gibi davran (ölçümle, 2 ardışık okuma) (S-M, orta: yanlış pozitif).
- [kısmen] Kapsama sinyali — eski `playheal.coverage_signal` (dizilerin ≥%50'si kaynaksız -> onarım) — server2 yalnız afiş payı
  (`system.py:27` `MISSING_SHARE`) — günlük `problem_scan`'e "görünür dizilerin > %X'i bölümsüz/kaynaksız" -> `missing_field(page_type=season|player)` (S, düşük).
- [yok] Fragman canlılığı — eski `library/trailer_check.py` (YouTube oEmbed, sağlam 24 sa / ölü 6 sa) — server2'de tablo var
  (`migrations/003_catalog.py:76`), yazan kod yok; `api/rows.py:583` olmayan `trailer_check.apply_detail`'e atıf — eski modülü
  `core/play/trailer_check.py` olarak kopyala, detay açılışında bütçeli çağır (S, düşük).

## Önerilen uygulama sırası (ilk 10: isabet olasılığı × düşük efor)

1. Karakter kodlaması sniff (S) 2. Başlıkta sezon/final eki (S) 3. Çift bölüm (S-M) 4. Numarasız Final/Özel bölüm (M)
5. "Başka sitede ara" sorgu temizliği + 2 kelime yedeği (S) 6. Özet "Devamını Göster" kırpma (S) 7. Afiş hotlink Referer yedeği (S)
8. `srcset`/`background-image` afiş (S) 9. Fragman canlılığı (S) 10. Kapsama + bölüm boşluğu sorunu (S)
Sonra: CORS vekili, IP ailesi, yılsız kimlik, soft-404, `Retry-After`, `title_link` yedeği; büyükler: AJAX "load more", tarayıcı korumalı vekil.

Paralel paketler (dosya çakışması yok):
- **Paket A — metin/bölüm** (2, 3, 4, 5, 6): `core/catalog/textnorm.py`, `core/extract/episodes.py`, `core/extract/detail.py`,
  `control/stages/search_run.py` (+ testleri). Bölüm/başlık değişince smoke testleri `tests/test_episodes*`, `test_textnorm*`.
- **Paket B — getirme/görsel** (1, 7, 8): `core/fetch/http.py` (`decode_body`), `core/catalog/images.py`, `core/extract/cards.py`.
  Dikkat: JS-çerez worker'ı `core/fetch/http.py`/`cookies.py`'ye dokunuyor olabilir: o iş bitince başlat ya da yalnız `decode_body`'ye dokun.
- **Paket C — sağlık** (9, 10): yeni `core/play/trailer_check.py`, `api/rows.py` (yalnız çağrı), `control/problems/system.py`,
  `core/catalog/seasons.py` (TMDB `episode_count` okuma). Sonraki tur: `core/play/streamprobe.py` + `streamproxy.py` (CORS, IP ailesi).

## Ek bilgi (diğer worker'ların konuları; eski sistemdeki ayrıntı)

- **AJAX kaynak sekmeleri** (`server/app/scraper/site_extractors/yabancidizi.py:196-233, 251-320`): sekmeler `.alternatives-for-this [data-link]`
  + `data-hash` + `data-querytype` (BOŞSA `alternate`). POST `/ajax/service` form `{link, hash, querytype, type: videoGet}`; başlıklar
  `Accept: application/json, text/javascript, */*; q=0.01`, `Origin`, `Referer`=bölüm sayfası, `X-Requested-With: XMLHttpRequest`. Yanıt JSON
  `api_iframe` (AYNI host olmalı, değilse ret) -> GET (Referer=sayfa) -> içteki `iframe[src]` provider adresi; beklenen host değilse "iframe yok".
  VidMoly sekmesi: `data-link` URL-güvenli base64 alfabesine çevrilip (`_safe_token` :62) `/api/moly/<token>` GET. Ders: aynı sayfadaki
  indirme bağlantıları `a[href*="/dl/"]` el sıkışmasız provider dosya kodu verir (Cloudflare'li ucu atlar) -> genel kural "indirme bağlantısı da aday".
- **JS ile kurulan çerez** (:241-249, 322-361): `main.min.js` içindeki `comolokko("udys", Date.now(), ...)` = milisaniye zaman damgası;
  `ci_session`'ı sunucu ilk çağrıda verir, aynı curl_cffi oturumunda tutulur -> tarayıcı gerekmez (tarayıcı oturumu 10+ sn). Tarayıcı yedeği
  YALNIZ engel görünümlü yanıtta: HTTP 401/403/429/503/520-526, JSON olmayan gövde, iframe'siz ara sayfa (= challenge); kesin "hayır"da
  (`api_iframe` yok + `error`) yedeğe gidilmez. Tarayıcı çerezleri + hafif çerez birleştirilir (ad çakışmasında tarayıcınınki).
- **Dil sekmeleri** (:22-60, 186-194): işaretlemede İKİ `class` özniteliği (`class="ui pointing item active" ... class="item"`); HTML
  ayrıştırıcı sonuncuyu tutar, `active` kaybolur -> etiket HAM metinden okunur (ilk öznitelik tarayıcının kullandığıdır). Varsayılan oynatıcı +
  alternatifler aktif sekmenin dilindedir; sekme yoksa ilk sekme. İndirme bağlantısı metni dosya başına dili söyler ("Türkçe/İngilizce Altyazılı
  İndir"). Etikete dil yalnız ÖLÇÜLEN (soft altyazı) sağlayıcıda girer; sıra ölçülmüş TR -> bilinmeyen -> EN; sitenin iddiası `site_lang_hint`;
  aynı dosyanın yeniden imzalı kopyası `tracks.file_identity` ile tek akış (`docs/arch/scraper-engine.md`).
- **Filmlerin gelmemesi**: eski sistemde film/dizi ayrımı sayfa içeriğinden değil `normalize.type{from_group, map, default}` URL regex'inden
  geliyordu; film sayfası = oynatma sayfası (`blocked on: episode_page` filmi de kapsar); `availability_gate` `screen` bütçesi (40 sayfa)
  aşılan film YAZILMAZ (`deferred`, `library/gate.py:439`) -> "film gelmiyor"un eski bir nedeni; TMDB varsayılanı yalnız film;
  aramayla gelen kayıt `library_lists`'e yazılmaz, `_added_at=0` (yeni satırında en sonda).
- **Yinelenen kategoriler**: eski sistemde tür adları merkezi `server/app/genres.py` etiket haritasından geçer, eşlenmeyen `genres_unmapped`'e
  ayrılırdı (ana alana girmez); kategori tek kaynak `home_categories.slug`; koleksiyon `category: <slug>` kayıtsızsa onboarding hatası
  (`ask_user`); üyelik `category_<slug>_<site>` list_id ile, siteler aynı slug'da birleşir (`library/categories.py`).
