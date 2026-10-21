# T14: Onarım, motor boşluğu, sistem sinyalleri, Sorunlar ekranı

**Bağımlı:** T09, T10, T11, T13. **Açar:** yok. **Oku:** README K8, K9, §5.6, §5.7; T13 (teşhis tablosu); `NEW-SYSTEM-PLAN.md §3.6, §4`;
`ARCHITECTURE-PROPOSAL.md §6.5, §6.7`.

## Amaç
Sorun kutusunu aşama motoruna bağlamak: teşhis bir sayfa türüne işaret ediyorsa o aşama **onarım kipinde** yeniden çalışır; ölçümü geçen
düzeltme kendiliğinden uygulanır ve admin'e bilgi notu düşer (K8); geçemeyen admin'e sorulur; motorun yapamadığı şey boşluk kaydı olur.
Ayrıca sistemin kendi tespit ettiği sorunlar ve tek "Sorunlar" ekranı.

## Yapılacaklar
1. **Onarım eylemi** (`control/problems/repair.py`): teşhis `page_type` verdiyse:
   - Ön koşullar: `core.llm.health.check` iyi; `budget.allowed(site_id, background=True)`; site için süren `stage` işi yok. Sağlanmıyorsa
     sorun `waiting_llm` (`next_at` = bütçe yenilenince / iş bitince), istemci sözü son tarihe kadar sürer.
   - `stages.actions.rerun(site_id, stage, repair=True, evidence=<sorunun kanıtı>, samples=<bozuk örnekler + meta.samples'tan 2 sağlam örnek>)`.
     Onarım kipi farkları: keşif yine önce çalışır (LLM'siz onarılabiliyorsa LLM çağrılmaz); prompt'a "şu örnekler bozuk, şu ölçüm bekleniyor"
     kanıtı girer; **geçme koşulu = bozuk örnekler artık geçiyor VE sağlam örnekler bozulmadı** (regresyon).
   - Aşama `passed*` -> yeni sürüm (`created_by=repair`, `note` = sorun id + sınıf) -> `settings.repair_autoapply` açıksa kendiliğinden etkin
     -> `admin_notes.add("info", "<site> · <aşama>: LLM düzeltmesi doğrulandı ve uygulandı (sürüm N)", ref=problem)` -> sorun `verifying`
     (T13 ölçümü). `repair_autoapply` kapalıysa: taslak bekler, sorun `waiting_user`, not `action`.
   - Aşama `needs_human` -> sorun `waiting_user`; soru kartı = aşama kartının aynısı (ipucu yazılınca aşama yeniden çalışır, sorun `acting`'e döner).
   - Aşama `engine_gap` -> madde 2.
   - Site başına aynı anda tek onarım; sorun başına en çok 2 LLM onarım koşusu (`llm_runs`), sonra `waiting_user`.
2. **Motor boşluğu** (`control/problems/gaps.py`): `capability_gaps` kaydı üç kaynaktan: aşamanın `engine_gap` sonucu, admin'in `mark_gap`
   eylemi, teşhisin `capability_missing` sınıfı. `key` = `provider:<host>` / `resolver:<neden>` / `embed:<tür>`; aynı `key` tek kayıt, `sites`
   listesi büyür. Bağlı sorunlar `waiting_capability` + izleyiciye `source_unavailable` ("motor desteği bekleniyor").
   **Kapanış:** açılışta `catalog_hash` (resolver tipleri + provider adları + tarif adlarının özeti) değiştiyse açık boşluklara bağlı sorunlar
   `open`'a, bağlı aşamalar `pending`'e döner ve yeniden çalışır; hâlâ aynı sınıfsa tekrar `waiting_capability`. Admin tek not alır
   ("yetenek kataloğu değişti, N bekleyen iş yeniden deneniyor"). Admin boşluğu elle `shipped` / `declined` işaretleyebilir.
3. **Sistem kaynaklı sorunlar** (`control/problems/system.py`, olay aboneleri + günlük tarama işi `problem_scan`):
   - `structure_changed(site_id, page_type)` (T11) -> site sorunu, `class=structure_changed`, doğrudan onarım eylemi (teşhis gerekmez).
     Çözülünce T11'de duran tür devam eder.
   - Eksik bilgi: görünür başlıklarda zorunlu alan boş (`title`, `poster`; TMDB de dolduramamış) -> site düzeyinde sayım; bir sitenin
     görünür başlıklarının %20'sinden fazlası afişsizse `class=missing_field`, `page_type=title` sorunu (tek tek başlık için sorun açılmaz).
   - Kırık bağlantı: frontier'de `gone` olan başlık/bölüm adresi -> kaynağı kaldırılır (`video_sources` satırı `disabled`), başlığın hiç
     kaynağı kalmadıysa görünmez; site genelinde `gone` oranı %30'u geçerse `class=links_gone` site sorunu.
   - Kaynaksız kalan görünür başlık (tüm kaynaklar `broken`) -> `title` kapsamlı sorun, T13 eylemleri.
   - `image_broken` tek tek sorun açmaz; site başına sayılır, eşik (görünür başlıkların %20'si) aşılınca tek sorun.
4. **Sorunlar ekranı** (`static/js/problems.js`, `routes_problems.py`):
   - Liste: durum rozeti (Türkçe: `seni bekliyor / LLM bekliyor / onarılıyor / doğrulanıyor / çözüldü, kullanıcı onayı bekliyor / onaylandı /
     motor desteği bekliyor / halka kapalı / vazgeçildi`), nerede (site · aşama ya da başlık · bölüm), ne (sınıfın Türkçe tek satırı),
     bildiren (N profil / sistem), yaş. Sıra: `waiting_user` en üstte, sonra açıklar, sonra son 50 kapanan. Süzgeç: site, durum.
   - Satır açılınca: olay zaman çizgisi (`problem_events`), kanıt (örnek adres, ölçülen değerler), bağlı sorunlar, bağlı sürüm (fark bağlantısı),
     `waiting_user` ise soru kartı (T10 aşama kartı bileşeni yeniden kullanılır), eylemler: `[yeniden dene]`, `[kapat: halka kapalı]`,
     `[kapat: önemsiz]`, `[sürümü geri al]` (onarım uygulandıysa).
   - Ayrı küçük bölüm: "Motor boşlukları" (anahtar, başlık, etkilenen siteler, bekleyen sorun sayısı, `[eklendi] [yapılmayacak]`).
   - Uçlar: `GET /api/control/problems?state=&site=`, `GET /api/control/problems/{id}`, `POST .../retry`, `POST .../close {reason}`,
     `GET /api/control/gaps`, `POST /api/control/gaps/{id} {status}`. Üst şeritteki "senden bekleyen" ve "açık sorun" sayıları buradan.
   - Site sayfası Harita sekmesinde, o aşamaya bağlı açık sorun varsa kartta rozet + bağlantı.
5. Prompt eki (`prompts_repair.py`): onarım kipinde kanıt bölümüne bozuk örneğin kırpılmış HTML'i + eski kuralın o örnekte ne döndürdüğü +
   sağlam örnekte ne döndürdüğü girer; kurallar: "yalnız bozulan kuralı düzelt, çalışan alanları değiştirme".

## Kapsam dışı
Yeni resolver tipi / provider kodu yazmak (boşluk kaydı açılır, kod işi ayrı görevdir), istemci tarafı "sorun bildir" düğmesi.

## Kabul (fixture + sahte LLM, sahte saat)
- Uçtan uca: `player_not_found` sorunu -> A5 onarım kipi -> sahte LLM yaması -> ölçüm geçer -> yeni sürüm `created_by=repair` -> admin notu
  (`info`) -> sorun `verifying` -> `resolved` -> izleyiciye `source_found` -> 10 sn ilerleme -> `confirmed`.
- LLM'siz onarım: keşif yeni işaretlemeyi kendisi buluyorsa `llm_calls` artmaz, yine yeni sürüm + not.
- Regresyon: yama bozuk örneği düzeltiyor ama sağlam örneği bozuyor -> uygulanmaz, sorun `waiting_user`.
- `repair_autoapply=false`: sürüm etkinleşmez, not `action`, sorun `waiting_user`.
- Bütçe dolu: sorun `waiting_llm`; ertesi gün (sahte saat) kendiliğinden sürer.
- `engine_gap`: boşluk kaydı açılır, iki sitenin sorunu aynı `key`'e bağlanır, izleyiciye `source_unavailable`. `catalog_hash` değişince
  sorunlar `open`'a döner ve tek not düşer.
- `structure_changed` -> site sorunu -> onarım -> çözülünce T11'de duran tür yeniden işler.
- Afişsiz oran eşiği: 10 başlıktan 3'ü afişsiz -> tek `missing_field` sorunu; 1'i afişsiz -> sorun yok.
- Sorun başına 2 LLM onarım koşusundan sonra üçüncü yapılmaz.
- Uç testleri: liste süzgeçleri, `close`, `retry`, boşluk durumu değiştirme; `summary` sayıları tutarlı.
- `node --check` temiz.

## Elle kontrol (kullanıcı)
Sorunlar listesinin tek bakışta anlaşılırlığı, soru kartıyla ipucu verme, bilgi notlarının zilde görünmesi, sürüm geri alma.
