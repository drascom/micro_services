#!/usr/bin/env bash
# Install the pinned, no-render stealth Obscura release used for HTML discovery.
set -euo pipefail

if [ "$(id -u)" != 0 ]; then
  echo 'Run as root to install the isolated Obscura runtime.' >&2
  exit 1
fi

VERSION=0.2.2
RUNTIME_DIR=/opt/diziflix-obscura
case "$(uname -m)" in
  x86_64)
    ASSET=obscura-x86_64-linux-no-render-stealth.tar.gz
    SHA256=817a7274e20b20402b388cb20aa0723a22f118be892a659a9ca19d7aaea4463d
    ;;
  aarch64|arm64)
    ASSET=obscura-aarch64-linux-no-render-stealth.tar.gz
    SHA256=a5b0195a27445906bccba4ad0f6f2c9c7ce7b1b63ad269b230eb5e24a326bf61
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

TEMP_DIR="$(mktemp -d -t diziflix-obscura.XXXXXX)"
trap 'rm -rf "$TEMP_DIR"' EXIT
URL="https://github.com/h4ckf0r0day/obscura/releases/download/v${VERSION}/${ASSET}"

curl -fL --retry 2 -o "$TEMP_DIR/$ASSET" "$URL"
printf '%s  %s\n' "$SHA256" "$TEMP_DIR/$ASSET" | sha256sum -c -
tar -xzf "$TEMP_DIR/$ASSET" -C "$TEMP_DIR"
install -d -m 755 "$RUNTIME_DIR"
install -m 755 "$TEMP_DIR/obscura" "$RUNTIME_DIR/obscura"
install -m 755 "$TEMP_DIR/obscura-worker" "$RUNTIME_DIR/obscura-worker"
"$RUNTIME_DIR/obscura" --version
