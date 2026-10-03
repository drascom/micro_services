# CLAUDE.md

diziflix: film/dizi kataloğu + FastAPI backend ve istemciler (web = `tizen-client/` tarayıcıda, Samsung Tizen TV, Android). Bu dizin `micro_services` reposunda bağımsız bir servis (git repo kökü bir üst dizin).

Bu dosya KISA tutulur (her ajan turunda bağlama girer). Ayrıntı `docs/arch/` altındadır: ilgili alana dokunmadan önce o dosyayı oku; yeni uzun paragrafı buraya değil oraya yaz.

## Odak ve kararlar (kullanıcı)

- **Odak: sunucu + web istemcisi** (tizen-client tarayıcıda). Android ve Tizen TV istemcileri ertelendi; kullanıcı "şimdi istemcilere bakalım" diyene kadar önerme, görevlere katma (APK/wgt, cihaz doğrulaması, TV'ye özgü kusurlar). Sözleşme gereği zorunlu ek alan değişikliği hariç.
- **Yalnız halka açık içerik**: telif/erişim engelli ya da oynatıcısı bulunamayan dizi/bölüm kütüphaneye HİÇ alınmaz (yaml `availability_gate:` + `blocked:`; `library/gate.py`). Ayrıntı: `docs/arch/onboarding-quality.md`.
- **Sistem kendini düzeltir, siteye elle dokunma**: "beyin" pi'de (skill + extension), "motor" kodda; site tasarımı site yaml'ında, video host bilgisi provider kütüphanesinde. Onboarding/heal ajanı hatayı kendi teşhis edip düzeltir, bilgiyi bulamazsa kullanıcıya sorar (`ask_user`), "yok" = o alanı atla. Var olan sitenin yaml/baseline'ını elle düzeltme.
- **Scraper config'inde referans SUNUCUDUR (61)**: gerçek LLM orada, heal `configs/<site>.yaml` + `.baseline.json`'u kendisi düzeltir/sürümler. Yerel `server/app/scraper/configs/` sunucunun AYNASIDIR (sunucudan çekilir, yerelden itilmez). Biz yalnız uygulama kodunu (normalize, site_extractors, providers, ingest, API, istemci, testler) düzenleriz; yalnız YENİ site config'i yerelde yazılıp deploy'la gider. Scraper bozulursa önce admin Olay defteri/heal (`POST /api/ops/sites/{site}/heal`); elle config düzeltmesi gerekirse kullanıcıya sor. Yerel fixture testi sunucu config'iyle uyuşmazsa FIXTURE/test düzeltilir (`tools.homepage_probe` ile yenile), config eski sürüme döndürülmez. Site config'leri (`configs/*.yaml`, `*.baseline.json`) git'te izlenmez; `configs/providers/` izlenir. Tam metin: `docs/arch/ops-deploy.md`.
- **Worker'lar görsel test yapmaz**: build/sözdizimi doğrulaması yeter; nasıl göründüğünü kullanıcı bakar.

## Deploy kuralı

Deploy'u yalnız kullanıcı "deploy et" dediğinde yapılır (kendiliğinden yapma). Host `root@192.168.0.61:/root/micro_services/diziflix/server`, servis `diziflix` (uvicorn, --reload YOK). **Her deploy restart + uç doğrulaması (`/admin`, `/api/health`, `/api/*`) içerir** (restart'sız deploy eski kodu çalıştırır). Önce `./deploy.sh --dry-run`; `.env`, `data/`, `venv/` korunur; `.env`'deki `TMDB_ACCESS_KEY`'i asla yazdırma. Sunucu DB'sini SIFIRLAMA (`./clear-remote.sh`) yalnız kullanıcı isterse. Bayraklar, config karar tablosu (v7 → v8), geri alma, yedek budama, REMOTEONLY/`--pull-configs`: `docs/arch/ops-deploy.md`.

## Test stratejisi

2000+ test küçük uygulama için fazla: yalnız **smoke set** (hedef 150-300 test, <20 sn) + tek komutluk sözdizimi kontrolü (`compileall`, `import app.main`, `node --check`, yaml/json ayrıştırma) koşturulur; tam takım YOK (worker'lar token harcamasın). Elenen testler silinmez, `server/tests_archive/`'e taşınır. Kullanıcı bölümleri manuel test edip hata bildirir; hata için yalnız tekrarı önemliyse tek hedefli test ekle. Test modülü `import _sandbox` (`tests/_sandbox.py`) ile başlar (geçici `DATA_DIR`/`DB_PATH`/`IMG_CACHE_DIR`, boş TMDB anahtarı, gerçek `data/`'ya dokunma kıyası: değişirse `SANDBOX VIOLATION` + çıkış kodu 3); yeni test dosyası da aynı satırla başlar. pi skill boyut bütçesi: `server/tests/test_pi_size_budget.py` (tavanı yükseltme, önce kısalt). `tools.gen_onboard_refs --check` (`cd server`) temiz kalmalı.

## Git kuralı

Repo `github.com/drascom/micro_services` (PUBLIC). Yalnız `diziflix` dalına commit/push; `main`'e dokunma/merge etme. Yalnız `git add diziflix` (kök düzeyindeki yerel farklara dokunma, ASLA `git add -A`/`commit -a`). Testler (`server/tests/`, `tests_archive/`, `tests/*.js`) git'te İZLENİR (kullanıcı kararı). Site config'leri (`providers/` hariç), `.env`, data, build, `deploy-backups`, `HANDOFF*.md` git dışı (`diziflix/.gitignore`). Commit sonuna sistemin verdiği `Co-Authored-By` satırı.

## Yapı

- `server/` - FastAPI + SQLite + Pillow, `0.0.0.0:8090` (ana kod). Giriş: `server/app/main.py` (`app.main:app`)
- `tizen-client/` - vanilla HTML/CSS/JS istemci (build adımı yok; `build-wgt.sh` -> `dist/diziflix.wgt`). Sunucu adresi varsayılan `http://192.168.0.61:8090`
- `API.md` - istemci-sunucu wire sözleşmesi (kaynak gerçek budur). Sözleşme değişirse `API.md` ve `tizen-client/js/api.js` birlikte güncellenir; yalnızca ALAN EKLENİR
- `docs/DATA-CONTRACT-V1.md`, `server/CANONICAL.md` - scraper -> katalog -> TV sözleşmesi, kimlik birleştirme, oynatma sağlığı
- `docs/arch/` - ayrıntılı mimari notlar (aşağıdaki liste); `docs/plans/site-onboarding/` - onboarding planı/faz görevleri
- `tests/*.js` - istemci testleri (Node, DOM/AVPlay sahte nesneleriyle), `server/tests/` - Python unittest
- Veri: `server/data/` (`diziflix.db`, `imgcache/`, `scraper_state/<site>.json`, `fixture.json`); db/imgcache gitignored; tabanı `DATA_DIR` (`config.py`)

## Komutlar (`server/` içinde)

```bash
./install.sh                                   # venv + requirements + .env + fixture (+ Linux'ta systemd `diziflix`)
source venv/bin/activate
uvicorn app.main:app --reload --port 8090      # geliştirme; docs: /docs
venv/bin/python -m unittest discover -s tests -v          # sunucu testleri (hedefli kullan; tam takım yok)
node tests/player_health.js                    # proje kökünden; diğer istemci testleri: tests/*.js
venv/bin/python -m tools.ingest <site>         # elle ingest (sinemalar | yabancidizi)
venv/bin/python -m tools.series_crawl yabancidizi [--dry-run] [--limit N] [--force] [--key dizi/<slug>]  # dizi bölüm envanteri
venv/bin/python -m tools.homepage_probe yabancidizi [--html tests/fixtures/yabancidizi_home.html]  # salt-okunur scraper probu
venv/bin/python -m tools.tmdb_enrich [--type movie|series] [--limit N] [--dry-run] [--force] [--seasons]  # TMDB geriye dönük doldurma
sudo tools/install_crawler.sh                  # Crawlee + Obscura runtime (veya tools/install_obscura.sh)
./deploy.sh --dry-run                          # proje kökünden; deploy'u yalnız kullanıcı isteyince
```

## Mimari haritası (server/app)

Her satır: ne yapar, ana dosya -> ayrıntı için doc.

- `config.py` - `.env` yükler (`os.environ.setdefault`), tüm ayarlar modül sabitleri; `.env.example` + `server/README.md` tam liste. `settings.py` admin panelinden yönetilen kalıcı ayarlar (`data/ops_settings.json`, `.env` yalnız varsayılan). -> `home-and-api.md`
- `cache.py` - katalog RAM'de `Snapshot`; APScheduler yeniler, 60 sn'de bir ingest "tick"i (`autoscan.py`); tarama ortasında da yenilenir (`set_progress_hook`). -> `home-and-api.md`
- `sources/` (`SourceAdapter` `base.py`: `mock` = `data/fixture.json` | `library` = SQLite kanonik kütüphane, `SOURCE` env), `routers/` (health, profiles, boot/rows/detail kademeleri, streams, subtitles, progress, mylist, search, images `/img`, catalog, ops*), `db.py` (sqlite3, WAL, `SCHEMA` + additive `_migrate`), `images.py` (Pillow placeholder + uzak afiş proxy, `REMOTE_IMG_HOSTS` allow-list, `data/imgcache`). Hata zarfı `{"error":{"code","message"}}` (`errors.py`); dev yardımcıları `?delay=<ms>`, `?fail=1`. Auth yok (kapalı LAN), profil = seçim. -> `home-and-api.md`
- `homelayout.py` - ana ekran düzeninin TEK yeri (`layout(profile_id)`): slider, continue, trending/tüm diziler/filmler, dikkate değer filmler, mylist. Eski satırlar `/api/row/<id>` ile çalışır. -> `home-and-api.md`
- `library/` - `ingest.py` (scraper -> normalize -> kimlik -> merge), `normalize.py` (`@register` site başına | yaml `normalize:` genel), `identity.py`, `tmdb.py`/`enrich.py`/`seasons.py` (TMDB), `series_crawl.py` + `series_dir.py` (dizi tam bölüm envanteri), `gate.py` (telif kapısı), `trailer_check.py`. -> `library-ingest.md`
- `library/videos.py` - kaynak çözümleme (adaylar/kaynaklar paralel, süre sınırı, önbellek; sağlık 1 hata=suspect, 3=broken), `streamlife.py` (`valid_until` yük önbelleği), `streamproxy.py`/`hlsproxy.py` (imzalı mp4/HLS vekili `/api/stream-proxy/<token>`), `streamdiag.py` (hata tanısı), `tracks.py`/`subtitles.py` (ses/altyazı). -> `streams.md`
- `scraper/` - config-as-data: `configs/<site>.yaml` (+`.baseline.json`) -> `runner.run_site` (fetch -> parse -> schema -> drift -> LLM self-heal `heal.py`); `site_extractors/`, `providers/` (vidmolly, okru + yaml tarifleri), `resolvers/`, `collections.py`, `series_generic.py`, `search_generic.py`, `fetch.py`, `obscura_worker.py`/`crawlee_worker.py`. -> `scraper-engine.md`
- `library/search_all.py` + `library/sourcefinder.py` - toplu arama (siteden yalnız ana sayfa bölümleri alınır, tam liste ARAMA ile gelir) ve oynatmada akış yoksa arka plan kaynak bulucu. -> `scraper-engine.md`
- Onboarding: `scraper/onboard.py` (tek slotlu pi oturumu), `routers/onboard_sandbox.py` (`/api/onboard/sandbox/*`, localhost + token, SSRF `netguard`), `server/pi/` (skill + extension), `onboard_pipeline.py`, `routers/ops_onboard.py`, `ops_sites.py`; heal ajanı `heal_agent.py`/`playheal.py`. -> `onboarding.md`, `onboarding-quality.md`
- Admin: `static/admin/` (vanilla; Olay defteri, Ayarlar, Kütüphane K/T rozeti, Site ekle/Siteler); yeni admin js dosyası `ops.py` `_ASSETS`'e eklenmeli. -> `home-and-api.md`, `onboarding.md`

## docs/arch/ (konu: ne zaman oku)

- Site ekleme, resolver/`player_page`, provider kütüphanesi, sandbox, pi skill/extension, admin Site ekle/Siteler: yeni site/yeni video host/onboarding arayüzü işinde -> `docs/arch/onboarding.md`
- Ajan öz-denetimi/`ask_user`, onboarding sertleştirme kriterleri (`ONBOARD_HARDEN`), telif kapısı (`availability_gate`/`blocked`), heal tetikleyicileri ve kapıları: onboarding kriteri/heal/telif işinde -> `docs/arch/onboarding-quality.md`
- Ingest, normalize, kimlik, TMDB zenginleştirme, dizi envanteri (`series_crawl`), `series_dir`: katalog/ingest/TMDB işinde -> `docs/arch/library-ingest.md`
- Scraper motoru (config-as-data, fetch, `series_page`, `search:`, koleksiyonlar, kaynak bulucu): scraper/yaml anahtarı işinde -> `docs/arch/scraper-engine.md`
- Akış çözümleme, `valid_until` yük önbelleği, akış vekili (`STREAM_PROXY*`) + HLS vekili, `streamdiag` tanısı, ses/altyazı izleri: oynatma/akış hatası işinde -> `docs/arch/streams.md`
- Ana ekran düzeni (`homelayout.py`), cache/ayarlar/routers/db, admin paneli, API sözleşme koruması: ana ekran/API/admin işinde -> `docs/arch/home-and-api.md`
- İstemci turu (finder/bildirim/kaynak yok), UI cilası, HLS/motor seçimi, Android: istemci işinde (şu an ertelenmiş) -> `docs/arch/clients.md`
- `deploy.sh` ayrıntısı, config karar tablosu, geri alma, yedek budama, `--pull-configs`, `clear-remote.sh`, config referansı kuralının tam metni: deploy işinde -> `docs/arch/ops-deploy.md`

## TMDB (kısa)

Anahtar `TMDB_ACCESS_KEY` (v4 Bearer ya da v3; `TMDB_TOKEN`/`TMDB_API_KEY` de geçerli), asla loglama. TMDB varsa poster/backdrop TMDB'den (`tmdb_*` kolonları), diğer alanlar yalnız boşluk doldurur; sezon/bölüm görselleri `library/seasons.py`. Ayarlar admin Ayarlar sekmesinden (`tmdb_auto`, `tmdb_types`). Env listesi, backfill, önizleme: `docs/arch/library-ingest.md`.

## Notlar / tuzaklar

- Yeni scraper sitesi: yaml + gerekirse `normalize.py` fonksiyonu/`site_extractors/`; yeni video host'u: provider kütüphanesine tarif (`scraper/configs/providers/<ad>.yaml`) ya da `scraper/providers/` modülü; yeni host'un afişi için `REMOTE_IMG_HOSTS`.
- Kart kimliği kuralı: `id` = yapım id'si, bölüm kartı `card_kind=episode` + `episode_id` + `card_key`. İstemci sözleşme koruması: `server/tests/test_client_contract.py` + `docs/api-samples/*.json` (`venv/bin/python -m tools.gen_api_samples`) + `node tests/api_samples_client.js`.
- İstemci HLS/motor kararı `streams[].type`'a göre (URL uzantısına değil); cache-bust şu an `responsive-v34` (`index.html`). UI cilası: `tizen-client/README.md`.
- `server/pi/` deploy ile gider; skill referans blokları üretilir (`tools.gen_onboard_refs`), elle dokunma.
- Bekleyen istemci işleri (oynatıcı akış değişikliği, sezon/bölüm görselleri, altyazı paneli vb.) kullanıcı istemci turunu başlatınca topluca yapılır (hafıza: diziflix-client-batch).
