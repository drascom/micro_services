#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f /etc/debian_version ]; then
  APT_PREFIX="sudo"
  if [ "$(id -u)" -eq 0 ]; then
    APT_PREFIX=""
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y python3 curl ca-certificates
  fi
  if ! python3 -m venv --help >/dev/null 2>&1; then
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y python3-venv
  fi
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is missing and this does not look like Debian/Ubuntu." >&2
    exit 1
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  else
    echo "curl is required to install uv." >&2
    exit 1
  fi
fi

if [ -f "${HOME}/.local/bin/env" ]; then
  # Ensure uv in PATH when installed to ~/.local/bin
  # shellcheck disable=SC1090
  source "${HOME}/.local/bin/env"
fi

UV_BIN="$(command -v uv || true)"
if [ -z "${UV_BIN}" ] && [ -x "${HOME}/.local/bin/uv" ]; then
  UV_BIN="${HOME}/.local/bin/uv"
fi

if [ -n "${UV_BIN}" ]; then
  if [ ! -d "${SCRIPT_DIR}/venv" ]; then
    "${UV_BIN}" venv "${SCRIPT_DIR}/venv"
  fi
fi

if [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  python3 -m venv "${SCRIPT_DIR}/venv"
fi

if [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  echo "Failed to create virtual environment at ${SCRIPT_DIR}/venv" >&2
  exit 1
fi

source "${SCRIPT_DIR}/venv/bin/activate"

if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
  if [ -n "${UV_BIN}" ]; then
    "${UV_BIN}" pip install -r "${SCRIPT_DIR}/requirements.txt"
  else
    python -m pip install -r "${SCRIPT_DIR}/requirements.txt"
  fi
fi

exec uvicorn app:app --reload --host 0.0.0.0 --port 1000
