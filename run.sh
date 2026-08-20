#!/bin/bash
# Lance LocalFlow. Si le LaunchAgent est installé, passe par lui (relance auto).
cd "$(dirname "$0")"
AGENT=com.louqui.localflow
if launchctl print "gui/$(id -u)/$AGENT" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/$AGENT" && echo "LocalFlow (re)lancé via LaunchAgent — logs : ~/.localflow*.log"
else
  . ./env.sh
  exec LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.app
fi
