# Villa Bridge — Otomasyon Planı

Bu doküman, ev otomasyonu özelliğinin araştırma sonuçlarını, alınan ürün kararlarını ve
uygulama planını taşır. Araştırma 2026-08-02'de yapıldı; kararlar kullanıcıyla birlikte alındı.

**Durum:** Tasarım tamamlandı, açık soru kalmadı (bkz. §9 — karar B). Faz 0 uygulanıyor:
Otomasyon sekmesi ve "Basit bağlantı" yolu arayüze eklendi.

---

## 1. Amaç

Villa Bridge kullanıcısının, kod yazmadan, teknik terim öğrenmeden ev otomasyonu kurabilmesi.
Hedef kitle ev halkı — teknik olmayan kişiler ve çocuklar dahil.

Kullanıcının kendi örneği, ilk sürümün karşılaması gereken asgari senaryo:

> "Hem sabah ve akşam olduğunda bu lambayı söndür/yak diyebilmeli, hem de bu sensör
> tetiklendiğinde aynı lambayı yak diyebilmeli."

---

## 2. Ürün kararları

Bunlar kullanıcıdan geldi ve tasarımın sınırlarını çiziyor. Araştırma raporunun bir bölümünü
geçersiz kıldılar — özellikle Home Assistant'a delege etme fikrini.

### 2.1 Home Assistant'a yaslanmıyoruz

**Villa Bridge, Home Assistant'a olan ihtiyacı ortadan kaldırmak için var.** Ona bir katman olarak
yaslanmak, çözmeye çalıştığı problemi geri getirir.

Kullanıcının tarifi: *"Bizim sistemimiz zaten Home Assistant'a ihtiyacı tamamen kaldırmak için
kurulmuş bir sistem. Sadece Zigbee cihazı olan insanların cihazlarını kolayca bağlayacağı, bunları
Matter'da yayınlayacağı, daha sonra iPhone cihazlarını rahatlıkla Matter'a tanıtıp oradan
kullanabilecekleri bir sistem. Matter bağlamayı istemeyen ya da tercih etmeyen insanlar bunu
tabletten de yapabilsinler."*

Sonuç: araştırma raporundaki "Katman 3 — ekosistem işleri Home Assistant'ta kalır" maddesi
**tamamen düşmüştür**. Telefon konumu, medya, kamera, bildirim gibi şeyler kapsam dışıdır ve
başka bir sisteme devredilmez — hiç yapılmaz.

### 2.2 Kapsam: yalnızca Zigbee

*"Home Assistant gibi her cihazı görelim, her sistemi bağlayalım gibi derdimiz yok. Sadece
Zigbee'den gelen cihazları bağlayacağız."*

Bu kapsamı daraltıyor ve işi kolaylaştırıyor: otomasyon motorunun tanıması gereken tetikleyici ve
eylem türleri, `DeviceView` / `DeviceControlView` ile sınırlı ve öngörülebilir.

### 2.3 Sürükle-bırak yok, sihirbaz var

Kullanıcı başlangıçta sürükle-bırak istemişti. Araştırma iki isteğin ("sürükle bırak" ve "milletin
alışkın olduğu") çeliştiğini gösterdi ve kullanıcı sihirbaz yaklaşımını kabul etti:

*"Sürükle bırak olayını anlıyorum ve kabul ediyorum. Eğer diğer tüm büyük sistemler koşul bazlı
sihirbazlar kullanıyorsa biz de bunu kullanabiliriz."*

### 2.4 "Orta düzeyde kompleks"

*"Bütün sistemleri inceleyip bunları sentezleyerek olabilecek en efektif, en basit ama orta düzeyde
kompleks otomasyonları yapabileceğimiz bir sistem kurmak istiyorum."*

Sınır şöyle çizildi:

| Var | Yok |
|---|---|
| Çoklu tetikleyici (herhangi biri) | Dallanma (if/else) |
| Koşullar (hepsi birden) | Döngü |
| Çoklu eylem (sırayla) | Değişken, şablon ifadesi |
| Gecikme | Cron ifadesi |

Bu kabaca SmartThings seviyesi — sıradan kullanıcının kaybolmadığı en üst nokta.

---

## 3. Araştırma özeti

Yedi sistem tarandı: Google Home, Apple Home, Alexa, SmartThings, Home Assistant, Homey, IFTTT.
Tam rapor ve kaynak bağlantıları için bkz. §11.

### 3.1 Herkesin kurduğu cümle

Yedi sistemin yedisi de aynı yapıyı kullanıyor:

> **"Şu olduğunda → (yalnızca şu doğruysa) → şunu yap."**

Farklar sadece kelimelerde: `starter` (Google), `When this happens` (Alexa), `If` (SmartThings,
IFTTT), `Trigger` (Home Assistant), `When` (Homey), isimsiz (Apple).

**Arayüz dili kararı:** Türkçe **"Ne olduğunda… / Şunu yap…"**, İngilizce **"When… / Then…"**.
"Tetikleyici", "koşul", "kural", "senaryo" kelimeleri **arayüzde geçmeyecek** — bunlar geliştirici
sözlüğü.

### 3.2 İlk otomasyon kaç adım

| Sistem | Adım | Ana feda |
|---|---|---|
| Apple Home | 5–6 | Neredeyse tüm güç (tek tetikleyici, koşul yok) |
| IFTTT | 6 | Her şey (1 tetikleyici + 1 eylem) |
| Google Home | 6–8 | Mantık ayrı bir script editöre sürülmüş |
| Alexa | 7 | Koşul ve dallanma |
| Homey Flow | 6–8 | Tablet ergonomisi (sürükle-bırak) |
| SmartThings | 7–8 | Ekran kalabalığı |
| Home Assistant | 8–10 | Sadelik |

Hedefimiz: **3 ekran, ~7 dokunuş.**

### 3.3 Karmaşıklık nerede saklanıyor

| Karmaşıklık | Nasıl saklanmış |
|---|---|
| VE / VEYA | SmartThings: tek "All / Any" anahtarı. Apple ve Alexa: hiç sunmuyor. |
| Zaman aralığı | Apple: tetikleyicinin içine gömülü hazır seçenekler (Gündüz / Gece / Belirli saat). |
| Gecikme | Alexa: "Wait" adında **bir eylem**. Kontrol akışı, eylem kılığında. |
| Tekrarlanabilirlik | Home Assistant blueprint: parametreli şablon; kullanıcı sadece boşlukları doldurur. |

---

## 4. Mimari: iki katman

Her otomasyon türü, **çalışabileceği en alt katmanda** çalışmalı.

### Katman 1 — Refleks (Zigbee binding)

**Ne:** Düğme → lamba, uzaktan kumanda → oda. Cihazdan cihaza doğrudan bağlama.

**Nerede:** Zigbee koordinatörü. `ZigbeeSource.bindDevice()` ve `groupScene()` **zaten mevcut**
(`src/source.ts`), `DeviceEndpointView.bindings` ile okunabiliyor. Yalnızca arayüzü yok.

**Neden değerli:** Bağlama tablosu cihazın kendi belleğinde. Villa Bridge, MQTT, Matterbridge —
hepsi kapalıyken bile düğme lambayı yakar. Gecikme ~20–80 ms (motor üzerinden ~150–400 ms yerine).

**Sınır:** Yalnızca Zigbee, yalnızca cihaz→cihaz/grup, yalnızca On/Off + Level + Color. Zaman yok,
koşul yok.

**Kullanıcı bunu "otomasyon" olarak görür**, altta binding olduğunu bilmez. Özet ekranında yalnızca
şu ek satır belirir:

> ⚡ *"Bu otomasyon doğrudan cihazların arasına kurulacak — sistem kapalıyken bile çalışır."*

### Katman 2 — Ev kuralları (Villa Bridge motoru)

**Ne:** Zaman, gün doğumu/batımı, sensör, çoklu tetikleyici, koşul, çoklu eylem, gecikme, sahne.

**Nerede:** Villa Bridge sunucusu. Yeni `src/automations.ts` (depo) + `src/automation-engine.ts`
(motor). Yeni bağımlılık yok.

**Neden sunucuda, tarayıcıda değil:** Arayüz 8 saniyede bir poll ediyor, tablet uyuyor, iki sekme
açılırsa her otomasyon iki kez tetiklenir. Motor sunucuda olmak zorunda.

**Neden Home Assistant'ta değil:** §2.1. Ayrıca HA'nın otomasyon yazma endpoint'i
(`POST /api/config/automation/config/{id}`) resmî olarak dokümante edilmemiş — HA'nın kendi
arayüzünün iç API'si. Ve `apps/runtime` (Android tablet / Pi) kurulumunda Home Assistant hiç yok.

---

## 5. Veri modeli

### 5.1 Otomasyon şekli

```json
{
  "id": "aksam-salon",
  "name": "Akşam salon",
  "enabled": true,
  "layer": "engine",
  "triggers": [
    { "type": "time", "at": "19:00", "days": [1,2,3,4,5,6,7] },
    { "type": "deviceState", "deviceId": "0x00124b0022ab34cd",
      "property": "occupancy", "equals": true }
  ],
  "conditions": [],
  "actions": [
    { "type": "device", "deviceId": "0x00124b0011cc22dd",
      "property": "state", "value": "ON",
      "autoOff": { "mode": "idle", "seconds": 0, "value": "OFF" } }
  ],
  "lastRunAt": "2026-08-01T16:00:00.000Z"
}
```

**Kritik:** `triggers` **çoğuldur**. Araştırma raporu v1 için tek tetikleyici öneriyordu (Apple
modeli); kullanıcının örneği bunu aşıyor. Çoklu tetikleyici **herhangi biri** (VEYA) mantığıyla
çalışır — Google Home ve Home Assistant da böyle yapar.

**Arayüzde "VEYA" kelimesi geçmez.** Kullanıcı "Ne zaman çalışsın?" ekranında
**"+ Başka bir zaman daha ekle"** der; VEYA mantığı arkada kalır.

Kullanıcının örneği iki otomasyona ayrılır:

```
Otomasyon 1 — "Sabah"
  Ne zaman:  07:00
  Ne yapsın: Salon lambası → Kapat

Otomasyon 2 — "Akşam salon"
  Ne zaman:  19:00
             VEYA  Koridor sensörü hareket algılayınca
  Ne yapsın: Salon lambası → Aç
```

### 5.1.1 Alt varlık kuralı — otomasyon cihaza değil, cihaz+özellik çiftine bağlanır

Hedef **`deviceId` + `property`** ikilisidir. Çok gangli anahtarların her kanalı (`state_l1`,
`state_l2`) ve sensörlerin her özelliği ayrı bir hedeftir.

- `property` **kanonik ve kalıcıdır** — MQTT özellik anahtarı; motor bunu kullanır.
- `controlId` (`DeviceControlView.id`) ve `name` yalnızca **sunum** verisidir.
- Cihaz veya kanal yeniden adlandırılınca otomasyon **bozulmaz** (proje UID kuralı).

### 5.2 Tetikleyici türleri

| Tür | Alanlar | Mevcut veriyle mümkün mü |
|---|---|---|
| `time` | `at "HH:MM"`, `days: number[]` | **Evet**, sunucu değişikliği gerekmez |
| `sun` | `event: "sunrise"\|"sunset"`, `offsetMinutes` | **Kısmen** — `config/default.yaml`'a `latitude`/`longitude` eklenmeli. Güneş hesabı ~40 satır, bağımlılık gerekmez. Faz 3'e ertelenebilir. |
| `deviceState` | `deviceId`, `property`, `equals?` | **Evet** — `src/device-store.ts` içindeki "interesting" kümesi zaten izleniyor: `action`, `state`, `contact`, `occupancy`, `presence`, `smoke`, `carbon_monoxide`, `battery_low`, `alarm`, `lock_state`, `water_leak` |
| `deviceAction` | `deviceId`, `action` | **Evet ve iyi bir tetikleyici** — `DeviceView.actionTypes` cihazın desteklediği buton eylemlerini zaten taşıyor. **Uyarı:** `action` anlık bir kenar olayıdır; motor bunu son-değer karşılaştırmasıyla değil, olay akışından dinlemeli. |
| `battery` | `deviceId`, `low: true` | **Evet** — türetilmiş `battery_threshold` olayı zaten üretiliyor |

#### `deviceState` içinde `equals` opsiyoneldir

`equals` **verilirse** yalnızca o değere geçişte tetiklenir — eski davranışın aynısı, mevcut
kurallar bozulmaz. `equals` **verilmezse** özelliğin **her** değişimi tetikler; böylece bir duvar
anahtarının hem açılışı hem kapanışı tek kuralla yakalanır. Kenar kuralı her iki halde de geçerli:
aynı değer yeniden bildirilirse tetiklenmez. `equals: null` "verilmemiş" sayılır.

### 5.3 Koşul türleri (v1'de ikisi yeter, yalnızca VE)

- `timeRange` — `{ from: "HH:MM", to: "HH:MM" }` (Apple'ın "During the day / At night" karşılığı)
- `deviceState` — `{ deviceId, property, equals }`

**VEYA koşulu v1'de yok.** Apple ve Alexa da sunmuyor. Gerekirse SmartThings'in tek "All / Any"
anahtarı sonradan eklenir; veri modelinde `conditionMode: "all" | "any"` alanı olarak yer tutulabilir.

### 5.4 Eylem türleri

| Tür | Alanlar | Mevcut altyapı |
|---|---|---|
| `device` | `deviceId`, `property`, `value` | `source.setDevice()` |
| `group` | `groupId`, `command` | `source.setGroup()` |
| `scene` | `groupId`, `sceneId` | `source.groupScene(id, sceneId, "recall")` — **zaten var** |
| `delay` | `seconds` (1–300) | Alexa'nın "Wait"'i; motor içinde |

Her eylem ayrıca isteğe bağlı **`autoOff?: { mode: "after" | "idle"; seconds: number; value }`**
taşır — §9'daki B kararının gerçekleşmiş hâli (plandaki `revertAfterSeconds` yer tutucusunun
yerini aldı; iki seçenek gerektiği için tek sayı yetmedi). Alan yoksa geri alma yoktur.

#### Eylem koşulu: `when` — anahtar durumunu eyleme eşleme

Her eylem isteğe bağlı **`when?: { equals: JsonScalar }`** taşıyabilir. Eylem yalnızca **tetikleyen
olayın değeri** `when.equals`'a eşitse çalışır:

```jsonc
"triggers": [{ "type": "deviceState", "deviceId": "0xf844…", "property": "state" }],
"actions": [
  { "type": "device", "deviceId": "0xa4c1…", "property": "state", "value": "ON",  "when": { "equals": "ON"  } },
  { "type": "device", "deviceId": "0xa4c1…", "property": "state", "value": "OFF", "when": { "equals": "OFF" } }
]
```

- `when` **yoksa** eylem her zaman çalışır → geriye tam uyumluluk.
- `when` yalnızca `equals` alanını tanır; başka alan **reddedilir**.
- **Zaman tetikleyicisinde ve elle çalıştırmada** eşleşecek bir olay değeri yoktur, bu yüzden
  `when` taşıyan eylemler **atlanır**.
- Hiçbir eylem eşleşmezse çalıştırma **başarısız sayılmaz**: motor `skipped` döner, kilit alınmaz,
  `lastRunAt`/`lastRunOk` dokunulmadan kalır. `POST /api/automations/:id/run` bu halde
  `{ ok: true, skipped: true }` verir.
- §8.2 döngü doğrulaması `when`'den bağımsızdır — eylem hedefi tetikleyici cihazla aynıysa kural
  yine kaydedilemez.

#### Akış kararı: durum sorma, sonra eşleme formu

Sihirbazda tetikleyici adımında kullanıcıya **"hangi durumda?"** sorulmaz. Kullanıcı **anahtarı**
seçer, **hedefi** seçer; ardından bir **eşleme formu** açılır ve anahtarın her durumu için ne
yapılacağı orada seçilir. Varsayılan **takip**: açılınca Aç, kapanınca Kapat. Kullanıcı isterse
tersine çevirebilir (buton kapalıyken lambayı açmak gibi). Form bunu `equals`'sız bir tetikleyici
ve `when` taşıyan iki eyleme çevirir.

**Zincirleme:** "bu tetiklenince şu, o da başkasını tetiklesin" ihtiyacı **tek kuralda sıralı
eylemlerle** karşılanır. Kural-kurala tetikleme birincil yol **değildir**; §8.2'deki döngü koruması
aynen geçerlidir.

---

## 6. API ve depolama

Mevcut desene (`GET/PUT /api/favorites`, `GET/PUT /api/home-groups`) birebir uyar.

```
GET    /api/automations           → { ok, automations: Automation[] }
PUT    /api/automations           → tüm dizi (doğrulanır, atomik yazılır)
POST   /api/automations/:id/run   → elle test çalıştırma
```

**Yetki (`src/access-control.ts`):** yalnızca `GET /api/automations` → `residentRoutes`.
`PUT` ve `run` listelenmez, dolayısıyla otomatik olarak admin ister. Gerekçe: bir otomasyon evin
tamamını etkiler; oluşturma/silme yönetici işidir.

**Depolama (`src/automations.ts`):** `src/home-groups.ts` sınıfının birebir kopyası —
doğrulama → geçici dosyaya yaz (`mode 0o600`) → `rename`. Sınırlar: en fazla 64 otomasyon,
otomasyon başına 8 eylem / 4 koşul, ad ≤ 64 karakter, `deviceId` için mevcut `/^0x[0-9a-f]{16}$/`.
Cihaz silindiğinde `removeDevice(deviceId)` — `home-groups.ts`'deki gibi.

**Motor (`src/automation-engine.ts`):**
- **Zaman tetikleyicileri:** 20 saniyelik tek `setInterval`; geçerli `HH:MM` eşleşiyorsa ve o dakika
  için henüz ateşlenmediyse çalıştırır. Yeniden başlatmada geçmiş dakikalar telafi edilmez.
- **Olay tetikleyicileri:** `DeviceStore`'un mevcut olay geri çağrımına takılır — poll yok.
- **Yeniden giriş koruması:** Bir otomasyon kendi eylemleri sürerken yeniden tetiklenemez;
  otomasyon başına minimum 2 saniye aralık.
- **Döngü koruması:** Bir otomasyon, kendi eylemlerinde kullandığı cihaz tarafından tetiklenemez.
  **Kaydetme sırasında doğrulanır**, çalışma zamanında değil — kullanıcı hatayı anında görür.
- Tahmini boyut: 250–350 satır + testler. Yeni bağımlılık yok.

---

## 7. Kullanıcı akışı

**Yerleşim:** `nav`'a beşinci düğme — **"Otomasyon"** (`data-view="automations"`).
Home / Cihazlar / **Otomasyon** / Bağlantılar / Ayarlar.

### Ekran 0 — Liste
- Üstte tek birincil düğme: **"+ Yeni otomasyon"**. Basınca **iki yol kartı** açılır (§4'teki iki
  katmanın kullanıcıya görünen hâli):
  - ⚡ **"Basit bağlantı"** — *"Bir düğme doğrudan bir lambayı çalıştırsın. Sistem kapalıyken bile
    çalışır."* → Katman 1, iki adımlı sihirbaz (Hangi düğme? / Neyi çalıştırsın?).
  - 🧩 **"Kural kur"** — *"Saate, sensöre veya cihaz durumuna göre çalışsın."* → Katman 2,
    aşağıdaki Ekran 1–3. **Faz 0'da "yakında" rozetiyle devre dışı.**
- Mevcut otomasyonlar `.device-card` diliyle kartlar halinde: ad, düz cümle özeti
  ("Her gün 19:00 · Salon Lambası açılır"), sağda **açık/kapalı anahtarı**, altta
  **son çalışma çipi** ("Dün 19:00").
- Boşken dört hazır şablon çipi: **"Akşam ışıkları"**, **"Kapı açılınca ışık"**,
  **"Düğmeyle çalıştır"**, **"Kimse yokken kapat"**. Sihirbazı önceden doldurulmuş açar
  (HA blueprint fikrinin küçük hali).
- Karta uzun basma → mevcut `deviceActionDialog` desenine benzer modal: *Düzenle / Şimdi çalıştır / Sil*.

### Ekran 1 — "Ne zaman çalışsın?"
Beş büyük dokunma hedefi (ikon + tek satır etiket, min 88 px):
- 🕐 Belirli bir saatte
- 🌅 Gün doğarken / batarken
- 🔘 Bir düğmeye basınca
- 🚪 Bir sensör algılayınca (kapı, hareket, su, duman)
- 💡 Bir cihaz açılınca/kapanınca

Saat seçimi **iri artır/azalt düğmeleriyle** — sayısal klavye duvar tabletinde en zayıf halka.
Gün çipleri, varsayılan "Her gün". Altta **"+ Başka bir zaman daha ekle"** (çoklu tetikleyici).

### Ekran 2 — "Ne yapsın?"
Üstte mevcut oda/grup çipleri (`home-groups`'tan — kullanıcı zaten tanıyor), altta o odanın
cihaz kartları. Cihaza dokununca kontrolleri satır içinde açılır (`DeviceControlView`'dan):
Aç / Kapat / parlaklık. Seçilen kart yeşil kenarlıkla işaretlenir.
Altta ikincil: **"+ Başka bir şey daha yapsın"**.

### Ekran 3 — Özet ve kaydet
Otomasyon **düz bir cümle** olarak gösterilir — kullanıcının "doğru mu kurdum?" endişesini
çözen yer:

> **"Her gün saat 19:00 olduğunda Salon Lambası açılacak."**

- Ad alanı otomatik doldurulmuş ("Akşam salon"), düzenlenebilir.
- Sessiz bağlantı: **"Sadece belirli bir durumda çalışsın…"** → koşul adımı (varsayılan kapalı).
- Birincil düğme: **"Kaydet"**.

**i18n uyarısı:** Özet cümlesi parça birleştirilerek kurulmamalı. Türkçe ve İngilizce kelime sırası
farklı olduğu için **tam şablon anahtarı** kullanılmalı:
`automationSummaryTime` → TR `"Her gün saat {time} olduğunda {device} {action}."` /
EN `"Every day at {time}, {device} will {action}."`

---

## 8. Güvenlik kuralları

### 8.1 Kilit ve siren otomasyon **eylemi** olamaz

`public/index.html`'de kilidi açmak ve sireni çalıştırmak zorunlu onay diyaloğu gerektiriyor
(bu oturumda eklendi). Bir otomasyon tetiklendiğinde **onaylayacak insan yoktur** — yani bu
güvenlik kontrolü yapısal olarak devre dışı kalır.

`validateAutomations()` bunu reddetmeli, arayüz bu cihazları eylem seçicide göstermemeli.

Google'ın kendi dokümanı da aynı çizgide: *"Routines are for convenience only, not safety- or
security-critical use cases."*

**Tersi tamamen serbest:** duman, CO, su kaçağı, kapı sensörü ve kilit durumu **mükemmel
tetikleyicilerdir** — "duman algılanınca tüm ışıkları yak" hem güvenli hem değerli.

İleride yalnızca **kilitleme** (asla açma) yönü, ayrı bir yönetici anahtarının arkasında açılabilir.

### 8.2 Diğer riskler

| Risk | Karşı önlem |
|---|---|
| **Geri besleme döngüsü** | Kaydetme anında doğrulama: otomasyon, eylem hedefindeki cihaz tarafından tetiklenemez |
| **Saat kayması** — standalone kurulumda NTP yoksa | Yerel saat + "aynı dakikada bir kez" kilidi; yaz saati geçişinde çift ateşleme engellenir |
| **Düğme gürültüsü** — pilli kumandalar aynı sinyali art arda yayınlar | Otomasyon başına min. 2 saniye yeniden tetikleme aralığı |
| **Sessiz başarısızlık** — kullanıcıların en çok takıldığı nokta hata ayıklama | Kartta "son çalışma" çipi; hiç çalışmadıysa hemen görünür. **Faz 1'e dahil.** |
| **Binding görünmezliği** — bağlama cihazın belleğinde, dışarıdan değiştirilebilir | Liste gerçek cihaz durumundan üretilmeli (`DeviceEndpointView.bindings`), ayrı kopya tutulmamalı |

---

## 9. Karar — "şu kadar sonra geri al"

Sensör tetikleyicilerinde neredeyse her zaman gereken bir davranış var: **"hareket bitince geri
kapansın"**. İki seçenek vardı:

**A) Ayrı otomasyon.** Kullanıcı ikinci bir kural yazar:
*"Koridor sensörü 10 dakika hareket görmezse → lambayı kapat"*.
Model saf kalır, ama kullanıcı her ışık için iki otomasyon kurar.

**B) Otomasyonun içinde "şu kadar sonra geri al" seçeneği.**
Tek kural yeter, ama modele geri-alma kavramı girer.

**Seçilen: B.** Gerekçe: tek kural ev halkı için doğal olan ifade biçimi ve zaten ana senaryo bu —
"ışık yansın, biraz sonra kendi kendine kapansın" iki ayrı otomasyon olarak düşünülmüyor.

**Gerçekleşen hâli (`autoOff`, §5.4):** kullanıcı iki seçenekten birini kurar —
**`after`** (şu kadar süre sonra) ya da **`idle`** (hareket bitince, isteğe bağlı ek beklemeyle).

- Geri alınacak değer kuralda yazılıdır (`value`); "önceki durum" tahmin edilmez.
- **Zamanlayıcı otomasyon + cihaz + kanal başına tekildir**; kural yeniden tetiklenirse sayaç
  sıfırlanır, ikinci zamanlayıcı açılmaz.
- **`idle` ölçütü jeneriktir:** kuralın kendi `deviceState` tetikleyicisinin `equals` değerinden
  çıkış demektir. Bu yüzden doğrulama `equals` taşıyan bir durum tetikleyicisi şart koşar;
  sensör modeli listesi yoktur.
- **Elle müdahale iptal eder:** hedef kanal bizim yazdığımız değerden başka bir değere geçtiyse
  (panel, Alexa, Apple Home, duvar anahtarı — hepsi aynı olay akışı) bekleyen kapatma düşer.
- **Yeniden başlatmada bekleyen kapatmalar telafi edilir:** kayıt yapılandırmanın yanındaki
  `automation-auto-off.json`'da atomik tutulur; süresi geçmişse hemen uygulanır, geçmemişse kalan
  süreyle devam eder. Hareket bekleyen kayıtlarda bir dakikalık üst sınır devreye girer — ışık
  sonsuza kadar açık kalmaz, yeni hareket sayacı yine sıfırlar.

---

## 10. Yol haritası

### Faz 0 — Otomasyon sekmesi + "Basit bağlantı" yolu ✅
Gerçek durum: **teknik binding paneli Ayarlar'da zaten vardı** (`#bindSource/#bindTarget/
#zigbeeBindingList`) ve yönetici aracı olarak yerinde kaldı. Faz 0'da eksik olan onun **ev halkı
dilindeki karşılığıydı**.

Yapılan: nav'a beşinci düğme (**Otomasyon**), "+ Yeni otomasyon" → iki yol kartı, ⚡ Basit bağlantı
sihirbazı (Hangi düğme? → Neyi çalıştırsın?), mevcut bağlantıların düz cümle listesi ve kaldırma.
Uç nokta/cluster kullanıcıya sorulmaz, arayüz kendisi türetir. Kilit ve siren hedef listesinde
görünmez (§8.1). Liste **gerçek cihaz durumundan** üretilir (`DeviceEndpointView.bindings`),
ayrı kopya tutulmaz (§8.2).

Yalnızca `public/index.html` + `public/locales/{tr,en}.json` değişti. Sunucu motoru yok, yeni
endpoint yok, kalıcı dosya yok — mevcut `POST /api/zigbee/bind` kullanıldı.
**Sıfır çalışma zamanı riski.**
→ Ev halkı ilk gerçek otomasyonunu kurar ve o otomasyon sistem kapalıyken bile çalışır.

### Faz 1 — Motorun çekirdeği
`automations.ts` + `automation-engine.ts` + `GET/PUT /api/automations` + 3 adımlı sihirbaz.
**Yalnızca `time` tetikleyicisi ve `device` eylemi.** Koşul yok, gecikme yok, güneş yok.
→ Kullanıcının kendi örneği çalışır. **İlk sürüm bu kadar dar olmalı.**

**Sunucu tarafı tamam** ✅ — `src/automations.ts` (depo, atomik yazma, kilit/siren reddi,
`removeDevice`, `markRun`), `src/automation-engine.ts` (20 sn tur, dakika kilidi, yeniden giriş
koruması, enjekte edilebilir saat), `GET/PUT /api/automations` + `POST /api/automations/:id/run`,
`GET` resident yetkisinde. Motor `src/index.ts`'te başlatılıp kapanışta durduruluyor — böylece
`apps/runtime` üzerinden çalışan tablet/Pi kurulumunda da otomasyonlar işler. Kalan: 3 adımlı
sihirbaz (`public/index.html`).

### Faz 2 — Sensör ve düğme tetikleyicileri
`deviceState` + `deviceAction` (olay akışına bağlanma), `group` eylemi, `delay` eylemi,
`timeRange` koşulu, çoklu tetikleyici arayüzü.
→ "Kapı açılınca koridor ışığı yansın", "Hareket 10 dakika yoksa kapansın".

**Düğme ve sensör tetikleyicileri tamam (sunucu tarafı)** ✅ — `deviceAction` ve `deviceState`
doğrulaması, `DeviceStore` olay akışına bağlı motor (poll yok), `action` için kenar davranışı
(aynı basış arka arkaya iki kez tetikler), `deviceState` için değer değişimi kontrolü, kaydetme
anında geri besleme döngüsü reddi. `DeviceView.actionTypes` + `state` sihirbaz için yeterli,
yeni endpoint açılmadı.

**"Sonra kapat" tamam** ✅ — eylem seviyesinde `autoOff` (`after` / `idle`), motorda otomasyon +
kanal başına tekil zamanlayıcı, yeni tetiklemede sayaç sıfırlama, elle müdahalede iptal, yeniden
başlatmada sürdürme (`automation-auto-off.json`) ve sihirbazın son adımındaki form. Kalan
(sonraki tur): `group`/`delay`/`scene` eylemleri, `timeRange` koşulu, çoklu tetikleyici arayüzü,
`sun` tetikleyicisi.

### Faz 3 — Cila
`sun` tetikleyicisi (config'e lat/lon), `scene` eylemi, boş ekrandaki 4 hazır şablon çipi,
otomasyon çalışma günlüğü (`device-events.ts` deseniyle).

---

## 11. Bilinçli olarak reddedilenler

| Fikir | Neden reddedildi |
|---|---|
| **Düğüm grafiği / sonsuz tuval editörü** (Node-RED, Homey Advanced Flow tarzı) | Masaüstü metaforu. Homey'nin kendi topluluğu tablette kullanışsız diyor. `index.html`'i tek başına ikiye katlar. Milletin alıştığı model bu değil. |
| **Otomasyon motorunu tarayıcıda çalıştırmak** | Arayüz 8 sn'de bir poll ediyor, tablet uyuyor, iki sekme = çift tetikleme. |
| **Home Assistant'a otomasyon yazmak** | §2.1 (ürün felsefesi) + dokümante edilmemiş endpoint + `apps/runtime`'da HA yok. |
| **Sürükle-bırak'ı birincil yol yapmak** | Kitlesel ürünlerin hiçbiri kullanmıyor; WCAG 2.2 SC 2.5.7 zaten tek-işaretçi alternatifi zorunlu kılıyor; duvar tabletinde jest çakışması. Yalnızca eylem sıralamasında, ↑/↓ düğmelerine **ek** bonus olabilir. |
| **v1'de VEYA koşulu, dallanma, değişken, cron** | Apple ve Alexa hiç sunmuyor; IFTTT tek tetikleyici + tek eylem üzerine şirket kurdu. Bu kısıtlar zayıflık değil, ürünün kendisi. |
| **Yeni bağımlılık / framework / sürükle-bırak kütüphanesi** | Motor ve güneş hesabı saf `setInterval` ve mevcut MQTT akışıyla yazılabilir. |
| **Otomasyonu friendly name üzerine kurmak** | Proje kuralı: her düşük seviyeli işlem IEEE UID ile. Cihaz yeniden adlandırılınca otomasyon bozulmamalı. |
| **Zigbee gruplarını doğrudan "oda" olarak kullanmak** | Zigbee grubu yalnızca aynı türden aktüatörleri taşır; sensör/kilit/perde içeremez. Mevcut "eşleşirse hızlı yol" yaklaşımı (`matchingZigbeePowerGroup`) doğru olan. |

### Kaynaklar

[Google Home Automations](https://developers.home.google.com/automations/starters-conditions-and-actions) ·
[Apple Home Automations](https://support.apple.com/guide/iphone/use-automations-iph6d50ec543/ios) ·
[Alexa Routines](https://www.androidpolice.com/alexa-routines-guide/) ·
[SmartThings Routines](https://support.smartthings.com/hc/en-us/articles/360051931952-Routines-in-SmartThings) ·
[HA Automation Editor](https://www.home-assistant.io/docs/automation/editor/) ·
[HA Blueprints](https://www.home-assistant.io/docs/automation/using_blueprints/) ·
[HA REST API](https://developers.home-assistant.io/docs/api/rest/) ·
[Homey Flow](https://support.homey.app/hc/en-us/articles/360009669174-Create-your-first-Flow) ·
[Homey topluluk — tablet sorunu](https://community.homey.app/t/advanced-flow-for-ipad-and-similar/65789) ·
[IFTTT Applets](https://help.ifttt.com/hc/en-us/articles/360021401373-Creating-your-own-Applet) ·
[WCAG 2.2 — Dragging Movements](https://wcag22aa.org/new-criteria/dragging-movements/) ·
[Zigbee2MQTT Binding](https://www.zigbee2mqtt.io/guide/usage/binding.html) ·
[NN/g — Smart-Home Users](https://www.nngroup.com/articles/smart-home-users/) ·
[arXiv — Automation Configuration in Smart Homes](https://arxiv.org/pdf/2408.04755)

---

## 12. Bu plan yazılırken projenin durumu

Otomasyon henüz başlamadı. Öncesinde tamamlanan işler, otomasyonun üzerine oturacağı zemini
oluşturuyor:

**Cihazlar sayfası** — kartlarda ürün fotoğrafı yerine durum ikonu, ızgara + detay modalı,
kolon sayısı slider'ı (1–4, telefonda zorunlu liste), her kontrol satırında yıldız (ana ekrana
ekleme) ve kalem (yeniden adlandırma), oda filtre çipleri.

**Dashboard** — hızlı kontrol şeridi altta, alarm hiyerarşisi (duman/CO kritik, düşük pil sessiz),
kilit ve siren için onay diyaloğu, ekran koruyucu (2 dk), 5 dk boşta kalınca Ana Sayfa'ya dönüş,
"Evin durumu" özet widget'ı, cihaz başına tek satır gösteren etkinlik listesi, sekmeli ekleme
modalı (Bilgi kutuları / Gruplarınız).

**Odalar/gruplar** — sunucuda saklanıyor (`src/home-groups.ts`, `home-groups.json`),
cihazlar arası paylaşılıyor, tarayıcıdan tek seferlik geçiş mantığı var. Oda ismi önerileri,
oda özeti, silme onayı, sıralama. Cihazlar sayfasında filtre olarak kullanılabiliyor.
**Otomasyon Ekran 2'si bu grup çiplerini yeniden kullanacak.**

**Cihaz görselleri** — `/api/device-image/:model` proxy + disk cache, sunucu açılışta tüm cihaz
modellerinin görsellerini arka planda indiriyor (offline çalışsın diye).

Test sayısı: Faz 0 öncesi 139, Faz 0 sonrası 140. Testler `src/dashboard-copy.test.ts` içinde markup ve CSS metnini birebir
doğruluyor — arayüz değişikliklerinde bu assertion'ları güncellemek işin parçasıdır.
