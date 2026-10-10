# server2 mimarisi

Kaynak sözleşme: `../../docs/plans/server2/README.md` (§4 ilişkiler ağı, §5 ortak sözleşmeler). Bu belge kodun bugünkü haritasıdır.

## 1. Katmanlar

```
 admin (/admin + /api/control/*) ─┐      api (istemci uçları, ../API.md)
                                  ▼                 ▼
 control: jobs  sitemap  stages  crawl  routing  problems  notes  wiring      (durumlu; DB'de)
                                  ▼
 core:    fetch  extract  play  catalog  llm  events  yamlsafe                (durumsuz kütüphane)
```
Kural: `core` hiçbir üst katmanı import etmez (`tools/check_imports.py`). `core` -> `control` haberi `core.events.emit`;
`control/wiring.py` açılışta iş türlerini ve core kancalarını (`catalog.hooks.bind_tmdb`, `play.hooks.bind_ctx`) bağlar.

## 2. Modül haritası

| Modül | Ne yapar |
|---|---|
| `app/main.py` | FastAPI; `ROUTERS` listesi, `startup_hooks` (`wiring.register`, `jobs.start`), `shutdown_hooks`; hata zarfı; `/api/control/*` için köken koruması |
| `app/config.py` | `.env` (yalnız README §6 değişkenleri); `DATA_DIR`, `DB_PATH`, sırlar |
| `app/settings.py` | `settings` tablosu: `get/set/all`, `DEFAULTS` (admin Ayarlar ekranı yönetir) |
| `app/db.py` + `app/migrations/` | iş parçacığı başına bağlantı, WAL, `transaction()`; sürümlü migrasyon (`schema_migrations`) |
| `core/fetch` | `get(url, site=SiteCtx)`: netguard (SSRF), robots, host başına sınır/gecikme, TLS taklitli http, sayfa önbelleği, oynatma yardımcıları (`playback.py`) |
| `core/extract` | HTML ayrıştırma, alan kuralı (`parse.apply_field`), kart/bölüm/oynatıcı/arama keşfi, url kalıbı, sayfa imzası |
| `core/play` | resolver kataloğu (`resolvers/`), provider kütüphanesi (`providers/`, tarifler `seed/providers` + `data/providers`), kaynak çözme (`sources.py`), kapı/engel, akış sağlığı, akış/HLS vekili |
| `core/catalog` | katalog yazma (`write.upsert_*`), kimlik birleştirme, TMDB, listeler, afiş vekili, RAM önbelleği (`cache.py`) |
| `core/llm` | tek atımlık `pi -p` çağırıcı (`client.complete`), sağlık (`health.check`), bütçe (`budget`), çağrı günlüğü |
| `control/jobs` | kalıcı iş kuyruğu: `enqueue/register/wake/cancel/drain`, kira, checkpoint, periyodik işler (`schedule.py`) |
| `control/sitemap` | site haritası (yaml) şeması, sürümler (`site_versions`), taslak, `adapters` (harita -> core parametreleri) |
| `control/stages` | aşama motoru (iş türü `stage`), A1-A7, LLM önerisi + ölçüm, site özeti (`brief.py`), etkinleştirme |
| `control/crawl` | frontier, K0/K1/K2 kademeleri, tazeleme, imza kayması (`structure_changed`) |
| `control/routing` | kaynak grubu -> ana ekran satırı/kategori kuralları, LLM önerisi (`routing_suggest`) |
| `control/problems` | sorun kaydı: alım (olaylar), teşhis, eylem, ölçüm, K9 onay, istemci sözü (`finder`), bildirim; onarım (T14) |
| `control/notes.py` | admin bilgi notları (zil) |
| `api/` | istemci sözleşmesi (`../API.md`): boot, home/rows, detail, streams, progress, mylist, search, images, stream-proxy |
| `admin/` | `routes_*.py` (`/api/control/*`) + `static/` (vanilla js; `app.js` kabuk, ekran başına bir dosya) |

Admin ekranları: Siteler (`sites.js`), Site sayfası (`site.js`; sekmeler Harita, Tarama `crawl.js`, Yönlendirme `routing.js`),
Katalog (`catalog.js`, `catalog_player.js`), Sorunlar (`problems.js`), Ayarlar (`settings.js`), zil (`notes.js`).

## 3. Veri akışı

```
 adres ─▶ POST /api/control/sites ─▶ sites(draft) ─▶ stage işleri A1..A7
           discover (LLM'siz) ─▶ verify (ölçüm) ─▶ geçmezse prompt ─▶ LLM ─▶ verify (en çok 2 tur) ─▶ needs_human
           passed* ─▶ patch draft_yaml'a ─▶ A1+A3 geçti: site active, site_versions v1
 site haritası ─▶ crawl_frontier (K0 öne çıkanlar, K1 listeler/backfill_days, K2 kalan) ─▶ sayfa ─▶ kayıt
           ─▶ core.catalog.upsert_title/upsert_episodes (+ source_origins) ─▶ tmdb işi ─▶ cache.refresh_soon
 source_origins ─▶ routing_rules ─▶ library_lists ─▶ ana ekran (homelayout)
 istemci /api/streams ─▶ core.play.sources ─▶ akış yoksa emit(stream_missing) ─▶ problems
```

**Kaynak alternatifleri (T21).** Bir oynatıcı sayfasındaki TÜM kaynaklar yedek olarak tutulur: `core.extract.player.player_candidates`
LLM'siz ilk katman (iframe/embed/video, data-* sekme/`option` düz ya da base64/hex, AJAX düğmesi + sayfadaki uç `ajax`,
betikte atob/hex/packed/`file:` -> `script_regex`, `data_attr`); aynı-site ara oynatıcı sayfaları en çok 4 adım izlenir
(`resolve.site_hops`). A5 her alternatife bir resolver yazar; çözülemeyen varsa LLM turunda `actions:` ile sunucuya inceleme
yaptırır (`core.play.inspect` + `control/stages/investigate.py`, adım başına 6 eylem, tur başına 4 adım, ağ yalnız `core.fetch`).
A6 her alternatifi her örnekte ölçer, sırayı + durumu (`çalışıyor|kısmen|çalışmıyor`) `pages.sources.alternatives`'e yazar;
tarama bölüm başına alternatif sayısı kadar `video_sources` satırı açar (`alt_key/alt_index/alt_label/alt_host`, 019), sağlık
alternatif başınadır, `/api/streams` hepsini sırayla döner (çözülemeyen atlanır).

```
 istemci playback-report ─▶ emit(playback_failed|ok) ─▶ problems (teşhis/eylem/ölçüm, K9 onay)
 crawl imza bozuldu ─▶ emit(structure_changed) ─▶ problems/onarım ─▶ yeni site_versions (created_by=repair) + admin notu
```

## 4. Durum makineleri

**İş (`jobs.state`)**
```
 queued ──kira──▶ running ──▶ done
   ▲                │ ├──▶ failed (Fail, deneme sınırı, timeout, cancelled)
   │                │ └──▶ waiting (Wait: dış olay bekler; wake(key) ─▶ queued)
   └── Retry(after_s) / kira süresi doldu (restart) / requeue (admin "yeniden dene")
```
Kira 120 sn, 30 sn'de bir yenilenir; `llm=True` türler sistemde aynı anda 1. `drain` açıkken yeni kira yok (kalıcı bayrak).

**Aşama (`site_stages.state`)**
```
 pending ─▶ running ─▶ passed | passed_warn
                    ├─▶ needs_human ──(ipucu / örnek / alanı atla / rerun)──▶ running
                    ├─▶ engine_gap (motor boşluğu, capability_gaps)
                    └─▶ skipped (isteğe bağlı aşama ya da kullanıcı)
```

**Sorun (`problems.state`)**
```
 open ─▶ diagnosing ─▶ acting ─▶ verifying ─▶ resolved ─▶ confirmed (K9: 10 sn hatasız oynatma)
   ▲         │           │           │            │
   │         └── waiting_llm / waiting_user / waiting_capability (dış girdi gelince devam)
   └──── resolved sonrası yeni hata (attempts+1) / ölçüm geçmedi
 kapanış: closed_not_public (halka kapalı), exhausted (denemeler bitti)
```
İstemci eşlemesi: açık + süresi geçmemiş -> `searching`; `resolved|confirmed` -> `found`; diğerleri `not_found`.

## 5. Tablolar

| Grup | Tablolar |
|---|---|
| Çekirdek | `schema_migrations`, `settings` |
| Getirme | `sample_pages` |
| Katalog | `profiles, progress, mylist, continue_hidden, library_items, source_items, field_provenance, library_lists, home_categories, catalogue_aliases, external_ids, identity_reviews, video_sources, playback_attempts, trailer_checks, library_seasons, library_episodes, stream_host_rules, stream_host_seen, notifications, blocked_pages, source_origins, catalog_unlisted` |
| İş kuyruğu / not | `jobs`, `admin_notes` |
| LLM | `llm_calls` |
| Site haritası / aşama | `sites`, `site_versions`, `site_stages`, `stage_messages` |
| Tarama | `crawl_frontier`, `crawl_origins`, `crawl_sites` |
| Yönlendirme | `routing_rules`, `routing_suggestions`, `routing_lists`, `routing_state` |
| Sorunlar | `problems`, `problem_events`, `problem_watchers`, `capability_gaps` |

Zaman alanları epoch saniye, JSON alanları TEXT. Dosya verisi `DATA_DIR` altında: `diziflix2.db`, `imgcache/`, `pages/`
(örnek sayfalar), `providers/` (öğrenilen provider tarifleri), `llm/` (oturum ve son çağrılar).

## 6. İşletim

- systemd `diziflix2` (`install.sh`): `uvicorn app.main:app --host $HOST --port $PORT`, `--reload` yok, `Restart=on-failure`,
  `KillSignal=SIGTERM`, `TimeoutStopSec=30` (kapanışta yürütücü adımı bitirir, kalan işleri sıraya geri verir).
- `deploy.sh`: check -> kod yedeği (son 5) -> fark -> `rsync --delete` (`.env`, `data/`, `venv/` hariç) -> `import app.main` -> drain
  (en çok 120 sn; LLM işi sürüyorsa `--force-restart` olmadan dur + drain kapat) -> restart -> uç doğrulama -> drain kapat.
- Ayarlar ekranı (`/api/control/settings`): `settings.DEFAULTS` anahtarları, tip/aralık doğrulaması; sırlar yalnız "tanımlı/tanımsız".
