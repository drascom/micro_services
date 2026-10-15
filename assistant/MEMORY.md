# Memory

Short durable memories in `data/memory.sqlite` (gitignored, 0600). No raw conversation text is stored.

| kind | what | sent to |
|---|---|---|
| assistant | how to work with the user ("kısa cevap ver", "silmeden önce sor") | remote and local |
| personal | facts about the user (people, devices, routines) | local model only |
| episodes | one short summary per extracted conversation batch | local model only |

## Use
- Save: "Bunu hatırla: cevaplar kısa olsun", "Şunu unutma: kızımın adı Elif", "Aklında tut: yazıcım Brother HL".
- Delete: "Elif ile ilgili notu unut", "Hafızandan yazıcı bilgisini sil".
- These words (not "hatırlat" = Reminders) add the `memory_save` / `memory_forget` tools to that request only.
- Admin → Hafıza: on/off, automatic learning on/off, list/edit/pin/delete, "Tümünü sil" per kind.

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

## Later (P3)
`topics` table and `topic_fact` kind exist; nothing writes them yet (topic memory, "X'e devam").
