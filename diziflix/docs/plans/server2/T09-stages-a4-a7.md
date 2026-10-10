# T09: A4-A7: bölümler, oynatıcı, kaynaklar, arama + kapı

**Bağımlı:** T03, T08. **Açar:** T11, T14. **Oku:** README §5.5-5.8; T08 (motor); `inventory/03 §1` (oynatılabilirlik, dizi envanteri,
arama satırları); `server/app/routers/onboard_sandbox.py:2238-2548, 2550-2843, 2845-3175`; `server/app/library/search_all.py`.

## Amaç
Kalan dört aşama: dizinin bölümleri, bölüm/film sayfasındaki oynatıcı, adaydan akışa giden yol, arama ve telif kapısı. Sonunda eklenen
siteden bir bölüm gerçekten oynar.

## Yapılacaklar
1. **A4 episodes** (`a4_episodes.py`, `needs=(A3,)`): keşif = A3 örneklerindeki dizi başlık sayfaları (2-3) -> `outline.episode_links` ->
   `episodes.build_series_page` -> harita `pages.episodes` (`where`, `row_selector`, `fields`, `numbering`, gerekiyorsa `seasons`).
   Ölçüm: 2 dizide bölüm sayısı `> 0`; her bölümde `season`/`episode` sayı; numaralar tekrarsız; çok sezonlu görünen dizide (sezon menüsü
   varsa) ikinci sezon da okunuyor. Sitede hiç dizi yoksa (A3 tüm örnekleri `movie` dedi) aşama `skipped` (kendiliğinden, notla).
2. **A5 player** (`a5_player.py`, `needs=(A3,)`, dizi varsa A4): keşif = 3 örnek oynatıcı sayfası (2 bölüm + 1 film, hangisi varsa) ->
   `core.extract.player.player_candidates` -> önerilen resolver öğeleri; `url_pattern`; `title_link` (bölüm sayfasında başlık sayfasına giden
   bağlantı: A3 `url_pattern`'ini eşleyen ilk bağlantı). Ölçüm: 3 örnekte `>= 1` aday (yer tutucu/engelli örnek paydadan düşer, yerine yedek
   örnek alınır); `title_link` ana sayfada `target: episode` bölümü varsa zorunlu.
3. **A6 sources** (`a6_sources.py`, `needs=(A5,)`): keşif yok (harita parçası yok; iş ölçümdür). Ölçüm = `core.play.follow` 3 örnekte:
   akış çözüldü + `quick_check` reddetmedi + `streamprobe.summarize` süresi makul (beklenen süre bilinmiyorsa `>= 10 dk`).
   Sonuç ayrıştırması, aday bazında:
   - host hiçbir provider'da yok -> `provider_match.match` (LLM'siz kuru çalıştırma): `use_provider|add_host` önerisi kendiliğinden
     uygulanır (tarif host genişletmesi, `recipes.update_problems` kapısıyla) ve yeniden ölçülür;
   - `new_recipe` önerisi: LLM'e TEK çağrı (kanıt: oynatıcı sayfası kırpılmış gövdesi + `match.page` özeti + tarif şeması kısa hali);
     yanıt tarif yaml'ı -> `validate_recipe` -> `follow` ile ölçüm -> geçerse `save_recipe` + `admin_notes` (yeni provider tarifi eklendi);
   - `needs_code` / desteklenmeyen gömme türü (ör. tam bölüm barındıran video sitesi gömmesi) -> aşama `engine_gap`, `evidence`'ta host ve neden.
   **Nereye yazılır (K4):** "site bu provider'a nasıl varıyor" bilgisi (sitenin kendi oynatıcı sayfası, sekme, öznitelik, el sıkışma,
   ara istek) A5'e aittir ve haritada `pages.player.resolvers` parametresi olarak kalır; A6 yalnız host'a ait bilgiyi (provider tarifi)
   kütüphaneye yazar. A6 ölçümü sitenin kendi oynatıcı sayfasında takılıyorsa sonuç A5'e geri gönderilir (A5 onarım kipinde yeniden
   çalışır), kütüphaneye siteye özel tarif YAZILMAZ.
   Geçme eşiği: örneklerin `>= 2/3`'ü oynuyor. 1/3 ise `passed_warn` DEĞİL `needs_human` (K5: oynamayan içerik değersiz).
   Tarifler kütüphaneye aittir: tarif yaması başka sitelerin örneklerini bozmamalı; etkin diğer sitelerde o tarifi kullanan 1 örnek
   (`meta.player.samples`) yeniden ölçülür, bozulursa tarif geri alınır (`restore_recipe`).
4. **A7 search_gate** (`a7_search_gate.py`, `optional=True`):
   - Arama: keşif = `core.extract.search` form tespiti; katalogdaki bilinen bir başlıkla (K0'dan) sorgu; ölçüm = sonuç `>= 1` ve bilinen
     başlık bulundu. Arama yoksa alt bölüm atlanır.
   - Kapı: A5/A6 sırasında görülen yer tutucu örneklerden `blocked` kuralı önerisi (`placeholder_hint`, `frame_sources`); `gate.require`
     varsayılanı `player`, `probe` 2. Ölçüm: kural yer tutucu örneği yakalıyor, sağlam örnekleri yakalamıyor.
5. Arama çalıştırıcı `control/stages/search_run.py`: `search(site_id, query, limit=20)` (`search_generic.search` kopyası: netguard, 10 sn,
   3 MB, bellek önbelleği) ve toplu `search_all(query)` (eski `library/search_all.py` mantığı: arama yetenekli etkin sitelerde paralel,
   sonuçlar `title_rule` + `upsert_title` ile kataloga, dizi seviyesinde). `app/api/_search_live.py` kancasına bağlanır (T04b).
6. Oynatma hazırlığı: A6 `passed*` olunca K0'da eklenmiş başlıklar için bölüm envanteri ve kaynak satırları doldurulur
   (`upsert_episodes` / `set_movie_source`): iş `k0_sources:<site>` (T11 frontier'e taşır). Kapıdan elenen başlık `mark_blocked` + görünmez.
7. Prompt'lar (`prompts_a4_a7.py`): T08 kurallarıyla; A6 tarif prompt'unda tarif şeması ve "tarif host'a aittir, site adı/yolu içermez" kuralı.

## Kapsam dışı
Tam aktarım ve tazeleme (T11), çalışma zamanı onarımı (T14), yeni resolver TİPİ ya da provider KODU yazmak (motor boşluğu: kullanıcıya
raporlanır, orkestratör ayrı görev açar).

## Kabul (fixture + sahte provider + sahte LLM)
- `fakesite`: A4-A7 LLM'siz `passed`; fixture dizisinin bölümleri `video_sources`'ta; `core.play.sources.streams` bir bölüm için akış döner.
- Sezon sayfaları ayrı varyant (`fakesite_b`): `where: season_pages` bulunur, iki sezon okunur.
- A5: aday olmayan örnek + ipucu ("oynatıcı şu sekmenin altında") -> LLM turu -> `passed`.
- A6: bilinmeyen host + `add_host` önerisi -> tarif host genişletmesi kendiliğinden, LLM çağrısı yok, `admin_notes` satırı var.
- A6: `new_recipe` -> LLM tek çağrı -> geçerli tarif kaydedilir; geçersiz tarif kaydedilmez, aşama `needs_human`.
- A6: tarif değişikliği başka sitenin örneğini bozuyorsa geri alınır (iki sahte siteyle test).
- A6: desteklenmeyen gömme -> `engine_gap`, kanıtta host; aşama `passed` görünmez.
- A6: 1/3 oynuyor -> `needs_human`.
- A7: arama bilinen başlığı bulur; yer tutucu kuralı engelli örneği yakalar, sağlamı yakalamaz; arama olmayan sitede aşama `passed` (arama `skipped` notuyla).
- Sadece film içeren sahte sitede A4 kendiliğinden `skipped`, A5/A6 film sayfasıyla çalışır.
- `search_all`: iki sahte sitede paralel, sonuçlar katalogda tek kanonik başlık.
