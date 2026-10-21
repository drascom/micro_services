# Faz 0: Resolver kütüphanesi

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

## Amaç
Video çözümleme zincirini, siteye özel Python modülü yerine **site yaml'ından seçilen genel resolver'lar** ile
çalışabilir hale getirmek. Mevcut siteler (yabancidizi, sinemalar) hiç değişmeden çalışmaya devam eder.

## Mevcut durum (keşiften; satır numaraları yaklaşık, önce dosyayı oku)
- `server/app/scraper/site_extractors/__init__.py`: `importlib` ile `site_extractors.<site_id>` modülünü yükler.
  - `discover(site_id, html, page_url) -> list[dict]`
  - `resolve_candidate(site_id, candidate, page_url, load_cookies) -> dict|None`
  - Modül yoksa discover `[]` döner, resolve_candidate adayı aynen geri verir. Aynı dosyada `series_catalog`,
    `series_inventory`, `detail_metadata` dispatch'leri de var; bunlara DOKUNMA.
- `site_extractors/yabancidizi.py`: aday dict alanları `{url, label, lang?, language?, handoff?: {provider, url,
  link, hash, querytype}}`. Siteye özel adımlar: `/dl/` VidMolly linkleri, `.alternatives-for-this [data-link]` →
  `/api/moly/{token}`, OK.ru `/ajax/service` POST, `udys` çerezi, hafif çerezden Obscura `load_cookies` yedeğine geçiş,
  `data-lango` dil sekmeleri. Genel olan kısımlar: `PLAYER_IFRAMES` iframe src toplama, hand-off sayfasındaki ilk
  `iframe[src]` + host regex kontrolü.
- `server/app/scraper/providers/`: `vidmolly.py`, `okru.py` (+ `trace.py`). Duck-typing arayüzü:
  `matches(url) -> bool`, `resolve(url, *, referer="") -> dict|None`. Dönüş:
  `{url, type, quality, duration, provider, streams:[{url,type,quality,label}], variant?, subtitles?:[...]}`.
- `providers/registry.py` `resolve(url, referer, max_handoffs=2, load_handoff)`: sabit if-zinciri (önce vidmolly, sonra
  okru); eşleşme yoksa sayfayı çekip ilk `iframe[src]`'yi izler (en fazla 2 adım, döngü korumalı).
- `server/app/library/videos.py`: `_resolve_page` → `fetch.page_bundle` → `site_extractors.discover` (+ tarayıcı
  frame/network adayları) → aday yoksa yaml `detail_fields` içindeki `trailer_url`/`video_url` → `_run_candidates`
  (paralel). `_resolve_candidate` sırası: `site_extractors.resolve_candidate` → `providers.resolve` → `cfg.stream_resolver`
  varsa `scraper/resolve.py` `resolve_stream`. `RESOLVER_VERSION = 5`.
- `sinemalar.yaml`: `playback: trailer`, extractor modülü yok, `stream_resolver:` bloğu (empower JSON API) var.
- `server/app/scraper/config.py`: `SiteConfig` (yaklaşık satır 30-89); `stream_resolver` okuma yaklaşık satır 79.

## Yapılacaklar

### 1. Provider registry'yi listeye çevir (`providers/registry.py`)
- Modül düzeyinde `PROVIDERS: list` tanımla. Her öğenin `name` (örn. "vidmolly", "okru"), `matches`, `resolve`
  alanları olsun. Mevcut modüllere `NAME = "vidmolly"` gibi bir sabit eklemek yeterli.
- `resolve(...)` fonksiyonuna isteğe bağlı `allowed: list[str] | None = None` parametresi ekle. `None` ise hepsi denenir
  (bugünkü sıra korunur). Liste verilirse yalnızca o adlar ve o sırayla denenir.
- iframe izleme yedeği aynen kalır. Ancak izlenen URL de `allowed` filtresine tabidir.
- `def catalog() -> list[dict]` ekle: `[{name, description, hosts}]`. `hosts` alanı, insanın okuyacağı host desenleri
  (örn. "vidmoly.*", "ok.ru"). Her provider modülüne `DESCRIPTION` ve `HOSTS` sabitleri ekle.

### 2. Genel resolver paketi: `server/app/scraper/resolvers/` (YENİ)
- `__init__.py`: tip kaydı `TYPES: dict[str, ResolverType]`.
- Her tipin şunları olur:
  - `NAME`, `DESCRIPTION` (LLM ve UI için 1-2 cümle).
  - `PARAMS`: parametre şeması, `{param: {"type": "str|int|bool|list|dict", "required": bool, "default": ..., "help": "..."}}`.
  - `discover(ctx, html, page_url, params) -> list[dict]`: adayları döner. Her aday `{url, label, lang?, resolver: <index>}`
    biçimindedir; `resolver` alanı listedeki sıra numarasıdır ve yönlendirme için kullanılır.
  - `resolve_candidate(ctx, candidate, page_url, params, load_cookies) -> dict|None`: adayı provider'ın tanıyacağı bir
    URL'ye çevirir (hand-off). Varsayılanı adayı aynen döndürmektir.
- `ctx` küçük bir dataclass olsun: `site_id`, `base_url`, `cfg`, `fetch` yardımcıları.
- Ağ çağrıları için mevcut `fetch.fetch_url` / `fetch.post_url` (oynatma taşıması) kullanılır. Yeni HTTP istemcisi açma.

Uygulanacak tipler:

| Tip | Parametreler | Davranış |
|---|---|---|
| `iframe` | `selector` (zorunlu), `attr`="src", `label`?, `host_regex`?, `lang`? | Eşleşen öğelerin attr URL'leri (mutlak), host filtresi |
| `anchor_host` | `selector`="a[href]", `host_regex` (zorunlu), `label_from`="text"\|attr adı, `lang_from`? | Host'u eşleşen linkler; etiket/dil link metninden |
| `data_attr_token` | `selector`, `attr`, `url_template` (örn. `"{base}/api/moly/{token}"`), `label_from`?, `filter_regex`? | Token'ı şablona koyup aday URL üretir |
| `ajax_handoff` | `selector`, `method`="POST", `url` (şablon), `form` (`{alan: "attr:data-link"\|"const:x"}`), `json_key` (örn. "api_iframe"), `expect_host_regex`?, `cookie_seed`? (`{udys: "now_ms"}`), `browser_fallback`=false | discover adayı `handoff` dict'iyle işaretler; resolve_candidate POST atar, JSON'dan iframe/URL çıkarır (HTML ise ilk `iframe[src]`), host'u doğrular |
| `json_api` | bugünkü `stream_resolver` bloğunun alanları aynen | `scraper/resolve.py` `resolve_stream`'i sarmalar (doğrudan akış döner, provider'a gitmez) |

- `ajax_handoff` + `browser_fallback: true` için yabancidizi'deki "hafif çerez → `load_cookies` yedeği" mantığını genel
  hale getir. yabancidizi modülünü değiştirme; mantığı kopyalayıp genelleştir, ortak bir yardımcıya çekebiliyorsan çek.
- `validate(resolvers: list) -> list[str]`: hata mesajları döner. Kontroller: bilinmeyen tip, eksik zorunlu parametre,
  derlenemeyen regex/selector, yanlış tür. Sandbox ve LLM tool'u bunu kullanacak.
- `catalog() -> list[dict]`: `[{type, description, params}]`.

### 3. Dispatcher (`site_extractors/__init__.py`)
- `discover`/`resolve_candidate`'e `cfg` erişimi ekle. Mevcut imzayı bozma: `cfg`'yi `scfg.load_site(site_id)` ile
  yükle (önbellekli), ya da isteğe bağlı parametre olarak geçir.
- Kural:
  - `cfg.resolvers` doluysa, listeyi sırayla çalıştır ve adayları birleştir (URL'ye göre tekrarsız).
  - Liste varsa siteye özel modül çağrılmaz. İstisna: yaml'da `use_site_module: true` ise modülün adayları da eklenir.
  - Liste yoksa bugünkü davranış aynen sürer (modül veya `[]`).
  - Adayda `resolver` indeksi varsa `resolve_candidate` o tipin metoduna gider, yoksa modüle gider.

### 4. `config.py`
- `SiteConfig`'e `resolvers` (list, varsayılan `[]`), `providers` (list|None) ve `use_site_module` (bool) alanlarını ekle.
- Yüklemede `resolvers.validate` çalışsın. Hatalı öğe logla ve atla; site yüklenmeye devam etsin, exception fırlatma.
- `stream_resolver` var ve `resolvers` içinde `json_api` yoksa: bugünkü yol aynen kalsın (geriye uyumluluk).

### 5. `library/videos.py`
- `_resolve_candidate`: `providers.resolve(..., allowed=cfg.providers)` çağrısı.
- `json_api` tipli aday doğrudan akış payload'ı döndürür, provider adımı atlanır.
- `RESOLVER_VERSION = 6` (önbellekteki eski payload'lar böylece geçersizleşir).
- Sağlık, negatif önbellek, `state.record_resolver` izi aynen çalışmalı. İzde aday hangi resolver tipinden geldiyse
  `resolver_type` alanı olarak eklensin.

### 6. Admin ucu
- `GET /api/ops/resolvers` → `{"resolvers": resolvers.catalog(), "providers": registry.catalog()}`.
- `routers/ops.py`'ye ekle; mevcut kalıbı izle.

## Dokunulacak dosyalar (sahiplik)
- `scraper/resolvers/*` (yeni), `scraper/providers/{registry,vidmolly,okru}.py` (yalnızca sabitler ve registry),
  `scraper/site_extractors/__init__.py`, `scraper/config.py`, `library/videos.py`, `routers/ops.py` (tek uç).
- DOKUNMA: `site_extractors/yabancidizi.py` (davranış), `library/normalize.py` (Faz 1'in), `images.py`, configs yaml'ları.

## Testler (`server/tests/test_resolvers.py`, yeni)
- Her tip için küçük inline HTML ile `discover` testi; `ajax_handoff` ve `json_api` için mock'lu `resolve_candidate`.
- `validate`: bilinmeyen tip, eksik parametre, bozuk regex.
- Registry: `allowed` filtresi ve sırası; `allowed=None` iken bugünkü sıra korunuyor.
- Dispatcher: liste varken modül çağrılmıyor; `use_site_module` ile çağrılıyor; liste yokken eski davranış.
- **Regresyon:** mevcut `test_yabancidizi.py`, video ve heal testleri değişmeden geçmeli.
- **Ek (yapılabiliyorsa):** `tests/fixtures/` altında bir yabancidizi detay HTML'i varsa, yabancidizi'nin discover
  davranışını genel tiplerle ifade eden bir resolver listesi yaz (yalnızca testin içinde) ve `discover` adaylarının
  URL kümesinin modülünkiyle aynı olduğunu doğrula. Mümkün değilse hangi adımın genelleşmediğini rapora yaz.

## Kabul kriterleri
- Tüm sunucu testleri yeşil; yabancidizi ve sinemalar yaml'ları ile modülleri değişmemiş.
- `GET /api/ops/resolvers` 5 tip ve 2 provider döndürüyor; her tipin `params` şeması dolu.
- Raporda: tip başına bir satır ve genelleşmeyen, kodda kalan noktalar.
