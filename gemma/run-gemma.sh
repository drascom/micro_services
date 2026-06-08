#!/usr/bin/env bash
# CLI alternative to the app: start the Gemma server in this terminal.
# Reads paths from config.env (created by ./install.sh). Ctrl-C to stop.
set -euo pipefail
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults; config.env overrides them.
LLAMA_SERVER="$PROJ/llama.cpp-mainline/build/bin/llama-server"
MODEL="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf"
MMPROJ="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/mmproj-gemma-4-12b-it-qat-q4_0.gguf"
HOST="0.0.0.0"; PORT=8080; CTX=32768
[ -f "$PROJ/config.env" ] && source "$PROJ/config.env"

[ -x "$LLAMA_SERVER" ] || { echo "llama-server not built. Run ./install.sh first."; exit 1; }

exec "$LLAMA_SERVER" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --alias gemma-4-12b \
  -ngl 999 \
  -c "$CTX" \
  --jinja \
  --host "$HOST" --port "$PORT"
