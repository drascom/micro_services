# Bitwarden / Vaultwarden vault

Find, copy and save passwords, tokens and 2FA codes by voice — with the **local model only**. Secret values never
reach any model, tool result, transcript, conversation history, activity log or spoken reply.

## Setup
1. `scripts/install-bitwarden.sh` — installs the pinned official CLI (`@bitwarden/cli` 2026.9.0, pure Node, macOS and
   Linux) into `runtime/bitwarden/`, points it at `https://pass.drascom.uk`; CLI data lives in `data/bitwarden/` (0700, gitignored).
2. Admin → Ayarlar → **Bitwarden**: switch on, email, master password (+ 2FA code on first login) → **Kilidi aç**.
   **Kilitle** locks. The master password is never stored or logged; it reaches `bw` only through the child's environment (`--passwordenv`).

## Lock rules
Session key in backend memory only (passed to `bw` as `BW_SESSION`, never argv). Dropped on: Lock, switch off,
`bitwarden_idle_lock_min` (default 15) without use, system sleep (app notifies the backend), app quit.

## Tools (registered only: local model + switch on + unlocked + user request, not tasks)
- `vault_search {query}` → ≤5 items: name, username, URI host, id, has_totp. Never password, notes, cards, hidden fields, TOTP secret.
- `vault_copy {id, field: password|username|totp}` → value to the clipboard (pbcopy; Linux wl-copy/xclip), cleared after
  30 s if unchanged; returns `{copied, field, item}` only.
- `vault_copy {…, speak: true}` ("şifremi sesli oku") → the backend spells the value in Turkish ("büyük A, küçük be, tire,
  yedi"; groups of 4, TOTP in groups of 3), slower, straight into the current TTS engine. Returns `{spoken, field, item}`
  only; the reply/ring shows `<item> <field>: ***`. The value and its spelling skip speech normalization, language
  detection, respelling and the activity log; the temp WAV (0700 dir) is deleted right after playback. Stop/new request
  stops it (`speech_stopped`); ring "Read aloud" off → `speech_off`; TTS failure → `speech_unavailable` (no text fallback).
- `vault_save {name, source: clipboard|prompt|generate, kind?, username?, uri?, length?, confirm_token?}` — no value parameter.
  `clipboard`: read in the backend, clipboard cleared after saving if unchanged. `prompt`: the app shows a secure input
  window (long-poll `/secret-prompt/wait`, answer `/secret-prompt/answer`, token-protected loopback); cancel saves nothing.
  `generate`: `bw generate -ulns`, saved and copied (30 s). New items are Login items (a token = password field).
  Existing name: nothing written; `confirm_token` redeemable only in the user's next turn → only the password changes
  (Bitwarden keeps the old one in password history). No delete, no other edits.
- Remote model: no tools, skill not advertised; a vault request gets one line ("needs the local model"). The permissions
  extension also blocks `vault_*` outside a local session.
- A secret spoken aloud is not saved (the skill offers clipboard/prompt instead).

## Config
`bitwarden_enabled` (false), `bitwarden_server`, `bitwarden_email`, `bitwarden_idle_lock_min` (15), `bitwarden_bin`.
