#!/usr/bin/env bash
# RECOMMENDED on this Mac: plain Gemma 4 12B (no MTP draft) — fastest (~30 tok/s).
# Opens an OpenAI-compatible server + web UI at http://127.0.0.1:8080
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$HERE/llama.cpp-mainline/build/bin/llama-server"
TARGET="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf"
MMPROJ="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/mmproj-gemma-4-12b-it-qat-q4_0.gguf"

exec "$SERVER" \
  -m "$TARGET" \
  --mmproj "$MMPROJ" \
  -ngl 999 \
  -c 32768 \
  --jinja \
  --host 0.0.0.0 --port 8080
