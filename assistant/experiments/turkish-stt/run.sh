#!/usr/bin/env bash
# Tüm ölçümler, SIRALI (paralel koşturma süreleri bozar). Şarj takılı olsun.
#   ./run.sh          tüm setler (human, say-tr, mihu-synth, say-en, varsa user)
#   ./run.sh user     yalnız audio/user/ kayıtları (record.py sonrası) + rapor
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
[[ -x $PY ]] || { echo "Önce kurulum: README.md > Kurulum" >&2; exit 1; }
pmset -g batt | head -1
$PY prepare.py
if [[ "${1:-}" == "user" ]]; then SETS=(user)
else SETS=(human say-tr mihu-synth say-en); [[ -f runs/sets/user.jsonl ]] && SETS+=(user); fi
for s in "${SETS[@]}"; do
  # mihu modeli silindi (models/turkish-stt yoksa eval.py mesajla atlar; yeniden indirme: README.md > Kurulum)
  $PY eval.py mihu --set "$s" --threads 1
  $PY eval.py mihu --set "$s" --threads 4
  $PY eval.py mihu --set "$s" --threads 1 --fp32
  $PY eval.py whisper --set "$s" --lang auto --threads 8 --name whisper-asistan   # eski yol: dosya başı whisper-cli
  $PY eval.py whisper --set "$s" --lang auto --threads 8 --server --name whisper-server   # yeni yol: src/whisper.ts
  $PY eval.py whisper --set "$s" --lang tr --threads 8 --name whisper-tr-t8
  $PY eval.py whisper --set "$s" --lang tr --threads 4 --name whisper-tr-t4
done
if [[ "${1:-}" != "user" ]]; then  # yalnız referans amaçlı, kısa sette
  $PY eval.py whisper --set say-tr --lang tr --threads 1 --name whisper-tr-t1
  $PY eval.py whisper --set say-tr --lang tr --threads 4 --cpu --name whisper-tr-t4-cpu
fi
$PY score.py > /dev/null && echo "Rapor: runs/summary.md"
