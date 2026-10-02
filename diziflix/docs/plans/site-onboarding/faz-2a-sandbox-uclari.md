# Faz 2a: Sandbox uçları (pi tool'larının arka ucu)

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç. **Önkoşul:** Faz 0 ve Faz 1 bitmiş olmalı. `scraper/resolvers/`
(`validate`, `catalog`) ve `library/normalize.py` (`validate_rules`, `preview`, `explain`) mevcut olmalı. Değilse dur
ve raporla.

## Amaç
pi ajanının tool'larının çağıracağı, **yalnızca localhost'tan ve yalnızca geçerli bir iş token'ıyla** erişilebilen,
hiçbir kalıcı config yazmayan deneme uçları. Ajan bunlarla sayfa çeker, selector dener, taslak yaml'ı test eder,
resolver dener ve sonunda taslağı teslim eder.

## Mevcut yardımcılar
- `scraper/fetch.py` `page(cfg, url, wait_for=)` → `transport.fetch_page`: izole worker alt süreç (`http` → crawlee,
  `browser` → obscura), flock ile tek eş zamanlı iş, en az 2 sn bekleme, 95 sn tavan, robots.txt uygulanır.
  `cfg` olarak bellek içi `SiteConfig(site_id, data, path="")` taslağı yeterli (`tools/scraper_smoke.py` yaklaşık satır 79).
- `scraper/parse.py` `parse_list`, `scraper/schema.py` `validate_items` (`SCHEMAS`: `MovieItem`, `HomepageItem`),
  `scraper/drift.py` `detect`, `heal.py` `_clean_html` (60000 karakter, script/style/head atılır), `field_fill`.
- `tools/homepage_probe.py` `probe(site, html)`: parse → validate → drift → normalize akışının örneği.

## Yapılacaklar

### 1. SSRF koruması: `server/app/netguard.py` (yeni)
- `check_url(url) -> str`: normalize edilmiş URL döner, uygun değilse `ValueError` fırlatır.
  - Şema yalnızca `http`/`https`. Kullanıcı bilgisi (`user:pass@`) yasak. Port yalnızca 80/443 ya da boş.
  - Host DNS ile çözülür. Çözülen TÜM adresler `ipaddress` ile kontrol edilir: private, loopback, link-local,
    multicast, reserved, unspecified, CGNAT (100.64/10) yasak. IP literal host'lar da aynı kontrole girer.
- Fetch sonrası **son URL** (yönlendirme sonrası) yeniden kontrol edilir. Transport bunu veriyorsa kullan; vermiyorsa
  ve eklemek transport'u bozacaksa rapora yaz.

### 2. Sayfa deposu: `server/app/scraper/onboard_store.py` (yeni)
- Kök: `DATA_DIR/onboard/`. Alt klasörler `pages/<page_id>.html` + `.json` meta (url, final_url, fetch_mode,
  status, bytes, fetched_at) ve `drafts/<draft_id>.json`.
- `page_id` = `pg_<12 hex>`. Atomik yazım (tmp + `os.replace`). Sayfa başına üst sınır 3 MB.
- Taslak dosyası şeması:
  `{id, url, status, created_at, updated_at, site_id_suggestion, yaml_text, report, events: [...], error}`.
  `status`: `running | needs_input | ready | failed | cancelled | saved`.
- Temizlik: 7 günden eski sayfa ve taslaklar (`saved` olanlar hariç), `prune()`.
- Faz 2c bu modülü genişletecek. Taslak CRUD'unu basit fonksiyonlar halinde yaz (`create_draft`, `get_draft`,
  `update_draft`, `list_drafts`, `append_event`).

### 3. Sandbox router: `server/app/routers/onboard_sandbox.py` (yeni), önek `/api/onboard/sandbox`
**Erişim koruması (her uçta):**
- `request.client.host` ∈ {`127.0.0.1`, `::1`}. Değilse 403.
- `X-Onboard-Token` header'ı, aktif bir taslağın token'ıyla eşleşmeli. Token'lar bellek içi
  `{token: draft_id}` sözlüğünde tutulur. `issue_token(draft_id)` ve `revoke_token(token)` fonksiyonları dışarıya
  açılır (Faz 2c kullanacak). Eşleşmezse 403. Testlerde token verilerek çağrılır.
- Tüm yanıtlar JSON. Yanıtlar LLM'e gideceği için **küçük** tutulur: metin alanları kırpılır, listeler sınırlanır.

**Uçlar:**

| Uç | Girdi | Çıktı |
|---|---|---|
| `POST /fetch` | `{url, mode: "auto"\|"http"\|"browser", wait_for?}` | `{page_id, final_url, fetch_mode, status, bytes, title, html_excerpt}` |
| `POST /query` | `{page_id, selector, attr?, limit=10}` | `{count, items: [{text (≤200), attr_value?, outer_html (≤600)}]}` |
| `POST /outline` | `{page_id}` | Sayfa yapısı özeti: tekrar eden kart adayları (aynı class kombinasyonuyla ≥5 kez tekrar eden öğeler, örnek selector, sayısı), iframe/a host sayımı, `<title>` |
| `POST /test_config` | `{yaml_text, page_id?, detail_page_id?}` | Aşağıya bak |
| `GET /resolvers` | — | `{resolvers: catalog, providers: catalog}` |
| `POST /test_resolvers` | `{yaml_text, detail_url}` | `{candidates: [{url, label, resolver_type}], resolved: [{candidate, ok, provider, streams_count, error}]}` |
| `POST /submit` | `{draft_id, yaml_text, site_id_suggestion, notes}` | `test_config` yeniden çalışır, taslağa yazılır, `status=ready`. Kabul kriteri sağlanmasa da yazılır ama `report.passed=false` |

- `fetch` + `mode=auto`: önce `http`. Sonuç challenge (`challenge_blocked`) ya da çok az içerik ise `browser`.
  URL `netguard.check_url`'den geçer. `html_excerpt`: `_clean_html` uygulanmış ilk 15000 karakter.
- `test_config`: `yaml_text` parse edilir. Ardından şunlar çalışır:
  - Config doğrulama: `SiteConfig` taslak kurulumu; `resolvers.validate`; `normalize.validate_rules`.
  - Liste sayfası: `page_id` verilmişse o HTML kullanılır, yoksa `base_url+list_url` çekilir.
    `parse_list` → `validate_items` → `field_fill` → `normalize.preview`.
  - Detay: `detail_page_id` verilmişse ya da ilk öğenin `detail_url`'i çekilebiliyorsa `detail.fields` parse edilir.

  Çıktı:
  ```json
  {"valid": bool, "errors": [...], "warnings": [...],
   "list": {"count": n, "valid_count": n, "field_fill": {"title": 1.0, ...}, "samples": [ilk 5 öğe, alanlar kırpılmış]},
   "normalize": {preview çıktısı (samples 5)},
   "detail": {"fields": {...}, "fill": {...}} | null,
   "passed": bool, "criteria": {...}}
  ```
- **Kabul kriterleri** (`passed`), sabitleri modül başında tut:
  - `valid_count ≥ 8`
  - `title` doluluk ≥ 0.95, `detail_url` ≥ 0.95, `poster_url` ≥ 0.8
  - normalize `ok/total ≥ 0.9`
  - tekrar eden anahtar oranı ≤ 0.1
  - config hatası yok
- Uzun süren işlemler: fetch zaten 95 sn tavanlı. Uçlar senkron kalabilir (pi tool'u bekler), ancak her uçta toplam
  süre sınırı olsun (`ONBOARD_TOOL_TIMEOUT`, varsayılan 120 sn). Aşılırsa 504 + açıklama.
- Router'ı `app/main.py`'deki router tuple'ına ekle.

### 4. Ayarlar
- `config.py`'ye ekle: `ONBOARD_TOOL_TIMEOUT` (120), `ONBOARD_MAX_PAGE_BYTES` (3_000_000), `ONBOARD_RETENTION_DAYS` (7).
- `.env.example`'a yorumlu satırlar ekle.

## Dokunulacak dosyalar
- `netguard.py`, `scraper/onboard_store.py`, `routers/onboard_sandbox.py` (yeni); `main.py` (tek satır);
  `config.py` (yalnızca yeni sabitler); `.env.example`.
- Gerekirse `scraper/transport.py`'de yalnızca son URL'yi döndürmek için küçük bir ekleme. Davranış değişmemeli.

## Testler (`server/tests/test_onboard_sandbox.py`, yeni)
- `netguard`: `http://127.0.0.1`, `http://10.0.0.5`, `http://192.168.0.61:8090`, `file:///etc/passwd`,
  `http://user:p@x.com`, private IP'ye çözülen host (DNS mock) reddedilir; public host kabul edilir.
- Erişim: localhost dışı istemci 403; token yok veya yanlış 403. TestClient'ta client host'u ayarla ya da kontrol
  fonksiyonunu patch'le.
- `fetch` (transport mock): `auto` modunda challenge sonrası browser'a geçiş; sayfa diske yazılıyor.
- `query`, `outline`: fixture HTML ile (`tests/fixtures/yabancidizi_home.html` ya da inline).
- `test_config`: geçerli taslak `passed=true`; selector'ı bozuk taslak `passed=false` ve anlamlı `errors`/`field_fill`;
  bilinmeyen resolver tipi `errors`'a düşüyor.
- `submit`: taslak dosyasına yazılıyor, `status=ready`.
- Testler gerçek ağa ÇIKMAZ (fetch ve DNS mock).

## Kabul kriterleri
- Tüm testler yeşil; hiçbir uç `configs/`'e veya baseline'a yazmıyor (testte doğrula: `CONFIG_DIR` değişmedi).
- Rapor: uç listesi (tek satırlık), kriter sabitleri ve SSRF'de kalan açık (örn. transport içi yönlendirmeler).
