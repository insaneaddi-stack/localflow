#!/bin/bash
# Lance LocalFlow. Si le LaunchAgent est installé, passe par lui (relance auto).
cd "$(dirname "$0")"
AGENT=com.louqui.localflow
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
if launchctl print "gui/$(id -u)/$AGENT" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/$AGENT" && echo "LocalFlow (re)lancé via LaunchAgent — logs : ~/.localflow*.log"
elif [ -f "$PLIST" ]; then
  launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "LocalFlow lancé via LaunchAgent — logs : ~/.localflow*.log"
else
  . ./env.sh
  exec LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.app
fi
