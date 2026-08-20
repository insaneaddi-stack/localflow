#!/bin/bash
# Installe LocalFlow comme agent de session : démarre à l'ouverture, relance si plantage.
# Le plist est généré ici (aucun chemin en dur dans le dépôt).
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
LABEL=com.louqui.localflow
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
. ./env.sh
pkill -f "localflow.app" 2>/dev/null || true
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/LocalFlow.app/Contents/MacOS/LocalFlow</string>
        <string>-m</string>
        <string>localflow.app</string>
    </array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>StandardOutPath</key><string>$HOME/.localflow.stdout.log</string>
    <key>StandardErrorPath</key><string>$HOME/.localflow.stderr.log</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HF_HUB_OFFLINE</key><string>1</string>
        <key>PYTHONPATH</key><string>$PYTHONPATH</string>
        <key>DYLD_FALLBACK_LIBRARY_PATH</key><string>$DYLD_FALLBACK_LIBRARY_PATH</string>
        <key>PYTHONHOME</key><string>$PYTHONHOME</string>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 1
for i in 1 2 3 4 5; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
  sleep 2
done
echo "✅ LocalFlow installé comme agent (démarre à la session, relance auto)."
