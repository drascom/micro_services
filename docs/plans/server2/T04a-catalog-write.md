# T04a: Katalog tabloları ve yazma (`core/catalog`)

**Bağımlı:** T00. **Açar:** T04b, T08, T11, T12. **Oku:** README §5.1, §5.8; `inventory/02 §1-3, §7`; `server/app/db.py`,
`server/app/library/{ingest,normalize,identity,enrich,tmdb,seasons,purge,categories,catalog}.py`, `server/app/images.py`,
`docs/arch/library-ingest.md`, `server/CANONICAL.md`.

## Amaç
Eski sistemde kataloga yazmanın tek tam yolu "tüm siteyi tara" idi (`_ingest_source`); tek kayıt yolu eksikti. `server2`'de tarama
frontier'den gelir, bu yüzden katalog **küçük, tam ve idempotent yazma fonksiyonları** sunar. Kimlik birleştirme ve TMDB mantığı kopyadır.

## Yapılacaklar
1. Migrasyon `010_catalog.py`: README §5.1'deki katalog tabloları (eski `SCHEMA`'dan; alınmayanlar hariç) + `source_origins`.
   `source_items.normalized` içindeki eski durum işaretleri (`_series_inventory`, `_gate`, ...) KULLANILMAZ; durum frontier'de (T11).
2. `core/catalog/textnorm.py`: `clean_title`, `clean_series_slug`, `fold`, `is_placeholder_image` (`normalize.py:522, 482, 458, 43`).
   `@register` site fonksiyonları ve yaml `normalize:` motoru ALINMAZ: alanlar site haritasından adlarıyla gelir.
3. `core/catalog/identity.py`: `choose`, `bind`, `merge` (kopya) + `identity_reviews`.
4. `core/catalog/write.py`: README §5.8 fonksiyonları.
   - `upsert_title`: `rec` doğrulanır (zorunlu: `key, type, title, source_url`); `source_items` upsert (PK `site_id, key`); `identity.choose`;
     `merge_canonical` (kopya, `ingest.py:103`; kaynak önceliği sabiti yerine "TMDB > en yeni kaynak" basit kuralı, `field_provenance`
     yazımı korunur); `added_at` ilk girişte şimdi; yer tutucu afiş elenir; `origins` -> `source_origins` (aynı çağrıda görülmeyen eski
     origin satırları silinmez, `seen_at` güncellenir).
   - `upsert_episodes`: `video_sources` satırları (`kind=episode`, `locator`=bölüm sayfası, `resolver=page`); kimlik `vs_<sha24>` eski
     kuralla; var olan satırın sağlık alanlarına dokunmaz; `locator` değişirse saklı çözüm sıfırlanır. Taze `blocked_pages` adresi yazılmaz.
   - `set_movie_source`, `mark_blocked` (`blocked_pages`; `gate.store/record/forget` karşılığı), `remove_title` (`purge.purge_source_items`:
     kullanıcı verisi kalır), `delete_site(site_id)` (`purge.delete_site`).
   - Hepsi `db.transaction()` içinde, yeniden çağrıda aynı sonucu verir.
5. `core/catalog/lists.py`: `library_lists` yazma yardımcıları `set_list(list_id, canonical_ids)` (sil + yaz, alias çözülür), `list_id(role, site_id, category=None)`
   ve rol sabitleri (`collections.py:23-42`). Kural uygulaması T12'de; burada yalnız ilkel.
6. `core/catalog/categories.py`: `home_categories` CRUD + `ensure_system` (kopya).
7. TMDB: `tmdb.py`, `enrich.py` (`enrich_items`, `apply_result`, `fill_norm`, `backfill`), `seasons.py`. Arka plan thread'i (`enrich.schedule`)
   yerine **iş**: `enqueue("tmdb", f"tmdb:{cid}", ...)` (T05 yoksa testte sahte kuyruk; arayüz `core.catalog.hooks.enqueue_tmdb` adında
   enjekte edilebilir bir çağrı olur, `control` açılışta bağlar: `core` `control`'ü import etmez). Ayarlar `settings` tablosundan
   (`tmdb_auto`, `tmdb_types`). Anahtar asla loglanmaz.
8. `core/catalog/images.py`: Pillow yer tutucu + uzak afiş vekili + önbellek (`images.py`). İzinli host listesi: TMDB host'ları + etkin
   sitelerin `base_url` host'u + haritadaki afiş host'ları + `settings.image_hosts_extra` (env `REMOTE_IMG_HOSTS` yok).
   Kırık afişte davranış aynı (200 yer tutucu) ama `events.emit("image_broken", ...)` (router düzeyinde, kimlikle; 1 saatte aynı adres için bir kez).
9. `core/catalog/cache.py`: `Snapshot` + `refresh()`, `get()`, `refresh_soon()` (20 sn debounce; yazmalardan sonra çağrılır).
   APScheduler yok: periyodik yenileme T05 kuyruğuna bırakılır (bu görevde yalnız fonksiyon).
10. `core/catalog/availability.py`: `catalog.availability(providers)` (`ready|check_required|unavailable`).

## Kapsam dışı
İstemci router'ları ve `homelayout` (T04b), yönlendirme kuralı (T12), tarama (T11), bölüm envanteri zamanlaması.

## Kabul
- `upsert_title` iki kez aynı kayıtla: tek `library_items`, tek `source_items`; `added_at` değişmez.
- İki farklı siteden aynı başlık+yıl: tek kanonik id (kimlik birleştirme); belirsiz durumda `identity_reviews` satırı.
- `origins` yazılır; ikinci çağrıda yeni origin eklenir, eskisi durur.
- `upsert_episodes`: yeniden çağrıda sağlık alanları korunur; `locator` değişince saklı çözüm sıfırlanır; blocked adres yazılmaz.
- `remove_title` sonrası `progress`/`mylist` satırları durur, katalog satırı gider.
- Yer tutucu afiş `poster_url` olarak yazılmaz.
- TMDB anahtarı boşken yazma başarıyla biter, tmdb işi sıraya konmaz; anahtar hiçbir log/hata metninde yok (grep testi).
- `image_broken` olayı kırık afişte bir kez yayılır.
- Kod içinde site adı ve `@register` yok.
