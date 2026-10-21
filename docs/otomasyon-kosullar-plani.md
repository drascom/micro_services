# Villa Bridge — Otomasyon Koşulları Genişletme Planı

`docs/otomasyon-plani.md`'nin devamı. O doküman §5.3'te koşulları iki türle sınırlamış ve
"VEYA gerekirse sonradan" demişti. Bu plan o kapıyı açıyor.

**Kapsam kararı (kullanıcı, 2026-08-06):** dört madde de yapılacak, **sunucu + arayüz tam**.

---

## 1. Bugünkü durum

| Katman | Dosya | Koşulla ilgili yer |
|---|---|---|
| Model + doğrulama | `src/automations.ts` | `AutomationCondition`, `validateConditions()` (:358), `automationDeviceIds()`, `removeDeviceFromAutomations()` |
| Değerlendirme | `src/automation-engine.ts` | `evaluateAutomationConditions(conditions, date, readState)` (:165), çağrı yeri :882 |
| Günlük | `src/automation-runs.ts` | `AutomationRunConditionResult { type, ok, reason? }` |
| Arayüz | `public/index.html` | `automationCondStages` (:5061), draft/line/choices (:5176–5250), sihirbaz düğümleri (:5785–5800) |
| Metin | `public/locales/{tr,en}.json` | `automationCond*`, `automationBlockCond*`, `automationNeedCond*` |

Mevcut iki tür: `timeRange` (from/to, gece yarısını aşabilir, opsiyonel `days`) ve
`deviceState` (`equals` **ya da** `not` — tam biri). Kural başına en fazla 4 koşul, yalnızca **VE**.

**İki temel gerçek:**

1. **Koşullar olay akışına bakmaz, anlık duruma bakar.** `index.ts:234` durumu doğrudan
   `store.getDevice(id).state[property]`'den okur. Bu yüzden `isInterestingEventProperty`
   listesinde **olmayan** özellikler (`temperature`, `humidity`, `illuminance`, `co2`, `battery`)
   koşul olarak **bugün bile** okunabilir durumda. Sayısal eşik için yeni veri yolu gerekmiyor.
2. **Kanal bazında "ne zamandır bu değerde" bilgisi hiçbir yerde yok.** `DeviceStore` yalnızca
   cihaz başına `updatedAt` (:429) tutuyor. Süre koşulunun tek gerçek altyapı maliyeti bu.

---

## 2. Tasarım kararları

### 2.1 Süre ayrı bir tür değil, `deviceState`'in alanı

Kullanıcının istediği iki cümle aynı şeyin iki yüzü:

- *"hareket 1 dakikadır aktifse"* → `occupancy = true`, `forSeconds: 60`
- *"10 dakikadır hareket yoksa"* → `occupancy = false`, `forSeconds: 600`

İkisi de **"değer şu an X **ve** en az N saniyedir X"**. Ayrı bir `duration` türü açmak aynı
ölçütü iki yerde yazmak olurdu. Karar: `deviceState` koşuluna `forSeconds?: number` eklenir.
Alan yoksa davranış bugünküyle **birebir** aynı — geriye tam uyumluluk.

Sayısal eşikle de birleşir: *"sıcaklık 5 dakikadır 25'in üstündeyse"*.

### 2.2 Sayısal eşik tetikleyiciden koşula taşınır

`AutomationDeviceStateTrigger` içinde `above`/`below` zaten var ve kenar semantiğiyle yazılmış
(`automations.ts:300–318`, `numericValue()` `automation-engine.ts:236`). Koşulda semantik
**kenar değil, o anki değer**: "şu an eşiğin üstünde mi". `numericValue()` aynen yeniden kullanılır.

`equals`/`not`/eşik üçlüsünden **tam biri** verilir; `above`+`below` birlikte "aralıkta" demektir.

### 2.3 Güneş ayrı bir tür değil, `timeRange`'in ucu

Kullanıcının notu belirleyici: *"cihazların çoğu yerel gün doğumu ve gün batımına göre ayarlanıyor…
bir set olarak sunset/sundown kullanabiliriz"*. Yani istenen "karanlık mı" bayrağı değil,
**gün batımı→gün doğumu aralığı**.

Bu zaten `timeRange`'in ta kendisi; farkı uçların sabit `HH:MM` yerine hesaplanan bir an olması.
Karar: **yeni tür açılmaz**, `timeRange`'in uçları genişletilir:

```jsonc
{ "type": "timeRange",
  "from": { "event": "sunset",  "offsetMinutes": -15 },
  "to":   { "event": "sunrise", "offsetMinutes": 0 },
  "days": [1,2,3,4,5,6,7] }
```

- `from`/`to` ya bugünkü `"HH:MM"` **dizesi** ya da `{ event, offsetMinutes }` **nesnesi** olur.
  Eski dosyalar dokunulmadan geçerli kalır.
- Uçlar karışabilir: *"gün batımından 23:00'a kadar"* tek koşulla ifade edilir.
- `offsetMinutes` sınırı güneş tetikleyicisiyle aynı: ±240 (`maxAutomationSunOffsetMinutes`).

**Gece yarısını aşma artık dize karşılaştırmasıyla belirlenemez.** Bugünkü `from > to` testi
(`automation-engine.ts:175`) yerine iki uç da **gün içi dakikaya** çözülür, sarma sayısal
karşılaştırmayla bulunur. Gün ölçütü yine "aralığın **başladığı** gün"e bakar (mevcut kural korunur).

**Konum yoksa** koşul `false` döner ve sebebi günlüğe düşer — `sun` tetikleyicisinin mevcut
davranışıyla aynı (`inactiveReason`).

### 2.4 VEYA: kural seviyesinde tek anahtar

`docs/otomasyon-plani.md` §5.3'teki yer tutucu gerçekleşir:
`Automation.conditionMode?: "all" | "any"`, varsayılan `"all"`. Koşul başına değil, **kural
başına** tek anahtar — SmartThings modeli. Arayüzde "VEYA" kelimesi geçmez:
**"Hepsi doğruysa" / "Herhangi biri doğruysa"**.

`any` modunda koşul listesi boşsa sonuç `true` (koşulsuz kural), `all` ile aynı.

### 2.5 Yeniden başlatma: süre sıfırdan sayılır

Süre bilgisi bellekte tutulur, diske yazılmaz. Sunucu yeniden başladığında geçmiş yoktur.
Karar: **ilk gözlem an'ı taban kabul edilir** — yani kural yeniden başlatmadan sonra ancak
`forSeconds` kadar süre geçince doğrulanır. Sebep günlüğe yazılır
("süre bilgisi yeniden başlatmadan beri N saniye").

Alternatifi (bilinmiyorsa `true` saymak) ışığı yanlış anda tetiklerdi; kapalı tarafa düşmek doğru.
Diske yazmak (`autoOff` gibi) her durum değişiminde dosya yazmak demekti — pahalı, değmez.

---

## 3. Veri modeli değişikliği

```ts
/** Uç: sabit saat ya da güneşe göreli an. */
export type AutomationTimePoint =
  | { kind: "clock"; at: string }                                   // "HH:MM"
  | { kind: "sun"; event: "sunrise" | "sunset"; offsetMinutes: number };

export interface AutomationTimeRangeCondition {
  type: "timeRange";
  from: AutomationTimePoint;
  to: AutomationTimePoint;
  days?: number[];
}

export interface AutomationDeviceStateCondition {
  type: "deviceState";
  deviceId: string;
  property: string;
  equals?: JsonScalar;
  not?: JsonScalar;
  above?: number;            // YENİ
  below?: number;            // YENİ
  /** Değerin kesintisiz sağlanması gereken süre; 1..86400. */
  forSeconds?: number;       // YENİ
}

export interface Automation {
  // …
  conditionMode?: "all" | "any";   // YENİ, varsayılan "all"
}
```

**Yazma biçimi kararı:** doğrulama girdide hem `"19:00"` dizesini hem `{kind,…}` nesnesini kabul
eder, **normalize edip her zaman nesne olarak yazar**. Böylece motor ve arayüz tek şekille uğraşır,
eski dosyalar ilk kaydetmede kendiliğinden yükselir.

---

## 4. Motor değişikliği

`evaluateAutomationConditions()` imzası büyür:

```ts
evaluateAutomationConditions(
  conditions: AutomationCondition[],
  date: Date,
  readState: (deviceId, property) => JsonScalar | undefined,
  options?: {
    mode?: "all" | "any";
    times?: SunTimes | null;
    stateSince?: (deviceId: string, property: string) => Date | null;
  }
)
```

- `times` — motor zaten her turda hesaplıyor (`automation-engine.ts:436`); aynı değer geçirilir.
- `stateSince` — **yeni bağımlılık**, aşağıdaki defter.
- `mode` — `all` (varsayılan) / `any`. `any` modunda `results` yine **tüm** koşulları taşır ki
  günlükte "hangisi tuttu" görünsün; `ok` hesabı `some(...)` olur.

### 4.1 Değişim defteri — `DeviceStore`

Süre için gereken tek veri: **(deviceId, property) → değerin kaçtan beri aynı olduğu.**

Yeri `DeviceStore`. Gerekçe: durum haritasını zaten o tutuyor (:429) ve olay akışının dar
"interesting" listesinden bağımsız — böylece `temperature` gibi olay üretmeyen özellikler için de
süre çalışır. Motorda tutmak bu özellikleri kapsam dışı bırakırdı.

- Yapı: `Map<string, { value: JsonScalar; since: Date }>`, anahtar `deviceId|property`
  (`automations.ts`'deki `automationChannelKey` ile aynı biçim).
- Yazma: durum payload'u işlenirken, **yalnızca değer gerçekten değiştiyse** `since` tazelenir.
  Aynı değerin yeniden bildirilmesi süreyi sıfırlamaz — "1 dakikadır hareket var" ancak böyle çalışır.
- Okuma: `store.stateSince(deviceId, property): Date | null`.
- **Bellek tavanı:** cihaz × özellik ile sınırlı ve cihaz silinince temizlenir; ayrıca üst sınır
  (ör. 5000 kayıt) konur, taşarsa en eski atılır — durum haritasının kendisiyle aynı büyüklük sınıfı.
- `apps/runtime` (`.cjs`) tarafında **değişiklik yok**: runtime derlenmiş çekirdeği çalıştırıyor.

---

## 5. Arayüz değişikliği

Sihirbazın koşul kolu bugün üç aşama: `cond` (tür seçimi) → `condTime` | `condDevice`+`condState`.

| Ekran | Değişiklik |
|---|---|
| `cond` — tür seçimi | Üçüncü seçenek: 🌗 **"Hava karanlık/aydınlıkken"**. (Sayısal eşik ve süre ayrı seçenek **değil** — mevcut iki ekranın içine girer.) |
| `condTime` | Her uç için "Saat / Gün doğumu / Gün batımı" seçici + dakika kaydırma. Hazır çipler: **"Hava karanlıkken"** (batış→doğuş), **"Gündüz"** (doğuş→batış), **"Özel"**. Konum girilmemişse güneş seçenekleri kilitli, altta Ayarlar'a bağlantı (`sunSummary()` zaten mevcut). |
| `condState` | Sayısal özellikte değer listesi yerine **karşılaştırma satırı**: `üstünde / altında / arasında` + iri artır-azalt. Boolean/enum özellikte bugünkü liste aynen kalır. |
| `condState` (alt) | **"… ve şu kadar süredir böyleyse"** sessiz satırı (varsayılan kapalı) → süre seçici. |
| Koşul bölümü başlığı | Koşul sayısı ≥ 2 olduğunda **"Hepsi / Herhangi biri"** ikili anahtarı görünür. Tek koşulda gizli — anlamsız. |

`automationConditionLine()` her yeni biçim için düz cümle üretmeli; i18n uyarısı geçerli
(§7 — parça birleştirme yok, **tam şablon anahtarı**). Yeni anahtarlar `tr` ve `en`'e birlikte girer.

---

## 6. Uygulama sırası

**Durum (2026-08-07):** Adım 1–5 canlıda (`7fa9af7`). Adım 6 (süreli tetikleyici, §10'un ilk
satırının kapsama alınması) yazıldı ve commit bekliyor; `npm test` 496/496 + runtime 47/47 yeşil.
Kalan tek şey kullanıcının **1024×640'ta göz testi** (§9.4) ve ardından sunucu + tablet deploy'u.

Her adım kendi testleriyle kapanır; `npm test` tek kapı. Adımlar birbirine sırayla bağlı
(hepsi `automations.ts` + `automation-engine.ts` + `index.html` dosyalarına dokunuyor) —
**paralel worker verilmez, sıraya konur.**

### Adım 1 — Sayısal eşik (en ucuz, en çok işe yarayan)
`above`/`below` + doğrulama (üçlü dışlama, `above < below`) → motorda `numericValue()` ile
karşılaştırma → `condState` ekranında karşılaştırma satırı → locale → testler.
*Yeni altyapı yok.*

### Adım 2 — VEYA anahtarı
`conditionMode` alanı + doğrulama + motorda `some/every` + bölüm başlığındaki ikili anahtar.
*Küçük ve bağımsız; erken alınırsa sonraki adımların testleri onu da kapsar.*

### Adım 3 — Güneşli zaman aralığı
`AutomationTimePoint` + normalize eden doğrulama + motorda dakikaya çözme ve sarma mantığının
yeniden yazımı + `condTime` ekranı + hazır çipler + konum yoksa kilit.
*En riskli adım: mevcut `timeRange` davranışı bozulmamalı — eski dize biçimi için var olan
testler aynen geçmeli, üstüne nesne biçimi testleri eklenir.*

### Adım 4 — Süre koşulu
`DeviceStore` değişim defteri + `stateSince()` → `forSeconds` doğrulaması → motorda kontrol ve
"yeniden başlatmadan beri" sebebi → `condState` altındaki süre satırı.
*Tek yeni altyapı burada; en sona bırakıldı ki önceki adımlar onu beklemesin.*

**Saha testinden gelen iki arayüz şartı (§9.2):** "sonra kapat" ile "şu kadar süredir" ekranda
**açıkça ayrılacak**, ve koşul adımındaki cihaz listesi neyi neden listelediğini belli edecek.

### Adım 5 — Gün doğumu ve gün batımı tek kuralda
Saha testinin ortaya çıkardığı eksik (§9.1). Veri modeli **değişmez**; motor `sun` tetikleyicisine
eşleşme değeri verir, sihirbaz anahtar akışındaki eşleme formunu güneş için de açar.

## 7. Test yükü

- `src/automations.test.ts` — her yeni alan için kabul + ret; **eski biçimli kuralların hâlâ
  doğrulandığı** (geriye uyumluluk) testleri şart.
- `src/automation-engine.test.ts` — enjekte edilen saat + sahte `stateSince` ile: eşik sınırları,
  süre eşiği (tam sınırda, altında, üstünde), gece yarısını aşan güneş aralığı, konum yokluğu,
  `any`/`all` farkı.
- `src/device-store.test.ts` — defter: aynı değer yeniden bildirilince `since` **değişmez**,
  değer değişince tazelenir, cihaz silinince temizlenir.
- `src/dashboard-copy.test.ts` — markup ve CSS metnini birebir doğruluyor; **her arayüz adımında
  bu assertion'ları güncellemek işin parçası.**

## 9. Saha testi bulguları — 2026-08-06

Adım 1–2 canlıya çıkmadan önce kullanıcı panelde dört kural kurdu. Üçü sorunsuz
(`Corridor Detector → Corridor light` iki ayrı sensörle: `presence` tetikleyici + `occupancy`
koşulu, tek denemede kuruldu). Kalan iki bulgu aşağıda.

### 9.1 Güneş tek kuralda iki yöne gitmiyor → Adım 5

Kullanıcı "gün doğumunda şunu, gün batımında şunu yap" demek istiyor; bugün **iki ayrı kural**
kurmak zorunda (`Test Light sunrise`, tek `sun` tetikleyicisi + `TOGGLE`).

**Çözüm — yeni alan gerekmiyor.** Duvar anahtarı için yazılmış `when: { equals }` (§5.4,
`docs/otomasyon-plani.md`) aynen kullanılır. Tek gereken: motor bir `sun` tetikleyicisi
ateşlendiğinde **eşleşme değeri olarak olay adını** (`"sunrise"` / `"sunset"`) versin.

```jsonc
"triggers": [
  { "type": "sun", "event": "sunset",  "offsetMinutes": 0, "days": [1,2,3,4,5,6,7] },
  { "type": "sun", "event": "sunrise", "offsetMinutes": 0, "days": [1,2,3,4,5,6,7] }
],
"actions": [
  { "type": "device", …, "value": "ON",  "when": { "equals": "sunset"  } },
  { "type": "device", …, "value": "OFF", "when": { "equals": "sunrise" } }
]
```

- Bugünkü kural — *"zaman tetikleyicisinde eşleşecek olay değeri yoktur, `when` taşıyan eylemler
  atlanır"* — yalnızca `time` için sürer; `sun` için eşleşme değeri artık vardır.
- `time` tetikleyicisi bilinçli olarak dışarıda: iki farklı saat için eşleme formu kurmak
  "her satır bir kural" sadeliğini bozar, kullanıcı da bunu istemedi.
- Sihirbaz: güneş seçilince **iki olayı birden** sunar ve eşleme formunu açar.
  Varsayılan **batınca Aç / doğunca Kapat** (ışık için doğal eşleme).
- `TOGGLE` değeri bu formda anlamsızlaşır (iki olay da aynı şeyi yapar); eşleme formunda
  Aç/Kapat çifti öne çıkar.

### 9.2 İki farklı "süre" birbirine karışıyor → Adım 4'ün arayüz şartı

Kullanıcı "sensör X süredir Y durumundaysa" aradı, bulamadı (henüz yok), yerine `autoOff` ile
kurulmuş `Toilet PIR Detector → Toilet Fan` kuralına baktı ve ikisini aynı şey sandı.

Ekranda **ayrılması gereken iki kavram**:

| Kavram | Cümlesi | Nerede |
|---|---|---|
| `autoOff` (mevcut) | *"…yap, **sonra** şu kadar süre içinde geri al"* | Eylem adımı, eylemin sonucu |
| `forSeconds` (Adım 4) | *"…**şu kadar süredir** böyleyse çalış"* | Koşul adımı, çalışma ölçütü |

İkisi ayrı adımda duruyor ama aynı kelimeyi ("süre", "dakika") kullandıkları için karışıyor.
Arayüz kararı: her ikisinin de etiketi **zaman yönünü** taşısın — "sonra" ve "…dir/…dır" —
ve süre seçicilerinin görsel dili birbirinden ayrışsın.

### 9.3 Koşul adımında cihaz seçimi belirsiz

Kullanıcı koşul eklerken tetikleyicideki cihazı yeniden aramak zorunda kaldı ve listedeki
cihazların **neden orada olduğu** belli değildi.

Yapılacak (Adım 4 turuyla birlikte):
- Listenin başında **tetikleyicide kullanılan cihaz** kısayolu ("Aynı cihazın durumu").
- Cihaz satırında hangi özelliğin koşula gireceği önden görünsün (bugün cihaza girmeden belli değil).
- Boş liste hâli sessiz kalmasın: koşula uygun özelliği olmayan cihaz neden listelenmiyor, tek
  satırla söylensin.

### 9.4 Panel oturumunun handoff'undan gelen iki kısıt

`HANDOFF-2026-08-07.md`'den, bu işi doğrudan bağlayan iki madde:

- **Tabletin gerçek CSS alanı 1024×640** (Nokia T10, 1280×800 @ 200 dpi). Koşul adımı bu turlarda
  üç yeni satır kazandı (eşik karşılaştırması, iki uçlu güneş seçimi, süre satırı) — **göz testi
  1024×640'ta yapılmalı**, 1280×800'de "sığıyor" görünen taşabilir. Ölçüler sabit px değil
  `clamp()` + viewport birimiyle yazılır. **`color-mix()` kullanılmaz** (Android WebView
  desteklemiyor); bu turların diff'i kontrol edildi, temiz.
- **Güneş hesabı host saat dilimine bağlı.** Handoff'un "karar bekleyenler" maddesi: tablet
  Londra, sunucu İstanbul; sunucu düşüp tablet devralırsa gün doğumu/batımı iki saat kayar.
  Bu iş güneşi **bir yerden üç yere** çıkarıyor (mevcut `sun` tetikleyicisi + Adım 3'ün güneş uçlu
  aralığı + Adım 5'in eşleme formu), yani kayma da üç yerde görünür hale gelir. Ayrı bir iş olarak
  çözülmeli: ya konumdan saat dilimi türetilir ya güneş anları UTC üzerinden hesaplanır.
  **Bu planın kapsamında değil**, ama failover senaryosunda beklenen davranış budur.

---

## 10. Bilinçli olarak kapsam dışı

| Fikir | Neden |
|---|---|
| ~~**Süreyi tetikleyici yapmak**~~ | **Artık kapsamda (Adım 6).** `AutomationDeviceStateTrigger.forSeconds` ile ifade edilir: hedefi olan durum tetikleyicisi, değer kesintisiz o kadar sürerse motorun turunda bir kez ateşler. Tek atış kilidi kanal hedeften çıkınca sıfırlanır; `autoOff: { mode: "idle" }` (§9) yerini almaz, üstüne binebilir. |
| Koşul başına VE/VEYA (iç içe mantık) | Dallanma demek; `docs/otomasyon-plani.md` §2.4 sınırını aşar. |
| Süre bilgisini diske yazmak | Her durum değişiminde dosya yazımı; kazanç yeniden başlatma sonrası tek bir pencere. §2.5. |
| Zigbee dışı koşullar (telefon konumu, hava durumu, takvim) | `docs/otomasyon-plani.md` §2.1–2.2 kapsam kararı. |
