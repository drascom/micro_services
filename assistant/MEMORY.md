# Memory

Short durable memories in `data/memory.sqlite` (gitignored, 0600). No raw conversation text is stored.

| kind | what | sent to |
|---|---|---|
| assistant | how to work with the user ("kısa cevap ver", "silmeden önce sor") | remote and local |
| personal | facts about the user (people, devices, routines) | local model only |
| episodes | one short summary per extracted conversation batch | local model only |
| topics | ongoing project/subject: name, state, decisions, open items | local; active one also remote (`memory_topic_remote`) |

## Use
- Save: "Bunu hatırla: cevaplar kısa olsun", "Şunu unutma: kızımın adı Elif", "Aklında tut: yazıcım Brother HL".
- Delete: "Elif ile ilgili notu unut", "Hafızandan yazıcı bilgisini sil".
- These words (not "hatırlat" = Reminders) add the `memory_save` / `memory_forget` tools to that request only.
- Admin → Hafıza: on/off, automatic learning on/off, "Konu uzak modele gitsin", list/edit/pin/delete, "Tümünü sil" per kind, Konular.

## Read path (`Orchestrator.context`)
- Always: assistant rules ≤ 500 chars (explicit/pinned first). Local model: + pinned personal facts ≤ 200 chars and
  recall (top 4 personal facts and summaries by EmbeddingGemma cosine ≥ 0.55) ≤ 600 chars.
- Memory has its own budget (`memory_chars`, 1900) on top of `history_chars`; the history window is unchanged.
- Recall never waits: embedding server (port 8093, shared with Files) down → skipped, it warms up in the background.

## Write path
- Explicit: confidence 1, `source=explicit`. The same `key` (or near-duplicate text) replaces the old value; an
  extracted item never overwrites an explicit one. Extracted assistant rules need a second hit to be used.
- Background extraction: after `memory_extract_every` (6) user turns or a reset (3 s), else after `memory_idle_ms`
  (2 min) idle; runs in the request lane and a new request cancels it (turns stay buffered, max 20).
  Remote/online mode → the remote model (local Gemma is never loaded for it); Local/offline mode → local Gemma.
  Offline-mode turns and local-model replies are never sent to the remote extractor.
- Never extracted: vault, mail, mail triage and file turns (intent or tools used), the hatırla/unut turn itself, Reminders tasks.
- Privacy filter (`isPrivate`) before storing and before any extractor: passwords, codes, TOTP, tokens/keys, IBAN,
  card numbers (Luhn), mail headers/quotes, vault.
- Caps: 40 assistant, 300 personal, 2000 summaries; unused extracted items expire (90 / 180 days).

## Topics
- Create + activate: "Yeni konu: tatil planı", "Ring projesi üzerinde çalışıyoruz". Activate: "Ring'e devam edelim",
  "Ring projesi" (name without "projesi/konusu", one name inside the other, else cosine ≥ 0.6; a closed topic reopens).
- Answered without a model (nothing leaves the Mac, not extracted): "Hangi konular var", "Konuyu kapat" (active →
  done), "Tatil konusunu sil".
- One active topic, held in the backend process; dropped after 30 min idle or a reset. With none active, a prompt
  whose embedding matches an open topic (touched < 7 days) with cosine ≥ 0.7 activates it (skipped if the embedding server is down).
- Active topic summary ≤ 600 chars inside `memory_chars` (oldest decisions dropped first) goes to the local model and,
  with `memory_topic_remote` (default true), to the remote model too (lines failing the privacy filter dropped). Only the
  active topic — never the list or other topics; personal facts/summaries stay local. The remote extractor gets no topic text.
- Extraction: turns carry their active topic; the extractor returns `topic {title, state, decisions, open, done}`;
  merged into that topic (state replaced, new entries appended, done items removed; caps state 300, entries 120,
  8 decisions, 8 open items; privacy filter). Local extractor also gets the current topic text. A batch with mixed
  topics updates none. No active topic: a proposed title joins an open topic with the same name (or cosine ≥ 0.85),
  else it becomes a candidate and a topic only when a later batch proposes it again (never auto-activated).
- Reminders tasks: no topic commands, detection, block or extraction.
- Caps: 50 topics; closed topics expire after 180 days; candidates after 30 days.
- No `memory_search` tool: per-request recall covers it (design: only if recall misses).
- Admin → Hafıza → Konular: edit name/state/decisions/open items (one per line), star = active, delete, "Tümünü sil".
