#!/bin/bash
# CloudStream Android TV emulator launcher
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$PATH"

AVD=cloudstream_tv

exec "$ANDROID_HOME/emulator/emulator" -avd "$AVD" \
  -gpu host \
  -memory 4096 \
  -no-boot-anim \
  "$@"
