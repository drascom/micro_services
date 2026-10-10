# diziflix: yeni sistem planı (site haritası + aşamalı tarama + tek sorun kutusu)

Tarih: 2026-10-03. Karar (kullanıcı): mevcut sistemi yamalamak yerine yönetim katmanı yeniden tasarlanır. Girdi: `SYSTEM-OVERVIEW.md`,
`ARCHITECTURE-PROPOSAL.md` (teşhis geçerli; oradaki "evrimsel sarma" önerisi yerine bu plan geçer, `problems` durum makinesi buraya taşınır).

**Güncel karar ve görevler: `docs/plans/server2/README.md`.** Bu belgeden sonra alınan kararlar (ayrı `server2/` klasörü, port 8091,
site taşıma yok, siteye özel kod yok, tam aktarım 3 gün, 10 sn onay, `/admin2` yerine kendi `/admin`'i, haritanın DB'de saklanması) orada
yazılıdır; çelişkide o belge geçerlidir.

Durum: TASLAK. Kod okunarak doğrulanmamış varsayımlar "(doğrulanacak)" ile işaretli; Faz 0 bunları netleştirir.

## 1. Tek cümle

Bir site, **sayfa türlerinden oluşan bir harita** olarak öğrenilir; her sayfa türü ayrı bir **aşamadır** (kendi durumu, kanıtı, sohbeti);
tarama, düzenleme, bozulma tespiti ve onarım hep aynı haritanın aynı aşamasına işaret eder; arka plandaki her iş tek bir kalıcı kuyruktan,
her sorun tek bir kutudan geçer.

## 2. Neyi çözüyor (bugünkü sorun -> yeni karşılığı)

| Bugün | Yeni |
|---|---|
| Onboarding tek uzun ajan oturumu; hata "bir yerde" | 7 kısa aşama; hata hangi aşamada/sayfa türünde olduğu belli |
| Site değişince tüm yaml'a heal | Yalnız bozulan sayfa türünün aşaması yeniden çalışır |
| Edit ayrı bir akış, diğerlerinden kopuk | Edit = kayıtlı sitede aynı aşama kartını yeniden çalıştırmak |
| Finder + playheal + drift heal + edit: 4 yol, ortak durum yok | Tek `problems` kutusu, tek durum makinesi |
| İşler bellekte, restart siler | Tek `jobs` tablosu, kiralamalı; restart'ta kaldığı yerden |
| Tarama "ana sayfa + arama"; tam katalog planı yok | 3 kademe: öne çıkanlar (hemen) -> planlı tam aktarım -> günlük tazeleme |
| "Şuradan gelen şu gruba" dağınık (yaml + kategoriler) | `routing_rules` tablosu; LLM önerir, admin onaylar, uygulama LLM'siz |
| ~120 env ayarı, çok eşik | Site başına 3 ayar (tam aktarım süresi, günlük ajan bütçesi, tazeleme saati) |

## 3. Çekirdek kavramlar

### 3.1 Site haritası (site modeli)

Site yaml'ı "sitenin tasarımı" olmaya devam eder, ama **sayfa türüne göre** düzenlenir. Video host bilgisi yine provider kütüphanesindedir.

```
 home ──▶ listing (kategori / tür / arşiv, sayfalama) ──▶ title (film | dizi detay)
   │                                                        │
   └────────────── öne çıkan bölümler ─────────────────────▶├─▶ film: player
                                                            └─▶ dizi: season ─▶ episode ─▶ player ─▶ sources (provider kütüphanesi)
 search (varsa) ──▶ title
```

Her sayfa türü (node) için saklanan:

| Alan | Anlam |
|---|---|
| `url_pattern` + `samples[3-5]` | Bu türün adres kalıbı ve doğrulamada kullanılan örnek sayfalar |
| `extract` | Alan -> seçici (başlık, afiş, yıl, tür, özet, bölüm no, ...) |
| `links` | Çocuk türe giden bağlantı seçicileri (+ sayfalama) |
| `fingerprint` | Yapısal imza (seçicilerin tuttuğu düğüm sayısı / DOM iskelet özeti); değişim tespiti için |
| `expect` | Kabul ölçütü (örn. "liste >= 10 öğe", "bölüm sayfasında >= 1 kaynak adayı") |
| `skipped[]` | Kullanıcının "sitede yok, atla" dediği alanlar |

Saklama: `configs/<site>.yaml` (sürümlü, sunucu referans; kural değişmez). Çıkarma motoru, resolver ve provider kütüphanesi kütüphane olarak
yeniden kullanılır; yeni model ile mevcut yaml anahtarları arasındaki eşleme Faz 0'da çıkarılır (doğrulanacak).

### 3.2 Aşamalar (onboarding = edit = onarım)

| # | Aşama | Çıktı | Kabul (ölçümle) |
|---|---|---|---|
| A1 | Ana sayfa | bölümler (öne çıkan, yeni, trend), menü bağlantıları | >= 1 bölüm, her bölümde >= 5 kart |
| A2 | Listeler | kategori/tür listesi, sayfalama, toplam sayfa tahmini | 2 örnek listede >= 10 öğe, 2. sayfa açılıyor |
| A3 | Başlık detayı | film/dizi ayrımı, başlık, afiş, yıl, tür, özet | 3 örnekte zorunlu alanlar dolu |
| A4 | Sezon/bölüm listesi | sezonlar, bölüm bağlantıları, numaralar | 2 dizide bölüm sayısı > 0, numaralar sıralı |
| A5 | Bölüm/film oynatıcı sayfası | oynatıcı adayları (iframe/script/JSON) | 3 örnekte >= 1 aday |
| A6 | Video kaynakları | aday -> provider -> akış | 3 örnekte akış çözüldü + yoklama 200 + süre makul |
| A7 | Arama + telif kapısı | arama biçimi, `availability_gate`/`blocked` | bilinen bir başlık aranıp bulunuyor |

Her aşama aynı üç adımla çalışır:

1. **Keşif (LLM'siz):** örnek sayfaları getir; bağlantıları adres kalıbına göre kümele, tekrar eden DOM bloklarını bul, bilinen provider
   host'larını eşle. Çoğu sitede A1-A2 burada biter.
2. **Öneri (LLM, yalnız gerekince):** tek aşamalık, **taze ve kısa** oturum. Girdi sunucunun hazırladığı özet: kırpılmış HTML örneği,
   keşif bulguları, kabul ölçütü, varsa kullanıcının ipucu. Çıktı yalnız o sayfa türünün `extract`/`links` alanları.
3. **Doğrulama (LLM'siz, ölçüm):** öneri örnek sayfalarda çalıştırılır, kabul ölçütüne bakılır. Geçmezse en çok 2 tur daha; sonra
   aşama `insan bekliyor` olur.

Aşama durumları: `bekliyor -> çalışıyor -> geçti | uyarıyla geçti | insan bekliyor | motor boşluğu | atlandı`.

**İnsan + LLM:** `insan bekliyor` durumundaki aşama kartında kanıt (örnek adres, ne arandı, ne bulundu) ve tek bir giriş kutusu vardır.
Kullanıcı ipucu yazar ("oynatıcı Kaynak 2 sekmesinin altında"), örnek adres verir ya da "yok, atla" der; aşama o ipucuyla yeniden çalışır.
Sohbet aşamaya bağlıdır, genel sohbet yoktur. **Edit modu** aynı ekrandır: kayıtlı sitede bir aşamayı ipucuyla yeniden çalıştırmak yeni
config sürümü üretir. **Onarım** da aynıdır: imzası bozulan sayfa türünün aşaması ipucu olmadan yeniden çalışır.

Hız: aşama başına süre sınırı (keşif 30 sn, LLM turu 120 sn); örnek sayfalar paralel getirilir; A3 geçince öne çıkan başlıklar hemen
kataloga girer (A4-A7 arkada sürer). Hedef: sorunsuz bir sitede ilk içerik 3-5 dk içinde görünür (ölçülecek, taahhüt değil).

### 3.3 Tek iş kuyruğu

```sql
jobs(id, kind, key UNIQUE, site, state,        -- queued | running | done | failed | waiting
     step, payload, attempts, run_after, lease_until, error, created_at, updated_at)
```

- Türler: `stage` (A1-A7), `crawl` (bir sayfa grubu), `refresh`, `repair`, `verify`, `tmdb`.
- Tek yürütücü döngü; iş `lease_until` ile kiralanır, adım bitince `step` yazılır. Restart: kirası dolan iş kaldığı adımdan sürer.
- Site başına aynı anda tek LLM işi; LLM'siz işler paralel. Bellekte durum yok.
- `deploy.sh` restart'tan önce "süren adımı bitir, yeni iş alma" sinyali gönderir.

### 3.4 Kademeli tarama

```sql
crawl_frontier(site, url, page_type, state,   -- new | done | failed | gone
               priority, discovered_at, crawled_at, next_at, fail_count, fingerprint_ok)
```

| Kademe | Ne zaman | Ne taranır | LLM |
|---|---|---|---|
| K0 Öne çıkanlar | Onboarding A3 geçer geçmez | Ana sayfa bölümlerindeki başlıklar (+ dizilerde son bölümler) | yok |
| K1 Tam aktarım | Kayıttan sonra, planlı | Listelerden keşfedilen tüm başlıklar; günlük kota = kalan / kalan gün | yok |
| K2 Günlük tazeleme | Her gün | Ana sayfa + listelerin ilk sayfaları + süren dizilerin yeni bölümleri | yok |

- Admin tek ayar verir: "tamamı N günde" (varsayılan 7). Kota ve hız (istek arası bekleme) bundan türetilir.
- İlerleme ölçülür: keşfedilen / aktarılan / başarısız / halka kapalı (kapıdan elenen).
- Telif kapısı aynen: oynatıcısı olmayan ya da engelli başlık/bölüm kataloga girmez; yalnız sayılır.
- Her taranan sayfada imza kontrolü: aynı sayfa türünde art arda K sayfa tutmazsa tarama o türde durur, `problems`'a **site yapısı
  değişti: <sayfa türü>** kaydı açılır; diğer sayfa türleri çalışmaya devam eder.

### 3.5 Yönlendirme kuralları ("şuradan gelen şu gruba")

```sql
routing_rules(id, site, match_kind,   -- section | listing | genre | url_pattern | type
              match_value, target_group, priority, enabled, created_by, note)   -- created_by: llm | admin
```

- Onboarding sonunda LLM, haritadaki bölüm/liste/tür adlarını mevcut gruplarla eşleştiren bir **öneri tablosu** üretir (tek çağrı).
- Admin tabloda onaylar/değiştirir. Serbest metin de yazabilir ("Kore dizileri Asya grubuna gitsin"): LLM bunu kurala çevirir,
  sistem **önizleme** gösterir (kaç başlık etkilenir, 5 örnek), admin onaylar.
- Uygulama ingest sırasında LLM'sizdir. Kural değişince etkilenen başlıklar yeniden gruplanır (yeniden tarama gerekmez).
- Mevcut `home_categories` hedef grup listesi olarak kalır (doğrulanacak).

### 3.6 Tek sorun kutusu

`ARCHITECTURE-PROPOSAL.md` §6.1-6.3'teki `problems` / `problem_events` / `problem_watchers` / `capability_gaps` modeli aynen alınır. Farklar:

| Konu | Bu planda |
|---|---|
| Kaynaklar | (1) istemci: oynatma hatası (mevcut `playback-report`), "sorun bildir" (yeni, ek alan/uç); (2) sistem: tarama imza bozulması, kırık afiş (`/img` 404), eksik zorunlu alan, kaynağı kalmayan bölüm; (3) admin |
| Hedef | Her sorun haritada bir **sayfa türüne** bağlanır (`problems.page_type`); onarım o aşamayı çalıştırır |
| Onarım sırası | LLM'siz (yeniden çöz, host kuralı, başka sitede ara) -> aşama onarımı (LLM) -> admin'e sor -> motor boşluğu |
| Çift doğrulama | **Sistem:** ölçüm (akış çözüldü + yoklama + süre). **Kullanıcı:** bildirene "düzeltildi" bildirimi; aynı içerik hatasız >= 2 dk oynarsa kendiliğinden onaylı, yeni hata gelirse sorun yeniden açılır; istemcide açık evet/hayır düğmesi sonraki istemci turunda |
| Admin müdahalesi | `waiting_user` durumunda soru kartı (aşama kartıyla aynı bileşen) |
| Söz | İstemciye "aranıyor" yalnız açık ve son tarihi olan sorun varken döner; her sorun bir sonuçla kapanır ve bildirilir |

## 4. Admin arayüzü (4 ekran, vanilla JS)

Üst şerit her ekranda: `çalışan işler: 2 · senden bekleyen: 1 · açık sorun: 3`.

**1. Siteler**
```
 Site          Harita      Katalog              Tazeleme     Sorun
 yabancidizi   7/7 ✓       ████████░░ 82%  3g   bugün 04:00  0
 ddizi         6/7 ⚠ A6    ███░░░░░░░ 31%  5g   bugün 04:10  2
 [+ Site ekle: adres ______ ]
```

**2. Site sayfası** (onboarding, edit ve onarım aynı ekran). Sekmeler: Harita · Tarama · Yönlendirme · Sürümler
```
 A1 Ana sayfa      ✓ 4 bölüm, 62 kart                         [örnekler] [yeniden çalıştır]
 A2 Listeler       ✓ 14 kategori, ~310 sayfa
 A3 Başlık detayı  ✓ 3/3 örnek
 A4 Sezon/bölüm    ✓ 2/2 dizi
 A5 Oynatıcı       ⚠ 2/3 örnek  (1 örnekte aday yok)
 A6 Kaynaklar      ✋ SENİ BEKLİYOR
      Ne oldu: 3 örnekte aday bulundu, 2'si çözüldü; "player/youtube.php" için provider yok.
      Örnek: https://.../evlilik-guzeldir-2-bolum
      [ ipucu yaz ____________________ ] [yeniden dene] [bu türü atla] [motor boşluğu olarak kaydet]
 A7 Arama + kapı   … bekliyor
```
Tarama sırasında kartlar canlı güncellenir (yoklama ile; SSE şart değil). Tarama sekmesi: K0/K1/K2 ilerlemesi, günlük kota, "N günde tamamla".
Yönlendirme sekmesi: kural tablosu + serbest metin kutusu + önizleme. Sürümler: config sürümleri, fark, geri al.

**3. Sorunlar** (tek kutu; en üstte "senden bekleyen")
```
 Durum          Nerede                              Ne                       Bildiren   Yaş
 ✋ seni bekl.   ddizi · A6 Kaynaklar                provider yok: youtube    1 profil   2 g
 onarılıyor     diziasya · A3 Başlık detayı         yapı değişti (imza)      sistem     4 dk
 doğrulanıyor   yabancidizi · Halef 37. bölüm       akış 403 -> host kuralı  1 profil   10 sn
 ✓ çözüldü      ddizi · Anne Yarısı 1               kullanıcı onayı bekliyor 1 profil   1 sa
```
Satır açılınca olay zaman çizgisi + kanıt + (gerekiyorsa) soru kartı.

**4. Ayarlar:** LLM hesabı/sağlığı, TMDB, varsayılan tam aktarım süresi, günlük ajan bütçesi. Başka ayar yok.

## 5. Mevcut koddan ne kalır

| Karar | Modüller |
|---|---|
| **Kalır (kütüphane olarak)** | `scraper/fetch.py` + worker'lar, çıkarma motoru (selectolax), `resolvers/`, `providers/` + `configs/providers/`, `library/videos.py` çözümleme çekirdeği, `streamproxy`/`hlsproxy`, `streamdiag`/`streamprobe`/`hostrules`, `identity`, `normalize`, TMDB (`tmdb`/`enrich`/`seasons`), `gate.py`, `netguard`, `db.py` katalog tabloları, `cache.py`, `homelayout.py`, tüm istemci API'si (`API.md` yalnız alan ekler) |
| **Yeniden yazılır (yeni paket `app/control/`)** | onboarding (`onboard.py`, `onboard_pipeline.py`, `routers/onboard_sandbox.py` 4554 satır), `autoscan.py`, `series_crawl` yürütmesi, `sourcefinder.py`, `playheal.py`, `heal.py`, `heal_agent.py`, admin paneli, pi skill/extension (aşama başına küçük prompt) |
| **Kalkar** | `finder_jobs`, bellek pencereleri/sayaçları, `_ops.json` durum kaynağı, finder/playheal/heal eşik ayarları (~25 env), uzun pi oturumu |
| **Veri** | Katalog, profil, izleme, liste tabloları korunur. Mevcut 3 site yeni sistemle **yeniden eklenir** (elle geçiş yok; sistemin ilk gerçek sınavı) |

Sınır kuralı: `app/control/` eski yönetim modüllerini import etmez; yalnız "kalır" satırındaki kütüphaneleri kullanır. Kesişim noktaları
(özellikle `onboard_sandbox` içindeki analiz/kriter fonksiyonlarından hangileri kütüphaneye çıkarılacak) Faz 0'da listelenir.

## 6. Yapım sırası

Eski sistem kesim gününe kadar çalışmaya devam eder; yeni panel `/admin2` altında büyür. Her faz tek başına kullanılabilir bir şey bırakır.

| Faz | Kapsam | Sonunda görünen | Doğrulama |
|---|---|---|---|
| 0 | Kod envanteri: yeniden kullanım sınırı, yaml eşlemesi; `jobs` tablosu + yürütücü; `/admin2` kabuğu | Boş Siteler ekranı, çalışan kuyruk | smoke: iş kiralama/restart devamı |
| 1 | Site haritası modeli + A1-A3 (keşif, LLM önerisi, ölçümle doğrulama) + aşama kartları + ipucu kutusu + K0 | Yeni site eklenir, öne çıkan başlıklar katalogda | kullanıcı bir siteyi ekler |
| 2 | A4-A7 + mevcut resolver/provider ile oynatma; motor boşluğu kaydı | Eklenen siteden bölüm oynar | kullanıcı 3 bölüm oynatır |
| 3 | `crawl_frontier`, K1 tam aktarım, K2 tazeleme, imza kontrolü, Tarama sekmesi | İlerleme çubuğu, günlük kota | 2 gün izleme |
| 4 | `routing_rules`: LLM önerisi, serbest metin -> kural, önizleme | Yönlendirme sekmesi | kullanıcı 2 kural yazar |
| 5 | `problems`: teşhis, onarım sırası, çift doğrulama, bildirim, Sorunlar ekranı; istemci "aranıyor" sözü buna bağlanır | Tek sorun kutusu | iki eski olay (Evlilik Güzeldir, Anne Yarısı) yeniden oynatılır |
| 6 | Kesim: 3 site yeniden eklenir, eski modüller + eski admin kaldırılır, testler `tests_archive/`'e, `/admin2` -> `/admin` | Tek sistem | smoke set < 20 sn |

Edit modu ayrı faz değildir: Faz 1'deki "aşamayı yeniden çalıştır + ipucu" kayıtlı sitede de çalışır.

## 7. Kullanıcıdan beklenen kararlar

| # | Karar | Öneri |
|---|---|---|
| 1 | Mevcut 3 site: yeniden ekle mi, yaml'ı çevir mi? | Yeniden ekle |
| 2 | Yeni sistem aynı sunucuda `/admin2` olarak mı büyüsün, ayrı servis mi? | Aynı sunucu, aynı DB |
| 3 | Tam aktarım varsayılan süresi | 7 gün |
| 4 | LLM onarımı ölçüm doğrulamasını geçince kendiliğinden uygulansın mı? | Evet; geçemezse admin'e sor |
| 5 | Kullanıcı doğrulaması: "2 dk hatasız oynatma = onay" yeterli mi, istemcide düğme şart mı? | Şimdilik örtük onay; düğme istemci turunda |
| 6 | "Sorun bildir" (eksik bilgi/kırık bağlantı) istemci düğmesi şimdi mi? | Sunucu ucu Faz 5'te; düğme istemci turunda. O zamana kadar otomatik sinyaller |
| 7 | Motor boşluğu çıkınca kim kapatır? | Orkestratör + worker (kod işi); admin'de "geliştirici bekliyor" görünür |

## 8. Riskler

- **Yeniden kullanım sınırı doğrulanmadı:** `onboard_sandbox.py` içindeki analiz kodunun ne kadarı kütüphaneye çıkarılabilir bilinmiyor; Faz 0 ölçer. Sınır kötü çıkarsa Faz 1 uzar.
- **Keşfin LLM'siz başarı oranı bilinmiyor:** A1-A2'nin çoğu sitede LLM'siz geçeceği varsayım; ilk 3 sitede ölçülür.
- **İmza kontrolünün yanlış alarmı:** eşik (art arda K sayfa) ilk haftada ayarlanır; yanlış alarm LLM çağrısı tetiklemez, önce LLM'siz yeniden doğrulama yapılır.
- **Çift sistem dönemi:** kesime kadar iki admin paneli var; eski sistemde yeni özellik geliştirilmez.
- **Tam aktarımın site yükü:** kota + istek arası bekleme ile sınırlı; engellenme belirtisinde (challenge, 429) tarama o site için durur ve sorun açılır.
