# Villa Bridge — Tablet Standalone ve Otomatik Failover Planı

Bu doküman, tabletin (Nokia T10, `com.villabridge.android`) sunucu düştüğünde kendi servislerini
devralması, sunucu döndüğünde bırakması için gereken tasarımı taşır. Araştırma 2026-08-02'de
kod okunarak yapıldı; **kod yazılmadı, gerçek eve dokunulmadı**.

Kullanıcının cümlesi:

> "Android'in standalone çalışabildiğini unutmayalım. Tabletin ağdaki kontrolünü sürekli yapıyor.
> Server'ın varlığından haberdar oluyor olması lazım. Server düştüğünde otomatik kendi servislerini
> yeniden başlatır. Tekrar server'ı görürse kendi servislerini durdurup server'ı kullanmaya başlar."

**Revizyon notu (2026-08-02):** Bu planın ilk sürümü, koordinatörün TCP portunun *fiziksel kilit*
olduğunu varsayıyordu. Ölçüm bu varsayımı çürüttü (§1.2). Doküman, **koordinatör hakkında hiçbir
şey varsaymayan** bir sahiplik protokolüne göre yeniden yazıldı.

**Güncelleme (2026-08-02, ikinci tur):** Dört açık soru kullanıcı tarafından karara bağlandı
(devralma eşiği, release onayı gelmemesi, `auth.json`, Matter) — özet §10.1, ayrıntı ilgili
bölümlerde. **Faz 1 tamamlandı** (commit `c81a0e2`, §9).

---

## 1. Bugünkü durum

### 1.1 Ölçülen topoloji

Tablet `192.168.0.67`, diagnostics `/api/ready`:

```json
{"mode":"android-monitor","provisioning":{"provisioned":false,
  "reason":"Waiting for config/villa-bridge.yaml."},
 "mqtt":{"status":"disabled"},"core":{"status":"remote","endpoint":"http://192.168.0.91:8091/"},
 "monitor":{"address":"192.168.0.91","serverMode":"direct"}}
```

Sunucu `192.168.0.91` (LXC, `villa-bridge.service`, `mode: direct`) → **koordinatörün sahibi o**.
Koordinatör ağ üzerinde tek: SLZB `192.168.0.248:6638`.

### 1.2 Koordinatör kilidi ölçümü — varsayım çürüdü

Sunucu koordinatörü tutarken ikinci bir TCP bağlantısı denendi:

| Gözlem | Sonuç |
|---|---|
| `connect(192.168.0.248:6638)` ikinci istemci | **Kabul edildi**, 0.78 sn |
| İkinci istemciden tek bayt gönderilmedi | Yine de **64 bayt ZNP koordinatör verisi aktı** |
| Sunucunun mevcut oturumu | **Hiç etkilenmedi**: aynı soket, `NRestarts=0`, log temiz, HTTP 200 |

Yani SLZB akışı ikinci istemciye **fan-out** ediyor: sıraya koymuyor, eskiyi düşürmüyor,
reddetmiyor. Sonuçlar:

- **"Bağlanabildim ⇒ kimse tutmuyor" çıkarımı geçersizdir.** Bağlantının başarılı olması sahiplik
  hakkında hiçbir şey söylemez. İlk sürümdeki §4.1 kilidi bu yüzden tamamen kaldırıldı.
- Buna karşılık **ters yön geçerlidir**: fan-out'ta veri akıyorsa biri koordinatörü *sürüyor*
  demektir. Bu, devralmayı **yasaklamak** için kullanılabilecek bir kanıttır (§4.5).

### 1.3 Kapsam kararı: koordinatör hakkında varsayım yok

> "Bu benim evimdeki Zigbee koordinatör. Ama herkesin evinde aynısı yok. … Biz herhangi bir Zigbee
> koordinatör cihazı olduğunda nasıl çalışacağımızı düşünerek yapmamız lazım. … şu an olabilecek en
> güvenli yolu seçmemiz lazım." — kullanıcı

Sahada dört sınıf birden mümkün:

| Sınıf | Örnek | İkinci bağlantıda | Kilit değeri |
|---|---|---|---|
| `exclusive-os` | Yerel USB seri adaptör | OS `EBUSY`/`flock` ile engeller | Var ama yalnızca tek makinede |
| `exclusive-net` | İkinci TCP'yi reddeden bridge | Reddedilir | Var, güvenilir |
| `preemptive` | Eskiyi düşüren bridge | Kabul, **eski oturum ölür** | Yoklamanın kendisi zararlı |
| `fan-out` | **SLZB (ölçüldü)** | Kabul, iki istemci de akışı görür | Yok |

Tasarım **dördünde de** güvenli olmalı. Bu yüzden tek yazarlığı artık donanım değil, **LAN üzerinde
konuşulan bir sahiplik protokolü** garanti eder. Cihaza özel davranış (§4.5) yalnızca *iyileştirme*
katmanıdır, temel tasarımın koşulu değildir.

### 1.4 Var olan mekanizmalar

| Mekanizma | Yer | Not |
|---|---|---|
| LAN keşfi (UDP 8093 broadcast + `/api/discovery` doğrulaması) | `apps/runtime/lan-discovery.cjs` | Çalışıyor, kimlik doğrulaması var |
| Sunucu tarafı duyurucu | `src/lan-discovery.ts` | Yalnızca `role === "server"` iken açılır |
| Standalone yığını başlatma (MQTT broker → self-test → core → Matterbridge) | `apps/runtime/main.cjs:479-549` | Sırası doğru |
| Temiz durdurma yolları | `main.cjs:554-574`, `src/index.ts:921-932`, `direct-zigbee-source.ts:490` | `source.stop()` koordinatör oturumunu kapatıyor ve `bridge/state: offline` yayınlıyor |
| Android süreç yeniden başlatma | `NodeRuntimeService.kt:176-212` | `killProcess` + `START_STICKY`, dakikada en fazla 3 |
| Yerel kontrol ucu | `POST /api/android/runtime/shutdown` | Loopback + `controlToken` şart |

### 1.5 Olmayan mekanizmalar (işin tamamı bunlar)

1. **Sahiplik protokolü yok.** Bugün hiçbir düğüm "koordinatör bende" diye bir şey söylemiyor.
   Duyuru kaydı (`VillaBridgeDiscoveryRecord`, `src/lan-discovery.ts:10-16`) yalnızca
   `role`/`mode`/`dashboardPort` taşıyor; sahiplik, epoch, canlılık damgası yok.
2. **Periyodik izleme yok.** *(Faz 2'de kapatıldı; §9.)* `discoverVillaBridgeServer()` yalnızca
   açılışta bir kez çağrılıyordu (`main.cjs:443-448`) ve sonuç bir daha sorgulanmıyordu. Artık
   `peer-watch` üç kanalı 10 saniyede bir yokluyor; **devralma hâlâ yok**.
3. **Mod geçişi yok.** `runtime.monitor` bir kez set edilince değişmiyor; monitor ↔ standalone
   arası geçiş kodu hiç yok. Bugün geçiş = uygulamayı elle yeniden başlatmak.
4. **Standalone tablet görünmez — ön koşul kusuru.** *(Faz 1'de kapatıldı, commit `c81a0e2`; §9.)*
   `buildCoreEnvironment` Android'e
   `VILLA_BRIDGE_NODE_ROLE=android` veriyor, `startLanDiscoveryResponder` da `role !== "server"`
   ise `null` dönüyor (`src/lan-discovery.ts:49`). Devralan tablet **kendini ağda duyurmuyor**.
   "Talep et ve bekle" de "düzenli devir" de bunsuz imkânsızdır; **bu satırın düzeltilmesi tüm
   planın ön koşuludur** (Faz 1).
5. **Sunucu başlangıç sırası tehlikeli — ön koşul kusuru.** *(Faz 1'de kapatıldı, commit `c81a0e2`;
   §9.)* `src/index.ts`'te `source.start()` (938)
   → `app.listen` (954) → keşif duyurucusu (956). Koordinatör başkasındaysa `source.start()` patlar,
   süreç ölür, sunucu **hiç duyuru yapamaz** ve geri alma el sıkışmasını hiç başlatamaz. Talep ve
   el sıkışma **`source.start()` öncesine** taşınmak zorundadır (Faz 1).
6. **Tablet provision değil.** `config/villa-bridge.yaml` yok → standalone imkânsız.
7. **Durum dosyaları ayrı ve senkronsuz.** Her düğüm `dirname(VILLA_BRIDGE_CONFIG)` altına yazıyor
   (`src/index.ts:51-75`): sunucuda `/var/lib/villa-bridge/runtime/`, tablette
   `villa-data/runtime/`. Aralarında hiçbir bağ yok.

### 1.6 Standalone testinin kanıtladıkları ve iki kalıcı bulgu (2026-08-03)

Tablet, sunucu kapalıyken elle standalone çalıştırıldı. Sonuç: **tablet 22 cihazla evi tek başına
sürdü** — `coordinatorStatus: "ready"`, otomasyon motoru çalıştı, panel yerel geldi. §1.2'nin
`fan-out` ölçümüyle birlikte bu, "frame counter donanımda tutuluyor" beklentisini destekliyor:
devralmada ağın bozulmadığı gözlendi. **Asıl risk frame counter değil, config uyuşmazlığıdır**
(§5.1): tabletteki ağ parametreleri sunucudakiyle birebir aynı olmazsa ağ yeniden kurulur.

Testten iki kalıcı bulgu çıktı:

1. **`node.state` gerçeği söylemiyordu (Faz 2'de düzeltildi).** `src/index.ts` sahiplik beyanını
   **rolden** türetiyordu (`if (nodeRole === "server") state = "owner"`). Tablet koordinatörü
   gerçekten devraldığı hâlde kendini `standby` ilan etti. Sahiplik protokolünün tamamı bu alanı
   okuduğu için Faz 3+ bu hatayla çalışamazdı. Yeni kural: `state`, **koordinatör sahipliğinden**
   türetilir (`applyCoordinatorOwnership`, `src/lan-discovery.ts`) — rolden bağımsız, sunucu ve
   Android için aynı.
2. **Android runtime'ı `POST /api/android/runtime/shutdown` ile kapatmak yetmiyor.**
   `NodeRuntimeService` süreci **otomatik yeniden başlatıyor** (`NodeRuntimeService.kt:178-185`,
   `killProcess` + `START_STICKY`). Durdurmak için önce `villa-runtime-desired-state` dosyası
   `STOPPED` yapılmalı. `ACTION_STOP` intent'i `exported="false"` olduğundan adb'den
   tetiklenemiyor. **Bu, Faz 5 devralma/geri çekilme kodunun bilmek zorunda olduğu bir kısıttır**
   (§4.7'nin "süreç ölümüyle değil, süreç içi durum makinesiyle" kuralının pratik karşılığı):
   tabletin yığınını durdurmak/başlatmak isteyen her kod, önce desired-state dosyasını yazmalı.

---

## 2. Tasarımın ilkeleri

Bu altı ilke, aşağıdaki her bölümün gerekçesidir. Çelişki çıkarsa ilkeler kazanır.

| # | İlke | Anlamı |
|---|---|---|
| 1 | **Talep et ve bekle** | Hiçbir düğüm koordinatöre doğrudan bağlanmaz. Önce LAN'a sahiplik talebi duyurur, kısa bir pencere dinler; başka sahip varsa **bağlanmaz**. |
| 2 | **Sabit öncelik** | Eşzamanlı talepte **sunucu kazanır**. Tie-break sabit ve kodda gömülü; iki düğümde quorum zaten imkânsız, oylama denemesi yapılmaz. |
| 3 | **Muhafazakâr devralma** | Tablet, **çok kanallı kanıt** (UDP beacon sessizliği + HTTP erişilemezliği + MQTT erişilemezliği) ve **uzun eşik** olmadan devralmaz. |
| 4 | **Düzenli devir** | Sunucu dönünce koordinatöre bağlanmadan **önce** tabletten bırakmasını ister ve onay bekler. Onay gelmezse **bağlanmaz**, uyarır ve beklemeye devam eder. Ne otomatik ne elle zorlama var — "zorla devral" düğmesi yok (§4.4). |
| 5 | **Şüphede dur** | Belirsizlikte hiçbir taraf bağlanmaz. **Kesinti, bozulmuş Zigbee ağından iyidir.** |
| 6 | **Cihaza özellik opsiyonel** | Koordinatör sınıfı çalışma zamanında tespit edilirse tasarım *iyileşir*; tespit edilemezse tasarım yine çalışır. Hiçbir cihaz davranışı ön koşul değildir. |

---

## 3. Sahiplik protokolü (kilit)

Kilit artık koordinatörde değil, **LAN'da konuşulan durumda**. Taşıyıcı, var olan UDP 8093
kanalıdır; kayıt şeması genişletilir.

### 3.1 Genişletilmiş duyuru kaydı

`VillaBridgeDiscoveryRecord` (`src/lan-discovery.ts:10`) şu alanları kazanır:

| Alan | Örnek | Neden |
|---|---|---|
| `nodeId` | `srv-91`, `tab-67` | Kalıcı, cihaza özgü; tie-break ve log için |
| `state` | `owner` \| `standby` \| `claiming` \| `releasing` | Sahiplik beyanı |
| `epoch` | 42 | Her sahiplik devrinde artan sayaç; bayat düğüm ayırt edilir |
| `coordinatorId` | `tcp://192.168.0.248:6638` özeti | Farklı koordinatörlü düğümler birbirini kilitlemesin |
| `priority` | `server` = 0, `android` = 1 | Sabit öncelik (İlke 2) |
| `sentAt` | epoch ms | Bayatlama tespiti |

`role !== "server"` kısıtı kalkar (§1.5/4): **her düğüm duyuru yapar**, `role` alanı bilgi olarak
kalır. Kilit kararı `state`/`priority` üzerinden verilir, `role` üzerinden değil.

*Uygulama durumu: bu şema Faz 1'de yazıldı (commit `c81a0e2`, §9).* `version` bilinçli olarak 1'de
bırakıldı (geriye dönük uyumlu), `role: "disabled"` hâlâ duyuru yapmıyor ve eksik alanlar `null`
= **"bilinmiyor"** anlamına geliyor — hiçbir koşulda "sahipsiz" diye okunmaz.

### 3.2 Beacon

Sahibi olan düğüm, `state: "owner"` kaydını **2 saniyede bir** yayınlar. Bir beacon **15 saniye**
duyulmazsa "sessiz" sayılır. Beacon, sahiplik alındığında **koordinatöre bağlanmadan önce**
başlar ve bırakıldığında durur — yani beacon her zaman gerçek sahipliğin *önünde* gider, arkasında
değil. Kaza hâlinde yanlış taraf fazladan beacon duyar, ki bu güvenli yöndür.

### 3.3 Talep akışı (claim)

1. Aday, 0-15 s rastgele jitter bekler (eşzamanlı adayları ayırır).
2. `state: "claiming"`, `epoch+1`, `priority`, `coordinatorId` ile CLAIM yayınlar.
3. **5 saniye** dinler. Pencerede:
   - `owner` beacon'ı duyulursa → **iptal**, `MONITOR`'a dön.
   - Daha yüksek öncelikli (`priority` küçük) CLAIM duyulursa → **iptal**.
   - Eşit öncelikli CLAIM duyulursa → `nodeId` sözlük sırasında küçük olan kazanır, diğeri iptal.
4. Pencere sessizse: `epoch` kalıcı olarak artırılır, `owner` beacon'ı başlatılır.
5. **1 saniye** daha beklenir (geç gelen beacon'a son şans), sonra core başlatılır.

Adım 5'in maliyeti bir saniye, kazancı: iki aday pencereyi aynı anda geçse bile ikincisi birincinin
beacon'ını görüp koordinatöre **hiç dokunmadan** vazgeçebilir.

### 3.4 Neden bu, koordinatörden bağımsız

Protokol koordinatörün ne yaptığına hiç bakmaz: ne bağlantı denemesi yapar, ne portun davranışını
varsayar. `exclusive-os`, `exclusive-net`, `preemptive`, `fan-out` — dördünde de aynı şekilde
çalışır. Cihaz kilidi olan sınıflarda (`exclusive-*`) protokol **ikinci bir savunma katmanı**
olarak kalır; olmayan sınıflarda **tek savunma** odur.

---

## 4. Failover durum makinesi

### 4.1 Durumlar

| Durum | Çalışanlar | Beacon | Panel |
|---|---|---|---|
| `MONITOR` | Yalnızca Android host + diagnostics | `standby` | Sunucunun paneli |
| `CLAIMING` | Talep penceresi, replika kopyalama | `claiming` | "Devralınıyor" |
| `STANDALONE` | MQTT broker + core (direct) + Matterbridge | `owner` | Yerel panel |
| `RELEASING` | Durdurma sırası, snapshot devri | `releasing` | "Sunucuya devrediliyor" |
| `DEGRADED` | Hiçbiri (devralma başarısız / şüphe) | `standby` | Hata + geri sayım |

Geçişler: `MONITOR → CLAIMING → STANDALONE → RELEASING → MONITOR`, ayrıca
`CLAIMING → DEGRADED → MONITOR` ve `STANDALONE → DEGRADED` (kendi core'u öldüyse).

### 4.2 İzleme ve eşikler

Monitor modunda **her 10 saniyede** bir yoklama. Bir yoklamanın **başarısız** sayılması için
**üç kanalın üçü birden** başarısız olmalı (İlke 3):

1. `owner` beacon'ı 15 saniyedir duyulmuyor,
2. Son bilinen sunucu IP'sine `GET http://<ip>:8091/api/discovery` başarısız (timeout 2000 ms),
3. Sunucudaki MQTT broker'a TCP bağlantısı kurulamıyor (timeout 2000 ms).

Üçünden biri bile başarılıysa yoklama **başarılıdır** ve sayaç sıfırlanır. Tek kanala güvenmek
Wi-Fi'da yanlış-negatif üretir; üçü birden aynı anda yalan söylemez.

| Eşik | Değer | Gerekçe |
|---|---|---|
| Beacon aralığı | 2 s | Ucuz; sahiplik değişimini hızla görünür kılar |
| Beacon bayatlama | 15 s | ~7 kayıp beacon; Wi-Fi kesintisine tolerans |
| Yoklama aralığı | 10 s | Wakelock zaten var; daha sık olmak pil kazandırmıyor |
| **Devralma eşiği** (karar) | **9 ardışık üç-kanallı başarısızlık = 90 s** | Kullanıcı kararı. Sunucu yeniden başlatmaları (~10-20 sn) ve güncellemeler rahatça tolere edilir; gerçek arızada ev 1,5 dakika içinde ayağa kalkar (İlke 3) |
| Claim öncesi jitter | 0-15 s | Eşzamanlı adayları ayırır |
| Claim penceresi | 5 s | Beacon aralığının 2,5 katı: en az iki beacon fırsatı |
| Bağlanma öncesi ek bekleme | 1 s | §3.3/5 |
| Geri çekilme (emniyet) histerezisi | 3 ardışık başarılı ≈ 30 s | Yalnızca sunucu-tetiklemeli el sıkışması hiç gelmezse |
| Release onayı zaman aşımı | 30 s, 3 deneme (≈ 90 s) | Sonrasında sunucu **bağlanmaz**, uyarı gösterir ve denemeye devam eder (§4.4 kararı, İlke 4) |
| Başarısız devralma sonrası bekleme | 60 s, 2 katına çıkarak, en fazla 15 dk | Koordinatöre saldırgan yeniden deneme yapılmaz |
| Kendi ağ sağlığı ön koşulu | Varsayılan ağ geçidine TCP/ICMP erişimi | Tabletin kendi Wi-Fi'ı düştüyse devralmaya kalkışmaz |

### 4.3 Devralma sırası (MONITOR → STANDALONE)

1. Devralma eşiği doldu (üç kanal, 90 s).
2. **Kendi ağ sağlığı** doğrulandı (ağ geçidine erişim var). Yoksa `MONITOR`'da kal.
3. **Opsiyonel veto** (§4.5): koordinatör sınıfı `fan-out` olarak biliniyorsa ve akışta ZNP verisi
   görülüyorsa → **devralma yapılmaz** (biri koordinatörü sürüyor).
4. Talep akışı çalıştırılır (§3.3). İptal edilirse `MONITOR`'a dön, sayacı sıfırla.
5. Replika → çalışma dizini kopyalanır (`villa-data/replica/` → `villa-data/runtime/`, §6).
6. Gömülü MQTT broker başlatılır, self-test beklenir.
7. Core başlatılır (`startVillaBridgeCore`) — **koordinatör bu adımda ele geçirilir**.
8. Matterbridge başlatılır (Faz 7'ye kadar kapalı, §7).
9. Panel yerel `127.0.0.1:8091`'e döner.

5-9 arasında herhangi bir adım hata verirse: açılmış olan her şey **ters sırayla** kapatılır,
`owner` beacon'ı durdurulur, durum `DEGRADED` olur, üstel geri çekilme başlar.

### 4.4 Geri verme sırası (STANDALONE → MONITOR)

Bu geçiş **sunucu tetiklemelidir**, tabletin "sunucuyu gördüm" demesiyle değil — çünkü koordinatör
tablette olduğu sürece sunucu zaten tam açılamaz (§1.5/5).

1. Sunucu açılışta, `source.start()`'tan **önce**, LAN'da `owner` beacon'ı dinler (en az 6 s).
2. Beacon varsa `POST /api/failover/release` gönderir (paylaşılan token ile).
3. Tablet: beacon'ı `releasing`e çevirir → Matterbridge'i durdurur → core'u durdurur
   (`source.stop()` koordinatör oturumunu kapatır, `bridge/state: offline` yayınlar) →
   durum snapshot'ını sunucuya devreder → broker'ı kapatır → beacon'ı `standby`ye çevirir.
4. Tablet `released` yanıtı verir; durum `MONITOR`.
5. Sunucu **beacon'ın gerçekten `standby`ye döndüğünü** 6 s dinleyerek doğrular, kendi beacon'ını
   `owner` olarak başlatır, sonra `source.start()` çağırır.
6. Tablet sunucuyu keşfeder, panelini sunucuya çevirir.

**Karar (kullanıcı): onay gelmezse bekle ve bildir.** Sunucu `released` alamazsa **zorlamaz**:

- `source.start()` **çağrılmaz** — sunucu koordinatöre hiçbir koşulda bağlanmaz.
- Servis `503` verir; arayüzde açık uyarı görünür: *"tablet henüz bırakmadı — koordinatör başka
  düğümde, elle müdahale gerekebilir."*
- Denemeye **devam eder**: 30 saniyede bir release isteği tekrarlanır (3 denemeden sonra da
  vazgeçmez, yalnızca uyarı kalıcılaşır). Tablet bıraktığı anda el sıkışma kendiliğinden tamamlanır.
- **"Zorla devral" düğmesi yoktur; otomatik zorlama yoktur.** Panelde böyle bir kaçış yolu
  bilinçli olarak sunulmaz.

*Gerekçe:* Zigbee ağı asla iki yazar görmemeli — bozulmuş ağın bedeli kesintinin bedelinden kat kat
yüksek (İlke 4-5). Bu kararın bedeli, kilitlenme hâlinde kesintinin uzamasıdır; kullanıcı bunu
açıkça kabul etti. Bu sırada ev tablet üzerinden çalışmaya devam eder.

### 4.5 Opsiyonel katman: koordinatör yetenek tespiti

**Bu katman tasarımın koşulu değil, iyileştirmesidir.** Hiç çalışmasa §3 protokolü tek başına
yeterlidir.

**Tespit nasıl yapılır (yalnızca sahip düğüm, kendi koordinatörü üzerinde):**

| Adım | Gözlem | Sınıf |
|---|---|---|
| Sahip, kendi koordinatörüne **ikinci** bir bağlantı açar | Yerel seri port, `EBUSY`/`flock` | `exclusive-os` |
| | TCP reddedildi / zaman aşımı | `exclusive-net` |
| | Kabul edildi, **sahibin kendi oturumu düştü** | `preemptive` |
| | Kabul edildi, veri aktı, sahip etkilenmedi | `fan-out` |

Tespiti **sahip** yapar; çünkü `preemptive` durumunda zararı ilk fark eden ve saniyeler içinde
yeniden bağlanabilecek olan odur. Standby düğüm asla yoklama yapmaz. Sonuç
`coordinator-capability.json`'a `serial.port` + adapter parmak iziyle yazılır; parmak izi
değişirse yeniden ölçülür. İlk çalıştırmada sınıf `unknown`'dır ve **hiçbir şey değişmez**.

**Sınıf ne kazandırır:**

- `fan-out` (SLZB, ölçüldü): standby düğüm akışa pasif bağlanıp ZNP trafiği görebilir. Bu, sunucu
  canlılığının **dördüncü kanalı**dır — ama yalnızca **veto** olarak kullanılır: *trafik varsa
  devralma yasak*. Trafik yokluğu asla devralma izni değildir (sahip boşta da durabilir).
- `exclusive-*`: sahiplik protokolüne ek olarak donanım kilidi de vardır; devralma hatası daha
  ucuza (temiz `EBUSY`) yakalanır. Panelde bilgi olarak gösterilir.
- `preemptive`: kullanıcıya **uyarı** gösterilir ("bu koordinatör ikinci bağlantıda mevcut oturumu
  düşürüyor; failover marjları daha dar"). Hiçbir otomatik yoklama yapılmaz.

### 4.6 Split-brain

Ağ bölünmesinde tablet sunucuyu göremez ama sunucu koordinatörü tutmaya devam eder.

- Tablet kendi ağ geçidine ulaşamıyorsa → devralma zaten anlamsız, `MONITOR`'da kalır (§4.3/2).
- Tablet ağa erişebiliyor ama sunucuyu üç kanalda da göremiyorsa → 90 s sonra **devralır**.
  Sunucu bu sırada gerçekten ayaktaysa, tablet onun beacon'ını da görmemiş demektir; bu, ilkelerin
  kabul ettiği artık risktir (İlke 3'ün eşiği bu riski ölçer, sıfırlamaz).
- Sunucu geri "görünür" olduğunda **kendi başına bağlanmaz** (§4.4): önce release ister. Yani
  bölünme iyileştiğinde iki yazar oluşamaz — çünkü dönen taraf her zaman izin ister.

Split-brain koruması artık koordinatörün davranışına değil, **§3 protokolü + §4.4 el sıkışması**
ikilisine dayanıyor.

### 4.7 Başarısız devralmada geri dönüş

Core `start()` hata verirse (herdsman bağlanamadı, ağ parametreleri uyuşmadı):

- Açılan her şey ters sırayla kapatılır, koordinatör oturumu bırakılır, beacon `standby`ye döner.
- Durum `DEGRADED`, panelde açık hata ve geri sayım.
- Android'in `killProcess` + `START_STICKY` yeniden başlatması bu hata için **kullanılmaz** —
  aksi hâlde tablet koordinatöre dakikada 3 kez saldırır. Failover mantığı süreç içinde
  durum makinesi olarak çalışmalı, süreç ölümüyle değil.
- Süreç yine de ölürse: yeniden açılışta düğüm **her zaman `standby` beacon'ı ile başlar** ve
  talep akışını baştan çalıştırır. Kalıcı `epoch` sayesinde eski sahiplik iddiası dirilmez.

---

## 5. Provisioning — tabletin standalone olabilmesi için gereken

### 5.1 Asgari dosya kümesi

`loadProvisioning()` (`apps/runtime/orchestration.cjs:39-145`) şunları arıyor:

```text
villa-data/
├── provisioning.json          # {"config":"config/villa-bridge.yaml","matter":true}
├── config/villa-bridge.yaml   # mode: direct  (zorunlu), zigbee.configurationFile zorunlu
└── zigbee/configuration.yaml  # klonlanmış Zigbee2MQTT ayarları
```

`config/villa-bridge.yaml` için şablon zaten var: `apps/android/provisioning/villa-bridge.yaml`.
Runtime bu dosyayı okuyup `villa-data/runtime/villa-bridge.yaml`'a yeniden yazıyor;
`zigbee.dataDir`, `mqtt.url`, `aliasesFile`, `matterbridge.wsUrl`, `http` alanlarını **kendisi
eziyor**. Yani şablonu olduğu gibi kopyalamak yeterli; tek kritik alan `mode: direct`.

`zigbee/configuration.yaml` için `src/config.ts:202-215` şu alanları **zorunlu** kılıyor
(eksikse "Doğrudan Zigbee modu için klonlanmış ağ ve SLZB ayarları eksik" hatası):

| Alan | Değer | Kaynak |
|---|---|---|
| `serial.port` | `tcp://192.168.0.248:6638` | Sunucudaki z2m `configuration.yaml` |
| `serial.adapter` | `zstack` / `ember` / … | aynı dosya (**doğrulanmadı** — sunucudaki gerçek değer okunmadı) |
| `advanced.channel` | sayı | aynı |
| `advanced.pan_id` | sayı | aynı |
| `advanced.ext_pan_id` | 8 baytlık dizi | aynı |
| `advanced.network_key` | 16 baytlık dizi | aynı |
| `mqtt.user` / `mqtt.password` | ikisi varsa gömülü broker kimlik ister | aynı |

Bu değerler **sunucudakiyle birebir aynı** olmalı. Farklı olursa tablet ağı yeniden kurar ve tüm
cihazlar düşer — bu, bu projedeki en pahalı hata.

### 5.2 Yetmez: cihaz veritabanı

Yukarıdakiler yığını **başlatmaya** yeter ama tabletin evi **tanıması** için `zigbee-herdsman`
veritabanı da gerekir: `villa-data/zigbee/database.db` (+ `coordinator_backup.json`). Onlarsız
tablet devraldığında panel boş bir ev gösterir; cihazlar ancak kendiliğinden haber verdikçe
belirir. **Doğrulanmadı:** herdsman'in mevcut ağa boş veritabanıyla bağlanınca ağı yeniden kurup
kurmadığı gerçek donanımda ölçülmedi — provisioning fazından önce kapatılması gereken belirsizlik.

### 5.3 Nasıl verilir — üç seçenek

| Yol | Artı | Eksi |
|---|---|---|
| **A. Elle dosya kopyalama** (`adb … run-as … cat >`, README'de tarif var) | Bugün, kod yazmadan yapılabilir; anahtar hiçbir yere düşmez | Tekrarlanabilir değil; ağ anahtarı değişirse tablet sessizce bayatlar |
| **B. Paketleme (APK içine gömme)** | Sıfır operasyon | **Reddedildi:** ağ anahtarı APK'ya girer, Git'e sızma riski, her ev için ayrı build |
| **C. Sunucudan çekme** (eşleştirme kodlu tek seferlik `GET /api/provisioning/bundle`) | Otomatik, tekrarlanabilir, `database.db` ve anahtar rotasyonu da aynı yoldan gelir | Yeni uç nokta + admin yetkisi + kısa ömürlü kod gerekir |

**Karar: önce A, hedef C.** A ile tablet elle provision edilir ve devralma gerçek donanımda
ölçülebilir hale gelir. §6'daki veri senkronu zaten sunucudan periyodik dosya çekmeyi
gerektirdiğinden, o kanal kurulduğunda provisioning de aynı kanalın ilk paketi olur. B kalıcı
olarak reddedildi — sır APK'ya girmez.

---

## 6. Veri tutarlılığı

Bugün sunucu ve tablet ayrı `automations.json`, `home-groups.json`, `home-favorites.json`,
`device-notes.json`, `device-images.json`, `aliases.json`, `auth.json` tutuyor
(`src/index.ts:51-75`, `dirname(configPath)` altında).

### 6.1 Seçenekler

| Seçenek | Değerlendirme |
|---|---|
| Çakışmayı yok say | Standalone'da kurulan kural sunucu dönünce kaybolur. Kullanıcının hedefiyle çelişir. **Ret.** |
| Son-yazan-kazanır (dosya bazlı zaman damgası) | Basit ama iki taraf da yazabildiği varsayımına dayanıyor; burada öyle değil. Gereksiz karmaşıklık. **Ret.** |
| Sunucu otoriter + tablet salt-okuma önbellek | Failover sırasında kural kurulamaz. "Tablet tek başına her şeyi yapabilmeli" hedefini karşılamaz. **Ret.** |
| **Sahiplikle taşınan snapshot** | Yazma hakkı **yalnızca sahipte**. Pasif düğüm replikayı periyodik çeker. Sahiplik devrinde snapshot devredilir. |

### 6.2 Öneri: sahiplikle taşınan snapshot (geçerliliğini koruyor)

Kural tek cümle: **koordinatörü kim tutuyorsa durumu da o yazar.** İki yazan olmadığı için
çakışma da yoktur — çakışma çözümü değil, çakışmanın imkânsızlığı tasarlanıyor.

**Revizyonun tek değişikliği burada:** tek yazarlığı artık **donanım değil, §3 protokolü** garanti
ediyor. Yani "sahip" tanımı = `owner` beacon'ını yayınlayan düğüm; koordinatöre TCP bağlanabilmek
değil. Sahiplik `epoch` ile sıralanır, böylece snapshot'ın hangi devirden geldiği belirsiz kalmaz:
**küçük `epoch`'lu snapshot asla büyük `epoch`'lunun üzerine yazılmaz.**

- `MONITOR` modunda tablet her 5 dakikada bir sunucudan snapshot çeker → `villa-data/replica/`.
  (Monitor modda tabletin paneli zaten sunucuya bakıyor, yerel yazma hiç olmuyor — replika
  bayatlama dışında risksiz.)
- `CLAIMING`'de replika `villa-data/runtime/`'a kopyalanır; devralma sonrası yazmalar tablette.
- `RELEASING`'de tablet snapshot'ı sunucuya devreder; **sunucu core'u başlatmadan önce** alır.

Snapshot kümesi:

| Dosya | Neden |
|---|---|
| `automations.json`, `home-groups.json`, `home-favorites.json` | Kullanıcının kurduğu her şey |
| `device-notes.json`, `device-images.json`, `aliases.json` | Kimlik ve sunum; alias'lar Matter/Alexa'ya da gidiyor |
| `zigbee/database.db`, `zigbee/coordinator_backup.json` | Devralan düğümün evi tanıması için zorunlu (§5.2) |
| `auth.json` | **Karar: kopyalanır.** Kesintide ev halkı tablete **aynı kullanıcı adı/parolayla** girebilsin; ayrı yerel hesap kurmak, tam da unutulacağı anda hatırlanması gereken ikinci bir parola demekti. Güvenlik bedeli §8'de açıkça yazılı |
| `device-events.json` | Kapsam dışı: yalnızca geçmiş, büyür, devri gecikmeye sokar |

Snapshot dosya bazında atomik yazılmalı (projede zaten `writeFile` + `rename` deseni var).

---

## 7. Bilinen sınırlar (failover'ın kapsamadıkları)

- **Matter — koşullu karar: tablet de devralır.** Kullanıcının seçimi, kesinti sırasında Apple
  Home / Alexa'nın da ayakta kalması yönünde: sahiplik devrinde Matterbridge kimliği (fabric,
  node id, sertifikalar) da devredilir ve replika kümesine girer. **Ancak bu karar bir ölçüme
  bağlıdır:** Matterbridge kimliğinin/fabric bilgisinin ikinci bir düğüme taşınabilirliği şu anda
  **ayrı bir görevde ölçülüyor**; sonuç bu dokümana henüz işlenmedi.
  - Ölçüm **taşınabilir** çıkarsa: karar uygulanır. Matterbridge deposu snapshot kümesine eklenir
    ve Zigbee koordinatörüyle **aynı tek-yazar kuralına** tabi olur — aynı kimlik iki yerde açık
    olamaz, `owner` beacon'ı olmayan düğüm Matterbridge'i asla başlatmaz.
  - Ölçüm **taşınamaz** çıkarsa: karar düşer, kullanıcıya geri dönülür ve "kesinti sırasında Matter
    kapalı" (`matter: false`; Zigbee, panel ve otomasyonlar çalışır, Matter çalışmaz) seçeneğiyle
    arasında **yeniden seçim** yapılır.
  - Her iki hâlde de uygulama Faz 7'dedir; Faz 4-5 boyunca tablette `matter: false` kalır.
- **Home Assistant taşınmaz.** HA broker adresine göre yapılandırılıyor; devralmada broker
  tabletin IP'sine geçiyor. Sabit sanal IP Android'de mümkün değil. Bugünkü provisioning
  şablonunda `homeAssistant.discoveryEnabled: false` — bu sınır şimdilik bedelsiz.
- **Panel oturumu.** Kullanıcı devralma anında panelde ise oturumu düşer; yeniden giriş gerekir.
- **İkiden fazla düğüm.** Tasarım iki düğüm (sunucu + tablet) için. Üçüncü düğüm eklenirse sabit
  öncelik (İlke 2) yetmez, gerçek bir kiralama/quorum gerekir. Kapsam dışı.

---

## 8. Riskler

| Risk | Karşı önlem |
|---|---|
| **Yanlış devralma → iki yazar → iç içe geçen ZNP çerçeveleri → Zigbee ağı bozulur** (en pahalı hata) | §3 sahiplik protokolü (talep et ve bekle); üç-kanallı kanıt + 90 s eşik; sunucu dönüşünde `source.start()` öncesi release el sıkışması; hiçbir yerde zorla devralma yok; `fan-out` sınıfında trafik vetosu (§4.5) |
| **"Bağlanabildim ⇒ boştur" yanılgısı** (ölçümle çürütüldü, §1.2) | Koordinatöre yoklama amaçlı bağlanma tasarımdan **tamamen çıkarıldı**; sahiplik yalnızca LAN protokolünden okunur |
| **`preemptive` koordinatör** (ikinci bağlantı eskiyi düşürür) | Standby düğüm hiçbir koşulda koordinatöre bağlanmaz; yetenek tespitini yalnızca sahip yapar (§4.5); sınıf `preemptive` çıkarsa kullanıcıya uyarı |
| **Bilinmeyen/yeni koordinatör donanımı** | Sınıf `unknown` iken tasarım tam olarak aynı çalışır; hiçbir cihaz davranışı ön koşul değil (İlke 6) |
| **Beacon kaybı → gereksiz devralma** | Beacon tek kanal değil; HTTP ve MQTT kanalları da başarısız olmadan sayaç ilerlemez; 15 s bayatlama toleransı |
| **Eşzamanlı talep** | Jitter + 5 s pencere + sabit öncelik (sunucu kazanır) + `nodeId` tie-break + bağlanma öncesi 1 s ek bekleme |
| **Bayat düğüm dirilmesi** (uzun kopuk kalan tablet sahiplik iddia eder) | Kalıcı `epoch`; küçük epoch'lu iddia ve snapshot yok sayılır |
| **Flapping** — sunucu inip inip kalkıyor | 90 s devralma, 30 s emniyet geri çekilmesi; başarısız devralmada üstel geri çekilme (60 s → 15 dk) |
| **Boş `database.db` ile devralma** — ağın yeniden kurulması riski | Replikaya `database.db` dâhil; herdsman'in boş veritabanı davranışı **önce ölçülecek** (§5.2) |
| **Ağ anahtarının tablete kopyalanması** | App-private, device-protected dizin, `0700`/`0600`; APK'ya gömme kalıcı olarak reddedildi (§5.3-B) |
| **`auth.json` tablete kopyalanıyor — parola özetleri ikinci cihazda** (bilinçli karar, §6.2) | **Kabul edilen bedel.** Tablet fiziksel olarak evin içinde ve çalınabilir bir cihaz; `auth.json` orada da ele geçirilebilir hâle gelir. Önlem: dosya app-private, device-protected dizinde `0600`; asla yedeğe/log'a/panel çıktısına düşmez; oturum çerezleri düğüm başına ayrı kalır (tablette açılan oturum sunucuda geçerli değildir). Parola değişince replika bir sonraki senkronla tazelenir — **eski özet tablette bayat kalabilir**, bu yüzden parola sıfırlamadan sonra replika senkronu zorlanmalı |
| **Bayat replika** — kesinti anında 5 dakikaya kadar eski kurallar | Kabul edilen bedel; devralma ekranında replikanın yaşı gösterilir |
| **Android süreç yeniden başlatma döngüsü** koordinatöre saldırır | Failover, süreç ölümüyle değil süreç içi durum makinesiyle yapılır (§4.7); açılışta her zaman `standby` |
| **Sunucu release onayı alamıyor** | Bağlanmaz, `503` + panelde "tablet henüz bırakmadı" uyarısı, 30 s'de bir denemeye devam. Zorla devralma yolu **hiç sunulmaz**; bedeli uzayan kesinti, bu bilinçle kabul edildi (§4.4, İlke 4-5) |

---

## 9. Fazlandırma

Sıra kullanıcı tarafından onaylandı: **önce kilit protokolü, sonra provisioning.** Her fazın
sonunda sistem tutarlı; hiçbir faz yarım bırakılamaz.

**Faz 0 — Ölçüm.** *Koordinatör kilidi ölçümü tamamlandı (§1.2: `fan-out`).* Kalan ölçümler:
sunucudaki `serial.adapter` ve ağ parametreleri, `database.db` yolu/boyutu, boş veritabanıyla
herdsman davranışı (§5.2). Sonuç: koordinatör kilidine dayanan tasarım terk edildi, protokole
geçildi — bu doküman o kararın ürünü.

**Faz 1 — Duyuru ve sahiplik protokolü altyapısı — ✅ TAMAMLANDI** (commit `c81a0e2`).
Davranış bilerek değiştirilmedi; yalnızca zemin döşendi. Gerçekleşen kapsam:

- **`src/lan-discovery.ts` — duyuru kaydı genişledi:**
  `{protocol, version: 1, role, mode, dashboardPort, nodeId, state, epoch, coordinatorId,
  priority, sentAt}`. İlk beş alan aynen korundu ve `version` **1'de bırakıldı** — eski
  istemciler yeni kaydı okumaya devam ediyor, yani değişiklik geriye dönük uyumlu.
- **`role !== "server"` kısıtı kalktı** (§1.5/4 kapandı): artık tablet de duyuru yapıyor.
  `role: "disabled"` **bilinçli olarak** hâlâ duyurmuyor — kapalı düğüm ağda görünmemeli.
- **`nodeId`:** `VILLA_BRIDGE_NODE_ID` verilmişse o; yoksa `srv-`/`tab-` öneki + hostname özeti.
  Yeniden başlatmada sabit kalır.
- **`coordinatorId`:** seri yolun özeti — adres/port ağda **sızmıyor**, yalnızca "aynı koordinatör
  mü" karşılaştırması yapılabiliyor.
- **`priority`:** server `0`, diğerleri `1` (İlke 2 sabit önceliği).
- **`state`:** sunucuda `source.start()` başarılıysa `owner`, aksi hâlde `standby`; tablette her
  zaman `standby`.
- **Eksik alanlar `null` = "bilinmiyor"** — asla "sahipsiz/taze" anlamına gelmez. Bu kural, eski
  sürümlü bir düğümün yanlışlıkla "boşta" sanılmasını engelliyor.
- **`src/index.ts` — başlatma sırası düzeltildi** (§1.5/5 kapandı): artık
  `app.listen` → duyurucu → `source.start()` (try/catch içinde). `source.start()` hatası **süreci
  öldürmüyor**; `coordinatorStatus: "coordinator-unavailable"` olarak `/api/health` ve
  `/api/overview` içindeki `node` alanında görünüyor. HTTP ve duyuru ayakta kalıyor, otomasyon
  motoru bu durumda başlatılmıyor. Koordinatör başkasındayken sunucu artık **konuşabiliyor** —
  geri alma el sıkışmasının ön koşulu buydu.
- **`apps/runtime`:** kendi `nodeId`'sini eliyor ve genişletilmiş kaydı okuyor; davranışı bugünküyle
  birebir aynı (periyodik yoklama veya devralma **yok**).
- **Testler:** `npm test` 157, `npm run runtime:test` 23 — tamamı geçiyor.

*Kazanç: iki ön koşul kusuru kapandı, sahiplik ağda görünür oldu, hiçbir risk alınmadı.*

**Faz 2 — Sürekli izleme (devralma yok) — ✅ TAMAMLANDI.** Davranış bilerek değiştirilmedi:
sunucu lider, tablet monitor. Tek fark, her düğümün karşı tarafı **sürekli ölçmesi**.
Gerçekleşen kapsam:

- **`node.state` düzeltmesi (§1.6/1):** `applyCoordinatorOwnership` +
  `ownershipStateForCoordinator` (`src/lan-discovery.ts`) sahiplik beyanını **koordinatör
  sonucundan** türetiyor; `src/index.ts` artık rolü hiç sormuyor. Sahiplik **kazanıldığında**
  `epoch` bir artıyor, bırakılırken artmıyor. *Sınır:* `epoch` hâlâ süreç içi — kalıcı sayaç
  (§4.7) Faz 3'e ait.
- **Üç kanallı izleme:** durum makinesi + zamanlayıcı iki yerde ikiz olarak duruyor —
  `src/peer-watch.ts` (sunucu, TypeScript) ve `apps/runtime/peer-watch.cjs` (tablet/Linux host,
  CommonJS). Kanallar §4.2'deki gibi: UDP duyuru (bayatlama toleranslı), `/api/discovery` HTTP'si,
  karşı düğümün MQTT portuna TCP. **Üçü birden düşmeden** sayaç ilerlemiyor; tek başarı sayacı
  sıfırlıyor. Eşikler dokümandan alındı (2 s / 15 s / 10 s / 2000 ms / 9 tur = 90 s / 3 başarı).
  Claim jitter'ı (0-15 s) eşik tablosunda taşınıyor ama izleme jitter uygulamıyor — jitter §3.3
  talep akışına ait, Faz 3.
- **Eylem yok:** eşik dolunca yalnızca `readyToTakeOver` bayrağı kalkıyor ve log düşüyor. Durum
  raporunda `action` alanı **her zaman `"none"`**; modülde devralma/talep/release ucu yok, testler
  bunu açıkça doğruluyor.
- **Bilinen yanlılık:** sunucudan bakıldığında tabletin gömülü broker'ı loopback'e bağlı olduğu
  için MQTT kanalı daima düşük görünür; sayaç ancak beacon **ve** HTTP de düştüğünde ilerler.
  Yanlılık güvenli yönde (geç fark etmek, yanlış devralmaktan iyidir).
- **Hiç görülmemiş düğüm için sayaç ilerlemez:** yokluk, arıza kanıtı değildir (§3.1'in
  "eksik alan = bilinmiyor" kuralının izleme karşılığı).
- **Raporlama:** sunucuda `/api/health` ve `/api/overview` içindeki `node.peerWatch`, tablette
  `/api/ready` içindeki `peerWatch` — karşı düğüm kimliği/adresi, son görülme anı ve yaşı, düşen
  kanallar, ardışık sayaçlar, eşiğe kalan tur, `readyToTakeOver`. **Dashboard'a UI eklenmedi**
  (gösterim kararı Faz 5).
- **Zamanlayıcılar** `unref` ediliyor ve kapanışta temizleniyor (sunucu `shutdown`, runtime
  `shutdown`); saat enjekte edilebilir, testler gerçek zaman beklemiyor.
- **Testler:** `npm test` 167, `npm run runtime:test` 41 — tamamı geçiyor.

*Kazanç: eşikler (özellikle 90 s) artık gerçek ağda gözlenebiliyor ve sahiplik beyanı doğruyu
söylüyor — Faz 3'ün ön koşulu buydu.*

**Faz 3 — Talep/release el sıkışması (koordinatöre dokunmadan).** `POST /api/failover/release`,
`CLAIMING` penceresi ve iptal mantığı; tablet talep akışını çalıştırır ama **core başlatmaz**
(dry-run, sonucu loglar). *Kazanç: protokolün doğruluğu canlı ağda, sıfır riskle doğrulanır.*

**Faz 4 — Tablet provisioning.** §5.3-A ile dosyalar kopyalanır. Sunucu **kapalıyken** tek
seferlik doğrulama: tablet standalone açılıyor mu, evi görüyor mu. Sonra elle monitor'a döndürülür.
`matter: false`. *Kazanç: standalone yolun çalıştığı kanıtlanır.*

**Faz 5 — Otomatik devralma.** `CLAIMING`/`STANDALONE`/`DEGRADED` durumları canlı; Faz 3'ün
dry-run'ı gerçek başlatmaya çevrilir. Geri verme Faz 3'teki el sıkışmasıyla zaten hazır.
**Kısıt (§1.6/2):** tablette yığını durdurmak/başlatmak `NodeRuntimeService`'in desired-state
dosyasından geçer — `POST /api/android/runtime/shutdown` tek başına yetmez, servis süreci geri
başlatır. Devralma ve geri çekilme kodu bunu hesaba katmalı. Faz 2'nin
`peerWatch.readyToTakeOver` bayrağı bu fazın tetikleyicisidir.
*Kazanç: döngü kapanır, sunucu düştüğünde ev otomatik ayakta kalır.*

**Faz 6 — Veri senkronu.** Replika çekme, snapshot devri, `epoch` sıralaması (§6). Bundan önce,
kesinti sırasında kurulan kuralların kaybolacağı panelde açıkça yazmalı.

**Faz 3 + 5 + 6 — ✅ KODLANDI (2026-10-20, canlıya çıkmadı).** Faz 3 ayrı dry-run olarak
yapılmadı; el sıkışması doğrudan gerçek devralmayla birlikte yazıldı ve sanal saatli iki düğümlü
simülasyonla sınandı. Gerçekleşen kapsam:

- **Ön adım — aynı Zigbee yığını:** Android runtime `zigbee-herdsman` 6.2.0 → **10.0.7**,
  converters 25.50.0 → **26.46.0** (sunucuyla aynı). Node 18.20.4'te tek engel `debounce` 3.x'in
  ESM olmasıydı (Z-Stack adaptörü `require` edemiyor); npm override ile `debounce` 2.2.0 (CJS,
  aynı API). ICU'suz `Intl` yedeği artık her IANA dilimini POSIX kural tablosundan
  (`apps/runtime/tz-rules.cjs`, `scripts/generate-tz-rules.mjs`) yaz saati dahil hesaplıyor.
  Commit `cc9c7d2`; tablete kuruldu, 22 cihaz DB'den okundu.
- **Mimari:** failover **paylaşılan çalışma zamanında** (`apps/runtime`), çünkü iki konak da
  `main.cjs` ile açılıyor (sunucu `--platform=linux`). Saf karar çekirdeği ikiz:
  `apps/runtime/failover-policy.cjs` ↔ `src/failover-policy.ts` (aynı test tablosu). Durum makinesi
  `apps/runtime/failover.cjs` (saat/ağ/yığın enjekte), ağ `failover-net.cjs`, dosya işi
  `failover-sync.cjs`, bağlama `failover-runtime.cjs`.
- **Durumlar:** `discovering` (≥6 s LAN dinleme) → sahip varsa `monitor` (tablet) /
  `awaiting-release` (sunucu); yoksa `claiming` (jitter 0-15 s, sunucuda 0 → 5 s pencere →
  epoch kalıcı +1 → `owner` beacon'ı → 1 s → yeniden bak → yığın). `monitor`'da 90 s üç kanal
  sessizliği + kendi ağ geçidi erişimi → `claiming`. Yığın başlamazsa `degraded` (60 s → 15 dk
  üstel). Kapanmayan koordinatör soketi → `halted` + `releasing` duyurusu (karşı taraf asla
  bağlanmaz).
- **Düzenli devir:** sunucu açılınca sahip görürse `POST :8094/api/failover/release` ister →
  tablet `releasing` → yığını durdurur → koordinatör soketinin kapandığını süreç tutamaçlarından
  doğrular → son snapshot'ı yanıtta döner → `monitor`. Sunucu 6 s beacon'ı dinleyip sahip
  kalmadığını doğrular, snapshot'ı **bağlanmadan önce** uygular, sonra talep eder. Onay yoksa
  bağlanmaz, 30 s'de bir yeniden ister; panel portunda bekleme sayfası (`/` 200 + uyarı,
  `/api/*` 503). Zorla devral yok. Sahip uç kanalda 90 s sessiz kalırsa sunucu da aynı kuralla
  talep eder (tablet ölürse ev sonsuza dek kapalı kalmasın).
- **Keşif artık yalnız açılışta değil:** `monitor ↔ owner` geçişi süreç içinde (yığın
  durdur/başlat, çekirdek yeniden içe aktarımı nesil sorgusuyla). Android WebView panel adresini
  10 s'de bir tanılamadan izliyor (`MainActivity.followDashboard`).
- **Replikasyon (Faz 6):** yön daima sahipten bekleyene. Bekleyen 60 s'de bir manifest (özet)
  sorar, değiştiyse ya da replika 24 saatten eskiyse snapshot'ı `replica/`'ya çeker. Devralmada
  replika yalnız canlı veriden yeniyse (epoch) kopyalanır; üzerine yazılan dosyalar
  `failover-backups/`'a (son 3). Kapsam: `failover-policy` `SNAPSHOT_FILES` + external_converters;
  hariç: provisioning, `*-runtime.json`, `failover-state.json`, `configuration.yaml`,
  `villa-bridge.yaml`, Matter, olay geçmişleri. Ağ alanları (pan_id, ext_pan_id, network_key,
  channel, adapter) farklı/bilinmiyorsa senkron **durur**, hiçbir şey yazılmaz.
- **Kimlik doğrulama (§10.2/1 kapandı):** jeton Zigbee ağ anahtarından HMAC ile türetilir — ağ
  anahtarı iki düğümde zaten aynı olmak zorunda; yeni sır dağıtılmaz, anahtar ağda ham gitmez.
- **Görünürlük:** tanılama `/api/ready` → `failover`, çekirdek `node.failover`; panel Genel Bakış'ta
  "Failover" satırı (durum, epoch, karşı düğümün replika yaşı/hatası); bekleme sayfası.
- **Testler:** `npm run failover:test` (30 test): kapsam filtresi, ağ alanı uyuşmazlığında durma,
  epoch fencing/eski snapshot reddi, owner→standby yönü, devir öncesi tablet→sunucu aktarımı,
  90 s eşiği, onaysız release'de sunucunun hiç bağlanmaması, kapanmayan soket, eşzamanlı açılış,
  geri çekilme; her senaryoda koordinatöre aynı anda **en fazla bir** bağlantı.
- **Gerçek tablette doğrulandı (2026-10-20, sunucu kapalı, tek koordinatör istemcisi):** yeni APK
  açılışta talep akışıyla sahip oldu (epoch 1, 22 cihaz). Mac'ten jetonlu sahte bir sunucu
  `release` istedi: tablet 13 s'de yığını durdurdu, 6638 soketi kapandı (`TIME_WAIT`), 18 dosyalık
  snapshot döndü, `standby` duyurdu, `monitor`'a geçti. Sahte sunucu hiç yanıt vermeyince 109 s
  sonra (90 s eşik + jitter + pencere) yeniden talep etti, aynı süreçte çekirdeği yeniden yükledi,
  epoch 2 ile sahip oldu, 22 cihaz/mesaj akışı geri geldi. Hiçbir an iki bağlantı olmadı.
- **Sınırlar:** seri (USB) koordinatörde soket doğrulaması yapılamaz (OS kilidine güvenilir);
  snapshot'ta olmayan dosyalar silinmez; sunucu "ayakta ama koordinatörsüz" kalırsa tablet
  devralmaz (kanallar canlı); `automation-state.json` replikaya girmez. Sunucudaki
  `/usr/local/sbin/villa-bridge-update` depo dışında: eski sürüme geri dönüş (rollback) bu
  korumayı taşımaz — bkz. dağıtım sırası notu.

**Faz 7 (opsiyonel) — Cihaz sınıfı katmanı ve Matter.** Koordinatör yetenek tespiti (§4.5) ve
`fan-out` vetosu; ardından **Matter devri** (§7 koşullu kararı) — taşınabilirlik ölçümü olumluysa
Matterbridge kimliği snapshot kümesine girer ve tek-yazar kuralına tabi olur, olumsuzsa kullanıcıya
geri dönülür. Yalnızca Faz 6 oturduktan sonra.

---

## 10. Kararlar ve açık sorular

### 10.1 Karara bağlananlar

| Soru | Karar | Nerede |
|---|---|---|
| Koordinatör kilidi | Ölçüldü → `fan-out`; kilit tasarımı LAN protokolüne taşındı | §1.2, §3 |
| Mimari model | Tek yazar, devredilebilir rol | §6.2 |
| Provisioning | Önce elle (A), hedef sunucudan çekme (C); APK'ya gömme kalıcı ret | §5.3 |
| Faz sırası | Önce kilit protokolü, sonra provisioning | §9 |
| **Devralma eşiği** | **90 s** (üç kanal, 9 ardışık tur) | §4.2 |
| **Release onayı gelmezse** | **Bekle ve bildir**; bağlanma yok, zorla devral düğmesi yok | §4.4 |
| **`auth.json`** | **Tablete kopyalanır**; güvenlik bedeli kabul edildi | §6.2, §8 |
| **Matter** | **Koşullu karar: tablet de devralır** — taşınabilirlik ölçümüne bağlı | §7 |

### 10.2 Hâlâ açık

1. **Release token'ı nereden gelsin?** Paylaşılan sabit token mu, provisioning paketiyle mi
   dağıtılsın, yoksa mevcut `controlToken` deseni mi genişletilsin? (**doğrulanmadı** — mevcut
   token'ın kapsamı okunmadı.) Faz 3'ten önce cevaplanmalı.
2. **Tablet devraldığında kullanıcıya nasıl haber verilsin?** Sessiz mi, panelde şerit mi, Android
   bildirimi mi? (Sessiz devralma, bir sorunun fark edilmemesi demektir.) Faz 5'ten önce.
3. **Koordinatör yetenek tespiti (§4.5) hiç yapılsın mı?** Sahip düğümün kendi koordinatörüne
   ikinci bağlantı açması `preemptive` sınıfında kısa bir kesinti üretir. (a) Hiç yapma, sınıf
   hep `unknown` kalsın; (b) yalnızca kullanıcı panelden tetiklerse yap (öneri); (c) ilk açılışta
   otomatik yap. Faz 7'den önce.

### 10.3 Sonucu beklenen ölçümler

| Ölçüm | Neyi bağlıyor |
|---|---|
| Matterbridge kimliği/fabric taşınabilir mi? (ayrı görevde, sürüyor) | §7 koşullu Matter kararı — olumsuzsa kullanıcıya geri dönülür |
| Boş `database.db` ile herdsman ağı yeniden kurar mı? | §5.2, Faz 4 provisioning |
| Sunucudaki `serial.adapter` + ağ parametreleri | §5.1 provisioning dosyaları |
