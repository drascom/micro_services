#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1 || ! command -v pip3 >/dev/null 2>&1; then
  if [ -f /etc/debian_version ]; then
    APT_PREFIX="sudo"
    if [ "$(id -u)" -eq 0 ]; then
      APT_PREFIX=""
    fi
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y python3 python3-pip
  else
    echo "python3/pip3 missing and this does not look like Debian/Ubuntu." >&2
    exit 1
  fi
fi

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
