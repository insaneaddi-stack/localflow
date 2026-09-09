#!/bin/bash
# Désinstalle proprement LocalFlow (agent, logs, préférences ; garde le dossier).
LABEL=com.louqui.localflow
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "localflow.app" 2>/dev/null || true
rm -f "$HOME/.localflow.log" "$HOME/.localflow.stdout.log" "$HOME/.localflow.stderr.log" "$HOME/.localflow.lock"
# Le mot de passe TimeTree part avec l'app : un identifiant ne doit jamais
# survivre au logiciel qui l'a demandé.
security delete-generic-password -s "LocalFlow TimeTree" >/dev/null 2>&1 \
  && echo "Identifiants TimeTree retirés du trousseau." || true
echo "Agent retiré. Préférences : ~/.localflow.json · dictionnaire : ~/.localflow.dict.txt (conservés)."
echo "Pour tout supprimer : rm -rf \"$(cd "$(dirname "$0")" && pwd)\" ~/.localflow.json ~/.localflow.dict.txt ~/.localflow.calendar-failed.jsonl"
