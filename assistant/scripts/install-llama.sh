#!/bin/sh
# Installs the pinned llama.cpp macOS arm64 (Metal) prebuilt into runtime/llama/ (chat :8091 and embeddings :8093).
# Usage: scripts/install-llama.sh [target-dir]   (default runtime/llama; existing dir is replaced only after success)
# components.json drives version/checksum via COMPONENT_VERSION, COMPONENT_SHA256 and COMPONENT_TARGET (setup.sh, updates).
set -eu
VERSION=${COMPONENT_VERSION:-b11574}
SHA256=${COMPONENT_SHA256:-85da72af68fce98663c8dc028d89d79d0083b1bf0d3df34340224c6fdf3a812b}
URL="https://github.com/ggml-org/llama.cpp/releases/download/$VERSION/llama-$VERSION-bin-macos-arm64.tar.gz"

cd "$(dirname "$0")/.."
TARGET=${COMPONENT_TARGET:-${1:-runtime/llama}}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/llama.tgz" "$URL"
echo "$SHA256  $TMP/llama.tgz" | shasum -a 256 -c -
tar -xzf "$TMP/llama.tgz" -C "$TMP"
xattr -dr com.apple.quarantine "$TMP/llama-$VERSION" 2>/dev/null || true
"$TMP/llama-$VERSION/llama-server" --version
printf '{\n  "version": "%s",\n  "source": "%s",\n  "sha256": "%s"\n}\n' "$VERSION" "$URL" "$SHA256" > "$TMP/llama-$VERSION/BUILD.json"
mkdir -p "$(dirname "$TARGET")"
if [ -e "$TARGET" ]; then mv "$TARGET" "$TMP/old"; fi
mv "$TMP/llama-$VERSION" "$TARGET"
echo "llama.cpp $VERSION -> $TARGET"
