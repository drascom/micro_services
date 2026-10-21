#!/usr/bin/env bash
# build-wgt.sh - tizen-client icerigini ../dist/diziflix.wgt olarak paketler.
# config.xml paket kokunde olur. Gizli dosyalar, __MACOSX, *.wgt ve bu script haric.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$(cd "$SRC_DIR/.." && pwd)/dist"
OUT="$DIST_DIR/diziflix.wgt"

mkdir -p "$DIST_DIR"
rm -f "$OUT"

cd "$SRC_DIR"

# --- build/versiyon damgasi: her build'de js/version.js yeniden yazilir ---
BUILD_STAMP="$(date '+%Y-%m-%d %H:%M')"
cat > "$SRC_DIR/js/version.js" <<EOF
/* version.js - build-wgt.sh tarafindan her build'de yeniden yazilir. */
window.DZ_BUILD='$BUILD_STAMP';
EOF
echo "Build damgasi: $BUILD_STAMP"

# zip kokunde config.xml olacak sekilde paketle; gereksiz/gizli dosyalar haric.
zip -r -X "$OUT" . \
  -x '.*' \
  -x '*/.*' \
  -x '__MACOSX/*' \
  -x '*.wgt' \
  -x 'build-wgt.sh' \
  >/dev/null

echo "Olusturuldu: $OUT ($(du -h "$OUT" | cut -f1))"
echo "--- unzip -l (ilk satirlar) ---"
unzip -l "$OUT" | head -20
