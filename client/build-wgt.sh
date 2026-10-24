#!/usr/bin/env bash
# build-wgt.sh - client/ icerigini ../dist/diziflix.wgt (Tizen) olarak paketler.
# config.xml paket kokunde. Haric: gizli dosyalar, tests/, README.md, version.json, bu script, *.wgt, __MACOSX.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$(cd "$SRC_DIR/.." && pwd)/dist"
OUT="$DIST_DIR/diziflix.wgt"

mkdir -p "$DIST_DIR"
rm -f "$OUT"
cd "$SRC_DIR"

# tek surum kaynagi: version.json {"version":"X.Y.Z"} -> config.xml widget version + js/version.js (DZ_VERSION + build damgasi)
VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' "$SRC_DIR/version.json" | head -1)"
[ -n "$VERSION" ] || { echo "HATA: version.json'da \"version\": \"X.Y.Z\" yok" >&2; exit 1; }
# yalniz <widget ... version="..."> (xml bildirimindeki version'a dokunma)
perl -0pi -e 's/(<widget\b[^>]*?\sversion=")[^"]*(")/${1}'"$VERSION"'${2}/s' "$SRC_DIR/config.xml"
BUILD_STAMP="$(date '+%Y-%m-%d %H:%M')"
cat > "$SRC_DIR/js/version.js" <<STAMP
/* version.js - build-wgt.sh tarafindan her build'de yeniden yazilir (kaynak: version.json). */
window.DZ_VERSION='$VERSION';
window.DZ_BUILD='$BUILD_STAMP';
STAMP
echo "Surum: $VERSION · build damgasi: $BUILD_STAMP"

zip -r -X "$OUT" . \
  -x '.*' -x '*/.*' \
  -x '__MACOSX/*' \
  -x '*.wgt' \
  -x 'build-wgt.sh' \
  -x 'README.md' \
  -x 'version.json' \
  -x 'tests/*' \
  >/dev/null

echo "Olusturuldu: $OUT ($(du -h "$OUT" | cut -f1))"
unzip -l "$OUT"
