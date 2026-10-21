# Faz 3: Admin "Site ekle" sekmesi

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

**Önkoşul:** Faz 2c bitmiş olmalı (`/api/ops/onboard/*`, `GET /api/ops/resolvers`, `kind=onboard` olayı).
**Görsel test YAPMA.** Yalnızca `node --check` ve sunucu testleri. Görsel kontrolü kullanıcı yapacak.

## Mevcut admin yapısı (`server/app/static/admin/`, vanilla JS, build yok)
- `index.html` (sekme düğmeleri satır 16-19 civarı, paneller ve script etiketleri), `app.js` (genel poll:
  `/api/ops/active` her 2.5 sn, overview her 30 sn, `renderBanner`, olay defteri `evHtml/evPill/visible/renderDetail`,
  önce/sonra diff ~232), `library.js` (`TABS`, `TITLES`, `showTab`, hash yönlendirme ~29 ve ~286), `settings.js`
  (`call()`/`save()` yardımcıları, sekme kancası `window.dzTabHooks.X = onShow` ~405, gizliyken polling yok),
  `style.css`.
- Statik dosyalar `routers/ops.py` `_ASSETS` beyaz listesinden servis edilir. Yeni js dosyası buraya eklenmeli.

## Yapılacaklar

### 1. Yeni sekme `#onboard` ("Site ekle")
- `index.html`: düğme `data-tab="onboard"`, `id="tab-btn-onboard"`; panel `id="tab-onboard"`; `<script src="onboard.js">`.
- `library.js` `TABS`/`TITLES`'a ekle. `ops.py` `_ASSETS`'e `onboard.js` ekle.
- `onboard.js` (yeni), `window.dzTabHooks.onboard = onShow`. Mevcut dosyaların kalıplarını (DOM yardımcıları,
  `call()`, hata gösterimi, toast varsa onu) kopyala ya da paylaş.

### 2. Görünümler

**a) Başlangıç / liste**
- Sağlık kartı (`GET /api/ops/onboard/health`): pi ✓/✗, skill ✓/✗, model adı, LLM hesabı. Bir sorun varsa
  "Başlat" düğmesi pasif ve neden yazılı.
- Form: URL (zorunlu), "Not" (isteğe bağlı; örn. "yalnızca filmler", "liste sayfası /filmler").
  "Başlat" → `POST /api/ops/onboard`.
- Geçmiş taslaklar tablosu: URL, durum rozeti, tarih, tur sayısı, "Aç"/"Sil".

**b) Taslak detayı** (`#onboard/<draft_id>` hash'i)
- **Durum başlığı:** rozet (`running`/`needs_input`/`ready`/`failed`/`cancelled`/`saved`), süre, "İptal" (yalnızca
  running).
- **Ajan günlüğü:** `events` zaman sıralı ve kompakt.
  - `tool` → "🔧 test_config…"
  - `tool_result` → ✓/✗ + özet
  - `say` → ajan metni
  - `user` → kullanıcı mesajı (sağa hizalı)

  Running iken her 2 sn `GET /{id}?events_after=n` ile artımlı yükleme yapılır. Sekme gizliyse polling durur.
  Otomatik aşağı kaydırma yalnızca kullanıcı en alttaysa.
- **Sonuç paneli** (`report` varsa, `ready`/`needs_input`):
  - Kriterler listesi: her biri ✓/✗ + değer (örn. "Geçerli öğe 18 ≥ 8 ✓").
  - Örnek kartlar: ilk 5 öğe; poster küçük resmi `/img` proxy üzerinden ya da yalnız metin, başlık, yıl, tür,
    `source_key`.
  - Alan doluluk çubukları.
  - Normalize: ok/ret sayıları, ret nedenleri, tekrar eden anahtarlar.
  - Resolver denemesi: aday sayısı, çözülen ve başarısız olanlar (provider adıyla).
- **Yaml görünümü:** salt-okunur `<pre>`, kopyala düğmesi. Sürüm 1 kapsamında elle düzenleme yok; değişiklikler
  geri bildirimle yapılır.
- **Resolver seçimi:** `GET /api/ops/resolvers` kataloğunu göster. Ajanın seçtiği tipler işaretli. Kullanıcı bir tip
  ekler ya da çıkarırsa bu otomatik bir geri bildirim mesajına dönüşür ("resolvers listesinden X'i çıkar, Y ekle").
  Doğrudan yaml düzenleme yok.
- **Geri bildirim kutusu:** metin + "Gönder / yeniden üret" → `POST /{id}/message`. Running iken pasif.
- **Kaydet bölümü** (`ready` ise aktif; `passed=false` ise uyarı + "Yine de kaydet" onay kutusu → `force`):
  - `site_id` (ajanın önerisiyle dolu, desen doğrulaması istemcide de yapılır), `display_name`,
    "Otomatik taramayı aç" (varsayılan kapalı).
  - "Kaydet" → `POST /{id}/save`.
  - Başarılıda bilgi: "Site eklendi (v1). Ayarlar sekmesinden taramayı açabilir ya da 'Şimdi tara' ile
    deneyebilirsin." + Ayarlar'a link.
  - Not: "Yerel kopya bir sonraki deploy'da otomatik çekilecek" (Faz 4).

### 3. Olay defteri entegrasyonu (`app.js`)
- `kind=onboard`: `evPill` (örn. mor "Site ekle"), `evHtml` (URL, durum, `site_id`), `visible` süzgecine ekle,
  `renderDetail` (özet + "Taslağı aç" linki `#onboard/<id>`).
- `renderBanner`: `kind=onboard` aktif işi için çubuk ("Site ekleniyor: <host> · <label>").

### 4. Stil
- `style.css`'e yalnızca gerekli sınıflar. Mevcut değişkenler/renkler kullanılır. Günlük alanı sabit yükseklikli,
  kaydırılabilir.

## Testler
- `node --check server/app/static/admin/onboard.js` ve değişen diğer js dosyaları için.
- Sunucu: `_ASSETS`'e eklenen dosya `GET /admin/onboard.js` ile 200 dönüyor (mevcut ops test kalıbına bir test ekle).
- Mevcut testler yeşil.

## Kabul kriterleri
- Sözdizimi kontrolleri ve testler geçiyor. Diğer sekmelerin davranışı değişmemiş.
- Rapor: eklenen görünümler ve kullanıcının görsel kontrolde bakması gereken 5-8 maddelik liste (hangi ekran, ne
  görmeli).
