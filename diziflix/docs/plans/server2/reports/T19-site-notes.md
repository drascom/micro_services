# T19 raporu: site notu (kullanıcının site tanımı)

**Yapılan**
- **Saklama:** `018_site_notes.py` -> `sites.notes`, `notes_updated_at` (+ `notes_applied_at`: aşamaların notla en son koştuğu an). Yalnız DB, ≤4000 karakter (`control/sitemap/site_notes.py`).
- **Uçlar:** `GET/PUT /api/control/sites/{id}/notes` (fazlası 400), `POST .../notes/rerun`; `POST /api/control/sites` isteğe bağlı `notes` alır.
- **Admin:** "Site ekle" formunda açılır "Site hakkında (isteğe bağlı)" metin kutusu; Harita sekmesinin üstünde her zaman görünen "Site notu" + Kaydet (sayaç); A1 onay kartında aynı düzenleyici. Kartlarda "Site notundan kullanılan: …" satırı; not değişince kartlarda "Not değişti — aşamaları notla yeniden çalıştır" (A1 kuyruğa, sonraki koşmuş aşamalar `pending`, zincirle sırayla; A1 onayından mevcut etiketlerle geçer). Kaydetmek hiçbir aşamayı sıfırlamaz. Çıktılar `A.esc`; `node --check` temiz.
- **LLM:** not `scrub`lanıp kırpılarak "Kullanıcının site notu" bloğu olarak tüm tam prompt'ların EN BAŞINA girer (aşama turları, oturum kaybı yedeği, onarım, yönlendirme önerisi): sabit önek, önbelleklenebilir. Siteye göre okunur, başka sitenin prompt'una girmez.
- **LLM'siz kullanım** (`stages/hints.py` genelleştirildi): cümledeki sayfa sözcüğü (dizi/bölüm/ana/liste sayfası) + alan sözcüğü (afiş…, `episode_list`, `player`) + seçici; adres + çevresindeki sözcük:
  - A1: arşiv/liste adresi -> onay kartında "adres ekle" satırı olarak önceden doldurulur (`series_list|film_list` = `content: title`, `bölüm` -> `episode_list`); menüde varsa o bağlantının önerisi olur. Kullanıcı yine onaylar.
  - A3: başlık sayfası seçicileri keşifte ipucundan sonra, keşiften önce ölçülür (bölüm sayfasınınki A3'e girmez); `/diziler/<slug>/` -> `url_pattern` + `key` adayı, örneklerin hepsine uyarsa kullanılır.
  - A4: "bölüm listesi .x" -> `.x li|.x a|.x` satır adayı (en az keşif kadar bölüm okursa kazanır). A5: "oynatıcı #x" -> `iframe` çözücüsü başa. A7: "arama /?s=<kelime>" -> `search.url: /?s={query}` ilk aday (form yedek).
- **Testler:** `tests/test_t19_site_notes.py` (7 test, ~0.4 sn; biri `node tests/js/cardsync_test.js`); `test_t10_admin` metin kutusu sayısı 1 -> 3 (ipucu, site notu, Site ekle notu).

**Sapmalar**
- Fazladan kolon `notes_applied_at` ("not değişti" bayrağı için). Oturum içi 2. tur / biçim düzeltmesi prompt'larına not tekrar eklenmez (aynı oturumda ilk prompt'ta zaten var).
- A2 nottan aday almaz (onaylanan A1 etiketleri A2'yi zaten sürer). Notta reddedilen öneri ("sil") her A1 koşusunda yeniden önerilir.
- A4/A5 not adayları yalnız birim düzeyinde elle sınandı (test bütçesi); A7 not adayı ölçüm için bilinen başlık ister.

**check.sh:** YESIL (6 adım), 305 test, ~11.1-11.4 sn. **Elle kontrol (kullanıcı):** Site ekle notu, Harita üstü not kutusu/Kaydet, A1 kartında önerilen adres satırları, "Not değişti" düğmesi, "Site notundan kullanılan" satırları.

## Kullanıcı hatası: sayfa kayması
- Neden: `place()` her 2 sn'lik yoklamada tüm kartları `appendChild` ile yeniden taşıyordu (odak düşer, sayfa kayar) + `sdata` tümden yeniden çiziliyordu.
- Düzeltme: yeni `static/js/cardsync.js` (saf karar: bölüm başına JSON anahtarı; yalnız değişen bölüm çizilir); kart yalnız sıra gerçekten değişince taşınır; güncellemede ekranın üstündeki kart sabitlenir (`scrollTo` ile telafi), yoklamada focus/scrollIntoView yok.
- Odak kartın alanındaysa o bölüm dokunulmaz; son input/change'ten 3 sn kart hiç güncellenmez; A1 onay formu (seçimler, eklenen satırlar) değiştirilmişse yeniden çizilmez; kanıt/listelerin iç kaydırması ve kartın açık/kapalı durumu korunur.
- Elle kontrol: başka kartta ipucu yazarken, A1 açılır listelerinde ve not kutusunda yoklama sırasında kayma/odak kaybı olmamalı.

## Akordeon
- Harita sekmesindeki aşama kartları (sabitlenen + liste tek grup) en çok 1 açık: bir kartın başlığına tıklayınca diğerleri kapanır, aynı karta yeniden tıklamak kapatır (0 açık olabilir). Tıklanan kart ekranda yerinde kalır (`scrollTo` ile telafi edilir).
- Yoklama açık/kapalı durumunu hiç değiştirmez (eski "SENİ BEKLİYOR olunca aç" kaldırıldı). Yalnız ilk yüklemede kayıtlı kart, yoksa ilk SENİ BEKLİYOR kartı açılır; admin bir kez tıkladıktan sonra otomatik açma yok.
- Seçim `sessionStorage` `admin.accordion.<site>.map` = `{open, touched}` (try/catch). Saf karar `cardsync.accordionPick/accordionToggle`, test `tests/js/cardsync_test.js`. Tarama sekmesinde açılır öğe yok (tablo), değişiklik yapılmadı.
