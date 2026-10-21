# cloudstream-mac

## Brief Introduction and Purpose

Mac-side helpers for running Android streaming apps (CloudStream, Nuvio/Nexora) in the Android emulator on an Apple Silicon Mac. It was written to make watching on the Mac a one-click affair: two small AppleScript `.app` launchers start (or bring to front) an emulator AVD, and `tr-livetv-addon/` is a dependency-free Node server that publishes the iptv-org Turkey channel list as a Stremio addon for Nuvio.

## Quick Install

Requirements: Homebrew `openjdk@17` and Android command-line tools (emulator, platform-tools) at `/opt/homebrew/share/android-commandlinetools`, plus AVDs named `cloudstream_tv` and `nexora_phone`. The APKs are not tracked; install them into the AVD with `adb install <file>.apk`.

```sh
git clone -b cloudstream-mac https://github.com/drascom/micro_services.git
cd micro_services/cloudstream-mac
open "CloudStream TV.app"     # or ./run-emulator.sh ; nexora.app / ./run-tablet.sh
```

Live TV addon (Node.js, no dependencies):

```sh
tr-livetv-addon/start.sh      # PORT=7788 by default
# in the emulator: http://10.0.2.2:7788/manifest.json
```

## Developing Mode

The launchers' sources are `launchers/*.applescript`; they look for `run-*.sh` next to the `.app` bundle. After editing, rebuild and re-sign:

```sh
cd cloudstream-mac
osacompile -o "CloudStream TV.app/Contents/Resources/Scripts/main.scpt" launchers/cloudstream-tv.applescript
osacompile -o nexora.app/Contents/Resources/Scripts/main.scpt launchers/nexora.applescript
codesign -f -s - "CloudStream TV.app" nexora.app
```

Addon settings (`PORT`, `NODE`) are listed in `.env.example`.

## Source Citations

- [Emulator launcher](cloudstream-mac/run-emulator.sh), [tablet launcher](cloudstream-mac/run-tablet.sh)
- [Addon README](cloudstream-mac/tr-livetv-addon/README.md) and [server](cloudstream-mac/tr-livetv-addon/server.js)
- Channel list: https://iptv-org.github.io/iptv/countries/tr.m3u
