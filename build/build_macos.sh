#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$BUILD/dist"
APP_NAME="ReviewFlow"

cd "$ROOT"
rm -rf "$BUILD/work-macos" "$DIST"
mkdir -p "$BUILD/work-macos" "$DIST"

python3 -m venv "$BUILD/work-macos/venv"
PY="$BUILD/work-macos/venv/bin/python"
"$PY" -m pip install --upgrade pip wheel
"$PY" -m pip install -r "$BUILD/requirements-app.txt" pyinstaller

ICONSET="$BUILD/work-macos/ReviewFlow.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$BUILD/icon_256.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
done
cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
cp "$ICONSET/icon_64x64.png" "$ICONSET/icon_32x32@2x.png"
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_256x256@2x.png"
cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_512x512.png"
iconutil -c icns "$ICONSET" -o "$BUILD/work-macos/ReviewFlow.icns"

"$PY" -m PyInstaller \
  --noconfirm --clean --onedir --windowed --name "$APP_NAME" \
  --icon "$BUILD/work-macos/ReviewFlow.icns" \
  --add-data "$ROOT/custom_backend:custom_backend" \
  --add-data "$ROOT/custom_frontend:custom_frontend" \
  --add-data "$ROOT/coscreen/assets:coscreen/assets" \
  --collect-all scipy --collect-all sklearn --collect-all statsmodels \
  --collect-all pandas --collect-data babel \
  "$BUILD/launcher.py" --distpath "$DIST" --workpath "$BUILD/work-macos/pyinstaller"

APP="$DIST/$APP_NAME.app"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier edu.ruc.bookshelf.reviewflow" "$APP/Contents/Info.plist"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/THIRD_PARTY_NOTICES.md"
hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "$DIST/$APP_NAME.dmg"
shasum -a 256 "$DIST/$APP_NAME.dmg" > "$DIST/SHA256SUMS.txt"
