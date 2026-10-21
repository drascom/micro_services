# T11: Kademeli tarama (`control/crawl`)

**Bağımlı:** T08, T09, T10. **Açar:** T14. **Oku:** README §5.1 (`crawl_frontier`), §5.8; `NEW-SYSTEM-PLAN.md §3.4`;
`inventory/02 §2.2` (eski kota/geri çekilme rakamları, fikir için), T08 (`k0.py`), T09 (madde 6).

## Amaç
Bir sitenin tamamını tek seferde taramak pahalı ve yavaş. Tarama üç kademede, planlı ve LLM'siz yürür: öne çıkanlar hemen, tamamı
`backfill_days` (varsayılan 3) içinde, sonra günlük tazeleme. Her taranan sayfa aynı zamanda yapı kontrolüdür (imza).

## Yapılacaklar
1. Migrasyon `050_frontier.py`: `crawl_frontier` + indeks `(site_id, state, priority, next_at)`.
2. `control/crawl/frontier.py`: `add(site_id, url, page_type, *, priority, parent_url=None, origin=None, position=0)` (var olan satırı
   düşürmez; `priority` yalnız yükselir), `take(site_id, n, now)`, `done(...)`, `failed(...)` (geri çekilme merdiveni 1 sa / 6 sa / 24 sa / 7 gün,
   4. hatadan sonra `gone`), `reschedule(url, next_at)`, `stats(site_id) -> {discovered, done, failed, gone, blocked, by_type}`.
   Durumlar: `new, done, failed, gone`. Öncelik: K0 = 100, K2 = 50, K1 = 10.
3. `control/crawl/process.py`: `process(site_id, row) -> Outcome`: sayfa türüne göre tek sayfanın işlenmesi (hepsi harita adaptörleriyle):
   - `home`: bölümler -> kart adresleri frontier'e (`origin=("section", başlık)`, tür = bölümün `target`'ı), öncelik 100.
   - `listing`: satırlar -> `title` adresleri (`origin=("listing"|"genre", ad)`), sayfalama -> sonraki `listing` sayfası.
   - `title`: `title_rule` -> `upsert_title(origins=frontier origin'leri)`; dizi ise bölüm listesi (`where=title_page`: aynı sayfadan;
     `season_pages`: sezon adresleri frontier'e) -> `upsert_episodes`; film ise `set_movie_source`.
   - `season`: bölümler -> `upsert_episodes`.
   - `episode` (yalnız ana sayfa `target: episode` kartı için): `title_link` -> başlık adresi frontier'e, öncelik 100.
   - Kapı: yeni başlıkta `gate` kuralı (`series_verdict` en yeni + en eski bölüm / `item_verdict`); engelli -> `mark_blocked`, sayılır, görünmez.
     Kapı yoklaması site başına günlük bütçeli (varsayılan 60 sayfa); bütçe bitince başlık "kontrol bekliyor" olarak görünmez kalır, ertesi gün sürer.
   - İmza: sayfa türünün `meta.fingerprint`'i ile `fingerprint.compare`; sonuç `page_crawled` olayıyla yayılır.
4. `control/crawl/plan.py`: site başına günlük plan (iş `refresh`, günlük `refresh_hour`'da; ayrıca etkinleşince hemen):
   - **K0**: `home` frontier'e (öncelik 100). T08 `k0.py` ve T09 `k0_sources` geçici işleri KALDIRILIR, yerlerine bu yol geçer.
   - **K1**: A2 `passed*` ise `listings[]` adresleri frontier'e. Günlük kota = `ceil(kalan new / kalan gün)`; kalan gün =
     `backfill_days - geçen gün` (en az 1). Kota gün içine yayılır: `crawl` işi (anahtar `crawl:<site>`) her koşuda en çok 20 sayfa işler,
     kota dolana kadar kendini `run_after` ile yeniden sıraya koyar. `backfill_days` değişince kota yeniden hesaplanır.
   - **K2**: her gün `home` + her `listing`'in ilk sayfası yeniden `new`; "süren dizi" (son bölümü 30 günden yeni ya da `air_date` gelecekte)
     başlık sayfaları yeniden `new`; diğer başlıklar 14 günde bir. Tam aktarım bittikten sonra K1 kotası sıfır, yalnız K2 çalışır.
   - Sayfa işleme `crawl` işinde `get_many` ile paralel (limiter'a tabi), LLM yok (`llm=False`).
5. **Yapı değişikliği kuralı** (`control/crawl/drift.py`): aynı sayfa türünde art arda 5 sayfa imza tutmazsa (ya da 5 sayfada sıfır satır)
   o sayfa türü için tarama DURUR (frontier'de o türün satırları bekler), diğer türler sürer; `events.emit("page_crawled", ..., fingerprint_ok=False)`
   zaten yayılmıştır, ayrıca tek seferlik `structure_changed(site_id, page_type, evidence)` olayı (T14 sorun açar; T14 gelene kadar
   `admin_notes` uyarısı). Tek sayfalık sapma tetiklemez (yanlış alarm koruması). Durdurulan tür, o aşama yeniden `passed*` olunca devam eder.
6. Engellenme koruması: `FetchError("challenge")` ya da `http_429` art arda 3 kez -> site taraması 6 saat durur + `admin_notes` uyarı; kota o gün için düşmez.
7. Admin **Tarama** sekmesi (`static/js/crawl.js`, `routes_crawl.py`): üç satır (Öne çıkanlar / Tam aktarım / Günlük tazeleme) durum + sayılar;
   ilerleme çubuğu `aktarılan / keşfedilen`, "halka kapalı: N", "başarısız: N"; bugünkü kota ve kalan; `tamamı [3] günde` alanı (kaydet);
   `[şimdi tazele]`, `[taramayı duraklat/sürdür]`; duran sayfa türü varsa kırmızı satır + Harita sekmesindeki aşamaya bağlantı.
   Uçlar: `GET /api/control/sites/{id}/crawl`, `PUT .../crawl {backfill_days, refresh_hour}`, `POST .../crawl/refresh`, `POST .../crawl/pause|resume`.
   Siteler ekranındaki ilerleme sütunu `stats`'tan beslenir.

## Kapsam dışı
Yönlendirme (T12: yalnız `origins` doğru yazılır), sorun kaydı (T14), LLM.

## Kabul (fixture + sahte getirici, sahte saat)
- K0: site etkinleşince ana sayfa kartları frontier'e girer, başlıklar ve bölümleri katalogda; `target: episode` kartı `title_link` ile başlığa çözülür.
- K1: 30 başlıklı sahte sitede `backfill_days=3` -> 1. gün ~10 başlık işlenir (kota), 3. gün sonunda hepsi `done`; gün atlanırsa kota yeniden hesaplanır.
- Sayfalama: 2 sayfalı liste tam okunur; sayfalama döngüsü (aynı adrese dönen "sonraki") sonsuz döngüye girmez.
- K2: ertesi gün ana sayfa ve liste ilk sayfaları yeniden işlenir; yeni eklenen fixture bölümü `video_sources`'a girer; var olan satırların sağlığı korunur.
- Hata geri çekilmesi: 4 hata -> `gone`; arada `next_at` merdivene uyar.
- İmza: bir sayfa türünün işaretlemesi değiştirilmiş 5 fixture sayfası -> o tür durur, `structure_changed` bir kez yayılır, diğer tür sürer; tek bozuk sayfa durdurmaz.
- Engelli başlık kataloga görünür girmez, `stats.blocked` artar.
- `challenge` x3 -> site 6 saat durur, not düşer.
- **Restart:** `crawl` işi ortasında kira düşürülür; yeniden çalışınca işlenmiş sayfalar tekrar getirilmez.
- Tarama yolunda `core.llm` çağrısı yok (test: sahte LLM sayacı 0; ayrıca `control/crawl` içinde `core.llm` importu yok).
