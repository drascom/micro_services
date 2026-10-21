# Villa Bridge — Panel Dosyasını Bölme Araştırması

> ## Durum: bölme tamamlandı (2026-08-08)
>
> §8'deki B planı uygulandı; aşağıdaki metin **bölmeden önceki** ölçümü ve kararı taşır, tarihsel
> kayıt olarak olduğu gibi bırakıldı. Bugünkü yapı:
>
> | Dosya | Satır |
> |---|---:|
> | `public/index.html` (yalnız markup + `<head>`'deki tema IIFE'si) | 227 |
> | `public/css/panel.css` | 584 |
> | `public/js/*.js` (13 dosya, en büyüğü `panel-automation.js` 3.501) | 8.636 |
> | **Toplam** | **9.447** |
>
> | Faz | Ne yapıldı | Commit |
> |---|---|---|
> | 0 + 1 | `src/panel-source.ts` test yardımcısı; `<style>` → `public/css/panel.css` | `e1ca850` |
> | 2 | Otomasyon kümesi → `public/js/panel-automation.js` | `a5e8c7c` |
> | 3 | Kalan 12 küme → `public/js/*.js` | `08d485c`, `7e17b5b`, `c1b35fa` |
> | 4 | Koruma testi (`scripts/panel-graph.mjs`), paketleme özeti, doküman | bu tur |
>
> Kalıcı kurallar:
>
> - **Derleme adımı yok.** Panel doğrudan `public/index.html` + `public/css/` + `public/js/`
>   içinde düzenlenir.
> - **Yükleme sırası = `index.html`'deki `<script src>` sırası.** `99-bind.js` her zaman en sonda.
> - Yeni bir panel dosyası iki yere birlikte yazılır: `index.html` etiketi ve `src/index.ts`
>   içindeki `panelAssetRoutes`. (`src/panel-source.ts` testlerle birlikte kaldırıldı.)
> - `scripts/panel-graph.mjs` (`npm run check` çalıştırır, ayrıca `npm run android:prepare`
>   paketlemeden önce çağırır) şunları korur: etiket–disk eşleşmesi, sıra, her dosyanın
>   ayrıştırılabilirliği, iki dosyada aynı üst düzey ad. `asset-manifest.json`'daki
>   `dashboardSha256` artık tüm panel dosyalarını kapsayan tek özettir (`dashboardFiles` listeler).

`public/index.html` tek dosyada büyüdü ve düzenlemesi zorlaştı. Bu doküman dosyanın **ölçülmüş**
anatomisini, bölmenin önündeki gerçek engelleri ve üç ayrı yolun bu projedeki gerçek maliyetini
taşır. Araştırma 2026-08-07'de yalnız kod okunarak yapıldı; **kod taşınmadı, dosya bölünmedi,
uygulama çalıştırılmadı**.

Kullanıcının cümlesi:

> "Tüm UI kodumuz tek dosyada, iş çok uzun, kodu inceleyip düzeltmek zor olacağından onu parçalara
> ayırmak için araştırma yap, kontrolü kolay bir yapıya dönüştürelim."

**Sonuç, peşinen:** Bölme yapılmalı, ama **ES modülüyle değil, klasik `<script src>` ile**. Ölçüm,
kodun bugün bile modül gibi yazılmış olduğunu gösteriyor (satır içi `onclick` yok, `window` global'i
yok, tüm çalışan üst düzey ifade dosyanın son %2'sinde). Klasik script bölmesi **tek satır kod
değişikliği bile gerektirmez**; yalnız metin taşınır. Ayrıntı §7 ve §8.

---

## 1. Dosyanın anatomisi

`public/index.html` — **8.576 satır, 705.681 bayt (689 KB)**.

| Bölüm | Satır | Bayt | Pay | İçerik |
|---|---:|---:|---:|---|
| `<head>` (1–20) | 20 | 2.264 | %0,3 | meta, data-URI ikonlar, FOUC önleyen 11 satırlık tema script'i |
| `<style>` (21–542) | 522 | 163.075 | %23 | 1.632 kural, 47 `@media`, 10 `@keyframes`, 36 CSS değişkeni, 57 yorum |
| Markup (543–732) | 190 | 62.810 | %9 | 5 `.view` bölümü, 26 `<dialog>`, 1 screensaver, 1 coach katmanı |
| `<script>` (733–8574) | 7.842 | 477.516 | %68 | tüm panel mantığı |
| `</body></html>` | 2 | 16 | — | |

Script içeriği (734–8573) **7.840 satır**: 433 üst düzey `function`, 442 üst düzey
`const/let/var`, toplam **875 benzersiz üst düzey ad**, 457 yorum satırı.

### 1.1 Script içindeki mantıksal kümeler

Kümeler, üst düzey bildirimlerin bitişik blokları olarak ölçüldü (satır aralıkları gerçek):

| Küme | Satır | Boyut | Pay |
|---|---:|---:|---:|
| Önsöz: localStorage, sabitler, `state`, `$`/`api`/`t` (734–888) | 155 | 14 KB | %3,0 |
| Oturum/kimlik: auth gate, PIN, parola (889–1027) | 139 | 6 KB | %1,3 |
| Cihaz sunum yardımcıları: kategori, pil, uyarı, olay (1028–1195) | 168 | 13 KB | %2,9 |
| Cihaz kartı ve kontrol HTML üretimi (1196–1507) | 312 | 28 KB | %6,1 |
| Ana ekran: sekmeler, `render`, özet, widget listeleri (1508–1996) | 489 | 29 KB | %6,3 |
| Döşeme genişliği + grup widget HTML (1997–2140) | 144 | 11 KB | %2,3 |
| Saat / konum arama / hava durumu (2141–2481) | 341 | 19 KB | %4,1 |
| Widget kataloğu, düzen, düzenleme kipi (2482–2855) | 374 | 18 KB | %4,0 |
| Grup komutları ve grup düzenleyici (2856–3046) | 191 | 10 KB | %2,1 |
| Ekleme diyaloğu, oda süzgeci, pull-to-refresh (3047–3247) | 201 | 10 KB | %2,1 |
| Eşleştirme, ad, görsel, silme, Matter (3248–3494) | 247 | 13 KB | %2,8 |
| Işık ve cihaz ayrıntı diyalogları (3495–3605) | 111 | 7 KB | %1,4 |
| Ayarlar, onboarding, koç turu, debug (3606–3928) | 323 | 17 KB | %3,6 |
| Bağlantılar, HA, Android, ev grupları/görünürlük (3929–4145) | 217 | 10 KB | %2,2 |
| Cihaz işlemleri + Zigbee araçları + yedek + `saveSettings` (4146–4744) | 599 | 30 KB | %6,6 |
| **Otomasyon: katalog, liste, ev konumu (4745–5596)** | **852** | **50 KB** | **%10,9** |
| **Otomasyon sihirbazı (5597–7950)** | **2.354** | **139 KB** | **%30,1** |
| Basit bağlantı — simple link (7951–8066) | 116 | 6 KB | %1,3 |
| Gezinme, tema, dil, ekran koruyucu (8067–8419) | 353 | 17 KB | %3,6 |
| Olay bağlama + `initialize()` (8420–8573) | 154 | 15 KB | %3,2 |
| **Toplam** | **7.840** | **462 KB** | |

**Tek çarpıcı sayı:** otomasyon üçlüsü (katalog + sihirbaz + simple link) **3.322 satır, 195 KB —
script'in %42'si**. Panelin yarısına yakını tek bir özellik.

### 1.2 En büyük 10 fonksiyon

Karaktere göre (satır sayısı yanıltıcı: dosyada 400 karakterden uzun 164 satır var):

| Fonksiyon | Satır aralığı | Satır | Karakter |
|---|---|---:|---:|
| `automationBindBody` | 6801–6892 | 92 | 9.562 |
| `openAutomationWizard` | 5597–5698 | 102 | 5.989 |
| `renderOnboarding` | 3660–3707 | 48 | 4.606 |
| `renderZigbeeGroups` | 1731–1757 | 27 | 4.253 |
| `renderNetworkGraph` | 4384–4425 | 42 | 3.820 |
| `saveAutomationWizard` | 7890–7950 | 61 | 3.715 |
| `applyLanguage` | 8213–8273 | 61 | 3.644 |
| `applyWidgetLayout` | 2575–2642 | 68 | 3.423 |
| `renderWeatherDialog` | 2399–2433 | 35 | 3.219 |
| `renderSimpleLink` | 7979–8025 | 47 | 3.000 |

Yani **tek fonksiyon patolojisi yok**: en büyük fonksiyon 92 satır. Sorun fonksiyon boyu değil,
**dosya boyu**.

### 1.3 Satır uzunluğu — okunabilirlik ölçüsü

| Uzunluk | Satır sayısı |
|---|---:|
| ≤ 120 karakter | 7.733 |
| 121–200 | 493 |
| 201–400 | 186 |
| > 400 | 164 |

En uzun satır: **125. satır, 7.263 karakter** (CSS). CSS bloğunda 400 karakterden uzun 65 satır var;
script'te 43. Bu, "yalnız satır sayısına bak" yaklaşımının neden yanıltıcı olduğunu gösteriyor: CSS
520 satır ama 163 KB.

### 1.4 Dosyanın değişim yoğunluğu (git)

| Ölçü | Değer |
|---|---:|
| Depodaki toplam commit | 145 |
| `public/index.html`'e dokunan commit | **101 (%70)** |
| Son 30 dokunuşta ortalama değişen satır | **205** |

Her üç commit'ten ikisi bu dosyada. Paralel çalışan iki ajan/worktree neredeyse kesin olarak burada
çakışır — bölmenin en somut pratik gerekçesi bu.

---

## 2. Bağlantı yoğunluğu — bölmenin önündeki asıl engel bu mu?

**Hayır.** Ölçüm bunun tersini gösteriyor.

### 2.1 Paylaşılan `state`: tek nesne, global değil

`public/index.html:841` — tek bir `const state={...}`, **~70 anahtar**, dosya boyunca **669 kez**
`state.` ile okunuyor/yazılıyor. Global değil: `<script>` blok kapsamında bir `const`. `window`
üzerine hiçbir şey yazılmıyor (`window.X=` araması: yalnız `window.matchMedia` okuması).

### 2.2 Çapraz küme bağımlılığı bir yıldız, ağ değil

Her küme için, başka kümede tanımlı adlara yapılan atıflar sayıldı (toplam **5.374** çapraz atıf):

| Küme | Dışa atıf | Kendi içi | Dışa oran | En çok bağımlı olduğu |
|---|---:|---:|---:|---|
| Önsöz/state/yardımcılar | 4 | 168 | %2 | — |
| Oturum | 77 | 35 | %69 | Önsöz 73 |
| Cihaz sunum yardımcıları | 136 | 49 | %74 | Önsöz 135 |
| Cihaz kartı HTML | 477 | 130 | %79 | Önsöz 442, Eşleştirme 16 |
| Ana ekran render | 377 | 81 | %82 | Önsöz 317, Cihaz işlemleri 14 |
| Saat/konum/hava | 273 | 126 | %68 | Önsöz 269 |
| Widget düzeni | 190 | 117 | %62 | Önsöz 149, Ana ekran 26 |
| Cihaz işlemleri/Zigbee | 429 | 111 | %79 | Önsöz 399, Ana ekran 11 |
| Otomasyon katalog/liste | 305 | 335 | %48 | Önsöz 276, Sihirbaz 8 |
| **Otomasyon sihirbazı** | 1.094 | **729** | %60 | Önsöz 930, Otomasyon katalog 157 |
| Basit bağlantı | 90 | 15 | %86 | Önsöz 77 |
| Gezinme/tema/dil | 261 | 102 | %72 | Önsöz 207 |
| Bağlama+init | 458 | 13 | %97 | Önsöz 262, Cihaz işlemleri 28 |

Fan-in (kime bağımlı olunuyor): **Önsöz kümesi 4.590 atıf, 19 kümeden** — tüm çapraz atıfların
**%85'i**. İkinci sıradaki "Otomasyon katalog/liste" 184 (çoğu kendi sihirbazından), üçüncü "Ana
ekran render" 107.

### 2.3 Bu 4.590 atıf aslında 5 sembol

Önsözde tanımlı 76 addan, önsöz dışından kullanılanların dağılımı:

| Ad | Önsöz dışından kullanım |
|---|---:|
| `$` | 1.879 |
| `state` | 845 |
| `t` | 808 |
| `esc` | 419 |
| `$$` | 180 |
| `api` | 142 |
| `showToast` | 104 |
| `weatherState` | 55 |
| kalan 43 ad | toplam 158 |

**İlk 5 sembol = 4.131 atıf = önsöz fan-in'inin %90'ı, tüm çapraz atıfların %77'si.**

Yorum: kümeler birbirine değil, **ortak bir çekirdeğe** bağlı. Bu çekirdek 155 satır / 14 KB.
Bölmenin önünde "her şey her şeye bağlı" gibi bir duvar **yok**; ortada tek bir paylaşılan kernel
var — ki bu, bölmeyi kolaylaştıran bir şey.

### 2.4 Değişken bağlar (`let`) — modül sorununun boyutu

Üst düzeyde yalnız **18 `let`** var. Bunlardan kendi kümesinin dışından yeniden atananlar sadece
**üç tane**:

| Değişken | Tanım | Dışarıdan atanan yer |
|---|---:|---|
| `installationOnboardingComplete` | 752 | 1018, 1021, 1024, 1029 (Oturum kümesi) |
| `applicationStarted` | 842 | 8547 (init) |
| `worldClockZones` | 863 | 2210, 2307 (Saat kümesi) |

Kalan 15'i (`automationAnimate`, `screensaverTimer`, `matterWatchTimer`, `pendingHomeBackup` …)
yalnız kendi kümesi içinde yeniden atanıyor.

### 2.5 Kritik bulgu: çalışan üst düzey kod dosyanın sonunda toplanmış

Script'te **satır içi olay bağlayıcı hiç yok** (`onclick=`, `onchange=`, `oninput=`, `onsubmit=`
HTML niteliği: **0 adet**). Bağlama tamamen `$("#x").onclick=fn` (262 atama) ve `addEventListener`
(47 çağrı) ile yapılıyor.

Ve daha önemlisi: **tüm çalışan (bildirim olmayan) üst düzey ifadeler 8414–8573 aralığında.**
734–8413 arası **saf bildirim** (fonksiyon, `const`, `let`) — tek istisna önsözdeki
`const savedX=(()=>{…})()` biçimli IIFE'ler, ki onlar yalnız `localStorage`, `Intl` ve
`matchMedia` kullanıyor. `initialize()` çağrısı en son satırda: 8573.

Bu, dosyanın **sırayı bozmadan istenilen yerden kesilebileceği** anlamına geliyor: kesme
noktalarında ne TDZ hatası, ne hoisting sürprizi çıkar (§7.2).

---

## 3. Panel nasıl servis ediliyor?

### 3.1 Sunucu

| Ne | Nerede | Nasıl |
|---|---|---|
| `index.html` | `src/index.ts:257` | Açılışta `readFile` ile **belleğe** okunuyor |
| `GET /` | `src/index.ts:1299` | Bellekteki string, `text/html`, **Cache-Control yok** |
| Arka plan görseli | `src/index.ts:261` | Ayrı el yazması route, açılışta belleğe, `max-age=31536000, immutable` |
| Dil paketleri | `src/index.ts:266` | `GET /api/locales`, dizini **istek anında** okuyup doğruluyor |
| Cihaz görselleri | `src/index.ts:386` | `/api/device-image/:model`, önbellekten |

**`@fastify/static` yok, sıkıştırma yok, CSP yok, HTTP/2 yok, service worker yok.** Her statik dosya
elle yazılmış bir route. Yani `/` bugün **689 KB'ı sıkıştırmasız ve önbelleksiz** gönderiyor; her
sayfa yenilemesi tam 689 KB.

### 3.2 Yeni bir statik dosya eklemek ne gerektiriyor?

Az şey — ve yetkilendirme tarafı kendiliğinden doğru:

`src/access-control.ts:163`:

```ts
if (!route.startsWith("/api/") || publicRoutes.has(route)) return;
```

Koruma yalnız `/api/*`'a bakıyor. Yani `/css/panel.css` veya `/js/automation.js` gibi bir route
**kendiliğinden herkese açık** olur; `publicRoutes` tablosuna dokunmaya gerek yok. (Buna karşılık
`AGENTS.md`'deki "yeni route'lar bu tablolara bilinçli eklenir" kuralı gereği, yeni route'ların
API olmadığı commit mesajında belirtilmeli.)

Gereken: açılışta `readFile` + tek `app.get`. Mevcut jpg route'u birebir örnek.

### 3.3 Android / Linux paketlemesi

- `scripts/prepare-android-node.mjs:137` — **`public/` dizininin tamamı** özyinelemeli
  kopyalanıyor (`cp(..., {recursive:true})`). Yeni `public/css/`, `public/js/` alt dizinleri
  **kendiliğinden** APK asset'ine girer, kod değişikliği gerekmez.
- Aynı dosya `asset-manifest.json`'a **yalnız `public/index.html`'in SHA-256'sını** yazıyor
  (`dashboardSha256`, satır 253–256). Bölmeden sonra bu bütünlük kontrolü panelin %68'ini
  kapsamaz olur → manifest tüm panel dosyalarını kapsayacak şekilde genişletilmeli.
  (`NodeRuntime.kt:164` manifest'in varlığını kontrol ediyor.)
- `apps/runtime/dashboard.html` (73 satır) panelin kendisi değil, **yükleme/monitor ekranı**;
  `apps/runtime/main.cjs:342` onu okuyor. Runtime paneli hiç okumuyor → runtime tarafında
  **sıfır etki**.
- `MainActivity.kt` paneli `http://…` üzerinden `webView.loadUrl` ile yüklüyor,
  `allowFileAccess=false`. Yani panel **hiçbir zaman `file://` altında çalışmıyor** — ES modül
  yasağı doğuran senaryo yok.

---

## 4. Testler — planın en pahalı kalemi

### 4.1 Ölçek

| Dosya | Satır | `test()` | `assert.*` |
|---|---:|---:|---:|
| `src/dashboard-copy.test.ts` | 5.639 | 117 | 2.553 |
| `src/dashboard-home-tabs.test.ts` | 616 | 24 | 144 |
| `src/dashboard-tile-width.test.ts` | 119 | 8 | 50 |
| `src/dashboard-widget-order.test.ts` | 201 | 8 | 37 |
| `src/dashboard-group-power.test.ts` | 135 | 3 | 20 |
| **Panel testleri toplamı** | **6.710** | **160** | **2.804** |
| `src/*.test.ts` genel toplam | — | 530 | 4.158 |

Panel testleri **testlerin %30'u, iddiaların %67'si**. Bu dosyaya dokunan her plan bu 2.804 iddiayı
hesaba katmak zorunda.

### 4.2 Paneli okuma mekanizmaları

1. **`readDashboard()`** — `dashboard-home-tabs`, `-tile-width`, `-widget-order`, `-group-power`:
   düz `readFile(../public/index.html)`.
2. **`readDashboardBundle()`** — `dashboard-copy.test.ts:11`: `index.html` + `en.json` + `tr.json`
   metinlerini **birleştirip tek string döndürüyor**. Yani "çok kaynaklı birleştirme" fikri
   testlerde **zaten var** — bu, bölmenin en önemli kolaylaştırıcısı.
3. **`dashboardScripts()`** — `dashboard-copy.test.ts:24`:
   `/<script>([\s\S]*?)<\/script>/g` ile script gövdelerini çıkarıp birleştiriyor. 24+ yerde
   kullanılıyor. **Bu regex `<script src="…">` ile eşleşmez** → bölmede mutlaka değişmeli.
4. **`extractFunction(source, name)`** — süslü parantez sayarak tek fonksiyonun gövdesini kesiyor;
   `home-tabs` 24, `widget-order` 3, `group-power` 3 kez kullanıyor.
5. **`new Function(...)` kum havuzları — 40 adet.** Panel metninden kesilen parça sahte
   bağımlılıklarla gerçekten çalıştırılıyor.
6. **`indexOf()` ile dilimleme/sıra iddiası — 64 adet.**

### 4.3 En kritik tek test yapısı

`dashboard-copy.test.ts:3110` — `automationSandbox()`:

```ts
const start = source.indexOf("const automationWeekDays=");   // satır 4751
const end   = source.indexOf("async function removeSimpleLink("); // satır 7951
```

**3.200 satırlık bitişik bir dilim** kesilip `new Function` ile koşturuluyor. Bu testin kendisi bir
kanıt: enjekte edilen 22 sahte bağımlılık, otomasyon kümesinin dış yüzeyinin **zaten yazılı** hâli:

```
t, esc, state, isProtectedDevice, deviceKind, ago, showToast, deviceSeenPress,
visiblePresses, deviceButtonName, deviceButtonPressLabel, openSimpleLink,
renderAutomations, activateView, api, simpleLinks, confirm, $, $$, document,
setTimeout, clearTimeout
```

Yani otomasyon kümesinin "import listesi" bugün var; çıkarılırsa neye bağlı kalacağı belli.

### 4.4 Bölme testleri nasıl etkiler?

| İddia türü | Yaklaşık sayı | Bölmeden etkilenir mi? |
|---|---:|---|
| CSS biçimli iddia (`@media`, `{prop:`) | ~365 (5 dosyada) | Birleştirici yardımcı CSS dosyasını da okursa **hayır** |
| Markup iddiası (`<section`, `<dialog`, `data-i18n=`) | ~226 | `index.html`'de kaldığı için **hayır** |
| Sıra/dilim iddiası (`indexOf`) | 64 | **Birleştirme sırası = yükleme sırası olduğu sürece hayır** |
| `new Function` kum havuzu | 40 | Dilim tek dosyada kaldığı sürece **hayır** |
| `dashboardScripts()` regex'i | 24+ kullanım | **Evet** — tek noktadan düzeltilmeli |

64 `indexOf` iddiası tek tek okundu: neredeyse hepsi **aynı alan içinde** karşılaştırma yapıyor
(markup–markup: `<section id=…>` sıralaması; CSS–CSS: `@media` blok sırası; JS–JS:
`prepareGroupEditor` ile `openGroupEditor` sırası). Alan sınırı aşan tek yapı §4.3'teki otomasyon
dilimi — o da tek dosyaya taşınacağı için korunuyor.

**Sonuç:** testlerin maliyeti, önce sanıldığı gibi "2.804 iddianın yeniden yazımı" değil; **tek bir
paylaşılan okuma yardımcısı** yazıp beş test dosyasını ona bağlamak. Şart: birleştirme sırası,
tarayıcının yükleme sırasıyla birebir aynı olmalı.

---

## 5. Kısıtlar

### 5.1 "No build step" bilinçli bir karar

`CLAUDE.md:38`:

> served to a single dependency-free dashboard (`public/index.html`, ~3k lines, no build step —
> edit it directly)

`AGENTS.md:9`: "The dependency-free dashboard is `public/index.html`."

*(Not: doküman "~3k lines" diyor, dosya bugün **8.576** satır. Bu satır ayrıca güncellenmeli.)*

Bu kuralın arkasındaki gerçek fayda ölçülebilir: bugün paneli değiştirmek = **dosyayı düzenle,
kopyala, servisi yeniden başlat**. Ara ürün yok, bayat artefakt sınıfı hata yok.

### 5.2 Tablet WebView'ı — ES modül desteği var mı?

`HANDOFF-2026-08-07.md:26`: "**`color-mix()` KULLANILMIYOR** — tabletin Android WebView'ı
desteklemiyor."

Bu, WebView sürümü için bir **üst** sınır veriyor. Alt sınırı panelin kendisi veriyor — bugün
canlıda çalışan özellikler:

| Özellik | Kullanım yeri | Gerektirdiği Chromium |
|---|---|---:|
| `element.inert` | `index.html:908` (auth gate, işlevsel) | 102 |
| CSS `:has()` | `index.html:163` (koyu tema) | 105 |
| `ResizeObserver` | `index.html:2039` | 64 |
| `<dialog>` / `showModal()` | 26 diyalog, 30 çağrı | 37 |
| `color-mix()` | **kullanılmıyor — desteklenmiyor** | 111 |

Yani tabletin WebView'ı **Chromium 105–110 aralığında**. `apps/android/app/build.gradle.kts`:
`minSdk = 24`, `targetSdk = 35`, `compileSdk = 35` — `minSdk` APK'nın tabanı, tabletin WebView'ı
değil; WebView Play üzerinden ayrı güncelleniyor.

**ES modül desteği için gereken: Chromium 61.** Dinamik `import()`: 63. Import maps: 89.
**Üçü de rahatlıkla destekleniyor.** Yani "modül kullanamayız" diye bir teknik engel yok — modülü
seçmemek için başka gerekçeler var (§7.2).

### 5.3 Çevrimdışı ve tek dosya bütünlüğü

Panel her zaman yerel sunucudan (`127.0.0.1:8091` veya `192.168.0.91:8091`) HTTP/1.1 ile yükleniyor;
internet gerekmiyor. Ek istekler de aynı yerel sunucuya gider. Bugünkü tek dosya, deploy sırasında
**atomik** bir avantaj sağlıyor: yarım kopyalanmış panel diye bir şey yok. Bölmede bu korunmalı —
korunma yolu §8.4'te.

---

## 6. Seçenekler

### A — Bölme yok, dosya içi düzen

Bölüm başlıkları (`/* ==== OTOMASYON SİHİRBAZI ==== */`), başa bir içindekiler yorumu, satır
aralıklarını gösteren bir harita.

| Ölçü | Değerlendirme |
|---|---|
| Testlere etki | **Sıfır** — hiçbir iddia yorum satırlarına bakmıyor |
| Çevrimdışı/Android | **Sıfır** |
| İlk yükleme | **Sıfır** |
| Geri dönüş | Anlık |
| İş büyüklüğü | **1 tur (~1–2 saat)** |

Ama sorunu çözmüyor. Kanıt: (1) commit'lerin %70'i bu dosyada, ortalama 205 satır değişimle — iki
paralel iş burada çakışır, bölüm başlığı bunu engellemez. (2) Dosyayı bir kez okumak 689 KB; okuma
araçlarının 2.000 satırlık varsayılan penceresi dosyanın **%23'ünü** kapsıyor, yani "hepsini gör"
her seferinde 5 okuma. (3) 3.322 satırlık otomasyon kümesi ile 155 satırlık çekirdek aynı dosyada
duruyor — birine dokunan diğerini de "değiştirilmiş" sayıyor.

**Karar: yetersiz.** Ancak §8'deki planın içinde ücretsiz bir yan ürün olarak yer alıyor.

### B — Klasik `<script src>` ile bölme (derleme yok)

`</body>` öncesindeki tek `<script>` bloğu, sırası korunarak birden çok
`<script src="/js/…js"></script>` etiketine bölünür; `<style>` bloğu
`<link rel="stylesheet" href="/css/panel.css">` olur.

**Kritik nokta:** Klasik (module olmayan) script'lerde üst düzey `const`/`let`/`class` bağları
**global sözcüksel kapsama** girer ve sonraki klasik script'lerden **görünür**; `function`
bildirimleri zaten `window`'a düşer. `src`'li klasik script'ler belge sırasına göre çalışır.
Yani bugünkü tek blok ile n bloğa bölünmüş hâli **anlamca aynıdır** — `export`/`import` yok,
`window.X=` yok, kod değişikliği yok. §2.5'teki ölçüm (tüm çalışan üst düzey ifadeler son 160
satırda) bunun güvenli olduğunu doğruluyor.

| Ölçü | Değerlendirme |
|---|---|
| Testlere etki | Tek paylaşılan okuma yardımcısı; `dashboardScripts()` düzeltilir. 2.804 iddia **olduğu gibi kalır** |
| Çevrimdışı/Android | Sıfır — `public/` zaten özyinelemeli paketleniyor (`prepare-android-node.mjs:137`). `asset-manifest` hash kapsamı genişletilmeli |
| İlk yükleme | +1 CSS +N JS isteği; toplam bayt aynı. Yerel/LAN'da istek başına ~1–3 ms, HTTP/1.1'de 6 paralel bağlantı → ~15 dosya için tahminî **30–60 ms** ek |
| Geri dönüş | Her faz tek commit, tek `git revert`; içerik metin taşımasından ibaret |
| İş büyüklüğü | **7–8 tur** (fazlara bölünmüş, §8) |
| Risk | Bir dosyada sözdizimi hatası yalnız o dosyayı düşürür → panel yarım çalışır. Karşı önlem: her dosyayı ayrıştıran ucuz bir test |

### B′ — `<script type="module">` ile bölme

Aynı bölme, ama modül semantiğiyle.

| Ölçü | Değerlendirme |
|---|---|
| Testlere etki | **Ağır.** `extractFunction` + 40 `new Function` kum havuzu, metnin tek kapsamda çalıştığını varsayıyor; `import`/`export` satırları `new Function` içinde **sözdizimi hatası** verir. Her kum havuzu için import satırlarını ayıklama katmanı gerekir |
| Kod değişikliği | 875 üst düzey ad için `export`/`import` listeleri; §2.4'teki 3 çapraz `let` (canlı bağlar salt-okunur olduğu için) setter'a çevrilmeli |
| Çevrimdışı/Android | Sıfır ek risk (WebView ≥105, modül ≥61 gerektiriyor) |
| İlk yükleme | Modül grafiği çözümü nedeniyle B'den bir tık yavaş; pratikte fark ölçülemez |
| İş büyüklüğü | B + **6–10 tur** |
| Kazanç | Gerçek kapsam yalıtımı, ad çakışması imkânsızlığı, açık bağımlılık grafiği |

Kazanç gerçek ama bu projede **şimdi** ödenecek bedeli karşılamıyor: B zaten okunabilirliği
kazandırıyor, kapsam yalıtımı ise 875 adın hiçbirinde bugüne kadar çakışma yaşanmamış bir sorunu
çözüyor.

### C — Derleme adımı eklemek (esbuild/vite → tek dosya)

Kaynak `src/panel/*.js` + `*.css`, `npm run build` bunları `public/index.html`'e paketler.

| Ölçü | Değerlendirme |
|---|---|
| Kural | **`CLAUDE.md` ve `AGENTS.md`'deki "no build step — edit it directly" kuralını doğrudan bozar** |
| Testlere etki | İddialar üretilen dosyayı okumalı → `npm test` panel derlemesini de yapmalı (TS derlemesi zaten var, sıra sorunu yok). Ama üretilen metin kaynak metinle birebir aynı olmazsa 2.804 iddia sallanır; minify **kesinlikle kapalı** kalmalı |
| Çevrimdışı/Android | `prepare-android-node.mjs` üretilen `public/`'i kopyalar; ama artık "kaynak" ile "gönderilen" ayrışır → bayat artefakt sınıfı yeni bir hata türü doğar |
| İlk yükleme | Bugünküyle aynı (tek dosya) |
| Geri dönüş | Zor: devDependency + npm script + paketleme + test yardımcısı aynı anda geri alınmalı |
| İş büyüklüğü | **3–4 tur** kurulum, sonra **kalıcı bakım yükü** |
| Kazanç | B'ye göre yalnız "tek istek" — ki bu, yerel ağda ölçülemeyecek bir kazanç |

**Karar: bedeli kazancından büyük.** Tek istek avantajı, LAN üzerinde 30–60 ms'lik bir şey için
kural bozup deploy zincirine ara ürün sokmayı haklı çıkarmıyor.

### D — Sunucuda açılışta birleştirme

`public/panel/*.js` dosyaları `src/index.ts` tarafından açılışta okunup **belleğe tek `<script>`
olarak** gömülür; tarayıcı yine tek dosya alır.

| Ölçü | Değerlendirme |
|---|---|
| Testlere etki | B ile aynı (aynı birleştirici mantık testlerde de kurulur) |
| İlk yükleme | Bugünküyle aynı |
| İş büyüklüğü | **2–3 tur** |
| Sorun | **Tarayıcıdaki satır numaraları kaynak dosyalarla eşleşmez.** Bölmenin ana faydası "hatayı 8576 satır yerine 400 satırlık dosyada bulmak"tı; birleştirme bunu geri alıyor |
| Sorun | Derleme adımını yasaklayıp aynı şeyi sunucunun içine saklamak — kuralın lafzına uyup ruhunu bozuyor |

**Karar: B'nin tüm işini yapıp faydasının yarısını veriyor.**

### Karşılaştırma özeti

| | A | **B** | B′ | C | D |
|---|---|---|---|---|---|
| Kod değişikliği | yok | **yok (yalnız taşıma)** | 875 ad | orta | yok |
| Testlere etki | yok | **1 yardımcı** | 40 kum havuzu | belirsiz | 1 yardımcı |
| "No build step" | korunur | **korunur** | korunur | **bozulur** | teknik olarak korunur |
| Android/çevrimdışı | — | **etkisiz** | etkisiz | yeni risk | etkisiz |
| İlk yükleme farkı | 0 | **+30–60 ms** | +40–80 ms | 0 | 0 |
| Tarayıcı hata satırları | 8.576/1 | **~400/dosya** | ~400/dosya | tek dosya | tek dosya |
| Geri dönüş | anlık | **commit başına revert** | zor | zor | orta |
| İş | 1 tur | **7–8 tur** | 14–18 tur | 3–4 tur + bakım | 2–3 tur |

---

## 7. Öneri

### 7.1 Karar

**B — klasik `<script src>` + ayrı CSS dosyası, aşamalı olarak.** Duruma göre değil; bu proje için
tek doğru yol bu.

Gerekçe, üç ölçülmüş olguya dayanıyor:

1. **Kod bunun için hazır.** Satır içi olay bağlayıcı 0; `window` global'i 0; tüm çalışan üst düzey
   ifade son 160 satırda; üst düzey `let` yalnız 18 ve bunlardan yalnız 3'ü kümesini aşıyor. Klasik
   script bölmesi bu dosyada **anlam değiştirmez**. Yani en büyük risk kalemi — "bölerken bir şey
   bozulur" — ölçümle sıfıra yakın çıkıyor.
2. **Bağımlılık grafiği bir yıldız.** Çapraz atıfların %85'i tek bir 155 satırlık çekirdeğe,
   %77'si beş sembole (`$`, `state`, `t`, `esc`, `$$`) gidiyor. Kümeler birbirine dolanmış değil;
   çekirdek en başa konursa gerisi düz sırayla dizilebilir.
3. **Testler zaten çok kaynaklı okuyor.** `readDashboardBundle()` bugün üç dosyayı birleştiriyor.
   Aynı kalıbın genişletilmesi 2.804 iddiayı olduğu gibi korur; 64 sıra iddiası da alan içi
   olduğu için ayakta kalır.

Buna karşılık C, projenin bilinçli bir kararını, LAN üzerinde ölçülemeyecek bir kazanç için bozuyor;
B′ 875 adı ve 40 kum havuzunu ellemeyi, bugüne dek yaşanmamış bir sorunu önlemek için istiyor;
D bölmenin ana faydasını (tarayıcı hata satırları) geri alıyor.

### 7.2 Neden modül değil de klasik script — tek cümlelik gerekçe

Klasik script'lerin üst düzey `const`/`let`'i **global sözcüksel kapsamı paylaşır**; dolayısıyla
bugünkü tek blok, sırası korunarak n parçaya kesildiğinde davranış birebir aynıdır — modül seçimi
ise aynı sonuç için 875 ad, 3 canlı bağ ve 40 kum havuzu dokunuşu ister.

---

## 8. Aşamalı geçiş planı

Her faz **tek commit**, **yalnız metin taşıması**, sonunda `npm test` (530 test) yeşil + kullanıcının
kendi gözüyle tablet doğrulaması (1024×640).

### Faz 0 — Testleri hazırla (panel dosyasına hiç dokunmadan)

Yeni `src/panel-source.ts` (yalnız testlerin kullandığı yardımcı):

- `readPanelSource()` → panel parçalarını **yükleme sırasıyla** birleştirir.
- `panelStyles()`, `panelMarkup()`, `panelScripts()` → alan bazlı dilimler
  (`dashboardScripts()`'in yerini alır).
- Beş test dosyası bu yardımcıya bağlanır.

Bu fazda yardımcı hâlâ tek dosyayı okur; çıktı bugünküyle **birebir aynı**. **Doğrulama:** 530 test
yeşil, iddia sayısı değişmemiş. **Geri dönüş:** tek dosya silinir.

Bu, planın "küçük ve tersine çevrilebilir" ilk adımı: panel dosyasına hiç dokunmadan tüm test
yüzeyi bölmeye hazır hâle gelir.

### Faz 1 — CSS'i çıkar

`<style>` (21–542, 520 satır, 163 KB) → `public/css/panel.css`;
`index.html`'e `<link rel="stylesheet" href="/css/panel.css">`.

Sunucu: `src/index.ts`'te açılışta bir `readFile` + `app.get("/css/panel.css")` (jpg route'u örnek;
`access-control` API dışını korumadığı için ek yetkilendirme gerekmez).

**Doğrulama:** ~365 CSS iddiası **değişmeden** geçmeli; tablet ekranı 1024×640'ta bozulmamalı.
**Geri dönüş:** dosyayı geri yapıştır, route'u sil — tek revert.

Neden ilk: tek dosya, tek route, sıfır JS riski, dosyadan **520 satır ve %23 bayt** gidiyor.

### Faz 2 — Otomasyonu çıkar

Script satır 4745–7950 → `public/js/panel-automation.js` (**3.206 satır, 189 KB, script'in %41'i**).

Neden bu küme:
- Tek başına panelin %41'i — en büyük tek kazanç.
- Dış yüzeyi zaten yazılı: `automationSandbox`'ın 22 sahte bağımlılığı (§4.3).
- Çapraz atıflarının %85'i (930/1.094) çekirdeğe gidiyor; çekirdek daha önce yüklendiği için sıra
  doğal olarak korunur.
- Testteki 3.200 satırlık dilim tek dosyada kaldığı için `new Function` kum havuzu bozulmaz.

Simple link (7951–8066, 116 satır) bu fazda **taşınmaz** — otomasyon dilimi
`removeSimpleLink`'te bitiyor, sınırı oynatmamak için kendi fazına bırakılır.

**Doğrulama:** 530 test yeşil (özellikle `dashboard-copy`'nin otomasyon kum havuzları); tablette bir
otomasyon sihirbazı turu.

### Faz 3 — Kalan kümeleri sırayla çıkar

Sıra **yükleme sırası** olmalı; dosya adları sırayı görünür kılsın:

| # | Dosya | Kaynak satır | Satır |
|---:|---|---|---:|
| 1 | `10-core.js` (çekirdek: `$`, `$$`, `state`, `t`, `esc`, `api`, `showToast`) | 734–888 | 155 |
| 2 | `20-auth.js` | 889–1027 | 139 |
| 3 | `30-device-view.js` (sunum yardımcıları + kart HTML) | 1028–1507 | 480 |
| 4 | `40-home.js` (sekmeler, render, özet, döşeme, grup HTML) | 1508–2140 | 633 |
| 5 | `45-clock-weather.js` | 2141–2481 | 341 |
| 6 | `50-widgets.js` (katalog, düzen, grup düzenleyici, ekleme diyaloğu) | 2482–3247 | 766 |
| 7 | `60-pairing.js` (eşleştirme, ad, görsel, silme, Matter, detay diyalogları) | 3248–3605 | 358 |
| 8 | `70-settings.js` (ayarlar, onboarding, koç, debug, bağlantılar, HA, Android) | 3606–4145 | 540 |
| 9 | `80-zigbee-tools.js` (cihaz işlemleri, ağ haritası, gruplar, bindings, yedek) | 4146–4744 | 599 |
| 10 | `85-automation.js` | 4745–7950 | 3.206 (Faz 2) |
| 11 | `88-simple-link.js` | 7951–8066 | 116 |
| 12 | `90-shell.js` (gezinme, tema, dil, ekran koruyucu) | 8067–8419 | 353 |
| 13 | `99-bind.js` (olay bağlama + `initialize()`) | 8420–8573 | 154 |

Kural: **`99-bind.js` her zaman en sonda** — §2.5 gereği tüm çalışan üst düzey kod orada; önce
gelen hiçbir dosya çalışma anında sonrakine dokunmaz.

Her dosya ayrı commit; 2–4 dosya bir tur. **Faz 3 = 3–4 tur.**

### Faz 4 — Koruma testi ve paketleme

1. `scripts/panel-graph.mjs` + testi — `scripts/runtime-module-graph.mjs` kalıbının aynısı:
   - `index.html`'deki `<script src>` sırası ile `public/js/` içeriği birebir eşleşiyor mu?
   - Her dosya ayrıştırılabiliyor mu? (`new vm.Script(source)` — ucuz, tam sözdizimi kontrolü)
   - Aynı üst düzey ad iki dosyada tanımlanmış mı? (klasik script'te `const` çift tanımı
     çalışma anında hata verir)
2. `scripts/prepare-android-node.mjs` — `asset-manifest.json`'daki `dashboardSha256`, panelin tüm
   dosyalarını kapsayan bir özete çevrilir.
3. `CLAUDE.md` / `AGENTS.md` — "~3k lines, edit it directly" cümlesi yeni yapıyı anlatacak şekilde
   güncellenir ("derleme adımı yok; panel `public/index.html` + `public/css/` + `public/js/`
   içinde doğrudan düzenlenir, sıra `index.html`'deki `<script src>` sırasıdır").

### 8.4 Deploy atomikliği korunur

Sunucu tüm panel dosyalarını **açılışta belleğe** okur (bugün `index.html` için yaptığı gibi,
`src/index.ts:257`). Böylece dosyaların diske yarım kopyalanması çalışan panele yansımaz; geçiş
noktası **servis yeniden başlatması** olarak kalır — bugünkü davranışın aynısı.

### 8.5 Nerede geri dönülür

| Faz | Geri dönüş |
|---|---|
| 0 | Yeni test yardımcısını sil (panel dosyasına dokunulmadı) |
| 1 | CSS'i `<style>` içine geri yapıştır, route'u sil — tek revert |
| 2 | Otomasyon dosyasını geri yapıştır — tek revert |
| 3 | Her dosya kendi commit'i; tek tek veya toplu revert |
| 4 | Koruma testi bağımsız; kaldırılabilir |

Her fazın çıktısı çalışır bir panel. Herhangi bir noktada durulabilir; yarım kalmış bir yapı yok.

---

## 9. Yapılmaması gerekenler

- **Bundler / derleme adımı eklemek (C).** Kural bilinçli, deploy zinciri bugün "kopyala + yeniden
  başlat". Kazanç yerel ağda ölçülemez (§6-C).
- **İlk turda `type="module"` kullanmak.** 875 üst düzey ad için `export`/`import`, §2.4'teki 3
  canlı bağ için setter, ve 40 `new Function` kum havuzu için import ayıklama katmanı gerekir —
  bugün yaşanmamış bir sorunu (ad çakışması) önlemek için (§6-B′).
- **Sunucuda birleştirme (D).** Tarayıcıdaki hata satırlarını kaynak dosyalara bağlama kazancını
  geri alır; bölmenin ana amacı buydu (§6-D).
- **Markup'ı bölmek.** 190 satır / dosyanın %9'u. Üstelik `dashboard-copy.test.ts:155` "5 `.view`
  bölümünün tamamı ilk `<dialog>`'dan önce gelir" diye **tek belge** üzerinden iddia ediyor.
- **Diyalog/görünüm başına "dikey dilim" (markup+CSS+JS bir arada) yapmak.** CSS'te kaskad sırası
  anlam taşıyor (47 `@media` bloğu ve `:root[data-theme="dark"]` geçersiz kılmaları) ve 64 `indexOf`
  iddiasının çoğu alan içi sıraya bakıyor. Bölme **yatay** olmalı: CSS ayrı, markup ayrı, JS küme
  küme.
- **Taşırken biçimlendirmek/güzelleştirmek.** 2.804 iddia birebir metinle eşleşiyor; ayrıca yalnız
  taşımadan oluşan bir diff gözle doğrulanabilir, taşıma+biçimlendirme karışımı doğrulanamaz.
  Uzun satırların kısaltılması **ayrı ve sonraki** bir iş.
- **Taşırken ad değiştirmek.** Bugün tek kapsam, 875 ad, sıfır çakışma. Yeniden adlandırma
  ihtiyacı yok, riski var.
- **Yeni dosyalara hemen `Cache-Control` koymak.** Bugün `/` önbelleklenmiyor; önbellek eklemek
  deploy sonrası bayat panel riski doğurur. Ayrı bir tur, ayrı bir karar (sürüm damgası tasarımıyla
  birlikte).
- **`@fastify/static` eklemek.** "Dependency-free" teslim yoluna yeni bağımlılık sokar, `public/`
  ağacının tamamını açar ve açılışta-belleğe-oku/atomik-yeniden-başlat özelliğini bozar. Mevcut
  jpg route'u zaten doğru kalıbı gösteriyor.
- **`defer`/`async` niteliği kullanmak.** `async` sırayı bozar. Script'ler zaten `</body>` öncesinde
  (satır 733, markup 732'de bitiyor); düz `<script src>` bugünkü davranışın birebir eşdeğeri.
- **Faz 2'yi (otomasyon) Faz 0 ve 1'den önce yapmak.** Test yardımcısı hazır değilken 3.200 satırlık
  kum havuzu dilimini oynatmak, 2.804 iddianın hangisinin neden düştüğünü ayırt edilemez hâle
  getirir.
