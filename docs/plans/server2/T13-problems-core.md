# T13: Sorun çekirdeği + istemci sözü (`control/problems`)

**Bağımlı:** T03, T04b, T05. **Açar:** T14. **Oku:** README §5.2, §5.7; `ARCHITECTURE-PROPOSAL.md §6.2, §6.3, §6.8` (durum makinesi,
teşhis tablosu, iki gerçek olayın işlenişi: bu görevin tasarım kaynağı); `inventory/02 §4, §5`; `API.md` (finder, notifications, source-finder).

## Amaç
Eski sistemde aynı sorun dört ayrı yoldan, ortak durum olmadan ele alınıyordu ve istemciye verilen "aranıyor" sözünün arkası boş
kalabiliyordu. `server2`'de her çözülmemiş durum **tek bir sorun kaydıdır**: tek durum makinesi, LLM'siz teşhis, ölçümle kapanış, izleyene
bildirim. Bu görev çekirdeği ve LLM'siz eylemleri yazar; LLM onarımı T14'te.

## Yapılacaklar
1. Migrasyon `070_problems.py`: `problems`, `problem_events`, `problem_watchers`, `capability_gaps` (README §5.7).
2. `control/problems/store.py`: `open_or_get(fingerprint, **fields) -> (problem, created)`, `watch(problem_id, profile_id, canonical_id, episode_id)`,
   `transition(problem_id, to_state, *, actor, kind, note="", data=None, **fields)` (izinli geçiş tablosu kodda tek yerde; izinsiz geçiş hata),
   `event(...)`, `list(filters)`, `get(id)` (olaylar + izleyiciler). Parmak izi: `episode:<cid>:<episode_id>`, `title:<cid>`, `site:<site>:<page_type>:<class>`, `host:<host>`.
3. `control/problems/intake.py` (olay aboneleri, açılışta `subscribe`):
   - `stream_missing` -> bölüm sorunu aç/izleyici ekle, `deadline_at = now + settings.problem_deadline_s`, kanıt = olay yükü.
   - `playback_failed` (cihaz kodları `unsupported, decode, autoplay, aborted, offline` hariç) -> aynı; `resolved` durumundaki sorun varsa `open`'a döner (`attempts+1`).
   - `playback_ok` / `progress` -> doğrulama (madde 7).
   - `stream_diag` -> açık soruna kanıt ekler; `learn` içeren hüküm teşhisi hızlandırır.
   - `image_broken` -> `title` kapsamlı sorun (`class=image_broken`).
   Her biri işi sıraya koyar: `enqueue("problem", f"problem:{id}")`.
4. `control/problems/diagnose.py`: `diagnose(problem) -> {class, layer, page_type, action, evidence}`; LLM yok. Girdi: kaynak satırları için
   `core.play.follow` (zorla, önbelleksiz) + gerekirse `quick_check` / `streamprobe.summarize`. Karar tablosu (ölçülen kanıt -> sınıf/katman/eylem):

   | Kanıt | `class` | `page_type` | Eylem |
   |---|---|---|---|
   | Sayfa getirilemedi / challenge | `page_unreachable` | - | 1 kez yeniden dene; tekrarında site kapsamlı soruna bağla |
   | `blocked` kuralı / kapı hükmü | `not_public` | - | `mark_blocked`, `closed_not_public` |
   | Sayfa geldi, aday yok, oynatıcı izi var | `player_not_found` | `player` (A5) | onarım (T14) |
   | Sayfa geldi, aday yok, iz de yok | `no_player` | - | kapı hükmü: `closed_not_public` |
   | Aday var, host'u provider tanımıyor | `unknown_host` | `sources` (A6) | `provider_match` (LLM'siz); tutmazsa onarım (T14) |
   | Desteklenmeyen gömme türü | `capability_missing` | `sources` | `waiting_capability` (T14 boşluk kaydı) |
   | Aday + provider var, akış yok | `provider_changed` | `sources` | onarım (T14) |
   | Akış var, yoklama reddi Referer/vekille düzeliyor | `host_needs_headers` | - | `hostrules.learn`, doğrula |
   | Akış var, yoklama reddi düzelmiyor | `stream_refused` | `sources` | onarım (T14) |
   | Akış var, yoklama iyi, süre uyuyor | `client_side` | - | kapat (not: "sunucu tarafı sağlam"); 2 ayrı motorda tekrar ederse `stream_refused` |
   | Akış var, süre belirgin kısa | `wrong_media` | `player` | sıradaki adayı dene; yoksa onarım (T14) |

   Reddetme kararı veren her satır, dayandığı ÖLÇÜMÜ `evidence`'a yazar (heuristik ad/host tahminiyle reddetme yok).
5. `control/problems/actions.py` (LLM'siz eylemler): `retry_sources` (bilinen kaynakları zorla çöz; `broken` olanlar dahil),
   `search_elsewhere` (diğer etkin sitelerde `search_all` + kaynak ekleme; yalnız bölüm/başlık kapsamı), `learn_host_rule`, `provider_dry_run`,
   `next_candidate`. Sıra: `retry_sources` -> teşhisin eylemi -> `search_elsewhere`. T14 gelene kadar "onarım" eylemi `waiting_user` + admin notu üretir.
6. `control/problems/run.py`: iş türü `problem` işleyicisi (`llm=False`); `open -> diagnosing -> acting -> verifying` adımları `checkpoint` ile.
   **Doğrulama:** sorun yalnız ölçümle `resolved` olur: zorla çözümleme akış verdi + `quick_check` reddetmedi + (beklenen süre biliniyorsa) süre uyuyor.
   Deneme sınırı 3 (sonra `exhausted`); bekleme `next_at` ile (1 dk, 10 dk). Son tarih geçince sorun kapanmaz ama istemci durumu `not_found` olur.
   **Bölüm -> site yükseltme:** aynı site + sınıf + `page_type` için 2 farklı bölüm/başlık sorunu açıksa bir site sorunu açılır, diğerleri
   `parent_id` ile bağlanır ve onun sonucunu bekler; site sorunu `resolved` olunca bağlı sorunlar `verifying`'e alınır.
7. `control/problems/confirm.py` (K9): `resolved` sorun için izleyen profil aynı içerikte `settings.confirm_seconds` (10) saniye ilerlediyse
   (`progress` olayındaki konum, `resolved_at`'ten sonraki ilk konuma göre >= 10 sn arttı) ve arada `playback_failed` gelmediyse -> `confirmed`.
   İzleyicisi olmayan (sistem kaynaklı) sorunlar `resolved`'da kalır; 7 gün sonra kendiliğinden `confirmed` sayılmaz, `closed` olur.
8. `control/problems/notify.py`: `resolved` -> her izleyiciye `source_found` (eski yük biçimi aynen); `exhausted`/`closed_not_public`/
   `waiting_capability` -> `source_unavailable` (yük: neden kısa metni); `notified_at` ile tekrar gönderilmez. Bildirim yazımı `core.catalog.notify`.
9. İstemci eşlemesi: `client_state(cid, episode_id, profile_id) -> {"state": ...}` ve `finder_status(cid, episode_id) -> {state, steps, updated_at}`
   (README §5.7 kuralı; `steps` = son 6 `problem_events` satırının `{name, ok, ms, note}` biçimine çevrimi). T04b'deki yer tutucular bunlarla değiştirilir.
10. Açılış kurtarması: `diagnosing|acting|verifying` durumundaki sorunların işi kuyrukta yoksa yeniden sıraya konur (iş kuyruğu zaten
    kirayı kurtarır; bu yalnız "iş satırı hiç yok" durumunu kapatır).

## Kapsam dışı
LLM onarımı, motor boşluğu kaydı, Sorunlar ekranı, sistem kaynaklı yapı sinyalleri (hepsi T14).

## Kabul (sahte getirici/provider, sahte saat)
- Akış yok -> `stream_missing` -> tek sorun + izleyici; aynı bölüm için ikinci istek yeni sorun açmaz, izleyici ekler. `finder.state = searching`.
- Host Referer istiyor senaryosu: teşhis `host_needs_headers`, kural yazılır, `resolved`, izleyiciye `source_found`; LLM çağrısı 0.
- Yer tutucu sayfa: `closed_not_public`, `blocked_pages` satırı, `source_unavailable` bildirimi.
- Aday yok ama oynatıcı izi var: `player_not_found`, `page_type=player`; (T14 yokken) `waiting_user` + admin notu; `finder.state = not_found`.
- İki bölüm aynı sınıfta -> site sorunu açılır, bölümler bağlanır.
- Son tarih: `deadline_at` geçince sorun açık kalsa da `client_state` `not_found`.
- K9: `resolved` + 10 sn ilerleme -> `confirmed`; arada hata gelirse `open`, `attempts` artar; 9 sn ilerleme onaylamaz.
- `resolved` yalnız ölçümle: config/kural değişti ama çözümleme hâlâ boş -> `resolved` olmaz.
- **Restart:** `acting` ortasında süreç "ölür" (kira düşürülür) -> sorun kaldığı adımdan sürer; izleyici ve son tarih korunur.
- İzinsiz durum geçişi istisna verir (test); sorun durumu için bellekte sözlük yok.
- `source-finder` ve `notifications` uçlarının yanıt biçimi sözleşme testinden geçer.
