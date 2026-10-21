# Geliştirmeler — zigbee2mqtt Karşılaştırması ve Durum Takibi

Son güncelleme: 2026-07-31 (TS0043 çoklu tuş olayı incelemeye alındı)
İlgili commit: `e1d0e87 zigbee: add endpoint bindings and scene management`
Son Linux birleştirme: `eaa0241 Merge documented Zigbee parity fixes`
Son çalışma doğrulaması: yerelde `npm test` 111/111 geçti

Bakış açısı: **geliştirici olmayan, basit ev kullanıcısı** — kurulum ve günlük
kullanımda fiilen hissedilen eksikler.

**Durum:** 26 madde tamamlandı · 3 madde ertelendi · 1 açık cihaz olayı sorunu var.

---

## ✅ Tamamlananlar

Uçtan uca doğrulandı: backend mantığı + HTTP API + arayüz + test.

| Özellik | Referans |
|---|---|
| Perde / panjur (`cover`, pozisyon + aç/kapa/dur) | `src/device-controls.ts:235-245`, `src/home-assistant-discovery.ts:76-101`, `public/index.html:590-596,610-611` |
| Termostat / TRV (`climate`, hedef sıcaklık + mod) | `src/device-controls.ts:246-254`, `src/home-assistant-discovery.ts:103-133` |
| Akıllı kilit (`lock`) | `src/device-controls.ts:255-263`, `src/home-assistant-discovery.ts:135-148` |
| Siren (`siren`) | `src/device-controls.ts:274-284`, `src/home-assistant-discovery.ts:176-189` |
| Cihaz ayar entity'leri (`number` / `select`, admin kısıtlı) | `src/device-controls.ts:285-292`, `src/home-assistant-discovery.ts:191-224`, `src/index.ts:482-484` |
| Router üzerinden eşleştirme | `src/direct-zigbee-source.ts:159-164`, `src/index.ts:243-262`, `public/index.html:249,795-802` |
| Install code ile eşleştirme | `src/direct-zigbee-source.ts:166-169`, `src/index.ts:264-276` |
| Ağ yedeği al / geri yükle (rollback kopyası dahil) | `src/zigbee-backup.ts`, `src/index.ts:662-707`, `src/direct-zigbee-source.ts:382-388` |
| Gerçek availability (Router 15 dk / pilli cihaz 36 saat) | `src/direct-zigbee-source.ts:39-50,696-713` |
| Cihaz notu | `src/device-notes.ts`, `src/index.ts:141-154` |
| Ağ haritası (router LQI komşu tabloları) | `src/direct-zigbee-source.ts:276-298`, `public/index.html:2207-2231` |
| Olay geçmişi (kalıcı, 200 kayıt) | `src/device-events.ts`, `src/device-store.ts:171-225` |
| MQTT LWT (last will) | `src/direct-zigbee-source.ts:653-658` |
| Favori / hızlı kontrol / grup için çoklu cihaz türleri | `public/index.html`, `src/dashboard-copy.test.ts` |
| Shadow mod grup kimliği normalizasyonu | `src/mqtt-source.ts`, `src/mqtt-source.test.ts` |
| Direct mod MQTT retain ve yinelenen durum debounce'u | `src/direct-zigbee-source.ts`, `src/direct-zigbee-source.test.ts` |
| Kısmi cihaz ayarlarında `null` yazımının engellenmesi | `src/index.ts`, `src/direct-zigbee-source.ts` |
| Yalnız pozisyon sunan perde için HA discovery | `src/home-assistant-discovery.ts`, `src/home-assistant-discovery.test.ts` |
| Gerçek Zigbee grubuna tek aç/kapat komutu | `src/source.ts`, `src/direct-zigbee-source.ts`, `src/mqtt-source.ts`, `src/index.ts`, `public/index.html` |
| OTA güncelleme denetimi ve direct mod ilerleme durumu | `src/source.ts`, `src/direct-zigbee-source.ts`, `src/mqtt-source.ts`, `src/index.ts`, `public/index.html` |
| Endpoint bazlı bind/unbind ve mevcut binding listesi | `src/types.ts`, `src/device-store.ts`, `src/direct-zigbee-source.ts`, `src/mqtt-source.ts`, `src/index.ts`, `public/index.html` |
| Adlandırılmış Zigbee sahnelerini listeleme, oynatma ve silme | `src/types.ts`, `src/device-store.ts`, `src/direct-zigbee-source.ts`, `src/mqtt-source.ts`, `src/index.ts`, `public/index.html` |
| Ayarlanabilir düşük pil eşiği, backend uyarısı ve kalıcı geçiş olayı | `src/config.ts`, `src/settings-store.ts`, `src/device-store.ts`, `public/index.html` |
| Home Assistant stateless buton `event` entity'leri | `src/device-store.ts`, `src/home-assistant-discovery.ts`, `src/direct-zigbee-source.ts` |
| Zigbee kanal değişikliğinde UI ve API seviyesinde açık risk onayı | `src/settings-store.ts`, `src/index.ts`, `public/index.html` |
| Direct cihaz yeniden yapılandırma regresyon testi | `src/direct-zigbee-source.test.ts` |

Komut zinciri kapalı: UI `command()` → `POST /api/devices/:id/command`
(`src/index.ts:473-517`) → `source.setDevice` → `convertSet`
(`src/direct-zigbee-source.ts:840-886`).

---

## ✅ Son kapatılan hatalar

| # | Sonuç |
|---|---|
| H1 | Favoriler, Quick Control ve dashboard grupları fan, siren, perde, kilit ve klima kontrollerini destekliyor. Kilitler güvenlik nedeniyle toplu açma komutuna dahil edilmiyor. |
| H2 | Shadow modda `group-N` kimliği z2m'in beklediği `N` değerine dönüştürülüyor. |
| H3 | Direct mod cihaz durumları yapılandırılmış `retain` değerini kullanıyor; aynı payload debounce süresinde tekrar yayınlanmıyor. |
| H4 | Yalnız gönderilen cihaz seçenekleri YAML'a yazılıyor; eksik alanlar `null` üretmiyor. |
| H5 | Pozisyon-only perdeler HA discovery'de var olmayan `state` alanına bağlanmıyor. |

---

## 🐞 Açık cihaz olayı sorunları

| # | Cihaz / UID | Bulgular | Sonraki doğrulama |
|---|---|---|---|
| R1 | Tuya TS0043 `0x20a716fffe6835f1` | Tanım 3 endpoint ile `1/2/3 × single/double/hold` olaylarını doğru sunuyor. Villa Bridge `actionTypes` bilgisini alıyor ancak arayüz bunu üç olay kanalına dönüştürmüyor. Son canlı denemede tuş basışları coordinator'a ulaşmadı ve `lastSeen` değişmedi. | Cihaz coordinator yakınındayken ham mesaj izleme açık olarak tekrar test edilecek. Mesaj akışı doğrulanınca genel `actionChannels` modeli ve üç tuşlu arayüz uygulanacak. |

Teşhis için çözümlenemeyen `genOnOff` komutlarını endpoint, cluster, mesaj tipi
ve payload ile kaydeden genel bir log noktası eklendi. Cihaza özel converter veya
kanıtsız binding uygulanmadı.

---

## ⏸️ Ertelenenler

Bu maddeler mevcut kullanım için acil görülmedi; bilerek sonraki sürüme bırakıldı.

| # | Madde | Ertelenen kapsam |
|---|---|---|
| Y6 | Fan | Sayısal hız HA'ya hiç gitmiyor — `src/home-assistant-discovery.ts:192` `fan:` önekini `number`'dan da dışlıyor, `percentage_*` alanları üretilmiyor |
| Y9 | Touchlink | `identify` yok — ampulü sıfırlamadan önce doğrulama adımı mümkün değil. scan + factory reset var (`src/direct-zigbee-source.ts:184-194`) |
| Y11 | MQTT permit_join | `bridge/request/permit_join` payload'ındaki `device` alanı yok sayılıyor (`src/direct-zigbee-source.ts:802-808`) — router seçimi sadece HTTP'de |

---

## Sıradaki iş önceliği

Bu karşılaştırma listesindeki zorunlu işler tamamlandı. Y6, Y9 ve Y11 ancak ilgili
cihaz veya kurtarma senaryosu gündeme geldiğinde yeniden değerlendirilecek.

### Y1 uygulama notu

Dashboard grubunun aç/kapat kontrollü cihaz üyeleri gerçek bir Zigbee grubuyla
birebir eşleştiğinde `POST /api/groups/:id/command` üzerinden tek grup komutu
gönderilir. Zigbee ağına kaydedilmemiş sanal gruplar ve farklı kontrol
kümelerini karıştıran gruplar işlev kaybetmemek için cihaz-başı komut yolunu
kullanmaya devam eder.

### Y4 uygulama notu

Cihaz kartına güncelleme denetimi eklendi. Shadow mod z2m'in OTA check
isteğini kullanır; direct mod Herdsman ile sürüm uygunluğunu denetler.
Planlanmış direct güncelleme cihazın OTA isteği geldiğinde başlar ve ilerleme,
kalan süre, tamamlanma veya hata cihaz state'i üzerinden arayüze yayınlanır.

### Y2 uygulama notu

Kaynak ve hedef cihaz için endpoint seçimi API ve iki kaynak modunda desteklenir.
Shadow mod z2m'in `from_endpoint` / `to_endpoint` alanlarını kullanır. Direct
mod Herdsman endpoint'ini seçer. Koordinatörde kayıtlı binding'ler cihaz
görünümüne taşınır, yönetim ekranında hedef ve cluster bilgisiyle listelenir ve
tek tek kaldırılabilir.

### Y3 uygulama notu

Sahne kaydında ID yanında kullanıcı adı da alınır. Shadow mod z2m'in
adlandırılmış `scene_store` biçimini kullanır. Direct mod sahneyi Zigbee
grubuna gönderir ve liste bilgisini Herdsman grup metadata veritabanında
saklar; `configuration.yaml` özel alanlarla kirletilmez. Kayıtlı sahneler
arayüzden çağrılabilir veya silinebilir.

---

## Küçük notlar (kozmetik / kırılganlık)

- `POST /api/devices/:id/command` kontrolü **property** ile buluyor
  (`src/index.ts:480`); aynı property'e sahip iki kontrol olursa ilki seçilir.
  Şu an çakışma yok ama kırılgan.
- Min/max'ı olmayan `number` kontrolü UI'da 0–100 slider'a düşüyor
  (`public/index.html:611`) — gerçek aralık farklıysa yanıltıcı.
- `deviceGlyph` (`public/index.html:500`) climate/termostat ikonu içermiyor,
  "•" fallback'e düşüyor.
- `permitJoin` clamp tutarsızlığı: MQTT yolu 0-254, HTTP yolu 10-254
  (`src/index.ts:248`).
- Olay geçmişi UI'da yalnız ana ekran widget'ında **son 5** kayıt gösteriliyor
  (`public/index.html:855-860`); tam geçmiş ekranı yok.
- Ağ haritası fonksiyonunun testi yok; EndDevice'ın parent'ı ancak router
  komşu tablosunda görünürse listeleniyor.

---

## Ev kullanıcısını ilgilendirmeyen, atlanabilir eksikler

External converter desteği · `configured_reportings` raporlaması · `legacy_api` ·
ban / blocklist / passlist · çoklu adapter · `pan_id` ve network key
değiştirme · `coordinator_check` · birth/will mesaj yapılandırması ·
`bridge/request/*` ailesinin permit_join dışındaki komutları.
