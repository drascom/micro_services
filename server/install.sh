#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="diziflix"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PORT="${PORT:-8090}"

# ---------------------------------------------------------------- python ----
if [ -f /etc/debian_version ]; then
  APT_PREFIX="sudo"
  if [ "$(id -u)" -eq 0 ]; then APT_PREFIX=""; fi
  if ! command -v python3 >/dev/null 2>&1; then
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y python3 curl ca-certificates
  fi
  if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    ${APT_PREFIX} apt-get update
    ${APT_PREFIX} apt-get install -y "python${PY_MINOR}-venv" python3-venv
  fi
elif ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is missing and this does not look like Debian/Ubuntu." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh || true
fi
if [ -f "${HOME}/.local/bin/env" ]; then
  # shellcheck disable=SC1090
  source "${HOME}/.local/bin/env"
fi
UV_BIN="$(command -v uv || true)"
if [ -z "${UV_BIN}" ] && [ -x "${HOME}/.local/bin/uv" ]; then UV_BIN="${HOME}/.local/bin/uv"; fi

# ------------------------------------------------------------------- .env ----
if [ ! -f "${SCRIPT_DIR}/.env" ] && [ -f "${SCRIPT_DIR}/.env.example" ]; then
  cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
  echo "Created ${SCRIPT_DIR}/.env from .env.example."
fi

# ------------------------------------------------------------------- venv ----
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
if [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  echo "Failed to create virtual environment at ${SCRIPT_DIR}/venv" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/venv/bin/activate"

if [ -n "${UV_BIN}" ]; then
  "${UV_BIN}" pip install -r "${SCRIPT_DIR}/requirements.txt"
else
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r "${SCRIPT_DIR}/requirements.txt"
fi

# ---------------------------------------------------------------- fixture ----
mkdir -p "${SCRIPT_DIR}/data/imgcache"
if [ ! -f "${SCRIPT_DIR}/data/fixture.json" ]; then
  echo "Generating catalogue fixture..."
  python "${SCRIPT_DIR}/tools/gen_fixture.py"
fi

# ---------------------------------------------------------------- systemd ----
if [ "$(uname -s)" != "Linux" ]; then
  echo
  echo "Not Linux — skipping systemd installation."
  echo "Run it manually:"
  echo "  cd ${SCRIPT_DIR} && source venv/bin/activate"
  echo "  uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found; skipping service installation." >&2
  echo "Run: ${SCRIPT_DIR}/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
  exit 0
fi

SUDO="sudo"
if [ "$(id -u)" -eq 0 ]; then SUDO=""; fi

${SUDO} tee "${SERVICE_FILE}" >/dev/null <<UNIT
[Unit]
Description=diziflix catalogue API
After=network.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${SCRIPT_DIR}/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now "${SERVICE_NAME}"

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  ${SUDO} systemctl status "${SERVICE_NAME}" --no-pager || true
  echo "Service ${SERVICE_NAME} failed to start." >&2
  exit 1
fi

echo "diziflix is running on http://0.0.0.0:${PORT}"
echo "Status: sudo systemctl status ${SERVICE_NAME}   Logs: sudo journalctl -u ${SERVICE_NAME} -f"
