#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="scan-emails"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [ -f /etc/debian_version ]; then
  APT_PREFIX="sudo"
  if [ "$(id -u)" -eq 0 ]; then
    APT_PREFIX=""
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y python3 curl ca-certificates
  fi
  if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y "python${PY_MINOR}-venv" python3-venv
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

if [ -d "${SCRIPT_DIR}/venv" ] && [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  rm -rf "${SCRIPT_DIR}/venv"
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

if command -v systemctl >/dev/null 2>&1; then
  cat <<EOF > "${SERVICE_FILE}"
[Unit]
Description=Scan Emails Service
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=./run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"

  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl status "${SERVICE_NAME}" --no-pager
    echo "Service ${SERVICE_NAME} failed to start." >&2
    exit 1
  fi
else
  echo "systemctl not found; skipping service installation." >&2
  exit 1
fi
