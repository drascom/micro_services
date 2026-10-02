# Faz 2c: pi skill + extension + onboarding iş yöneticisi

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

**Önkoşul:** Faz 2a bitmiş olmalı (sandbox uçları, `onboard_store`, `issue_token`/`revoke_token`) ve
`docs/plans/site-onboarding/pi-spike-notes.md` mevcut olmalı (Faz 2b). Komut satırı, extension iskeleti ve olay
ayrıştırması için **spike notlarını kaynak al**. Bu dosyadaki bir varsayım notlarla çelişirse notlar kazanır;
farkı rapora yaz.

## Amaç
1. "Yeni site tanıma" bilgisini tek merkezde, repoda versiyonlanan bir **pi skill**'inde toplamak.
2. Sandbox uçlarını çağıran dar bir **pi extension** (tool seti) yazmak.
3. Sunucuda arka planda pi oturumu yürüten, olayları taslağa yazan, kullanıcı geri bildirimini aynı oturuma ileten
   ve sonunda yaml'ı kaydeden **onboarding iş yöneticisi** + admin uçları yazmak.

## A. Dosya yerleşimi (deploy ile sunucuya gitsin diye `server/` altında)
```
server/pi/
  skills/diziflix-site-onboarding/
    SKILL.md
    references/config-schema.md      # list/detail/fields/collections/fetch_mode/playback; parse.py alan spec'i
    references/normalize.md          # Faz 1 normalize: şeması + iki örnek
    references/resolvers.md          # Faz 0 tip kataloğu + örnekler (catalog() çıktısından üret)
    references/quality.md            # kabul kriterleri (2a'daki sabitlerle aynı)
    references/examples/sinemalar.yaml, yabancidizi.yaml   # mevcut config'lerin KOPYASI (salt örnek)
  extensions/diziflix-onboard.ts
```
- `references/resolvers.md` ve `config-schema.md` elle yazılır. Ancak `tools/gen_onboard_refs.py` (yeni, küçük)
  `resolvers.catalog()` ve `registry.catalog()`'tan resolvers bölümünü yeniden üretebilsin. Test, dosyanın
  güncel olduğunu kontrol eder (`docs/api-samples` kalıbı gibi).
- `./deploy.sh --local-only -v` çıktısında `server/pi/` dosyalarının gönderildiğini doğrula. Gönderilmiyorsa
  `deploy.sh`'ye DOKUNMA, rapora yaz (Faz 4 düzeltir).

## B. SKILL.md içeriği (özet; Türkçe ya da İngilizce, model için net olsun)
- Frontmatter: `name: diziflix-site-onboarding`; `description`: yeni bir film/dizi sitesi için diziflix scraper
  yaml'ı üretme ve doğrulama.
- **Görev:** verilen URL'deki site için `site yaml'ı` üret. Bölümler: `base_url`, `list_url`, `fetch_mode`,
  `schema` (`MovieItem`), `list.row_selector`, `list.fields`, `detail.fields`, `playback`, `normalize`,
  `resolvers`, `providers`, `image_hosts`, `display_name`.
- **İş akışı (adım adım):**
  1. `fetch_page(url)`.
  2. `outline(page_id)` ile tekrar eden kart yapısını bul.
  3. `query` ile selector'ları dene.
  4. Taslak yaml yaz.
  5. `test_config`.
  6. Sorunlu alanları düzelt, en fazla 6 tur.
  7. Bir detay sayfası çek, `detail.fields` ve `resolvers` üret, `test_resolvers`.
  8. `submit_draft`.
- **Kurallar:**
  - Yalnızca katalogdaki resolver tiplerini ve provider adlarını kullan.
  - Uygun provider yoksa `playback: trailer` seç ve notlara "yeni host: <host>, kod gerekli" yaz.
  - Selector'lar kararlı olsun: anlamsız hash class'lardan kaçın, yapısal ve anlamlı class'ları tercih et.
  - `normalize.key.regex` URL yapısından çıkarılır ve `test_config` normalize raporunda ret oranı düşük olmalı.
  - Kullanıcının bir isteği varsa (geri bildirim mesajı), önce onu uygula.
  - Asla tahmin ederek "geçti" deme. `submit_draft`'tan önce son `test_config` sonucu `passed` olmalı. Olmuyorsa
    yine submit et ama notlarda neyin eksik olduğunu yaz.
- **Bitiş:** son mesajda 3-6 satırlık özet: ne bulundu, kriterler, açık sorunlar.

## C. Extension: `server/pi/extensions/diziflix-onboard.ts`
- Spike'taki iskeleti kullan. Tool'lar ve sandbox uçları:

  | Tool | Uç |
  |---|---|
  | `fetch_page` | `POST /fetch` |
  | `query_html` | `POST /query` |
  | `outline_page` | `POST /outline` |
  | `test_config` | `POST /test_config` |
  | `list_resolvers` | `GET /resolvers` |
  | `test_resolvers` | `POST /test_resolvers` |
  | `submit_draft` | `POST /submit` |

- Taban URL ve kimlik bilgisi ortamdan okunur:
  - `DIZIFLIX_SANDBOX_URL` (varsayılan `http://127.0.0.1:8090/api/onboard/sandbox`).
  - `DIZIFLIX_ONBOARD_TOKEN` → her istekte `X-Onboard-Token` header'ı.
  - `DIZIFLIX_DRAFT_ID` → `submit_draft` bunu otomatik ekler.
- Her tool'un açıklaması model için net olsun: ne zaman kullanılır, ne döner.
- HTTP hatasında anlamlı bir hata metniyle `throw`. Tool başına zaman aşımı 150 sn (`AbortController`).
- Çıktılar JSON string. Büyükse kırp (sınırı spike notlarına göre belirle).

## D. İş yöneticisi: `server/app/scraper/onboard.py` (yeni)

### Durum
- Aynı anda tek onboarding işi. `state.activity_start("_onboard", "onboard", trigger)` ile slot alınır; doluysa
  `already_running`.
- Bellek içi: `{draft_id: Popen}`. Restart'ta kaybolur. Başlangıçta `status=running` kalmış taslaklar `failed`
  (`reason=server_restart`) yapılır.

### `start(url, hint="", trigger="admin") -> draft`
1. `netguard.check_url`.
2. Taslak oluştur (`od_<12hex>`).
3. `issue_token` ile token al.
4. Arka planda (thread) `_run(draft_id, message)` başlat.

### `_run(draft_id, message)`
- Spike notlarındaki komutla `pi -p --mode json` çalıştırılır. Ana hatlarıyla:
  - `--no-builtin-tools`, `--tools <7 tool>`, `--no-extensions -e <ext>`, `--no-skills --skill <skill>`,
    `--no-context-files`, `--no-prompt-templates`.
  - `--model $SCRAPER_HEAL_MODEL`.
  - `--session-dir DATA_DIR/onboard/sessions --session-id <draft_id>`.
- `env`: mevcut env'in **kopyası** + `DIZIFLIX_ONBOARD_TOKEN`, `DIZIFLIX_DRAFT_ID`, `DIZIFLIX_SANDBOX_URL`
  (port `config` içindeki port'tan). Sunucu gizli anahtarlarını (TMDB vb.) env'den çıkar; pi kendi `auth.json`'ını
  kullanır.
- İlk mesaj:
  `"/skill:diziflix-site-onboarding <url>\nKullanıcı notu: <hint>"` ya da spike'ın önerdiği zorlama biçimi.
- stdout satır satır JSON okunur. Taslağa `events[]` olarak **özet** yazılır:
  - `tool_execution_start` → `{t, kind: "tool", name, args_short}`
  - `tool_execution_end` → `{t, kind: "tool_result", name, ok, summary (≤300)}`
  - Asistan metin parçaları birleştirilip mesaj sonunda `{t, kind: "say", text (≤1000)}`.
  - Hata/tekrar deneme olayları.
- `events` en fazla 500 öğe tutulur; eskiler kırpılır.
- `activity_update(label=..., phase=...)` ile panel çubuğu güncellenir (örn. "test_config 3. tur").
- **Bitiş:**
  - `submit` geldiyse `status=ready`.
  - Gelmediyse `needs_input` (ajan soru sormuş olabilir; son "say" mesajı soru olarak gösterilir).
  - Süreç hatalı çıktıysa `failed` + stderr özeti (≤1000, gizli bilgi filtresiyle).
- Zaman aşımı: `ONBOARD_TIMEOUT` (varsayılan 900 sn) → süreç öldürülür, `failed`.
- `finally`: `activity_end`, `revoke_token`, `_ops.json` kaydı (aşağıya bak).

### Diğer işlemler
- **`message(draft_id, text)`:** taslak `ready | needs_input | failed` ise yeni token alınır ve aynı oturumla
  `_run(draft_id, text)` çalıştırılır. Kullanıcı mesajı `{kind: "user", text}` olarak events'e eklenir.
- **`cancel(draft_id)`:** SIGTERM, 5 sn sonra SIGKILL; `status=cancelled`.
- **`save(draft_id, site_id, display_name=None, enable=False)`:**
  - `site_id` deseni `^[a-z][a-z0-9_]{1,31}$`. `scfg.list_sites()` içinde yoksa ya da arşivi varsa reddet (çakışma).
  - Taslağın yaml'ı tekrar `test_config` mantığından geçer (aynı iç fonksiyon). `passed=false` ise `force=true`
    olmadan reddet.
  - Yazım: `scfg.save_new_version(site_id, data)` → v1. `display_name` yaml'a girer.
  - Baseline: `scraper/config.py`'ye yeni bir `write_baseline_thresholds(site_id, {min_items, min_fill_ratio,
    critical_field_fill})` fonksiyonu ekle (mevcut `update_baseline` kalıbı, atomik). Eşikler test sonuçlarından
    türetilir: `min_items = max(5, floor(valid_count*0.6))`, `critical_field_fill` = title/detail_url/poster_url için
    `max(0.5, ölçülen-0.15)`.
  - Site otomatik taraması **kapalı** başlar: `settings.py`'de bu site için açık bir `enabled=false` kaydı yazılır.
    `INGEST_SITES` env varsayılanı bu siteyi açmasın. `enable=true` gelirse açılır.
  - Taslak `status=saved`, `saved_site_id`.
  - `cache`/`list_sites` için ek işlem gerekiyorsa yap (yeni yaml anında görünmeli).
- **`_ops.json`:** `state.py`'de `onboard` anahtarı ekle (`_ops_read` bilinmeyen anahtarları atıyor; bunu
  düzelt, yeni anahtarı listeye ekle). `record_ops_onboard({draft_id, url, site_id?, status, at, seconds, turns,
  passed, notes})`. `list_ops` ve `/api/ops/events` `kind=onboard` olayını üretmeli. Admin `app.js`'teki olay
  çizimi Faz 3'ündür, burada yalnızca API.

## E. Admin uçları: `server/app/routers/ops_onboard.py` (yeni), önek `/api/ops/onboard`

| Uç | Girdi | Çıktı |
|---|---|---|
| `POST /` | `{url, hint?}` | `{draft}` (202) |
| `GET /` | — | `{drafts: [özet]}` |
| `GET /{id}` | `?events_after=<n>` | `{draft, events: [...]}` (artımlı polling) |
| `POST /{id}/message` | `{text}` | — |
| `POST /{id}/cancel` | — | — |
| `POST /{id}/save` | `{site_id, display_name?, enable?, force?}` | — |
| `DELETE /{id}` | — | Çalışmıyorsa sil |
| `GET /health` | — | pi var mı, skill/extension dosyaları var mı, model ayarı var mı, `llm_health` durumu |

- Hatalar `ApiError` ile.
- `main.py` router tuple'ına ekle.
- `ops.py` `_ASSETS` beyaz listesine dokunma (Faz 3 yapacak).

## F. Ayarlar
- `ONBOARD_ENABLED` (varsayılan 1), `ONBOARD_TIMEOUT` (900), `ONBOARD_PI_BIN` (`pi`), `ONBOARD_MODEL`
  (boşsa `SCRAPER_HEAL_MODEL`). `.env.example`'a ekle.

## Testler (`server/tests/test_onboard.py`, yeni). Gerçek pi YOK.
- `subprocess.Popen` sahte bir süreçle değiştirilir; stdout'a kaydedilmiş bir JSON olay dizisi basar. Örnekleri
  spike notlarından al.
  - Olay ayrıştırma: tool/say/user olayları taslağa doğru yazılıyor; 500 sınırı korunuyor.
  - Akış sırasında sandbox `submit` simüle edilirse `status=ready`, edilmezse `needs_input`.
- Zaman aşımı → `failed`; iptal → `cancelled`; restart sonrası `running` → `failed`.
- `message`: aynı `session-id` ile ikinci çalıştırma; komut satırı argümanları beklendiği gibi (`--no-builtin-tools`,
  `--tools`, `-e`, `--skill`, `--session-id`).
- `save`:
  - Geçersiz/çakışan `site_id` reddediliyor.
  - `passed=false` + `force=false` reddediliyor.
  - Başarılıda geçici `CONFIG_DIR`'de `<site>.yaml` v1 ve eşikli baseline var.
  - Ayarlarda site kapalı.
  - `_ops.json`'da `onboard` kaydı var ve mevcut `runs/heals/tmdb` anahtarları korunuyor.
- `GET /api/ops/events` `kind=onboard` döndürüyor.
- Env'den gizli anahtarların çıkarıldığı test ediliyor.
- Skill referansı güncellik testi (`gen_onboard_refs` çıktısı ile dosya aynı).

## Kabul kriterleri
- Tüm testler yeşil. `extensions/diziflix-onboard.ts` en azından sözdizimi olarak geçerli: yerelde `node`/`tsc`
  yoksa sunucuda, spike'ta belirlenen yöntemle ve `/root/pi-spike/` altında bir kopyayla, **yalnızca yükleme**
  denemesi yap (`pi --help` benzeri, LLM'siz bir yöntem varsa). Yoksa rapora yaz.
- Gerçek uçtan uca deneme (gerçek pi + gerçek site) YAPILMAZ; deploy sonrası kullanıcıyla yapılacak.
- Rapor: dosya listesi, komut satırının son hali, spike notlarından sapmalar.
