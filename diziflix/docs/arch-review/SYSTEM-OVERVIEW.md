# diziflix: mevcut sistem, yaşam döngüsü, zayıflıklar ve hedef (mimari analiz girdisi)

Hazırlanma: 2026-10-03, bir geliştirme oturumunun sonunda (orkestratör ajan + worker ajanlar + kullanıcı). Amaç: başka bir ajanın
sistemi sıfırdan anlayıp **yeni bir mimari için analiz** yapabilmesi. Bu belge bir özet ve teşhistir; kod gerçeği kaynak koddadır.
Her önemli iddianın yanında okunacak dosya yazılıdır. **Doğrulama notu:** buradaki akışlar ve olay örnekleri bu oturumda koddan
ve canlı sunucudan (salt-okuma) çıkarıldı; yine de analiz yapan ajan kritik iddiaları kodda yeniden doğrulamalıdır. Doğrulanmamış
olanlar "(çıkarım)" diye işaretlidir.

---

## 1. Ürün ve asıl amaç

**Ne:** Ev ağında (LAN, tek kullanıcı/aile, kimlik doğrulama yok) çalışan dizi/film kataloğu ve oynatma sistemi. Bir sunucu
(FastAPI + SQLite, port 8090) birden çok dış web sitesinden (bölüm/dizi sayfaları) katalog toplar, kimlikleri birleştirir, TMDB ile
zenginleştirir ve istemcilere (web = `tizen-client/` tarayıcıda; ayrıca Samsung Tizen TV ve Android, şimdilik ertelendi) tek bir
Netflix benzeri arayüz sunar. Oynatma, sitelerin oynatıcı sayfalarından/sağlayıcılarından (OK.ru, Vidmoly, HLS, mp4, YouTube...)
akış adresi çıkarıp (gerekirse sunucu vekilinden geçirerek) yapılır.

**Asıl amaç (kullanıcının kararları, tasarımın pusulası):**
1. **Siteler sistemle eklenir, elle değil.** Yeni site "Site ekle" ile bir LLM ajanına (pi) verilir; ajan siteyi inceler, YAML yazar,
   doğrular. Siteye elle dokunma YOK. Bir site bozulunca sistem **kendini düzeltir** (heal); ajan bilgi bulamazsa kullanıcıya sorar.
2. **Modüler tasarım:** Site yaml'ı = sitenin TASARIMI (liste, detay, normalize, koleksiyonlar, oynatıcı nerede). Video host bilgisi
   (OK.ru, Vidmoly, streambox...) = yeniden kullanılabilir **provider/resolver kütüphanesi**. LLM bu ayrıma uymak zorunda; tek
   siteye özel hack yok.
3. **Yalnız halka açık içerik:** telif/erişim engelli ya da oynatıcısı bulunamayan dizi/bölüm kütüphaneye hiç alınmaz.
4. **Kullanıcı yorulmamalı:** kullanıcı hataları orkestratöre taşımak zorunda kalmamalı; oynatılamayan bir bölüm olursa kullanıcı
   bilgilendirilir, arkada sistem çözmeye çalışır, çözülünce haber verir ("Kaynak bulundu").
5. **Ekonomi:** LLM çağrıları ve worker token'ı pahalı: tarama LLM'siz (selector'larla), LLM yalnız onboarding'de ve onarımda.
6. **Odak:** sunucu + web istemcisi. Android/Tizen TV sonra.

Ölçek: tek kullanıcı, birkaç site (şu an: yabancidizi, ddizi, diziasya; trdiziizle ve sinemalar kaldırıldı), yüzlerce başlık.
Kod: ~38 bin satır Python (`server/app`), ~7.8 bin satır istemci JS, ~2.3 bin satır admin JS, 79 test dosyası / ~2700 test
(strateji gereği yalnız hedefli koşulur).

## 2. Bileşenler (kısa harita)

| Katman | Ne yapar | Ana dosyalar |
|---|---|---|
| Fetch | HTTP (Crawlee worker) ve tarayıcı (Obscura) ile sayfa getirir; yalnız siteye POST (koleksiyon) | `scraper/fetch.py`, `transport.py`, `crawlee_worker.py`, `obscura_worker.py` |
| Site config | `configs/<site>.yaml` + `.baseline.json` (sunucu referans; yerel dizin ayna, gitignore) | `scraper/config.py`, `runner.py` |
| Çıkarma | selectolax ile liste/detay/seri sayfası/koleksiyon/arama; şema, drift kontrolü | `scraper/runner.py`, `collections.py`, `series_generic.py`, `search_generic.py` |
| Resolver/provider | Oynatıcı sayfasından akış çıkarma: tipler `iframe, anchor_host, data_attr_token, ajax_handoff, json_api, player_page, embedded_json`; provider'lar (okru, vidmolly, yaml tarifleri) | `scraper/resolvers/`, `scraper/providers/`, `configs/providers/*.yaml` |
| Ingest | scraper -> normalize -> kimlik -> birleştirme -> TMDB -> `library_*` tabloları; tarama sonrası dizi bölüm envanteri | `library/ingest.py`, `normalize.py`, `identity.py`, `enrich.py`, `series_crawl.py`, `series_dir.py`, `gate.py` |
| Kütüphane | SQLite (WAL): `library_items`, `source_items`, `library_lists`, `library_seasons/episodes`, `video_sources`, `playback_attempts`, `playback_issues`, `stream_host_rules`, `finder_jobs`, `notifications`, `blocked_pages`, `home_categories`, profiller/izleme/liste | `db.py`, `sources/library.py`, `cache.py` |
| API | boot/rows/detail kademeleri, streams, arama, ilerleme, bildirimler; istemci sözleşmesi `API.md` | `routers/*`, `homelayout.py`, `rows.py` |
| Akış | kaynak çözümleme, sağlık (suspect/broken), akış vekili (mp4+HLS), tanı, süre doğrulama, host kuralları | `library/videos.py`, `streamproxy.py`, `hlsproxy.py`, `streamdiag.py`, `streamprobe.py`, `hostrules.py` |
| Onboarding ajanı | pi (LLM) + skill + extension; sandbox araçları; kriterler; ask_user; otomatik düzeltme turu; site kalitesi özeti | `scraper/onboard.py`, `onboard_pipeline.py`, `routers/onboard_sandbox.py`, `server/pi/` |
| Onarım (heal) | drift heal (liste/detay bozulunca) ve oynatma heal (çözüm/oynatma hataları) | `scraper/heal.py`, `heal_agent.py`, `playheal.py` |
| Kaynak bulucu | oynatılamayan bölüm için arka plan işi: retry, başka sitede ara, heal | `library/sourcefinder.py` |
| Admin | Olay defteri, Kütüphane, Ayarlar, Site ekle/Siteler, Kategoriler (vanilla JS) | `static/admin/`, `routers/ops*.py` |
| Deploy/ops | tar-over-ssh, config karar tablosu, yedek, geri alma, DB sıfırlama | `deploy.sh`, `clear-remote.sh`, `docs/arch/ops-deploy.md` |

Ayrıntılı mimari notlar: `CLAUDE.md` (kısa) ve `docs/arch/*.md` (konu başına). Wire sözleşmesi: `API.md`.

## 3. Yaşam döngüleri (mevcut)

### 3.1 Site ekleme (onboarding)
1. Admin "Site ekle": URL (+ ipucu) -> `scraper/onboard.py` tek slotlu bir **pi oturumu** başlatır (uzun ömürlü LLM oturumu; slot tek).
2. Ajan sandbox araçlarıyla (`fetch_page`, `query_html`, `grep_page`, `test_config`, `test_resolvers`, `test_search`, `submit_draft`,
   `ask_user`...) siteyi gezer, yaml yazar. Sandbox: `routers/onboard_sandbox.py` (localhost + token, SSRF `netguard`).
3. `test_config` rapor üretir: kriterler (`failing[]` + `hint`), uyarılar, tanılar, site kalitesi özeti (`report.site_quality`).
   Yeni site için sertleştirilmiş kriterler (`ONBOARD_HARDEN`): `playable_ratio`, `availability_gate_defined`,
   `series_signal_collection`, `series_full_inventory`, ... Başarısızsa **otomatik düzeltme turu** (`ONBOARD_AUTO_ROUNDS=2`) aynı
   oturumda ajana geri gönderir.
4. Ajan bilgi bulamazsa `ask_user` (`missing_info|decision|engine_gap`); kullanıcı cevap verir ("Sitede yok, atla: <alan>" vb.).
   `engine_gap` = motorda yetenek eksik; onay verilirse taslağa `engine_gaps` kaydı + admin olayı.
5. Taslak `ready` -> kullanıcı "Kaydet" -> yaml+baseline sunucuya yazılır (sürüm 1), ilk tarama başlar. Düzenleme modu ("edit"): kayıtlı
   siteye aynı akışla değişiklik; yeni sürüm.
6. Config'in referansı SUNUCUDUR (heal orada düzeltir); yerel `configs/` ayna (deploy/`--pull-configs` ile çekilir; git dışı).

### 3.2 Tarama ve ingest
- APScheduler her 60 sn "tick" (`autoscan.py`): ayarlanan aralıkta (site başına açık/kapalı + saat) siteyi tarar.
- `runner.run_site`: fetch -> parse -> şema -> baseline/drift kontrolü; drift'te **LLM heal** (`heal.py`) yaml'ı onarmayı dener.
- Ingest: normalize (anahtar/başlık temizliği), kimlik birleştirme (kanonik id), TMDB zenginleştirme (poster/backdrop/yıl/özet),
  kapı (`availability_gate`/`blocked`), dizi için bölüm envanteri (`series_crawl`/`series_dir`: dizi sayfası/dizin okunur, kota ve
  geri çekilme var). Sonuç `library_*` tablolarına; `cache.py` RAM'de `Snapshot`.
- Ana ekran: `homelayout.layout()` satırları (slider, continue, trending, tüm diziler, filmler, kategoriler, listem); yalnız
  oynatılabilir (`HOME_ONLY_READY`) başlıklar.
- Arama: istemci sorgusu -> tüm arama yetenekli sitelerde canlı arama (`library/search_all.py`) -> kanonik kimliğe çözülür -> sonuçlar
  dizi seviyesinde; arama ile eklenen başlık sonradan TMDB'ye girer.

### 3.3 Oynatma (bir bölüm açılınca)
1. İstemci `GET /api/streams/{id}?episode=...` ister (`routers/streams.py`).
2. `library/videos.py`: bölümün `video_sources` satırları (her biri bir site+bölüm sayfası) paralel çözülür: resolver zinciri
   (yaml `resolvers:`/`providers:`) bölüm sayfasından akış çıkarır (mp4/hls/embed). Süre sınırı + önbellek (`valid_until`).
   Sağlık: 1 hata = `suspect`, 3 hata = `broken`. Birden çok dosya varsa **içerik probu** süreye göre sıralar (`streamprobe.py`).
3. Akış istemciye doğrudan ya da **sunucu vekili** (`/api/stream-proxy/<token>`) üzerinden verilir: neden `ua|ip|recipe|learned|env`.
   `stream_host_rules` (host düzeyinde öğrenilmiş Referer/vekil, TTL 30 gün) ve `proxy_required` (kaynak başına) bunu belirler.
4. İstemci (hls.js/native/embed motoru `type`'a göre) oynatır. Hata olursa `POST /api/playback-report` (kod + detay + `hlsjs`) gönderir.

### 3.4 Hata yönetimi (bugünkü dört yol)
| Yol | Tetik | Ne yapar | Kapılar / sınırlar |
|---|---|---|---|
| **Kaynak bulucu** (`sourcefinder.py`) | `/api/streams` akış çıkaramadı, ya da istemci oynatma hatası raporladı | iş: `retry` (bilinen kaynakları zorla çöz) -> `search` (başka sitelerde ara) -> `heal` (ajan); bulununca `source_found` bildirimi | tek-uçuş, `SOURCEFINDER_COOLDOWN=3600`, `DAILY_BUDGET=10`, `MAX_SITES=4`; heal adımı yalnız "aday var ama akış yok / bilinmeyen host" izi varsa; heal meşgulse `HEAL_WAIT=600` bekler; başarılı heal sonrası 24 sa'lik `not_found` işleri yeniden |
| **Oynatma heal** (`playheal.py`, `heal_agent.py`) | çözümleme hata penceresi (`PLAYHEAL_WINDOW`, `MIN_SOURCES=3`, oran 0.6) ya da `playback_issues` eşiği (24 sa'de aynı sınıftan >=2 kaynak) | pi ajanı site yaml'ını/provider tarifini onarma önerir; `heal_autoapply` açıksa uygular | site başına tek heal, cooldown (`PLAYHEAL_ISSUE_COOLDOWN` 6 sa), LLM sağlığı, sandbox doğrulama + regresyon kapıları |
| **Drift heal** (`heal.py`) | tarama sırasında liste/detay şeması bozuldu | aynı ajan yaml'ı onarır | cooldown, uygulanmış/arşiv sürümleri |
| **Onboarding edit / admin** | kullanıcı "Düzenle" ile ajana bilgi verir | taslak -> yeni sürüm kaydı | **yukarıdakilerle bağlantısı yok** (bkz. zayıflıklar) |
Ek görünürlük: `playback_issues` tablosu (kaynak başına bir satır) -> admin Olay defteri `playback_issue` olayı; `stream_host_rules`
admin ucu; `finder_jobs` -> `finder` olayı; bildirimler `notifications`.

### 3.5 Config / deploy yaşam döngüsü
- Referans sunucu; yerel `server/app/scraper/configs/*.yaml` ayna (gitignore; `configs/providers/` izlenir, kod gibi).
- `deploy.sh`: sunucuda yedek -> fark/karar tablosu (yeni site: gönder; sunucu >= yerel ya da heal kaydı varsa: koru) -> tar-over-ssh -> import
  kontrolü -> **restart** -> uç doğrulama. Silinmiş siteler `deleted_sites.json` (tombstone) ile izlenir.
- Restart çalışan ajan koşularını ve taramaları KESER (bu oturumda iki kez canlı onboarding koşusu kesildi).
- Sunucu DB'sini sıfırlama: `clear-remote.sh` (artık `home_categories` korunur).

## 4. Zayıflıklar (bu oturumdaki kanıtlarla)

Aşağıdaki başlıklar "kod kokusu" değil, kullanıcının canlıda yaşadığı olaylardan çıkarılmıştır (bkz. §6 olay günlüğü).

**Z1. Aynı problemin birden çok giriş yolu, ortak durum yok.** Bir oynatma sorunu finder, oynatma heal, drift heal, onboarding edit
ve admin tarafından ayrı ayrı ele alınır; her birinin kendi kapısı/cooldown'u/bütçesi var ve birbirinin sonucunu bilmez. Örnek: kullanıcı
edit modunda config'i düzeltti (v2 kaydedildi); finder işi `not_found` + 1 sa cooldown'da kaldı; istemciye "bulundu" gitmedi.
"Bir site için çözülmemiş oynatma sorunu" diye tek bir kayıt/durum makinesi yok.

**Z2. Dar tetik kapıları: "sorun var ama kimse çalışmıyor".** Heal adımı yalnız "aday var ama akış yok / bilinmeyen host" izi varsa
çalışır. Sayfada hiç eşleşen oynatıcı yoksa (v1 yaml seçicisi `iframe[src*='/player/oynat/']` idi, sayfadaki iframe `/player/youtube.php?...` olduğu için eşleşmedi) heal atlandı, ama istemci "Kaynak aranıyor,
bulununca haber vereceğiz" diye söz vermişti. Söz ile arka plan işi arasında bağ yok.

**Z3. Motor boşlukları kayboluyor.** Ajan "motorda yetenek yok" bulgusunu yalnızca serbest notlara yazarsa (soru sormazsa) kimse
bilgilenmez; taslak "geçti" ile kaydedilir. Onaylı `engine_gap` bile bir süre çıkmaza giriyordu ("yetkim yok"). Uzun ömürlü pi oturumları
sonradan eklenen yetenekleri bilmiyor (diziasya: yeni `embedded_json` resolver'ı deploy edildi, eski oturum kullanmadı).

**Z4. Heuristikler gerçekliğe karşı doğrulanmıyor.** "twimg bir sosyal medya host'u, akış olamaz" kuralı gerçek bölüm akışını reddetti
(site bölümleri Twitter CDN'inde barındırıyor: 138.7 dk HLS = bölüm süresi). Heal ajanı bu yanlış girdiyle "kod gerekir" dedi. Sonradan
içerik doğrulaması (süre karşılaştırması) eklendi, ama karar mantığı hâlâ host/ad heuristikleriyle iç içe.

**Z5. Bilgi birçok yerde, tutarsız sahiplik.** Bir host'un davranışı (Referer, IP bağlılığı, vekil, HLS tuhaflığı) site yaml'ında, provider
tarifinde (`configs/providers/*.yaml`), DB'de (`stream_host_rules`, `proxy_required/proxy_headers`), kodda (`badhosts.py`, `streamdiag`) ve
LLM skill metninde dağınık. "Çözüm nerede yaşar?" belirsiz; öğrenilen çözüm bölümden bölüme taşınmıyordu (host kuralı sonradan eklendi).

**Z6. Yaşam döngüsü boyunca sürekli yama.** Çözümler tek tek eklendi (host kuralı, probe, heal bekleme, finder yeniden deneme, kalite özeti,
engine_gap kaydı); her biri yeni durum + yeni ayar + yeni admin görünümü getirdi. Sistem; `config.py`'de yüzün üzerinde env okuması, 7 resolver tipi, 2 fetch modu
(`http` = Crawlee, `browser` = Obscura), 3 heal türü (drift, oynatma, finder), çok sayıda cooldown/bütçe/eşik taşıyor. Kullanıcı "çok karışık tasarlamış olabiliriz" diyor.

**Z7. Uzun ömürlü ajan oturumlarının bayat bilgisi + token maliyeti.** Pi oturumu saatlerce yaşar; skill/extension boyut bütçesi (`test_pi_size_budget`)
yüzünden bilgi sıkıştırılıyor; ajan "pes etme çıkışlarını" (needs code, engine_gap) kolay kullanıyor; her görev worker'ı 100-450K token harcıyor.

**Z8. Operasyonel kırılganlık.** Restart ajan/tarama koşusunu keser (deploy öncesi kontrol elle yapılıyor, bir kez atlandı); config
referansı sunucuda, yerelde ayna; silinmiş site tombstone'u deploy'la etkileşiyor; testler artık yerelde olmayan site config'lerine
bağlı (12 bilinen düşüş; config'ler gitignore olunca temiz klonda daha çok düşer).

**Z9. Ana ekran sıralamasında kaynak dengesizliği.** "Haftanın Trendleri" doldurması site ayırt etmez, `trend_score` tazeliği ağır basar;
`added_at` = DB'ye ilk giriş olduğundan yeni/yeniden taranan site trendleri kaplar. (Plan hafızada: site başına eşit pay.)

**Z10. Gözlemlenebilirlik dağınık.** Kullanıcı bir olayın durumunu (açık/onarılıyor/çözüldü/geliştirici bekliyor) tek yerden göremiyor;
admin Olay defteri farklı türleri (`scan`, `heal`, `finder`, `playback_issue`, `onboard`) ayrı ayrı gösteriyor.

## 5. Sık karşılaşılan hatalar (belirti -> kök neden -> bugünkü işlem -> boşluk)

| # | Belirti | Kök neden (örnekler) | Bugünkü işlem | Boşluk |
|---|---|---|---|---|
| E1 | "Kaynak çalışmıyor" (mp4/HLS 403) | Akış host'u Referer ister (streambox) ya da IP'ye bağlı | `streamdiag` Referer dener, `proxy_required` + host kuralı öğrenir | ilk deneme her zaman bir kez başarısız (proaktif öğrenme yok) |
| E2 | Doğru akış reddediliyor | Heuristik host reddi (twimg) | host listesi daraltıldı; süre probu | heuristik/doğrulama ayrımı net değil (Z4) |
| E3 | Bölümde oynatıcı bulunamıyor | Oynatıcı seçicisi/tipi uymuyor (iframe `/player/` vs YouTube, gömülü JSON, ajax) | resolver tipleri eklendi (`player_page`, `embedded_json`) | her yeni yapı bir motor yeteneği gerektirir; heal kapısı bu durumda atlıyor (Z2) |
| E4 | Tam bölüm YouTube videosu | YouTube yalnız fragman için işleniyor; `embed` akış türü var ama YouTube-bölüm provider'ı yok | yok (bu oturumda teşhis edildi) | motor boşluğu kaydı yok (Z3) |
| E5 | Cloudflare/challenge sayfası | oynatıcı sayfası doğrulama istiyor | `fetch_mode: browser` (Obscura), `warm_session` | site kalitesi özeti "zayıf" der, otomatik çözüm yok |
| E6 | Heal "çalışıyor/meşgul" -> iş pes ediyor | site başına tek heal | finder artık bekler ve yeniden dener | heal süresi dakikalar; kullanıcı "aranıyor" ekranında bekler |
| E7 | Başlık/kimlik bozuk ("Halef 37.Bölüm") | bölüm kartı dizi sayfası sanılıyor | `clean_series_slug`, `series_dir` ile dizi sayfası çözümleme | site başına yeni örüntü çıkabilir |
| E8 | Posterde yer tutucu (`none.png`), TMDB yok | site yer tutucu verdi; arama ile eklenen başlık TMDB'ye girmiyordu | yer tutucu tespiti + arka plan TMDB | — |
| E9 | Aramada sonuç yok | form/POST araması (ddizi `arama=`), GET boş | koleksiyon/arama POST desteği eklendi | her yeni biçim yeni yetenek |
| E10 | "Tüm site kayboldu/değişti" | drift (HTML değişti) | LLM drift heal, baseline | heal sonucu doğrulaması sınırlı; elle düzeltme yasağı |
| E11 | Ajan takılıyor/soru döngüsü | bilgi eksik, onay akışı, bayat oturum | `ask_user`, engine_gap kaydı, katalog yeniden okuma | uzun oturum kırılganlığı (Z7) |
| E12 | Ana ekran dengesizliği | trend doldurma/tazelik (Z9) | yok (plan var) | — |
| E13 | Deploy sonrası ajan/tarama kesildi | restart | elle kontrol | otomatik "çalışan iş var" kapısı yok |

## 6. Olay günlüğü (kanıt, 2026-10-02/03)

- **Anne Yarısı (trdiziizle) 1. bölüm:** HLS `streambox.xyz` Referer'sız 403; sunucudan `Referer: trdiziizle.tv` ile 200. Sistem öğrenmedi
  (proxy öğrenmesi yalnız akışın kendi başlığı varsa çalışıyordu). Sonra `stream_host_rules` ile çözüldü: 1. bölüm bir kez başarısız
  oldu, finder 3 sn'de buldu, host kuralı öğrenildi.
- **Anne Yarısı 2. bölüm:** çözümlenen akış `video.twimg.com/.../*.m3u8` (138.7 dk). Eklediğimiz "ilgisiz host" reddi onu eledi -> "Video sağlayıcısı
  çözülemedi"; heal ajanı yanlış kanıtla "kod gerekir" dedi. Düzeltme + içerik probu eklendi. Ders: doğrulanmamış varsayım regresyon yaratır.
- **Başkalarının Hayatı / Ömür Usta:** aynı kök neden; tek heal 4 dk çalışırken diğer finder işi "heal çalışıyor" diye `not_found` bitirdi;
  heal sonrası yeniden deneme yoktu (sonra eklendi).
- **Evlilik Güzeldir 2. bölüm (ddizi):** sayfada `iframe src="https://www.ddizi.re/player/youtube.php?id=viMWJEvO_qc"` (resmi TRT1 kanalı yüklemesi,
  oEmbed 200). v1 yaml'ındaki `iframe[src*='/player/oynat/']` seçicisi bu iframe'e (`/player/youtube.php`) uymuyordu -> "Sayfada video sağlayıcısı bulunamadı"; finder: `retry` yok, `search` zaman aşımı,
  `heal` atlandı. Kullanıcı edit modunda bilgi verdi; ajan seçiciyi `iframe[src*='/player/']` olarak genişletti, YouTube'u buldu ama çözücü olmadığı için "YouTube için resolver kodu
  gerekiyor" diye yalnız NOTA yazdı, taslak "geçti" kaydedildi. Config v2 yazıldı, finder işi güncellenmedi, istemciye "bulundu" gitmedi,
  yeniden denemede çalışmadı. (Z1, Z2, Z3 aynı olayda.)
- **Diziasya:** kaynak URL'leri yalnız gömülü Next.js JSON'unda; ajan `engine_gap` sordu, onay sonrası "yetkim yok" dedi. `embedded_json` resolver'ı
  eklendi; eski pi oturumu yeni tipi bilmediği için taslak yine bekletildi; çözüm: taslağı silip yeni oturumla yeniden eklemek.
- **ddizi onboarding'i iki kez deploy restart'ıyla kesildi** (çalışan ajan kontrolü atlandı).
- **Trend satırı:** canlı veride yalnız `trending_yabancidizi` listesi vardı; ddizi/diziasya yalnız `latest_*`/`category_*` üretti; doldurma tazelik
  skoruyla yeni siteyi öne çıkardı.

## 7. Kısıtlar (yeni mimarinin uyması gerekenler)

- Tek kullanıcı/LAN, kimlik doğrulama yok; SQLite; FastAPI; tek sunucu (Linux, systemd `diziflix`); uvicorn `--reload` yok.
- İstemci-sunucu **wire sözleşmesi** (`API.md`) korunur; yalnız alan eklenir. Mevcut web istemcisi çalışmaya devam etmeli.
- Yalnız halka açık içerik; `availability_gate`/`blocked` kuralı bozulmamalı.
- Siteler elle düzeltilmez; sistem düzeltir, bilgi yoksa kullanıcıya sorar; kullanıcıdan "yok" cevabı = alanı atla.
- LLM: pi (global, gerçek model sunucuda); tarama LLM'siz kalmalı; ajan çağrıları sınırlı bütçeli.
- Config referansı sunucu; elle config düzeltmesi yok; yalnız yeni site config'i yerelden deploy'la gider.
- Test stratejisi: küçük smoke set + sözdizimi kontrolü; tam takım koşulmaz; kullanıcı manuel test eder.
- Worker ajanlar görsel test yapmaz; deploy'u kullanıcı/orkestratör ister; her deploy restart + uç doğrulaması içerir.
- Odak: sunucu + web istemcisi; Android/Tizen TV ertelendi.

## 8. Analiz edilecek sorular (öneri)

1. **Tek problem kaydı:** "(site, problem sınıfı, kapsam)" için tek bir yaşam döngüsü (açık -> teşhis -> onarılıyor -> doğrulanıyor -> çözüldü /
   geliştirici bekliyor) kurulursa finder, heal, onboarding edit, admin ve istemci bildirimi bunun üzerine nasıl oturur? Mevcut tablolar
   (`video_sources`, `playback_issues`, `finder_jobs`, `notifications`, heal kayıtları) nasıl birleşir/sadeleşir?
2. **Teşhis ile eylemin ayrılması:** Sayfa ne içeriyor (oynatıcı adayları, iframe/script/JSON özeti) -> hangi katman çözer (yaml / provider /
   motor yeteneği / site korumalı) kararı tek bir "teşhis" aşamasında mı verilmeli; "heal tetik kapısı" bu kararın çıktısı olmalı mı?
3. **Bilgi sahipliği:** Host/sağlayıcı davranışı (Referer, vekil, IP, tür) için tek bir kütüphane/kayıt mı? Site yaml'ı yalnız "nerede" mi
   söylemeli? Öğrenilen çözüm (şu an DB) kütüphaneye terfi edebilir mi (onay/doğrulama kapısıyla)?
4. **Doğrulama ilkesi:** Heuristik reddetmek yerine ölçüm (içerik probu: süre/çözünürlük/şifre/erişim) ile sıralama; karar noktalarında "kanıt" zorunlu mu?
5. **Motor yetenek boşluğu yönetimi:** Ajan bir yetenek eksikliği bulduğunda bunun kayıt altına alınması (iş listesi), kullanıcıya görünürlüğü ve
   yeteneğin eklenince bekleyen işlerin otomatik devam etmesi nasıl tasarlanır? Uzun ömürlü ajan oturumlarının bayat bilgisi (kısa oturum + durum
   özeti/yeniden başlatma) çözülebilir mi?
6. **Sadeleşme:** Hangi resolver tipleri, fetch/transport yolları, ayarlar, cooldown/bütçe parametreleri birleştirilebilir/kaldırılabilir? Hangi
   özellikler (örn. üç ayrı heal türü) tek bir "onarım işi" soyutlamasına inebilir?
7. **Kullanıcıya söz verme:** İstemcideki "Kaynak aranıyor / bulununca haber vereceğiz" sözü, arkada gerçekten çalışan ve zaman aşımına/sonuca
   bağlanan bir iş olmadan verilmemeli; bunu mimari olarak nasıl garanti ederiz?
8. **Operasyon:** Restart sırasında çalışan işlerin yarım kalması, config referansı/ayna modeli, tombstone, DB sıfırlama; daha güvenli bir
   deploy/iş kuyruğu modeli (devam ettirilebilir işler)?
9. **Ana ekran adaleti:** Çok kaynaklı sıralamada (trend/slider/yeni) kaynak adaleti ve "yeni tarama = yeni içerik" yanılgısı için model.
10. **Geçiş:** Küçük, geri alınabilir adımlarla (mevcut API ve veriyi koruyarak) nasıl geçilir? İlk 3 adım ne olmalı, hangi risk önce ölçülmeli?

## 9. Okunacak yerler

- `CLAUDE.md` (diziflix): kararlar + mimari haritası. `docs/arch/{onboarding,onboarding-quality,scraper-engine,library-ingest,streams,home-and-api,clients,ops-deploy}.md`.
- Akış/hata: `server/app/library/{videos,streamdiag,streamprobe,hostrules,playissues,sourcefinder}.py`, `server/app/{streamproxy,hlsproxy}.py`, `server/app/scraper/{playheal,heal_agent,heal}.py`.
- Onboarding: `server/app/scraper/{onboard,onboard_pipeline,discover,sitequality}.py`, `server/app/routers/onboard_sandbox.py`, `server/pi/` (skill + extension).
- Scraper: `server/app/scraper/{runner,fetch,transport,collections,series_generic,search_generic}.py`, `resolvers/`, `providers/`, `configs/providers/`.
- Veri: `server/app/db.py` (SCHEMA + `_migrate`), `server/app/library/ingest.py`, `homelayout.py`, `rows.py`.
- Sözleşme: `API.md`, `docs/DATA-CONTRACT-V1.md`, `server/CANONICAL.md`. Planlar: `docs/plans/site-onboarding/`.
- Ayarlar: `server/app/config.py` (env listesi), `server/.env.example`, `server/README.md`.
