#!/bin/sh
# Builds whisper.cpp (whisper-cli + whisper-server, static, embedded Metal, Accelerate BLAS) from the upstream source tag
# into runtime/whisper/. Needs cmake and Apple Command Line Tools.
# Usage: scripts/install-whisper.sh [target-dir]   (default runtime/whisper; existing dir is replaced only after success)
# components.json drives version/checksum via COMPONENT_VERSION, COMPONENT_SHA256 (source tarball) and COMPONENT_TARGET.
set -eu
VERSION=${COMPONENT_VERSION:-v1.9.4}
SHA256=${COMPONENT_SHA256:-}
URL="https://github.com/ggml-org/whisper.cpp/archive/refs/tags/$VERSION.tar.gz"

cd "$(dirname "$0")/.."
TARGET=${COMPONENT_TARGET:-${1:-runtime/whisper}}
command -v cmake >/dev/null || { echo "cmake missing (brew install cmake)" >&2; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/whisper.tgz" "$URL"
ACTUAL=$(shasum -a 256 "$TMP/whisper.tgz" | cut -d' ' -f1)
if [ -n "$SHA256" ] && [ "$SHA256" != "$ACTUAL" ]; then echo "checksum mismatch: $ACTUAL" >&2; exit 1; fi
tar -xzf "$TMP/whisper.tgz" -C "$TMP"
SRC=$(find "$TMP" -maxdepth 1 -type d -name 'whisper.cpp-*' | head -1)
FLAGS="-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_BACKEND_DL=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_BLAS=ON -DGGML_OPENMP=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=ON"
# shellcheck disable=SC2086
cmake -S "$SRC" -B "$SRC/build" $FLAGS >/dev/null
cmake --build "$SRC/build" --config Release -j --target whisper-cli whisper-server >/dev/null
OUT="$TMP/out"; mkdir -p "$OUT"
cp "$SRC/build/bin/whisper-cli" "$SRC/build/bin/whisper-server" "$OUT/"
cp "$SRC/LICENSE" "$OUT/LICENSE-whisper"; cp "$SRC/ggml/LICENSE" "$OUT/LICENSE-ggml" 2>/dev/null || true
"$OUT/whisper-cli" --help >/dev/null 2>&1
printf '{\n  "source": "%s",\n  "source_sha256": "%s",\n  "version": "%s",\n  "build": "Release; static whisper/ggml; embedded Metal; Accelerate BLAS; native CPU; no OpenMP",\n  "cmake_flags": "%s"\n}\n' \
  "$URL" "$ACTUAL" "$VERSION" "$FLAGS" > "$OUT/BUILD.json"
mkdir -p "$(dirname "$TARGET")"
if [ -e "$TARGET" ]; then mv "$TARGET" "$TMP/old"; fi
mv "$OUT" "$TARGET"
echo "whisper.cpp $VERSION -> $TARGET"
