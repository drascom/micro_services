#!/usr/bin/env bash
# Double-click this from the Desktop to start serving Gemma 4 12B locally via llama.cpp.
# It exposes an OpenAI-compatible API your other apps can use.
#
#   Base URL : http://127.0.0.1:8080/v1
#   API key  : (anything — not checked)
#   Model    : gemma-4-12b
#
# Close this Terminal window (or press Ctrl-C) to stop the server.

GEMMA_DIR="$HOME/work/GEMMA"
SERVER="$GEMMA_DIR/llama.cpp-mainline/build/bin/llama-server"
MODEL="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf"
MMPROJ="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/mmproj-gemma-4-12b-it-qat-q4_0.gguf"
PORT=8080

clear
echo "======================================================"
echo "  Starting Gemma 4 12B  (llama.cpp, Metal)"
echo "======================================================"

# Sanity checks with friendly errors
if [ ! -x "$SERVER" ]; then
  echo "ERROR: llama-server not found at:"
  echo "  $SERVER"
  echo "Rebuild it (see $GEMMA_DIR/README.md), then try again."
  echo; read -r -p "Press Return to close..."; exit 1
fi
if [ ! -f "$MODEL" ]; then
  echo "ERROR: model file not found at:"
  echo "  $MODEL"
  echo "Is it still in your LM Studio models folder?"
  echo; read -r -p "Press Return to close..."; exit 1
fi

# Stop any server already holding the port
pkill -f "llama-server.*--port $PORT" 2>/dev/null && sleep 1

echo "  Endpoint : http://127.0.0.1:$PORT/v1   (OpenAI-compatible)"
echo "  Model id : gemma-4-12b"
echo "  Features : text + images + audio + reasoning + tools"
echo "  Chat UI  : http://127.0.0.1:$PORT"
echo "  Stop     : close this window or press Ctrl-C"
echo "------------------------------------------------------"

# Open the chat UI in the browser once the server is up (runs in background)
( for i in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/health 2>/dev/null)" = "200" ] && { open "http://127.0.0.1:$PORT"; break; }
    sleep 1
  done ) &

exec "$SERVER" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --alias gemma-4-12b \
  -ngl 999 \
  -c 32768 \
  --jinja \
  --host 0.0.0.0 --port $PORT
