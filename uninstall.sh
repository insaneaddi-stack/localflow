#!/bin/bash
# Désinstalle proprement LocalFlow (agent, logs, préférences ; garde le dossier).
LABEL=com.louqui.localflow
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "localflow.app" 2>/dev/null || true
rm -f "$HOME/.localflow.log" "$HOME/.localflow.stdout.log" "$HOME/.localflow.stderr.log" "$HOME/.localflow.lock"
echo "Agent retiré. Préférences : ~/.localflow.json · dictionnaire : ~/.localflow.dict.txt (conservés)."
echo "Pour tout supprimer : rm -rf \"$(cd "$(dirname "$0")" && pwd)\" ~/.localflow.json ~/.localflow.dict.txt"
