# T26: TMDB otomatik zenginleştirme + geriye dönük doldurma

**Kontrol bulguları (madde 1):** `write.upsert_title` ekleme VE güncellemede `enrich.needs_lookup` (anahtar + `tmdb_auto` + `tmdb_types` + eşleşmemiş + 7 gün penceresi) -> `hooks.enqueue_tmdb` -> `jobs.enqueue("tmdb","tmdb:<cid>")` (aynı anahtar = idempotent). Tarama (`crawl/process.py` K0/K1/K2/tazeleme) ve arama/A3 içe aktarımı (`stages/search_run.py`) bu yoldan yazar; `tmdb` handler'ı `wiring.register`'da kayıtlı. Yerel DB'de 0 iş vardı çünkü anahtar boştu (hook `None` döndü) ve geriye dönük tetikleyici yoktu. Eksikler: 429 istemcide 2 kez 1 sn beklenip sonra iş `Done` oluyordu (yeniden deneme yok); 401 her başlıkta sessizce hata sayılıyordu.
Anahtar biçimi zaten otomatik (`tmdb.key_kind`: `eyJ...`/40+ karakter -> Bearer, aksi v3 `api_key`). Seriler: eşleşme yazılınca `enrich_items` -> `seasons.auto_enrich` (zaten var). `run_job` yazımdan sonra `cache.refresh_soon()` çağırıyor (zaten var).

**Yapılan**
- `core/catalog/enrich.py`: `enrich_items` sayaçlarına kısa hata kodu `error` (ör. `HTTP 401`; anahtar asla).
- `control/wiring.py`: `_tmdb_job` 401/403 -> tek `warn` notu "TMDB anahtarı geçersiz", bellek içi bayrak (yeniden başlatmaya dek yeni TMDB işi/sıralama yok), `Done` (döngü yok); 429/5xx/timeout/network -> `Retry(600)`. `tmdb_backfill()`: `tmdb_types` içinde durum NULL ya da eşleşmemiş + 7 gün eski başlıklara iş, 20'şerli/10 sn aralıklı `run_after`. Açılışta `register()` içinde çağrılır; günlük `tmdb_backfill` iş türü (saat 04). `tmdb_stats()` sayaç.
- Admin Ayarlar: `GET /api/control/settings` yanıtına `tmdb {matched,total,pending,key_invalid}`; `POST /api/control/settings/tmdb-backfill` ("Eksikleri şimdi doldur"; anahtar yok/geçersiz/auto kapalı -> 409). `settings.js`: "Zenginleştirilmiş X / Y", sıradaki iş sayısı, düğme, "geçersiz (401)" rozeti.
- Test: `tests/test_t26_tmdb_auto.py` (3 test: anahtarlı upsert iş sıralar / anahtarsız sıralamaz, güncelleme kopya üretmez; açılış backfill 3 iş + ikinci açılışta kopya yok + aralıklı; 429 Retry, 401 tek not + durma). Sahte `_http`, gerçek TMDB çağrısı yok.

**Sapmalar:** geçersiz anahtar bayrağı kalıcı değil (anahtar `.env`'den açılışta okunur; düzeltip restart = sıfırlanır). Zaten eşleşmiş ama sezon verisi eksik diziler için ayrı tarama yok (yalnız eşleşme anında sezonlar dolar).
**check.sh:** YESIL (6 adım), testler ~15.1 sn.
**Elle kontrol (kullanıcı):** Ayarlar > TMDB sayaç/düğme; restart sonrası sayacın 0/133'ten artması.
