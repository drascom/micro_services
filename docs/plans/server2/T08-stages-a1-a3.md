# T08: Aşama motoru + A1-A3 + öne çıkanlar (K0)

**Bağımlı:** T01, T02, T04a, T05, T06, T07. **Açar:** T09, T10 (birlikte), T11, T12, T14. **Oku:** README §5.3, §5.5, §5.6, §5.8;
`NEW-SYSTEM-PLAN.md §3.2, §3.4`; `inventory/03 §1` (kabul kriterleri tablosu), `inventory/01 §5`;
`server/app/routers/onboard_sandbox.py:2016-2147, 3188-3406` (ölçüm ve sertleştirme mantığı, fikir kaynağı).

## Amaç
Sistemin kalbi: bir adres verilir, site **aşama aşama** öğrenilir; her aşama kendi kanıtını, ölçümünü ve durumunu üretir; hata hangi
aşamadaysa orada durur ve insana sorar. Bu görev motoru, ilk üç aşamayı ve A3 sonrası ilk içerik aktarımını yazar.

## Yapılacaklar
1. Migrasyon `040_stages.py`: `site_stages`, `stage_messages`.
2. `control/stages/base.py`: `Stage` taban sınıfı, `Finding(patch, evidence, confidence)`, `Measurement(checks, samples, ok, warn)`,
   `StageCtx(site_id, map, site_ctx, hint, samples, fetch, deadline)` (README §5.6).
3. `control/stages/engine.py`: iş türü `stage` (`llm=True`), anahtar `stage:<site>:<Ax>`. Adımlar `ctx.checkpoint` ile:
   1. `discover` (süre sınırı 30 sn) -> örnek sayfalar `core.fetch.pages`'e, adresler `meta.samples`'a.
   2. `verify(discover.patch)`; `ok` ise 5'e.
   3. `prompt` + `core.llm.complete` (120 sn) + `parse.extract_yaml` + `sitemap.apply_patch` kapsam denetimi (taslağa yazmadan, kopya üzerinde).
   4. `verify(llm patch)`; geçmezse ölçüm sonucu bir sonraki prompt'a "geçen sefer şu ölçüldü" diye eklenir; en çok 2 LLM turu.
   5. Sonuç: `passed` (tüm denetimler), `passed_warn` (zorunlular geçti, isteğe bağlılar eksik), `needs_human`, `engine_gap`.
      `passed*`: yama + `meta` (samples, fingerprint, expect) taslağa işlenir; site etkinse `sitemap.commit(created_by=edit|repair)`.
   - Her adım `stage_messages`'a `system` satırı yazar (kullanıcının göreceği kısa Türkçe özet: "3 bölüm bulundu", "2/3 örnekte afiş yok").
   - LLM yanıtı yaml değilse / kapsam dışına taşıyorsa tur harcanır, ölçüme `llm_reply_invalid` denetimi düşer.
   - `evidence` her durumda doldurulur: denenen örnek adresler, ne arandı, ne bulundu (kart için hazır veri).
4. Kullanıcı eylemleri (`control/stages/actions.py`): `start(site_id)` (A1'i sıraya koyar; bir aşama `passed*` olunca `needs`'i sağlanan
   sıradakiler kendiliğinden sıraya girer), `hint(site_id, stage, text)` (mesaj `user` olarak yazılır, aşama ipucuyla yeniden çalışır),
   `sample(site_id, stage, url)` (netguard + `base_url` host'u şartı; örnek listesinin başına eklenir), `skip(site_id, stage, field=None)`
   (alan: `meta.skipped`'e, aşama yeniden ölçülür; aşama: yalnız `optional` ise `skipped`), `rerun`, `mark_gap(site_id, stage, title)`
   (T14 gelene kadar yalnız `engine_gap` durumu + `admin_notes`). Atlanamayan alanlar: `title.title`, `title.key`, her türde `url`.
5. **A1 home** (`a1_home.py`): keşif = `outline` -> tekrar eden kart blokları = bölümler; `cards.extract_fields`; `target` = bölüm bağlantı
   kümesinin `urlpatterns.classify` sınıfı (`title|episode`); menü bağlantıları `nav.links` (tür/kategori adaylarıyla).
   Ölçüm: `>= 1` bölüm; her bölümde `>= 5` kart; `title` ve `url` doluluk `>= 0.95`, `poster >= 0.8`; bölüm kartlarının adresleri tek kümede.
6. **A2 listing** (`a2_listing.py`): keşif = A1 menüsünden en çok 3 liste adresi getir; `cards.extract_fields`; `urlpatterns.pagination`;
   `listings[]` = menüden gelen ad/adres/tür. Ölçüm: 2 örnek listede `>= 10` satır (doluluk eşikleri A1 ile aynı); sayfalama kuralı 2. sayfa
   adresini üretiyor ve 2. sayfa farklı satırlar veriyor (ya da `pagination.kind = none` + tek sayfa kanıtı). Bu aşama `optional` DEĞİL ama
   etkinleştirme için şart değil (K1 için şart).
7. **A3 title** (`a3_title.py`): keşif = A1/A2 kartlarından 3-5 başlık adresi (film ve dizi karışık olacak şekilde seçilir);
   `detail.detail_fields`; `url_pattern` = başlık kümesi; `key` kuralı; `type` kuralı (adres parçası film/dizi ayırıyorsa `from: url`, yoksa
   sayfa içi işaret: bölüm listesi var mı). Ölçüm: 3 örnekte `title` dolu, `key` benzersiz, `type` belirlendi; `poster >= 0.67`;
   `year/genres/overview` isteğe bağlı (`passed_warn`).
   **Site özeti** (`control/stages/brief.py`, `brief(site_id) -> str`, <= 2 KB): sunucunun ürettiği, her prompt'un başına giren kalıcı hafıza:
   adres, getirme kipi, geçen aşamaların tek satırlık sonucu (bulunan adres kalıpları, bölüm adları), atlanan alanlar, kullanıcının bu sitede
   verdiği tüm ipuçları (aşama adıyla), açık motor boşlukları. DB'den her çağrıda yeniden üretilir (bayatlamaz). Aşamalar arası "hatırlama"
   budur; LLM oturumu aşamalar arasında TAŞINMAZ.
   **Oturum kullanımı:** bir aşama koşusunun 1. LLM turu tam prompt'la (özet + kanıt) ve `session=<site>:<stage>:<attempt>` ile gider; 2. tur
   aynı oturuma yalnız "ölçüm sonucu + (varsa) yeni ipucu" gönderir. Koşu bitince `end_session`. `session_lost` dönerse 2. tur tam prompt'la
   oturumsuz yapılır. Kullanıcı ipucuyla sonradan başlayan koşu YENİ oturumdur (özet zaten ipucunu taşır).
8. Prompt'lar (`prompts_a1_a3.py`, `core.llm.prompts.build` ile): her biri <= 30 KB toplam; kanıt = `htmlclip` ile kırpılmış 1-2 örnek +
   keşfin bulduğu/bulamadığı + ölçüm sonucu + ipucu; yanıt biçimi = yalnız `pages.<tür>` yaml parçası. Kurallar bölümünde K3/K4 (host adı,
   siteye özel hile yok) ve "emin değilsen alanı boş bırak" yazar.
9. **K0** (`control/stages/k0.py`, iş türü `crawl` yerine geçici `k0:<site>`; T11 frontier'e taşır): A3 `passed*` olunca site `active` +
   `sitemap.commit(created_by=onboard)`; ana sayfa bölümlerindeki kartlar -> başlık sayfaları `get_many` ile -> `title_rule` -> `upsert_title`
   (`origins` = `("section", <bölüm başlığı>, sıra)`). `target: episode` bölümlerinde başlık sayfası henüz bulunamıyorsa (A5 `title_link` yok)
   kart atlanır ve sayılır. En çok 60 başlık.
10. `control/stages/status.py`: `overview(site_id) -> {site, stages: [{id, title, state, summary, checks, samples, evidence, messages, actions}]}`
    (T10 bunu aynen gösterir; `summary` Türkçe tek satır, `actions` o durumda izinli eylemler).

## Kapsam dışı
A4-A7 (T09), frontier (T11), admin arayüzü (T10; ama `status.overview` biçimi bu görevde sabitlenir), onarım tetikleri (T14).

## Kabul (fixture `fakesite`, sahte getirici ve sahte LLM ile)
- `start` -> A1, A2, A3 LLM'siz `passed`; `llm_calls` boş. Site `active`, `site_versions` sürüm 1, K0 sonrası katalogda fixture başlıkları.
- Keşfin kör kaldığı varyant (`fakesite_b`, bir sayfa türü): LLM bir kez çağrılır, sahte yanıtla `passed`.
- LLM iki turda da başarısız: aşama `needs_human`; `evidence` ve `checks` dolu; sonraki aşamalar başlamaz.
- `hint` sonrası aşama yeniden çalışır, ipucu prompt'ta geçer (sahte çağırıcı prompt'u yakalar), mesaj geçmişinde `user` satırı var.
- LLM yanıtı başka sayfa türüne dokunuyor: reddedilir, taslak değişmez.
- `skip(field="year")`: ölçüm o alanı aramaz, `meta.skipped`'te görünür. `skip(field="title")` reddedilir.
- **Restart:** adım 3 sırasında iş kirası düşürülür; yeniden çalışınca `discover` tekrar edilmez (getirme sayacı artmaz), kaldığı turdan sürer.
- Etkin sitede `rerun` + farklı yama: yeni sürüm `created_by=edit`; yama aynıysa yeni sürüm yok.
- Site özeti: A3 prompt'unda A1/A2'nin sonuç satırları ve daha önce A1'e verilmiş ipucu geçer; özet 2 KB'ı aşmaz.
- Oturum: 2. tur prompt'u HTML kanıtını içermez ve 1. turla aynı `session` kimliğini taşır; koşu sonunda oturum silinir; `session_lost`
  durumunda 2. tur tam prompt'la tamamlanır; ipucuyla başlayan yeni koşunun kimliği farklıdır.
- Aşama başına LLM prompt'u 60 KB altında; prompt'ta sır ve başka sitenin adı yok.
- `status.overview` her aşama için durum, özet, denetimler, örnekler ve izinli eylemleri döner.
