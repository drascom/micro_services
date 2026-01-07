#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="scan-emails"

if [ ! -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
  echo "Missing virtual environment. Run ${SCRIPT_DIR}/install.sh first." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl stop "${SERVICE_NAME}"
    echo "Stopped ${SERVICE_NAME} for manual run."
  fi
fi

trap 'echo "Manual run complete. Start service with: systemctl start ${SERVICE_NAME}. Check status with: systemctl status ${SERVICE_NAME}."' EXIT

source "${SCRIPT_DIR}/venv/bin/activate"

exec uvicorn app:app --reload --host 0.0.0.0 --port 1000
