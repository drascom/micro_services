#!/usr/bin/env bash
# Builds GemmaServer.app — the menu-bar app that starts/stops the Gemma server.
# Output goes next to the project (gemma/GemmaServer.app). Run via ../install.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # gemma/app
PROJ="$(cd "$HERE/.." && pwd)"                          # gemma
APP="$PROJ/GemmaServer.app"

# Load install-time config (paths/port/ctx) if present; otherwise use defaults.
LLAMA_SERVER="$PROJ/llama.cpp-mainline/build/bin/llama-server"
MODEL="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/gemma-4-12b-it-qat-q4_0.gguf"
MMPROJ="$HOME/.lmstudio/models/google/gemma-4-12B-it-qat-q4_0-gguf/mmproj-gemma-4-12b-it-qat-q4_0.gguf"
HOST="0.0.0.0"; PORT=8080; CTX=32768
[ -f "$PROJ/config.env" ] && source "$PROJ/config.env"

echo "Building $APP …"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -swift-version 5 -O \
  -o "$APP/Contents/MacOS/GemmaServer" \
  "$HERE/GemmaServer.swift" \
  -framework AppKit

# Bake resolved config into the bundle so the app knows where everything is.
cat > "$APP/Contents/Resources/config.json" <<JSON
{
  "server": "$LLAMA_SERVER",
  "model":  "$MODEL",
  "mmproj": "$MMPROJ",
  "host":   "$HOST",
  "port":   $PORT,
  "ctx":    $CTX
}
JSON

# --- Icons ---------------------------------------------------------------
swiftc -swift-version 5 -o /tmp/render-icon "$HERE/render-icon.swift" -framework AppKit
/tmp/render-icon "$HERE"

ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz             /tmp/gemma-icon-1024.png --out "$ICONSET/icon_${sz}x${sz}.png"    >/dev/null
  sips -z $((sz*2)) $((sz*2)) /tmp/gemma-icon-1024.png --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
cp "$HERE/menubar.png" "$APP/Contents/Resources/menubar.png"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>GemmaServer</string>
  <key>CFBundleDisplayName</key>     <string>Gemma Server</string>
  <key>CFBundleIdentifier</key>      <string>com.drascom.gemmaserver</string>
  <key>CFBundleExecutable</key>      <string>GemmaServer</string>
  <key>CFBundleIconFile</key>        <string>AppIcon</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSUIElement</key>            <true/>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "Done: $APP"
