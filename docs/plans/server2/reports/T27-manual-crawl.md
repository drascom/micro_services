# T27 raporu: elle tarama

**Yapılan:** `control/crawl/manual.py`: iş `crawl_all` (`crawl_all:<site>`, yük `trigger=admin`, parti başına checkpoint) bekleyen tüm sayfaları (`new` + `gate_wait` + kota bekleyen K1, vadesi gelmiş `failed`, yolda bulunanlar) okur; sayfa iş başına bir kez (`crawled_at < started_at`). İş `crawl_title` (`crawl_title:<cid>`): başlık sayfası -> kapı -> bölümler + sezon sayfaları; frontier'de yoksa `source_url`'den eklenir; sonuç "N sezon, M bölüm, K kaynak yazıldı" ya da hata (kapı engelli/challenge/bölüm yok).
- İkisi de K1 kotasını ve kapı kotasını saymaz (`Ctx.admin`, `reserve_gate(admin=True)`); nezaket aynı (`core.fetch` limiter, robots, `hold_until` -> başlamaz/durur, 3 challenge -> 6 sa bekleme).
- Elle tarama sürerken normal `crawl` işi `Retry(300)` ile bekler (çift getirme yok). Durdur = `jobs.cancel`; kalan sayfalar planlarında kalır. Bitiş/durdurma admin notu + süre.
- Ayar `crawl_gate_daily_per_site` (60, 10-2000): `settings.DEFAULTS` + `SCHEMA` (+ `settings.js` grubuna tek anahtar: aksi halde Ayarlar kaydı boş alana takılırdı).
- Uçlar: `POST /api/control/sites/{id}/crawl/all`, `.../crawl/all/stop`; `POST|GET /api/control/catalog/{cid}/crawl`. `status.view`'e `plan` (Plan satırı) + `manual` (ilerleme, süre).
- Admin: Tarama sekmesi Plan satırı, "tümünü şimdi tara"/"elle taramayı durdur", çubukta "elle tarama: X / Y", son elle taramanın süresi; değişmeyen veri yeniden çizilmez; düğme odağı artık "düzenleniyor" sayılmaz (eski hata: tıklanan düğme yanıtı çizdirmiyordu). Katalog ayrıntısında "bu başlığı şimdi tara" (iş izlenir, bitince ağaç yenilenir + sonuç notu).

**Dosyalar:** yeni `app/control/crawl/manual.py`, `tests/test_t27_manual_crawl.py` (4 test, ~0.2 sn); değişen `crawl/{process,run,status}.py`, `admin/routes_{crawl,catalog,settings}.py`, `settings.py`, `static/js/{crawl,catalog,settings}.js`.
**Sapma:** Tarama sekmesinde başlık satırı yok, "bu başlığı tara" yalnız Katalog ayrıntısında. Elle tarama site `paused` iken de çalışır (admin açık eylemi).
**Elle kontrol (kullanıcı):** yabancidizi Tarama > tümünü şimdi tara (ilerleme, durdur, süre notu); Stranger Things ayrıntısı > bu başlığı şimdi tara.
**check.sh:** YESIL (6 adım), testler ~14.5 sn.
