#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "${SCRIPT_DIR}/venv" ]; then
  python3 -m venv "${SCRIPT_DIR}/venv"
fi

source "${SCRIPT_DIR}/venv/bin/activate"

if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
  python -m pip install -r "${SCRIPT_DIR}/requirements.txt"
fi

exec uvicorn app:app --reload --host 0.0.0.0 --port 8001
