# T06: Tek atımlık LLM çağırıcı (`core/llm`)

**Bağımlı:** T00. **Açar:** T08, T12, T14. **Oku:** README K6, §5.1 (`llm_calls`); `inventory/01 §6`, `inventory/03 §2`;
`server/app/scraper/heal.py:134-300` (`_call_pi`, `_extract_yaml`), `server/app/scraper/llm_health.py`, `server/app/scraper/pi_agent.py:119-145` (`child_env`, `scrub`).

## Amaç
Eski sistemde LLM uzun, araçlı, oturumu sürdürülen bir ajandı (15 dakikaya kadar, 25 KB skill). `server2`'de LLM yalnız **kısa, araçsız,
oturumsuz, tek atımlık** çağrılır: sunucu kanıtı hazırlar, LLM küçük bir yaml parçası döner, sunucu ölçer. Bu görev o tek çağrı yolunu yazar.

## Yapılacaklar
1. `core/llm/client.py`:
   ```python
   complete(prompt: str, *, purpose: str, site_id: str | None = None, stage: str | None = None, timeout: int = 120,
            max_prompt_chars: int = 60_000) -> Reply          # Reply(ok, text, error, ms, model)
   ```
   Uygulama: `pi -p --model <LLM_MODEL> --no-session --no-tools --offline` (prompt stdin, yanıt stdout; eski `_call_pi` kalıbı). Alt süreç
   ortamı sırlardan arındırılır (`child_env` mantığı: sır görünümlü değişkenler atılır). Süre aşımında süreç öldürülür (yetim süreç kalmaz:
   süreç grubu). Asla raise etmez. `max_prompt_chars` aşılırsa çağrı yapılmaz, `error="prompt_too_large"`.
   **Kısa oturum:** `complete(..., session: str | None = None)`. `session` verilirse çağrı `--no-session` yerine
   `--session-dir DATA_DIR/llm/sessions --session-id <session>` ile yapılır: aynı aşama koşusunun 2. turu yalnız yeni bilgiyi (ölçüm sonucu,
   ipucu) gönderir, HTML kanıtı yeniden gönderilmez. `end_session(session)` oturum dosyasını siler; 1 saatten eski oturum dosyaları açılışta
   ve `jobs_gc` ile temizlenir. Oturum bir aşama koşusundan uzun yaşamaz (çağıran sorumlu; kimlik `<site>:<stage>:<job attempt>`).
   Oturum dosyası bulunamazsa (restart, temizlik) çağıran tam prompt'la oturumsuz yeniden dener: oturum yalnız tasarruftur, doğruluk ona dayanmaz.
   Skill, extension, bağlam dosyası ve araç her iki kipte de kapalıdır (`--no-tools --no-skills --no-context-files --no-extensions`).
2. Her çağrı `llm_calls` tablosuna yazılır (pi çıktısı token kullanımı veriyorsa `input_tokens`, `output_tokens`, `cached_tokens` kolonlarına da;
   vermiyorsa boş kalır: oturumlu/oturumsuz maliyet farkı bu kolonlardan ölçülür) (migrasyon bu görevde): amaç, site, aşama, model, karakter sayıları, süre, sonuç. Prompt ve yanıt
   METNİ tabloya yazılmaz (boyut + sır riski); hata ayıklama için son 20 çağrının metni `DATA_DIR/llm/last/<id>.txt` (dönen dosya, `scrub`'dan geçmiş).
3. `core/llm/budget.py`: `allowed(site_id, *, background: bool) -> (bool, reason)`: arka plan (onarım) çağrıları için site başına günlük
   sınır `settings.llm_daily_calls_per_site`; admin'in başlattığı (onboarding/edit/kural çevirisi) çağrılar sınıra takılmaz ama sayılır.
   Sayım `llm_calls`'tan (kalıcı), bellekten değil. `usage(site_id=None, days=7)` özet döner (T15 Ayarlar ekranı için).
4. `core/llm/parse.py`: `extract_yaml(text) -> (dict | None, error)` (çitli blok ya da düz yaml; alias'sız güvenli yükleyici, boyut sınırı),
   `extract_json(text)`. Yanıtın başka metin içermesi hata değildir; ilk geçerli blok alınır.
5. `core/llm/health.py`: `check(force=False)` (eski `llm_health.check`: `pi auth check`, 10 sn, 60 sn önbellek), dönen
   `{ok, provider, model, error}`; `PI_DEFAULT_MODEL` sabiti buraya taşınır.
6. `core/llm/prompts.py`: ortak iskelet `build(task: str, rules: list[str], evidence: dict[str, str], answer_format: str) -> str`.
   Her prompt aynı dört bölümden oluşur (görev, kurallar, kanıt, yanıt biçimi); kanıt parçaları ayrı ayrı kırpılır. Aşamaya özel içerik
   kendi görevinde (T08/T09/T12/T14) yazılır. **25 KB'lık skill dosyası ve referans belgeleri ALINMAZ.**
7. Sahte çağırıcı: `DIZIFLIX_FAKE_LLM=1` iken `complete` ağ/alt süreç açmaz; `core.llm.fake.script([...])` ile sıraya konmuş yanıtları
   döner, sıra boşsa `error="fake_empty"`. `_sandbox.py` bunu açar. Sahte çağrılar da `llm_calls`'a yazılır (bütçe testleri için).

## Kapsam dışı
Aşama prompt'ları, araçlı ajan, extension, oturum sürdürme (hiçbiri yazılmayacak).

## Kabul
- Sahte çağırıcıyla `complete` yanıtı döner ve `llm_calls` satırı yazılır; prompt metni tabloda yok.
- `prompt_too_large` durumunda alt süreç açılmaz.
- Süre aşımı: sahte "uyuyan" komutla süreç öldürülür, `error="timeout"`, zombi yok.
- Alt süreç ortamında `TMDB_ACCESS_KEY` ve `*_TOKEN`/`*_KEY`/`*_SECRET` adlı değişkenler yok (test: ortamı yazdıran sahte komut).
- Bütçe: arka plan çağrısı sınırı aşınca `allowed` False; admin çağrısı True; sayım restart'tan (yeni bağlantı) sonra aynı.
- Oturum: aynı `session` ile iki çağrı aynı oturum kimliğiyle komut üretir; `end_session` dosyayı siler; oturum dosyası yokken çağrı hata
  vermez (çağırana `session_lost` bilgisi döner); komut satırında araç/skill/bağlam dosyası açan bayrak yok (komut üretimi testi).
- `extract_yaml`: çitli blok, düz yaml, çöp metin, alias bombası (reddedilir).
- `health.check` pi yokken `ok=False` + anlaşılır hata, istisna yok.
