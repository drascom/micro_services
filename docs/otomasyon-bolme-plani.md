# `public/js/panel-automation.js` bölme planı

Dosya: 3706 satır, tek dosya, `NN-` öneki yok (panel-graph sıra denetiminden bu yüzden muaf).
Tüm üst düzey bildirimler 2 boşluk girintili — `scripts/panel-graph.mjs` bu girintiye bakıyor.
Paylaşılan taslak durumu dosyada değil, `state.automationWizard` içinde (`10-core.js:175`).
Dosyaya özel değişken durumu yalnız 4 `let`: `automationAnimate`, `automationHoldQuiet`,
`automationAdvanceTimer`, `automationPickChoosing`.

---

## 1. Mevcut dosyanın işlev haritası

| # | Bölüm | Satır | Adet |
|---|---|---|---|
| A | Kumanda/değer ilkelleri: `isAutomationControl`, değer kumandaları (level/temperature/color), yüzde↔ham ölçekleme, "tetikleyeni izle", cihaz sekmeleri, arama, eylem adı/etiketi, `automationSensorEvents` tablosu | 1–207 | 207 |
| B | `automationTriggerEvents` — cihazın yeteneğinden tetikleyici satırı türetme (button / sensor / deviceState) | 208–257 | 50 |
| C | Cümle katmanı: olay etiketi, eşleme görünümü (`automationMapView`), güneş eşlemesi, eşik cümlesi, `automationSentence`, `automationCardLine`, süre metinleri, "sonra kapat" satırı, koşu rozeti, kart HTML'i | 258–530 | 273 |
| D | Liste + sunucu IO: ajan şeridi, `renderAutomations`, koşu listesi aç/kapa, `loadAutomations`, `persistAutomations` | 531–620 | 90 |
| E | Evin konumu: yükle/çiz/yaz, konum yöneticisi diyaloğu, hava konumundan al | 621–716 | 96 |
| F | Sebep/sonuç metinleri, kart menüsü işleri: etkin/pasif, şimdi çalıştır, sil, çoğalt | 717–804 | 88 |
| G | Sihirbaz modeli: tetikleyici seçenekleri, id/ad üretimi, `automationWizardTrigger(s)`, hedef/grup/sahne modeli, aşama listeleri, `openAutomationWizard`, `automationActionToTarget` | 805–1130 | 326 |
| H | Akış HTML ilkelleri: pill/özet/seçenek/ekle satırları, gün şeridi, saat kadranı, sayaç, güneş bloğu | 1131–1253 | 123 |
| I | **Sayısal keşif + eşik**: `automationNumericProperties`, özellik etiketi/birimi, otomatik adım, eşik kadranı ve HTML'i | 1254–1316 | 63 |
| J | Koşullar: satır türetme (`automationConditionRows/AllRows`), önizleme, zaman noktası metni, `automationConditionLine`, taslak↔yük dönüşümü, koşul HTML blokları (mod, uç, zaman, eşik, tazelik, süre, durum) | 1317–1672 | 356 |
| K | **Cihaz keşfi (2. küme)**: düğme ipucu, sensör sayısal satırları, değer kanalı satırları, `automationTriggerRows`, seçim grupları, cihaz simgesi | 1673–1717 | 45 |
| L | Seçici + hedef HTML: sekme şeridi, cihaz listesi, tetikleyici ayrıntısı, hedef parçaları, değer editörü, eşleme, gecikme/bekleme, grup/sahne, "sonra kapat", ad, yol; akış düğümleri (`when/cond/wait/then/after`), `automationFlowHtml` | 1718–2393 | 676 |
| M | Bağlama: basılı-tut sayacı, `automationBindBody` (tüm `data-automation-*` sevk tablosu), arama girişi, sekme seçimi | 2394–2555 | 162 |
| N | Gezinme: ilerleme zamanlayıcısı, `automationRedraw`, `automationAdvance`, hazır/sonraki aşama/geri/engel sebebi, `automationSyncFoot`, `renderAutomationWizard` | 2556–2755 | 200 |
| O | Sihirbaz işleyicileri: `chooseAutomation*` / `stepAutomation*` / `setAutomation*` / `commit*` / ekle-sil, `nextAutomationStep`, `saveAutomationWizard` | 2756–3706 | 951 |

**Dışa açılan adlar (diğer panel dosyaları kullanıyor):**
`99-bind.js` → `openAutomationWizard`, `closeAutomationWizard`, `nextAutomationStep`,
`stepBackAutomation`, `cancelAutomationAdvance`, `deleteAutomation`, `duplicateAutomation`,
`runAutomationNow`, `revertAgentAutomations`, `loadAutomations`, `loadHomeLocation`,
`chooseHomeLocation`, `saveHomeLocationForm`, `openHomeLocationManager`, `useWeatherLocationForHome`
· `90-shell.js` → `loadAutomations`, `renderAutomations`, `renderAutomationWizard`,
`renderHomeLocation`, `renderHomeLocationDialog` · `88-simple-link.js` → `renderAutomations`,
`renderAutomationWizard`, `openAutomationWizard`, `chooseAutomationTriggerDevice`, `automationJoin`
· `40-home.js` → `renderAutomations`, `refreshAutomationHint` · `80-zigbee-tools.js` →
`loadAutomations` · `45-clock-weather.js` → `chooseHomeLocation` · `30-device-view.js` →
`automationPressKeys`.

**İçe bağımlılıklar:** `10-core.js` (`state`, `t`, `esc`, `api`, `showToast`, `ago`,
`reducedMotion`, `weatherState`) · `30-device-view.js` (`deviceKind`, `deviceButtonName`,
`deviceButtonPressLabel`, `deviceSeenPress`, `visiblePresses`, `lightPanelParts`,
`lightColorPresets`) · `45-clock-weather.js` (`locationName`, `renderLocationSearchResults`,
`resetLocationSearch`) · `88-simple-link.js` (`isProtectedDevice`, `simpleLinks`, `openSimpleLink`,
`removeSimpleLink`) · `90-shell.js` (`activateView`).

**Küresel kapsam gerçeği:** klasik `<script>`'lerin üst düzey `let/const/function` bildirimleri tek
ortak küresel sözcüksel kapsama girer, yani dosyalar arası referans **çalışma anında** serbesttir.
Otomasyon kodunun tamamı kullanıcı etkileşimiyle çalışır (yükleme anında çalışan tek yer
`99-bind.js` içindeki `initialize()`), bu yüzden bölme **TDZ riski taşımaz**. Tek gerçek kısıt:
`99-bind.js` en sonda kalmalı ve sayısal önekler azalmamalı.

---

## 2. Önerilen bölme — 6 dosya

`panel-automation.js` kaldırılır, yerine 91–96 gelir (97, 98 ileriye pay olarak boş kalır).

| Dosya | Sorumluluk (tek cümle) | Tahmini satır | Kaynak bölümler |
|---|---|---|---|
| `91-automation-devices.js` | **Cihaza özel motor**: bir cihazın bildirdiği kumanda/özellik/duruma bakıp hangi tetikleyici, koşul ve eylem seçeneklerinin mümkün olduğunu türetir — HTML yok, sihirbaz durumu yok, sunucu çağrısı yok. | ~355 | A, B, I'nin keşif yarısı (1254–1292), J'nin satır türetme yarısı (1321–1334), K |
| `92-automation-text.js` | Kaydedilmiş bir kuralı insan diline çevirir: olay etiketi, özet cümlesi, kart satırı, eşleme cümlesi, süre/sebep/sonuç metinleri. | ~365 | C, F'nin metin tabloları (717–740), zaman noktası metni (1366–1393), `automationConditionLine` (1394–1452) |
| `93-automation-list.js` | **Genel iş akışı — liste tarafı**: kural listesi ve kartları, koşu geçmişi, sunucuyla konuşan katman (`loadAutomations`/`persistAutomations`), kart menüsü işleri ve evin konumu. | ~270 | D, E, F'nin işleyicileri (741–804) |
| `94-automation-wizard-model.js` | **Genel iş akışı — model**: sihirbaz taslak nesnesi, tetikleyici/koşul/hedef yapıları, taslak↔sunucu yükü dönüşümü, aşama listeleri ve "hazır mı / sıradaki aşama / geri" kuralları. | ~505 | G, koşul taslağı (1453–1539), aşama kararları (2606–2699) |
| `95-automation-wizard-view.js` | **Genel iş akışı — çizim**: sihirbazın bütün HTML üreticileri (akış ilkelleri, eşik kadranı, koşul blokları, seçici, hedef/değer/eşleme/gecikme/grup/sahne/ad blokları, akış düğümleri) ve `renderAutomationWizard`. | ~1045 | H, I'nin HTML yarısı (1293–1316), J'nin HTML yarısı (1335–1365 + 1540–1672), L, N'nin çizim yarısı (2700–2755) |
| `96-automation-wizard-actions.js` | **Genel iş akışı — davranış**: `automationBindBody` sevk tablosu, basılı-tut sayacı, ilerleme/animasyon ve tüm `choose*/step*/set*/commit*/add*/remove*` işleyicileri ile `saveAutomationWizard`. | ~1165 | M, N'nin ilerleme yarısı (2556–2605), O |

**Kullanıcının istediği ayrım:** `91-automation-devices.js` = "cihaza özel motor" (tek başına,
diğerlerinin hiçbirine bağımlı değil). `93/94/95/96` = "genel iş akışı" (liste · model · çizim ·
davranış). `92` ikisinin arasında duran salt-metin katmanı.

**Yükleme sırası kısıtları**
- Sıra: `… 90-shell.js → 91 → 92 → 93 → 94 → 95 → 96 → 99-bind.js`.
- Sayısal önek azalmamalı (panel-graph denetler); `99-bind.js` en sonda kalmalı.
- Çağrılar çalışma anında olduğu için 91→96 arasındaki iç sıra **teknik olarak serbest**; yukarıdaki
  sıra bağımlılık yönünü (aşağıdaki yukarıdakini çağırır) okunur kılmak içindir.
- `let` durumu taşındığı yere gitmeli: `automationAnimate` + `automationHoldQuiet` +
  `automationAdvanceTimer` + `automationPickChoosing` → hepsi `96`. `automationAnimate` bugün
  `openAutomationWizard` (satır 1126, → dosya 94) içinden de yazılıyor; bu çapraz referans korunur,
  fonksiyon gövdesi içinde olduğu için sorun çıkarmaz.
- Aynı üst düzey ad iki dosyada tanımlanamaz (panel-graph hata verir) — bölme sırasında hiçbir ad
  kopyalanmamalı.

> `95` bölmeden sonra da ~1045 satır kalıyor. Daha da bölmek **şimdi** yapılmamalı: sihirbaz
> yenilemesi zaten bu dosyanın büyük kısmını yeniden yazacak, ikinci bölme dikişini o iş belirlesin.

---

## 3. `index.html` + `src/index.ts` güncellemeleri

İkisi birlikte değişmeli; `npm run check` (`scripts/panel-graph.mjs`) ayrışmayı yakalıyor.

**`public/index.html` satır 384** — tek satır silinir, yerine altı satır:
```html
<script src="/js/91-automation-devices.js"></script>
<script src="/js/92-automation-text.js"></script>
<script src="/js/93-automation-list.js"></script>
<script src="/js/94-automation-wizard-model.js"></script>
<script src="/js/95-automation-wizard-view.js"></script>
<script src="/js/96-automation-wizard-actions.js"></script>
```
(383. satır `90-shell.js`, 385. satır `99-bind.js` olarak kalır.)

**`src/index.ts` satır 441** — `panelAssetRoutes` içindeki `"/js/panel-automation.js"` satırı
aynı altı yolla değiştirilir (sıra `index.html` ile birebir aynı).

Başka dokunulacak yer yok: `panel-automation.js` adı yalnız bu iki yerde geçiyor (kalanlar
`docs/` altında tarihsel metin). Android paketleme `panelDigest` ile dizini taradığı için yeni
dosyalar kendiliğinden kapsama girer; `scripts/runtime-module-graph.mjs` yalnız `apps/runtime`
CJS ağacına bakar, ilgisiz.

---

## 4. Jeneriklik taraması

**Model/üretici adına bakan yer YOK.** `panel-automation.js`, `src/automations.ts`,
`src/automation-engine.ts`, `src/device-controls.ts` içinde `model`/`manufacturer`/`vendor`/
`TS0601`/`_TZ…` geçen tek satırlar yorum satırları ("model listesi yoktur" diyen notlar).

**Zaten jenerik olanlar**
- Sayısal keşif (`automationNumericProperties`, 1255–1262): `features ∪ Object.keys(state)`
  içinden `number` olan her alanı alır, yalnız `linkquality`'yi eler. Tam jenerik.
- Değer eylemleri (parlaklık/renk sıcaklığı/renk): kimlik `<kanal>:brightness` kalıbından, ölçek
  kumandanın kendi `min`/`max`/`step` alanından okunur. Tam jenerik.
- Eşik adımı (`automationAutoStep`): okunan değerin büyüklüğünden türetilir, tablo yok.
- Motorun beslendiği olay akışı **sunucuda** izin listesi değil **yasak listesi** ile süzülüyor
  (`isAutomationEventProperty`, `device-store.ts:301` — yalnız `linkquality`, `last_seen`,
  `elapsed`, `update*` elenir). Yani motor cihazın bildirdiği her skaler değişimi görüyor.

**İŞLEVSEL ihlal — tek ve net: `automationSensorEvents` (satır 196–206)**
Sabit 9 alanlık izin listesi: `occupancy`, `presence`, `contact`, `smoke`, `carbon_monoxide`,
`water_leak`, `battery_low`, `alarm`, `lock_state`. Kullanıldığı yerler:
`automationTriggerEvents` (satır 233 — `for(const property of Object.keys(automationSensorEvents))`),
`automationConditionRows` (1322 üzerinden), `automationDeviceTabs` (118, sensör sekmesi kararı),
ve `automationNumericProperties` (1261, tersten eleme).
Sonuç: cihaz `vibration`, `tamper`, `gas`, `moving`, `sos`, `door_state`, `child_lock` gibi bir
boole/enum alanı bildirse bile **sihirbazda hiç görünmez** — motor o olayı görüyor olmasına rağmen
kullanıcı kural kuramıyor. Bu kozmetik değil, cihazı dışlıyor.

**Önerilen jenerik alternatif** (ayrı, davranış değiştiren commit):
1. `automationSensorEvents` kapı olmaktan çıkar, **yalnız etiket tablosu** olarak kalır
   (bilinen alan güzel cümlesini korur: "Hareket var" / "Kapı açıldı").
2. Satır türetme sunucudaki yasak listesiyle aynı dili konuşur: `features ∪ keys(state)` içinden
   gürültü alanları (`linkquality`, `last_seen`, `elapsed`, `update*`) elenir; kalanlardan
   - sayı → mevcut sayısal/eşik yolu,
   - boole → iki satır (`true`/`false`), etiket tablodan, yoksa jenerik yedek
     (`"<okuma> oldu"` / `"<okuma> bitti"`, ad `automationPropertyLabel` ile),
   - dize/enum → kumanda `options`/`values` bildiriyorsa onlardan, bildirmiyorsa gözlenen değerden
     satır üretilir.
3. `automationDeviceTabs` sensör yedeği (118) otomatik jenerikleşir, ayrı düzeltme gerekmez.
4. Yan not: `device-store.ts:260` `interestingEventProperties` de dar bir izin listesi ama o
   **olay günlüğünü** ("Ev hareketleri") besliyor, otomasyonu değil — yeni seçilebilir bir alan
   günlükte görünmez. Kozmetik; ayrıca karar verilsin.

**Tek doğruluk kaynağı (bölmeden sonra):** `91-automation-devices.js`. Kural: `device.features`,
`device.state`, `device.controls`, `device.buttons` alanlarına **yalnız bu dosya** bakar; 92–96
hazır satır/nesne alır. Bu kuralı `scripts/check-graph.mjs` içine küçük bir grep denetimi olarak
eklemek mümkün (opsiyonel, bölmeden sonra).

---

## 5. Sunucu tarafı

**`src/automations.ts` (1339 satır) — ŞİMDİ BÖLÜNMESİN.**
İçerik tek konu: tel üzerindeki sözleşme — tip tanımları, tip koruyucuları, limit sabitleri ve
doğrulama/normalizasyon. Tipler ile onları doğrulayan kod birbirine bitişik; ayırmak ikisinin
zamanla ayrışmasına yol açar ve dosya panel gibi devasa bir çizim yığını değil. Büyürse dikiş
belli: `automations.ts` (yalnız tip + guard + limit, bağımlılıksız) ↔ `automation-validate.ts`
(ayrıştırma/normalizasyon).

**`src/automation-engine.ts` (1698 satır) — bölünebilir ama panel kadar acil değil.**
Dosyada net bir dikiş var: satır 35–615 **saf fonksiyonlar** (zaman/güneş matematiği, koşul
değerlendirme, olay eşleme, eylem imzası) ve satır 616–1698 **durumlu `AutomationEngine` sınıfı**.
Öneri: `automation-engine-core.ts` (~580, saf) + `automation-engine.ts` (~1100, sınıf).
Gerekçe ertelemeye: sınıf kendi içinde tutarlı, saf yarı zaten dışarıdan import edilebilir
durumda, ve TypeScript'te derleyici bir taşıma hatasını anında yakalıyor — paneldeki sessiz
kırılma riski burada yok. Panel bölmesi bittikten sonra, sınıf ~1200 satırı aşarsa yapılsın.

---

## 6. Risk, doğrulama ve sıra

**`npm run check` neyi yakalar:** `index.html` etiketleri ↔ disk dosyaları ayrışması,
`panelAssetRoutes` eksiği (etiketsiz/dosyasız yol), sayısal önek sırası, `99-bind.js`'in sonda
olması, her dosyanın tek tek ayrıştırılabilmesi, **iki dosyada aynı üst düzey ad**, ve birleştirilmiş
metnin ayrıştırılabilmesi.

**Yakalamadığı riskler:**
1. `automationBindBody` sevk tablosundaki bir `data-automation-*` kancasının işleyicisi taşınırken
   düşerse — sessiz hiçbir şey yapmayan düğme.
2. Bir fonksiyonun taşınırken gövdesinin kırpılması/çift kopyalanması (ad çakışması olmayan hâli).
3. i18n anahtar kayması, CSS sınıf kaybı, görsel/yerleşim bozulması.
4. Sihirbaz aşama makinesinin davranışı (hangi adımdan hangisine geçildiği).

**Kanıt yöntemi (ucuz ve güçlü):** bölme **saf kes-yapıştır** olmalı — taşınan hiçbir satır
düzenlenmeyecek. Bittiğinde altı yeni dosyanın satırları ile eski dosyanın satırları, sıralanmış
çoklu-küme olarak birebir eşleşmeli:
```
cat 91-… 92-… 93-… 94-… 95-… 96-… | sort > /tmp/new.txt
sort public/js/panel-automation.js > /tmp/old.txt
diff /tmp/old.txt /tmp/new.txt     # yalnız eklenen bölüm başlığı yorumları çıkmalı
```
Bu geçerse davranış değişmediği metin düzeyinde kanıtlanmış olur. Sonrasında görsel doğrulamayı
kullanıcı yapar (sunucuya deploy edip tablette/panelde bakarak) — 1024×640 önce.

**Sıra (gerekçeli):**
1. **Önce: aynı cihazı iki kez seçme düzeltmesi bitsin.** Şu an sürüyor ve tam da taşınacak
   bölgeye (`chooseAutomationTriggerDevice` / `…CondDevice` / `…TargetDevice`, satır 2834–3234)
   dokunuyor. 3706 satırlık dosyayı altına çekmek garanti çakışma demek.
2. **Sonra: bölme.** Sihirbaz yenilemesinden **ÖNCE** yapılmalı: yenileme bugün dosyanın büyük
   kısmını yeniden yazacak; bölünmüş hâlde aynı iş yalnız `95-automation-wizard-view.js` (+ bir
   miktar `96`) dosyasını yeniden yazar, `91` cihaz motoru ile `92`/`93` hiç ellenmez. Yenilemeden
   sonra bölmek ise aynı emeği iki kez harcamak olur.
3. **En son: sihirbaz yenilemesi**, ve ondan bağımsız olarak jeneriklik düzeltmesi.

**Kaç adım (3 commit):**
- **Commit 1 — sihirbaz dışı yarı:** `91`, `92`, `93` oluşturulur; `panel-automation.js` yalnız
  kalan bölümlerle küçülür; `index.html` + `panelAssetRoutes` güncellenir. `npm run check`.
- **Commit 2 — sihirbaz yarısı:** `94`, `95`, `96` oluşturulur; `panel-automation.js` silinir;
  iki tablo son hâline gelir. `npm run check` + sıralanmış-satır diff kanıtı.
- **Commit 3 — ayrı ve davranış değiştiren:** jenerik sensör olayı türetme (§4).
  Taşıma ile davranış değişikliği **asla aynı commit'te olmamalı**; karışırsa yukarıdaki
  diff kanıtı kullanılamaz hâle gelir.
