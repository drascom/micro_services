#!/usr/bin/env bash
# Builds GemmaServer.app (menu-bar app to start/stop the llama.cpp Gemma server).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/work/GEMMA/GemmaServer.app"

echo "Building $APP …"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -swift-version 5 -O \
  -o "$APP/Contents/MacOS/GemmaServer" \
  "$HERE/GemmaServer.swift" \
  -framework AppKit

# --- Icons ---------------------------------------------------------------
# Render the lightning+G artwork (writes /tmp/gemma-icon-1024.png and menubar.png).
swiftc -swift-version 5 -o /tmp/render-icon "$HERE/render-icon.swift" -framework AppKit
/tmp/render-icon "$HERE"

# Build AppIcon.icns from the 1024 master.
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz       /tmp/gemma-icon-1024.png --out "$ICONSET/icon_${sz}x${sz}.png"      >/dev/null
  sips -z $((sz*2)) $((sz*2)) /tmp/gemma-icon-1024.png --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# Menu-bar glyph (template image, tinted at runtime by server state).
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

# Ad-hoc code signature so macOS lets it run without an Xcode identity.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "Done: $APP"
