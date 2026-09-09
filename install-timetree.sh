#!/bin/bash
# Prépare l'écriture dans TimeTree : identifiants dans le trousseau, prérequis vérifiés.
# Les identifiants ne sont JAMAIS écrits dans ~/.localflow.json — ce fichier est en clair.
set -e
cd "$(dirname "$0")"

SERVICE="LocalFlow TimeTree"
MCP_DEFAUT="$HOME/Desktop/Projects/TIMETREE/dist/index.js"
MCP="${1:-$MCP_DEFAUT}"

echo "==> Serveur MCP TimeTree"
if [ ! -f "$MCP" ]; then
  echo "❌ introuvable : $MCP"
  echo "   Construis-le d'abord :  cd \"$(dirname "$(dirname "$MCP")")\" && npm ci && npm run build"
  echo "   (ou passe le chemin en argument : ./install-timetree.sh /chemin/vers/dist/index.js)"
  exit 1
fi
echo "   ✅ $MCP"

echo "==> node"
NODE=""
for c in /usr/local/bin/node /opt/homebrew/bin/node /usr/bin/node; do
  [ -x "$c" ] && NODE="$c" && break
done
if [ -z "$NODE" ]; then
  echo "❌ node introuvable. L'agent LocalFlow ne voit que /opt/homebrew/bin, /usr/local/bin,"
  echo "   /usr/bin et /bin : node doit être dans l'un d'eux."
  exit 1
fi
echo "   ✅ $NODE ($("$NODE" --version))"

echo "==> Identifiants TimeTree"
# Sans terminal interactif (lancé depuis un agent, un hook, ou le préfixe « ! »
# de Claude Code), `read` attend une saisie qui n'arrivera jamais : le script
# reste pendu sans rien dire. On le dit tout de suite.
if [ ! -t 0 ]; then
  echo "❌ Ce script doit te poser deux questions : il lui faut un vrai Terminal."
  echo "   Ouvre l'app Terminal (⌘Espace → « Terminal »), puis colle :"
  echo "   cd \"$PWD\" && ./install-timetree.sh"
  exit 1
fi
if security find-generic-password -s "$SERVICE" >/dev/null 2>&1; then
  COMPTE=$(security find-generic-password -s "$SERVICE" 2>&1 | sed -n 's/.*"acct"<blob>="\(.*\)"/\1/p')
  printf "   Déjà dans le trousseau (%s). Les remplacer ? [o/N] " "$COMPTE"
  read -r REPONSE
  case "$REPONSE" in
    o|O|oui|y|Y) ;;
    *) echo "   Inchangés."; SAUTER=1 ;;
  esac
fi

if [ -z "$SAUTER" ]; then
  printf "   Adresse e-mail TimeTree : "
  read -r EMAIL
  printf "   Mot de passe (invisible) : "
  read -rs MDP
  echo
  if [ -z "$EMAIL" ] || [ -z "$MDP" ]; then
    echo "❌ e-mail ou mot de passe vide."
    exit 1
  fi
  # -U remplace l'entrée existante au lieu d'en empiler une seconde.
  security add-generic-password -U -s "$SERVICE" -a "$EMAIL" -w "$MDP" \
    -T /usr/bin/security -D "mot de passe d'application"
  unset MDP
  echo "   ✅ déposés dans le trousseau (service « $SERVICE »)"
fi

echo "==> Réglage de LocalFlow"
PY=.venv/bin/python
if [ -x "$PY" ]; then
  "$PY" - "$MCP" <<'PYEOF'
import sys
from localflow.config import Config
c = Config()
c.data["calendar_mcp_path"] = sys.argv[1]
c.data["calendar_enabled"] = True
c.save()
print(f"   ✅ agenda activé, calendrier {c.calendar_id}")
PYEOF
else
  echo "   ⚠️  venv absent : active « Agenda : fn+⇧ » depuis le menu de la barre."
fi

echo
echo "✅ Prêt. Redémarre l'agent (./run.sh), puis maintiens fn+⇧ en dictant :"
echo "   « mets-moi un événement demain à 14h, déjeuner avec César »"
echo "   L'aperçu s'affiche 3 s en bas de l'écran — Esc annule."
