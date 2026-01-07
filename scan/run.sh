#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  if ! python3 -m pip --version >/dev/null 2>&1; then
    python3 -m ensurepip --upgrade
  fi
  python3 -m pip install --user uv
fi

if [ ! -d "${SCRIPT_DIR}/venv" ]; then
  uv venv "${SCRIPT_DIR}/venv"
fi

source "${SCRIPT_DIR}/venv/bin/activate"

if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
  uv pip install -r "${SCRIPT_DIR}/requirements.txt"
fi

exec uvicorn app:app --reload --host 0.0.0.0 --port 1000
