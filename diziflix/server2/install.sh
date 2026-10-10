#!/usr/bin/env bash
# server2 setup: venv (uv when available) + requirements + .env from .env.example; on Linux the systemd unit `diziflix2`
# (separate from the old `diziflix` service on 8090). Idempotent: deploy.sh runs it on a first install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8091}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is missing." >&2
  exit 1
fi
UV_BIN="$(command -v uv || true)"
if [ -z "${UV_BIN}" ] && [ -x "${HOME}/.local/bin/uv" ]; then UV_BIN="${HOME}/.local/bin/uv"; fi

if [ ! -f "${SCRIPT_DIR}/.env" ] && [ -f "${SCRIPT_DIR}/.env.example" ]; then
  cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
  echo "Created ${SCRIPT_DIR}/.env from .env.example."
fi

if [ -d "${SCRIPT_DIR}/venv" ] && [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  rm -rf "${SCRIPT_DIR}/venv"
fi
if [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  if [ -n "${UV_BIN}" ]; then
    "${UV_BIN}" venv "${SCRIPT_DIR}/venv"
  else
    python3 -m venv "${SCRIPT_DIR}/venv"
  fi
fi

if [ -n "${UV_BIN}" ]; then
  "${UV_BIN}" pip install --python "${SCRIPT_DIR}/venv/bin/python" -r "${SCRIPT_DIR}/requirements.txt"
else
  "${SCRIPT_DIR}/venv/bin/python" -m pip install --upgrade pip >/dev/null
  "${SCRIPT_DIR}/venv/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements.txt"
fi

mkdir -p "${SCRIPT_DIR}/data"

# ---------------------------------------------------------------- systemd ----
SERVICE_NAME="diziflix2"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
if [ "$(uname -s)" != "Linux" ] || ! command -v systemctl >/dev/null 2>&1; then
  echo "No systemd here. Run: cd ${SCRIPT_DIR} && venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
  exit 0
fi
SUDO="sudo"
if [ "$(id -u)" -eq 0 ]; then SUDO=""; fi
# No --reload. SIGTERM + 30 s lets the job executor finish its step and hand unfinished jobs back to the queue.
${SUDO} tee "${SERVICE_FILE}" >/dev/null <<UNIT
[Unit]
Description=diziflix server2 (catalogue API + admin, port ${PORT})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=${SCRIPT_DIR}
Environment=HOST=0.0.0.0
Environment=PORT=${PORT}
EnvironmentFile=-${SCRIPT_DIR}/.env
ExecStart=${SCRIPT_DIR}/venv/bin/uvicorn app.main:app --host \${HOST} --port \${PORT}
Restart=on-failure
RestartSec=3
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable "${SERVICE_NAME}" >/dev/null
if ! ${SUDO} systemctl is-active --quiet "${SERVICE_NAME}"; then ${SUDO} systemctl start "${SERVICE_NAME}"; fi
echo "systemd: ${SERVICE_NAME} enabled (journalctl -u ${SERVICE_NAME} -f)"
