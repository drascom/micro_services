# Envanter 03: eski yönetim katmanı (kurtarılacaklar + kesim bağları)

Tarih: 2026-10-03. Salt-okur kod okuması (test/uygulama çalıştırılmadı, sunucuya bağlanılmadı). Yollar `server/` köküne göre; `sb` = `app/routers/onboard_sandbox.py`.
Hüküm kısaltmaları: **KÜT** = kütüphaneye taşınmalı (saf, aynen), **YY** = yeniden yazılmalı, **GRK** = gereksiz (yeni sistemde karşılığı yok).

## 1. `routers/onboard_sandbox.py` (4554 satır) fonksiyon haritası

| Grup | Satır | ~Boyut | Ana fonksiyonlar (imza) | Saf mı | Hüküm |
|---|---|---|---|---|---|
| Belge + sabitler | `sb:1-271` | 270 | eşikler `MIN_*`, `PLAYABLE_*`, `SERIES_*`, `HARDEN_CRITERIA`, `INFO_FIELDS`, kırpma sınırları | saf | KÜT (eşikler `expect` tablosuna) |
| Güvenlik / token | `sb:278-330`, `393`, `786-802`, `4444-4451` | 80 | `issue_token(draft_id) -> str` `:280`, `revoke_token` `:288`, `_guard(request, x_onboard_token)` `:305` (localhost + bellek token), `_bounded(fn,*args,hint)` `:315` (havuz + 504), `_check(url)` `:393` (netguard), `_redact(text)` `:4448` | bellek (`_tokens`), FastAPI | Ajan araç kullanırsa YY (token modeli aynı kalabilir); araçsız LLM çağrısında GRK. `_redact`, `_check` KÜT |
| Küçük yardımcılar | `sb:333-431` | 100 | `_clip` `:335`, `module_site(site_id)` ctx `:364`, `_draft_cfg(data, extra_providers) -> SiteConfig` `:400`, `_load_yaml(text) -> (dict, hata)` `:423` (alias'sız yükleyici) | saf (`_edit_site` `:384`, `_load_page` `:407` store'a bağlı) | `_load_yaml`, `_draft_cfg`, `_clip` KÜT |
| Tanılama (`diagnostics`, `failing`) | `sb:433-773` | 340 | `_rows_diagnostics(html,row_selector,fields,where,...)` `:575`, `_detail_diagnostics` `:640`, `_failing_hint(name,c,out)` `:679`, `_failing(out) -> [{criterion,value,bound,hint}]` `:761` | saf ama ContextVar `_DIAG` `:444` üstünden yan etki | KÜT (ContextVar yerine dönüş değeri); ipucu metinleri ajan-odaklı, aşama kartı kanıtı için uyarlanır |
| Sayfa getirme / önbellek | `sb:775-1050` | 275 | `_fetch_once(url,mode,wait_for,referer,deadline,...)` `:804`, `_fetch_store(url,mode,wait_for,deadline,...) -> {html,meta}` `:818` (auto: http ince ise browser'a yükselt, `THIN_CHARS`), `_page_signals(html,requested,final) -> {canonical_url,redirect_hint}` `:931`, `_do_fetch(body,draft_id,*,deadline)` `:1025` | `_page_signals` saf; diğerleri ağ + `onboard_store.save_page` (`DATA_DIR/onboard`) | `_page_signals` + auto-yükseltme mantığı KÜT; depolama YY (örnek sayfa = `samples[]`) |
| HTML sorgu / anahat / grep | `sb:1052-1594` | 540 | `_do_query` `:1054`, `_nav_links(tree,base)` `:1115`, `_scoped_selector(tree,nodes,card_sel)` `:1154`, `_link_kind(href)` `:1187`, `_blocks` `:1249`, `_sections` `:1302`, `_episode_links` `:1431`, `_anchor_selector` `:1401`, `_outline_of(html,base,requested,final) -> dict` `:1478`, `_do_grep` `:1563` (alt süreçte regex, 5 sn) | `_outline_of` ve altı saf (html -> dict); `_do_*` store'dan sayfa okur | **KÜT, en değerli parça**: plan §3.2 "keşif (LLM'siz)" = tekrar eden blok / bölüm / bağlantı türü kümeleme tam olarak bu. `_do_query`/`_do_grep` yalnız ajan aracı: YY/GRK |
| Config statik denetimi | `sb:1596-1762` | 165 | `_check_selector` `:1598`, `_check_fields(data)` `:1633`, `_check_core(data) -> (errors,warnings)` `:1677`, `_check_playback(data,extra_names)` `:1708`, `_check_normalize` `:1728`, `_url_params_errors` `:1739` | saf | KÜT (yaml şeması değişirse uyarlanır) |
| Koleksiyonlar (ana sayfa bölümleri) | `sb:1764-2014` | 250 | `_check_collection(index,c,base,strict_categories,fetch_mode)` `:1771`, `_check_collections(data,site_hint,strict)` `:1838`, `_parse_collection(cfg,spec,html,limit)` `:1905`, `_collections_stage(cfg,pairs,main,deadline,...)` `:1920` | denetim saf; `_stage` ağ + `library.ingest`/`normalize` | denetim + `_parse_collection` KÜT (A1); `_stage` YY |
| test_config çekirdeği | `sb:2016-2147` | 130 | `_criterion(value,bound,kind) -> {value,min|max,ok}` `:2022`, `_criteria(valid_count,fill,norm,error_count)` `:2027`, `_list_stage(cfg,html,errors,warnings)` `:2048`, `_normalize_stage` `:2079`, `_detail_stage(cfg,items,detail_page_id,deadline,...)` `:2103` | `_criterion*`, `_list_stage`, `_normalize_stage` saf; `_detail_stage` ağ | KÜT (A2/A3 ölçümü) |
| Telif kapısı çalıştırma | `sb:2149-2236` | 90 | `_check_blocked_blocks(data)` `:2157`, `class _GateRun(cfg)` `:2168` (`blocked_report()`, `gate_report()`) | ağ (`library.gate`) | KÜT (A7) |
| Oynatılabilirlik | `sb:2238-2548` | 310 | `_playable_picks(cfg,items,inventories,spares,skip_series)` `:2285`, `_follow_playback(cfg,locator,deadline,site_id,got,...)` `:2341` (sayfa -> aday -> akış; `videos` yolunun DB'siz hâli), `_playable_stage` `:2413`, `_playable_criteria(block,norm,series_block)` `:2532` | ağ; DB yazmaz | **KÜT**: `_follow_playback` heal kapılarının da tek doğrulayıcısı (`heal_agent.py:594`). A5/A6 ölçümü |
| Dizi envanteri + ingest örneği | `sb:2550-2843` | 295 | `_series_candidates` `:2559`, `_series_sample(cfg,spec,pick,deadline,warnings,...)` `:2590`, `_series_stage` `:2631`, `_ingest_sample_block` `:2759`, `_series_criteria(block,has_spec)` `:2835` | ağ + `series_dir`, `site_extractors.series_inventory` | KÜT (A4) |
| Arama testi | `sb:2845-3175` | 330 | `search_query(title,site_names)` `:2929`, `_run_search(data,query,known,extra_providers,directory)` `:2991`, `_search_stage` `:3046`, `_search_criteria(data,block)` `:3095`, `_do_test_search` `:3108` | ağ (`search_generic`) | KÜT (A7) |
| Sertleştirme kriterleri | `sb:3188-3406` | 220 | `harden_enabled()` `:3190` (env `ONBOARD_HARDEN`), `_hardening(data,norm,pairs,collections_ran,skipped,html,meta,detail) -> (criteria,exempt,hint,info)` `:3335`, `_detail_info` `:3255`, `_removed_fields(data,previous,skip)` `:3289`, `_home_path_criterion` `:3231` | saf (rapor üstünde); `_draft_memory` `:3202` store okur | KÜT; kriterler aşamalara dağıtılır (aşağıdaki tablo) |
| Taslak provider tarifleri | `sb:3408-3521` | 115 | `_prepare_recipes(items,replace) -> _Recipes(providers,entries,errors)` `:3438`, `_merge_recipes` `:3499`, `_recipe_usage` `:3508` | `_prepare_recipes` saf (kütüphane adlarını okur); merge store'a bağlı | `_prepare_recipes` KÜT (heal + save kullanıyor) |
| Rapor birleştirme | `sb:3524-3736` | 210 | `_analyze(*a,**kw)` `:3524`, `_analyze_report(yaml_text,page_id,detail_page_id,deadline,collections,site_hint,playable,draft_recipes,baseline,search_stage,harden,skipped,previous) -> dict` `:3540`, `_baseline_check(data,out)` `:3699` | ağ; tek 120 sn bütçede TÜM aşamalar | **YY**: yeni sistemde aşama başına ayrı ölçüm; iç adımlar (yukarıdaki satırlar) KÜT |
| Resolver / provider testi | `sb:3739-4129` | 390 | `_resolve_one(cfg,raw,locator,load_cookies,deadline,site_id)` `:3741`, `_trial_page` `:3813`, `_do_test_resolvers` `:3949`, `_run_within(candidates,run_one,deadline)` `:4039`, `_do_test_provider` `:4069` | ağ | `_resolve_one`, `_run_within` KÜT; `_do_*` sarmalayıcıları YY |
| Keşif / provider eşleme sarmalayıcı | `sb:4131-4160` | 30 | `_do_discover_site` `:4133` -> `scraper/discover.py`; `_do_match_providers` `:4153` -> `scraper/provider_match.match(url,*,referer,fetch_api,deadline)` `provider_match.py:200` | ağ | sarmalayıcı GRK; `discover.py`, `provider_match.py` KÜT (bkz. §8) |
| Taslak gönderme | `sb:4162-4249` | 90 | `_edit_summary(site_id,data)` `:4164`, `_do_submit(body,*,deadline)` `:4180` | store yazar (`update_draft status=ready`) | YY (aşama sonucu `jobs`/harita kaydı olur) |
| Gövdeler + rotalar | `sb:4251-4439` | 190 | 11 Pydantic gövde, `_remember(draft_id,kind,result)` `:4377` (`live` kayıt) | HTTP | YY/GRK |
| Repair modu | `sb:4442-4554` | 110 | `_do_site_config(site_id,recipe)` `:4468`, `_do_submit_repair(job_id,body)` `:4503` (yalnız `repairs/<job>.json` yazar) | store | YY |
| ask_user | sandbox'ta YOK | - | extension içinde yerel (`pi/extensions/diziflix-onboard.ts:423-463`), olaydan okunur (`pi_agent.py:301-408` `EventParser.ask`/`asking()`), `onboard.question_data(args)` `onboard.py:199`, `skippable(field)` `:189`, cevap kalıpları `ANSWER_*` `:171-174`, `engine_gap_approval` `:263` | saf | `question_data`, `skippable`, kalıplar KÜT (aşama kartı "ipucu / yok, atla"); soru akışı YY |

Uçlar (14): `POST fetch|query|grep|outline|test_config|test_search|test_provider|discover_site|match_providers|test_resolvers|submit|submit_repair`, `GET resolvers|site_config/{site_id}` (`sb:4357-4554`), önek `/api/onboard/sandbox` (`sb:160`).

### Kabul kriterleri: nerede, nasıl ölçülüyor

Hepsi `_criterion(value, bound, "min"|"max")` (`sb:2022`); `passed = all(ok)` (`sb:3695`). Değerler tek `_analyze_report` çağrısında, canlı sayfalardan ölçülür.

| Kriter | Eşik (sabit) | Ölçüm yeri | Yeni aşama |
|---|---|---|---|
| `valid_count` | >= 8 (`MIN_VALID_COUNT` `sb:163`) | `_criteria` `sb:2027`, `_list_stage` | A2 |
| `title_fill`, `detail_url_fill`, `poster_url_fill` | 0.95 / 0.95 / 0.8 (`MIN_FILL` `sb:164`) | `_criteria` | A2 |
| `normalize_ok_ratio`, `duplicate_key_ratio` | >= 0.9, <= 0.1 (`sb:165-166`) | `_criteria` + `_normalize_stage` | A3 |
| `config_errors` | 0 | `_criteria`; submit'te yeniden (`sb:4220`) | hepsi |
| `collections_valid_count`, `collections_normalize_ok_ratio` | >= 3 (`sb:167`), >= 0.9 | `_collection_criteria` `sb:3177` | A1 |
| `playable_ratio` | >= 0.67, 3 örnek + 2 yedek, örnek başı 25 sn (`sb:172-177`); engelli örnek paydadan düşer | `_playable_criteria` `sb:2532` <- `_playable_stage` `sb:2413` | A6 |
| `series_have_episode_sources` | >= 0.9 (`sb:173`) | `_playable_criteria` | A4/A5 |
| `series_inventory_ok` | 1.0, 3 dizi sayfası (`sb:182-185`) | `_series_criteria` `sb:2835` | A4 |
| `ingest_sample_ok` (harden) | >= 0.8, 10 öğe / 5 sayfa (`sb:190-192`) | `_ingest_sample_criterion` `sb:2827` | A4 |
| `search_ok` | 1 sorgu, sonuç >= 1 ve bilinen başlık bulundu | `_search_criteria` `sb:3095` | A7 |
| `baseline_ok` (repair) | alan düşmedi, doluluk düşmedi | `_baseline_check` `sb:3699` -> `heal.check_repair_proposal`, `drift.detect`, `heal._check_fill` | onarım doğrulaması |
| Harden: `availability_gate_defined`, `series_signal_collection`, `series_full_inventory`, `home_path_is_canonical`, `collection_poster_fill` (>= 0.8), `detail_info_defined` (>= 3 grup, 2 örnek) | `HARDEN_CRITERIA` `sb:218`; `ONBOARD_HARDEN=0` kapatır (`sb:3194`), `tests/_sandbox.py` kapalı koşar | `_hardening` `sb:3335`; yalnız YENİ site (edit/repair'de yok: `sb:4198`, `sb:4392-4394`) | A1/A3/A4/A7 |
| Muafiyet | taslak `skipped_fields` ("Sitede yok, atla") -> `exempt[]` | `_skip_names` `sb:3197`; atlanamazlar `onboard.py:180-186`, `_NEVER_SKIPPED` `onboard.py:339` | harita `skipped[]` |

Kayıt anındaki baseline eşikleri ayrıca üretilir: `onboard.thresholds_from(report)` `onboard.py:997` (min_items = max(5, %60), alan doluluğu = ölçülen - 0.15).

## 2. LLM ajan altyapısı

| Konu | Yer | Not |
|---|---|---|
| Koşu imzası | `pi_agent.run(job: Job, *, token, timeout, on_event, on_label=None, on_start=None, model_name=None, extension=None, skill=None, pi_bin=None) -> RunResult` `pi_agent.py:485` | Senkron; ilk mesaj stdin; watchdog `threading.Timer`; asla raise etmez |
| İş tanımı | `Job(job_id, first_message, mode="onboard", tools=ONBOARD_TOOLS, env_extra={})` `pi_agent.py:64` | `job_id` = pi `--session-id` = token sahibi |
| Komut satırı | `build_command(job_id,*,tools,pi_bin,model_name,extension,skill,session_dir)` `pi_agent.py:146` | `pi -p --mode json --offline --no-builtin-tools --tools <liste,read> --no-extensions -e <ts> --no-skills --skill <dir> --no-context-files --no-prompt-templates --model --session-dir --session-id` |
| Ortam | `child_env(token, job_id, skill_dir, extra)` `pi_agent.py:119` | Sır görünümlü env'ler ayıklanır; `DIZIFLIX_SANDBOX_URL/ONBOARD_TOKEN/DRAFT_ID/SKILL_DIR/MODE/SITE_ID` eklenir |
| Olay ayrıştırma | `class EventParser` `pi_agent.py:315` (`feed`, `finish`, `last_say`, `provider_error`, `turns`, `asking()`) | Model hatası `error` olayı DEĞİL: çıkış 0 + `stopReason:error` (`pi_agent.py:181-183`) |
| Oturum modeli | cwd sabit `DATA_DIR/onboard/work` (`pi_agent.py:98`), oturum dosyaları `DATA_DIR/onboard/sessions` (`:94`); aynı id + aynı cwd = aynı oturum | `onboard.message` aynı oturumu sürdürür (`onboard.py:844`); heal her koşuda yeni `rp_` id (taze oturum, `heal_agent.py:82-97`) |
| Araç sunumu | TS extension her aracı `pi.registerTool` ile kaydeder, `DIZIFLIX_MODE`'a göre süzer (`ts:79-82`, `:562-586`), araç = sandbox'a HTTP (`call` `ts:146`); pi'nin `read` aracı yalnız skill dizinine (`readViolation` `ts:539`) | Araç listeleri `ONBOARD_TOOLS` (13), `REPAIR_TOOLS` (11), `EDIT_TOOLS` (`pi_agent.py:44-54`) |
| Süre / bütçe | koşu 900 sn `ONBOARD_TIMEOUT` (`config.py:272`); araç çağrısı 120 sn (`config.py:265`), extension 150 sn (`ts:56`); otomatik tur 2, en çok 5 (`onboard.py:343`); heal ajanı 300 sn (`heal.py:502`), kapı doğrulaması 420 sn (`heal_agent.py:62`); finder günlük 10 heal (`config.py:327`); heal cooldown 3600 sn (`heal.py:459`) | Token/maliyet sayacı yok (doğrulanmadı: pi olayından okunmuyor) |
| LLM sağlık | `llm_health.check(force=False)` `llm_health.py:215`: `pi auth check --provider <p> --json --no-refresh`, 10 sn, 60 sn önbellek (`:25-26`, `:189-213`); `onboard.health()` `onboard.py:971` (pi bin, skill, extension, model, llm) | `llm_health.py:48` `scraper.heal`'den `PI_DEFAULT_MODEL` alır: kesimde kopar |
| Tek atımlık çağrı (araçsız) | `heal._call_pi(prompt, timeout) -> {status,text}` `heal.py:239`: `pi -p --model M --no-session --no-tools --offline` | Yeni "öneri" adımına en yakın hazır kalıp |
| Diğer çağırıcılar | `heal._call_codex` `heal.py:191`, `_call_pi_agent` `:278`, seçici `SCRAPER_HEAL_PROVIDER` (`heal.py:287`) | Üç çağırıcı tek yola iner |

`server/pi/` içeriği (bayt): `extensions/diziflix-onboard.ts` 30987 (586 satır, 15 araç); `skills/diziflix-site-onboarding/SKILL.md` 25668 (tavan 26000, `tests/test_pi_size_budget.py:22`); `references/`: `resolvers.md` 13450, `heal.md` 12053, `quality.md` 11492, `series-page.md` 9820, `collections.md` 9471, `normalize.md` 8918, `config-schema.md` 8363, `search.md` 8257, `providers.md` 7333, `player-authoring.md` 7144, `edit.md` 7136, `blocked.md` 4605, `discover.md` 2930; `examples/` 2 site yaml + 9 provider tarifi (~11 KB). Referans blokları üretilir: `tools/gen_onboard_refs.py` (sandbox/heal_agent/pi_agent import eder, `:182-331`).

**Aşama başına kısa, taze oturum için asgari:** (1) `pi_agent.run` + `build_command` + `child_env` + `EventParser` + `scrub` + `terminate` aynen (KÜT; `onboard_store` bağımlılığı yalnız `sessions_dir`/`work_dir` `pi_agent.py:94-103`, yol parametreye çevrilir); (2) her çağrıda yeni `job_id` (taze oturum hazır davranış) ya da araçsızsa `_call_pi` kalıbı; (3) araç gerekiyorsa: aşamaya özel araç listesi + extension'da yeni mod (`mode()` `ts:79` yalnız 3 mod tanır) + token korumalı yerel HTTP ucu; (4) `llm_health.check`; (5) aşama başına küçük prompt (tek 25 KB skill yerine). Plan §3.2 "çıktı yalnız o sayfa türünün extract/links alanları" araçsız çağrıya uyuyor: (3) büyük olasılıkla gereksiz (karar gerekli).

## 3. Config yazma / sürümleme / geri alma

| İşlem | Fonksiyon | Not |
|---|---|---|
| Sürümlü yazma | `scfg.save_new_version(site_id, new_data) -> int` `scraper/config.py:389` | Aktif dosya `<site>.v<N>.yaml`'a arşivlenir, yeni sürüm = max(aktif, arşivler)+1, atomik + `flock`; tombstone'u siler. **Kalır (kütüphane)** |
| Arşiv yeri | `scraper/configs/<site>.v<N>.yaml`, `archived_versions(site_id)` `config.py:370`, `site_files` `:418` | Aktif `<site>.yaml` + `<site>.baseline.json` aynı dizinde |
| Geri alma | `scfg.rollback_config(site_id) -> int` `config.py:514` | Aktifi arşivler, bir önceki arşivi aktif yapar; uç `POST /api/ops/sites/{site}/rollback` `ops.py:447` (heal kaydı `rolled_back`; finder/issue'ya dokunmaz) |
| Baseline | `write_baseline_thresholds(site_id, thresholds)` `config.py:563`, `update_baseline(site_id, last_good)` `:541` | |
| Tarif sürümleme | `save_recipe(name, data) -> int` `config.py:666`, `recipe_snapshot` `:686`, `restore_recipe` `:695`, `delete_recipe` `:712` | `configs/providers/<ad>.yaml` (+ `.vN.yaml`) |
| Yeni site / edit kaydı | `onboard.save(draft_id, site_id, display_name=None, enable=False, force=False, scan_now=False) -> dict` `onboard.py:1171` | Sırası: id denetimi -> tarif çakışması -> koleksiyon id yeniden adlandırma (`_rekey_collections` `:1037`) -> `sb._analyze` yeniden test (`:1220`) -> tarifler -> `save_new_version` -> `thresholds_from` -> tombstone sil -> `settings.update` (yeni site kapalı başlar) -> devir notu -> `_start_scan` (`:1147`, `ops._bg_scan`). Maskeli sır geri koyma `_restore_masked` `:1109` |
| Heal uygulama (seçici) | `heal._heal_impl` `heal.py:304` -> `save_new_version` `heal.py:380` (`heal_autoapply` açıksa) | |
| Heal uygulama (ajan) | `heal_agent._apply(cfg, new_data, plan, trace, result)` `heal_agent.py:785` | Önce tarifler, sonra site; hata olursa tarifleri geri alır |
| Tombstone | `DATA_DIR/deleted_sites.json` (`config.py:31`): `{site: {at, files[]}}`; `tombstone_add` `:489`, `tombstone_clear` `:501`, `tombstones()` `:459` | `deploy.sh` sunucudakini okur (`deploy.sh:1030-1031`), yerel aynadan siler (`cfg_remove_deleted` `deploy.sh:826`) |
| Site silme | `DELETE /api/ops/sites/{site}?purge=` `ops_sites.py:269` -> `library/purge.delete_site(site, *, purge=True, refresh=True)` `purge.py:225` | Meşgulse 409 (`_busy_map` `ops_sites.py:64`), `scan` slotunu tutar; ayar + devir notu da silinir (`purge.py:267`, `:273`). `purge.py` kalır |

## 4. Heal kapıları

`LAYERS` (`heal_agent.py:128-137`): katman -> dokunulabilir üst düzey yaml anahtarları.

| Katman | Anahtarlar | Kanıt (aşama) | Yeni sayfa türü |
|---|---|---|---|
| `list` | `list`, `collections` | `list` / `collection` / `drift` | home, listing (A1/A2) |
| `detail` | `detail` | `detail` | title (A3) |
| `series_page` | `series_page` | `series` / `inventory` | season/episode (A4) |
| `normalize` | `normalize` | `normalize`, `no_sources` | A3/A4 |
| `resolvers` | `resolvers`, `providers` | `discover` (aday yok) | player (A5) |
| `provider` | `providers` (+ kütüphane tarifi) | `player_page.*`, host | sources (A6) |
| `fetch` | `fetch_mode` | `page` / `fetch` | hepsi |

`_gate_and_apply(cfg, evidence, record, trace)` `heal_agent.py:665` kapı sırası: tarif geçerliliği (`sb._prepare_recipes`) -> yaml ayrıştırma -> etkili değişiklik var mı -> **kapsam** (`scope_violation` `:252`) -> yapı (`heal.check_repair_proposal` `heal.py:530`) -> yeni statik hata yok (`_static_errors` `:559`) -> baseline (`sb._analyze(baseline=True)` `:717`) -> bozuk örnekler oynuyor (>= `MIN_PLAYABLE_RATIO`, `_follow` `:594`) -> çalışan örnekler bozulmadı -> tarifi kullanan diğer siteler (`_other_users` `:635`, `regression: <site>`) -> `heal_autoapply` -> `_apply`.

Aynen kullanılabilecek saf fonksiyonlar:

| Fonksiyon | Yer | İş |
|---|---|---|
| `layers_of(evidence, cfg=None) -> list` | `heal_agent.py:164` | kanıt -> katman (yeni `problems.page_type` teşhisi) |
| `allowed_keys(layers) -> set`, `touched(old, new) -> (keys, paths)` | `heal_agent.py:185`, `:190` | "bu aşama yalnız kendi anahtarına dokundu mu" kapısı |
| `scope_violation(cfg, evidence, layers, new_data, plan) -> Optional[str]` | `heal_agent.py:252` | kapsam kapısı (ağ yok) |
| `evidence_hosts`, `new_hosts`, `related_recipes` | `heal_agent.py:215-234` | yeni host -> motor boşluğu sinyali |
| `check_repair_proposal(cfg, data) -> list[str]` | `heal.py:530` | site_id/şema/host/normalize.key/alan korunumu |
| `_check_structure(before, after)`, `_check_fill(baseline, metrics, all_fill)`, `field_fill(raw, names)` | `heal.py:77`, `:101`, `:70` | alan düşmedi / doluluk düşmedi (toleranslar `heal.py:62-63`) |
| `selector_diff(before, after) -> list[dict]` | `heal.py:396` | Sürümler sekmesi farkı |
| `_clean_html(html, limit=60000, anchor_tokens)` | `heal.py:134` | LLM'e kırpılmış HTML (plan §3.2 "kırpılmış HTML örneği") |
| `drift.detect(metrics, baseline)` | `scraper/drift.py` (okunmadı, doğrulanmadı) | imza/eşik kontrolü adayı |

Yeniden yazılacaklar: `_gate_and_apply` gövdesi (sandbox özel fonksiyonlarına bağlı), `cooldown_skip`/`settle` (`heal.py:422`, `:443`; `_ops.json` + bellek), `repair_message`/`drift_message` (`heal_agent.py:316`, `:379`).

## 5. `state.py`

| Parça | Yer | Biçim / davranış | Yazanlar |
|---|---|---|---|
| Site durumu dosyası | `data/scraper_state/<site>.json` (`state.py:19-23`), `flock` `.state.lock` (`:39`) | `last_ingest`, `last_error`, `reports[-30]`, `history`, `drift`, `heal`, `heal_cooldown`, `last_resolver` | `record_ingest` `:50` (`ingest.py:1015`), `record_run` `:59` (`runner.py:110`), `record_heal` `:97` (`ops.py`, heal), `set_heal_cooldown` `:114` (`heal.settle`), `record_resolver` `:139` (`videos.py:329`, `:521`) |
| Canlı işler (bellek) | `_active: dict[(site, kind)]` + `_active_lock` (`state.py:193-194`) | `activity_start(site_id, kind, trigger) -> bool` `:197`, `activity_update(**fields)` `:209`, `activity_end` `:219`, `activity_list()` `:224` | scan: `ingest.py:1002`, `ops.py:408`, `onboard.py:1150`, `ops_sites.py:277`; heal: `ops.py:420`, `playheal._reserve` `:537`; tmdb: `tmdb_admin.py:159`; onboard slotu: `onboard.py:780`. **Restart siler** |
| Olay akışı | `data/scraper_state/_ops.json` (`state.py:192`): `{"runs":[], "heals":[], "tmdb":[], "onboard":[]}`, her liste son 200 (`OPS_LIMIT` `:191`), kayıt başına `id` (uuid12) | `_ops_append(key, entry)` `:250`, `list_ops(key, site, limit)` `:291` | `record_ops_run`: `ingest.py:1018`, `autoscan.py:117`; `record_ops_heal`: `heal.py:432`, `:488`, `heal_agent.py:490`, `ops.py:375`, `:456`; `record_ops_tmdb`: `tmdb_admin.py:240`; `record_ops_onboard`: `onboard.py:510`, `:865` |
| Yan etki | `record_ops_heal` uygulanmış onarımda `sourcefinder.rerun_for_site` çağırır (`state.py:268-276`) | telemetri fonksiyonu iş tetikliyor | kesimde kalkar |

Okuyanlar: `ops.py` (`overview` `:171`, `active` `:187`, `runs` `:203`, `heals` `:228`, `events` `:290`), `autoscan.last_run_ts` `autoscan.py:42`, `sourcefinder.heal_runs_today` `sourcefinder.py:620`, `heal.cooldown_skip`. Hüküm: site durumu dosyası + `record_resolver`/`record_run`/`record_ingest` çekirdek tarafından kullanılıyor (kalır ya da `jobs`/`crawl_frontier`'a taşınır: karar gerekli); `_active` -> `jobs`; `_ops.json` -> `problem_events` + iş geçmişi.

## 6. Ayarlar ve env'ler

`settings.py` (`data/ops_settings.json`, `settings.py:36`; şema `:1-20`):

| Ayar | Okuma | Env varsayılanı | Kesimde |
|---|---|---|---|
| `sites.<site>.enabled` | `site_settings` `:176`, `enabled_sites` `:192` | `INGEST_SITES` | kalkar (site başına "tazeleme saati" gelir) |
| `sites.<site>.interval_hours` (0.25-168) | `interval_seconds` `:196` | `INGEST_INTERVAL` | kalkar ("tam aktarım süresi") |
| `heal_autoapply` | `heal_autoapply()` `:200` | `SCRAPER_HEAL_AUTOAPPLY` | kalkar (plan karar #4) |
| `tmdb.auto`, `tmdb.types` | `tmdb_auto` `:208`, `tmdb_types` `:214` | `TMDB_ENRICH_TYPES` | **kalır** (`enrich.py:75-82`, `tmdb_admin.py:150`, `tools/tmdb_enrich.py:19`) |

Yazma: `update(patch)` `:315` (`PUT /api/ops/settings`, `onboard.save`), `forget_site(site)` `:297` (`purge.py:267`). `settings.py` çekirdekten import ediliyor: dosya kalır, site/heal alanları boşalır.

Yönetim katmanı env'leri (kesimde kalkacak adaylar, 43 ad):

| Önek | Adlar | Yer |
|---|---|---|
| `ONBOARD_` (9) | `TOOL_TIMEOUT`, `MAX_PAGE_BYTES`, `RETENTION_DAYS`, `ENABLED`, `TIMEOUT`, `PI_BIN`, `MODEL` | `config.py:265-274` |
| | `HARDEN`, `AUTO_ROUNDS` (config.py dışında okunur) | `sb:3194`, `onboard.py:347` |
| `PLAYHEAL_` (10) | `ENABLED`, `WINDOW`, `MIN_SOURCES`, `FAIL_RATIO`, `STREAM_MIN_SOURCES`, `ISSUE_MIN_SOURCES`, `ISSUE_TTL`, `ISSUE_COOLDOWN`, `COVERAGE_RATIO`, `COVERAGE_MIN_SERIES` | `config.py:280-306` |
| `SOURCEFINDER_` (10) | `ENABLED`, `COOLDOWN`, `MAX_JOBS`, `MAX_SITES`, `DAILY_BUDGET`, `HEAL_WAIT`, `HEAL_POLL`, `RERUN_HOURS`, `RERUN_MAX`, `PROBE` | `config.py:323-337` |
| `SCRAPER_HEAL_` (12, hepsi config.py DIŞINDA) | `ENABLED` `heal.py:39`, `AUTOAPPLY` `settings.py:71`, `PROVIDER` `heal.py:287`, `MODEL` `llm_health.py:49`, `PI_BIN` `llm_health.py:192`, `BASE_URL` `heal.py:297`, `TIMEOUT` `:312`, `AUTO_TIMEOUT` `:314`, `AGENT_TIMEOUT` `:507`, `VERIFY_TIMEOUT` `heal_agent.py:67`, `COOLDOWN` `heal.py:459`, `HTML_LIMIT` `heal.py:155` | model + pi yolu yeni Ayarlar'a taşınır |
| Tarama | `INGEST_SITES`, `INGEST_INTERVAL` | `config.py:118-120`, `settings.py:55-60` |

Kalır (kütüphane): `STREAM_HOST_RULE*` (`config.py:295-297`), `RESOLVE_*`, `STREAM_*`, `SERIES_CRAWL_*`, `GATE_*`. Plandaki "~25 env" tahmini düşük: sayılan 43.

## 7. Admin paneli

| Dosya | Satır | Ekran |
|---|---|---|
| `static/admin/index.html` | 80 | Kabuk: 5 sekme (Olay defteri, Kütüphane, Kategoriler, Ayarlar, Siteler), 5 `<script>` |
| `app.js` | 425 | Olay defteri (akış + site süzgeci + detay), üst durum, sekme geçişi, ortak `esc`/`api` (`app.js:7-8`) |
| `library.js` | 314 | Kütüphane (K/T rozeti, video kaynakları) |
| `categories.js` | 175 | Kategoriler (ana ekran satırları) |
| `settings.js` | 345 | Ayarlar (heal anahtarı, LLM sağlığı, TMDB kartı) |
| `onboard.js` | 1032 | Siteler: liste + otomatik tarama + Site ekle/düzenle (log, yaml, adım görünümü, soru kartı) |
| `style.css` | 454 | ortak stil |

`_ASSETS` (`ops.py:27-29`): ad -> MIME sözlüğü; `GET /admin` `index.html` döner (`ops.py:34`), `GET /admin/{name}` yalnız sözlükteki adları sunar, `Cache-Control: no-cache` (`ops.py:40-45`). Auth yok.

Admin'in çağırdığı uçlar:

| Yöntem + yol | Yer | İş |
|---|---|---|
| GET `/api/ops/overview` | `ops.py:171` | site kartları + sistem özeti |
| GET `/api/ops/active` | `ops.py:187` | canlı işler (`activity_list` + finder) |
| GET `/api/ops/events?site=&limit=` | `ops.py:290` | birleşik olay akışı (6 kaynak) |
| POST `/api/ops/sites/{site}/scan` | `ops.py:405` | elle tarama |
| POST `/api/ops/sites/{site}/heal?force=` | `ops.py:414` | drift heal |
| POST `/api/ops/sites/{site}/heal-playback` | `ops.py:433` | oynatma heal ("Ajan düzeltsin") |
| POST `/api/ops/sites/{site}/rollback` | `ops.py:447` | config geri al |
| POST `/api/ops/stream-host-rules/{host}/suspend`, DELETE `/api/ops/stream-host-rules/{host}` | `ops.py:216`, `:222` | host kuralı askıya al / sil |
| GET `/api/ops/sites/manage` | `ops_sites.py:182` | site listesi + sayılar + auto_scan |
| GET `/api/ops/sites/{site}/config`, `/handoff` | `ops_sites.py:220`, `:237` | yaml (maskeli), devir notu |
| POST `/api/ops/sites/{site}/rename`, DELETE `/api/ops/sites/{site}?purge=` | `ops_sites.py:247`, `:269` | ad değiştir, sil |
| GET/PUT `/api/ops/settings` | `ops_settings.py:56`, `:61` | ayarlar |
| GET/POST `/api/ops/llm/health` | `ops_settings.py:72`, `:77` | LLM hesabı (POST = zorla) |
| POST/GET `/api/ops/onboard`, GET `/health`, GET `/{id}?events_after=`, POST `/{id}/message`, `/{id}/cancel`, `/{id}/save`, DELETE `/{id}` | `ops_onboard.py:84-137` | site ekle/düzenle |
| GET `/api/ops/library`, `/api/ops/library/{id}/videos` | `ops_library.py:232`, `:342` | kütüphane |
| GET/POST `/api/ops/categories`, PUT `/categories-order`, PUT/DELETE `/categories/{slug}` | `ops_categories.py:44-85` | kategoriler |
| GET `/api/ops/tmdb/status`, `/preview`; POST `/tmdb/backfill` | `ops_tmdb.py:77-106` | TMDB |

Tanımlı ama admin JS'in çağırmadığı: GET `/api/ops/runs` `ops.py:203`, `/heals` `:228`, `/stream-host-rules` `:208`, `/resolvers` `:464`, `/api/admin/video-sources` (+action) `:472-481`, `/api/admin/identities` `:503-518` (başka çağıran: doğrulanmadı).

**`/admin2` asgari iskelet:** `app/control/router.py`: `APIRouter()`, `GET /admin2` + `GET /admin2/{name}` (`ops.py:26-45` kalıbı, kendi `_ASSETS`), `static/admin2/{index.html,app.js,style.css}`, veri uçları `/api/control/*`; `main.py:20-43` import + `main.py:143-166` `include_router` listesine bir satır. Çakışma yok (eski `/admin/{name}` yalnız kendi adlarını sunar). Kütüphane/Kategoriler/TMDB sekmeleri yönetim katmanı değil: `ops_library`, `ops_categories`, `ops_tmdb` router'ları yeni panelden aynen çağrılabilir (plandaki 4 ekranda yerleri yok: karar gerekli).

## 8. Kesim haritası (eski yönetim modüllerini import eden yerler)

| Çağıran | Çağrılan | Yeni karşılık |
|---|---|---|
| `main.py:28`, `:32`, `:34`, `:160-164` | router `onboard_sandbox`, `ops_onboard`, `ops_sites` | `control` router'ı |
| `main.py:57-58` | `onboard.recover_stale()` | `jobs` kira kurtarması |
| `cache.py:239-241`, `:272`, `:288-295` | `autoscan.tick()`, `TICK_SECONDS` (APScheduler işi `library_ingest`) | `jobs` yürütücü döngüsü |
| `library/ingest.py:26`, `:1002-1018` | `state.activity_start/end("scan")`, `record_ingest`, `record_ops_run` | `jobs` + tarama kaydı (karar gerekli: state.py'nin site dosyası kalır mı) |
| `library/ingest.py:618-619`, `:1041-1045` | `playheal.coverage_signal`, `record_coverage`, `record_crawl`, `maybe_trigger` | `problems` sistem sinyali (kaynaksız dizi / envanter) |
| `library/ingest.py:1035-1036` | `site_handoff.record_scan` | karar gerekli (devir notu kalıyor mu) |
| `library/videos.py:305`, `:329`, `:518-521` | `state.record_resolver`, `get_site_state` | karar gerekli (çekirdek sağlık kaydı) |
| `library/videos.py:340-343` | `playheal.record`, `maybe_trigger` | `problems` olay girişi |
| `library/videos.py:608-609`, `:730-734` | `sourcefinder.request(cid, ep, profile, trigger)` | `problems` aç + istemciye "aranıyor" sözü |
| `library/videos.py:714-717`, `:731` | `playissues.clear_for_token`, `record_failure`, `HEAL_CLASSES` | `problems` (playback-report girişi `routers/streams.py:149`) |
| `library/streamdiag.py:375-383`, `:391-392` | `playheal.record_stream_blocked/clear/maybe_trigger`, `playissues.note_diag` | `problems` olay girişi |
| `routers/notifications.py:19-53` | `sourcefinder.notifications/mark_read/status` | **istemci sözleşmesi** (`/api/notifications`, `/api/notifications/read`, `/api/source-finder/{id}`): uçlar kalmalı, arkası `problems` |
| `scraper/runner.py:12`, `:79`, `:110` | `heal.heal(cfg, page=, html=, reasons=, trigger=)`, `state.record_run` | imza bozulması -> `problems`; `repair` işi |
| `scraper/discover.py:125`, `:284`, `:1124-1127`, `:1257`, `:1271` | `sb._link_kind`, `_check_fields/_core/_playback/_normalize/_series_page/_collections`, `_scoped_selector`, `_anchor_selector` | bu fonksiyonlar kütüphaneye çıkınca oradan (discover.py LLM'siz keşfin hazır çekirdeği, 1502 satır) |
| `scraper/site_handoff.py:95-99` | `pi_agent.scrub` | `scrub` kütüphaneye |
| `scraper/state.py:272-273` | `sourcefinder.rerun_for_site` | kalkar |
| `scraper/__init__.py:18` | `state.get_site_state`, `list_sites_state` | karar gerekli |
| `llm_health.py:48-49` | `heal.PI_DEFAULT_MODEL` | sabit `llm_health`'e taşınır |
| `settings.py:70-71` | env `SCRAPER_HEAL_AUTOAPPLY` | kalkar |
| `library/purge.py:267`, `:273` | `settings.forget_site`, `site_handoff.delete` | kalır / karar gerekli |
| `library/enrich.py:75-223`, `library/tmdb_admin.py:28-240`, `routers/ops_tmdb.py:23-26` | `settings.tmdb_*`, `state.activity_*`, `record_ops_tmdb` | TMDB işi `jobs kind=tmdb` |
| `routers/ops.py:19-21`, `:123`, `:192`, `:337-342`, `:366` | `autoscan`, `playheal`, `state`, `playissues`, `sourcefinder`, `heal`, `drift` | eski admin: kesimde silinir |
| `routers/ops_settings.py:16-19` | `autoscan`, `llm_health`, `settings`, `heal`, `state` | yeni Ayarlar ucu |
| `routers/ops_sites.py:32-35`, `:74`, `:81` | `autoscan`, `settings`, `state`, `onboard_store`, `sourcefinder._inflight` | yeni Siteler ucu |
| `scraper/heal_agent.py:85`, `:401`, `:667` + `onboard.py:525`, `:805`, `:847`, `:1194` | `sb.issue_token/_prepare_recipes/_load_yaml/_analyze/_follow_playback/_edit_summary/module_site` | birlikte silinir; saf parçalar kütüphaneye |
| `tools/gen_onboard_refs.py:182-331` | `onboard_sandbox`, `heal`, `heal_agent`, `pi_agent` | yeni promptlarla yeniden yazılır ya da kalkar; `--check` test kuralı güncellenir |
| `tools/scraper_smoke.py:22` | `scraper.heal` | karar gerekli |
| `tools/tmdb_enrich.py:19` | `settings` | kalır |
| Testler: `server/tests/` 83 dosyadan ~47'si yönetim modülü import ediyor | yoğun olanlar: `test_onboard*.py` (9), `test_heal*.py` (2), `test_playheal*.py` (2), `test_playback_heal_signals.py`, `test_stream_blocked_heal.py`, `test_sourcefinder.py`, `test_ops*.py` (5), `test_provider_match.py`, `test_site_handoff.py`, `test_sitequality.py`, `test_discover_site.py`, `test_regex_lint.py`, `test_collection_post.py`, `test_category_collections.py` | `tests_archive/`'e (dizin bugün YOK). Yalnız `state` import eden ~12 çekirdek test (`test_resolvers.py`, `test_series_crawl.py`, `test_rows_roles.py` ...) state kararına bağlı. `test_client_contract.py` `sourcefinder` import ediyor: sözleşme testi, korunmalı |

## 9. `deploy.sh`

| Konu | Yer | Not |
|---|---|---|
| Akış | `do_deploy` `deploy.sh:1229`: [0] ön kontrol, [1] yedek, [2] fark, [3] kopyala, [4] import, [5] restart, [6] doğrulama | |
| Restart | `do_restart` `:1158-1188` | `systemctl restart`, 6 sn bekle, `is-active`, journal'da error/traceback tara. **Çalışan iş kontrolü yok** |
| Uç doğrulama | `verify_endpoints` `:1190-1217` | `/api/health` 25 sn bekler; sonra `/admin`, `/api/health`, `/api/ops/overview`, `/api/ops/settings`, `/api/ops/tmdb/status`, `/api/ops/library?limit=1` (liste `:1199`). Kesimde bu liste güncellenmezse deploy "DOĞRULAMA BAŞARISIZ" verir |
| Çalışan iş kontrolü eklenecek yer | `do_deploy` `:1268-1269` arası ([4] import ile [5] restart arası) ya da `do_restart` başı `:1163` | `rssh` ile salt-okur uç (bugün `/api/ops/active`; yeni: `jobs` sayımı) + "yeni iş alma" sinyali; `--force-restart` bayrağı argüman bloğuna `:122-160` |
| `server/pi/` | `make_archive` `:1074`: tüm `server/` tar ile gider; dışlananlar `.env`, `data`, `venv`, `__pycache__`, KORU config'leri (`build_tar_args` `:1063-1071`) | pi için ayrı adım yok |
| **Silme yok** | `send_files` `:1109-1136`: tar yalnız üstüne yazar ("gönderiliyor (silme yok)"); silinen dosyalar elle listelenir (`OLD_FILES` `:1111`) | Kesimde silinen modüller sunucuda KALIR; `OLD_FILES` listesine eklenmeli, yoksa eski router dosyaları diskte durur (import edilmedikçe zararsız ama `import app.main` denetimi yanıltıcı) |
| Tombstone / REMOTEONLY | `cfg_classify` `:546`, `cfg_remove_deleted` `:826`, `cfg_pull_step` `:863` | config referans kuralı değişmiyor |

## 10. Zamanlama / eşzamanlılık

APScheduler işleri (yalnız 2; `cache.start` `cache.py:266-296`): `catalog_refresh` (`refresh_and_prewarm`, `CACHE_TTL` 900 sn, `max_instances=1`), `library_ingest` (`ingest_tick` -> `autoscan.tick`, 60 sn, `max_instances=3`).

| Mekanizma | Yer | Kapsam | Restart |
|---|---|---|---|
| Tarama tick kilidi | `autoscan._tick_lock` `autoscan.py:26`; `tick()` `:128` siteleri SIRAYLA tarar (5 sn ara) | aynı anda tek zamanlanmış tarama | tick yeniden başlar |
| Site başına iş slotu | `state._active[(site, kind)]` `state.py:194` | scan / heal / tmdb / onboard için "zaten çalışıyor" | silinir |
| Onboarding tek slotu | `("_onboard","onboard")` `onboard.py:59`, `_take_slot` `:779`, kayıp/bitmiş sahibi toplama `_reap_lost` `:732`, `_reap_finished` `:747` | sistem genelinde tek ajan koşusu | taslak `failed(server_restart)` (`recover_stale` `onboard.py:930`) |
| Onboarding thread'i | `threading.Thread onboard-<draft>` `onboard.py:701`; `_procs`, `_threads`, `_cancelled`, `_reaped` + `_lock` (RLock) `:79-83`; `_save_lock` `:84` | koşu + otomatik turlar aynı thread | kaybolur |
| Kayıt sonrası tarama | `Thread onboard-scan-<site>` `onboard.py:1163` | | kaybolur |
| Sandbox havuzu | `ThreadPoolExecutor(4) "onboard"` `sb:273`; `onboard-resolve` (1) `sb:4044`; token kilidi `sb:275` | araç çağrıları | - |
| Playheal | `_lock`, `_windows`, `_blocked`, `_meta`, `_inflight`, `_threads` `playheal.py:86-91`; `Thread playheal-<site>` `:634`; `_reserve` `:537` (site başına tek heal, `state` slotu) | pencere ve stream_blocked olayları yalnız bellekte | silinir |
| Sourcefinder | `_lock`, `_inflight`, `_title_search`, `_heal_runs` `sourcefinder.py:56-59`; daemon thread `:81`; `finder-resolve` havuzu `:250`; heal'i uyuyarak bekler `_wait_for_heal` `:568` | `SOURCEFINDER_MAX_JOBS` (2) | `finder_jobs` satırı `searching` kalır (teşhis X1) |
| pi koşusu | watchdog `threading.Timer` + stderr drain thread `pi_agent.py:514-521` | koşu başına | süreç yetim kalabilir (doğrulanmadı) |
| Diğer | `provider_match` havuzu (3) `provider_match.py:224`; `playissues._lock` `:61`; `llm_health._lock` `:37`; `settings._lock` + `flock` `settings.py:44`, `:156`; config `flock` `scraper/config.py:349`, `:452`, `:659`; `onboard_store._lock` `:29`; `site_handoff._lock` `:51` | | |
| FastAPI BackgroundTasks | `ops.py:405-444` (scan/heal/heal-playback), `ops_tmdb.py:77` | elle tetiklenen işler | kaybolur |

Özet: kalıcı kuyruk yok; eşzamanlılığın tamamı süreç belleğindeki sözlük + thread. `jobs` tablosu `state._active`, onboarding slotu, playheal `_inflight`, finder `_inflight`, BackgroundTasks ve `autoscan` tick'inin altısının da yerini alır.
