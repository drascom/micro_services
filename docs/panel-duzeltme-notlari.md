# Panel düzeltme notları — 2026-08-07

Kullanıcının panelde gezerken aldığı 8 not, koddaki karşılıkları ve uygulama planı.
Devir dokümanı: `../../HANDOFF-2026-08-07.md`. Hepsi `public/index.html`'e dokunuyor,
biri ayrıca `src/location.ts` + `src/index.ts` istiyor.

**Durum (2026-08-07, 04:30):** A, B ve C turları **yazıldı ve canlıya çıktı** (`2b57042`).
Sekiz notun sekizi de uygulandı. `npm test` 521/521. Kalan: **kullanıcının 1024×640 göz testi**
ve **D turu** (handoff'un kendi backlog'u — `/api/favorites` emekliliği, hub'a iki şehir,
hava durumu detayı). D bilinçli olarak yapılmadı: sahibi uyanıkken yapılacak iş.

## 0. Kısıtlar

| Kısıt | Kaynak | Sonuç |
|---|---|---|
| Tabletin gerçek CSS alanı **1024×640** | Handoff | Her doğrulama 1024×640'ta; sabit px yok, `clamp()` + viewport birimi |
| **`color-mix()` yasak** | Android WebView desteklemiyor | `dashboard-copy.test.ts` negatif iddiayı koruyor |
| **Paralel oturum** aynı ağaçta | Handoff | Turlar sırayla; dosya seviyesinde son yazan kazanır |
| Lokalde test yok | Memory `test-on-server-never-local` | Her tur commit + deploy, doğrulama sunucudan |
| Ürün çok evli | Memory `villa-bridge-is-multi-home` | Sabit şehir/ülke listesi gömülmez |
| Testler markup/CSS'i **birebir** doğruluyor | `dashboard-copy.test.ts` | Her arayüz dokunuşunun test maliyeti aşağıda |

### Handoff "sıradaki iş" listesiyle örtüşme

| Handoff maddesi | Bu plandaki not | Durum |
|---|---|---|
| 1. `/api/favorites` emekliliği | — | Örtüşme yok |
| 2. Hub'a iki şehir | 3 | Aynı arama akışına (`searchLocations`) dokunur → **sıralı** |
| 3. Hava durumu detayı | — | Örtüşme yok |
| 4. Masaüstünde yatay düzen | **5, 6, 7** | Aynı CSS bölgesi → **aynı turda gitmeli** |

---

## 1. Eşleştirme sonrası cihaz detayında "Bitti" yok — **küçük**

**Nerede:** `finishPairingFlow()` `:3235` → `showDevice()` `:7882` → `openDeviceDetail()` `:3488`;
gövde `deviceDetailBodyHtml()` `:1390`, tehlike satırı `:1415`.

**Karar: evet eklenecek, ama "Kaldır"ın yanına değil.** O satır `.card-actions-danger` (`:267`) —
bilinçli olarak tehlikeli bölge; olumlu onayı oraya koymak yanlış basmayı davet eder. Ayrıca
`data-admin-only`, ev sakini oturumunda görünmez → sakin yine bitirme düğmesi göremezdi.

Tehlike satırından **sonra** tam genişlikte tek `primary` düğme; metni bağlama göre:
eşleştirmeden geldiyse "Kurulumu bitir", normal açılışta mevcut `close`.
Bağlam `state.detailFromPairing` ile taşınır, dialog `close` olayında sıfırlanır.
Kaydedilecek bir şey yok (isim/oda/rol kendi pencerelerinde kaydedildi) — bu yüzden doğru kelime
"Save" değil **"Bitti"**. Sağ üstteki `×` kalır.

Yeni metin: `finishSetup`.

Aynı akışın ağ tarafı ayrı bir notta: `eslesme-permit-join-notu.md` (interview bitince
`permit_join` hemen kapatılmıyor, kurulum bitene kadar açık kalıyor).

---

## 2. Devices sayfası üst tasarımı — **7 ile birleşik**

**Nerede:** `<header class="page-head">` `:531`, `.toolbar` `:532`, CSS `.page-head` `:81`,
`#devices .page-head>.add-device` `:265`.

Bu not **7. notun kendisi**, ayrı iş değil. Ek olarak yalnız buradan gelen: `.eyebrow`
("All equipment") **kaldırılır** — başlık ortalanınca üstünde ikinci satır taşıyor.
`toolbar` başlık altında kalır; 1024×640'ta iki satır yer yemesin diye arama + ızgara/liste kalır.

---

## 3. Ev konumu koordinat olarak isteniyor — **orta, en zorlu**

**Nerede:** `#homeLocationForm` `:572`, `renderHomeLocation()` `:5177`, `saveHomeLocationForm()`
`:5198`; sunucu `GET/PUT /api/settings/location` `src/index.ts:877`/`:884`, `LocationStore` +
`validateLocation` `src/location.ts`, yetki `src/access-control.ts:46`.

### Doğrulanan iki gerçek

1. **`navigator.geolocation` bu üründe çalışmaz.** Panel düz HTTP ile servis ediliyor
   (`src/index.ts:1331`, TLS yok, ters vekil yok). LAN IP'si üzerinden HTTP güvenli köken
   değildir. **Yan bulgu:** hava durumu penceresindeki "Mevcut konumu kullan"
   (`requestWeatherLocation()` `:2371`) bu yüzden **tablette bugün de bozuk** — nesne var olduğu
   için kod hata dalına girmiyor, sessizce "izin verilmedi"ye düşüyor.
2. **Panelde zaten geocoding var:** `searchLocations(kind,query)` `:2139` →
   `geocoding-api.open-meteo.com/v1/search`, tarayıcıdan, `clock` ve `weather` kind'larıyla.
   Yardımcılar hazır (`normalizeLocationResult` `:2085`, `renderLocationSearchResults` `:2115`,
   `scheduleLocationSearch` `:2163`). **Yeni bağımlılık gerekmiyor.**

### Karar — tek pencere, üç katman; "enlem/boylam" kelimesi görünmez

`#homeLocationForm` iki sayı kutusu yerine: kayıtlı yerin **adı** + mevcut güneş satırı
(`homeLocationSunTimes` — doğrulamanın tek gerçek yolu) + tek düğme **"Konumu seç"**.
Düğme `locationSearchState`'e üçüncü kind (`home`) ekleyerek aynı pencereyi açar.

Pencerede sırayla:
1. **"Hava durumu konumunu kullan"** — `localStorage["villa-weather-location"]`'dan, tek dokunuş,
   **çevrimdışı çalışır**, tipik evde zaten doğru cevap.
2. **Şehir/mahalle araması** — mevcut `searchLocations`. Ağ yoksa `locationSearchUnavailable`
   ile nazikçe düşüyor.
3. `<details>` içinde **"Koordinatı elle gir"** — bugünkü iki kutu buraya taşınır. Çevrimdışı
   kurulan ev kilitlenmesin ve teknik kullanıcının kaçış kapısı olsun diye. Varsayılan kapalı.

"Mevcut konumu kullan" **eklenmez** (çalışmıyor); hava durumundaki ölü düğme de kaldırılır.

**Seçilen yerin adı sunucuya yazılır** — kullanıcının kuralı: "bir kere yapılan seçim her yerde
aynı olmalı". Sunucu değişikliği küçük ve geriye dönük uyumlu: `HomeLocation.label?: string`
(≤80 karakter, boşsa yazılmaz), `validateLocation` kabul eder, bilinmeyen alan reddi korunur,
eski `location.json` sorunsuz okunur. `home-backup.ts` konumu içermiyor → yedek şeması değişmiyor.

**Risk:** handoff'un "hub'a iki şehir" maddesi aynı arama akışına dokunuyor → asla eş zamanlı.
`#homeLocationForm` markup'ını birebir tutan test **yok** → arayüz maliyeti düşük.
Ev sakini oturumunda alanlar salt-okunur kalmalı (`isResidentSession()` `:5171`).

---

## 4. Boş grup kartı genel görünümde çıkmasın — **küçük**

**Nerede:** `renderGroupWidgets()` `:2032`, `overviewGroupEntries()` `:1857`,
`groupControlEntries()` `:1867`, sekme şeridi `homeTabItems()` `:1419`.

İki durumdan biri **zaten çözülmüş**:
- Hiç cihaz eklenmemiş grup → `:2036` `if(!groupControlEntries(group).length)continue;` ile
  bugün de basılmıyor.
- **Tümü gizlenmiş grup** → bugün basılıyor. Koşul, düzenleme kipi **dışındayken**
  `overviewGroupEntries(entries).entries.length===0` durumunu da atlayacak şekilde genişletilir.

Sekme şeridi dokunulmaz (`homeTabItems` girdi sayısına bakmıyor) → **grup alt şeritte kalır**,
istenen bu.

İki incelik: **düzenleme kipinde kart görünür kalmalı** (yoksa göz düğmelerine ulaşılamaz;
`applyWidgetLayout():2511` aynı emniyeti kuruyor), ve `overviewTabSummary` sayımı yeni "görünür
girdi" ölçütünü kullanmalı, yoksa şeritteki sayı ekrandakiyle uyuşmaz.

---

## 5. Arka plan resmi tüm sayfalarda — **orta**

**Nerede:** CSS `:51` (açık) ve `:52` (koyu), ikisi de `body[data-active-view="home"]` kilidinde.
Cam belirteçleri `:26`/`:39`, kullanan blok `:115`.

Fotoğraf `body`'ye taşınır. **Ama iş bundan ibaret değil:** okunabilirliği sağlayan her şey
`body[data-active-view="home"] #home ...` ile kapsanmış (`:115`, `:183`, `:199`, `:204`, `:220`,
`:233`, `:244`). Bunlar `.view` düzeyine genelleştirilmezse alt sayfaların opak `var(--surface)`
kartları fotoğrafı yalnız kenarlarda gösterir → yamalı görünür.

Kapsam: kart/panel yüzeyleri tek cam belirtecine bağlanır. `<dialog>` içeriği **hariç** —
tam ekran pencerelerin arkasında fotoğraf okunabilirliği düşürür. Saydamlık düz `rgba()` ile.

Koyu temada karartma katmanı alt sayfalarda daha güçlü olmalı (metin yoğunluğu yüksek).

---

## 6. Alt sayfalarda menü yerine "Genel görünüm" — **orta**

**Nerede:** beş başlık `:481`, `:531`, `:539`, `:544`, `:565`; `.app-menu-button` `:75`,
ölçüler `:29`; `activateView()` `:7864`, `openAppMenu()` `:7846`.

Menü düğmesi **yalnız ana ekranda** kalır; alt sayfalarda aynı ölçü ve biçimde
**"Genel görünüm"** düğmesi → `activateView("home")`. Simge alt şeritteki "Genel görünüm"
sekmesiyle **aynı** olmalı (`deviceIconSvg("overview")`), iki farklı "eve dön" görseli olmasın.

**Bedel çözüldü (kullanıcı kararı):** Otomasyonlar / Bağlantılar / Ayarlar'a bugün **sadece**
menüden gidiliyor; menü alt sayfalardan kalkınca "Ayarlar → Otomasyonlar" iki dokunuş olurdu.
Kullanıcının çözümü: **Otomasyon menüden çıkarılıp ana ekrana taşınır.** Menü zaten ana ekranda
kalıyor, dolayısıyla sık kullanılan üç yer (Cihazlar, Otomasyon, Genel görünüm) doğrudan;
Bağlantılar ve Ayarlar menüde kalır — ikisi de nadir kullanılıyor.

Yeri `.home-actions` (bugün Ekle / Düzenle / Menü). **1024×640'ta dördüncü düğmenin sığması
ölçülmeli** — sığmıyorsa etiketler kısalır, düğme sayısı değil. Simge ve biçim, aşağıdaki
8. nottaki `.page-action-tile` diliyle aynı olmalı.

Erişilebilirlik: alt sayfalarda `aria-haspopup`/`aria-expanded` **kaldırılmalı** (artık pencere
açmıyor). Yeni metin: `backToOverview`.

---

## 7. Ortak başlık: [ana sayfa] · [ortada başlık] · [sayfa eylemi] — **büyük**

**Nerede:** aynı beş `header.page-head`; CSS `.page-head` `:81`, portre ızgarası `:180`,
yatay kip `:176`/`:160`.

`.page-head` simetrik üç sütuna çevrilir:

```css
grid-template-columns: var(--head-action-w) minmax(0,1fr) var(--head-action-w);
```

Sağ hücre boşken bile başlık **gerçekten** ortada durur — esnek `justify-content` ile ortalama,
düğme genişliği değiştikçe kayardı; bu yüzden ızgara.

Sağ hücre: Cihazlar → `#devicesAddDevice`, Otomasyonlar → `#newAutomation`,
Bağlantılar/Ayarlar → boş. `.eyebrow` ve `.lead` alt sayfalarda kaldırılır.

**Ana ekran bu şablonun dışında** — hub, metrik satırı ve `.home-actions` kendi düzenine sahip ve
handoff'un "masaüstünde yatay düzen" maddesi tam oraya dokunacak.

---

## 8. "Yeni otomasyon" düğmesi "Cihaz ekle" ile **aynı tipte** olsun — **küçük, 7 içinde**

Kullanıcının açıklaması: *"tipleri aynı olsun dedim; her ikisi de ayrı sayfalarda ve farklı
görevleri var, ben tema tutarlı olsun demek istedim."* Yani istenen **görsel dil birliği**,
işlev ortaklığı değil — aşağıdaki çözüm tam olarak bunu yapıyor.

**Nerede:** `#devicesAddDevice` `:531` (`class="primary add-device"`, biçim `:265`),
`#newAutomation` `:539` (`class="primary"`).

**`add-device` sınıfı otomasyon düğmesine VERİLMEYECEK** — bu sınıf bir **davranış kancası**:
`:8300` `$$(".add-device").forEach(button=>button.onclick=()=>startPairing(true))` ve `:1697`
`disabled` yönetimi. Verilirse "Yeni otomasyon" eşleştirme başlatır.

Yerine `:265`'teki biçim kuralı yalnız sunumsal yeni bir sınıfa taşınır — `.page-action-tile`.
İki düğme de bunu alır; `#devicesAddDevice` `add-device`'ı **davranış için korur**.
Seçiciden `#devices` kapsamı düşer, kural her sayfada geçerli olur.

---

## Uygulama sırası

**Ön koşul:** `public/index.html` / `src/index.ts` / `public/locales/*.json` üzerinde paralel
oturumun işi bitmiş ve commit edilmiş olmalı; ağaç temiz.

| Tur | İçerik | Neden birlikte | Boyut |
|---|---|---|---|
| **A** | Not **1** + Not **4** | İkisi de küçük, bağımsız, farklı bölgeler; hızlı kazanç, dosyayı erken serbest bırakır | Küçük |
| **B** | Not **3** + ölü "Mevcut konumu kullan"ın kaldırılması + `location.ts` `label` | Tek bölge. Handoff'un 2. maddesiyle **çakışır**, peş peşe gitmeli | Orta |
| **C** | Not **5**+**6**+**7**+**2**+**8** ve handoff'un "masaüstü yatay düzen"i | Beşi de `.page-head` / `body[data-active-view]` / `--head-action-*` yazıyor. Ayrı turlarda aynı satırlar üç kez yeniden yazılır ve testler üç kez düzeltilir. Tur içi sıra: ortak başlık ızgarası (7+2+6+8) → arka plan + cam yüzeyler (5) → masaüstü yatay düzen | Büyük |
| **D** | Handoff 1 (`/api/favorites`) + 3 (hava durumu detayı) | C'den bağımsız; C'nin CSS'i oturduktan sonra hub'a dokunmak güvenli | Orta |

Turların hiçbiri eş zamanlı yürütülmez.

**Her turun kapanışı:** `npm test` yeşil → commit → sunucuya **ve** tablete deploy
(memory `deploy-both-targets`) → tablette **1024×640**'ta doğrulama, yanında 1280×800 ve 800×480;
açık ve koyu tema ayrı ayrı.

**Test maliyeti:** A ~2-3 yeni iddia · B ~3 yeni iddia + `automation-runs.test.ts`'e bir satır ·
C **en az 14 mevcut iddianın yeniden yazılması** (`dashboard-copy.test.ts` `:73`, `:79`, `:213`,
`:214`, `:219`, `:220`, `:221`, `:280`, `:746`, `:763`, `:764`, `:1480`, `:4297-4303`, `:4446`) —
C'nin gerçek maliyetinin yarısı burada.
