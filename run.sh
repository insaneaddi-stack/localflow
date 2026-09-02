#!/bin/bash
# Lance LocalFlow. Si le LaunchAgent est installé, passe par lui (relance auto).
cd "$(dirname "$0")"
AGENT=com.louqui.localflow
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
# Une dictée en cours ne survit pas à un kickstart -k : le processus est tué,
# l'audio n'a jamais atteint le modèle et la phrase est perdue. On attend qu'elle
# finisse (60 s au plus, le témoin périme tout seul au-delà de 120 s).
BUSY="$HOME/Library/Caches/LocalFlow/busy"
wait_idle() {
  local i=0
  while [ -f "$BUSY" ] && [ $i -lt 60 ]; do
    age=$(( $(date +%s) - $(cat "$BUSY" 2>/dev/null || echo 0) ))
    [ "$age" -gt 120 ] && break          # témoin oublié par un plantage
    [ $i = 0 ] && echo "dictée en cours — j'attends qu'elle se termine (Ctrl+C pour forcer)…"
    sleep 1; i=$((i+1))
  done
}

if launchctl print "gui/$(id -u)/$AGENT" >/dev/null 2>&1; then
  [ "${1:-}" = "--force" ] || wait_idle
  launchctl kickstart -k "gui/$(id -u)/$AGENT" && echo "LocalFlow (re)lancé via LaunchAgent — logs : ~/.localflow*.log"
elif [ -f "$PLIST" ]; then
  launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "LocalFlow lancé via LaunchAgent — logs : ~/.localflow*.log"
else
  . ./env.sh
  exec LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.app
fi
