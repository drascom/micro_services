#!/usr/bin/env bash
# tools/release.sh [--dry-run] <X.Y.Z> - diziflix release: surum yaz -> Tizen wgt + imzali Android APK -> GitHub release.
#   Surum kaynagi client/version.json; build-wgt.sh config.xml + js/version.js'i, gradle versionName/versionCode'u ondan alir.
#   Ciktilar: dist/diziflix-<ver>.wgt, dist/diziflix-<ver>.apk, dist/SHA256SUMS
#   --dry-run: yalniz build (surum dosyalari sonra eski haline doner; commit/tag/push/release YOK).
#   Gercek mod: temiz agac + `diziflix` dali + gh oturumu + release keystore (~/.diziflix) sart; commit "diziflix: release v<ver>",
#   etiket diziflix-v<ver>, yalniz dal + o etiket itilir, `gh release create` (notlar: son release'ten beri client/android/server).
#   Android istemcisi bu release'i kendisi indirip kurar (APK + SHA256SUMS), Tizen yalniz bildirir.
set -euo pipefail

DRY=0
VER=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) VER="$a" ;;
  esac
done
die() { echo "HATA: $*" >&2; exit 1; }
[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "kullanim: tools/release.sh [--dry-run] X.Y.Z"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAG="diziflix-v$VER"
REPO="drascom/micro_services"
VFILES=(client/version.json client/config.xml client/js/version.js)

# --- on kontroller ---
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
DIRTY="$(git status --porcelain)"
if [ "$DRY" = 1 ]; then
  [ "$BRANCH" = diziflix ] || echo "UYARI: dal '$BRANCH' (gercek modda 'diziflix' sart)"
  [ -z "$DIRTY" ] || echo "UYARI: calisma agaci temiz degil (gercek modda sart)"
else
  [ "$BRANCH" = diziflix ] || die "dal 'diziflix' degil: $BRANCH"
  [ -z "$DIRTY" ] || die "calisma agaci temiz degil (git status)"
  command -v gh >/dev/null || die "gh (GitHub CLI) kurulu degil"
  gh auth status >/dev/null 2>&1 || die "gh oturumu yok: gh auth login"
  [ -f "$HOME/.diziflix/keystore.properties" ] || die "release keystore yok: ~/.diziflix/keystore.properties (android/README.md)"
  git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "etiket zaten var: $TAG"
  git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 && die "etiket uzakta zaten var: $TAG"
fi

# --- dry-run: surum dosyalarini sonra geri yukle ---
BK=""
if [ "$DRY" = 1 ]; then
  BK="$(mktemp -d)"
  for f in "${VFILES[@]}"; do mkdir -p "$BK/$(dirname "$f")"; cp "$f" "$BK/$f"; done
  trap 'for f in "${VFILES[@]}"; do cp "$BK/$f" "$f"; done; rm -rf "$BK"' EXIT
fi

# --- surum yaz + build ---
printf '{"version": "%s"}\n' "$VER" > client/version.json
bash client/build-wgt.sh >/dev/null
mkdir -p dist
cp dist/diziflix.wgt "dist/diziflix-$VER.wgt"

if [ -z "${JAVA_HOME:-}" ]; then
  for j in /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home /usr/lib/jvm/java-17-openjdk-amd64; do
    [ -d "$j" ] && { export JAVA_HOME="$j"; break; }
  done
fi
(cd android && ./gradlew -q assembleRelease)
APK_DIR=android/app/build/outputs/apk/release
if [ -f "$APK_DIR/app-release.apk" ]; then
  cp "$APK_DIR/app-release.apk" "dist/diziflix-$VER.apk"
elif [ -f "$APK_DIR/app-release-unsigned.apk" ]; then
  [ "$DRY" = 1 ] || die "APK imzasiz (keystore okunamadi)"
  echo "UYARI: APK imzasiz (keystore yok) -> dist/diziflix-$VER.apk otomatik guncellemede kurulamaz"
  cp "$APK_DIR/app-release-unsigned.apk" "dist/diziflix-$VER.apk"
else
  die "release APK bulunamadi ($APK_DIR)"
fi
(cd dist && shasum -a 256 "diziflix-$VER.wgt" "diziflix-$VER.apk" > SHA256SUMS)
echo "Ciktilar:"
ls -l "dist/diziflix-$VER.wgt" "dist/diziflix-$VER.apk" dist/SHA256SUMS | awk '{print "  " $5 "\t" $9}'

if [ "$DRY" = 1 ]; then
  echo "dry-run: commit/tag/push/release yapilmadi (surum dosyalari geri alindi)."
  exit 0
fi

# --- commit + etiket + push ---
PREV="$(git describe --tags --abbrev=0 --match 'diziflix-v*' 2>/dev/null || true)"
git add "${VFILES[@]}"
git commit -q -m "diziflix: release v$VER" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git tag "$TAG"
git push origin diziflix "$TAG"

# --- release notlari: son release'ten beri (yoksa son 30 commit) ---
NOTES="$(mktemp)"
{
  echo "diziflix $VER"
  echo
  echo "Android: uygulama bu surumu kendisi indirip kurar. Tizen: diziflix-$VER.wgt'yi bilgisayardan kur."
  echo
  echo "Degisiklikler${PREV:+ ($PREV sonrasi)}:"
  if [ -n "$PREV" ]; then git log --oneline "$PREV..HEAD" -- client android server; else git log --oneline -30 -- client android server; fi | sed 's/^/- /'
} > "$NOTES"
gh release create "$TAG" --repo "$REPO" --target diziflix --title "diziflix $VER" --notes-file "$NOTES" \
  "dist/diziflix-$VER.wgt" "dist/diziflix-$VER.apk" dist/SHA256SUMS
rm -f "$NOTES"
echo "Release: https://github.com/$REPO/releases/tag/$TAG"
