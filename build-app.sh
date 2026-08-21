#!/bin/bash
# Construit LocalFlow.app : bundle macOS signé (ad hoc) — nom, icône, identité « LocalFlow ».
# Compatible Python Homebrew / python.org (framework) ET python standalone installé par uv.
# Le venv n'est PAS copié : il est fourni via PYTHONPATH (voir install-agent.sh / run.sh).
set -euo pipefail
cd "$(dirname "$0")"
APP=LocalFlow.app
# JAMAIS toucher au bundle pendant qu'il tourne : macOS invalide le processus et son tap clavier
# (clavier/trackpad qui ne répondent plus). On arrête d'abord, on relance à la fin.
WAS_RUNNING=0
if pgrep -f "LocalFlow.app/Contents/MacOS/LocalFlow" >/dev/null 2>&1; then
  WAS_RUNNING=1
  echo "LocalFlow tourne : arrêt le temps de reconstruire le bundle…"
  launchctl bootout "gui/$(id -u)/com.louqui.localflow" 2>/dev/null || true
  pkill -f "LocalFlow.app/Contents/MacOS/LocalFlow" 2>/dev/null || true
  sleep 1
fi

REAL="$(.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.executable))')"
BIN_DIR="$(dirname "$REAL")"
# Framework (Homebrew/python.org) : bin/python3.x est un LANCEUR qui relance Python.app → on copie le vrai.
CANDIDATE="$BIN_DIR/../Resources/Python.app/Contents/MacOS/Python"
STANDALONE=0
if [ -x "$CANDIDATE" ]; then PY="$CANDIDATE"; else PY="$REAL"; STANDALONE=1; fi   # standalone (uv) : le binaire est déjà le vrai
echo "exécutable Python : $PY"
PYHOME="$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')"
PYVER="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PY" "$APP/Contents/MacOS/LocalFlow"
chmod 755 "$APP/Contents/MacOS/LocalFlow"
cp assets/LocalFlow.icns "$APP/Contents/Resources/LocalFlow.icns"
# Python standalone : le binaire charge libpython via @rpath (= ../lib à côté de lui) → on la met dans le bundle.
if [ "$STANDALONE" = 1 ]; then
  mkdir -p "$APP/Contents/lib"
  for dylib in "$PYHOME/lib/libpython$PYVER.dylib" "$PYHOME/lib/libpython${PYVER}t.dylib"; do
    [ -f "$dylib" ] && cp "$dylib" "$APP/Contents/lib/" && echo "libpython copiée : $(basename "$dylib")"
  done
fi
# Helper de capture du son système (réunions). Recompilé si swiftc est là, sinon binaire prébuilt du dépôt.
mkdir -p "$APP/Contents/Helpers"
if command -v swiftc >/dev/null 2>&1 && xcode-select -p >/dev/null 2>&1; then
  (cd helpers/audiotap && ./build.sh >/dev/null 2>&1) && echo "audiotap recompilé" || echo "audiotap : compilation impossible, binaire prébuilt utilisé"
fi
if [ -x helpers/audiotap/audiotap ]; then
  cp helpers/audiotap/audiotap "$APP/Contents/Helpers/audiotap"
  chmod 755 "$APP/Contents/Helpers/audiotap"
else
  echo "⚠️  helpers/audiotap/audiotap manquant : les réunions n'auront que le micro"
fi
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
    <key>NSAudioCaptureUsageDescription</key><string>LocalFlow enregistre le son de tes réunions (Zoom, Meet, Teams…) pour les transcrire, 100 % en local.</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
PLIST
codesign --force --deep --sign - --identifier com.louqui.localflow "$APP"
codesign --verify --deep --strict "$APP"

# Le binaire copié doit retrouver libpython : test réel, avec le même environnement que l'agent.
. ./env.sh
# test strict : SANS variables DYLD (comme si macOS les avait purgées)
if ! env -u DYLD_FALLBACK_LIBRARY_PATH "$APP/Contents/MacOS/LocalFlow" -c "import sys; import localflow, mlx.core, Quartz" 2>/tmp/localflow-build-test.log; then
  echo "❌ Le bundle ne démarre pas :"; cat /tmp/localflow-build-test.log; exit 1
fi
echo "✅ $APP construit, signé et testé"
if [ "$WAS_RUNNING" = 1 ] && [ "${LOCALFLOW_NO_RELAUNCH:-0}" != 1 ]; then
  echo "Relance de LocalFlow… (macOS peut redemander Accessibilité / Micro : le bundle a changé)"
  ./run.sh
fi
