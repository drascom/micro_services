# AUDIT — harness: LLM'e yanlış bilgi / LLM'i atlama / yanlış ölçüm (salt-okuma, 2026-10-04)

Kaynak: `data/diziflix2.db` (llm_calls 219 çağrı, 03-04.10; stage_messages), `data/llm/last/200-219.txt`, kod (server2).
Kapsam dışı (başka worker'lar): js akış, A6 alternatif hükmü, A6 LLM incelemesi, crawl aday toplama.

## Özet tablo (llm_calls + stage_messages)

| site/aşama | LLM çağrısı | şemada ret (biçim düzeltmesi) | hakem: LLM ezildi / ikisi de geçmedi | Yardım gerekiyor | not |
|---|---|---|---|---|---|
| trdiziizle A1 | 15 | 0 | 0/0 | 1 | `target_one_cluster` 2 LLM turunu reddetti |
| trdiziizle A3 | 22 | 0 | 0/1 | 2 | biri "tüm denetimler geçti" diyen Yardım |
| trdiziizle A5 | **119** | **13 (13)** | 0/0 | 4 | **107/119 çağrı A6→A5 geri dönüş döngüsünde** (53 dönüş) |
| trdiziizle A6 | 0 | - | - | 5 | 53 kez "Sorun oynatıcı sayfasında: A5 yeniden" |
| yabancidizi A2 | 8 | 0 | 0/0 | 2 | `samples_count>=2` (artık "onaylı liste" ile düzeltilmiş) |
| yabancidizi A3/A4 | 1/3 | 0 | 2/0 | 0 | sistem tahmini LLM'i puanla/oranla ezdi |
| yabancidizi A5 | 29 | 0 | 0/5 | 5 | `dizi/dizi` 404 örnekler + boş `page` incelemesi + `en az 2 örnek` |
Ret nedeni dağılımı (A5 trdiziizle, 13): `extract must be list` 6, `type must be auto/hls/mp4` 7, `yaml değil` 6, `fetch` bool/'iframe' 2, `follow` bool 1, `extract[].selector` 2 (çakışan sayımlar).

## Bulgular

- [HIGH] `stages/a6_sources.py:444-453` + `stages/a5_player.py:273-318` — A6 başarısızsa A5'i **sınırsız** geri çağırıyor; A5 ölçümü yalnız "sayfada aday var" (`candidate_ratio`), oynamayı ölçmüyor → A5 her seferinde "Geçti", A6 yine düşüyor. — trdiziizle 03.10 22:40→23:35 ve 04.10 17:58-18:01: 53 dönüş, A5'in 107 çağrısı (~369k girdi + 983k önbellek token). — Öneri: dönüş sayısını `failed_patch` başına 1 ile sınırla; 2.'de A5'i değil kullanıcıyı/A6-LLM'i çağır; A5 dönüş turunda geçme ölçütü "A6'nın düşen örneği artık oynuyor" olsun (`repair_ok` benzeri).
- [HIGH] `stages/a5_player.py:177-207` (`compare_by_playback`) — geri dönüş turunda yalnız LLM ↔ keşif karşılaştırılıyor; **önceki çalışan kural** yarışa girmiyor ve "hiçbiri oynamadı" olsa bile LLM yaması kabul ediliyor. — 04.10 18:00:07 LLM kuralı 1/1 oynadı, A6 2/3 oynadı; dönüşte yeni LLM kuralı 0/1 → "hiçbiri oynamadı, LLM önerisi ölçülüyor" → Geçti; çalışan kural ezildi. — Öneri: mevcut (geçmiş) A5 kuralını da oynat; oynayanı tut, hiçbiri oynamazsa "geçti" deme, LLM'e oynatma sonucunu verip tur aç.
- [HIGH] `stages/prompts_read.py:221-225` — ilk okuma (T23, A5'in asıl yolu) istemine `player_page extract rules` (`player_page.schema_notes()`) eklenmiyor; `full()` (`prompts_a4_a7.py:79`) ekliyor. LLM `extract` kuralının anahtarlarını bilmiyor. — llm/last/218: `extract: [{type: iframe, selector: ...}, {type: script_regex,...}]`, `fetch: "iframe"`; 13 şema reddinin kaynağı. — Öneri: aynı satırı read_prompt'a ekle (1 satır).
- [HIGH] `stages/engine.py:571` + `prompts_read.py:253-256` — okuma kipinde biçim düzeltmesi `format_fix(stage, err)` çağrılıyor, `LAST_REPLY_TYPES` geçilmiyor → hatalı çözücünün tam şeması verilmiyor; genel örnek `type: iframe` gösteriliyor; hata 400 karakterde kesiliyor ("(got no"). — llm/last/219: düzeltme istemi yalnız iframe örneği içeriyor, LLM yine `type: iframe` extract yazdı. — Öneri: `prompts_a1_a3.format_fix`'teki çözücü şeması bloğunu okuma kipine de koy; hata listesini satır satır, kesmeden ver.
- [MID] `core/play/resolvers/player_page.py:253-303` (`normalize`) — `fetch: true/false/"iframe"` ve `extract[].selector` (css takma adı) normalize edilmiyor. — 03.10 23:01:41 `fetch must be str (got bool)`, 23:10:23 `unknown key 'selector'`, call 219. — Öneri: `fetch` bool/geçersiz → kaldır (varsayılan http); `selector`→`css`, `{type: iframe, css}`→`{css, attr: src}`.
- [MID] `stages/investigate.py:45-55` + `core/play/inspect.py:197-202` — örnek sayfa yoksa tezgah `Bench(base_url, "")` boş HTML ile kuruluyor; `find in page` sessizce "0 eşleşme", `show_script` "betik sırası 0..-1" dönüyor; LLM sayfanın boş olduğunu bilmiyor. — yabancidizi 04.10 17:33:07-17:33:24: 11 adım boş sayfada (A5 inceleme 1-3). — Öneri: `page` boşsa "page boş (örnek yok); önce fetch, sonra in: r<N>" diye reddet/bilgi ver.
- [MID] `stages/prompts_read.py:213-215` — motorun A6 geri dönüş metni "Admin hint (follow it)" başlığıyla veriliyor; içerik (`ara sayfada çerçeve yok`) A6'nın tanısı, A6'nın oynatma izi (denenen akış URL'leri, HTTP kodları, hangi örnek oynadı) ve başarısız eski kural (`failed_patch`) istemde yok. — llm/last/218 "Admin hint" bloğu; A6 2/3 oynarken bile aynı metin. — Öneri: "Sonraki aşamanın ölçümü" başlığıyla A6 örnek tablosu + `failed_patch` + kodları ver; "admin" etiketi yalnız kullanıcı ipucuna.
- [MID] `stages/engine.py:787-788` (`referee`) — "ikisi de geçti, aynı sonuç" iken `score` farkıyla sistem tahmini LLM'i eziyor; aynı sonuçta puan farkı anlamsız. — yabancidizi A3 04.10 17:22:44 "(9, 5.0) > (8, 4.0)". — Öneri: aynı sonuçta LLM'i al (ilke: LLM karar verir), puanı yalnız sonuç farklıysa aday kartında göster.
- [MID] `stages/a4_episodes.py` ölçümü — bölüm URL'si hiç getirilmeden geçiyor; "17 bağlantı başka diziye benziyor (`dizi/dizi/...`)" uyarısıyla Geçti → A5 3 örnekte 404. — yabancidizi 04.10 17:23:06 (A4) → 17:23:23 (A5 404'ler); kullanıcı elle düzeltti. — Öneri: ölçümde 1 bölüm URL'sini GET et; 4xx → geçmez, kod LLM'e gider.
- [MID] `stages/a5_player.py:308` + `explain.py:26` — `samples_count>=2` (A5): tek oynatıcı sayfası ölçülebildiğinde LLM kuralı 1/2 oynasa da Yardım. — yabancidizi 04.10 17:36:16, 17:39:09. — Öneri: A2'deki "onaylı/oynayan tek örnek yeterli" kuralını A5'e uygula ya da eksik örneği A4 handoff'tan otomatik tamamla.
- [MID] `stages/a1_home.py:176-194` (`one_cluster`) — "kart adresleri tek kalıpta değil" deterministik kümesi LLM'in 2 turunu reddetti (19 kart). — trdiziizle 03.10 22:14:57. — Öneri: karışık kümeyi ret değil, LLM'e/aday kartına "karışık: dizi+bölüm" bilgisi olarak ver.
- [LOW] `stages/prompts_read.py:227-231` — A5 isteminde alan-kuralı grameri (poster/fallback) iki kez tekrarlı ve player için alakasız; site özetinde 10 satırlık A3 "Örnek düzenlendi" günlüğü "Admin hints" diye geçiyor (call 218, ~2k karakter). — Öneri: player'da FIELD_GRAMMAR'ı çıkar, brief'te örnek düzenleme olaylarını tek satıra indir.
- [LOW] `stages/engine.py:1098-` / `explain.py` — onarım kipinde `repair_ok` düşünce kullanıcıya "Yardım gerekiyor: tüm denetimler geçti" yazılıyor (trdiziizle A3 03.10 22:52:00); neden (bozuk örnek hâlâ posterli değil) görünmüyor. — Öneri: `repair_ok` notunu Yardım metnine ve LLM `last` ölçümüne ilk satır olarak koy.
- [LOW] `stages/a5_player.py:325-327` + `prompts_read.py:192-198` — A5 istemine "Handed over by the previous stage" olarak A4'ün **dizi** sayfası URL'leri gidiyor (call 218), bölüm URL'leri değil; A5 `consumes_handoff=False`. — Öneri: A5'e A4'ün ölçtüğü bölüm URL'lerini ver ya da bloğu çıkar.
- [LOW] Önbellek: her A5 dönüşü yeni oturum + ~20k karakterlik aynı istem (cached_tokens=0, call 209/211/213/216/218). — Öneri: döngü sınırı (1. bulgu) yeterli; ayrıca aynı `upstream_sig`+`hint` ile tekrar koşuda önceki sonucu geri getir.

## En az işle en büyük kazanç (ilk 5)
1. A6→A5 geri dönüşünü `failed_patch` başına 1 ile sınırla (a6_sources.py:444) — trdiziizle A5 çağrılarının ~%90'ı.
2. `player_page.schema_notes()` satırını read_prompt'a ekle (prompts_read.py:224) — 13 şema reddi/biçim düzeltmesi.
3. Okuma kipi format_fix'ine çözücü şemasını + kesilmemiş hatayı ver (engine.py:571).
4. `compare_by_playback`'e mevcut A5 kuralını kat; "hiçbiri oynamadı" = geçmez (a5_player.py:177).
5. normalize: `fetch` bool/geçersiz kaldır, `extract[].selector`→`css`; boş `page` incelemesinde açık uyarı (player_page.py:253, inspect.py:199).
