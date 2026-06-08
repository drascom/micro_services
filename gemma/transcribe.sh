#!/usr/bin/env bash
# Best-effort audio transcription with Gemma 4 via the local llama.cpp server.
#
# IMPORTANT: Gemma 4 audio is EXPERIMENTAL and Gemma is a reasoning model, so it
# is ERRATIC at transcription — across identical runs it may transcribe, deny the
# file, or return blank. This script retries a few times until it commits to a
# real answer. For accurate/reliable transcription use Whisper (see README).
#
# Usage:  ./transcribe.sh /path/to/audio.(wav|mp3|flac)   [instruction]
# Needs the server running (Serve Gemma / ./run-gemma.sh).
set -euo pipefail
FILE="${1:?Usage: ./transcribe.sh <audiofile> [instruction]}"
INSTR="${2:-Bu sesi birebir yaz.}"
[ -f "$FILE" ] || { echo "No such file: $FILE"; exit 1; }

FILE="$FILE" INSTR="$INSTR" python3 - <<'PY'
import base64, json, os, sys, urllib.request
F=os.environ["FILE"]; INSTR=os.environ["INSTR"]
fmt=os.path.splitext(F)[1].lstrip(".").lower() or "wav"
b64=base64.b64encode(open(F,"rb").read()).decode()
SYS=("Sen ses dosyalarini duyabilen bir transkripsiyon asistanisin. "
     "Gonderilen sesi dikkatle dinle. Asla 'dosya yok' veya bos cevap verme. "
     "Cevabin SADECE duydugun konusmanin birebir metni olsun.")
def ask(temp):
    payload={"model":"gemma-4-12b","temperature":temp,"top_p":0.95,"max_tokens":4000,
     "messages":[{"role":"system","content":SYS},
      {"role":"user","content":[{"type":"text","text":INSTR},
        {"type":"input_audio","input_audio":{"data":b64,"format":fmt}}]}]}
    req=urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",
        data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    return (json.load(urllib.request.urlopen(req,timeout=300))
            ["choices"][0]["message"].get("content") or "").strip()

bad=("dosya yok","ses dosyası bulunmuyor","herhangi bir dosya","no audio","no file")
try:
    for i,temp in enumerate([0.2,0.5,0.7,0.9]):
        out=ask(temp)
        if out and not any(b in out.lower() for b in bad):
            print(out); sys.exit(0)
        print(f"[attempt {i+1}: empty/denied, retrying...]",file=sys.stderr)
    sys.exit("Gemma would not commit to a transcription. Use Whisper for this file (see README).")
except urllib.error.URLError as e:
    sys.exit(f"Request failed ({e}). Is the server running on :8080?")
PY
