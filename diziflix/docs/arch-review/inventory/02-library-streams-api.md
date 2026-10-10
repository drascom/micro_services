# Envanter 02: katalog / ingest / akış çekirdeği + istemci API'si

Salt-okur kod okuması (2026-10-03). Yollar `server/app/` altına görelidir. Kod çalıştırılmadı; "doğrulanmadı" yazan yerler koddan
kesinleştirilemedi. Amaç: `app/control/`'ün çağıracağı kütüphane yüzeyi ve kesilecek eski yönetim kancaları.

## 1. DB (`db.py`)

### 1.1 Tablolar (`SCHEMA`, db.py:16-235)

| Tablo (satır) | Ana kolonlar / PK | Amaç |
|---|---|---|
| `profiles` (17) | `id` PK, name, is_kids, avatar_seed | Profil (auth yok); boşsa `p1`,`p2` tohumlanır (db.py:404) |
| `progress` (24) | PK (profile_id, episode_id); item_id, position, duration, watched | İzleme ilerlemesi |
| `mylist` (34) | PK (profile_id, item_id) | Listem |
| `continue_hidden` (42) | PK (profile_id, item_id); hidden_at | "Devam et"ten yumuşak gizleme |
| `library_items` (52) | `id` PK (kanonik), tmdb_id, type, title, year, genres(JSON), poster_url, backdrop_url, tmdb_poster_url, tmdb_backdrop_url, tmdb_enrich_status, tmdb_checked_at, added_at, popularity | Kanonik başlık; `merge_canonical` yeniden üretir |
| `source_items` (77) | PK (source, source_key); canonical_id, raw(JSON), normalized(JSON), source_url, trailer_url, fetched_at (+ eski resolved_* kolonları) | Site başına ham + normalize kayıt. **Durum işaretleri `normalized` JSON içinde**: `_series_inventory`, `_gate`, `_added_at`, `_detail_metadata_at`, `_series_page_unknown` |
| `field_provenance` (93) | PK (canonical_id, field); source | Hangi alan hangi siteden |
| `library_lists` (103) | PK (list_id, canonical_id); position | Liste üyeliği (bkz. §3) |
| `home_categories` (118) | slug PK, title, position, enabled, min_items, kind (`system`\|`category`) | Ana ekran satır sırası + admin kategorileri |
| `catalogue_aliases` (127) | alias PK -> canonical_id | Birleşen eski kimlikler |
| `external_ids` (128) | PK (provider, media_type, external_id); UNIQUE(canonical_id, provider) | tmdb/imdb bağları |
| `identity_reviews` (132) | UNIQUE(source, source_key); reason, candidates, status | Belirsiz kimlik kuyruğu |
| `video_sources` (137) | `id` PK (`vs_<sha24>`), canonical_id, source, source_key, episode_id, season, episode, kind (movie\|episode\|trailer), locator, resolver (page\|direct\|embed), media_type, status, failures, last_error, last_checked_at, last_success_at, resolved_payload, resolved_at, trailer_dead, proxy_required, proxy_headers, last_diag, episode_* meta | Oynatma kaynağı + sağlık + çözüm önbelleği (bkz. §4) |
| `playback_attempts` (151) | token PK, source_id, created_at, success_at, failure_at, error_code, engine, failure_counted | `/api/streams` deneme belirteci (24 sa) |
| `trailer_checks` (161) | video_id PK, state (ok\|dead) | YouTube fragman hükmü |
| `library_seasons` (169) / `library_episodes` (174) | PK (canonical_id, season[, episode]) | TMDB sezon/bölüm metası |
| `stream_host_rules` (185) | host PK, headers(JSON), reason (referer\|ip), fail_count, expires_at, suspended | Doğrulanmış akış host kuralı |
| `playback_issues` (196) | source_id PK, site, issue_class, diag_code, host, reports, triggered_at | Oynatma sorun defteri (`playissues.py`) |
| `finder_jobs` (211) | id PK, canonical_id, episode_id, profile_id, state, trigger, steps(JSON), source_id, method | Kaynak bulucu işleri (**plan: kalkar**) |
| `notifications` (219) | id AUTOINCREMENT, profile_id, kind, canonical_id, episode_id, payload(JSON), created_at, read_at | Profil bildirimi |
| `blocked_pages` (230) | PK (site, url); kind (episode\|movie\|series), source_key, status (blocked\|retry), via (rule\|gate), reason, checked_at | Telif kapısı hükümleri |

### 1.2 Migrasyon, bağlantı, kilit

| Konu | `dosya:satır` | Gerçek davranış |
|---|---|---|
| Yeni tablo | db.py:16,115,126,182,192,207 | `SCHEMA += ["CREATE TABLE IF NOT EXISTS ..."]`; `init()` her açılışta hepsini çalıştırır (db.py:400). Sürüm numarası/migrasyon tablosu YOK |
| Yeni kolon | `_migrate(conn)` db.py:327 | `PRAGMA table_info(t)` ile kolon var mı bakılır, yoksa `ALTER TABLE ... ADD COLUMN` (yalnız ekleme). Yeni kolon hem `SCHEMA`'ya hem `_migrate`'e yazılır |
| `init()` | db.py:395 | `_lock` altında ayrı bağlantı: SCHEMA -> `_migrate` -> commit -> profil tohumu. Her açılışta `library_items` tam taranıp `external_ids`'e `INSERT OR IGNORE` yapılır (db.py:351) |
| `connect()` | db.py:238 | Yeni `sqlite3` bağlantısı, `timeout=10`, `Row`, `journal_mode=WAL`, `foreign_keys=ON` |
| `_shared()` | db.py:264 | **İş parçacığı başına kalıcı bağlantı** (`threading.local`); `DB_PATH`/inode değişince yeniden açılır |
| `query(sql, params=()) -> list[Row]` | db.py:277 | Paylaşılan bağlantıdan okuma, kilitsiz |
| `query_one(sql, params=()) -> Optional[Row]` | db.py:308 | `query`'nin ilk satırı |
| `execute(sql, params=()) -> None` | db.py:313 | Süreç geneli `_lock` + commit; hata olursa rollback. Tek ifade = tek işlem |
| `read_transaction()` | db.py:288 | Context manager: tek tutarlı okuma görünümü (WAL), çıkışta ROLLBACK; yazma desteklenmez |
| Çok ifadeli yazma | ingest.py:826, videos.py:744, purge.py:169 | `db.connect()` ile kendi bağlantısı; **`db._lock`'u ALMAZ**, yalnız SQLite `timeout=10` + (`videos._feedback`, purge) `BEGIN IMMEDIATE` |
| Ingest kilidi | `enrich.ingest_lock()` enrich.py:198 | `fcntl.flock` dosya kilidi (`<DB_PATH>.ingest.lock`): süreçler arası; `ingest_source` tüm tarama boyunca tutar |
| Keşif kilidi | `ingest._discovery_lock` ingest.py:31 | `RLock`: arama kaydı, detay hydrate, dizi envanter yazımı |

Sonuç: `jobs`, `crawl_frontier`, `routing_rules`, `problems*` yeni tabloları `SCHEMA += [...]` ile eklenir; tek ifadeli yazma `db.execute`, çok
ifadeli atomik yazma (iş kiralama) `db.connect()` + `BEGIN IMMEDIATE` kalıbıyla yapılır.

## 2. Ingest giriş noktaları

### 2.1 Kataloga yazma zinciri

| Fonksiyon | `dosya:satır` | Ne yapar |
|---|---|---|
| `ingest_source(site: str, trigger: str = "cli") -> dict` | library/ingest.py:995 | TÜM site taraması: `ingest_lock` + `state.activity_start` + `_ingest_source` + run kaydı + **playheal kancası** (1040-1045) |
| `_ingest_source(site) -> dict` | ingest.py:625 | `run_site` (drift/heal dahil) -> koleksiyonlar -> normalize -> `series_dir` -> `gate.screen` -> TMDB toplu -> yazma -> `library_lists` -> `series_crawl.run_stage` -> `gate.recheck` -> kapsama -> TMDB sezon |
| **`ingest_discovered_items(site: str, items: list[dict]) -> list[str]`** | ingest.py:336 | **Tek kayıt/küçük parti için var olan en küçük giriş.** Ham kart dict'leri alır; normalize -> `series_dir.ensure` -> blocked anahtar eleme -> `identity.choose` -> `source_items` upsert -> `merge_canonical` -> `videos.sync_source`. Kanonik id listesi döner |
| `merge_canonical(conn, canonical_id) -> None` | ingest.py:103 | Bir `library_items` satırını bağlı tüm `source_items`'tan yeniden kurar (`SOURCE_PRECEDENCE`, yer tutucu afiş elenir) |
| `videos.sync_source(conn, site, key, cid, norm, cfg)` | library/videos.py:44 | `norm["video_sources"]` (+fragman/film sayfası) -> `video_sources` upsert; taze blocked URL'ler yazılmaz |
| `hydrate_item_metadata(item_id) -> bool` | ingest.py:398 | Tek başlığın detay sayfasını okuyup metayı tazeler (TTL 6 sa) |
| `hydrate_series_item(item_id) -> bool` | ingest.py:526 | Tek dizinin envanterini (vadesi geldiyse) okur ve yazar; kapı elerse kütüphaneden çıkarır |
| `_enrich_series_catalogs(cfg, by_key, raw_by_key, reports, errors, *, force=False, cards=None)` | ingest.py:265 | Verilen dizilerin envanterini `normalized` içine okur (yazmaz); özel ama `series_crawl` ve hydrate bunu çağırır |
| `set_progress_hook(fn)` / `_progress(stage, site)` | ingest.py:41 / 50 | Aşamalar: `titles`, `inventory`, `seasons` (cache yenileme kancası) |

`ingest_discovered_items`'ın K0/K1 için **eksikleri** (yeni kod tamamlamalı ya da fonksiyon genişletilmeli):
1. `library_lists`'e HİÇ yazmaz (üyelik yalnız `_ingest_source` ingest.py:900-909'da, liste bazında sil-yeniden-yaz).
2. `_added_at`'i 0 bırakır (ingest.py:373: arama kaydı ana ekranda "yeni" görünmesin diye) -> `series`/`movies` satırlarında en sonda kalır.
3. TMDB'yi çağırmaz (çağıran `enrich.schedule` yapar, search_all.py:96); `gate.screen` çalıştırmaz (yalnız taze blocked anahtarı atlar).
4. Girdi biçimi sitenin ham kart dict'idir (`normalize(site, raw)` ne bekliyorsa); "url + alanlar" için genel bir giriş yok.

### 2.2 Normalize, kimlik, kapı, envanter, TMDB

| Fonksiyon | `dosya:satır` | Not |
|---|---|---|
| `normalize(source: str, raw: dict) -> Optional[dict]` | library/normalize.py:75 | `@register(site)` fonksiyonu kazanır (sinemalar :106, yabancidizi :138); yoksa yaml `normalize:` (`_site_rules` :777, dosya mtime önbelleği); ikisi de yoksa `KeyError`. `raw`'daki `tmdb_id`/`imdb_id`/`video_sources` aynen taşınır |
| `generic_normalize(rules, raw, *, base_url="")` / `validate_rules(rules)` / `preview(rules, raws, *, base_url="")` / `explain(...)` | normalize.py:697 / 262 / 702 / 681 | Config'siz (taslak kuralla) çalıştırma: yeni aşama doğrulaması bunları kullanır |
| `is_placeholder_image(url, shared=None) -> bool` | normalize.py:43 | Yer tutucu afiş tespiti (kırık afiş sinyaline de yarar) |
| `clean_title`, `clean_series_slug`, `fold` | normalize.py:522 / 482 / 458 | Başlık/slug temizliği |
| `identity.choose(conn, site, norm, previous=None) -> str` | library/identity.py:33 | Kanonik id seçer: external id eşleşmesi -> önceki bağ -> başlık+yıl tek aday -> yeni id; çakışmada `identity_reviews` |
| `identity.bind(conn, cid, tmdb_id=None, imdb_id=None)` / `merge(conn, source_id, target_id)` | identity.py:81 / 105 | Elle bağlama / birleştirme |
| `gate.configured(cfg)` / `gate_of(cfg)` / `episode_rules(cfg)` | library/gate.py:98 / 94 / 86 | Sitede `blocked:` ya da `availability_gate:` var mı |
| `gate.fetch_verdict(cfg, url, *, kind, require=None, fetcher=None, module_site=None) -> dict` | gate.py:205 | Tek sayfa hükmü `{state: ok\|blocked\|retry, reason, via}` |
| `gate.judge_html(cfg, html, url, *, kind, require=None, ...)` | gate.py:161 | Getirilmiş HTML'i yargılar (istek yok) |
| `gate.series_verdict(cfg, episodes, *, known=None, probe=None, require=None, pacer=None, fetcher=None, module_site=None, judge=None)` | gate.py:302 | `{state, reason, via, probed, pages[], blocked[], retry[]}`; en yeni+en eski(+orta) bölüm yoklanır |
| `gate.item_verdict(cfg, url, *, ...)` | gate.py:348 | Film için aynı biçim, tek sayfa |
| `gate.store(site, key, item_url, verdict, *, kind, now=None)` / `record(...)` / `forget(site, url)` | gate.py:372 / 221 / 237 | `blocked_pages` yazımı |
| `gate.fresh_blocked_urls(site, key, conn=None)` / `blocked_item_keys(site, now=None)` / `rows_of(site, *, limit=100)` | gate.py:248 / 260 / 269 | Taze (BLOCKED_RECHECK_DAYS=7) blocked okuma |
| `gate.screen(cfg, site, by_key, *, now, sleep, fetcher) -> {dropped, removed_keys, counters}` | gate.py:439 | Yazmadan önce film + envantersiz dizi kartı eleme; bütçe `GATE_BUDGET`=40 sayfa |
| `gate.recheck(cfg, site, *, limit=None, ...)` | gate.py:515 | Eski blocked sayfaları yeniden yargılar (`GATE_RECHECK_PAGES`=3); açılanın kaynağı `status='unknown'` |
| `gate.remove_items(site, keys) -> int` | gate.py:407 | `purge.purge_source_items` (purge.py:169): kullanıcı verisi kalır |
| `series_crawl.enabled(cfg)` | library/series_crawl.py:70 | Envanter aşaması açık mı (modül `series_catalog: true` ya da yaml genel `series_page`) |
| `series_crawl.crawl(cfg, url) -> dict` | series_crawl.py:197 | Tek dizinin envanterini okur, YAZMAZ (sayfa sınırı `SERIES_CRAWL_MAX_PAGES`=6) |
| `series_crawl.due(inv, card=None, now=None) -> Optional[str]` | series_crawl.py:124 | `missing\|retry\|new_episode\|aired\|stale` ya da None |
| `series_crawl.select(site, cfg, by_key=None, now=None, force=False, only=None, stats=None)` | series_crawl.py:479 | Vadesi gelen diziler, aciliyet sırasıyla |
| `series_crawl.run_stage(cfg, site, by_key=None, *, force=False, limit=None, dry_run=False, only=None, on_progress=None) -> dict` | series_crawl.py:571 | Bütçeli geçiş. `only=<source_key>, limit=1, force=True` = tek dizi (sourcefinder.py:404 böyle çağırır) |
| `series_crawl.inventory_due(item_id) -> bool` | series_crawl.py:164 | Detay isteğinde ucuz vade kontrolü |
| `series_dir.for_site(cfg)` / `ensure(cfg, norm, directory=None, ...)` / `build(cfg, norms)` / `remember(cfg, directory)` | library/series_dir.py:192 / 229 / 148 / 170 | Bölüm kartını dizi sayfasına çözme dizini (istek atmaz) |
| `enrich.schedule(cids, on_done=None) -> bool` | library/enrich.py:318 | Arka plan TMDB (thread `title-enrich`; başlık başına 300 sn'de bir) |
| `enrich.enrich_items(cids, budget=None, force=False) -> dict` | enrich.py:270 | Bloklayan TMDB eşleme + (dizi) sezonlar |
| `enrich.run_batch(jobs, budget=None, ...)` / `apply_result(conn, cid, site, key, res, now=None)` / `fill_norm(norm, data)` | enrich.py:101 / 160 / 147 | Ingest içi toplu yol |
| `enrich.backfill(kind, dry_run=False, limit=0, ...)` | enrich.py:486 | Geriye dönük doldurma (admin/CLI) |
| `seasons.auto_enrich(cids, budget=None, locked=False)` | library/seasons.py:262 | TMDB sezon/bölüm metası (`locked=True`: ingest kilidi zaten tutuluyor) |

Kota / geri çekilme (config.py:156-172): `SERIES_CRAWL_BUDGET`=15 dizi/koşu (yalnız BAŞARILI okuma sayılır), `SERIES_CRAWL_SECONDS`=300,
`SERIES_CRAWL_DELAY`=3 sn, `SERIES_CRAWL_MAX_ERRORS`=5 art arda hata geçişi durdurur, `SERIES_CRAWL_REFRESH_DAYS`=7, iyi okumadan sonra
`MIN_GAP`=3600 sn (series_crawl.py:91). Hata merdiveni `BACKOFF_STEPS=(1,4,12,28)` x `SERIES_CRAWL_RETRY_HOURS`(6 sa) = 6 sa/24 sa/72 sa/7 gün
(series_crawl.py:96). Durum tabloda değil, `source_items.normalized._series_inventory` işaretindedir (`at, ok_at, complete, fails, last, card, next_air`).

## 3. Liste / grup modeli

| Konu | `dosya:satır` | Bugünkü durum |
|---|---|---|
| Liste kimliği | `scollections.list_id(role, site_id, category=None)` scraper/collections.py:42 | `<role>_<site>`; kategori için `category_<slug>_<site>` |
| Roller | `ROLES` collections.py:23, `HOME_ROLES` :39 | trending, latest_episodes, latest_series, latest_movies, noteworthy_movies, featured, upcoming, new, catalog, genre, category |
| **Başlık -> liste eşlemesi** | ingest.py:655-698 | Yalnız **site yaml `collections:`** girdisinin `role` (+`category: <slug>`) alanı belirler: o koleksiyon sayfasındaki her kart o listeye girer. Kural tablosu yok, tür/url kalıbına göre eşleme yok |
| Diğer liste id'leri | ingest.py:646, 712, 722 | `source_<site>` (ana liste), `detail_<site>_<slug>`, `featured_<site>` (`featured == "poster-media"` kartları) |
| Üyelik yazımı | ingest.py:900-909 | Bu koşunun dokunduğu her `list_id` için `DELETE` + `INSERT OR REPLACE` (position = sayfa sırası); alias çözülür |
| Kategori tablosu | library/categories.py (`create` :265, `update` :287, `reorder` :310, `delete` :329, `ensure_system` :145) | `home_categories`: `kind='system'` iskelet satırları + admin kategorileri. Üyelik burada TUTULMAZ |
| Kategori üyeliği okuma | `categories.member_lists(snap, slug)` categories.py:199; `membership()` :188 | `library_lists WHERE list_id GLOB 'category_<slug>_*'` |
| Rol listeleri okuma | `rows._role_by_list(snap, role)` rows.py:323 | `GLOB '<role>_*'`, yalnız `HOME_ROLES` |
| Satır listesi | `homelayout.layout(profile_id="") -> list[str]` homelayout.py:196 | `home_categories` sırası; kategori satırı `cat_<slug>` |
| Satır içeriği | `homelayout.Context._build(row_id)` homelayout.py:262; `pool(snap, row_id, ...)` :316 | `series`/`movies` = türün tümü (`added_at` azalan); `trending_*` = sitelerin trending listeleri + `trend_score` dolgusu (:278); `cat_<slug>` = `_category` (:295, round-robin, `min_items` altı gizli); `noteworthy_movies` (:305) |
| Sinyaller | `homelayout.signals(snap)` homelayout.py:98 | trending + `SCORE_ROLES` listeleri (her istekte DB'den) |
| Tür satırları | sources/library.py:120 `genre_members()` | `library_items.genres`'ten türetilir (`tag_<tür>`), `library_lists` kullanılmaz |

`routing_rules` kanca noktası (öneri, kod değişikliği yapılmadı): **yazma tarafı**. Okuma tarafı (`homelayout`, `categories.member_lists`,
`rows._role_by_list`) yalnız `library_lists`'in `list_id` adlandırmasına bakar; kurallar `category_<slug>_<site>` / `<role>_<site>` satırlarını
üretirse okuma kodu ve istemci API'si değişmeden çalışır. Dikkat: (a) `_ingest_source` aynı `list_id`'yi her koşuda silip yeniden yazar
(ingest.py:904), kural çıktısı aynı id'yi paylaşırsa ezilir; (b) "kural değişince yeniden grupla" için girdi `source_items.normalized`
(tür, genres, source_url) + bölüm adı olmalı, bölüm adı bugün hiçbir yerde saklanmıyor (yalnız `library_lists.list_id` içinde örtük);
(c) yazmadan sonra `cache.refresh()` gerekir.

## 4. Akış (`library/videos.py` ve çevresi)

| Fonksiyon | `dosya:satır` | Ne yapar / döner |
|---|---|---|
| `streams(cid, episode_id=None, kind=None, profile_id='') -> dict` | videos.py:615 | `disabled/broken/blocked` dışı kaynakları paralel çözer; `{streams[], subtitles[], audio[], duration[, finder]}`; kaynak başına `playback_attempts` belirteci. **:652-654 akış yoksa `_finder_state` -> sourcefinder** |
| `resolve_source(row, force=False) -> dict` | videos.py:528 | TEK kaynak: geçerli önbellek varsa onu döner (bitimine `RESOLVE_REFRESH_AHEAD` kala arka plan tazeleme); `force=True` önbellek ve negatif önbelleği atlar. Hata: `ValueError` / `Blocked` |
| `retry(sid) -> {'ok', 'streams', 'status'}` | videos.py:788 | Zorla yeniden çözüm + `status='unknown', failures=0` (admin düğmesi; ops.py:488) |
| `feedback(token, event, code='', engine='', detail='', user_agent='', hlsjs=False, profile_id='') -> dict` | videos.py:694 | Sağlık güncelleme (`_feedback` :740, `BEGIN IMMEDIATE`) + `streamdiag.schedule` + **playissues (:714-717) + sourcefinder (:718, :724)** |
| `prefetch(cid, episode_id=None) -> bool` | videos.py:664 | `RESOLVE_PREFETCH` (varsayılan kapalı) arka plan ısıtma |
| `backfill()` | videos.py:95 | Açılışta tüm `source_items` için `sync_source` (kaynak satırlarını yeniden kaydeder) |
| `reset_caches()` / `_invalidate(sid)` | videos.py:137 / 577 | Bellek negatif önbelleği / tek kaynağın saklı çözümü silinir |
| `is_blocked(exc)` / `class Blocked` | videos.py:25 / 19 | Telif yer tutucusu: hata sayılmaz |
| `_record(row, outcomes, ...)` -> `_playheal(row, event)` | videos.py:301 / 335 | Her gerçek çözümde `state.record_resolver` + **`playheal.record` / `maybe_trigger`** |

`video_sources.status` yaşam döngüsü:

| Geçiş | Nerede |
|---|---|
| (yeni) -> `unknown` | `sync_source` videos.py:75 (locator değişirse `resolved_payload` sıfırlanır) |
| çözüm hatası -> `suspect` (failures artmaz) | videos.py:634 |
| telif yer tutucusu -> `blocked` | videos.py:355 (`_mark_blocked`), :632; `blocked_pages`'e de yazar |
| istemci hatası -> `failures+1`; 1-2 = `suspect`, >=3 = `broken`; saklı çözüm silinir | videos.py:777-781 (cihaz kodları `unsupported, decode, autoplay, aborted, offline` sayılmaz) |
| istemci başarısı -> `healthy`, failures=0 | videos.py:765 (daha yeni bir hata varsa yalnız kendi sayımını geri alır :766-769) |
| başarılı çözüm, failures=0 iken `suspect` -> `unknown` | videos.py:570-572 |
| `retry` / admin `enable` -> `unknown` | videos.py:797; routers/ops.py:493 |
| admin `disable` -> `disabled` | routers/ops.py:490 |
| kapı yeniden açtı -> `unknown` | gate.py:545 |

`broken` kaynak `streams()` tarafından hiç denenmez (videos.py:617); onu yalnız `retry`, admin ya da (bugün) sourcefinder `retry` adımı geri getirir.
Katalog durumu: `catalog.availability(providers)` catalog.py:16 -> `ready` (unknown/healthy var) \| `check_required` (yalnız suspect) \| `unavailable`.

| Tanı fonksiyonu | `dosya:satır` | Hüküm biçimi |
|---|---|---|
| `streamdiag.build_job(source, streams, code, engine, detail, browser_hls) -> Optional[dict]` | library/streamdiag.py:541 | Tanı işi `{source_id, streams[], learned, learned_headers, code, engine, detail, browser_hls, site, locator, episode_id, source_kind, resolver}` |
| `streamdiag.schedule(job) -> bool` | streamdiag.py:497 | Arka plan kuyruğu (kaynak başına `STREAM_DIAG_COOLDOWN`=600 sn, havuz 2, en çok 50 bekleyen) |
| `streamdiag.diagnose(job, *, now=None) -> Optional[dict]` | streamdiag.py:281 | `{code, http, ct, ms, why, learn, stream:{host,type,sent,proxied}[, learned_headers, rule, rule_ok, body]}`; `code` ∈ `CODES` (:53): reachable, forbidden, not_media, gone, timeout, expired, ip_bound, hls_unsupported_browser, server_blocked, unreachable |
| `streamdiag.run(job)` | streamdiag.py:452 | diagnose + `record` + hostrules + **`_issue_signal` (playissues :388) + `_heal_signal` (playheal :371)** |
| `streamdiag.quick_check(streams, learned=False, timeout=3.0) -> Optional[dict]` | streamdiag.py:346 | Yan etkisiz hızlı yoklama (aynı hüküm; None = belirsiz). **Yeni "doğrula" adımı için en temiz giriş** |
| `streamdiag.record(source_id, verdict, detail="")` / `persist_learning(source_id, verdict)` | streamdiag.py:397 / 436 | `last_diag` JSON `{code, http, ct, ms, at, note}` (<=600 bayt); `learn` ise `proxy_required=1` (+`proxy_headers`) ve host kuralı |
| `streamdiag.public_diagnosis(row) -> dict` / `read_diag(text)` | streamdiag.py:149 / 140 | Admin görünümü `{code, note, at, http, proxied, proxy_reason, proxy_required}` |
| `streamprobe.probe(url, headers=None, kind=None, locator="") -> dict` | library/streamprobe.py:157 | `{ok, http, ct, kind, duration_s, segments, encrypted, live, variants, needs_referer, referer, note, ms}` |
| `streamprobe.summarize(url, headers=None, kind=None, locator="", expected_min=None) -> dict` | streamprobe.py:224 | `{ok, http, kind, duration_min, expected_min, duration_match: ok\|short\|long\|unknown, ...}` ("süre makul" ölçümü) |
| `streamprobe.expected_runtime(canonical_id, episode_id="")` / `rank_streams(streams, expected_min, locator="")` / `summarize_many(items, workers=3, timeout=8.0)` | streamprobe.py:201 / 290 / 238 | Beklenen süre (dk) / süreye göre sıralama / paralel yoklama |
| `hostrules.get(host)` / `for_hosts(hosts)` / `find(host)` / `listing()` | library/hostrules.py:47 / 67 / 58 / 123 | Etkin kural `{host, proxy_required, headers, reason, fail_count, expires_at, suspended}` |
| `hostrules.learn(host, headers, reason, source_id="") -> bool` / `note_probe(host, ok)` / `suspend(host)` / `delete(host)` | hostrules.py:80 / 99 / 112 / 117 | Yalnız doğrulanmış yoklamadan yazılır; TTL 30 gün, 2 hata askıya alır |

## 5. İstemci sözleşmesi ve eski yönetim çağrı noktaları

| Uç | `dosya:satır` | Sözleşme (API.md) |
|---|---|---|
| `GET /api/streams/{item_id}?episode=&profile=&kind=video\|trailer` | routers/streams.py:75 | `{streams, subtitles, audio, resume_position, duration}` + isteğe bağlı `finder: {"state": "searching"\|"not_found"}` (streams.py:132; yalnız akış yokken; API.md:327-331) |
| `POST /api/playback-report?profile=` | routers/streams.py:149; gövde `PlaybackReport` :137 | `attempt_token`(32), `event` success\|failure, `code` ∈ {"", network, timeout, playback_failed, unsupported, decode, autoplay, aborted, offline}, `engine` ∈ {"", avplay, html5, embed}, `detail`<=2000, `hlsjs`. Yanıt `{"ok":true[, "duplicate":true][, "finder":{...}]}`; geçersiz/süresi dolmuş belirteç 400 (API.md:339-343) |
| `GET /api/source-finder/{item_id}?episode=` | routers/notifications.py:39 | `{state: idle\|searching\|found\|not_found, steps[<=6]{name,ok,ms,note}, updated_at}` (API.md:345-349) |
| `GET /api/notifications?profile=&since=` | routers/notifications.py:24 | `{items:[{id, kind, canonical_id, episode_id, title, season, episode, site, method, created_at}], last_id}`; okunmamış, en çok 50 (API.md:351-357) |
| `POST /api/notifications/read?profile=` `{"upto": n}` | routers/notifications.py:34 | `{"ok":true,"marked":n}` |

Bildirim türü bugün yalnız `source_found` (`sourcefinder.KIND_FOUND` sourcefinder.py:53); yazan tek yer `sourcefinder._notify` (:226), okuyan
`sourcefinder.notifications` (:741) / `mark_read` (:760). Yani **notifications tablosunun tüm okuma/yazma kodu kaldırılacak modülün içinde**:
`problems` bildirimi ("düzeltildi") için bu üç fonksiyon çekirdeğe taşınmalı; yeni `kind` eklemek sözleşmece serbest (alan ekleme).
`finder.state` sözleşmede `searching|not_found` olarak kalmalı; plandaki "açık ve son tarihli sorun varken aranıyor" bu alana eşlenir.

Yeniden yazımda değişecek çağrı noktaları (tam liste, yorum satırları hariç grep):

| Çağıran | Çağrılan | İş |
|---|---|---|
| library/videos.py:608-609 (`_finder_state`) | `sourcefinder.request(..., trigger='play')` | Akış yok -> arama |
| library/videos.py:730-734 (`_finder_for_failure`) | `playissues.HEAL_CLASSES`, `sourcefinder.request(..., trigger='playback_failed')` | Oynatma hatası -> arama |
| library/videos.py:714-717 (`feedback`) | `playissues.clear_for_token`, `playissues.record_failure` | Sorun defteri |
| library/videos.py:340-343 (`_playheal`) | `playheal.record`, `playheal.maybe_trigger` | Her çözümleme sonucu |
| library/streamdiag.py:375-383 (`_heal_signal`) | `playheal.clear_stream_blocked`, `record_stream_blocked`, `maybe_trigger` | Reddedilen akış |
| library/streamdiag.py:391-392 (`_issue_signal`) | `playissues.note_diag` | Tanı defteri |
| library/ingest.py:618-619 (`coverage_warnings`) | `playheal.coverage_signal` | Kapsama uyarısı |
| library/ingest.py:1041-1045 (`ingest_source`) | `playheal.record_coverage`, `record_crawl`, `maybe_trigger` | Tarama sonu onarım |
| library/ingest.py:1035-1036 | `scraper.site_handoff.record_scan` | Handoff notu (yönetim katmanı, doğrulanmadı) |
| library/ingest.py:634 | `scraper.runner.run_site(site, persist=True)` | Drift + LLM self-heal ingest'in içinde |
| library/playissues.py:361 | `hostrules.find` | (çekirdeğe bağımlı, ters yön değil) |
| scraper/state.py:272-273 (`record_ops_heal`) | `sourcefinder.rerun_for_site` | Onarım sonrası yeniden arama |
| routers/notifications.py:27, 36, 53 | `sourcefinder.notifications`, `mark_read`, `status` | İstemci uçları |
| routers/ops.py:117, 124, 193, 338, 343, 428, 440 | `playheal.summary/run_reserved/begin_manual`, `playissues.summary/events`, `sourcefinder.events` | Admin |
| routers/ops_sites.py:81-83 | `sourcefinder._lock`, `_inflight` (özel alanlara doğrudan erişim) | Admin |
| routers/ops.py:60, 68; ops_settings.py:40, 49; ops_sites.py:130, 192 | `autoscan.site_status/next_scan_iso/TICK_SECONDS` | Admin |
| cache.py:239-241, 272, 291 | `autoscan.tick`, `TICK_SECONDS` | Zamanlayıcı |
| main.py:57-58 | `scraper.onboard.recover_stale()` | Açılış kurtarma |
| routers/ops_onboard.py:42-103 | `onboard`, `onboard_pipeline`, `onboard_store` | Admin |

`routers/streams.py` bu modülleri DOĞRUDAN çağırmaz; hepsi `videos.streams` / `videos.feedback` içinden gelir. Yani plandaki "kalır" modülleri
(`videos`, `streamdiag`, `ingest`) eski yönetim katmanını geç import ile çağırıyor: kesim için 4 kanca (videos `_finder_state`,
`_finder_for_failure`+playissues, `_playheal`; streamdiag `_heal_signal`/`_issue_signal`; ingest 1035-1047) olay kancasına çevrilmeli.

## 6. Önbellek, otomatik tarama, açılış

| Konu | `dosya:satır` | Davranış |
|---|---|---|
| `cache.refresh() -> Snapshot` | cache.py:166 | Tek okuma görünümüyle yeniden kurar; bilet numarası sayesinde eski kurulum yenisini ezmez; her iş parçacığından güvenli |
| `cache.refresh_and_prewarm()` | cache.py:187 | refresh + `images.start_prewarm` |
| `cache.refresh_progress(stage="")` | cache.py:199 | Ingest kancası: `titles` her zaman, diğerleri `INGEST_REFRESH_MIN_INTERVAL`=20 sn debounce; eşzamanlı çağrı None döner |
| `cache.get()` | cache.py:229 | Yayınlanmış anlık görüntü |
| `Snapshot` | cache.py:38 | `items, by_id, episodes, flat_eps, seasons, by_genre, newest, search_index`; alias'lar `by_id`'ye eklenir |

Yenileme tetikleri: APScheduler `catalog_refresh` her `CACHE_TTL`=900 sn (cache.py:276); `ingest_tick` tarama yaptıysa (cache.py:245); ingest
aşama kancası (cache.py:255); detay hydrate/sezon/fragman (routers/detail.py:41, 70, 110, 186); canlı arama (search_all.py:96, 186); admin
tarama/kaynak eylemi/kimlik bağlama (routers/ops.py:358, 499, 525); TMDB admin (tmdb_admin.py:171); site silme (purge.py:285). `python -m tools.ingest`
kancasızdır: yalnız DB'ye yazar.

`autoscan.tick(now=None, *, ingest=None, sleep=time.sleep, gap=None) -> list[str]` (autoscan.py:128): scheduler işi `library_ingest` her
`TICK_SECONDS`=60 sn (cache.py:288, `max_instances=3`); `_tick_lock` ile tek tick; `due_sites` (autoscan.py:95) = `settings.all_sites()` içinde
enabled, taramıyor ve `last_run + interval_hours` geçmiş olanlar; siteler sırayla, arada `SITE_GAP_SECONDS`=5; her site için
`ingest_source(site, trigger="scheduled")`. Son koşu zamanı DB'den değil `scraper.state` ops geçmişinden (`_ops.json`) okunur (autoscan.py:42).

`main.py` `lifespan` (main.py:48): `db.init()` -> `videos.backfill()` -> `cache.start()` (refresh_and_prewarm + kanca bağlama + scheduler)
-> `onboard.recover_stale()` (hata yutulur) -> yield -> `cache.stop()` (kanca çöz, `scheduler.shutdown(wait=False)`). Kapanışta başka hiçbir
şey beklenmez/temizlenmez: aşağıdaki daemon thread'ler yarıda kesilir.

| Thread / havuz | Nerede |
|---|---|
| APScheduler `BackgroundScheduler(daemon=True)` | cache.py:274 |
| `img-prewarm-main` + `img-prewarm` işçileri | images.py:596, 564 |
| `resolve` / `resolve-src` havuzları, `resolve-refresh`, `resolve-prefetch` | videos.py:267, 599, 490 |
| `streamdiag` havuzu, `streamdiag-quick` | streamdiag.py:492, 351 |
| `title-enrich` | enrich.py:346 |
| `detail-hydrate` (ad doğrulanmadı), `detail-seasons` | routers/detail.py:55, 90 |
| `trailer-check` havuzu, `search` havuzu | trailer_check.py:178, search_all.py:172 |
| yönetim: `finder-<id>`, `playheal-<site>`, `onboard-<draft>`, `onboard-scan-<site>`, `onboard` havuzu | sourcefinder.py:81, scraper/playheal.py:634, scraper/onboard.py:701, 1163, routers/onboard_sandbox.py:273 |

Yarıda kalan iş kurtarması bugün yalnız onboarding taslağı için var (`recover_stale`); finder işi restart sonrası `idle` görünür
(sourcefinder.py:736), taramanın kurtarması yok.

## 7. `/img` proxy: kırık afiş kancası

| Nokta | `dosya:satır` | Durum |
|---|---|---|
| `artwork(item_id, kind, request, w, h)` | routers/images.py:61 | 404 yalnız bilinmeyen `kind` (:70) ya da bilinmeyen id (:97). **Uzak afiş kırıksa 404 DÖNMEZ**: sessizce Pillow yer tutucusu 200 (:109-113) |
| Sessiz düşüş | routers/images.py:105-107 | `remote_url` var ama `images.remote_jpeg_bytes` None -> yer tutucu. **Kanca burası** (item_id, kind, remote_url elde) |
| Host izinli değil | routers/images.py:101; `images.remote_host_allowed` images.py:361 | `REMOTE_IMG_HOSTS` dışı host -> yer tutucu (ayrı sinyal: "afiş host'u izin listesinde yok") |
| İndirme hatası | `images._load_original(url)` images.py:431; `_http_fetch` :418 | HTTP != 200, image olmayan content-type ya da istisna -> `_neg_add(url)` (:463, :468); HTTP kodu çağırana DÖNMEZ (None) |
| Negatif önbellek | `_neg_hit` / `_neg_add` images.py:398 / 409 | Yalnız bellekte, `IMG_NEG_TTL`=60 sn; kalıcı kırık afiş kaydı yok |
| Arka plan ısıtma | `images.prewarm(items)` images.py:537 | Aynı `remote_jpeg_bytes` yolunu kullanır (sayaç döner; ayrıntı doğrulanmadı) |
| `remote_url` hiç yok | sources/library.py:77-78 | `tmdb_poster_url` yoksa ve site afişi yer tutucuysa None: "afişsiz başlık" sinyali `/img`'e gerek kalmadan DB'den sayılabilir |

Not: kanca `_load_original` içine konursa hem istek hem prewarm yakalanır ama item kimliği yoktur (yalnız URL); router'a konursa kimlik vardır
ama prewarm kaçar. Negatif önbellek yüzünden aynı URL 60 sn'de en çok bir kez yeniden denenir.

## 8. Testler (`server/tests/`)

79 `test_*.py`; hepsi `import _sandbox` içerir. `tests/_sandbox.py` (:39-70): geçici `DATA_DIR`/`DB_PATH`/`IMG_CACHE_DIR`, boş TMDB anahtarları,
`TRAILER_CHECK=0`, `SOURCEFINDER_ENABLED=0`, `STREAM_DIAG=0`, `STREAM_PROBE_RANK=0`, `ONBOARD_HARDEN=0`; çıkışta gerçek `data/` parmak izi kıyası
(`SANDBOX VIOLATION`, kod 3). `app.config` daha önce import edildiyse `RuntimeError`. Yardımcılar: `_contract_schema.py`, `_contract_seed.py`,
`_fake_pi.py`, `fixtures/`. **`server/tests_archive/` dizini YOK** (CLAUDE.md'deki smoke set ayrımı henüz yapılmamış). Sınıflama dosya adına
göredir; içerikler tek tek okunmadı (doğrulanmadı).

| Katman | Dosyalar (konu) |
|---|---|
| **Çekirdek: katalog/ingest** | test_catalog (katalog ucu), test_multisource (çok kaynak birleştirme), test_series_identity (dizi kimliği), test_normalize_generic (yaml normalize), test_series_crawl (bölüm envanteri), test_series_generic (genel series_page), test_gate (telif kapısı), test_blocked (blocked kuralları), test_placeholder_art (yer tutucu afiş), test_tmdb_enrich, test_tmdb_seasons, test_coverage (kapsama ölçümü; playheal sinyaline dokunur), test_collection_post (POST koleksiyon), test_category_collections (kategori rolü), test_yabancidizi (site modülü), test_isolation (sandbox koruması), test_reset_keep_categories |
| **Çekirdek: akış** | test_canonical_playback (kaynak+sağlık), test_playback_counter (hata sayımı), test_resolve_cache (valid_until önbelleği), test_resolve_timeouts, test_video_providers, test_provider_recipes, test_provider_registry, test_provider_match, test_resolvers, test_resolver_dispatch, test_resolver_types, test_resolver_player, test_resolver_embedded_json, test_okru_flow, test_stream_diag (tanı), test_streamprobe, test_stream_proxy, test_stream_hls_proxy, test_subtitles, test_trailer_check |
| **Çekirdek: ana ekran / API** | test_homelayout, test_home_skeleton, test_rows_roles, test_categories, test_client_contract (sözleşme koruması), test_continue_hidden, test_detail_hydrating, test_detail_source_names, test_progressive_refresh (tarama ortası yenileme), test_live_search, test_search_all, test_search_generic, test_perf |
| **Çekirdek: scraper motoru** | test_fetch_impersonated, test_crawlee_integration, test_obscura_worker, test_regex_lint |
| **Yönetim (yeniden yazılacak)** | test_onboard, test_onboard_ask, test_onboard_blocked, test_onboard_harden, test_onboard_ingest_sample, test_onboard_pipeline, test_onboard_ready_return, test_onboard_refs, test_onboard_sandbox, test_discover_site, test_sitequality, test_site_handoff, test_pi_size_budget, test_heal, test_heal_agent, test_playheal, test_playheal_crawl, test_playback_heal_signals, test_stream_blocked_heal, test_sourcefinder |
| **Admin uçları (yönetim; içinde çekirdek uç da var)** | test_ops, test_ops_library, test_ops_settings (autoscan durumu), test_ops_sites, test_ops_tmdb |

Autoscan'e özel test dosyası yok (`test_ops_settings` / `test_ops` içinde olabilir, doğrulanmadı). İstemci tarafında finder/bildirim sözleşmesini
kullanan Node testleri (proje kökü `tests/`): `finder_flow.js`, `notifications.js`, `no_source_state.js`, `player_health.js`, `toast_rich.js`.
