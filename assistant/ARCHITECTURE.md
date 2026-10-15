# Architecture and inspection

## Final application

```text
Native SwiftUI desktop ring / CLI
                 ↓
          TypeScript Orchestrator
           ├─ Pi AgentSession → global Pi/OpenAI OAuth subscription + approved tools
           ├─ Pi Models → TypeSafe Jev (typed intent classification)
           ├─ Pi AgentSession → bundled llama.cpp/Gemma + offline permission policy
           ├─ Speech → bundled whisper-server (lazy, idle-stopped, greedy decode; whisper-cli fallback) + warm local EMA/Kokoro + Piper fallback + macOS audio playback; Turkish TTS text respelled offline (`pronounce.ts`: user dictionary, CMUdict, `lexicon/`)
           ├─ Files → EmbeddingGemma 2 llama-server (:8093, lazy, idle-stopped) + SQLite index of ~/Documents (`files-index.ts`, background, paused during requests)
           ├─ Vault → `bw` CLI (runtime/bitwarden, data/bitwarden) with an in-memory session key (`vault.ts`); local model only
           └─ Pi extensions/skills → web search, skill management, read-only Gmail mail, local files, Bitwarden vault; future calendar
```

Pi's coding-agent harness owns the model/tool loop, resource loading, skills and
permission hooks. App-owned extensions register online search, public-page reading
and skill validation. Pi read/write/edit tools are restricted by the harness
permission extension: skills/extensions can be read, only skills can be edited.
Shell execution and sending are unavailable. The injected orchestrator retains
voice, bounded visible history, cancellation, provider fallback and resource
ownership. Jev remains a classifier operation; it does not dispatch tools or
grant permissions. See PI-HARNESS.md and AGENTS.md for the ability rules.

The global Pi login inspected on 1 October 2026 contains an `openai` OAuth
credential and selects `gpt-5.5`. The app reuses that source of truth and Pi's
subscription-compatible request handling. Updates use Pi's 30-second stale
lock and `proper-lockfile` convention. Credentials remain global by design;
app files, runtimes, and models live in this folder. The Codex desktop token
file is not used or modified.

Normal remote flow: validate input → optionally classify intent with Jev →
ask the Pi subscription model → commit successful visible reply. Jev failure
is non-fatal. Chat failure switches to local Gemma using the same recent
history. Offline mode makes neither remote request. Cancellation aborts the
active request and does not turn into fallback. Turning off Local only cancels
active work and unloads the owned local process after cancellation finishes.
Remote-mode fallback releases its owned model when the request ends. Model-generated decisions do
not count as user authorization for external actions.

The app has one Swift/AppKit desktop host and one Node orchestration process
plus local inference helpers. The desktop host owns a transparent floating
NSPanel, SwiftUI animation/text UI, CoreGraphics modifier event monitor, and
AVAudioRecorder. It talks to a protected native-only loopback API.
Two shortcuts: the dictation shortcut notes another app's focused editable text element
(Accessibility, `AXFocusedUIElement`) and after `POST /transcribe` pastes there if it still has
focus (`Paster.swift`), else copies to the clipboard; never chat/TTS/history, only `POST /dictation`
logs app id + length. The assistant shortcut always goes to `/chat` and never pastes. Both stop
speech at the press (`/stop-speech`); dictation also mutes the running request's reply.
Barge-in: `/chat` while a request generates calls `Steering.steer` → `promptSteered` (Pi
`session.steer`, delivered before the next model call; late text is prompted again in the same
session) and returns `{steered:true}`; one reply on the first response, history keeps both texts.
Not steerable (no live Pi session yet) → the running request is aborted (409 `REPLACED`) and a new
one starts at once with both texts. A user `/chat` cancels a running Reminders task.
It starts the backend automatically and stops only its owned process on quit.
The ring's right-click menu is minimal (type, cancel, new conversation, Admin,
restart, quit). A native SwiftUI Admin window holds settings, live status (`POST /status`:
active request stage, mode, remote model, Pi login metadata, owned processes with
PID/port/memory, recent activity) and user editing of app-owned skills and
extensions (`POST /admin/*`, confined to `.pi/skills` and `.pi/extensions`). Both
use the same session-token loopback API; nothing is added to model context.

The backend runs as one Node process plus native helpers. Combining model engines
into one binary would require unnecessary bindings and lose compatibility
with existing assets. The native client uses one protected loopback server; no HTML page is served. No
separate manually managed services, proxy, database, or broker are needed.

## Original projects inspected

| Area | Dictate | Gemma |
| --- | --- | --- |
| Production entry | `app/Dictate.swift`: Swift/AppKit menu bar | `app/GemmaServer.swift`: Swift/AppKit menu bar |
| Other entries | `DictateCLI.swift`, `test/build-cli.sh`, `install.sh` | `run-gemma.sh`, `transcribe.sh`, `context_proxy.py`, legacy `swift_app/main.swift` |
| STT | AVAudioEngine → 16K mono WAV → Whisper → TextGuard | Experimental audio via Gemma; inconsistent transcription acknowledged by source |
| TTS | None | None |
| LLM | None | Local Gemma, OpenAI-compatible llama.cpp endpoint |
| Dependencies | Foundation, AppKit, AVFoundation, CoreGraphics, ApplicationServices, IOKit, whisper.cpp/model | Foundation/AppKit, llama.cpp/Metal/dylibs, GGUF/projector, Python stdlib summary proxy, cmake/CLT build tooling |
| Useful components | Recorder, modifier hotkey, paste restoration, config, Whisper flags, silence guards | Compatible bundled runtime, ownership semantics, API shape, model cache, context/metrics/MTP controls |

Dictate shared Foundation-only config/transcription code between GUI and test
CLI. Its native recorder/hotkey/paste components were coherent but tightly
tied to the menu-bar delegate and callback/queue concurrency. The new app
reuses the audio contract and guard behavior in TypeScript rather than importing
old Swift entry points. A short substring guard that rejected a valid mixed
subtitle/speech sentence was removed; regression tests preserve real speech
while rejecting all-boilerplate output and repeated words.

Gemma's compatible llama-server and dylibs were found inside GemmaServer.app;
its CLI build path was absent. Its configured app port was 8090, despite the
README's 8080 default. The app now uses the pinned upstream prebuilt
(`scripts/install-llama.sh`) on 8091. The
separate legacy `swift_app/` path was an audio/paste prototype, not the reliable
Whisper dictation app. Original model asset names and files were inspected
locally; no new model compatibility claims or downloads were needed.

## Consolidation

The first Python draft was superseded by new TypeScript source after the user
requested Pi and TypeSafe integration. Old code and applications were moved
out of the active app folder into a recoverable dated archive. Both runtimes
and both required models now have local app paths. Whisper now uses a static v1.9.4 native build with embedded Metal and Accelerate;
no Homebrew runtime/backend paths are required. Speech uses full Large v3 Q5
with automatic Turkish/English detection. The original Turbo asset was removed
after checksum, bilingual transcription, and latency verification.

The original optional features (automatic paste, context slider, multimodal
Gemma, MTP, metrics UI, and summary proxy) are not silently copied into the new
app; their source is archived for future migration. The current replacement
implements a native hold-to-talk shortcut, animated desktop ring, text input,
local dictation, chat, TTS, subscription primary, Jev,
offline fallback, bounded history, cancellation, and adapter seams.

## Open seams

- `Chat` and `Classifier` expose provider operations with explicit cancellation.
- `Runtime` exposes start/readiness/owned-stop, allowing safe existing-server attachment.
- `SpeechService` separates local capture/transcription/output from the chat flow.
- Read-only email: `.pi/extensions/mail` (tools `mail_list_messages`, `mail_read_message`) → `MailClient` (opaque per-request references, serial calls, limits, error codes) over the connected Gmail accounts (`Mailboxes` from `.pi/extensions/gmail/service.ts`): inbox search `in:inbox after:<epoch>` + filters, metadata per message, plain-text body from `format=full` (`gmail/text.ts`: text/plain first, else HTML → text; attachments never read). One failing account is listed under `errors`. Online only (no mail tools offline). Budgets in `permissions.ts`: 3 listings, 5 body reads per request.
- Apple Reminders: `.pi/extensions/apple-reminders` (one `reminders` tool) → `RemindersClient` (opaque per-request refs, serial calls, generic error codes) → injectable transport, default fixed JXA `reminders-bridge.js` via `/usr/bin/osascript` (Reminders launched hidden). Registered only when `reminders_enabled` (Admin switch). Delete needs a backend `ConfirmationStore` token redeemable only in the next user turn (`newTurn()` per `Orchestrator.ask`, shared by remote attempt and local fallback) within 2 minutes; the next turn's system prompt names the pending deletion.
- `ContextSource` remains an unused internal reference-data seam. Future user-facing calendar abilities must be Pi extension tools and skills, including cached offline reads, governed by the same permission hook.
- Assistant tasks: `src/tasks.ts` `TaskScheduler` (started by `makeServer` when `TaskOptions` are given) lists open, due-or-undated reminders in `tasks_list` via the same bridge, takes the server's request lane (waits for `/chat`; `/chat` cancels a running task, which stays ⏳ and reruns on a later scan), runs `Orchestrator.ask(..., { automated: true })` (no history, no user turn, tools filtered to `TASK_TOOLS`, permissions extension blocks other tools and `reminders delete`), appends the result to the notes and completes it. State key = hash(id + due), so a repeating reminder's next occurrence runs again.
- Gmail triage: `.pi/extensions/gmail` — missing `Sonra Oku`/`Gereksiz` labels are created per account on connect and on Admin status (`labels.create`, gmail.modify scope; only creation); `mail_organize` accepts an act in `action` and an apply without token uses the turn's pending plan (local-model tolerance); `MailProvider` interface (`GmailProvider`: REST via injectable `fetch`), `GmailAuth` (OAuth Desktop, PKCE + loopback, access token in memory), `SecretStore` (`KeychainSecretStore` via `security` stdin; swappable for an encrypted file on Linux), `classify.ts` (DMARC/DKIM from Gmail's own header, category rules, batched model call built in `harness.ts` from the request's model, fixed level→action table), `MailTriage` (propose → next-turn token → apply/purge, undo from `data/mail-actions.jsonl`, processed ids in `data/mail-triage-state.json`, daily cap). Registered only when `gmail_triage_enabled` and connected, online only; Jev `mail_triage` routes to the `mail-triage` skill. Admin routes `/admin/gmail/*`.
- External email/calendar writes require their own confirmed action model and reconnect logic (Reminders' next-turn token is the first such model).

Tests use injected providers and temporary credential files. They do not
contact paid services or read the user's real credentials. Separate smoke
checks used non-sensitive synthetic text through the real configured providers.
