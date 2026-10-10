# T04b: İstemci API'si (`app/api`)

**Bağımlı:** T03, T04a. **Açar:** T13. **Oku:** README §5.2, §5.7; `API.md` (tamamı, kaynak gerçek budur); `docs/api-samples/*.json`;
`inventory/02 §3, §5, §6`; `server/app/routers/*` (ops* ve onboard* hariç), `server/app/{homelayout,rows}.py`, `server/app/sources/library.py`,
`server/tests/test_client_contract.py`, `docs/arch/home-and-api.md`.

## Amaç
Mevcut web/TV istemcisi, yalnız sunucu adresini 8091'e çevirerek `server2` ile çalışabilmeli (K10). Bu görev okuma tarafını ve oynatma
uçlarını taşır; sözleşme **bayt düzeyinde alan uyumlu** kalır (alan eklenebilir, çıkarılamaz, yeniden adlandırılamaz).

## Yapılacaklar
1. Router'lar (eski karşılıklarından kopya, `core.catalog` / `core.play` üstüne): health, profiles, boot, rows (`/api/row/<id>` dahil),
   detail kademeleri, streams, subtitles, progress, mylist, search, images (`/img`), catalog, stream-proxy, notifications, source-finder.
   `SourceAdapter`/`mock` kaynağı ALINMAZ (tek kaynak: kütüphane). Dev yardımcıları `?delay=`, `?fail=1` korunur.
2. `homelayout.py` + `rows.py` + kütüphane okuma (`sources/library.py`): kopya. Görünürlük kuralı K5 (yalnız `ready` başlıklar).
   Trend dolgusu sitelere **eşit pay** verir (round-robin; `trend_score` yalnız site içi sıralama): eski dengesizlik taşınmaz.
3. Detay isteğindeki "hydrate" tetikleri (eski `routers/detail.py:41-186` arka plan thread'leri): thread yerine iş kuyruğu kancası.
   `api` katmanı `control.jobs.enqueue`'yu doğrudan çağırabilir (katman kuralı izin verir). T05 hazır değilse uç yine çalışır (kanca no-op).
4. Oynatma uçları:
   - `GET /api/streams/{id}`: `core.play.sources.streams(...)`; akış boşsa `finder` alanı `control.problems.client_state(cid, episode_id,
     profile_id)`'den (T13; o gelene kadar `{"state": "not_found"}` döndüren yer tutucu fonksiyon `app/api/_finder.py` içinde, T13 değiştirir).
   - `POST /api/playback-report`: `sources.feedback(...)` + `events.emit("playback_failed" | "playback_ok", ...)`. Yanıt biçimi aynı.
   - `POST/PUT progress`: yazma + `events.emit("progress", ...)`.
   - `GET /api/source-finder/{id}`, `GET /api/notifications`, `POST /api/notifications/read`: biçim aynı; `notifications` tablosu okuma/yazma
     `core/catalog/notify.py` içinde (`add(profile_id, kind, canonical_id, episode_id, payload)`, `unread(profile_id, since)`, `mark_read`).
     `source-finder` durumu T13'ten; o gelene kadar `idle`.
5. Arama: yerel katalog araması (Snapshot `search_index`) bu görevde. Canlı site araması (`search_all`) T09'da eklenir; uç şimdiden
   "canlı kaynak" için enjekte edilebilir kanca bırakır (`app/api/_search_live.py`, varsayılan boş liste).
6. Sözleşme koruması: `tests/test_client_contract.py` (eski testten uyarlanır): `docs/api-samples/*.json` şemasına göre her ucun yanıtında
   beklenen alanlar ve tipler var mı. Tohum verisi testte `core.catalog.write` ile kurulur (fixture.json ALINMAZ).
7. `tools/gen_api_samples.py` karşılığı ALINMAZ (örnekler eski sunucudan üretilmiş referanstır; değiştirilmez).

## Kapsam dışı
Admin uçları (T10+), sorun mantığı (T13), canlı arama (T09), istemci kodu.

## Kabul
- Sözleşme testi yeşil: boot, rows, row, detail, streams, progress, mylist, search, notifications, source-finder, playback-report için
  örnek dosyalardaki her alan yanıtlarda mevcut ve tipi aynı.
- Hata zarfı biçimi aynı (bilinmeyen id 404 `{"error":{"code","message"}}`).
- Kaynağı olmayan başlık boot/rows'ta görünmez; kaynağı `unknown` olan görünür.
- Trend satırı iki sahte siteyle kurulduğunda ilk 10 öğede iki site de yer alır.
- `playback-report` başarısızlıkta `playback_failed`, başarıda `playback_ok` olayı yayar; `progress` olayı konumla yayılır.
- Geçersiz/süresi dolmuş `attempt_token` 400.
- `api` içinde eski yönetim modülü adı geçmez.

## Elle kontrol (kullanıcı, istemci turunda)
İstemcinin sunucu adresi 8091'e çevrilince ana ekran, detay, oynatma. Bu görevde yapılmaz.
