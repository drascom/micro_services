# Component updates

## Manifest
`components.json` (in git) lists every component: `id`, `kind` (binary | npm-runtime | python-venv | model | npm-dep),
pinned `version`, `source` (GitHub releases, npm, PyPI, Hugging Face, url/local), `channel`, installer, `sha256`/files,
`smoke` test, `restart` target and `uses` (the flags/APIs we depend on, given to the summary model).

| channel | meaning |
|---|---|
| `stable` | newest non-prerelease |
| `prerelease` | prereleases too (llama.cpp publishes only these) |
| `manual` | never checked or offered (models, local builds); `node src/main.ts updates-check --all` still reports them |

Installers: `scripts/install-llama.sh`, `install-whisper.sh` (source build, needs cmake), `install-bitwarden.sh`
(contract: `COMPONENT_VERSION`, `COMPONENT_SHA256`, `COMPONENT_TARGET`); venvs via `uv`; models via resumable
`curl` + SHA-256; npm deps via `npm install --save-exact`. Shared code: `src/components.ts`.

## Daily check
- Backend scheduler (`src/updates.ts`): at `update_check_hour` (config, default 9) and on startup when the last check
  is older than 24 h. Read-only HTTP to GitHub API, npm registry, PyPI JSON, HF API; no model for detection.
- New version: release notes (truncated) → local Gemma (remote only if local is unavailable) → 2 Turkish sentences +
  `breaking`, `security`, `affects_our_flags`. Stored in `data/updates.json`.
- Manual read-only check: `node src/main.ts updates-check [--all]`.

## Asking
- At an idle moment (no request, task, recording or speech) the ring shows and reads e.g. "3 güncelleme var:
  llama.cpp, Pi, bw. Güncelleyeyim mi?" (panel opens without taking focus) plus a macOS notification.
- At most once a day; offered versions are snoozed until the next morning, so "sonra"/"hayır" needs nothing else.
  Admin "Atla / Skip" skips that version for good.
- The answer is the next turn: a one-time token (2 min, next turn only) lets the model call `updates apply`.
  The `updates` tool (`list | apply | rollback`) is sent only when routed (Jev `updates`) or when that offer is pending;
  never to automated tasks. apply/rollback without a token only return one; the model asks and stops.

## Apply
Queued and run in the request lane after the current reply (never during a request; a request that arrives
interrupts it, nothing is swapped, it is retried after the request up to 3 times):
1. install into `<path>.staging` (models: `<file>.staging`; npm deps: package files backed up to `data/updates/prev/`),
2. smoke test (llama.cpp: also tok/s of old vs new on a temp port; more than 10% slower fails),
3. atomic swap; previous kept as `<path>.prev` (venv paths relocated; requirements file re-frozen),
4. `components.json` pinned version updated (no git command; Admin shows "components.json değişti, commit edilmedi"),
5. only the affected process restarts (llama/embedding, whisper, EMA, Kokoro: lazily on next use). npm deps:
   `npm run check` + `npm test` (failure restores package files + `npm ci`), then `npm run rebuild-app`; restart Ayhan.

Failure: staging removed, live version untouched, result announced. Rollback (Admin or tool, with token) swaps
live and `.prev`, so it can be undone too. Models are never offered unless their channel is changed; Admin asks
with the download size first. Activity log: `update_*` events with component ids and versions only.

## Admin → Güncellemeler
Last check, "Şimdi kontrol et", per update: current → new, summary, flags, Güncelle / Atla; Geri al list.
