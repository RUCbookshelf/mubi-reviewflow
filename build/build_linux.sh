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
"$PY" -m pip install --upgrade pip wheel
"$PY" -m pip install -r "$BUILD/requirements-app.txt" pyinstaller
"$PY" -m PyInstaller \
  --noconfirm --clean --onedir --name ReviewFlow \
  --add-data "$ROOT/custom_backend:custom_backend" \
  --add-data "$ROOT/custom_frontend:custom_frontend" \
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

APPIMAGETOOL="$WORK/appimagetool-x86_64.AppImage"
curl -fL --retry 3 "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" -o "$APPIMAGETOOL"
chmod +x "$APPIMAGETOOL"
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$DIST/ReviewFlow-x86_64.AppImage"
chmod +x "$DIST/ReviewFlow-x86_64.AppImage"
sha256sum "$DIST/ReviewFlow-x86_64.AppImage" > "$DIST/SHA256SUMS.txt"
