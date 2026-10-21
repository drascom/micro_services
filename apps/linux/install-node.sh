#!/bin/sh
set -eu

NODE_VERSION="${VILLA_NODE_VERSION:-22.23.1}"
INSTALL_BASE="${VILLA_NODE_INSTALL_BASE:-/usr/local/lib/nodejs}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64|amd64) NODE_ARCH="x64" ;;
  aarch64|arm64) NODE_ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: $(uname -m). Use a 64-bit x86 or ARM system." >&2
    exit 1
    ;;
esac

for command in curl sha256sum tar; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done

ARCHIVE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
BASE_URL="https://nodejs.org/download/release/v${NODE_VERSION}"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT HUP INT TERM

curl --fail --location --proto '=https' --tlsv1.2 \
  "${BASE_URL}/${ARCHIVE}" -o "${TEMP_DIR}/${ARCHIVE}"
curl --fail --location --proto '=https' --tlsv1.2 \
  "${BASE_URL}/SHASUMS256.txt" -o "${TEMP_DIR}/SHASUMS256.txt"

(
  cd "$TEMP_DIR"
  grep "  ${ARCHIVE}\$" SHASUMS256.txt | sha256sum --check -
)

install -d -m 0755 "$INSTALL_BASE"
tar -xJf "${TEMP_DIR}/${ARCHIVE}" -C "$INSTALL_BASE"
NODE_HOME="${INSTALL_BASE}/node-v${NODE_VERSION}-linux-${NODE_ARCH}"
ln -sfn "${NODE_HOME}/bin/node" /usr/local/bin/node
ln -sfn "${NODE_HOME}/bin/npm" /usr/local/bin/npm
ln -sfn "${NODE_HOME}/bin/npx" /usr/local/bin/npx
ln -sfn "${NODE_HOME}/bin/corepack" /usr/local/bin/corepack

echo "Installed $(/usr/local/bin/node --version) for linux-${NODE_ARCH}."
