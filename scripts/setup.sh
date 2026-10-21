#!/usr/bin/env bash
# One-command setup: prerequisites, every runtime and model from components.json, npm deps, the app, doctor.
# Idempotent and resumable: installed components are skipped, model downloads resume from `.part`.
# Usage: scripts/setup.sh [--dry-run] [--skip-big-models] [--no-app]
#   --dry-run          only report what would be installed (nothing is downloaded, installed or built)
#   --skip-big-models  skip model downloads of 1 GB or more (Gemma, Whisper); install them later by re-running
#   --no-app           do not build PersonalAssistant.app
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0; SKIP_BIG=0; APP=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;; --skip-big-models) SKIP_BIG=1 ;; --no-app) APP=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
OS=$(uname -s); ARCH=$(uname -m)
step() { printf '\n== %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then echo "would run: $*"; else "$@"; fi; }
missing=()

step "Platform"
case "$OS-$ARCH" in
  Darwin-arm64) echo "macOS $(sw_vers -productVersion) on Apple Silicon" ;;
  # TODO(linux): llama.cpp linux prebuilt (CUDA) and whisper.cpp CUDA build in components.json; no native app/detect-language.
  Linux-*) echo "Linux: runtimes marked darwin-* in components.json are skipped (TODO)" ;;
  *) echo "Unsupported platform $OS-$ARCH" >&2; exit 1 ;;
esac

step "Prerequisites"
need_brew() { # $1 command, $2 brew formula
  if command -v "$1" >/dev/null 2>&1; then echo "ok   $1"; return; fi
  if [ "$OS" = Darwin ] && command -v brew >/dev/null 2>&1; then run brew install "$2"
  else missing+=("$1 ($2)"); echo "MISSING $1"; fi
}
if [ "$OS" = Darwin ]; then
  command -v brew >/dev/null 2>&1 && echo "ok   brew" || { echo "Homebrew missing: https://brew.sh" >&2; exit 1; }
  xcode-select -p >/dev/null 2>&1 && echo "ok   Command Line Tools" || { echo "Run: xcode-select --install" >&2; exit 1; }
fi
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
if [ "$NODE_MAJOR" -ge 24 ]; then echo "ok   node $(node -v)"
elif [ "$OS" = Darwin ]; then run brew install node@24; [ "$DRY" = 1 ] || export PATH="$(brew --prefix node@24)/bin:$PATH"
else missing+=("node 24+"); fi
need_brew uv uv
need_brew python3.12 python@3.12
need_brew python3.13 python@3.13
need_brew cmake cmake          # whisper.cpp source build
need_brew ffmpeg ffmpeg        # pi-web-access video helpers
if [ ${#missing[@]} -gt 0 ]; then
  if [ "$DRY" = 1 ]; then echo "would need: ${missing[*]}"; else echo "Install first: ${missing[*]}" >&2; exit 1; fi
fi

step "npm dependencies"
LOCK_SUM=$(shasum -a 256 package-lock.json | cut -d' ' -f1)
if [ -d node_modules ] && [ "$(cat node_modules/.setup-lock 2>/dev/null)" = "$LOCK_SUM" ]; then echo "ok   node_modules"
elif [ -d node_modules ] && npm ls --depth=0 >/dev/null 2>&1; then
  echo "ok   node_modules (matches package-lock)"; [ "$DRY" = 1 ] || echo "$LOCK_SUM" > node_modules/.setup-lock
else run npm ci --ignore-scripts --no-audit --no-fund; [ "$DRY" = 1 ] || echo "$LOCK_SUM" > node_modules/.setup-lock; fi

step "Runtimes and models (components.json)"
FLAGS=()
[ "$DRY" = 1 ] && FLAGS+=(--dry-run)
[ "$SKIP_BIG" = 1 ] && FLAGS+=(--skip-big-models)
node scripts/components.ts install "${FLAGS[@]+"${FLAGS[@]}"}"

step "App"
if [ "$OS" != Darwin ] || [ "$APP" = 0 ]; then echo "skipped"   # TODO(linux): headless backend only (no SwiftUI ring)
elif [ -d PersonalAssistant.app ] && [ -x runtime/detect-language ]; then echo "ok   PersonalAssistant.app (rebuild: npm run rebuild-app)"
else run npm run build-app; fi

step "Doctor"
node src/main.ts doctor

step "Manual steps"
cat <<'EOF'
- Pi login (remote chat): run `pi`, then /login (OpenAI subscription). TypeSafe Jev key: env.txt (see env.example.txt).
- macOS permissions on first use: Microphone, Accessibility (shortcut/paste), Automation for Mail and Reminders.
- Gmail: Admin → Ayarlar → Gmail: OAuth client, Add account (see GMAIL.md).
- Bitwarden: Admin → Ayarlar → Bitwarden: email, master password Unlock (see VAULT.md).
- Personal rules: Admin → Edit instructions. Start: open PersonalAssistant.app
EOF
