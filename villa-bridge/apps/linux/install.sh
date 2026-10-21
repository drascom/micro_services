#!/bin/sh
set -eu

# Villa Bridge Linux/Raspberry Pi installer.
#
# Scope on purpose: this script only does OPERATING SYSTEM level work — packages,
# service account, data directory ownership, the systemd unit, enable and start.
# It does NOT create application configuration. The same product also runs on an
# Android tablet where there is no shell and no installer, so first-run seeding
# (villa-bridge.yaml, zigbee/configuration.yaml, generated Zigbee network key)
# lives in the shared runtime: apps/runtime/first-run.cjs. Both hosts take the
# same path, and the rest of the setup is finished in the dashboard wizard.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)"
DATA_DIR="${VILLA_BRIDGE_DATA_DIR:-/var/lib/villa-bridge}"
SERVICE_USER="${VILLA_BRIDGE_USER:-villa-bridge}"
SERVICE_GROUP="${VILLA_BRIDGE_GROUP:-villa-bridge}"
UNIT_PATH="/etc/systemd/system/villa-bridge.service"
NODE_MINIMUM=22
ASSUME_YES="${ASSUME_YES:-0}"
START_SERVICE=true

usage() {
  cat >&2 <<'USAGE'
Usage: install.sh [-y|--yes] [--no-start]

  -y, --yes    Install missing prerequisites without asking (needed over SSH
               and in any non-interactive run).
      --no-start
               Install and enable the service but do not start it.

Environment: ASSUME_YES=1 is the same as --yes.
USAGE
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --no-start) START_SERVICE=false ;;
    --start) START_SERVICE=true ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root (sudo apps/linux/install.sh)." >&2
  exit 1
fi

step() { printf '\n==> %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# Asks the user, unless --yes was given. Without a terminal and without --yes it
# never blocks: it explains what to run and stops.
confirm() {
  question="$1"
  if [ "$ASSUME_YES" = "1" ]; then
    note "$question -> yes (--yes)"
    return 0
  fi
  if [ ! -t 0 ]; then
    echo "No terminal available to ask: $question" >&2
    echo "Re-run with --yes (or ASSUME_YES=1) to allow this automatically." >&2
    exit 1
  fi
  printf '    %s [Y/n] ' "$question"
  read -r answer </dev/tty || answer=""
  case "$answer" in
    ""|y|Y|e|E|yes|YES|evet) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# 1. Operating system prerequisites
# ---------------------------------------------------------------------------
step "Checking operating system prerequisites"

# A bare Debian 13 image has none of these. Without them the Node.js installer
# used to die with "Missing required command: curl" and nothing explained why.
MISSING_PACKAGES=""
add_missing() {
  case " $MISSING_PACKAGES " in
    *" $1 "*) ;;
    *) MISSING_PACKAGES="${MISSING_PACKAGES} $1" ;;
  esac
}

command -v curl >/dev/null 2>&1 || add_missing curl
command -v git >/dev/null 2>&1 || add_missing git
command -v tar >/dev/null 2>&1 || add_missing tar
command -v xz >/dev/null 2>&1 || add_missing xz-utils
[ -e /etc/ssl/certs/ca-certificates.crt ] || add_missing ca-certificates

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is required and was not found; this installer targets systemd hosts." >&2
  exit 1
fi

MISSING_PACKAGES="$(printf '%s' "$MISSING_PACKAGES" | sed 's/^ *//')"
if [ -n "$MISSING_PACKAGES" ]; then
  note "Missing packages: ${MISSING_PACKAGES}"
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Install them with your package manager and run this installer again:" >&2
    echo "  ${MISSING_PACKAGES}" >&2
    exit 1
  fi
  if confirm "Install them now with apt-get?"; then
    DEBIAN_FRONTEND=noninteractive apt-get update
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $MISSING_PACKAGES
  else
    echo "Nothing was changed. Install the packages yourself and run this installer again:" >&2
    echo "  sudo apt-get install -y ${MISSING_PACKAGES}" >&2
    exit 1
  fi
else
  note "All required packages are present."
fi

# ---------------------------------------------------------------------------
# 2. Node.js
# ---------------------------------------------------------------------------
step "Checking Node.js"
NODE_OK=false
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
  if [ "$NODE_MAJOR" -ge "$NODE_MINIMUM" ]; then
    NODE_OK=true
    note "Found $(node --version)."
  else
    note "Found $(node --version); Villa Bridge needs Node.js ${NODE_MINIMUM} or newer."
  fi
else
  note "Node.js is not installed."
fi

if [ "$NODE_OK" = false ]; then
  if confirm "Download and install Node.js ${NODE_MINIMUM}+ into /usr/local?"; then
    "${SCRIPT_DIR}/install-node.sh"
  else
    echo "Nothing was changed. Install Node.js ${NODE_MINIMUM}+ yourself, for example:" >&2
    echo "  sudo apps/linux/install-node.sh" >&2
    exit 1
  fi
fi

case "$(uname -m)" in
  aarch64|arm64) RUNTIME_PLATFORM="raspberry-pi" ;;
  *) RUNTIME_PLATFORM="linux" ;;
esac

# ---------------------------------------------------------------------------
# 3. Service account and data directory
# ---------------------------------------------------------------------------
step "Preparing the service account and data directory"
if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
  groupadd --system "$SERVICE_GROUP"
  note "Created group ${SERVICE_GROUP}."
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --gid "$SERVICE_GROUP" --home-dir "$DATA_DIR" \
    --shell /usr/sbin/nologin "$SERVICE_USER"
  note "Created service account ${SERVICE_USER}."
fi
# A USB coordinator appears as /dev/ttyUSB* or /dev/ttyACM*, owned by `dialout`.
# Network coordinators (SLZB over TCP) do not need this, but adding the account
# costs nothing and saves a confusing permission error later.
if getent group dialout >/dev/null 2>&1; then
  if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx dialout; then
    usermod -a -G dialout "$SERVICE_USER"
    note "Added ${SERVICE_USER} to the dialout group for USB coordinators."
  fi
fi

# Only the directory is created here. Its CONTENTS are seeded by the shared
# runtime on first start, so an Android tablet without a shell gets the same
# files. Existing data is never touched.
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 "$DATA_DIR"
note "Private data directory: ${DATA_DIR}"

# ---------------------------------------------------------------------------
# 4. Build
# ---------------------------------------------------------------------------
step "Building the shared Villa Bridge core"
(cd "$PROJECT_ROOT" && npm ci && npm run build)
step "Installing the shared MQTT and Matter runtime"
(cd "${PROJECT_ROOT}/apps/runtime" && npm ci --omit=dev)

# ---------------------------------------------------------------------------
# 5. systemd unit
# ---------------------------------------------------------------------------
step "Installing the systemd unit"
escape_sed() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

TEMP_UNIT="$(mktemp)"
trap 'rm -f "$TEMP_UNIT"' EXIT HUP INT TERM
sed \
  -e "s|@SERVICE_USER@|$(escape_sed "$SERVICE_USER")|g" \
  -e "s|@SERVICE_GROUP@|$(escape_sed "$SERVICE_GROUP")|g" \
  -e "s|@PROJECT_ROOT@|$(escape_sed "$PROJECT_ROOT")|g" \
  -e "s|@NODE@|$(escape_sed "$(command -v node)")|g" \
  -e "s|@DATA_DIR@|$(escape_sed "$DATA_DIR")|g" \
  -e "s|@RUNTIME_PLATFORM@|${RUNTIME_PLATFORM}|g" \
  "${SCRIPT_DIR}/systemd/villa-bridge.service.in" > "$TEMP_UNIT"
install -o root -g root -m 0644 "$TEMP_UNIT" "$UNIT_PATH"
systemctl daemon-reload
systemctl enable villa-bridge.service >/dev/null
note "Unit installed at ${UNIT_PATH} and enabled at boot."

# ---------------------------------------------------------------------------
# 6. Start and hand over to the dashboard
# ---------------------------------------------------------------------------
if [ "$START_SERVICE" = true ]; then
  step "Starting Villa Bridge"
  systemctl restart villa-bridge.service
else
  note "Not starting the service (--no-start)."
fi

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$HOST_IP" ] || HOST_IP="<host-ip>"

cat <<EOF

Villa Bridge is installed for ${RUNTIME_PLATFORM}.

  Dashboard   http://${HOST_IP}:8091
  Data        ${DATA_DIR}
  Logs        journalctl -u villa-bridge --all -f

Open the dashboard and finish the setup there. Until the coordinator address is
entered in the wizard, Villa Bridge deliberately connects to NO coordinator.
EOF
