# server2: görev seti, ortak sözleşmeler, ilişkiler ağı

Tarih: 2026-10-03. Bu klasör, diziflix'in sıfırdan yazılacak yeni sunucusunun (`diziflix/server2/`) görev tanımlarıdır.
Kodu yazan: Opus 5.5. Denetleyen: orkestratör (bu belgeyi yazan). Her görev tek dosyadır (`T00`..`T15`); bu README hepsinin ortak
sözleşmesidir ve **her görevden önce okunur**. Görev dosyası ile README çelişirse README geçerlidir; ikisi de kodla çelişirse dur ve raporla.

Girdi belgeleri (okuma sırası): bu README -> ilgili `Txx` -> görevin "Oku" listesi. Arka plan:
`docs/arch-review/NEW-SYSTEM-PLAN.md` (tasarım), `docs/arch-review/inventory/0{1,2,3}-*.md` (eski kodun `dosya:satır` envanteri),
`API.md` (istemci sözleşmesi), `docs/api-samples/*.json`.

## 1. Kullanıcı kararları (değişmez)

| # | Karar |
|---|---|
| K1 | Yeni sistem `diziflix/server2/` klasöründe, sıfırdan; kendi veritabanı, port **8091**. Eski `server/` (8090) yerinde durur, DOKUNULMAZ |
| K2 | Mevcut siteler taşınmaz. Sistem boş katalogla başlar; siteleri kullanıcı kendisi ekleyip dener |
| K3 | **Siteye özel kod yok.** `server2/app` altında hiçbir site adı geçmez. Genel motorun yapamadığı şey "motor boşluğu"dur, genel yetenekle kapanır |
| K4 | Modüler ayrım: **provider kütüphanesi** = bir video host'unun akışını çözmek (kalite/çözünürlük varyantları, altyazı, ses izi, başlık/Referer gereksinimi, hız ölçümü); ayrı kütüphanedir, hiçbir siteyi bilmez. **Sitenin o provider'ı nasıl çağırdığı** (oynatıcı sayfada nerede, hangi öznitelik/istek/el sıkışmayla provider adresine varılır) siteye özeldir ve site haritasında (`pages.player.resolvers`) yaşar |
| K13 | Admin panelinde Katalog ekranı: bulunan medya, sezon/bölüm ayrıntısı, video kaynakları ve **panel içi oynatma**; kullanıcı istemciyi açmadan doğrulayabilmeli |
| K5 | Yalnız halka açık içerik: oynatıcısı olmayan / engelli başlık-bölüm istemciye görünmez |
| K6 | Tarama LLM'siz. LLM yalnız aşama önerisi, yönlendirme kuralı çevirisi ve onarımda; her çağrı kısa ve araçsız, dosya okumaz (skill/bağlam dosyası yok; kanıtı sunucu prompt'a koyar). **Oturum kapsamı = tek aşama koşusu** (aynı aşamanın art arda turları aynı oturumu sürdürür, koşu bitince oturum silinir); siteler ve aşamalar arası hafıza oturumda değil, sunucunun tuttuğu **site özeti**ndedir (her prompt'a girer). **Öneri (T23): aşama önce LLM okur; ölçüm hakemdir**: A1-A5 koşusu LLM'in sayfa haritasını (`core/extract/pagemap`) amaçla okumasıyla başlar (1 çağrı + biçim düzeltmesi, site günlük bütçesinden); LLM kuralları ve LLM'siz keşif aynı ölçümle yarışır; bütçe yok/LLM yoksa keşif tek başına. Tarama yine LLM'siz |
| K7 | Tam aktarım varsayılanı **3 gün** (site başına değiştirilebilir) |
| K8 | LLM düzeltmesi ölçüm doğrulamasını geçerse **kendiliğinden uygulanır + admin'e bilgi notu** düşer; geçemezse admin'e sorulur |
| K9 | Kullanıcı doğrulaması = çözümden sonra aynı içerikte **10 sn hatasız oynatma** (örtük; istemci değişikliği yok) |
| K10 | İstemcilere (tizen-client, Android) bu turda DOKUNULMAZ. `server2` `API.md` sözleşmesini aynen sunar (yalnız alan eklenebilir) |
| K11 | Test stratejisi: küçük smoke set (tüm `server2/tests` < 20 sn, ağsız, gerçek LLM'siz) + `./check.sh` |
| K12 | Görevler siteden bağımsızdır: hiçbir kabul ölçütü belirli bir gerçek siteye bağlanmaz; testler uydurma HTML ile |

## 2. Herkes için kurallar

1. **Yazma alanı:** yalnız `diziflix/server2/**` ve `diziflix/docs/plans/server2/reports/Txx.md`. `server/`, `tizen-client/`, `deploy.sh`,
   `API.md`, `docs/arch*` değiştirilmez. Eski kod **kopyalanır**, import edilmez (`server2` içinde `server/` yolu ya da `sys.path` hilesi yok).
2. **Kopya kaydı:** eski koddan alınan her dosya/fonksiyon `server2/COPIED.md`'ye yazılır: kaynak `dosya:satır` -> hedef, yapılan değişiklik (tek satır).
3. **Katman kuralı (ilişkiler ağı §4):** `core` -> hiçbir şey; `control` -> `core`; `api`/`admin` -> `core` + `control`. `core`, `control`'ü
   **import etmez**; haber vermesi gerekiyorsa `core.events.emit(...)` kullanır. `tools/check_imports.py` bunu denetler.
4. **Durum diskte:** iş, aşama, sorun, bütçe, söz durumu DB'dedir. Bellekte yalnız önbellek ve kilit olabilir; restart hiçbir işi/sözü kaybetmez.
5. **Yeni ortam değişkeni yok** (§6'daki liste dışında). Çalışma zamanı ayarları `settings` tablosunda, admin'den yönetilir.
6. **Güvenlik:** kullanıcıdan/LLM'den gelen her adres `core.fetch` içinde `netguard` denetiminden geçer (çağırana bırakılmaz). Sırlar
   (`TMDB_ACCESS_KEY`, pi kimlikleri) loglanmaz, yanıtta dönmez, LLM prompt'una girmez.
7. **Yapma:** deploy, sunucuya (192.168.0.61) bağlanma, `git commit/push`, uygulamayı açıp görsel test, testte gerçek ağ/LLM çağrısı,
   eski `server/data` ya da `server/.env` okuma.
8. **Dil:** kod, tanımlayıcı ve kod yorumu İngilizce (eski kodla aynı); admin arayüz metni ve raporlar Türkçe.
9. **Test:** her test dosyası `import _sandbox` ile başlar (geçici `DATA_DIR`/`DB_PATH`, boş TMDB anahtarı, sahte LLM çağırıcı, ağ kapalı).
   Görev başına en çok ~10-15 hedefli test; davranışı sabitleyen testler, kapsama oranı için değil.
10. **Tıkanırsan:** sözleşmede boşluk/çelişki görürsen makul varsayımla ilerle, varsayımı raporun "Sapmalar" bölümüne yaz. Sözleşmeyi
    (bu README'yi) sessizce değiştirme; değişiklik öner.

## 3. Klasör yapısı (hedef)

```
server2/
  app/
    main.py  config.py  db.py  errors.py
    core/                    # LLM'siz, durumsuz kütüphane (çoğu eski koddan kopya)
      events.py              # emit/subscribe (core -> control tek yönlü haber)
      fetch/                 # T01: fetcher, netguard, robots, obscura işçisi, sayfa önbelleği
      extract/               # T02: parse, outline (blok/bağlantı kümeleme), discover yardımcıları, url kalıbı, imza
      play/                  # T03: resolvers/, providers/, recipes, gate/blocked, follow, probe, diag, hostrules, proxy
      catalog/               # T04a: tablolar, normalize yardımcıları, identity, upsert, tmdb, lists, images, cache
      llm/                   # T06: tek atımlık çağırıcı, sağlık, çağrı günlüğü
    control/                 # durumlu yönetim katmanı (tamamı yeni)
      jobs/                  # T05
      sitemap/               # T07
      stages/                # T08, T09
      crawl/                 # T11
      routing/               # T12
      problems/              # T13, T14
      notes.py               # admin bilgi notları
    api/                     # T04b: istemci uçları (API.md)
    admin/                   # T10+: /admin + /api/control/*; static/
  seed/providers/*.yaml      # provider tarifi tohumları (git'te)
  tests/  tools/  check.sh  install.sh  requirements.txt  .env.example  CLAUDE.md  COPIED.md
  data/                      # gitignore: diziflix2.db, imgcache/, pages/, providers/
```

## 4. İlişkiler ağı

### 4.1 Modül bağımlılıkları (ok = "kullanır")

```
 admin (static + /api/control/*) ──┬──────────────────────────────┐
 api (istemci, API.md) ────────────┤                              │
                                   ▼                              ▼
 control.problems ──▶ control.stages ──▶ control.sitemap     control.routing
        │                  │    │              │                  │
        │                  │    └──▶ core.llm  │                  │
        ├──▶ control.crawl ┘                   │                  │
        │        │                             ▼                  ▼
        ▼        ▼                        core.extract       core.catalog ◀── core.play (kaynak satırları)
   control.jobs (herkes iş kuyruğunu kullanır)   │                ▲
        │                                        ▼                │
        └────────────────────────────────▶  core.fetch ───────────┘
 core.events:  core.play / core.catalog / api ──emit──▶ control.problems (subscribe)
```

### 4.2 Veri akışı

```
 adres ─▶ [A1..A7 aşamaları] ─▶ site haritası (site_versions) ─▶ crawl_frontier ─▶ sayfa ─▶ kayıt ─▶ catalog.upsert
                │  ▲                      │ imza                      │ K0/K1/K2                │ source_origins
       keşif ─ öneri(LLM) ─ ölçüm         ▼                           ▼                         ▼
                │                    imza bozuldu ───────────▶ problems ◀── istemci olayı   routing_rules ─▶ library_lists
                ▼                                                 │                                          │
        aşama kartı (admin) ◀── ipucu / "yok, atla"               ▼                                          ▼
                                              teşhis ─▶ eylem ─▶ ölçüm ─▶ resolved ─▶ confirmed (10 sn)   ana ekran (API)
                                                          └─ aşama onarımı (LLM) ─▶ yeni sürüm + admin notu
```

### 4.3 Görev bağımlılıkları

```
 T00 iskelet
  ├─▶ T01 fetch ──────────┬─▶ T03 play ───────┬─▶ T04b istemci API ─┐
  ├─▶ T02 extract ─┬──────┤                   │                     ├─▶ T13 sorun çekirdeği ─┐
  │                └─▶ T07 site haritası ─┐   │                     │                        │
  ├─▶ T04a katalog yazma ─────────────────┼───┘                     │                        ├─▶ T14 onarım + Sorunlar
  ├─▶ T05 iş kuyruğu ─────────────────────┼─▶ T08 aşama motoru+A1-A3+K0 ─▶ T09 A4-A7 ─┬─▶ T11 tarama ─┘
  └─▶ T06 LLM çağırıcı ───────────────────┘        │                                  └─▶ T12 yönlendirme
                                                   └─▶ T10 admin kabuğu + Site sayfası ─▶ (T11, T12, T14 sekmeleri) ─▶ T15 ops
 T03 + T04a + T04b + T10 ─▶ T16 admin Katalog + panel içi oynatma
```

| Dalga | Görevler (aynı dalgadakiler paralel yazılabilir) | Sonunda çalışan şey |
|---|---|---|
| 1 | T00 | Boş sunucu 8091'de ayağa kalkar, `check.sh` yeşil |
| 2 | T01, T02, T04a, T05, T06 | Kütüphaneler + kuyruk (uçtan uca henüz yok) |
| 3 | T03, T07 | Oynatma kütüphanesi, harita modeli |
| 4 | T04b, T08, T10 | Site eklenir, A1-A3 geçer, öne çıkanlar katalogda; admin'de aşama kartları |
| 5 | T09, T13, T16 | Bölüm oynar; admin Katalog'dan panel içinde izlenir; istemci hatası sorun kaydı açar |
| 6 | T11, T12 | Planlı tam aktarım + tazeleme; yönlendirme kuralları |
| 7 | T14, T15 | LLM onarımı, tek sorun kutusu, deploy betiği |

Paralel yazımda aynı dosyaya iki görev dokunmaz: ortak dosyalar (`db.py` migrasyon listesi, `main.py` router kaydı, admin `index.html`) için
her görev **kendi dosyasını ekler** (`app/migrations/NNN_<görev>.py`, `app/admin/routes_<konu>.py`, `static/js/<ekran>.js`) ve ortak dosyaya
yalnız tek satırlık kayıt ekler.

## 5. Ortak sözleşmeler

Aşağıdaki adlar ve biçimler görevler arası arayüzdür; değiştirmek isteyen görev bunu raporunda önerir.

### 5.1 Veritabanı

`db.py`: iş parçacığı başına bağlantı, WAL, `query/query_one/execute/transaction()`; **sürümlü migrasyon**:
`schema_migrations(version, applied_at)`, `app/migrations/NNN_ad.py` (`VERSION`, `up(conn)`), açılışta sırayla uygulanır.
Çok ifadeli yazma her zaman `with db.transaction():` (`BEGIN IMMEDIATE`).

Katalog tabloları (T04a; eski şemadan, `inventory/02 §1.1`): `profiles, progress, mylist, continue_hidden, library_items, source_items,
field_provenance, library_lists, home_categories, catalogue_aliases, external_ids, identity_reviews, video_sources, playback_attempts,
trailer_checks, library_seasons, library_episodes, stream_host_rules, notifications, blocked_pages`. **Alınmayanlar:** `finder_jobs`,
`playback_issues`, `source_items.resolved_*` eski kolonları.

Yönetim tabloları (zaman alanları epoch saniye, JSON alanları TEXT):

```sql
settings(key TEXT PRIMARY KEY, value TEXT)                                   -- T00
sites(site_id TEXT PRIMARY KEY, display_name, base_url, status,              -- T07  status: draft|active|paused
      active_version INTEGER, draft_yaml TEXT, backfill_days INTEGER, refresh_hour INTEGER, created_at, updated_at)
site_versions(site_id, version INTEGER, yaml TEXT, created_at, created_by,   -- T07  created_by: onboard|edit|repair|admin
              note, PRIMARY KEY(site_id, version))
site_stages(site_id, stage, state, round INTEGER, patch TEXT, measurement TEXT, evidence TEXT,
            started_at, updated_at, PRIMARY KEY(site_id, stage))             -- T08
stage_messages(id INTEGER PRIMARY KEY, site_id, stage, role, text, at)       -- T08  role: user|system|llm
sample_pages(site_id, url, page_type, status INTEGER, path, fetched_at, PRIMARY KEY(site_id, url))   -- T01
jobs(id INTEGER PRIMARY KEY, kind, key TEXT UNIQUE, site_id, state, step INTEGER, payload TEXT, result TEXT,
     attempts INTEGER, priority INTEGER, run_after INTEGER, lease_until INTEGER, lease_owner, error,
     created_at, updated_at, finished_at)                                    -- T05
crawl_frontier(site_id, url, page_type, state, priority INTEGER, parent_url, origin_kind, origin_value, position INTEGER,
               discovered_at, crawled_at, next_at, fail_count INTEGER, PRIMARY KEY(site_id, url))   -- T11
source_origins(site_id, source_key, kind, value, position INTEGER, seen_at,
               PRIMARY KEY(site_id, source_key, kind, value))                -- T04a  kind: section|listing|genre|type
routing_rules(id INTEGER PRIMARY KEY, site_id, match_kind, match_value, target_kind, target_value,
              priority INTEGER, enabled INTEGER, created_by, note, created_at)   -- T12
problems / problem_events / problem_watchers / capability_gaps               -- T13 (§5.7)
admin_notes(id INTEGER PRIMARY KEY, at, level, site_id, text, ref_kind, ref_id, read_at)   -- T05  level: info|warn|action
llm_calls(id INTEGER PRIMARY KEY, at, purpose, site_id, stage, session, model, prompt_chars, reply_chars,
          input_tokens, output_tokens, cached_tokens, ms, ok, error)                                -- T06
```

### 5.2 Olaylar (`core/events.py`)

`emit(name: str, **payload)` eşzamanlı ve hata yutan (abone hatası yayıncıyı bozmaz, loglanır); `subscribe(name, fn)`.

| Olay | Yayıncı | Yük |
|---|---|---|
| `stream_missing` | `core.play` (streams) | `canonical_id, episode_id, profile_id, entries[]` (kaynak başına: site, source_id, error, stage, candidates[]) |
| `playback_failed` | `api` (playback-report) | `source_id, canonical_id, episode_id, profile_id, code, engine, detail` |
| `playback_ok` | `api` (playback-report) | `source_id, canonical_id, episode_id, profile_id` |
| `progress` | `api` (progress) | `profile_id, canonical_id, episode_id, position, duration` |
| `stream_diag` | `core.play` (diag) | `source_id, verdict{code, http, learn, stream{host,type}}` |
| `image_broken` | `core.catalog` (images) | `canonical_id, kind, url, reason` (`http`\|`host_not_allowed`) |
| `page_crawled` | `control.crawl` | `site_id, url, page_type, ok, fingerprint_ok, counts` |

### 5.3 İş kuyruğu (`control/jobs`)

```python
enqueue(kind, key, *, site_id=None, payload=None, run_after=0, priority=0) -> int   # same key while queued/running/waiting = no-op, returns id
register(kind, handler, *, llm=False)       # handler(job, ctx) -> Done(result) | Retry(after_s, error) | Wait(reason) | Fail(error)
ctx.checkpoint(step, payload=None)          # persisted; a restarted job resumes from here
wake(key) / cancel(key) / active(site_id=None) / drain(on: bool)
```
Durumlar: `queued -> running -> done | failed | waiting`. Kira: `lease_until = now + 120`, yürütücü 30 sn'de bir yeniler; kirası dolan
`running` iş açılışta ve her turda `queued`'a döner (`attempts` korunur). `llm=True` işler: sistemde aynı anda 1, site başına 1.
`drain(True)`: yeni iş kiralanmaz, sürenler adımını bitirir. İş türleri: `stage, crawl, refresh, routing, problem, verify, tmdb, llm_health`.

### 5.4 Getirme (`core/fetch`)

```python
get(url, *, site: SiteCtx, mode="auto", wait_for="", method="GET", data=None, referer="") -> Page
Page(url, final_url, status, html, engine, ms, from_cache)      # raises FetchError(code): blocked_ssrf|robots|timeout|challenge|http_<n>|too_large
SiteCtx(site_id, base_url, mode, delay_ms=1000, concurrency=2)
```
Paralel: global 8 eşzamanlı istek; host başına `concurrency` ve istekler arası `delay_ms`. `mode`: `http` (süreç içi, TLS taklitli),
`browser` (Obscura alt süreci, havuz 2), `auto` (http; gövde inceyse/challenge ise bir kez browser'a yükselt). `429`/challenge üst üste
gelirse host için artan bekleme; çağırana `challenge` döner.

### 5.5 Site haritası (`control/sitemap`)

Tek belge (yaml), `site_versions.yaml` içinde saklanır; çalışma kopyası `sites.draft_yaml`. Şema sürümü `map: 1`.

```yaml
map: 1
site_id: example
base_url: https://example.tld
fetch: {mode: auto, delay_ms: 1000, concurrency: 2}
pages:
  home:                      # A1
    url: /
    sections:                # each = a row of cards on the home page
      - {id: s1, title: "Yeni Bölümler", row_selector: "...", target: episode,      # target: title|episode; ignore: true = alınmaz
         fields: {url: {...}, title: {...}, poster: {...}}}
    nav: {selector: "...", links: [{name: "Aksiyon", url: "/tur/aksiyon", kind: genre, content: title}]}   # kind: genre|category|type|archive
    # links[].content: title|episode (episode = bölüm kartı listesi, başlık listesi DEĞİL); ignore: true; title_type: series|movie;
    # manual: true (admin ekledi). A1 incelemesi (T17) bunları yazar; yalnız content: title listeleri A2/K1'e girer (arşiv önce)
  listing:                   # A2
    url_pattern: "^/tur/[^/]+/?$"
    row_selector: "..."
    fields: {url: {...}, title: {...}, poster: {...}, year: {...}}
    pagination: {kind: next_link, selector: "a.next"}      # kind: next_link|page_param|none ; page_param: {template: "?page={n}"}
    listings: [{name: "Aksiyon", url: "/tur/aksiyon", kind: genre}]
  title:                     # A3
    url_pattern: "^/(dizi|film)/[^/]+/?$"
    key: {regex: "^/(?:dizi|film)/([^/]+)", template: "{1}"}
    type: {from: url, map: {dizi: series, film: movie}, default: series}       # from: url|field
    fields: {title: {...}, poster: {...}, year: {...}, genres: {...}, overview: {...}}
  episodes:                  # A4 (series only)
    where: title_page        # title_page|season_pages
    row_selector: "..."
    fields: {url: {...}, title: {...}, air_date: {...}}
    numbering: {from: url, regex: "(\\d+)-sezon-(\\d+)-bolum", season: 1, episode: 2, default_season: 1}
    seasons: {menu_selector: "...", url_regex: "..."}       # only when where=season_pages
  player:                    # A5 (episode page / movie page)
    url_pattern: "..."
    title_link: {selector: "...", attr: href}               # episode page -> its title page (needed when home links to episodes)
    resolvers: [{type: iframe, selector: "..."}]            # resolver catalog of core.play
  sources: {providers: []}   # A6: optional allow-list/order; host knowledge lives in the provider library
  search: {url: "/?s={query}", method: GET, row_selector: "...", fields: {...}}   # A7, optional
gate: {require: player, probe: 2, blocked: [{on: episode_page, html_regex: "...", reason: "..."}]}   # A7
meta:                        # per page type, written by the stage engine
  title: {samples: ["https://..."], fingerprint: {...}, skipped: ["year"], expect: {...}}
```
Alan kuralı (`{selector, attr, regex, split, index, cast, replace, fallback[], all, multi, self}`) eski `parse.apply_field` ile aynıdır.
`sitemap.adapters`: haritayı `core` fonksiyonlarının beklediği parçalara çevirir (liste kuralı, dizi sayfası kuralı, resolver listesi,
arama kuralı, blocked/gate). Aşama kimliği <-> sayfa türü: `A1 home, A2 listing, A3 title, A4 episodes, A5 player, A6 sources, A7 search_gate`.

### 5.6 Aşama sözleşmesi (`control/stages`)

```python
class Stage:
    id: str; page_type: str; needs: tuple[str, ...]; optional: bool
    def discover(self, ctx) -> Finding      # LLM-free: candidate patch + evidence, may be empty
    def prompt(self, ctx, finding, hint) -> str          # short prompt for ONE page type (only if needed)
    def verify(self, ctx, patch) -> Measurement          # LLM-free: checks=[{name, value, bound, ok}], samples=[{url, ok, note}]
```
Motor döngüsü (iş türü `stage`, `ctx.checkpoint` ile adım adım): `discover -> verify`; geçmezse `prompt -> llm -> verify` en çok 2 tur
(ipucu geldiyse ipucuyla).
**LLM-first (T23, `reads_first` aşamalar A1-A5, onarım hariç):** `discover` -> LLM ilk okuma (amaç + sayfa haritası + devir + site özeti/etiketler +
ipucu + sistem tahmini + yanıt şeması; yanıt `interpretation: [{ref, meaning, take, why}]` + `rules:` = aşama yaması) -> ikisi de ölçülür:
ikisi geçer + aynı sonuç (`Stage.outcome`, devir adres kümeleri) -> puanı iyi olan (eşitlikte LLM); farklı sonuç -> `review` iki aday
(admin seçer, sonra aşamanın kendi incelemesi); biri geçer -> o; hiçbiri -> 2. tur aynı oturumda. Bütçe dolu / LLM yok -> keşif tek başına,
kartta "LLM kullanılamadı, sistem tahmini ile devam". Yorum kanıtta (`interpretation`, `llm_read`) ve A1/A2 inceleme kartında (seçimler ön dolu). Aşama durumları: `pending, running, passed, passed_warn, review, needs_human, engine_gap, skipped`
(`review` = A1 ölçümü geçti, admin bölüm/bağlantı etiketlerini onaylayana kadar bekler; T17).
`passed*` olunca `patch`, `draft_yaml`'a işlenir. LLM yamasının yalnız kendi sayfa türü anahtarına dokunabilmesi motor tarafından zorlanır.
Kullanıcı eylemleri: `hint(text)`, `sample(url)`, `skip(field|stage)`, `rerun`, `mark_gap(title)`, `review(labels)` (A1).
**Etkinleştirme:** A1 (inceleme onaylanmış) ve A3 `passed*` -> site `active` olur ve K0 başlar; A2 geçince K1; A4-A6 geçince oynatma. Etkin sitede bir aşamanın
yeniden çalışıp `passed*` olması yeni `site_versions` satırı yazar (`created_by` = `edit` ya da `repair`).

### 5.7 Sorun kaydı (`control/problems`)

```sql
problems(id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, scope, site_id, page_type, host, canonical_id, episode_id, source_id,
         class, layer, state, parent_id, gap_id, evidence TEXT, attempts INTEGER, llm_runs INTEGER,
         next_at, deadline_at, opened_at, updated_at, resolved_at, confirmed_at, closed_at, resolution)
problem_events(id INTEGER PRIMARY KEY, problem_id, at, actor, kind, note, data TEXT)     -- actor: client|system|llm|admin
problem_watchers(problem_id, profile_id, canonical_id, episode_id, notified_at, PRIMARY KEY(problem_id, profile_id, episode_id))
capability_gaps(id INTEGER PRIMARY KEY, key TEXT UNIQUE, title, detail, status, first_at, sites TEXT, shipped_at, catalog_hash)
```
`scope`: `episode|title|site|host`. Durumlar: `open -> diagnosing -> acting -> verifying -> resolved -> confirmed`; yan durumlar
`waiting_llm, waiting_user, waiting_capability`; kapanışlar `closed_not_public, exhausted`. `resolved` = sistem ölçümü geçti;
`confirmed` = K9 (10 sn hatasız oynatma). `resolved` sonrası yeni hata -> `open` (`attempts+1`). Teşhis tablosu ve eylem sırası T13/T14'te.
İstemci eşlemesi: `finder.state = searching` yalnız açık ve `deadline_at` geçmemiş sorun varken; `found` = `resolved|confirmed`;
`not_found` = diğer hepsi. Bildirim türleri: `source_found` (var), `source_unavailable`, `fixed` (ek alan; sözleşme serbest).

### 5.8 Katalog yazma (`core/catalog`)

```python
upsert_title(site_id, rec, *, origins=()) -> str          # canonical_id
# rec = {key, type: movie|series, title, source_url, year?, poster_url?, backdrop_url?, genres?, overview?, extra?}
upsert_episodes(site_id, title_key, episodes) -> int      # [{season, episode, url, title?, air_date?}] -> video_sources rows
set_movie_source(site_id, title_key, url) -> None
mark_blocked(site_id, url, *, kind, reason, via) -> None
remove_title(site_id, key) -> None
origins: [(kind, value, position)]                        # persisted to source_origins; routing derives library_lists from it
```
Yazma `added_at`'i doldurur, TMDB zenginleştirmeyi `jobs kind=tmdb` olarak sıraya koyar, sonunda `cache.refresh_soon()` çağırır.
İstemciye görünürlük (K5): yalnız en az bir `unknown|healthy` kaynağı olan başlık (eski `HOME_ONLY_READY` davranışı).

## 6. Yapılandırma

`.env` (yalnız bunlar): `HOST` (0.0.0.0), `PORT` (8091), `DATA_DIR`, `TMDB_ACCESS_KEY`, `PI_BIN`, `LLM_MODEL`, `STREAM_PROXY_SECRET`,
`OBSCURA_BIN`. Diğer her şey `settings` tablosu: `backfill_days` (3), `refresh_hour` (4), `llm_daily_calls_per_site` (40),
`repair_autoapply` (true), `problem_deadline_s` (900), `confirm_seconds` (10), `tmdb_auto`, `tmdb_types`, `image_hosts_extra`.

## 7. Tamamlanma tanımı (her görev)

1. `cd server2 && ./check.sh` yeşil: `compileall`, `import app.main`, `node --check` (admin js), `tools/check_imports.py` (katman kuralı),
   site adı taraması (`server2/app` içinde bilinen site adları yok), tüm testler < 20 sn.
2. Görevin "Kabul" maddelerinin her biri bir testle ya da raporda gösterilen komut çıktısıyla kanıtlı.
3. `docs/plans/server2/reports/Txx.md` yazıldı (en çok 40 satır): ne yapıldı, dosya listesi, kabul maddesi -> kanıt tablosu,
   **Sapmalar** (sözleşmeden ayrılan her şey + neden), açık sorular, kopyalanan kaynaklar (`COPIED.md`'ye de işlendi).
4. Görsel doğrulama yapılmadı; kullanıcının elle bakması gereken şeyler raporda "Elle kontrol" başlığında listelendi.

## 8. Denetim (orkestratörün bakacağı)

Sözleşmeye uyum (§5 adları/biçimleri), katman kuralı, K1-K12 ihlali (özellikle: site adı, siteye özel dal, bellekte durum, LLM'in tarama
yoluna sızması, `server/` importu), kabul kanıtlarının gerçekliği (testi okuyup neyi sınadığına bakılır), restart dayanıklılığı,
SSRF ve sır sızıntısı, test süresi, `COPIED.md` ile gerçek kopyaların tutarlılığı, raporda bildirilmeyen sapma.

## 9. Görev listesi

| Görev | Başlık | Bağımlı |
|---|---|---|
| [T00](T00-skeleton.md) | İskelet, DB, check.sh | - |
| [T01](T01-fetch.md) | Paralel sayfa getirme | T00 |
| [T02](T02-extract.md) | Çıkarma + LLM'siz keşif kütüphanesi | T00 |
| [T03](T03-play.md) | Oynatma kütüphanesi (resolver/provider/akış) | T00, T01 |
| [T04a](T04a-catalog-write.md) | Katalog tabloları ve yazma | T00 |
| [T04b](T04b-client-api.md) | İstemci API'si (API.md) | T03, T04a |
| [T05](T05-jobs.md) | Kalıcı iş kuyruğu + admin notları | T00 |
| [T06](T06-llm.md) | Tek atımlık LLM çağırıcı | T00 |
| [T07](T07-sitemap.md) | Site haritası modeli | T02 |
| [T08](T08-stages-a1-a3.md) | Aşama motoru + A1-A3 + öne çıkanlar (K0) | T01, T02, T04a, T05, T06, T07 |
| [T09](T09-stages-a4-a7.md) | A4-A7: bölümler, oynatıcı, kaynaklar, arama/kapı | T03, T08 |
| [T10](T10-admin-shell.md) | Admin kabuğu, Siteler, Site sayfası | T05, T07 (T08 ile birlikte) |
| [T11](T11-crawl.md) | Kademeli tarama (frontier, K1, K2, imza) | T08, T09, T10 |
| [T12](T12-routing.md) | Yönlendirme kuralları | T04a, T06, T08, T10 |
| [T13](T13-problems-core.md) | Sorun çekirdeği + istemci sözü | T03, T04b, T05 |
| [T14](T14-repair.md) | Onarım, motor boşluğu, Sorunlar ekranı | T09, T10, T11, T13 |
| [T15](T15-ops.md) | Ayarlar ekranı, deploy betiği, belgeler | T05, T10 |
| [T16](T16-admin-catalog.md) | Admin Katalog ekranı + panel içi oynatma | T03, T04a, T04b, T10 |

### 5.9 Provider sonucu (`core/play/providers`)

Kütüphanenin dışarıya tek biçimi (site bilgisi içermez; girdi yalnız provider adresi + Referer):

```python
resolve(url, *, referer="") -> ProviderResult | None
ProviderResult = {provider, duration_s?, cache_ttl?,
  streams:   [{url, type: hls|mp4|embed, quality?, width?, height?, bitrate?, label?, request_headers?, stream_proxy?}],
  subtitles: [{url, lang, label, format}],
  audio:     [{lang, label, default?}],
  measured:  {resolve_ms}}
```
Sınır kuralı: `core/play/providers/**` ve tarifler site adı, site yolu ya da site seçicisi içermez; `core/play/resolvers/**` yalnız
haritadan gelen parametrelerle çalışır (siteye özel olan PARAMETREDİR, kod değil).

## 10. Opus'a görev verme

Her görev ayrı oturumda, şu tek cümleyle verilir (çalışma dizini `micro_services/diziflix`):

> Önce `docs/plans/server2/README.md`'yi, sonra `docs/plans/server2/Txx-<ad>.md`'yi oku ve görevi uygula. Yalnız `server2/` altına ve
> `docs/plans/server2/reports/Txx.md`'ye yaz. Bitince `cd server2 && ./check.sh` çıktısını ve raporu ver.

Sıra §4.3'teki dalgalara göredir; bir dalga, önceki dalganın tüm raporları denetimden geçmeden başlamaz. Aynı dalgadaki görevler paralel
verilecekse her biri ayrı git worktree'de çalışır (ortak dosyalara tek satırlık kayıtlar birleştirmede elle çözülür).
Commit'i görevi yazan yapmaz; denetimden geçen görev `diziflix` dalına orkestratör/kullanıcı kararıyla işlenir (yalnız `git add diziflix`).

## 11. Kullanıcının elle sınayacağı kilometre taşları

| Dalga sonu | Kullanıcı ne dener | Beklenen |
|---|---|---|
| 4 | `/admin` -> Site ekle: bir adres ver | A1-A3 kartları canlı ilerler; takılırsa kart "SENİ BEKLİYOR" + kanıt; geçince öne çıkan başlıklar eklenir |
| 5 | Katalog ekranı -> bir dizi -> bölüm -> `[çöz]`, `[oynat]` | Sezon/bölüm/kaynak ağacı; çözüm izi adım adım; video panelde oynar, kalite/altyazı seçilir |
| 6 | Tarama sekmesi, Yönlendirme sekmesi | İlerleme çubuğu 3 günlük plana göre artar; öneri kuralları onaylanınca ana ekran satırları dolar |
| 7 | Bir aşamayı bilerek bozacak ipucu ver / kaynağı bozuk bir bölüm | Sorun kutusunda tek satır; LLM düzeltmesi doğrulanırsa kendiliğinden uygulanır + zilde bilgi notu; doğrulanamazsa soru kartı |

`tools/smoke_site.py <adres>` (T15) aynı akışı terminalden verir. Gerçek sitelerdeki sonuçlar görev kabulü değildir (K12); çıkan eksikler
"motor boşluğu" ya da yeni görev olarak orkestratöre döner.

## 12. Bilinen riskler (denetimde izlenecek)

| Risk | Nerede ortaya çıkar | Önlem |
|---|---|---|
| LLM'siz keşfin başarı oranı bilinmiyor | T08/T09, ilk gerçek siteler | `llm_calls` sayımı site başına izlenir; keşif zayıfsa `core/extract` güçlendirilir (yeni görev) |
| Crawlee yerine süreç içi getirme bazı sitelerde engellenebilir | T01 | `auto` kipi browser'a yükseltir; sürerse boşluk kaydı |
| Eski koddan kopyalanan modüllerde gizli yönetim bağı kalması | T03, T04a | `check_imports` + grep kabul maddeleri; `COPIED.md` denetimi |
| İstemci sözleşmesinde fark | T04b, T13 | sözleşme testi `docs/api-samples` üzerinden; istemci turunda elle doğrulama |
| pi'nin oturum/token çıktısı varsayıldığı gibi olmayabilir | T06 | oturum yalnız tasarruf; `session_lost` yolu her zaman çalışır; token kolonları boş kalabilir |
| Kapsam: 18 görev | tümü | dalga 4 sonunda uçtan uca ilk sonuç; gerekirse T12/T15 ertelenir |

Bu turun dışında (ayrı karar): eski `server/`'ın kaldırılması ve istemci adresinin 8091'e çevrilmesi (kesim), Kategoriler ve TMDB toplu
doldurma ekranları, istemcide "sorun bildir" ve onay düğmeleri, Android/Tizen.
