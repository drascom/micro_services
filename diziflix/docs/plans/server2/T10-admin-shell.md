# T10: Admin kabuğu, Siteler, Site sayfası (`app/admin`)

**Bağımlı:** T05, T07; T08 ile birlikte yazılır (`status.overview` sözleşmesi T08'de). **Açar:** T11, T12, T14, T15 (her biri kendi
sekmesini/ekranını ekler). **Oku:** README §5.6; `NEW-SYSTEM-PLAN.md §4`; `server/app/static/admin/{index.html,app.js,style.css}` (stil ve
`api`/`esc` yardımcıları için), `server/app/routers/ops.py:26-45` (`_ASSETS` kalıbı). Hafıza notu (kullanıcı tercihi): teknik metrik yok,
uygulama akışına göre aşama ilerlemesi, sade.

## Amaç
Sade, işlevsel yönetim arayüzü. Bu görev kabuğu, Siteler ekranını ve Site sayfasının **Harita** ve **Sürümler** sekmelerini yazar.
Site ekleme, düzenleme ve onarım aynı ekrandır: aşama kartları.

## Yapılacaklar
1. `app/admin/assets.py`: `GET /admin` -> `index.html`; `GET /admin/{name}` yalnız kayıtlı adlar (`_ASSETS` kalıbı), `Cache-Control: no-cache`.
   Vanilla JS, build adımı yok. Dosyalar: `static/index.html`, `static/style.css`, `static/js/{app,sites,site,notes}.js`. Her ekran kendi
   js dosyasında; `app.js` yalnız yönlendirme (hash tabanlı: `#/sites`, `#/site/<id>/<sekme>`, `#/problems`, `#/settings`), `api()`, `esc()`,
   yoklama zamanlayıcısı.
2. **Üst şerit** (her ekranda): `çalışan işler: N · senden bekleyen: N · açık sorun: N` + okunmamış admin notları zili (açılır liste,
   "okundu" düğmesi). Kaynak: `GET /api/control/summary` (bu görevde; sorun sayısı T13 gelene kadar 0).
3. **Siteler** (`#/sites`): tablo satırı = site adı, harita durumu (`5/7 ✓`, takılan aşama rozeti `✋ A6` / `⚠ A5`), katalog ilerlemesi
   (T11 gelene kadar başlık sayısı), son tazeleme, açık sorun sayısı. Üstte "Site ekle": tek adres kutusu + düğme -> `POST /api/control/sites`
   -> Site sayfasına gider. Satır eylemleri: aç, duraklat/sürdür, sil (onay kutusu: "katalogdaki içeriği de sil").
4. **Site sayfası / Harita** (`#/site/<id>/map`): `GET /api/control/sites/{id}/stages` (= `status.overview`) 2 sn'de bir yoklanır (çalışan
   aşama varken; yoksa 10 sn). Aşama kartı:
   - başlık + durum rozeti (`bekliyor / çalışıyor / geçti / uyarıyla geçti / SENİ BEKLİYOR / motor boşluğu / atlandı`) + tek satır özet;
   - açılınca: ölçüm satırları (ad, değer, eşik, ✓/✗; Türkçe adlar), örnek adresler (bağlantı), "ne arandı / ne bulundu" kanıtı, mesaj
     geçmişi (sistem / sen / LLM kısa notu);
   - `SENİ BEKLİYOR` kartı açık gelir ve sayfanın en üstüne sabitlenir: ipucu kutusu + `[yeniden dene]`, `[örnek adres ver]`, `[bu alanı atla ▾]`
     (yalnız atlanabilir alanlar), `[aşamayı atla]` (yalnız isteğe bağlı aşama), `[motor boşluğu olarak kaydet]`.
   - `geçti` kartında `[yeniden çalıştır]` ve ipucu kutusu (edit modu: aynı bileşen).
   Yoklama sırasında kullanıcının yazmakta olduğu ipucu kutusu ve açık/kapalı kart durumu KORUNUR (DOM yeniden kurulmaz, yalnız değişen alanlar güncellenir).
5. **Site sayfası / Sürümler** (`#/site/<id>/versions`): sürüm listesi (no, tarih, kim: ekleme/düzenleme/onarım/admin, not), iki sürüm arası
   fark (`sitemap.diff`: yol, önce, sonra), `[bu sürüme dön]`, `[yaml indir]`, `[yaml içe aktar]` (taslak olarak).
   Haritanın ham yaml'ı salt-okur gösterilir (elle düzenleme alanı YOK: "siteye elle dokunma" kuralı).
6. Uçlar (`app/admin/routes_sites.py`):
   `GET/POST /api/control/sites`, `GET/DELETE /api/control/sites/{id}`, `POST .../pause`, `POST .../resume`,
   `GET .../stages`, `POST .../stages/{stage}/hint {text}`, `.../sample {url}`, `.../skip {field?}`, `.../rerun`, `.../gap {title}`,
   `GET .../versions`, `GET .../versions/diff?a=&b=`, `POST .../versions/{v}/rollback`, `GET .../export`, `POST /api/control/sites/import`,
   `GET /api/control/summary`. Girdi doğrulama: adres netguard'dan; metin alanları boyut sınırlı (ipucu <= 2000 karakter).
   Hata zarfı ortak biçimde, Türkçe `message`.
7. Stil: eski admin `style.css` temel alınır (aynı görsel dil). Mobil şart değil; 1280 px masaüstü.

## Kapsam dışı
Tarama, Yönlendirme, Sorunlar, Ayarlar, Katalog ekranları (T11, T12, T14, T15, T16 kendi js dosyasını ve menü/sekme kaydını ekler);
Kategoriler ve TMDB toplu doldurma ekranları (bu turda yok).

## Kabul
- `node --check` tüm js dosyalarında temiz; `/admin` ve varlıklar 200, kayıtsız ad 404.
- Uç testleri: site oluşturma (geçersiz/özel adres reddi), aşama eylemlerinin her biri doğru `control.stages.actions` çağrısına gider,
  bilinmeyen site/aşama 404, atlanamaz alan 400.
- `summary` sayıları kuyruk ve aşama durumlarıyla tutarlı (test: bir `needs_human` aşama -> "senden bekleyen: 1").
- Sürüm farkı ve geri dönme uçları T07 davranışını verir.
- Arayüzde ham yaml düzenleme alanı yok; teknik metrik (token, ms, selector sayısı) kartın kapalı halinde görünmez.

## Elle kontrol (kullanıcı)
Site ekleme akışı, kartların canlı güncellenmesi, ipucu yazarken yoklamanın metni silmemesi, `SENİ BEKLİYOR` kartının üste sabitlenmesi,
sürüm farkı okunabilirliği.
