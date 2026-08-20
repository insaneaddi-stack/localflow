#!/bin/bash
# Construit LocalFlow.app : bundle macOS signé (ad hoc) — nom, icône, identité « LocalFlow ».
# Le venv n'est PAS copié : il est fourni via PYTHONPATH (voir plist / run.sh).
set -euo pipefail
cd "$(dirname "$0")"
APP=LocalFlow.app
PY="$(dirname "$(readlink -f .venv/bin/python)")/../Resources/Python.app/Contents/MacOS/Python"  # le VRAI exécutable
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PY" "$APP/Contents/MacOS/LocalFlow"
cp assets/LocalFlow.icns "$APP/Contents/Resources/LocalFlow.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>CFBundleName</key><string>LocalFlow</string>
    <key>CFBundleDisplayName</key><string>LocalFlow</string>
    <key>CFBundleIdentifier</key><string>com.louqui.localflow</string>
    <key>CFBundleVersion</key><string>2.0</string>
    <key>CFBundleShortVersionString</key><string>2.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>LocalFlow</string>
    <key>CFBundleIconFile</key><string>LocalFlow</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>LocalFlow écoute ta voix pour la dicter, 100 % en local.</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict></plist>
PLIST
codesign --force --deep --sign - --identifier com.louqui.localflow "$APP"
codesign --verify --deep --strict "$APP"
echo "✅ $APP construit et signé"
