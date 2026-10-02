# Faz 2b: pi spike (sunucuda, izole deneme)

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

## Amaç
Faz 2c'den önce, sunucudaki pi'nin (0.99.1) bizim kullanacağımız biçimde çalıştığını **küçük bir prototiple**
doğrulamak ve kesin komut satırını, extension API'sini ve olay biçimini belgelemek. Ürün kodu YAZILMAZ.

## Ortam
- Sunucu: `ssh root@192.168.0.61` (anahtarla, parolasız).
- pi: `/usr/local/bin/pi`, sürüm 0.99.1, paket `@earendil-works/pi-coding-agent`.
- Dokümantasyon (OKU): `/usr/local/lib/nodejs/node-v22.23.1-linux-x64/lib/node_modules/@earendil-works/pi-coding-agent/`
  - `docs/extensions.md` (`pi.registerTool`, TypeBox parametreleri, `execute()`)
  - `docs/skills.md`, `docs/json.md` (`--mode json` olayları), `docs/sessions.md`, `docs/rpc.md`, `docs/cli.md`,
    `docs/security.md`
  - `examples/extensions/`
- Heal'in kullandığı model: sunucu `.env`'deki `SCRAPER_HEAL_MODEL` (şu an `openai-codex/gpt-5.6-luna`).
  Değeri oku ama `.env`'in başka hiçbir satırını yazdırma.

## Sınırlar
- Her şey `/root/pi-spike/` altında olur. `diziflix` servisine, koduna, `.env`'ine, `~/.pi/agent/settings.json`'a ve
  global extension/skill'lere DOKUNMA. `pi install` ÇALIŞTIRMA. Yalnızca `-e`/`--skill` ile açık yol ver.
- LLM çağrısı sayısını düşük tut (toplam ≤ 10 koşu).
- İş bitince `/root/pi-spike/` kalabilir (Faz 2c başvurabilir). İçinde gizli bilgi bırakma.

## Denenecekler

1. **Extension tool'u:** `/root/pi-spike/ext/spike.ts` içinde iki tool:
   - `get_number()`: sabit `42` döner.
   - `http_echo(url)`: `fetch("http://127.0.0.1:8090/api/health")` çağırıp gövdenin ilk 200 karakterini döner.
     Bu, extension'dan yerel diziflix'e HTTP çağrısının çalıştığını gösterir. Header ekleme de dene
     (`X-Onboard-Token: test`).

   TypeScript'in derleme gerektirip gerektirmediğini, import yollarını (`ExtensionAPI` tipi, TypeBox) belgele.
2. **Skill:** `/root/pi-spike/skills/spike-skill/SKILL.md` (frontmatter `name`, `description`) ve bir
   `references/notes.md`. Skill'in yüklendiğini ve modelin reference dosyasını okuyabildiğini doğrula. Yerleşik
   `read` tool'u kapalıyken skill dosyaları nasıl okunuyor? Skill içeriğini `--append-system-prompt` ile vermek
   gerekiyor mu? Bunu net olarak belirle.
3. **Kısıtlı tool seti:** `--no-builtin-tools --tools get_number,http_echo` (gerekirse `read` de; skill için
   gerekiyorsa yalnızca skill dizinine kısıtlanabiliyor mu?). Ayrıca `--no-extensions --no-skills
   --no-context-files --no-prompt-templates` ve açık `-e`/`--skill`. Ajanın bash/write çalıştıramadığını doğrula
   (izin verilmeyen tool istenirse ne oluyor?).
4. **JSON olay akışı:** `pi -p --mode json ... "get_number tool'unu çağır, sonra sonucu söyle"`. stdout'tan
   `tool_execution_start/end`, `message_update`, `agent_end` olaylarının gerçek örneklerini kaydet. Son yanıt metni
   hangi olaydan alınır?
5. **Oturum sürdürme:** `--session-dir /root/pi-spike/sessions --session-id od_test123` ile ilk mesaj, sonra aynı
   session-id ile ikinci mesaj ("az önceki sayıyı 2 ile çarp"). Bağlam korunuyor mu? Oturum dosyası nerede ve hangi
   biçimde?
6. **İptal ve süre:** çalışan bir koşuya SIGTERM gönderildiğinde süreç temiz çıkıyor mu, oturum dosyası bozuluyor mu?
   Tipik bir 2-3 tool'lu koşunun süresini ölç.
7. **Uzun tool çıktısı:** 15000 karakterlik bir tool sonucu sorunsuz gidiyor mu? Kırpma ya da hata var mı?
8. **(İsteğe bağlı) RPC modu:** `--mode rpc` ile kullanıcıdan araya mesaj alma (`docs/rpc.md`). Basit bir alternatif
   olarak not et; uygulama `-p` + oturum sürdürme ile yapılacak.

## Çıktı
`/Users/drascom/Documents/work/micro_services/diziflix/docs/plans/site-onboarding/pi-spike-notes.md` dosyasını yaz
(yerelde). İçerik:
- Kesin, çalışan komut satırı (tüm bayraklarıyla), ilk mesaj ve devam mesajı için.
- Minimal extension iskeleti (çalışan `spike.ts`'in sadeleşmiş hali) ve skill iskeleti.
- JSON olay örnekleri (kısaltılmış) ve ayrıştırma notları: hangi olay tool çağrısı, hangisi son metin, hata nasıl
  görünür.
- Skill + kısıtlı tool sorusunun cevabı (madde 2-3).
- Süre ölçümleri, iptal davranışı, sınırlar/sorunlar.
- Faz 2c için öneriler (en fazla 5 madde).

## Kabul kriterleri
- `pi-spike-notes.md` var. 1-7. maddelerin her birinin sonucu açık: çalıştı / çalışmadı + neden.
- Sunucuda `/root/pi-spike/` dışında hiçbir değişiklik yok.
