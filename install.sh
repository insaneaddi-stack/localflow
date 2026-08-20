#!/bin/bash
# ╭──────────────────────────────────────────────────────────────╮
# │  LocalFlow — assistant d'installation (aucune connaissance    │
# │  requise). À coller dans le Terminal :                        │
# │  curl -fsSL https://raw.githubusercontent.com/insaneaddi-stack/localflow/main/install.sh | bash
# ╰──────────────────────────────────────────────────────────────╯
set -euo pipefail
REPO="${LOCALFLOW_REPO:-insaneaddi-stack/localflow}"
DEST="${LOCALFLOW_DIR:-$HOME/Applications/LocalFlow}"
LOG="$HOME/.localflow-install.log"

B=$'\033[1m'; D=$'\033[2m'; V=$'\033[38;5;141m'; G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'
say()  { printf "%s\n" "$*"; }
step() { printf "\n${V}${B}%s${N}  %s\n" "$1" "$2"; }
ok()   { printf "   ${G}✓${N} %s\n" "$*"; }
ko()   { printf "   ${R}✗${N} %s\n" "$*"; }
ask()  { local a; printf "   %s" "$1"; read -r a < /dev/tty || a=""; printf "%s" "$a"; }
wait_enter() { printf "   ${D}Appuie sur Entrée quand c'est fait…${N}"; read -r _ < /dev/tty || true; }
spin() {  # spin "message" cmd…  → exécute en arrière-plan avec un indicateur
  local msg="$1"; shift; local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
  ("$@") >>"$LOG" 2>&1 & local pid=$!
  while kill -0 $pid 2>/dev/null; do printf "\r   %s %s " "${frames:i++%10:1}" "$msg"; sleep 0.1; done
  if wait $pid; then printf "\r   ${G}✓${N} %s   \n" "$msg"; else printf "\r   ${R}✗${N} %s   \n" "$msg"; return 1; fi
}

clear 2>/dev/null || true
printf "\n${V}${B}  LocalFlow${N}  ${D}— dictée vocale locale, gratuite, hors-ligne${N}\n"
say "  Maintiens la touche fn, parle, relâche : le texte est collé. Rien ne quitte ton Mac."
say "  L'installation prend 5 à 10 minutes (téléchargement des modèles). Je te guide."
: > "$LOG"

# ── 1. Le Mac ────────────────────────────────────────────────────────────────
step "1/4" "Vérification de ton Mac"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  ko "LocalFlow nécessite un Mac Apple Silicon (M1, M2, M3, M4…)."; exit 1
fi
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo Apple Silicon)"
RAM=$(( $(sysctl -n hw.memsize) / 1073741824 ))
FREE=$(df -g "$HOME" | awk 'NR==2{print $4}')
ok "$CHIP · ${RAM} Go de RAM · macOS $(sw_vers -productVersion)"
if [ "$FREE" -lt 8 ]; then ko "Il faut au moins 8 Go libres (tu en as ${FREE}). Libère de la place puis relance."; exit 1; fi
ok "${FREE} Go libres sur le disque"
if [ "$RAM" -lt 16 ]; then say "   ${D}Note : 8 Go de RAM, ça marche — choisis l'installation légère juste après.${N}"; fi

# ── 2. Le choix ──────────────────────────────────────────────────────────────
step "2/4" "Quelle version ?"
say "   ${B}1${N}  Complète  ${D}(recommandée · ~2,6 Go · nettoyage IA des « euh » en option)${N}"
say "   ${B}2${N}  Légère    ${D}(~1,6 Go · parfait si 8 Go de RAM)${N}"
CHOICE="$(ask "Ton choix [1] : ")"; echo
MIN=""; [ "$CHOICE" = "2" ] && MIN="--minimal"
ok "Version $([ -n "$MIN" ] && echo légère || echo complète)"

# ── 3. L'installation ────────────────────────────────────────────────────────
step "3/4" "Installation  ${D}(tu peux laisser tourner, détails dans $LOG)${N}"
mkdir -p "$DEST"
dl() {
  local tmp; tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$tmp"
  local src; src="$(find "$tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
  (cd "$src" && tar -cf - --exclude .venv .) | (cd "$DEST" && tar -xf -)
  rm -rf "$tmp"; chmod +x "$DEST"/*.sh
}
spin "Téléchargement de LocalFlow" dl
cd "$DEST"
export LOCALFLOW_WIZARD=1
spin "Python et dépendances" ./setup.sh --only-python
spin "Modèle de reconnaissance vocale (~1,6 Go, le plus long)" ./setup.sh --only-whisper
if [ -z "$MIN" ]; then spin "Modèle de nettoyage IA (~1 Go)" ./setup.sh --only-qwen; fi
spin "Application LocalFlow.app et démarrage automatique" ./setup.sh --only-app $MIN

# ── 4. Les autorisations ─────────────────────────────────────────────────────
step "4/4" "Deux autorisations macOS  ${D}(une seule fois)${N}"
DOC="$DEST/LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.doctor"
PYP="$DEST:$DEST/.venv/lib/python$(.venv/bin/python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')/site-packages"
doctor() { PYTHONPATH="$PYP" $DOC "$1" 2>/dev/null; }

say "   ${B}Accessibilité${N} — c'est ce qui permet d'écouter la touche fn et de coller le texte."
say "   Je viens d'ouvrir les Réglages et le dossier de l'app :"
say "   → glisse ${B}LocalFlow.app${N} (fenêtre Finder) dans la liste ${B}Accessibilité${N}, puis active l'interrupteur."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
open -R "$DEST/LocalFlow.app" 2>/dev/null || true
i=0
until doctor accessibility; do
  sleep 2; i=$((i+1))
  if [ $((i % 15)) -eq 0 ]; then say "   ${D}J'attends toujours… (Réglages → Confidentialité et sécurité → Accessibilité → + → LocalFlow.app)${N}"; fi
  if [ $i -ge 300 ]; then ko "Pas d'autorisation après 10 min. Relance cette commande quand tu veux, tout est conservé."; exit 1; fi
done
ok "Accessibilité accordée"

say ""
say "   ${B}Micro${N} — une fenêtre « LocalFlow souhaite accéder au micro » va apparaître : clique ${B}OK${N}."
launchctl kickstart -k "gui/$(id -u)/com.louqui.localflow" 2>/dev/null || true
i=0
until doctor microphone; do
  sleep 2; i=$((i+1))
  if [ $i -eq 10 ]; then
    say "   ${D}Pas de fenêtre ? Réglages → Confidentialité et sécurité → Micro → active LocalFlow.${N}"
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone" 2>/dev/null || true
  fi
  if [ $i -ge 150 ]; then ko "Micro non accordé. Active-le dans Réglages → Micro, LocalFlow marchera aussitôt."; break; fi
done
doctor microphone && ok "Micro accordé"

printf "\n${G}${B}  C'est prêt.${N}  L'icône 🎙 est dans la barre des menus, et LocalFlow se lance tout seul à chaque session.\n\n"
say "   ${B}Maintiens fn${N}, parle, relâche  →  le texte est collé"
say "   ${B}fn + espace${N}                  →  mains-libres (fn pour finir)"
say "   ${B}Double-tap fn${N}                →  panneau (historique, réglages)"
say "   Dis « à la ligne », « point d'interrogation », « efface ça »…"
say ""
say "   ${D}Astuce : va dans un champ de texte et essaie tout de suite.${N}"
say ""
