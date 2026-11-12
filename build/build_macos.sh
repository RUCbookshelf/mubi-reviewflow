#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$BUILD/dist"
APP_NAME="ReviewFlow"

# F1-9:begin version helpers
# F1-9: VERSION feeds bash arithmetic below; malformed content (an rc suffix,
# an empty file, five components) used to die as an opaque "10#: invalid
# integer constant" or unbound-variable error. The guard rejects anything but
# 2-4 dot-separated numeric components with a readable message; the historical
# four-component hotfix tags (2.0.5.1-2.0.5.3) and short two-component values
# stay accepted - PATCH/HOTFIX default to 0 instead of crashing.
# Apple bundle fields accept at most three components: a four-component input
# keeps its full value in VERSION/ReviewFlowVersion but CFBundleShortVersionString
# gets the first three components, as before.
validate_app_version() {
  if ! [[ "$1" =~ ^[0-9]+\.[0-9]+(\.[0-9]+){0,2}$ ]]; then
    echo "ERROR: VERSION contains '${1:-}'; expected MAJOR.MINOR[.PATCH[.HOTFIX]] with numeric components only (e.g. 2.1.4)" >&2
    return 1
  fi
  # F1-13: the CFBundleVersion encoding below packs PATCH and HOTFIX into one
  # component (PATCH*100 + HOTFIX) and therefore only stays injective while
  # HOTFIX is at most two digits. Past that it collided with a later patch
  # release and the ordering stopped being monotonic: 2.1.4.100 and 2.1.5.0
  # both produced 2.1.500, 2.1.4.1000 and 2.1.14.0 both 2.1.1400. The
  # assumption is a property of the encoding, so it is checked here with a
  # readable message instead of being implicit - deliberately WITHOUT
  # renumbering the historical tags (2.0.5.1-2.0.5.3 must keep mapping to the
  # bundle versions Apple already saw). CFBundleVersion is limited to three
  # period-separated integers, so appending the hotfix as a fourth component
  # is not an option either.
  local comps hotfix
  IFS='.' read -r -a comps <<<"$1"
  hotfix="${comps[3]:-0}"
  if [ "$((10#$hotfix))" -gt 99 ]; then
    echo "ERROR: VERSION '${1}' has HOTFIX ${hotfix}; CFBundleVersion packs PATCH and HOTFIX into one component, so hotfix numbers 0-99 are supported (a four-component macOS bundle version is not allowed)" >&2
    return 1
  fi
}

compute_bundle_versions() {
  validate_app_version "$1" || return 1
  local major minor patch hotfix short build
  IFS='.' read -r major minor patch hotfix <<<"$1"
  patch="${patch:-0}"
  hotfix="${hotfix:-0}"
  # F1-13: PATCH*100+HOTFIX is injective only while HOTFIX does not exceed two
  # digits; validate_app_version above now enforces that bound, so a hotfix
  # release can no longer silently share a CFBundleVersion with a later patch
  # release. The encoding itself is deliberately unchanged, because Apple has
  # already consumed the values of every published tag (2.0.5.1-2.0.5.3).
  build="$major.$minor.$((10#$patch * 100 + 10#$hotfix))"
  short="$1"
  case "$1" in *.*.*.*) short="$major.$minor.$patch" ;; esac
  printf '%s\n%s\n' "$short" "$build"
}
# F1-9:end version helpers

APP_VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
BUNDLE="$(compute_bundle_versions "$APP_VERSION")" || exit 1
BUNDLE_VERSION="${BUNDLE%%$'\n'*}"
BUNDLE_BUILD="${BUNDLE##*$'\n'}"

# F1-3: two builds from the same checkout share $WORK and $DIST (build_macos.sh
# writes into the same $DIST), and one instance's `rm -rf` used to delete the
# other's tree mid-build. `mkdir` is atomic on Linux and macOS alike, so it is
# the whole protocol: acquire = mkdir success, release = the owner's trap.
#
# There is deliberately NO automatic reclaim of an abandoned lock. Every
# automatic-reclaim scheme that inspects the recorded pid is racy: the winner
# has to delete and recreate the directory, and a contender that sampled the
# stale pid just before that recreation will happily "take over" the live lock
# (measured: up to 4 of 4 concurrent builds inside the section). Since mkdir is
# the only primitive that is atomic on both platforms, the correct design is to
# never touch a lock somebody else created - a crashed build leaves the
# directory behind and the next build stops with an actionable message.
# REVIEWFLOW_BUILD_LOCK_FORCE=1 clears it for the user who decided no build is
# running (that decision is exactly the thing the script cannot make safely).
LOCK="$BUILD/.build-dist.lock"
if [ "${REVIEWFLOW_BUILD_LOCK_FORCE:-0}" = "1" ] && [ -d "$LOCK" ]; then
  echo "WARNING: REVIEWFLOW_BUILD_LOCK_FORCE=1 - removing $LOCK without checking whether a build is running." >&2
  rm -rf "$LOCK"
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  holder=""
  [ -f "$LOCK/pid" ] && holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  echo "ERROR: another ReviewFlow packaging build holds $LOCK${holder:+ (pid $holder)}." >&2
  echo "       Stop that build first. If it crashed or the PID is from a previous boot," >&2
  echo "       re-run with REVIEWFLOW_BUILD_LOCK_FORCE=1 once no build is running." >&2
  exit 1
fi
echo $$ >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

cd "$ROOT"
rm -rf "$BUILD/work-macos" "$DIST"
mkdir -p "$BUILD/work-macos" "$DIST"

python3 -m venv "$BUILD/work-macos/venv"
PY="$BUILD/work-macos/venv/bin/python"
# F1-2: bound every tool this script installs directly so the same VERSION tag
# keeps resolving inside a known range (pyinstaller 6.x line, current 6.22).
# Trade-off: requirements-app.txt is NOT hash-locked here - the bounds only pin
# the packages the packaging scripts themselves install.
"$PY" -m pip install --upgrade "pip>=25,<27" "wheel>=0.45,<1"
"$PY" -m pip install -r "$BUILD/requirements-app.txt" "pyinstaller>=6.10,<7"

# MM9-07: a local build runs exactly the same four static validators CI runs
# (release.yml) - previously they ran only in CI, so a locally built DMG shipped
# without any of these gates. Node.js is required for the two JavaScript
# validators, the same hard requirement build_windows.bat already enforces.
# httpx goes to a throwaway --target dir so PyInstaller never freezes it.
"$PY" -m pip install --no-build-isolation --prefer-binary --no-warn-script-location \
  --disable-pip-version-check --target "$BUILD/work-macos/validator_deps" httpx >/dev/null
REVIEWFLOW_VALIDATION_DEPENDENCIES="$BUILD/work-macos/validator_deps" "$PY" "$BUILD/validate_import_review.py"
REVIEWFLOW_VALIDATION_DEPENDENCIES="$BUILD/work-macos/validator_deps" "$PY" "$BUILD/validate_screening_rounds.py"
command -v node >/dev/null 2>&1 || {
  echo "ERROR: node is required to run the JavaScript static validators (install Node.js or add it to PATH)" >&2
  exit 1
}
node "$BUILD/validate_import_review_static.cjs"
node "$BUILD/validate_team_prisma_static.js"

# F1-8/F1-11: every iconset slot now carries its true pixel size. Previously
# icon_512x512.png and icon_256x256@2x.png were both overwritten with the
# 256px source (so the sips 512px upscale was dead work and Finder's large
# icons showed an upscaled-from-256 image), icon_64x64.png - not a legal
# iconset member - was left in the directory iconutil reads, and the required
# icon_512x512@2x.png (1024px) was never produced at all.
ICONSET="$BUILD/work-macos/ReviewFlow.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$BUILD/icon_256.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
done
cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
cp "$ICONSET/icon_64x64.png" "$ICONSET/icon_32x32@2x.png"
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_256x256@2x.png"
sips -z 1024 1024 "$BUILD/icon_256.png" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
rm -f "$ICONSET/icon_64x64.png"
iconutil -c icns "$ICONSET" -o "$BUILD/work-macos/ReviewFlow.icns"

# F1-7: PyInstaller used to take whatever architecture the runner happened to
# have. The release matrix labels this artifact macos-x64 while `macos-14`
# runners are arm64, so the DMG's real architecture was a property of the
# machine, not of the release - and nothing downstream looked at the Mach-O.
# The target is now derived from the HOST explicitly and passed to PyInstaller
# (PyInstaller refuses to cross-build, so a mismatch is caught, not shipped),
# and the frozen executable's architecture is asserted after the build.
# REVIEWFLOW_MACOS_ARCH overrides the host when the label and the runner
# disagree - the operator must then run on a matching machine.
resolve_target_arch() {
  local machine="${1:-}"
  case "$machine" in
    arm64|aarch64) echo "arm64" ;;
    x86_64|amd64|x64) echo "x86_64" ;;
    *) return 1 ;;
  esac
}
TARGET_ARCH="$(resolve_target_arch "${REVIEWFLOW_MACOS_ARCH:-$(uname -m)}")" || {
  echo "ERROR: unsupported machine '${REVIEWFLOW_MACOS_ARCH:-$(uname -m)}' for a macOS build (expected arm64 or x86_64)" >&2
  exit 1
}

"$PY" -m PyInstaller \
  --noconfirm --clean --onedir --windowed --name "$APP_NAME" \
  --target-architecture "$TARGET_ARCH" \
  --icon "$BUILD/work-macos/ReviewFlow.icns" \
  --add-data "$ROOT/custom_backend:custom_backend" \
  --add-data "$ROOT/custom_frontend:custom_frontend" \
  --add-data "$ROOT/VERSION:." \
  --add-data "$ROOT/coscreen/assets:coscreen/assets" \
  --collect-all scipy --collect-all sklearn --collect-all statsmodels \
  --collect-all pandas --collect-data babel \
  --hidden-import custom_backend.auth_recovery \
  "$BUILD/launcher.py" --distpath "$DIST" --workpath "$BUILD/work-macos/pyinstaller"

APP="$DIST/$APP_NAME.app"
# F1-7: assert the artifact really is the architecture the release claims. The
# DMG is named/uploaded from the matrix label, so a silently different slice
# would be published under the wrong platform name.
FROZEN_EXE="$APP/Contents/MacOS/$APP_NAME"
if [ ! -f "$FROZEN_EXE" ]; then
  echo "ERROR: the frozen executable is missing: $FROZEN_EXE" >&2
  exit 1
fi
FROZEN_ARCHS="$(lipo -archs "$FROZEN_EXE" 2>/dev/null || true)"
case " $FROZEN_ARCHS " in
  *" $TARGET_ARCH "*) : ;;
  *)
    echo "ERROR: the built app is '${FROZEN_ARCHS:-unknown}', expected '$TARGET_ARCH' (set REVIEWFLOW_MACOS_ARCH to the architecture of this machine)" >&2
    exit 1
    ;;
esac
echo "built architecture: $TARGET_ARCH"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier edu.ruc.bookshelf.reviewflow" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $BUNDLE_VERSION" "$APP/Contents/Info.plist" || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $BUNDLE_VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUNDLE_BUILD" "$APP/Contents/Info.plist" || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $BUNDLE_BUILD" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :ReviewFlowVersion string $APP_VERSION" "$APP/Contents/Info.plist"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/THIRD_PARTY_NOTICES.md"
hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "$DIST/$APP_NAME.dmg"
# F1-12: record the bare artifact name so SHA256SUMS.txt stays portable - the
# previous absolute-path entry could only be re-verified on the build machine.
( cd "$DIST" && shasum -a 256 "$APP_NAME.dmg" > SHA256SUMS.txt )
# MM9-07 (cont.): verify the finished payload tree against the checked-out
# sources with the same gate CI runs on the built artifact.
"$PY" "$BUILD/verify_release_payload.py" macos
