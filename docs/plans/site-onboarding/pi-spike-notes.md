# Faz 2b: pi spike notları

Sunucu 61, pi 0.99.1, model `openai-codex/gpt-5.6-luna`. Deneme alanı `/root/pi-spike/` (ext/, skills/, sessions/, out/, fake_sandbox.py; gizli bilgi yok).
Toplam 10 LLM koşusu (bir tanesi öldürüldü, biri sağlayıcı hatasıyla bitti). `~/.pi/agent/settings.json` ve `auth.json` md5'leri deneme öncesi/sonrası aynı; `pi install` yok; diziflix'e dokunulmadı.

## 1. Sonuç özeti (kabul maddeleri)

| # | Madde | Sonuç |
|---|---|---|
| 1 | Extension tool'ları (`get_number`, `http_echo`, header `X-Onboard-Token`) | ÇALIŞTI. Derleme yok (pi `jiti` ile .ts'i doğrudan yükler). |
| 2 | Skill + `read` yokken skill dosyaları | KISMEN. `read` yoksa skill sistem istemine hiç yazılmaz; `/skill:ad ...` ise SKILL.md gövdesini ilk kullanıcı mesajına gömer; `references/*.md` OKUNAMAZ. Çözüm: `read` + yol kısıtlayan guard extension (çalıştı). |
| 3 | Kısıtlı tool seti | ÇALIŞTI. `--no-builtin-tools --tools ...` ile modele yalnızca listedeki tool'lar bildirilir (bash/write yok). Listede olmayan tool'u model çağıramadı (2 denemede "yok" dedi); pi kaynağı: bilinmeyen tool çağrısı = hata sonucu `Tool X not found`. |
| 4 | JSON olay akışı | ÇALIŞTI. Aşağıda örnekler. Hata biçimi beklenenden FARKLI (bkz. 4.2). |
| 5 | Oturum sürdürme | ÇALIŞTI. Aynı `--session-id` ile 2. koşu "84" dedi (bağlam korundu). |
| 6 | İptal / süre | ÇALIŞTI. SIGTERM: süreç anında ölür (exit 143 / Python'da -15), oturum dosyası geçerli kalır, sonradan sürdürülebilir. |
| 7 | 15000 karakter tool çıktısı | ÇALIŞTI. Kırpma/hata yok (model `END_MARKER`'ı gördü). 50 KB üstü denenmedi. |
| 8 | RPC (isteğe bağlı) | LLM'siz denendi: `get_state`, `get_commands` çalışıyor; araya mesaj için `prompt` + `streamingBehavior: steer/followUp` (doküman). Uygulama `-p` + oturum sürdürme ile yapılmalı. |

## 2. Kesin komut satırı

cwd her koşuda aynı (`/root/pi-spike`; oturum "proje = cwd" kapsamlı, başka cwd denenmedi). Mesaj stdin'den (argüman da olur).

```bash
cd /root/pi-spike
printf '%s' "$MESAJ" | pi -p --mode json --offline \
  --no-builtin-tools --tools get_number,http_echo,read \
  --no-extensions -e /root/pi-spike/ext/spike.ts -e /root/pi-spike/ext/guard.ts \
  --no-skills --skill /root/pi-spike/skills/spike-skill \
  --no-context-files --no-prompt-templates \
  --model openai-codex/gpt-5.6-luna \
  --session-dir /root/pi-spike/sessions --session-id od_test123 \
  > out.jsonl 2> err.txt
```
- İlk mesaj: `/skill:spike-skill sihirli kelimeyi söyle` (çok satırlı olabilir). Devam mesajı: aynı komut, aynı `--session-id`, düz metin ("az önceki sayıyı 2 ile çarp").
- Extension ortam değişkenlerini `process.env`'den okur (child_env'den gelir): `DIZIFLIX_SANDBOX_URL/ONBOARD_TOKEN/DRAFT_ID` gerçek pi altında doğrulandı.
- İlk koşuda stderr'e zararsız uyarı: `Warning: No project session found with id '...'; creating a new session with that id.`
- `--api-key` kullanma: OAuth sağlayıcıda "No API key found for openai-codex" ile pi'yi çıkış 1 ile durdurdu.
- Oturum dosyası: `<session-dir>/<ISO-zaman>_<session-id>.jsonl` (v3 ağaç: `session`, `model_change`, `thinking_level_change`, `message{system,user,assistant,toolResult}` girdileri, `id`/`parentId`). Dosya adında draft id geçer.

## 3. İskeletler

`ext/spike.ts` (TypeBox'lı; TypeBox'sız düz JSON Schema da çalışıyor, bkz. tablo):
```ts
import { Type } from "@earendil-works/pi-ai";            // "typebox" paketi de var; gerekmez
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "http_echo", label: "HTTP echo", description: "...",
    parameters: Type.Object({ url: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, signal /*, onUpdate, ctx */) {
      const res = await fetch("http://127.0.0.1:8090/api/health", { headers: { "X-Onboard-Token": "test" }, signal });
      return { content: [{ type: "text", text: `HTTP ${res.status} ${(await res.text()).slice(0, 200)}` }], details: {} };
    },
  });
}
```
`ext/guard.ts` (yerleşik `read`'i skill dizinine kısıtlar; engellenen çağrı `isError:true` + `reason` metniyle döner):
```ts
import { resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
const ROOT = resolve("/root/pi-spike/skills/spike-skill");
export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (event.toolName !== "read") return undefined;
    const p = resolve(String((event.input as Record<string, unknown>).path ?? ""));
    if (p === ROOT || p.startsWith(ROOT + sep)) return undefined;
    return { block: true, reason: `read is limited to ${ROOT}; ${p} is outside` };
  });
}
```
Skill: `skills/spike-skill/SKILL.md` (frontmatter `name`, `description`; gövdede `references/notes.md`'ye göreli yol) + `references/notes.md`. `name` küçük harf/rakam/tire.

## 4. JSON olay akışı

stdout katı JSONL (satır sonu LF). Okuma: bir satır = bir nesne; ` ` güvenli ayrıştırma için `readline` değil LF ile böl (Python `for line in proc.stdout` + `json.loads` yeterli, JSON `\n` kaçırır).

### 4.1 Normal koşu (kısaltılmış, gerçek)
```
{"type":"session","version":3,"id":"od_test123","timestamp":"2026-10-01T13:15:43.522Z","cwd":"/root/pi-spike"}   # ilk satır, "olay" değil başlık
{"type":"agent_start"} {"type":"turn_start"}
{"type":"message_start","message":{"role":"system","content":"","sections":{...},"toolsAdded":[{"name":"get_number","description":"..","parameters":{..}}]}}  # yalnız YENİ oturumda; sürdürülen koşuda yok
{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
{"type":"message_start","message":{"role":"assistant","content":[],"provider":"openai-codex","model":"gpt-5.6-luna","stopReason":"pending"}}
{"type":"message_update","usage":{..},"assistantMessageEvent":{"type":"thinking_delta","contentIndex":0,"delta":".."}}
{"type":"message_update","usage":{..},"assistantMessageEvent":{"type":"toolcall_end","contentIndex":1,"toolCall":{"type":"toolCall","id":"call_X|fc_Y","name":"get_number","arguments":{}}}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"thinking",..},{"type":"toolCall","id":"call_X|fc_Y","name":"get_number","arguments":{}}],"usage":{"input":532,"output":30,..},"stopReason":"toolUse"}}
{"type":"tool_execution_start","toolCallId":"call_X|fc_Y","toolName":"get_number","args":{}}
{"type":"tool_execution_end","toolCallId":"call_X|fc_Y","toolName":"get_number","result":{"content":[{"type":"text","text":"42"}],"details":{}},"isError":false}
{"type":"message_end","message":{"role":"toolResult","toolCallId":"..","toolName":"get_number","content":[{"type":"text","text":"42"}],"isError":false}}
{"type":"turn_end","message":{assistant},"toolResults":[..]}
{"type":"turn_start"} ... {"type":"message_update",..,"assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"42"}} ... {"type":"text_end","contentIndex":0,"content":"<tam metin>"}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"<SON YANIT>"}],"stopReason":"stop"}}
{"type":"turn_end",...}
{"type":"agent_end","messages":[system,user,assistant,toolResult,...],"willRetry":false}
{"type":"agent_settled"}                       # son satır; süreç bundan sonra çıkar (exit 0)
```
Ayrıştırma notları:
- Tool çağrısı: `tool_execution_start` (`toolCallId`, `toolName`, `args`) ve `tool_execution_end` (`toolName`, `result.content[].text`, `isError`). Hata (extension `throw` veya guard `block`) = `isError:true`, metin = hata mesajı.
- Aynı mesajdaki birden çok tool çağrısı paralel: `start, start, end, end` (id ile eşle).
- Son yanıt metni: son `assistant` `message_end`'in `message.content` içindeki `type:"text"` blokları (yetkili; = `turn_end.message`, = `agent_end.messages[-1]`, = `text_end.content`). `thinking`/`toolCall` blokları metin değil.
- `message_update` yalnız delta; `thinking_*` ve `toolcall_*` alt türleri de var.
- Sürdürülen koşuda `agent_end.messages` yalnız o koşunun mesajlarını taşır.

### 4.2 Hata biçimi (beklenenden farklı)
- Sağlayıcı/model hatası (denenen: desteklenmeyen model): AYRI `error` olayı YOK, stderr boş, ÇIKIŞ KODU 0. Hata asistan mesajında:
```
{"type":"message_end","message":{"role":"assistant","content":[],"stopReason":"error","errorMessage":"Codex error: The 'gpt-nonexistent-xyz' model is not supported when using Codex with a ChatGPT account."}}
{"type":"turn_end","message":{...aynı...}} {"type":"agent_end","messages":[...],"willRetry":false} {"type":"agent_settled"}
```
- Yeniden deneme olayları (doküman, gözlenmedi): `auto_retry_start{attempt,maxAttempts,delayMs,errorMessage}`, `auto_retry_end{success,attempt,finalError}`; `agent_end.willRetry`.
- Kimlik bilgisi yok: stdout'ta yalnız `session` satırı, stderr `No API key found for openai-codex...`, çıkış kodu 1.
- Bilinmeyen `--model`: stderr uyarısı `Model "x" not found ... Using custom model id.`, sonra sağlayıcı hatası (yukarıdaki).

## 5. Skill + kısıtlı tool: net cevap (madde 2-3)

- `read` aktif DEĞİLKEN: skill sistem isteminde listelenmez (`<skills>` bölümü yok); model skill'in varlığını bilmez. `--skill` yine de yüklenir (`get_commands` listeledi).
- `/skill:ad <metin>` print/json modda ve stdin'den çalışır: ilk kullanıcı mesajı `<skill name=".." location="..">References are relative to <dizin>.\n\n<SKILL.md gövdesi, frontmatter'sız></skill>\n\n<metin>` olur (çok satırlı metin dahil). Yani SKILL.md için `read` ve `--append-system-prompt` GEREKMEZ. Ama `references/*.md` ve `examples/*` okunamaz (model improvize etmeye çalıştı: `http_echo`'ya dosya yolu verdi).
- `read` aktifken (`--tools ...,read`): skill `<available_skills>` olarak listelenir (ad, açıklama, `location`), model SKILL.md ve referansları mutlak yolla okur. `read` tüm dosya sistemine açıktır; guard extension (`pi.on("tool_call")` + `{block:true,reason}`) ile skill dizinine kısıtlandı: SKILL.md ve `references/notes.md` okundu, `/etc/hostname` engellendi (model engeli açıkça raporladı).
- Alternatifler (denenmedi): extension'a `read_skill_file(path)` tool'u (yalnız skill dizini; `read`'e gerek kalmaz) veya referansları `--append-system-prompt <dosya>` ile vermek (her turda token maliyeti).
- Sistem istemi varsayılan olarak "expert coding assistant" önsözü + pi doküman yolları taşır (`--no-builtin-tools` ile tools bölümü `(none)`); `--system-prompt/--append-system-prompt` ile değiştirilebilir (denenmedi).

## 6. Süre, iptal, sınırlar

- Süre (gpt-5.6-luna, düşünme `medium`): tool'suz 1 tur 5.7 s; 2 tool çağrılı 3 tur 10-15 s; 4 tool çağrılı 4 tur 19 s; tur başına ~3-5 s. pi başlangıç yükü ~0.2 s. Gerçek sandbox tool'ları (fetch_page tarayıcı modu) bunun üzerine biner; `ONBOARD_TIMEOUT` 900 s yeterli görünüyor.
- SIGTERM (tool çalışırken): anında ölür (7 ms, exit 143), stdout `agent_end`siz yarım kalır, oturum dosyası geçerli JSONL kalır (son girdi tool çağrılı asistan mesajı, sonuçsuz). Aynı `--session-id` ile sonraki koşu sorunsuz sürdü (pi askıdaki tool çağrısını onardı). Yani zaman aşımı/iptal sonrası "devam" mümkün.
- Aynı mesajdaki tool çağrıları paralel yürür; sandbox eşzamanlı çağrıya dayanıklı olmalı.
- Özel tool çıktısını pi kırpmaz (kendi 50 KB/2000 satır sınırı yalnız yerleşik tool'lar; özel tool'lar kendi kırpmalı: bizde 20000 karakter `shrink`).
- Model `provider/id` biçimi sorunsuz (`openai-codex/gpt-5.6-luna`).

## 7. Doğrulama tablosu (yerel kod varsayımları ↔ gerçek pi 0.99.1)

### 7.1 `server/app/scraper/onboard.py`

| Varsayım (satır) | Sonuç | Gerçek |
|---|---|---|
| `-p` + mesaj stdin'den (139, 425) | doğru | `-p --mode json` birlikte kabul; stdin mesajı ilk istem olur. |
| `--mode json` stdout'a JSON satırları (144) | doğru | Katı JSONL; ilk satır `{"type":"session",...}` başlığı (parser'ın `{`-ile başlayan-bilinmeyen-tür yoluyla yok sayması yeterli). |
| `--no-builtin-tools` yerleşik tool yok (145) | doğru | Sistem istemi tools bölümü `(none)`; modele yalnız extension tool'ları bildirildi. |
| `--tools` izin listesi (146) | doğru | Allow-list çalışır; listede olmayan tool çağrılamaz. UYARI: `read` listede yoksa skill referansları okunamaz (bkz. bölüm 5); bu varsayım kodda yok. |
| `--no-extensions -e yol` yalnız bizim extension (147) | doğru | Açık `-e` yolu `-ne` ile birlikte yüklendi. |
| `--no-skills --skill dizin` (148) | doğru | Skill yüklendi (`/skill:` genişledi, `get_commands` listeledi). Sistem isteminde ilan edilmesi için `read` gerekir. |
| `--no-context-files` (149) | doğru (etkisi doğrulanmadı) | Bayrak geçerli (cli.md); test cwd'sinde AGENTS.md/CLAUDE.md olmadığından etkisi ölçülmedi. |
| `--no-prompt-templates` (150) | doğru | Kabul edildi. |
| `--model` (151) | doğru | `provider/id` çalışır. Bilinmeyen model: uyarı + sağlayıcı hatası (çıkış 0!). |
| `--session-dir` kendi oturum deposu (152) | doğru | Dosyalar doğrudan o dizinde: `<ISO>_<id>.jsonl`. |
| `--session-id` aynı id = aynı oturum (153) | doğru | Bağlam korundu. İlk koşuda stderr'e zararsız "creating a new session" uyarısı. cwd sabit tutulmalı (başka cwd denenmedi). |
| (eksik) `--offline` | öneri | `heal._call_pi` kullanıyor; model kataloğu yenilemesini kapatır. Tüm denemeler `--offline` ile çalıştı. |
| `/skill:` print modunda (158) | doğru | Genişler; çok satırlı mesajla da. Gövde frontmatter'sız, `<skill name location>` içinde. `first_message`'ın 2. satırı (düz yönerge) gereksiz ama zararsız. |
| `delete_draft` oturum dosyası adında draft id (630-635) | doğru | Ad `<ISO>_<draft_id>.jsonl`. |
| `_terminate` SIGTERM→SIGKILL (384) | doğru | SIGTERM yeter (anında ölür); oturum bozulmaz, sürdürülebilir. |
| `_ev_type` `type` (172) | doğru | `ev["type"]`; `event` yazımı yok. |
| `tool_execution_start`: `toolName`, `toolCallId`, `args` (176-190, 312) | doğru | Üçü de aynı adla. |
| `tool_execution_end`: `toolName`, `result.content[].text`, `isError` (202-219, 324) | doğru | `isError` üst düzey; `result` = `{content:[{type:"text",text}],details}`. |
| `message_update` → `assistantMessageEvent.type=="text_delta"`, `delta` (222-226, 329) | doğru | `thinking_delta`/`toolcall_delta` ayrı tür, yok sayılması doğru. |
| `message_end` → `message.role/content` blok listesi (229-234, 331) | doğru | Blok türleri `thinking`/`toolCall`/`text`; `_content_text` yalnız `text`'i alıyor, doğru. `system`/`user`/`toolResult` rolleri de `message_end` ile gelir, `role in ("", "assistant")` süzgeci doğru. |
| `turn_end` / `agent_end` (337) | doğru | Var. Ayrıca `agent_settled` (son olay) ve `agent_end.willRetry`; parser kullanmıyor, sorun değil. |
| `auto_retry_start` `attempt/maxAttempts/errorMessage` (341-344) | doğru (yalnız doküman) | Alanlar json.md ile uyumlu, gözlenmedi. |
| `auto_retry_end` `success/finalError` (345-348) | doğru (yalnız doküman) | json.md ile uyumlu, gözlenmedi. |
| `kind == "error"` olayı hata bildirir (349-351) | YANLIŞ | Üst düzey `error` olayı yok. Hata `message_end`/`turn_end`/`agent_end.messages[-1]` asistan mesajında `stopReason:"error"` + `errorMessage` (content `[]`), stderr boş, çıkış kodu 0. Bugünkü kodla `_finish` bunu `needs_input` (boş soru) yapar. Düzeltme: `stopReason=="error"` (veya `errorMessage`) yakala, `error` olayı üret, `_finish`'te `failed` say. |
| Kimlik bilgisi yoksa çıkış kodu ≠ 0 (497-500) | doğru | Çıkış 1, stderr "No API key found for ...", stdout yalnız `session` satırı. |
| Paralel tool çağrıları `names[id]` ile eşlenir (281, 315) | doğru | Gözlendi: `start,start,end,end`; `toolName` end'de de var. |
| `turns` = `turn_end` sayısı (289) | doğru | Her LLM turu için bir `turn_end`. |
| Son yanıt = `last_say` (298, 502) | doğru | Son asistan `message_end` metni; delta ile `message.content` aynı. |

### 7.2 `server/pi/extensions/diziflix-onboard.ts`

| Varsayım (satır) | Sonuç | Gerçek |
|---|---|---|
| Tip paketi `@mariozechner/pi-coding-agent` (18, 29) | YANLIŞ (zararsız) | Doğrusu `@earendil-works/pi-coding-agent`. `import type` silindiği için yanlış adla da yükleniyor (gerçek pi altında doğrulandı); tipi doğru pakete çevir. |
| `pi.registerTool({ name, label, description, parameters, execute })` (20, 294) | doğru | Aynen; gerçek dosya fake sandbox'a karşı 7 tool ile yüklendi. |
| `execute(toolCallId, params, signal)` (20) | doğru | Gerçek imza `execute(toolCallId, params, signal, onUpdate, ctx)`; ilk üçü uyumlu. `signal`'in iptalde tetiklenmesi doğrulanmadı (SIGTERM süreci doğrudan öldürüyor). |
| Sonuç `{ content:[{type:"text",text}], details }` (21) | doğru | `details: {}` kabul. |
| `throw Error` = hata sonucu, model mesajı görür (22-23) | doğru | `isError:true`, içerik = mesaj; model hata metnini aynen aktardı (`outline_page: HTTP 404 not_found: ...`). |
| `parameters` düz JSON Schema, TypeBox import'u gerekmez (24-26) | doğru | `required: []`, `additionalProperties:false`, `enum` değiştirilmeden modele iletildi (`toolsAdded`). `Type.Unsafe` gereksiz. |
| Yalnız silinebilir TS sözdizimi, derleme yok (26-27) | doğru | pi `jiti` ile .ts'i doğrudan yükler. |
| `process.env.DIZIFLIX_*` extension içinde okunur (10-14, 112-113) | doğru | Çocuk sürecin ortamı extension'a geçti. |
| `fetch` + `X-Onboard-Token` + JSON gövde (130-135) | doğru | Fake sandbox: GET gövdesiz, POST `application/json`, header ulaştı. |
| `as never` ile tip kaçışı (304) | doğru | Çalışma zamanında etkisiz. |

## 8. Faz 2c önerileri (en fazla 5)

1. Hata yakalama: EventParser asistan `message_end`/`turn_end` üzerinde `stopReason=="error"`/`errorMessage` görünce `error` olayı üretsin ve `_finish` bunu `failed` (reason `pi_failed`) yapsın; aksi halde sağlayıcı hatası boş soruyla `needs_input` olur (çıkış kodu 0!).
2. Skill referansları: `--tools ...,read` ekle ve `diziflix-onboard.ts` içine `pi.on("tool_call")` guard'ı koy (yalnız `SKILL_DIR` altına izin; yol `resolve()` + `startsWith(dir+sep)`); ya da `read_skill_file` tool'u yaz. `/skill:` gövdeyi zaten gömüyor, sistem istemi ilanı `read` ile gelir. `read` yokken referansları okuma varsayımı çalışmaz.
3. `build_command`'a `--offline` ekle; cwd'yi (`work/`) her koşuda aynı tut (oturum bulma proje=cwd); `--api-key` kullanma.
4. Tip import adını `@earendil-works/pi-coding-agent` yap; düz JSON Schema kalabilir (doğrulandı).
5. İptal/zaman aşımı: SIGTERM yeterli ve oturum güvenli; `failed(timeout)` sonrası `message()` ile devam gerçekten çalışır (kill sonrası sürdürme doğrulandı). Sandbox paralel tool çağrısına dayanıklı olmalı; tur başına 3-5 s LLM gecikmesi varsay.

## 9. Bilinmeyenler (denenmedi)

`--no-context-files` etkisi; farklı cwd ile oturum bulma; listede olmayan tool'un gerçek hata sonucu (yalnız kaynak kodu); `--append-system-prompt`/`--system-prompt`; `--thinking low` hız etkisi; 50 KB üstü özel tool çıktısı; auto-retry olaylarının canlı biçimi; RPC'de gerçek `steer` akışı.
