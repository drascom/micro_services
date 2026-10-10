# T00: İskelet, DB, check.sh

**Bağımlı:** yok. **Açar:** tüm görevler. **Oku:** README (tamamı); `server/app/{main,config,db,errors}.py`, `server/tests/_sandbox.py`,
`server/install.sh`, `inventory/02 §1.2`.

## Amaç
`server2/` klasörünü, boş ama çalışan bir FastAPI uygulaması, sürümlü migrasyonlu SQLite katmanı, test sandbox'ı ve tek komutluk
denetim betiğiyle kurmak. Sonraki tüm görevler buraya dosya ekler.

## Yapılacaklar
1. Klasör yapısı README §3'e göre (boş paketler `__init__.py` ile). `requirements.txt`: eski `server/requirements.txt`'ten yalnız gerekenler
   (fastapi, uvicorn, selectolax, httpx, curl_cffi, pillow, pyyaml, pydantic, apscheduler GEREKMEZ: zamanlama T05 yürütücüsünde).
2. `app/config.py`: yalnız README §6'daki env'ler; `.env` yükleme (`os.environ.setdefault`), `DATA_DIR` altı yollar (`DB_PATH`,
   `IMG_CACHE_DIR`, `PAGES_DIR`, `PROVIDERS_DIR`). `.env.example` tam liste.
3. `app/db.py`: README §5.1. `connect()`, iş parçacığı başına `_shared()`, `query`, `query_one`, `execute`, `transaction()` (context manager,
   `BEGIN IMMEDIATE`, hata olursa rollback), `read_transaction()`. `init()`: `schema_migrations` + `app/migrations/NNN_*.py` dosyalarını
   sürüm sırasıyla, her birini tek işlemde uygular; uygulanmış sürümü atlar; aynı `VERSION` iki dosyada varsa açılışta hata.
4. `app/migrations/001_base.py`: `settings` tablosu. `app/settings.py`: `get(key, default)`, `set(key, value)`, `all()`; değerler JSON;
   README §6 varsayılanları kodda tek sözlükte.
5. `app/errors.py`: hata zarfı `{"error": {"code", "message"}}` (eski `errors.py` ile aynı biçim; istemci sözleşmesi).
6. `app/core/events.py`: README §5.2 (`emit`, `subscribe`, `clear()` test için). Abone hatası loglanır, yayıncıya sızmaz.
7. `app/main.py`: `lifespan` = `db.init()` -> (sonraki görevlerin kayıt noktası: `startup_hooks`/`shutdown_hooks` listeleri) ;
   `GET /api/health` -> `{"ok": true, "version", "db": "ok"}`. Router kaydı tek listeden (`ROUTERS`), her görev bir satır ekler.
8. `tests/_sandbox.py`: geçici `DATA_DIR`/`DB_PATH`, boş `TMDB_ACCESS_KEY`, `LLM` sahte kipi (`DIZIFLIX_FAKE_LLM=1`), ağ kapalı
   (`socket` bağlantısı yerel dışına hata verir); `app.config` önce import edildiyse `RuntimeError`; çıkışta gerçek `server2/data`
   değiştiyse `SANDBOX VIOLATION` + çıkış kodu 3.
9. `tools/check_imports.py`: `app/core/**` içinde `app.control`, `app.api`, `app.admin` importu; `app/control/**` içinde `app.api`,
   `app.admin` importu varsa listeleyip kod 1 ile çıkar (AST ile, metin araması değil; tembel importlar dahil).
10. `check.sh`: sırayla `compileall app tools`, `python -c "import app.main"`, `node --check` (`app/admin/static/js/*.js` varsa),
    `tools/check_imports.py`, site adı taraması (`grep -rniE` ile `server2/app` ve `server2/seed` içinde eski site adları:
    liste betikte değişken), `python -m unittest discover -s tests` + süre ölçümü (20 sn üstü = hata). İlk hatada durur, özet basar.
11. `install.sh`: venv (uv varsa uv) + requirements + `.env` yoksa `.env.example`'dan. systemd kurulumu T15'te.
12. `server2/CLAUDE.md` (kısa: komutlar + README'ye işaret), `server2/COPIED.md` (başlık + boş tablo), `server2/.gitignore` (`data/`, `venv/`, `.env`).

## Kapsam dışı
Katalog tabloları (T04a), kuyruk (T05), herhangi bir iş mantığı, systemd/deploy.

## Kabul
- `./install.sh && venv/bin/uvicorn app.main:app --port 8091` açılır; `/api/health` 200.
- Migrasyon: boş DB'de 001 uygulanır; ikinci açılışta yeniden uygulanmaz; sahte `002` eklenince yalnız o uygulanır (test).
- `transaction()` içindeki hata tüm yazmaları geri alır (test).
- `check_imports.py` bilerek konmuş ihlali yakalar (test: geçici dosya).
- `check.sh` yeşil; bilinen bir site adı `app/` içine konunca kırmızı (raporda gösterilen komut çıktısı).
- Sandbox: test içinden dış ağa bağlantı denemesi hata verir (test).
