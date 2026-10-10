# Personal Assistant

One TypeScript app: Pi/OpenAI subscription chat, TypeSafe Jev intent decisions,
local Whisper transcription, EMA Lightning Turkish speech, Kokoro English speech, Piper fallback, and automatic local Gemma
fallback. All app source, UI, model files, native binaries, and launch scripts
are in this folder. You do not need the old Dictate or Gemma projects running.

## Start

```bash
cd /Users/drascom/Documents/work/micro_services/assistant
open PersonalAssistant.app
```

Open **PersonalAssistant.app** in this folder for the floating desktop assistant.
The ring centre shows system memory used / total (for example 12.4 / 24 GB),
refreshed every two seconds. It counts application, wired, and compressed
memory while excluding purgeable cache; this is total Mac usage, not just
Ayhan. Its animated ring has a transparent background, stays above ordinary windows,
and appears across Spaces. Click the ring to toggle the compact input panel
immediately above it. Drag the ring to reposition it; an open panel follows.
Drag the panel by its native title bar or empty background. Close it with the
red macOS close button or by clicking the ring again. The panel displays only
the assistant reply, not your spoken transcript. The send arrow sits inside
the input field. Right-click the ring for a minimal menu: **Mesaj yaz / Type a
request…**, **İsteği iptal et / Cancel request**, **Yeni konuşma / New
conversation**, **Yönetim / Admin…**, **Yeniden başlat / Restart Ayhan** and
**Çıkış / Quit Ayhan** (the menu-bar
icon uses the same menu). Quitting stops the backend and local model processes
owned by this app.

**Yönetim / Admin…** opens one native window (reopening brings it to front):
- **Durum / Status** refreshes every 3 s: backend state, active request stage,
  mode, remote model, Pi login (provider/type only), owned processes
  (llama-server, whisper-server, EMA/Kokoro workers) with PID, port and memory.
- **Etkinlik / Activity**: latest activity log entries, newest first.
- **Ayarlar / Settings**: shortcut, instructions, Local only, Read aloud,
  Menu bar icon, Restart (moved from the context menu).
- **Skill & Extension**: list, view, edit and create app-owned `.pi/skills` and
  `.pi/extensions` files. These are user actions through the protected loopback
  API (`/admin/*`); skill files are validated with the harness parser before
  saving, paths are confined to those folders (no traversal or symlinks), and
  changes apply on the next request. The model's permissions are unchanged.

Hold **Control + Option** together for at least 350 ms to speak; release them
to transcribe locally and submit the message. The ring changes colour while
listening, thinking, and speaking. Replies are spoken by default; turn off
**Read aloud** in Admin → Settings if preferred. **Local only** there
bypasses both remote providers. Unchecking **Local only** cancels any
active request, waits for it to finish, and unloads the assistant-owned Gemma
process. Checking it again loads Gemma lazily on the next request.
**Cancel request** is in the ring menu.

The menu-bar icon is hidden by default. Toggle **Menu bar icon** in Admin →
Settings to show or hide it; this preference survives a restart. **Change shortcut…**
opens a key recorder: press and release your chosen chord, then click **OK**.
Use at least one modifier (Control, Option, Command, Shift, or Fn); a letter
or another key can be included. The saved shortcut replaces Control + Option
and appears below the ring. Shortcuts are observed rather than consumed, so
avoid combinations already assigned to another app. Recording shortcuts
suspends hold-to-talk. Physical key-state polling also detects releases lost
while a microphone permission dialog is open. Grant permission, then press
and hold the shortcut again to start a fresh recording. Typed Send cancels
any pending or active recording before submitting the text.

Dictation: if a text field in another app has keyboard focus when you start
holding the shortcut, the transcript is pasted there (clipboard + ⌘V, previous
clipboard restored) and the assistant is not asked (no answer, no speech, no
history). If it cannot be pasted (focus moved, Ayhan's own windows,
no Accessibility permission — macOS asks once per launch), the transcript goes
to the assistant as usual. The activity log records only the app id and length.

On first use, allow microphone access. If the shortcut is unavailable, select
**Change shortcut…** in Admin → Settings, save the shortcut, and allow Input
Monitoring in macOS System Settings → Privacy & Security. Restart the app
if macOS requires it. Typing remains available without these permissions.

The desktop app starts its backend automatically. `./run.sh serve` is an
optional headless backend command and does not open a browser or serve a web
page. Ctrl-C stops a terminal-started backend. Use one app/backend at a time;
the native app can attach to an existing backend and stops only one it owns.

The native launcher references this folder and the Node interpreter used when
it was built. Keep this folder in place; this is a personal local app, not a
portable distribution bundle. `data/native-launcher.log` records launcher errors.
To rebuild, run `npm run rebuild-app` (moves the old app to `/tmp`; requires
Apple Command Line Tools). The resulting app uses a stable local code-signing certificate (see below).

## Your existing Pi subscription login

This app shares the global Pi OAuth connection in `~/.pi/agent/auth.json` and
the model selected in `~/.pi/agent/settings.json`. Your current connection is
`openai` OAuth with **gpt-5.5**. There is no extra OpenAI API key setup or
separate assistant login. `PI_CODING_AGENT_DIR` or `pi_agent_dir` in config can
select a different Pi configuration directory.

The app requires OAuth for remote chat: an ambient `OPENAI_API_KEY` is not used
as a substitute. Pi refreshes the shared OAuth token when needed. Credential
updates use the same `proper-lockfile` protocol as global Pi, preserving the
other providers' entries. No credentials are copied into this app or exposed
to the desktop UI. If login expires or is revoked, use `/login` in global `pi`.

The `openai` route uses Pi's supported Sign in with ChatGPT integration. A
global `openai-codex` OAuth login is also supported for Pi's legacy Codex route.
Access still depends on your plan, model eligibility, and remaining usage.

## Jev from env.txt

Your `env.txt` contains `TYPESAFE_AI_API_TOKEN`. The server reads it and maps it
to Pi's `TYPESAFE_API_KEY` internally. The file is owner-readable/writable and
ignored by source control. Do not paste the key into the UI. `.env` is also
supported; shell environment values take precedence. Files are parsed as data,
never executed as shell scripts.

Jev makes **typed decisions**, not conversational responses. In remote mode it
classifies each message as conversation, email, calendar, brief news, web search, or skill management and returns its
confidence. This is returned as API metadata. Web/skill-management decisions select a skill through Pi’s own skill-command expansion, saving a model round. An email decision selects the
`mail-assistant` skill (read-only Apple Mail, see below). Other decisions do not authorize actions. A Jev outage does not block chat.
Set `jev_enabled: false` to disable it. No calendar adapter is connected, and the
assistant does not claim to send mail or change events.

## Voice and offline mode

Hold Control + Option to record and release to submit. Click the ring for
text input. Read aloud and Local only are in Admin → Settings; Cancel request is in the ring menu.

Microphone audio stays on your Mac and temporary WAVs are deleted. The default
Whisper language is automatic (`auto`), supporting Turkish and English between
requests. Very short recordings and switching language within one recording
can be less reliable. Replies follow the latest message's language.
An on-device macOS language detector selects the reply engine. Turkish uses
**EMA Lightning** by default; its persistent local worker loads the model on the
first Turkish reply and keeps it warm for later replies. English uses local
**Kokoro-82M** with the warm female `af_heart` voice and its own persistent
worker. If either primary engine cannot load or synthesize, the same reply
falls back to the matching local Piper voice: Turkish DFKI or English Alan.
No text or audio is sent to a TTS service.

Set `ema_speed` in `config.json` from 0.25 to 4 and `kokoro_speed` from 0.5 to 2
(1 is normal speed). `kokoro_voice` selects a bundled Kokoro voice; the default
is `af_heart`. EMA produces 48 kHz audio and Kokoro produces 24 kHz audio.
Piper speed defaults to 1.25× for both languages; adjust
`piper_speed_tr` and `piper_speed_en`. Temporary WAV files are played with macOS
`afplay` and deleted. Cancellation stops synthesis or playback. Both warm TTS
workers are owned by the assistant and stop when the assistant exits.

`tts_engine: "ema"` is the default. `tts_engine: "piper"` uses Piper for both
languages; `tts_engine: "say"` restores the macOS engine and is the only mode
that uses `voice`, `voice_tr`, and `voice_en`.

EMA Lightning 1.0.4 is installed in `runtime/ema/` with Python 3.12. Recreate it
with `uv venv --python 3.12 runtime/ema`, then
`uv pip sync --python runtime/ema/bin/python scripts/ema-requirements.txt`.
The worker downloads the approximately 34 MB model weights from Hugging Face on
first use and keeps its private cache under `models/ema/` inside this service.
It does not run code from another project. EMA Lightning is Apache-2.0.

Kokoro uses `kokoro-onnx` 0.6.1 in `runtime/kokoro/` with Python 3.12. Recreate it
with `uv venv --python 3.12 runtime/kokoro`, then
`uv pip sync --python runtime/kokoro/bin/python scripts/kokoro-requirements.txt`.
The FP16 model and voice bank live under `models/kokoro/`; together they occupy
about 192 MB. They are app-local and must be downloaded before first use as
documented in `HANDOFF-EMA-TTS.md`. Kokoro-82M weights are Apache-2.0 and the
`kokoro-onnx` runtime is MIT.

Piper 1.8.0 is installed in `runtime/piper/`, using the installed Python 3.13
interpreter. Recreate it with `uv venv --python 3.13 runtime/piper`, then
`uv pip sync --python runtime/piper/bin/python scripts/piper-requirements.txt`.
Runtime packages are pinned in that requirements file. Voice source revision
and hashes are in `models/piper/SOURCES.json`; per-voice model cards are alongside
the voices. Piper is GPL-3.0; the Turkish DFKI voice dataset uses CC BY-NC-SA 4.0.
Read the included voice model cards before redistribution or commercial use.

The Kokoro smoke check loaded its model in 0.46 seconds. Two assistant-style
sentences produced 3.69 seconds of audio in 1.00 seconds and 4.06 seconds in
0.61 seconds; the second request used the same warm worker. Earlier Piper
preview checks generated 5.27 seconds of Turkish audio in 0.89 seconds and
5.14 seconds of English audio in 0.55 seconds, including process startup.
Voice quality should be judged by listening, not those timing measurements.

Remote mode sends **your message to TypeSafe Jev**, and **your message plus recent
conversation to your Pi/OpenAI subscription**. The **Offline / local only**
switch bypasses both providers and starts the bundled local Gemma if needed.
Remote chat errors/timeouts also trigger Gemma fallback with a visible reason.
In remote mode, an assistant-owned fallback model unloads after that request.
**Cancel request** cancels transcription or chat without saving an incomplete
turn. The model may already have processed some of a cancelled remote request.

History is memory-only. New conversation clears the server's history; quitting
clears it too. Older complete turn pairs are dropped at a conservative character
limit. The system prompt is retained; no summary proxy or database is needed.

## Settings and commands

Copy `config.example.json` to `config.json` and edit it. Settings are read on
startup; unknown fields and invalid types fail clearly. Paths are relative to
this app folder. Leave `codex_model` empty to inherit global Pi's selected model.

```bash
./run.sh doctor
./run.sh models
./run.sh ask 'Merhaba'
./run.sh ask 'Merhaba' --offline --speak
./run.sh classify 'Please help me draft an email'
./run.sh transcribe /absolute/path/audio.wav
./run.sh serve
npm run check
npm test
npm run test:native
```

CLI `ask` starts a fresh conversation. `transcribe` accepts 16 kHz mono PCM16
WAV. Model listing is Pi's catalog, not a guarantee your subscription can use
every listed model.

## Dependencies and assets

- Node.js 24+, already installed; application code runs TypeScript natively.
- `@earendil-works/pi-ai` and `pi-coding-agent` pinned to 1.1.0; `package-lock.json` locks dependencies.
  Reinstall with `npm ci --ignore-scripts` if needed.
- Apple Silicon macOS, installed Python 3.12 for EMA/Kokoro, Python 3.13 for Piper, and built-in `afplay`.
- An installed `say` voice only if opting into the previous speech engine.
- macOS 14+ for the native SwiftUI desktop ring; Apple Command Line Tools to rebuild it.
- Existing global Pi OAuth login for subscription chat.

Both inference runtimes are bundled locally. `runtime/llama/` includes
llama.cpp and dylibs. `runtime/whisper/whisper-cli` and `whisper-server` are whisper.cpp v1.9.4,
built with static Whisper/ggml libraries, embedded Metal GPU kernels, CPU,
and Accelerate BLAS. It depends only on macOS system frameworks; no Homebrew
backend paths or OpenMP dylibs are needed. Source URL, checksum, and build
flags are recorded in `runtime/whisper/BUILD.json`. Upstream licenses are included.
This is a native build for this M4 Pro Mac, not a general distribution binary.

Speech uses **full Whisper Large v3 Q5_0** (`ggml-large-v3-q5_0.bin`,
1,081,140,203 bytes). The previous Turbo model was removed from this app after
validation. Turkish and English automatic detection remains enabled. The model
is downloaded from the upstream whisper.cpp model repository; its SHA-256 is
`d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1`.
Full Large v3 retains the decoder layers pruned in Turbo and trades some speed
for model capacity. This is not a guarantee of better accuracy on every recording.

On this M4 Pro/24 GB Mac, synthetic Turkish speech of 7.75 seconds took 2.51
seconds to process; synthetic English speech of 7.45 seconds took 2.45 seconds,
including model startup. These are compatibility and latency smoke checks, not
an evaluation of the user's real voice. Measurements are in `data/stt-benchmark.json`.
Whisper runs as a persistent localhost `whisper-server` (`whisper_url`, default
`http://127.0.0.1:8092`): started lazily on the first recording, the model stays loaded (~1.5 GB RSS greedy, ~2.0 GB beam),
and the process gets SIGTERM after `whisper_idle_ms` without use (default 10 min) or when the
assistant exits. Server and CLI fallback use the same flags (`-l auto -t 8 -nt -bs N -bo N`, N = `whisper_beam_size`).
Default `whisper_beam_size: 1` is greedy decoding: vs beam 5 it is ~150–250 ms/file faster (p95 3.5 s -> 2.3 s
on long clips), uses ~0.5 GB less RAM (~1.5 GB) and WER is about the same (human 6.4 -> 6.8, user TR 12.5 -> 11.9).
Set it to 5 for the previous beam search. The server alone saves ~380 ms/file vs per-request CLI
(see `experiments/turkish-stt/RESULTS.md`).
If the server fails to start or errors, that request falls back once to the per-recording
`whisper-cli`; `whisper_server: false` uses only the CLI. Server output goes to `data/whisper.log`
(no transcript text). With Gemma loaded too, total RSS is ~8.6 GB (Gemma ~7.1 GB mmap, Whisper ~1.5 GB greedy).

`models/` also contains Gemma, hard-linked from the existing model cache to
avoid duplicate disk space. Treat model files as immutable. No old-project
startup is required. Changing models/config requires restarting the assistant.

Local Gemma starts lazily on loopback port **8091**, at 24K context (q8_0 KV cache, flash attention, 64-token reasoning budget via `local_reasoning_budget`; 64 keeps tool calls intact and roughly halves reply time vs 384, 0 is faster but drops date filters; a short budget message stops cut-off thoughts leaking into replies), one slot,
Metal acceleration, and text-only input. `data/llama.log` records runtime
output. To attach to an existing server, set `local_url` and
`auto_start_local: false`; the assistant never stops a server it did not start.
Authenticated local endpoints can use `ASSISTANT_LOCAL_API_KEY` in env.txt.

The desktop backend binds only to **127.0.0.1**. API requests require a random session
token and matching host/origin. No CORS allowances are used. These controls do not isolate the app from other programs running under
your macOS account.

## Files and future integrations

`src/orchestrator.ts` owns the shared conversation flow. `providers.ts` registers
Pi chat, local chat, and Jev. `speech.ts` handles local audio; `processes.ts`
owns native subprocesses; `credentials.ts` coordinates Pi token storage;
`server.ts` provides the native API and `main.ts` wires the app and CLI.
`native/` contains the SwiftUI ring, text panel, global modifier monitor, local
recorder, and backend client. There is no browser interface or web asset directory.

`ContextSource` is the read-only email/calendar adapter seam. Cached local data
could support offline searching and drafting. External writes need a separate
explicit action queue and reconnect/recheck flow. Any reference data supplied
to cloud chat would also reach OpenAI, so future adapter configuration must
make that choice clear. No local mailbox/calendar caches were inspected here.

## Verification and remaining work

22 automated tests cover provider routing, offline isolation, cancellation,
history, transcription guards, WAV validation, shared credential locking,
API-key refusal, local process ownership, HTTP protections, and subprocess
timeouts. Native regression tests cover shortcut persistence, missed key
releases, microphone permission cancellation, and typed requests superseding
recording, and recovery after silent/empty audio. If no speech is detected,
the ring flashes orange for 450 ms and returns to idle without opening a popup
or changing the previous reply. Type checking passes. Live checks passed for Pi subscription chat,
Jev classification, local Gemma, and bundled Whisper transcription.

The Swift 6 native app compiles and starts its backend successfully. Native visual
interaction could not be inspected through the available computer-control tool,
which does not expose this new app. Basic voice and chat functionality were confirmed by the user. The updated
compact panel, native close button, and drag behaviour compile successfully
and still need an interactive user check.

Automatic paste/clipboard restoration, image/audio Gemma chat, MTP, and metrics
controls are not included. Their old source is recoverable from the archive.
The native launcher uses a local Apple Development code-signing certificate; it is
not notarized for distribution. `build-app` and `npm run sign-app` share the same
signing routine. The first successful signing saves the certificate fingerprint
in `data/signing-identity.txt`; later builds require that same certificate.
If multiple certificates are available, set `ASSISTANT_SIGNING_IDENTITY` to the
chosen certificate fingerprint. Missing or expired certificates stop the build
instead of silently switching to a temporary signature.

macOS Documents/microphone permissions may need granting once after switching
from the old ad-hoc signature. Subsequent updates preserve the signing identity
and bundle identifier. Keep using `npm run sign-app` after replacing the native
executable. Do not use ad-hoc signing for updates.
Calendar remains an extension point; email is read-only through Apple Mail (see Harness abilities). Ring position, Local only, and Read aloud currently reset when the app restarts.
The shortcut and menu-bar icon visibility are saved in native preferences.

The old `dictate/` and `gemma/` projects are archived under
`../.assistant-archive/2026-10-01/`. They are not dependencies of this app.

Technical inspection and decisions are in [ARCHITECTURE.md](ARCHITECTURE.md).
Official references: [Pi AI](https://github.com/earendil-works/pi/tree/main/packages/ai),
[ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source),
[Jev](https://docs.typesafe.ai/introduction),
[Whisper models](https://huggingface.co/ggerganov/whisper.cpp),
[Turbo architecture](https://huggingface.co/openai/whisper-large-v3-turbo),
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), and
[Piper CLI](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md).

### Personal instructions

Open Admin → Settings → **Talimatları düzenle / Edit instructions…**.
`instructions.md` is untracked; it is created from `instructions.example.md` if missing (the backend also falls back to the example).
This opens `instructions.md` in TextEdit. Add rules, save, and they apply to
the next request through both remote and local LLMs, without restarting.
These rules supplement the built-in assistant instructions.

## Harness abilities

Pi is called **the harness** in Ayhan. Both remote and local requests now run
through its coding-agent SDK sessions. Tools and permissions use Pi extensions,
while workflows use its skill system. See `PI-HARNESS.md` for the inspected
architecture and `AGENTS.md` for future ability development rules.

The first ability is online search: ask **“Search online for…”** or
**“İnternette … araştır.”** The harness loads the online-search skill, searches
DuckDuckGo and can read public HTTPS HTML source pages. Answers cite retrieved
sources. No extra search API key is needed. Bot challenges, throttling, and
JavaScript-only pages may prevent retrieval; the assistant reports failures.
Local-only mode disables live web tools. Online fallback to Gemma can still use
approved web tools.

You can also ask the harness to create, improve or validate a skill. It can read
all files under `.pi/skills/` and `.pi/extensions/`, and write/edit only inside
`.pi/skills/`. `test_skill` checks structure with Pi's native skill parser;
behavioral testing is limited to existing approved tools. Saved changes are
discovered on the next request. It cannot edit extension code, change permissions,
run shell commands, or read arbitrary files.

Permissions live in `.pi/permissions.json` and are enforced before tool execution
by `.pi/extensions/permissions.ts`. Search and skill edits are already authorized;
new abilities default to blocked. Editable response rules cannot grant access.
No native approval-dialog bridge is enabled yet; future permissions will use
Pi's confirmation interface when such a bridge is added.

### Bağlam bütçesi / Context budget

Context sent to any model (local or remote) is kept minimal: short system prompt and
skills, only the routed skill's tools, compacted web tool schemas, bounded history.
New skills/tools must follow this; `tests/context-budget.test.ts` enforces a limit.
See `PI-HARNESS.md`.

### Apple Mail (read-only)

Ayhan can list, search, summarize and classify messages from the Apple Mail
accounts enabled in Admin (each with one mailbox, default `INBOX`), using mail already on this Mac (also offline). It never sends,
moves, deletes, flags or marks messages, and opens no links or attachments.
Tools: `mail_list_messages` (metadata of all active accounts merged newest first, max 25; each row
has an `account` label; optional `account` filter accepts only an active account) and `mail_read_message` (plain-text
body, max 8,000 chars, 40,000 per request); at most 2 listings and 5 body reads per
request. By default Mail's local store is read directly and read-only (`mail-db.ts`: `Envelope Index`
SQLite, account names from `Accounts4.sqlite`, bodies from `.emlx` files), so Mail.app may be closed;
this needs **Full Disk Access** for PersonalAssistant.app (or the terminal running it). Without access,
or if the store layout is unknown, it falls back to a fixed JXA script (`mail-reader.js` under
`/usr/bin/osascript`), which needs Mail open and Automation permission. Mail is never launched. No mail content is logged.

Setup:
1. Grant Full Disk Access (System Settings → Privacy & Security → Full Disk Access).
   `node scripts/test-mail-smoke.ts "<Mail account name>"` — read-only check: inbox
   count and up to 5 recent metadata rows in the terminal. On the JXA fallback, allow the
   Automation prompt (System Settings → Privacy & Security → Automation).
2. Admin → Ayarlar / Settings → Mail hesapları / Mail accounts: pick an account from
   Mail.app (or enter its name), optional mailbox; toggle active/inactive, remove.
   Saved to `config.json` as `mail_accounts: [{ account, mailbox, enabled }]` (max 10);
   applies from the next Mail tool call. Older `mail_enabled`/`mail_account`/`mail_mailbox`
   settings are read as a one-entry list and replaced on the first save.
3. `.pi/permissions.json` has `"mail_read": "allow"`.

To disable: set `mail_read` to `deny` or make every account inactive; remove the
Automation permission in System Settings if wanted.

### Apple Reminders

One `reminders` tool (`.pi/extensions/apple-reminders`): `list` (list name, open/done/all,
today/overdue/upcoming, limit), `add` (title, notes, local `due` `YYYY-MM-DD[THH:MM]`, priority,
list; default = Reminders' own default list), `update`/`complete`/`delete` by the opaque `ref`
from a listing. Fixed JXA bridge (`reminders-bridge.js`) under `/usr/bin/osascript`; Reminders is
launched hidden (`open -g -j`), never activated. Works offline.

- Admin → Ayarlar / Settings → **Reminders** switch (`reminders_enabled`, default off). Off: the
  tool and skill are not sent to the model at all.
- `.pi/permissions.json` `"reminders": "allow"` (missing = allowed, `deny` blocks); list/add/update/complete
  run without asking.
- Delete is two-step: `delete` + ref only returns a single-use `confirm_token`; Ayhan asks
  "… silinsin mi?". The token works only in the user's **next** message (not the same turn),
  within 2 minutes; the backend enforces this, the model only judges the yes/no.
- First call asks for macOS Automation permission (Ayhan → Reminders); denied → `automation_denied`.

## Activity logging

Admin → **Etkinlik / Activity** shows the latest entries; **Open log file…**
opens `data/activity.log` in TextEdit. The JSON-lines log records timestamps, request
IDs, elapsed milliseconds, classification, model rounds, tool timings and
permission decisions, local model loading/fallback, and success/failure. Voice
transcription, chat and speech have separate request entries. Chat elapsed time
is the reply wait; speech elapsed time includes playback.

It records metadata rather than questions, replies, tool arguments, credentials
or private reasoning. Logs rotate at 2 MiB, keeping one previous file. New
versions of the app must be restarted to load backend/menu changes. TextEdit
shows a file snapshot; reopen it to inspect newly written entries.

For brief current-news requests, the harness uses Bing News RSS with publication
dates and original article links, excluding stories older than 72 hours. General search uses DuckDuckGo; bot challenges
are reported and should not be repeatedly retried. A quick roundup uses one
focused news search. For detailed research, it can read original pages.

**Yeniden başlat / Restart Ayhan** in the ring menu or Admin → Settings cancels active work, waits
for the owned backend to exit and unload its model, then reopens the app after
the current instance exits. An externally started backend remains externally
owned; restart only restarts the desktop host in that case.

### Speech-only text filtering

Replies retain source URLs and formatting on screen. Before speech synthesis,
`spokenText` deterministically removes raw URLs, email addresses, standalone
domains, code blocks, images, absolute file paths and numeric citation markers.
Markdown links retain their human-readable labels. Useful prose, numbers and
model names are preserved. An URL-only reply produces no audio. All speech
engines use the same filtered text, and language detection also uses it.
The activity log records original/spoken character counts, not their contents.

Brief news has a separate Jev intent. A Pi workflow extension selects the news
category and limits a brief roundup to one search. Preloaded skills are marked
as already supplied to avoid redundant reads; detailed research retains the
normal search workflow.

## Full Pi web-access package

Ayhan loads the complete app-local pi-web-access extension. See [WEB-ACCESS.md](WEB-ACCESS.md) for provider settings, tools, test results and features requiring additional credentials or a UI bridge. Global Pi extensions are unchanged.
