# T03: Oynatma kütüphanesi (`core/play`)

**Bağımlı:** T00, T01. **Açar:** T04b, T09, T13. **Oku:** README §5.2, §5.8; `inventory/01 §4`; `inventory/02 §4, §5` (çağrı noktaları);
`server/app/scraper/{resolvers/,providers/,blocked,badhosts,sitequality,provider_match,config}.py`, `server/app/library/{videos,gate,
streamdiag,streamprobe,hostrules,streamlife,tracks,subtitles}.py`, `server/app/{streamproxy,hlsproxy}.py`,
`server/app/routers/onboard_sandbox.py:2341-2412, 3741-3812`, `docs/arch/streams.md`.

## Amaç
"Oynatıcı sayfası -> aday -> provider -> akış -> yoklama" zincirini, eski yönetim katmanına hiçbir bağı kalmadan `server2`'ye taşımak.
Bu kod büyük ölçüde KOPYADIR; iş, bağımlılıkları sökmek ve temiz bir yüzey vermektir.

## Yapılacaklar
1. **Resolver'lar** `core/play/resolvers/`: 7 tip (`iframe, anchor_host, data_attr_token, ajax_handoff, json_api, player_page, embedded_json`)
   + `TYPES`, `catalog()`, `validate(list)`, `make_ctx`. `ctx.fetch` T01 getiricisine bağlanır. `SiteConfig` yerine küçük bir
   `PlayCtx(site_id, base_url, resolvers, providers_allow, gate, fetch)` veri sınıfı kullanılır (T07 adaptörü üretir).
2. **Provider'lar** `core/play/providers/`: `registry` (`resolve`, `providers`, `catalog`), kod provider'ları (`okru`, `vidmolly`: host'a ait,
   K4 gereği serbest), tarif motoru `recipes` (`validate_recipe`, `from_text`, `load_all`, `update_problems`, `widen_host_regex`), `trace`.
   Tarifler `DATA_DIR/providers/*.yaml`'dan okunur; açılışta `seed/providers/*.yaml` içinden eksik olanlar oraya kopyalanır. Tarif
   sürümleme (`save_recipe`, `recipe_snapshot`, `restore_recipe`) `scraper/config.py:620-712`'den alınır. **Tohum olarak yalnız host'a ait
   genel tarifler konur; adı bir siteye bağlı tarifler (`<site>_player` gibi) ALINMAZ** (K3; kullanıcı siteyi ekleyince sistem üretir).
   **Kütüphane sınırı (K4, README §5.9):** provider'lar ayrı bir kütüphanedir; girdileri yalnız provider adresi + Referer'dır, çıktıları
   `ProviderResult` biçimidir (kalite/çözünürlük varyantları, altyazılar, ses izleri, başlık gereksinimi, `resolve_ms`). HLS ana listesi
   dönen provider'da varyantlar (`width/height/bitrate`) ana listeden okunup `streams[]`'e yazılır; altyazı ve ses izleri `tracks`/`subtitles`
   kodundan bu biçime bağlanır. Provider kodu/tarifi `PlayCtx`'i, site haritasını ya da `site_id`'yi GÖRMEZ. Sitenin provider'a nasıl
   vardığı resolver parametreleridir ve haritadan gelir.
3. **Aday bulma/çözme**: `discover(ctx, html, page_url) -> [candidate]` ve `resolve_candidate(ctx, candidate, page_url, load_cookies)`
   (`site_extractors/__init__.py:66, 119`'un genel yolu; `use_site_module` dalı ve site modülleri ALINMAZ). Eski `stream_resolver` yolu ALINMAZ.
4. **`follow(ctx, page_url, *, deadline) -> FollowResult`**: `onboard_sandbox._follow_playback` (`:2341`) + `_resolve_one` (`:3741`) birleşimi,
   DB'siz: `{ok, stage: page|discover|provider|stream, candidates[{url, host, resolver_type, ok, error}], streams[], blocked, ms}`.
   Bu, aşama ölçümünün (T09), teşhisin (T13) ve onarım doğrulamasının (T14) TEK oynatma doğrulayıcısıdır.
5. **Kapı** `core/play/gate.py` + `blocked.py`: `judge_html`, `fetch_verdict`, `series_verdict`, `item_verdict`, `blocked.match`,
   `placeholder_hint`, `validate`, `validate_gate`. `blocked_pages` yazımı T04a'nın `mark_blocked`'ına gider (burada yalnız hüküm).
6. **Host bilgisi**: `host_known(url, pool)`, `provider_pool` (`sitequality.py:49, 40`), `sitequality.assess`, `provider_match.match`
   (bilinmeyen host'u tüm provider'larla kuru çalıştırma), `badhosts` (yalnız izleyici/reklam süzgeci; medya görünümlü adres asla reddedilmez).
7. **Yoklama ve tanı**: `streamprobe` (`probe`, `summarize`, `rank_streams`, `summarize_many`), `streamdiag` (`diagnose`, `quick_check`,
   `build_job`, `record`, `persist_learning`, `public_diagnosis`), `hostrules` (tümü; tablo T04a'da). `streamdiag.run` içindeki
   `_heal_signal`/`_issue_signal` çağrıları SİLİNİR; yerine `events.emit("stream_diag", ...)`.
8. **Kaynak çözümleme çekirdeği** `core/play/sources.py` (eski `library/videos.py`): `streams(cid, episode_id, kind, profile_id)`,
   `resolve_source(row, force)`, `retry(sid)`, `feedback(token, event, ...)`, `sync_source`, sağlık geçişleri (`inventory/02 §4` tablosu aynen),
   `valid_until` önbelleği (`streamlife`), ses/altyazı (`tracks`, `subtitles`). Sökülecek bağlar:
   - `_finder_state` / `_finder_for_failure` / `playissues.*` / `_playheal` çağrıları -> silinir.
   - Akış çıkmadığında `events.emit("stream_missing", ...)`; her çözüm sonucunda yük §5.2'deki `entries` biçiminde hazırlanır.
   - `state.record_resolver` -> silinir (gerekirse `video_sources.last_error/last_checked_at` yeter).
   - `streams()` yanıtına `finder` alanını **bu modül koymaz**; T04b router'ı `control.problems`'tan alıp ekler (katman kuralı).
9. **Akış vekili**: `streamproxy`, `hlsproxy` (imzalı belirteç; sır `STREAM_PROXY_SECRET`, yoksa açılışta `DATA_DIR` altında üretilip saklanır).
   Vekil nedeni önceliği eskisiyle aynı (`routers/streams.py:28-30`): `ua|ip|recipe|learned|env` -> `env` nedeni kalkar (env yok),
   yerine `settings`.
10. **İlk görüşte host yoklaması** (yeni, küçük): bir akış host'u için `stream_host_rules` kaydı yokken ilk başarılı çözümde arka planda
    bir kez `quick_check`; düzeltme bulunursa kural yazılır (`hostrules.learn`). Host başına bir kez (kalıcı işaret `stream_host_rules`'ta
    `reason='ok'` satırı ya da ayrı kolon; migrasyon bu görevde).

## Kapsam dışı
İstemci router'ları (T04b), sorun kaydı (T13), site haritası adaptörü (T07), herhangi bir LLM çağrısı.

## Kabul
- `check_imports` temiz; `core/play` içinde `playheal|sourcefinder|playissues|heal_agent|onboard` geçmiyor (grep).
- `follow`: fixture bölüm sayfası + sahte provider ile `ok=True`, `stage="stream"`; aday olmayan sayfada `stage="discover"`; bilinmeyen
  host'ta `stage="provider"` ve adayda `host`; yer tutucu sayfada `blocked=True`.
- `streams()` akış yokken `stream_missing` olayı bir kez yayılır ve yükte kaynak başına `stage` + `candidates` vardır (abone testte toplar).
- `feedback`: sağlık geçişleri (1 hata `suspect`, 3 hata `broken`, başarı `healthy`, cihaz kodları sayılmaz) eski davranışla aynı (4 küçük test).
- `streamdiag.run` sonrası `stream_diag` olayı yayılır; eski modüllere çağrı yok.
- Tarif: geçerli tarif yüklenir, bozuk tarif atlanır, `save_recipe` sürüm arşivler, `restore_recipe` geri alır.
- İlk görüş yoklaması: host başına tek çağrı (ikinci çözümde çağrılmaz).
- Vekil: imzalı belirteç doğrulanır, süresi geçmiş/bozuk belirteç reddedilir.
- `seed/providers/` içinde site adı taşıyan dosya yok.
- Sınır: `core/play/providers/**` içinde `PlayCtx`, `site_id`, `sitemap` geçmez (grep); `registry.resolve` yalnız adres + Referer ile çağrılabilir.
- `ProviderResult`: sahte HLS ana listesiyle 3 varyant `width/height/bitrate` ile döner; altyazı ve ses izi listeleri README §5.9 biçiminde.
