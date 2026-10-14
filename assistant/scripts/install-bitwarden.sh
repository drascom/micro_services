#!/bin/sh
# Installs the pinned official Bitwarden CLI (npm @bitwarden/cli, pure Node: macOS and Linux) into runtime/bitwarden/
# and points it at the vault server. Usage: scripts/install-bitwarden.sh [server-url]   (default https://pass.drascom.uk)
set -eu
VERSION=2026.9.0
SERVER=${1:-https://pass.drascom.uk}

cd "$(dirname "$0")/.."
TARGET=runtime/bitwarden
mkdir -p "$TARGET" data/bitwarden
chmod 700 data/bitwarden
printf '{\n  "private": true,\n  "dependencies": { "@bitwarden/cli": "%s" }\n}\n' "$VERSION" > "$TARGET/package.json"
npm install --prefix "$TARGET" --ignore-scripts --omit=dev --no-audit --no-fund --loglevel=error
BW="node $TARGET/node_modules/@bitwarden/cli/build/bw.js"
export BITWARDENCLI_APPDATA_DIR="$PWD/data/bitwarden"
$BW --version
# Changing the server is refused while logged in; then it is already configured.
$BW config server "$SERVER" >/dev/null 2>&1 || true
$BW status --nointeraction
