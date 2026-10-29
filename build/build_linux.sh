#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
WORK="$BUILD/work-linux"
DIST="$BUILD/dist"
APPDIR="$WORK/AppDir"

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
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL="$WORK/appimagetool-x86_64.AppImage"
curl -fL --retry 3 "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
printf '%s  %s\n' "$APPIMAGETOOL_SHA256" "$APPIMAGETOOL" | sha256sum --check --strict -
chmod +x "$APPIMAGETOOL"
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$DIST/ReviewFlow-x86_64.AppImage"
chmod +x "$DIST/ReviewFlow-x86_64.AppImage"
sha256sum "$DIST/ReviewFlow-x86_64.AppImage" > "$DIST/SHA256SUMS.txt"
