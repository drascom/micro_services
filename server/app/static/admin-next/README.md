# admin-next: Diziflix yeni admin prototipleri

Canlı sistemden bağımsız, build/CDN yok. Dosyaya çift tıkla aç. Veri js içinde sahte; canlı tarama, drift, LLM heal, geri alma simüle edilir.

- **concept-a.html "Kokpit"**: tek ekran. Üstte canlı iş çubuğu, altında kaynak başına sağlık kartı (son 24 tarama şeridi, hız, süre sparkline'ı), 12 saatlik lane zaman çizgisi (heal = ◇), LLM düzeltme kartları (önce/sonra, geri al), kısa katalog.
- **concept-b.html "Olay defteri"**: her tarama ve heal bir olay. Sol: kaynak süzgeçleri (sparkline'lı) + zaman çizgisi feed; sağ: seçilen olayın detayı (metrikler, süre/öğe trendi, selector diff, geri al). Telefonda detay alttan sheet.

## Bağlanacak gerçek veri (alan listesi)
- Tarama/run: `source`, `started_at`, `finished_at|duration_s`, `items_count`, `pages_count`, `status` (ok/partial/failed), `error`
- Canlı iş: `job_id`, `source`, `phase` (scan/drift/heal), `pages_done`, `pages_total`, `items_done`, `rate` (öğe/sn)
- Heal: `source`, `field`, `selector_before`, `selector_after`, `model`, `attempts`, `duration_s`, `outcome` (applied/failed/rolled_back), `run_id`, `reason`
- Sağlık: `source`, `drift_flag`, `cooldown_until`, `last_error`, `consecutive_failures`
- Katalog: `source`, `item_count`, `recent_items[]` (`title`, `added_at`)
- Geri alma: heal id ile `POST rollback` (selector_before geri yazılır)

## Öneri
**B (Olay defteri)**: "ne oldu, neden, işe yaradı mı" sorusu her zaman bir olaya iner; heal önce/sonra ve geri alma detay panelinde doğal yer bulur, telefonda da iyi çalışır. A ise "şu an sağlıklı mı" sorusuna daha hızlı cevap verir; B'nin üstüne A'nın sağlık şeridi eklenirse en iyisi olur.
