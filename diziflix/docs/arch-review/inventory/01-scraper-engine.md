# Envanter 01: scraper motoru API'si (yeni `app/control/` için)

Kapsam: `server/app/scraper/` (salt-okur kod okuması, 2026-10-03). Yollar `server/app/` köküne göredir. Çalıştırılarak doğrulanmadı; emin olunmayan yerler "doğrulanmadı" diye işaretli.

## 1. Site yaml şeması

`scraper/config.py` şema doğrulayıcı DEĞİL: `SiteConfig` (config.py:104) ham `data` sözlüğünü tutar, özellikler tembel okur ve bozuk bloğu loglayıp atlar (asla raise etmez). Üst düzey doğrulama yönetim katmanındadır: `routers/onboard_sandbox.py` `_check_core` (:1677), `_check_fields` (:1633), `_check_playback` (:1708), `_check_normalize` (:1728), `_check_collections` (:1838), `_check_blocked_blocks` (:2157), `_check_series_page` (:2552), `_check_search` (:2857). Bunlar kütüphaneye çıkarılmalı.

| Anahtar | Okuyan | Alt alanlar / not | Blok doğrulayıcı |
|---|---|---|---|
| `site_id`, `display_name`, `base_url`, `list_url`, `schema` (`MovieItem`\|`HomepageItem`), `playback` (`video`\|`trailer`), `image_hosts[]`, `item_limit` (1..200, varsayılan 30, sayfalama YOK) | config.py:110-123; `images.py:350`; `library/ingest.py` | çekirdek | `onboard_sandbox._check_core` :1677 |
| `fetch_mode` | config.py:126 | `http` (crawlee) \| `browser` (obscura); başka değer `ValueError` | aynı |
| `obscura_stealth`, `obscura_settle_seconds`, `cache_images` | transport.py:79-83 | yalnız browser modu | yok |
| `list` | config.py:133,137 | `row_selector`, `fields{ad: alan-kuralı}` | `_check_fields` |
| `detail` | config.py:141 | `fields{...}` (belge kökünde uygulanır) | `_check_fields` |
| alan-kuralı | parse.py:1-17,123 | `selector, attr, all, regex, split, index, then_split, multi, cast(int\|float\|date_tr), replace, fallback[], self` | `_check_spec` :1608 |
| `collections[]` | config.py:145; collections.py | `id, title, path, role, row_selector?, fields?, required_fields?, excluded_fields?, sort_by?, sort_desc?, genre?, category?, method?, data?`; roller `ROLES` :23, `HOME_ROLES` :39; kimlik `list_id(role, site_id)` :42 | `collections.check_request` :118 + `_check_collection` :1771 |
| `series_page` | config.py:210 | `SPEC_KEYS` series_generic.py:50: `row_selector*, episode_url_regex*, fields{url,title,air_date}, default_season, series_url_regex, series_slug_regex, same_series_regex, season_pages{season_menu, season_url_regex}, unaired_classes, first_episode, last_episode` | `series_generic.validate_spec(spec)` :195 |
| `search` | config.py:234 | `SPEC_KEYS` search_generic.py:47: `url*({query}), method, form\|json, headers, fetch, format(html\|json), row_selector, fields, results_path, limit<=20, cache_ttl` | `search_generic.validate_spec(spec, base_url)` :211 |
| `resolvers[]` | config.py:158 | düz öğe `{type, ...params}`; tipler §4 | `resolvers.validate(list)` resolvers/__init__.py:104 |
| `providers[]` | config.py:182 | izinli provider adları (öncelik sırası); yok = hepsi | `_check_playback` |
| `use_site_module` | config.py:205 | `site_extractors/<site>.py` adaylarını da ekle | yok |
| `stream_resolver` | config.py:152; resolve.py:61 | eski JSON API tarifi: `endpoint({video_id}), video_id_regex, referer, sources_json_path, source_field, ...` | yok |
| `normalize` | `library/normalize.py` :231, `generic_normalize` :697 | `host, base_url, absolute_urls, key{from,regex,match,template}, type{from_group,map,default}, source_url, fields, split, episode_source{enabled,url,url_template,when,label,default_season}, clean` | `normalize.validate_rules` :262 |
| `availability_gate` | config.py:268 | `probe` (0..3, vars. 2), `require` (`player`\|`stream`) | `blocked.validate_gate` :234, `gate_of` :249 |
| `blocked[]` | config.py:254 | <=6 kural: `on` (`episode_page`\|`series_page`), `iframe_src_regex`\|`html_regex`\|`selector`, `reason` | `blocked.validate` :74, `rules_of` :122 |
| `series_crawl{enabled}`, `series_catalog`, `detail_pages[]`, `detail_prewarm_limit`, `detail_prewarm_lists[]` | `library/series_crawl.py:74-77`, `library/ingest.py:701,961` | yalnız yabancidizi kullanıyor | yok |
| `version`, `updated_at` | `save_new_version` yazar | sürüm yaml'ın içinde | - |

Regex lint: `config.regex_lint(data) -> list[str]` (config.py:85), `double_backslash_hint` (:57).

Yükleme / kaydetme / sürümleme (hepsi `scraper/config.py`, `CONFIG_DIR` :30 = `scraper/configs/`):

| İmza | Satır | Ne yapar |
|---|---|---|
| `list_sites() -> list[str]` | 293 | etkin `<site>.yaml` adları (`.vN.yaml` arşivleri hariç) |
| `load_site(site_id) -> SiteConfig` | 311 | yaml oku; yoksa `FileNotFoundError`. Bellekten taslak için `SiteConfig(site_id, data, path="")` doğrudan kurulabilir |
| `registry() -> list[dict]` | 322 | site özeti (`version, schema, base_url, engine, fetch_mode`) |
| `save_new_version(site_id, new_data) -> int` | 389 | eskiyi `<site>.v<eski>.yaml` olarak arşivler, `version = max(eski, arşivler)+1`, `updated_at` yazar; atomik + `flock`; tombstone temizler |
| `archived_versions(site_id) -> list[int]` | 370 | arşiv numaraları |
| `rollback_config(site_id) -> int` | 514 | etkinin altındaki en yeni arşive dön (etkin de arşivlenir) |
| `SiteConfig.baseline() -> dict` | 281 | `<site>.baseline.json` oku |
| `write_baseline_thresholds(site_id, thresholds)` | 563 | `min_items, min_fill_ratio, critical_field_fill` (+ `series_page.min_items`) yazar |
| `update_baseline(site_id, last_good)` | 541 | yalnız `last_good{valid_count, fill_ratio, field_fill, field_fill_all, config_version, at}` tazeler |
| `site_files` / `delete_site_files` / `tombstone_add` / `tombstone_clear` | 418 / 427 / 489 / 501 | silme + `data/deleted_sites.json` (deploy.sh okur) |
| `load_recipe(name)`, `list_recipes()`, `save_recipe(name, data) -> int`, `recipe_snapshot`, `restore_recipe`, `delete_recipe` | 620, 632, 666, 686, 695, 712 | provider tarifi aynı sürümleme düzeniyle (`providers/<ad>.vN.yaml`) |

Baseline dosyasının sürümü yok (tek dosya, üzerine yazılır); yaml'daki `version` ile bağı yalnız `last_good.config_version`.

## 2. Fetch

| İmza | Satır | Kullanım | Sınırlar |
|---|---|---|---|
| `fetch.page(cfg, url, *, wait_for="", method="GET", data=None) -> str` | fetch.py:378 | TARAMA sayfası (liste, detay, dizi, bölüm). `cfg.fetch_mode`'a göre crawlee-http ya da obscura alt süreci | aşağıda |
| `fetch.page_bundle(cfg, url, ...) -> dict` | fetch.py:398 | `{html, status?, assets?, engine, method}` | aynı |
| `fetch.browser_page(cfg, url, *, wait_for="") -> str` | fetch.py:387 | `fetch_mode` ne olursa olsun tarayıcı motoru | aynı |
| `fetch.session_cookies(cfg, url) -> list[dict]` | fetch.py:406 | taze tarayıcı oturumu çerezleri (yalnız browser) | aynı |
| `transport._worker_result(cfg, url, *, wait_for, capture, method, data)` | transport.py:45 | yukarıdakilerin tek uygulaması | süreç içi `_lock` + dosya `flock` (tüm süreçlerde TEK tarama), her istekten önce `sleep(2)`, alt süreç 95 sn, robots'a uyar, sır içermeyen ortam |
| `crawlee_worker.crawl(job)` | crawlee_worker.py:13 | http modu; `max_request_retries=0`, 50 sn işleyici / 85 sn toplam; Cloudflare challenge = `challenge_blocked` | - |
| `obscura_worker.crawl(job)` | obscura_worker.py:72 | browser modu; `--timeout 45`, `settle_seconds` <= 10; POST yok | - |
| `fetch.fetch_url(url, *, headers, timeout, retries, check_robots=True, min_delay) -> str` | fetch.py:117 | OYNATMA taşıması (paylaşımlı `httpx.Client`, 40 bağlantı); varsayılanlar `RESOLVE_TIMEOUT/RETRIES/MIN_DELAY` | 429/5xx yeniden dener |
| `fetch.post_url(url, *, data, headers, ...) -> str` | fetch.py:136 | form POST (player API'leri) | aynı |
| `fetch.fetch_limited(url, *, headers, timeout=5, max_bytes=1e6, max_redirects=3, allow=None) -> bytes` | fetch.py:151 | sert süre + boyut sınırı, her yönlendirmede `allow(url)` | yeniden deneme yok |
| `fetch.impersonated_get(url, *, headers, cookies, timeout=12, max_bytes=3e6, max_redirects=3, allow, session) -> ImpersonatedPage(text, url, status)` | fetch.py:239 | curl_cffi Chrome TLS parmak izi + Referer/çerez (Cloudflare TLS denetimini geçer) | yeniden deneme yok |
| `fetch.fetch_impersonated(url, **kw) -> str`, `impersonated_session()` | fetch.py:325, 224 | gövde metni / paylaşımlı çerez oturumu | - |
| `fetch.reachable(url, *, timeout=15) -> bool` | fetch.py:330 | 1 baytlık Range yoklaması | - |
| `fetch.fetch(url, *, timeout=25, retries=3) -> str` | fetch.py:348 | eski düz httpx GET, robots + 2 sn/host | hâlâ çağrılıyor mu: doğrulanmadı |
| `fetch.allowed(url) -> bool` | fetch.py:50 | robots.txt (okunamazsa izinli) | - |

- POST: yalnız `fetch_mode: http`, yalnız `base_url` host'u, urlencoded gövde <= 4096 bayt (`transport._post_job` :28, `collections.form_body` :97). Browser modunda net hata, sessiz GET yok. Eski worker kuruluysa hata (`result.method` denetimi, transport.py:112).
- Eşzamanlılık: `fetch.page` yolu KÜRESEL SERİ (tek kilit + 2 sn). Planın "örnek sayfalar paralel getirilir" maddesi bu yolla sağlanamaz; paralellik yalnız `fetch_url` / `impersonated_get` yolunda var.
- SSRF: `netguard.check_url(url) -> str` (netguard.py:54; yalnız http/https, port 80/443, özel/CGNAT adres yasak, `ValueError`). `fetch.py` ve `transport.py` netguard'ı KENDİ ÇAĞIRMAZ; çağıranlar: `search_generic` (:43, `_make_allow` :358), `player_page._allowed` (:446), `onboard_sandbox`. Yeni kod kullanıcıdan gelen her adresi `fetch.page`'den önce kendisi denetlemeli.

## 3. Çıkarma

| İmza | Satır | Ne yapar |
|---|---|---|
| `parse.parse_list(html, row_selector, fields) -> list[dict]` | parse.py:148 | satır seçici + alan kuralları; saf fonksiyon |
| `parse.parse_detail(html, fields) -> dict` | parse.py:158 | belge kökünde alan kuralları |
| `parse.apply_field(node, spec) -> Any` | parse.py:123 | tek alan kuralı (selectolax düğümü) |
| `parse.turkish_date(text) -> str\|None` | parse.py:77 | Türkçe tarih -> ISO |
| `schema.validate_items(schema_name, raw_items) -> (valid, metrics)` | schema.py:85 | pydantic doğrulama + `metrics{raw_count, valid_count, invalid_count, field_fill{}, fill_ratio}` |
| `drift.detect(metrics, baseline) -> {drift, reasons[]}` | drift.py:15 | eşik kıyası (aşağıda) |
| `series_generic.series_inventory(html, page_url, spec) -> dict` | series_generic.py:386 | `{video_sources[{key "s1e3", url, kind, season, episode, title, air_date?}], unaired, declared_seasons, season_pages[], first, last, structured, warnings, metrics, diagnostics}`; saf |
| `site_extractors.series_inventory(site_id, html, page_url, spec)` | site_extractors/__init__.py:170 | site modülü varsa o, yoksa genel motor |
| `site_extractors.detail_metadata(site_id, html, page_url)` / `supports_detail_metadata` | :207 / :196 | yalnız kod modülü olan site (yabancidizi) |
| `search_generic.search(cfg, query, limit=20) -> list[{title, detail_url, poster_url, year, genres}]` | search_generic.py:624 | ağ çağrısı içerir (netguard, 10 sn, 3 MB, bellek önbelleği); `SearchError` |
| `site_search.search(site, query, limit=20, *, cfg=None)`, `supports(site)`, `search_sites()`, `describe(site)` | site_search/__init__.py:37, 58, 67, 73 | modül adaptörü önce, yoksa yaml `search:` |
| `collections.request_of(spec) -> (method, data)`, `request_key`, `method_of` | collections.py:104, 112, 90 | koleksiyon isteği; getirme+ayrıştırma `library/ingest.py` `_fetch_collection` :226, `load_collection` :257 (yönetim değil ama ingest'e bağlı) |

`runner.run_site(site_id, *, persist=True) -> RunResult` (runner.py:61) adımları: `load_site` -> `fetch.page(list_url, wait_for=row_selector)` -> `parse.parse_list` -> `schema.validate_items` -> `drift.detect` -> drift varsa `heal.heal(...)` + yeniden ayrıştırma + `update_baseline`; yoksa `_enrich_first` (:39, TEK detay sayfası) -> `state.record_run`. Yalnız ana `list_url`'i kapsar; koleksiyon, dizi sayfası, oynatıcı içermez. Tek başına çağrılabilir parçalar: fetch/parse/schema/drift (hepsi saf ya da tek amaçlı). `run_site` kendisi `heal` + `state` import ettiği için yeni kodda KULLANILMAMALI (4 satırlık zincir yeniden yazılır).

Drift = sayaç eşiği, yapısal imza değil: `valid_count == 0`, `< min_items`, `fill_ratio < min_fill_ratio` (vars. 0.5), `field_fill[alan] < critical_field_fill[alan]`. Dizi sayfası için ayrı taban `baseline.series_page.min_items` + `structured=False` uyarısı. Planın `fingerprint` alanı (seçici başına düğüm sayısı / DOM iskeleti) için hazır kod YOK; en yakın malzeme `validate_items` metrikleri, `heal.field_fill(raw, names)` (heal.py:70, tüm alanların doluluk oranı) ve `onboard_sandbox._outline_of` (:1478, tekrar eden `(etiket, sınıflar)` grupları). Yeniden kullanım: "sayfa türü başına `{valid_count, field_fill}` + eşik" imza olarak doğrudan uygun; DOM iskelet özeti yeni yazılacak.

## 4. Resolver / provider

Resolver tipleri (`resolvers.TYPES`, resolvers/__init__.py:37; her modül `NAME, DESCRIPTION, PARAMS, discover(ctx, html, page_url, params)`, isteğe bağlı `check(params)`, `resolve_candidate(ctx, candidate, page_url, params, load_cookies)`):

| Tip | Dosya | Aday nasıl bulunur |
|---|---|---|
| `iframe` | resolvers/iframe.py:32 | seçicideki öğenin `attr`'ı (vars. `src`), `host_regex` süzgeci |
| `anchor_host` | resolvers/anchor_host.py:32 | host'u provider'a uyan bağlantılar |
| `data_attr_token` | resolvers/data_attr_token.py:54 | öznitelikteki token -> URL şablonu |
| `ajax_handoff` | resolvers/ajax_handoff.py:84, 181 | site ucuna POST/GET el sıkışması (çerez, tarayıcı yedeği) |
| `json_api` | resolvers/json_api.py:61, 76 | embed URL -> JSON API; adayın içinde hazır `stream` döner |
| `player_page` | resolvers/player_page.py:237, 511 | sitenin kendi oynatıcı sayfasını açar, `follow` (<=2) + `extract` (<=8 kural) |
| `embedded_json` | resolvers/embedded_json.py:188 | sayfa içi JSON / Next.js flight verisi |

| İmza | Satır | Ne yapar / dönen biçim |
|---|---|---|
| `resolvers.catalog() -> [{type, description, params}]` | resolvers/__init__.py:115 | LLM/arayüz için tip kataloğu |
| `resolvers.make_ctx(cfg) -> Ctx(site_id, base_url, cfg, fetch)` | :31 | resolver bağlamı |
| `site_extractors.discover(site_id, html, page_url, cfg=None) -> list[dict]` | site_extractors/__init__.py:66 | GİRİŞ 1: sayfa HTML'i -> adaylar `{url, label?, lang?, handoff?, stream?, resolver (liste indeksi), resolver_type}`; ağ yok |
| `site_extractors.resolve_candidate(site_id, candidate, page_url, load_cookies, cfg=None) -> dict\|None` | :119 | GİRİŞ 2: aday -> provider URL'si (el sıkışma gerekiyorsa) |
| `providers.registry.resolve(url, *, referer="", max_handoffs=2, load_handoff=None, allowed=None, extra=None) -> dict\|None` | providers/registry.py:111 | GİRİŞ 3: provider URL'si -> `{url, type (hls\|mp4), quality, duration, provider, streams[{url, type, quality, label, request_headers?, stream_proxy?}], subtitles?, cache_ttl?, variant?}`; bilinmeyen host'ta <=2 iframe atlaması |
| `player_page.resolve_player(url, params, *, referer, fetch_api, base_url="", cfg=None)` | resolvers/player_page.py:542 | tarif motoru (site yaml'ı olmadan) |
| `player_page.extract(body, base, rules, notes=None) -> [{url, type, quality, label}]` | :387 | saf: gövde + kurallar -> akışlar |
| `resolve.resolve_stream(cfg, embed_url_or_video_id)` | resolve.py:61 | eski `stream_resolver:` yolu |
| `providers.trace.begin() / take() / note(stage, host, ok, started, error)` | providers/trace.py:17, 21, 32 | iş parçacığı başına aşama izi |
| `library/videos._resolve_candidate(row, cfg, raw, locator, frame_pages, load_cookies, use_neg) -> {label, provider, streams, subtitles, duration, error, events, ms, lang, resolver_type}` | library/videos.py:182 | 2+3'ü birleştiren, hata yutmayan tek aday çözümü (özel ad; DB'siz çağrı örneği `library/gate._resolve_streams` :140) |
| `library/gate.judge_html(cfg, html, url, *, kind, require, ...) -> {state ok\|blocked\|retry, reason, via, candidates?}` | library/gate.py:161 | "bu sayfada oynatıcı/akış var mı" hazır yargısı (A5/A6 kabul ölçütü) |

Provider kaydı: kod provider'ları sabit liste `CODE_PROVIDERS` (registry.py:54: `vidmolly`, `okru`; `Provider(name, matches, resolve, description, hosts, kind)` :22). Tarifler diskten: `recipes.load_all()` (recipes.py:310, mtime/boyut imzasıyla önbellek, bozuk dosya atlanır). Birleşik liste `registry.providers(extra=None)` (:60), canlı görünüm `registry.PROVIDERS` (:77), katalog `registry.catalog()` (:80). Kayıt dekoratörü yok; yeni kod provider'ı = listeye elle ek.

Tarif şeması (`configs/providers/<ad>.yaml`, `RECIPE_KEYS` recipes.py:58): `name, description, version, label?, match{host_regex*, path_regex?}, fetch (http\|browser), referer ({page_url}\|{base}), headers, warm_session, wait_for, follow[], extract[]*, stream_headers, verify, cache_ttl (60..86400), stream_proxy (bool), updated_at`. Doğrulama `recipes.validate_recipe(data) -> list[str]` (:126); metinden `recipes.from_text(text, *, fetch_api=None) -> (RecipeProvider\|None, errors)` (:276); güncellemede yalnız host genişletme `recipes.update_problems(active, new)` (:182), `widen_host_regex` (:177). Mevcut tarifler: `ddizi_player`, `trdizi_player`.

"Host'u kim tanıyor" sorguları:

| İmza | Satır | Not |
|---|---|---|
| `provider.matches(url) -> bool` (`Provider` / `RecipeProvider.matches` recipes.py:240) | - | temel sorgu |
| `sitequality.host_known(url, pool) -> bool`, `provider_pool(extra)` | sitequality.py:49, 40 | motor tarafında, saf: yeni kod bunu kullanmalı |
| `heal_agent._host_covered(host) -> bool` | heal_agent.py:198 | yönetim modülünde; `sourcefinder.py:472` de buradan çağırıyor. Kütüphaneye taşınmalı |
| `provider_match.match(url, *, referer="", fetch_api=None, deadline=None) -> {player_url, host, page{media_urls, iframes, packed,...}, matches[], recommendation{action: use_provider\|add_host\|new_recipe\|needs_code, recipe_yaml?}, ms, notes}` | provider_match.py:200 | host eşleşmesi YOK SAYILARAK tüm provider'ları kuru çalıştırır (<=50 sn, 3 işçi); netguard denetimi çağırana ait |
| `badhosts.bad_stream_host(url)`, `looks_like_media(url)`, `own_domain(url)` | badhosts.py:40, 32, 51 | reklam/izleyici host süzgeci |

## 5. LLM'siz keşif / analiz yardımcıları

| İmza | Satır | Ne yapar | Yeni aşama |
|---|---|---|---|
| `discover.discover(url, getter, *, outline=None, deadline=None, now=...) -> {yaml_text, found, missing, confidence, pages_fetched, pages, notes, errors, site_id}` | discover.py:1496 | <=8 sayfayla tüm taslak yaml'ı kodla üretir (`getter(url, role) -> Page` enjekte) | A1-A5'in tamamı, tek parça |
| `_Discovery.read_home` / `choose_list` / `build_collections` / `read_series_pages` / `read_detail` / `read_episode_page` / `build_player` / `build_normalize` | :696, 741, 826, 920, 950, 963, 992, 1027 | aşama adımları sınıf içinde, ortak duruma bağlı | bölünmesi gerekir |
| `discover.extract_fields(cards, base, kind) -> (fields\|None, evidence)` | :347 | kart bloğundan başlık/bağlantı/afiş/yıl/puan seçicileri, kartlarda doğrulanmış | A1, A2 |
| `discover.role_of(heading, kind, hero=False) -> (role\|None, neden)` | :426 | bölüm başlığından koleksiyon rolü | A1 |
| `discover.episode_regex(path) -> {regex, season, slug}\|None` | :499 | bölüm adresinden `episode_url_regex` türetir | A4 |
| `discover.build_series_page(html, page_url, outline, list_paths) -> (spec\|None, evidence)` | :1142 | `series_page` bloğu, gerçek genel motorla doğrulanmış | A4 |
| `discover.detail_fields(html, page_url) -> (fields, evidence)` | :1281 | etiketli metin -> JSON-LD -> og/meta | A3 |
| `discover.player_candidates(html, page_url, base) -> [{url, host, how, trailer, placeholder, resolver}]` | :1416 | iframe, sekme/data özniteliği, bilinen host bağlantısı, script URL'leri + önerilen resolver öğesi | A5 |
| `discover._search_form(tree, base)`, `_json_ld(tree)` | :459, :473 | arama formu / JSON-LD (özel adlar) | A7, A3 |
| `onboard_sandbox._outline_of(html, base, requested, final="") -> dict` | routers/onboard_sandbox.py:1478 | bağlantı kümeleme: tekrar eden bloklar, `nav_links`, `sections`, `episode_links` | A1, A2, A4: KRİTİK, yönetim modülünde |
| `blocked.match(html, rules, on)`, `placeholder_hint(html)`, `frame_sources(html, limit=4)` | blocked.py:177, 219, 155 | telif yer tutucusu eşleşmesi / ipucu | A7 |
| `sitequality.assess(playable, data=None, warnings=None) -> {grade, reasons, hosts, *_ratio}`, `source_rows(...)` | sitequality.py:141, 60 | oynatma kalitesi notu (standart/karışık/zayıf); saf | A6 raporu |
| `provider_match.match` (§4) | provider_match.py:200 | bilinmeyen oynatıcı host'unu kütüphaneyle kıyaslar | A6 |

Eksik: adres kalıbına göre genel bağlantı kümeleme (`url_pattern` üretimi) yalnız bölüm adresi için var (`episode_regex`); listing/title kalıbı çıkarımı ve sayfalama keşfi YOK. Kabul ölçütü hesapları (`_criteria` :2027, `_playable_criteria` :2532, `_series_criteria` :2835, `_search_criteria` :3095, `_collection_criteria` :3177) `onboard_sandbox` içinde.

## 6. LLM çağrısı

| İmza | Satır | Not |
|---|---|---|
| `pi_agent.run(job: Job, *, token, timeout, on_event, on_label=None, on_start=None, model_name=None, extension=None, skill=None, pi_bin=None) -> RunResult(parser, exit_code, crash, timed_out, stderr)` | pi_agent.py:485 | araçlı ajan turu; asla raise etmez; `timeout` sonunda süreç öldürülür |
| `pi_agent.Job(job_id, first_message, mode="onboard", tools=ONBOARD_TOOLS, env_extra={})` | :64 | `job_id` = pi oturum kimliği (aynı id + aynı cwd = aynı oturum sürer); ilk mesaj stdin'den |
| `pi_agent.build_command(job_id, *, tools, pi_bin, model_name, extension, skill, session_dir) -> list[str]` | :146 | `pi -p --mode json --offline --no-builtin-tools --tools <liste,read> --no-extensions -e <ext> --no-skills --skill <dir> --no-context-files --no-prompt-templates --model M --session-dir D --session-id ID` |
| `pi_agent.child_env(token, job_id, skill_dir=None, extra=None)` | :119 | sır görünümlü değişkenler atılır; `DIZIFLIX_SANDBOX_URL/ONBOARD_TOKEN/DRAFT_ID/SKILL_DIR/MODE` eklenir |
| `pi_agent.EventParser` | :315 | JSONL olay akışı; `last_say`, `provider_error`, `turns`, `label` |
| `pi_agent.model()` | :85 | `ONBOARD_MODEL`, yoksa `llm_health.heal_model()` |
| `heal._call_pi(prompt, timeout) -> {status: ok, text}\|hata` | heal.py:239 | ARAÇSIZ TEK SEFERLİK: `pi -p --model M --no-session --no-tools --offline`, prompt stdin, yanıt stdout |
| `heal._call_codex(prompt, timeout)` | heal.py:191 | `codex exec - -s read-only`; `SCRAPER_HEAL_PROVIDER` ile seçilir (`_provider_generate` :286) |
| `heal._extract_yaml(text)`, `_clean_html(html, limit=60000, anchor_tokens)` | heal.py:178, 134 | yanıttan yaml bloğu / kırpılmış HTML özeti |

Araçlar Python'da tanımlı değil: `server/pi/extensions/diziflix-onboard.ts` kaydeder, her araç `/api/onboard/sandbox/*` ucuna HTTP çağrısıdır; Python yalnız izin listesini (`ONBOARD_TOOLS` :44, `REPAIR_TOOLS` :48, `EDIT_TOOLS` :53) ve `DIZIFLIX_MODE`'u verir. Kısa, araçsız, tek seferlik çağrı MÜMKÜN ve hazır (`heal._call_pi`), ama `heal.py` yeniden yazılacaklar listesinde: fonksiyon (ve `_extract_yaml`, `_clean_html`) kütüphaneye taşınmalı. Yapılandırılmış çıktı zorlaması yok; yanıt serbest metin, yaml bloğu ayıklanıyor.

## 7. Yönetim katmanına bağımlılıklar (kopması gerekenler)

| Motor modülü | İçe aktardığı | Satır | Etki |
|---|---|---|---|
| `scraper/runner.py` | `heal`, `state` | 12 | `run_site` kullanılmaz; fetch/parse/schema/drift zinciri yeniden yazılır |
| `scraper/__init__.py` | `runner` (dolayısıyla `heal`), `state` | 16-18 | `import app.scraper` bile `heal` + `state` yükler; paket başlığı temizlenmeli |
| `scraper/discover.py` | `routers.onboard_sandbox` (`_outline_of`), `library.normalize` (`fold`) | 125, 130 | `_outline_of` kütüphaneye taşınmadan `discover` yeni kodda kullanılamaz |
| `scraper/pi_agent.py` | `onboard_store` (`store.root()`: oturum ve çalışma dizini), `llm_health` | 28-29 | dizin kökü parametreye çevrilmeli |
| `scraper/state.py` | `library.sourcefinder` (`rerun_for_site`, tembel) | 272 | `_ops.json` zaten kalkıyor |
| `scraper/collections.py` | `library.categories` (tembel) | 67, 76 | kütüphane içi, sorun değil |
| `scraper/transport.py` | `images.cache_remote_original` (tembel) | 122 | kalır |
| `scraper/site_handoff.py` | `db` (tembel) | 528 | yönetim tarafı (site not dosyası), motor değil |
| `library/videos.py` | `playheal` (`_playheal` :335), finder (`_finder_state` :603) | - | çözümleme çekirdeği yönetim modüllerine olay yolluyor; ayrıntı: doğrulanmadı (ayrı envanter) |
| `library/sourcefinder.py` | `heal_agent._host_covered` | 472 | §4 |

Temiz (yönetim importu yok): `config.py`, `fetch.py`, `parse.py`, `schema.py`, `drift.py`, `series_generic.py`, `search_generic.py`, `blocked.py`, `badhosts.py`, `resolve.py`, `sitequality.py`, `provider_match.py`, `resolvers/*`, `providers/*`, `site_extractors/*`, `site_search/*`.

## 8. Sayfa türü modeline eşleme

| Yeni düğüm | Bugünkü yaml karşılığı | `extract` | `links` | `expect` karşılığı | Eksik |
|---|---|---|---|---|---|
| home | `collections[]` (rol başına bölüm; `path` çoğunlukla `/`) | `collections[].row_selector` + `fields` | kartın `detail_url` alanı | `collections_valid_count >= 3` (sandbox) | menü bağlantıları saklanmıyor (yalnız `_outline_of.nav_links` çıktısında); `url_pattern` yok |
| listing | `list_url` + `list{row_selector, fields}`; ayrıca `role: catalog\|genre\|category` koleksiyonları | `list.fields` | `detail_url` | baseline `min_items`, `critical_field_fill` | SAYFALAMA YOK (`item_limit` ile tek sayfa); toplam sayfa tahmini yok; birden çok liste türü tek `list` bloğuna sığmıyor |
| title | `detail{fields}` + `normalize{key, type, fields, clean}` | `detail.fields` | dizi: `series_page`'e; film: aynı sayfa oynatıcı | yok (runner yalnız ilk öğeyi zenginleştirir) | film/dizi ayrımı `normalize.type`'ta (adres regex'i), sayfa içeriğinden değil; `url_pattern` yalnız `normalize.key.regex` ve `series_url_regex` içinde örtük |
| season | `series_page.season_pages{season_menu, season_url_regex}` | - | sezon menüsü seçicisi | - | ayrı düğüm değil, `series_page`'in alt alanı; sezon başına alan yok |
| episode (liste) | `series_page{row_selector, fields{url,title,air_date}, episode_url_regex}` | `series_page.fields` | `fields.url` | baseline `series_page.min_items`, `structured` | sezon/bölüm no adresten gelir (işaretlemeden değil); `normalize.episode_source` ile örtüşen ikinci yol var |
| player | `resolvers[]` + `use_site_module` + `blocked[]` + `availability_gate` | resolver parametreleri | aday URL'leri -> provider | `availability_gate.require: player` | bölüm sayfası için alan çıkarımı yok (oynatıcı sayfasında başlık/tarih okunmuyor) |
| sources | `providers[]` + `configs/providers/*.yaml` (+ eski `stream_resolver`) | tarif `extract[]` | - | `require: stream`, `sitequality` | site yaml'ında değil, kütüphanede (plan da böyle) |
| search | `search{...}` ya da `site_search/<site>.py` | `search.fields` | `detail_url` | `search_ok` / `found_known` (sandbox) | - |

Eşlenemeyen / eksik kalanlar:
- `samples[3-5]`: yaml'da örnek adres saklanmıyor (onboarding taslağında geçici).
- `fingerprint`: yok (§3); baseline yalnız ana liste + dizi sayfası için eşik tutuyor, detay/oynatıcı/arama/koleksiyon için hiç taban yok.
- `skipped[]`: yaml'da karşılığı yok (bugün "blok yok" = atlanmış; nedeni yalnız handoff notlarında).
- `url_pattern`: yalnız `episode_url_regex`, `series_url_regex`, `normalize.key.regex` biçiminde dağınık.
- Sayfa türüne ait olmayan mevcut anahtarlar yeni modelde site düzeyinde kalmalı: `fetch_mode`, `obscura_*`, `cache_images`, `image_hosts`, `item_limit`, `playback`, `schema`, `normalize.host/base_url`, `series_crawl`, `detail_prewarm_*`, `detail_pages`.
- `site_extractors/yabancidizi.py` (560 satır) ve `site_search/yabancidizi.py` kod modülü: yaml ile ifade edilmiyor; "3 site yeniden eklenir" kararında bu sitenin genel motorla çalışıp çalışmayacağı doğrulanmadı.
