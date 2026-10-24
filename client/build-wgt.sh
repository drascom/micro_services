#!/usr/bin/env bash
# build-wgt.sh - client/ icerigini ../dist/diziflix.wgt (Tizen) olarak paketler.
# config.xml paket kokunde. Haric: gizli dosyalar, tests/, README.md, bu script, *.wgt, __MACOSX.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$(cd "$SRC_DIR/.." && pwd)/dist"
OUT="$DIST_DIR/diziflix.wgt"

mkdir -p "$DIST_DIR"
rm -f "$OUT"
cd "$SRC_DIR"

# build/versiyon damgasi: her build'de js/version.js yeniden yazilir
BUILD_STAMP="$(date '+%Y-%m-%d %H:%M')"
cat > "$SRC_DIR/js/version.js" <<STAMP
/* version.js - build-wgt.sh tarafindan her build'de yeniden yazilir. */
window.DZ_BUILD='$BUILD_STAMP';
STAMP
echo "Build damgasi: $BUILD_STAMP"

zip -r -X "$OUT" . \
  -x '.*' -x '*/.*' \
  -x '__MACOSX/*' \
  -x '*.wgt' \
  -x 'build-wgt.sh' \
  -x 'README.md' \
  -x 'tests/*' \
  >/dev/null

echo "Olusturuldu: $OUT ($(du -h "$OUT" | cut -f1))"
unzip -l "$OUT"
