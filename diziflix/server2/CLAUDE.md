# CLAUDE.md (server2)

diziflix'in yeni sunucusu: port 8091, kendi DB'si `data/diziflix2.db`, systemd servisi `diziflix2`. Siteyi kullanıcı adresle ekler,
aşamalar (A1-A7) site haritasını çıkarır, tarama LLM'siz kataloğu doldurur, sorunlar tek kutuda onarılır. Eski `../server/`
(8090, servis `diziflix`) DOKUNULMAZ, import edilmez. Sözleşme: `../docs/plans/server2/README.md`; raporlar `reports/Txx.md`.
Mimari: `docs/ARCHITECTURE.md`. Kurulum/.env: `README.md`.

## Kararlar (README §1, kısa)

- K1 ayrı klasör/DB/port; K2 siteler taşınmaz; K3 `app/` ve `seed/` içinde site adı ve siteye özel kod yok (motor boşluğu genel yetenekle kapanır).
- K4 provider kütüphanesi (`core/play/providers`) siteyi bilmez; sitenin provider'a nasıl vardığı haritada (`pages.player.resolvers`).
- K5 oynatıcısı olmayan/engelli içerik istemciye görünmez. K6 tarama LLM'siz; LLM yalnız aşama önerisi, kural çevirisi, onarım (tek atım, araçsız).
- K7 tam aktarım varsayılanı 3 gün. K8 ölçümü geçen LLM düzeltmesi kendiliğinden uygulanır + admin notu. K9 onay = 10 sn hatasız oynatma.
- K10 istemcilere dokunulmaz, `../API.md` aynen sunulur (yalnız alan eklenir). K11 küçük smoke test seti. K12 testler uydurma sitelerle.
- K13 admin Katalog ekranında panel içi oynatma.

## Komutlar (`server2/` içinde)

```bash
./install.sh                                   # venv (uv) + requirements + .env; Linux'ta systemd `diziflix2`
venv/bin/uvicorn app.main:app --port 8091      # yerelde çalıştır (--reload yok); /api/health, /admin
./check.sh                                     # compileall, import, node --check, katman kuralı, site adı taraması, testler (<20 sn)
venv/bin/python -m unittest discover -s tests  # yalnız testler; tek dosya: cd tests && ../venv/bin/python -m unittest test_t15_ops
./deploy.sh --dry-run                          # yalnız check + fark (rsync -n); deploy YALNIZ kullanıcı isteyince: ./deploy.sh
./deploy.sh --print-plan                       # hiçbir şey çalıştırmadan deploy komutlarını yaz; geri alma: ./deploy.sh --rollback
venv/bin/python -m tools.smoke_site <adres>    # çalışan sunucuda site ekle, aşamaları izle (gerçek ağ + LLM; testte YOK)
```

## Katman kuralı

`core` -> hiçbir şey; `control` -> `core`; `api`/`admin` -> `core` + `control` (`tools/check_imports.py`, check.sh adım 4).
`core` haber verecekse `core.events.emit(...)`; `control` aboneliği/kancaları `app/control/wiring.py`'de bağlar.
Ağ çağrısı yalnız `core.fetch` (netguard + IP sabitleme + boyut sınırı); httpx/curl_cffi doğrudan kullanılmaz.

## Nasıl eklenir

- **Yeni iş türü:** handler `(job, ctx) -> Done|Retry|Wait|Fail` yaz, `jobs.register(kind, fn, llm=?)` çağrısını modülün `register()`'ına
  koy (gerekirse `wiring.register`'a tek satır). Uzun iş adım adım `ctx.checkpoint(step, payload)`; durum DB'de, bellekte değil.
- **Yeni aşama:** `control/stages/<ax>_<ad>.py` içinde `Stage` (`discover`/`prompt`/`verify`), `engine.add_stage(...)` ile kayıt; LLM yaması yalnız
  kendi sayfa türü anahtarına dokunur. Kart/eylemler `status.py` + admin `site.js` zaten genel.
- **Yeni migrasyon:** `app/migrations/NNN_<ad>.py` (`VERSION = NNN`, `up(conn)`), yalnız ekleme (tablo/kolon); açılışta sırayla uygulanır.
  Çok ifadeli yazma `with db.transaction():`.
- **Yeni admin ekranı:** `app/admin/routes_<konu>.py` (`router`) + `app/main.py` `ROUTERS`'a tek satır; `static/js/<ekran>.js` içinde
  `ADMIN.screen(ad, {title, menu, render})` (site sekmesi: `ADMIN.siteTab`) + `assets.py` `_ASSETS`'e ve `static/index.html`'e birer satır.
- **Yeni ayar:** `app/settings.py` `DEFAULTS` + `admin/routes_settings.py` `SCHEMA` (tip/aralık; test ikisinin eşitliğini denetler). Yeni env YOK.

## Tuzaklar

- Test dosyası `import _sandbox` ile başlar (geçici DATA_DIR, ağ kapalı, sahte LLM); gerçek `data/` değişirse `SANDBOX VIOLATION`.
  `tests/_sandbox.py` ve `tools/check_imports.py` paylaşımlıdır, değiştirmeden önce sor. check.sh toplam test süresi < 10 sn hedefi.
- Sırlar (`TMDB_ACCESS_KEY`, `STREAM_PROXY_SECRET`, pi kimlikleri) loglanmaz, yanıtta dönmez, prompt'a girmez; Ayarlar yalnız "tanımlı/tanımsız".
- `drain` kalıcıdır (`settings` `jobs:drain`): açık kalırsa yeni iş başlamaz; açılışta log WARNING + tek `warn` admin notu. Deploy sonunda/hata trap'inde
  kapatılır (HTTP başarısızsa ssh ile doğrudan `settings.set`), Ayarlar > Sistem'den de kapanır. Deploy kodu önce `server2.next/`'e yazar,
  canlı dizine ancak drain başarılıysa geçer (çıkış 3'te canlı dizin değişmez).
- Site haritaları ve provider tarifleri DB'de / `data/`'dadır: deploy onlara dokunmaz, elle dosya düzeltmesi yok (sürüm: `site_versions`).
- Browser yükseltmesi kapalı (`core/fetch/browser.GUARDED=False`); `auto` kipi browser'a çıkmaz.
- Eski koddan kopya -> `COPIED.md`'ye satır. Deploy, sunucuya bağlanma, commit yalnız kullanıcı isteyince.
