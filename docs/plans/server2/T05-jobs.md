# T05: Kalıcı iş kuyruğu + admin notları (`control/jobs`, `control/notes.py`)

**Bağımlı:** T00. **Açar:** T08, T10, T11, T13, T15. **Oku:** README §5.3; `inventory/03 §5, §10` (bugün bellekte olan her şey).

## Amaç
Eski sistemde eşzamanlılığın tamamı süreç belleğindeki sözlük + thread idi; restart işi ve kullanıcıya verilen sözü siliyordu.
`server2`'de arka plandaki HER iş bu kuyruktan geçer ve restart'ta kaldığı adımdan sürer.

## Yapılacaklar
1. Migrasyon `020_jobs.py`: `jobs`, `admin_notes` (README §5.1). İndeksler: `(state, run_after, priority)`, `(site_id, state)`.
2. `control/jobs/store.py`: `enqueue` (aynı `key` `queued|running|waiting` iken yeni satır açmaz, var olanın id'sini döner; `done|failed`
   ise yeni koşu için satır sıfırlanır), `lease(owner, kinds, now)` (tek işlemde `BEGIN IMMEDIATE` ile seçip kiralar; sıra: `priority`
   azalan, `run_after` artan), `renew`, `complete`, `fail`, `retry(after_s)`, `wait(reason)`, `wake(key)`, `cancel(key)`, `checkpoint(step, payload)`,
   `recover(now)` (kirası dolan `running` -> `queued`), `active(site_id=None)`, `history(site_id, limit)`.
3. `control/jobs/worker.py`: yürütücü. N iş parçacığı (varsayılan 4) + 1 kira yenileyici + 1 zamanlayıcı döngüsü.
   - Kurallar: `llm=True` türlerden sistemde aynı anda 1, site başına 1 (DB üzerinden; iki süreç çalışsa bile bozulmaz).
   - İşleyici sonucu: `Done(result)`, `Retry(after_s, error)` (üstel bekleme çağırana ait, en çok `max_attempts` = 5 sonra `failed`),
     `Wait(reason)` (dış olay bekler; `wake` ile döner), `Fail(error)`. İşleyicide yakalanmamış hata = `Retry(60)` + hata metni (iz kısaltılmış).
   - `ctx`: `job`, `checkpoint`, `deadline` (iş başına süre sınırı, türe göre kayıtla verilir), `log(note)` (son 20 not `result`'a).
   - `drain(True)`: yeni iş kiralanmaz; `GET` durumunda süren iş sayısı görünür. Süreç kapanışında (`shutdown_hook`) drain + en çok 10 sn bekleme.
4. `control/jobs/schedule.py`: periyodik iş tanımı `every(kind, key, seconds, payload)` ve `daily(kind, key, hour)`; zamanlayıcı döngüsü
   vadesi gelenleri `enqueue` eder (kalıcı "son koşu" `settings` tablosunda `sched:<key>`). İlk kayıtlar: `cache_refresh` (900 sn),
   `jobs_gc` (günlük: 14 günden eski `done/failed` satırları siler).
5. `control/notes.py`: `add(level, text, *, site_id=None, ref_kind=None, ref_id=None)`, `unread()`, `list(limit, site_id=None)`,
   `mark_read(upto_id)`. K8'deki "admin'e bilgi notu" bunun üstünden gider.
6. Admin/ops uçları (`app/admin/routes_jobs.py`): `GET /api/control/jobs?state=&site=`, `GET /api/control/jobs/active`,
   `POST /api/control/jobs/{id}/cancel`, `POST /api/control/jobs/{id}/retry`, `POST /api/control/drain {"on": bool}`, `GET /api/control/drain`,
   `GET /api/control/notes?unread=1`, `POST /api/control/notes/read`. Yalnız yerel ağ (auth yok; kapalı LAN kuralı aynı).
7. `main.py` kayıtları: açılışta `recover` + yürütücü başlat; kapanışta drain.

## Kapsam dışı
İş türlerinin işleyicileri (kendi görevlerinde), admin arayüzü (T10).

## Kabul
- Aynı `key` ile iki `enqueue`: tek satır. İş `done` olduktan sonra aynı `key` yeniden sıraya girer.
- İki yürütücü iş parçacığı aynı işi iki kez kiralayamaz (eşzamanlı test).
- **Restart devamı:** adım 2'de `checkpoint` yapan iş, kirası düşürülüp yeniden çalıştırıldığında adım 2'den sürer; `attempts` korunur.
- `llm=True` iki iş aynı site için aynı anda çalışmaz; farklı sitelerin llm işleri de aynı anda çalışmaz (sistemde 1).
- `Retry` 5 denemeden sonra `failed`; `Wait` + `wake` ile iş yeniden çalışır.
- `drain(True)` iken sıradaki iş kiralanmaz, süren iş biter; `drain(False)` sonrası sıra işler.
- `daily`/`every` zamanlayıcısı vadesi gelen işi bir kez sıraya koyar; restart sonrası aynı gün ikinci kez koymaz.
- Not ekleme/okundu işaretleme.
- Kuyruk durumu için süreç belleğinde sözlük yok (kod incelemesi: durum yalnız DB'den okunur).
