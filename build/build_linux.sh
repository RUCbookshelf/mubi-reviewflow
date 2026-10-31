#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
WORK="$BUILD/work-linux"
DIST="$BUILD/dist"
APPDIR="$WORK/AppDir"

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
rm -rf "$WORK" "$DIST"
mkdir -p "$WORK" "$DIST" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

python3 -m venv "$WORK/venv"
PY="$WORK/venv/bin/python"
# F1-2: bound every tool this script installs directly so the same VERSION tag
# keeps resolving inside a known range (pyinstaller 6.x line, current 6.22).
# Trade-off: requirements-app.txt is NOT hash-locked here - the bounds only pin
# the packages the packaging scripts themselves install; the Windows build's
# runtime-lock.txt stays a post-install freeze, not a build constraint.
"$PY" -m pip install --upgrade "pip>=25,<27" "wheel>=0.45,<1"
"$PY" -m pip install -r "$BUILD/requirements-app.txt" "pyinstaller>=6.10,<7"

# MM9-07: a local build runs exactly the same four static validators CI runs
# (release.yml) - previously they ran only in CI, so a locally built AppImage
# shipped without any of these gates. Node.js is required for the two
# JavaScript validators, the same hard requirement build_windows.bat already
# enforces. httpx (used by the two Python validators) goes to a throwaway
# --target dir, mirroring build_windows.bat's validator_deps handling, so it
# is never frozen into the application by PyInstaller below.
"$PY" -m pip install --no-build-isolation --prefer-binary --no-warn-script-location \
  --disable-pip-version-check --target "$WORK/validator_deps" httpx >/dev/null
REVIEWFLOW_VALIDATION_DEPENDENCIES="$WORK/validator_deps" "$PY" "$BUILD/validate_import_review.py"
REVIEWFLOW_VALIDATION_DEPENDENCIES="$WORK/validator_deps" "$PY" "$BUILD/validate_screening_rounds.py"
command -v node >/dev/null 2>&1 || {
  echo "ERROR: node is required to run the JavaScript static validators (install Node.js or add it to PATH)" >&2
  exit 1
}
node "$BUILD/validate_import_review_static.cjs"
node "$BUILD/validate_team_prisma_static.js"

"$PY" -m PyInstaller \
  --noconfirm --clean --onedir --name ReviewFlow \
  --add-data "$ROOT/custom_backend:custom_backend" \
  --add-data "$ROOT/custom_frontend:custom_frontend" \
  --add-data "$ROOT/VERSION:." \
  --add-data "$ROOT/coscreen/assets:coscreen/assets" \
  --collect-all scipy --collect-all sklearn --collect-all statsmodels \
  --collect-all pandas --collect-data babel \
  "$BUILD/launcher.py" --distpath "$WORK/dist" --workpath "$WORK/pyinstaller"

cp -a "$WORK/dist/ReviewFlow/." "$APPDIR/usr/bin/"
cp "$ROOT/build/icon_256.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/reviewflow.png"
cp "$ROOT/build/icon_256.png" "$APPDIR/.DirIcon"
cp "$ROOT/build/icon_256.png" "$APPDIR/reviewflow.png"
cp "$ROOT/LICENSE" "$APPDIR/LICENSE"
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$APPDIR/THIRD_PARTY_NOTICES.md"
cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/ReviewFlow" "$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/reviewflow.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=ReviewFlow
Comment=Systematic review and meta-analysis workspace
Exec=AppRun
Icon=reviewflow
Categories=Office;Science;
Terminal=false
EOF
cp "$APPDIR/reviewflow.desktop" "$APPDIR/usr/share/applications/reviewflow.desktop"

# F1-1: fetch appimagetool from an immutable tagged release - the AppImageKit
# "continuous" channel previously used here is marked obsolete upstream ("DO NOT
# USE") - and verify the exact sha256 (the digest GitHub publishes for this
# release asset) BEFORE executing anything. A mismatch aborts the build via
# set -e before the downloaded file is ever run.
# F1-4/F1-6: the tool architecture is parameterized (the same 1.9.1 release
# publishes x86_64 and aarch64 builds with pinned digests; x86_64 stays the
# default so existing callers are unaffected), and APPIMAGE_EXTRACT_AND_RUN=1
# makes the Type-2 AppImage extract itself instead of dlopen-ing libfuse.so.2,
# so the build also works on hosts/containers without FUSE2.
APPIMAGE_ARCH="${APPIMAGE_ARCH:-x86_64}"
case "$APPIMAGE_ARCH" in
  x86_64)
    APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
    APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
    ;;
  aarch64)
    APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-aarch64.AppImage"
    APPIMAGETOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158"
    ;;
  *)
    echo "ERROR: unsupported APPIMAGE_ARCH '$APPIMAGE_ARCH' (supported: x86_64, aarch64)" >&2
    exit 1
    ;;
esac
APPIMAGETOOL="$WORK/appimagetool-${APPIMAGE_ARCH}.AppImage"
curl -fL --retry 3 "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
printf '%s  %s\n' "$APPIMAGETOOL_SHA256" "$APPIMAGETOOL" | sha256sum --check --strict -
chmod +x "$APPIMAGETOOL"
ARCH="$APPIMAGE_ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APPDIR" "$DIST/ReviewFlow-${APPIMAGE_ARCH}.AppImage"
chmod +x "$DIST/ReviewFlow-${APPIMAGE_ARCH}.AppImage"
# F1-5: record the bare artifact name so SHA256SUMS.txt stays portable - the
# previous absolute-path entry could only be re-verified on the build machine
# (renaming/moving $DIST already broke `sha256sum -c`).
( cd "$DIST" && sha256sum "ReviewFlow-${APPIMAGE_ARCH}.AppImage" > SHA256SUMS.txt )
# MM9-07 (cont.): verify the finished payload tree against the checked-out
# sources with the same gate CI runs on the built artifact.
"$PY" "$BUILD/verify_release_payload.py" linux
