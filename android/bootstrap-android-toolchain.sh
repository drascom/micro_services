#!/usr/bin/env bash
#
# Diziflix Android — derleme araçlarını macOS'ta (Homebrew ile) kurar ve Gradle wrapper üretir.
#
# Kurulanlar: OpenJDK 17, Gradle (yalnızca wrapper üretmek için), Android command-line tools,
#             platform-tools, Android SDK 34 platformu + build-tools 34.0.0.
# Sonunda: android/gradlew, android/gradle/wrapper/gradle-wrapper.jar, android/local.properties.
#
# Kullanım (android/ dizininde):
#   ./bootstrap-android-toolchain.sh           # yalnızca kurulum
#   ./bootstrap-android-toolchain.sh --build   # kurulum + birim testleri + debug APK derleme
#
# Betik yeniden çalıştırılabilir (idempotent): kurulu olanı atlar.
set -euo pipefail

# ---- 0. Ayarlar ---------------------------------------------------------------------------
GRADLE_VERSION="8.7"            # AGP 8.5.2 için gereken en düşük sürüm (gradle-wrapper.properties ile aynı)
ANDROID_PLATFORM="android-34"   # compileSdk / targetSdk 34
ANDROID_BUILD_TOOLS="34.0.0"
DO_BUILD=0
[[ "${1:-}" == "--build" ]] && DO_BUILD=1

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31mHATA: %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "Bu betik yalnızca macOS içindir."
command -v brew >/dev/null 2>&1 || die "Homebrew bulunamadı. Önce kurun: https://brew.sh"
BREW_PREFIX="$(brew --prefix)"

# ---- 1. JDK 17 ----------------------------------------------------------------------------
step "1/6 OpenJDK 17"
if ! brew list --formula openjdk@17 >/dev/null 2>&1; then
  brew install openjdk@17
else
  info "openjdk@17 zaten kurulu"
fi
# openjdk@17 'keg-only': sistem JVM listesine görünsün diye /Library/Java/JavaVirtualMachines'e bağla.
JDK17_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
[[ -x "$JDK17_HOME/bin/java" ]] || die "JDK 17 bulunamadı: $JDK17_HOME"
if [[ ! -e /Library/Java/JavaVirtualMachines/openjdk-17.jdk ]]; then
  info "İsteğe bağlı: sistem JVM listesine bağlamak için (parola ister) şunu çalıştırabilirsiniz:"
  info "  sudo ln -sfn \"$(brew --prefix openjdk@17)/libexec/openjdk.jdk\" /Library/Java/JavaVirtualMachines/openjdk-17.jdk"
fi
export JAVA_HOME="$JDK17_HOME"
export PATH="$JAVA_HOME/bin:$PATH"
info "JAVA_HOME=$JAVA_HOME"
java -version 2>&1 | sed 's/^/    /'

# ---- 2. Gradle (yalnızca wrapper üretmek için) -------------------------------------------
step "2/6 Gradle"
if ! command -v gradle >/dev/null 2>&1; then
  brew install gradle
else
  info "gradle zaten kurulu: $(command -v gradle)"
fi

# ---- 3. Android command-line tools --------------------------------------------------------
step "3/6 Android command-line tools"
if ! brew list --cask android-commandlinetools >/dev/null 2>&1; then
  brew install --cask android-commandlinetools
else
  info "android-commandlinetools zaten kurulu"
fi
# Cask SDK kökünü $(brew --prefix)/share/android-commandlinetools altına koyar.
export ANDROID_HOME="${ANDROID_HOME:-$BREW_PREFIX/share/android-commandlinetools}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
SDKMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
if [[ ! -x "$SDKMANAGER" ]]; then
  # Bazı sürümlerde sdkmanager PATH'e brew tarafından konur.
  SDKMANAGER="$(command -v sdkmanager || true)"
fi
[[ -n "$SDKMANAGER" && -x "$SDKMANAGER" ]] || die "sdkmanager bulunamadı (ANDROID_HOME=$ANDROID_HOME)"
info "ANDROID_HOME=$ANDROID_HOME"
info "sdkmanager=$SDKMANAGER"

# ---- 4. SDK paketleri ---------------------------------------------------------------------
step "4/6 SDK lisansları ve paketleri ($ANDROID_PLATFORM, build-tools $ANDROID_BUILD_TOOLS, platform-tools)"
# 'yes' SIGPIPE ile biter; pipefail'e takılmasın diye alt kabukta hata yoksayılır.
set +o pipefail
yes | "$SDKMANAGER" --sdk_root="$ANDROID_HOME" --licenses >/dev/null
set -o pipefail
"$SDKMANAGER" --sdk_root="$ANDROID_HOME" \
  "platform-tools" \
  "platforms;$ANDROID_PLATFORM" \
  "build-tools;$ANDROID_BUILD_TOOLS"

# ---- 5. Rosetta (Apple Silicon) -----------------------------------------------------------
step "5/6 Rosetta 2 denetimi (AGP'nin aapt2 ikilisi x86_64 olabilir)"
if [[ "$(uname -m)" == "arm64" ]]; then
  if /usr/bin/pgrep -q oahd 2>/dev/null; then
    info "Rosetta 2 zaten kurulu"
  else
    info "Rosetta 2 kuruluyor"
    softwareupdate --install-rosetta --agree-to-license || info "Rosetta kurulamadı; gerekirse elle kurun."
  fi
else
  info "Intel Mac: gerekmiyor"
fi

# ---- 6. local.properties + Gradle wrapper -------------------------------------------------
step "6/6 local.properties ve Gradle wrapper"
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$PROJECT_DIR/local.properties"
info "local.properties yazıldı (sdk.dir=$ANDROID_HOME)"

# Wrapper, proje DIŞINDA boş bir klasörde üretilir: brew'in gradle'ı (Gradle 9.x olabilir) bu projenin
# AGP 8.5.2 eklentisini yüklemeye çalışıp hata vermesin. Sonra dosyalar projeye kopyalanır.
if [[ -f "$PROJECT_DIR/gradle/wrapper/gradle-wrapper.jar" && -x "$PROJECT_DIR/gradlew" ]]; then
  info "gradlew ve gradle-wrapper.jar zaten var"
else
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  touch "$TMP_DIR/settings.gradle"
  ( cd "$TMP_DIR" && gradle wrapper --gradle-version "$GRADLE_VERSION" --distribution-type bin )
  mkdir -p "$PROJECT_DIR/gradle/wrapper"
  cp "$TMP_DIR/gradlew" "$TMP_DIR/gradlew.bat" "$PROJECT_DIR/"
  cp "$TMP_DIR/gradle/wrapper/gradle-wrapper.jar" "$PROJECT_DIR/gradle/wrapper/"
  # gradle-wrapper.properties projede zaten var (sürüm 8.7); wrapper'ın ürettiğiyle aynı sürümü içerir.
  cp "$TMP_DIR/gradle/wrapper/gradle-wrapper.properties" "$PROJECT_DIR/gradle/wrapper/gradle-wrapper.properties"
  chmod +x "$PROJECT_DIR/gradlew"
  info "gradlew, gradlew.bat ve gradle/wrapper/gradle-wrapper.jar üretildi"
fi

cat <<EOF

Kurulum tamam. Yeni bir terminalde aşağıdaki değişkenleri ayarlayın (ya da ~/.zshrc'ye ekleyin):

  export JAVA_HOME="$JDK17_HOME"
  export ANDROID_HOME="$ANDROID_HOME"
  export PATH="\$JAVA_HOME/bin:\$ANDROID_HOME/platform-tools:\$PATH"

Derleme komutları (android/ dizininde):

  ./gradlew :app:testDebugUnitTest     # JVM birim testleri
  ./gradlew :app:assembleDebug         # app/build/outputs/apk/debug/app-debug.apk

Telefona kurulum (USB hata ayıklama açık ya da kablosuz eşleştirilmiş):

  adb install -r app/build/outputs/apk/debug/app-debug.apk

EOF

if [[ "$DO_BUILD" == "1" ]]; then
  step "Birim testleri + debug APK derleniyor (ilk çalıştırma bağımlılıkları indirir, birkaç dakika sürebilir)"
  "$PROJECT_DIR/gradlew" --no-daemon :app:testDebugUnitTest :app:assembleDebug
  info "APK: $PROJECT_DIR/app/build/outputs/apk/debug/app-debug.apk"
fi
