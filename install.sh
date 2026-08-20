#!/bin/bash
# ╭──────────────────────────────────────────────────────────────────────────╮
# │  LocalFlow — assistant d'installation animé (aucune connaissance requise)│
# │  curl -fsSL https://raw.githubusercontent.com/insaneaddi-stack/localflow/main/install.sh | bash
# ╰──────────────────────────────────────────────────────────────────────────╯
set -uo pipefail
REPO="${LOCALFLOW_REPO:-insaneaddi-stack/localflow}"
DEST="${LOCALFLOW_DIR:-$HOME/Applications/LocalFlow}"
LOG="$HOME/.localflow-install.log"
DEMO="${LOCALFLOW_DEMO:-0}"

# ─── couleurs (palette 256 : fonctionne dans Terminal.app, Ghostty, iTerm) ───
c()  { printf '\033[38;5;%sm' "$1"; }
bg() { printf '\033[48;5;%sm' "$1"; }
N=$'\033[0m'; B=$'\033[1m'; D=$'\033[2m'
GRAD=(99 135 141 147 153 159 123 87 80 44)          # violet → turquoise
WARM=(213 177 141 135 99)
VIO=141; TUR=80; PINK=213; OK=114; KO=203; MUT=244; DARK=234
COLS=$(tput cols 2>/dev/null || echo 80); ROWS=$(tput lines 2>/dev/null || echo 30)
[ "$COLS" -lt 40 ] && COLS=80
ORB_COL=$(( COLS - 20 )); [ "$ORB_COL" -gt 84 ] && ORB_COL=84
SHOW_ORB=1; [ "$COLS" -lt 78 ] && SHOW_ORB=0
BANNER_W=$(( ORB_COL - 6 ))
TEXT_ROW=10

SUMMARY=""
cleanup() { printf '\033[r'; tput rmcup 2>/dev/null; tput cnorm 2>/dev/null; printf '%s' "$N"; [ -n "$SUMMARY" ] && printf '\n%s\n' "$SUMMARY"; }
trap cleanup EXIT
trap 'exit 130' INT

at() { tput cup "$1" "$2" 2>/dev/null; }
gradient_text() {  # gradient_text "texte" → lettre par lettre dans le dégradé
  local s="$1" i n=${#GRAD[@]}
  for ((i=0;i<${#s};i++)); do printf '%s%s' "$(c "${GRAD[$(( i * n / ${#s} ))]}")" "${s:i:1}"; done
  printf '%s' "$N"
}

# ─── l'orbe : 7 lignes × 16 colonnes, yeux orientables, clignement, sourire ──
orb_rim() {  # couleur du liseré selon la colonne (0-15) et le temps
  local col=$1 t=$2 n=${#GRAD[@]}
  echo "${GRAD[$(( (col + t) % n ))]}"
}
orb() {  # orb <eyes: L|C|R|blink|happy> <tick>
  [ "$SHOW_ORB" = 1 ] || return 0
  local eyes=$1 t=$2 r=$3 cc=$4
  local rim=("     ▄▄▄▄▄▄     " "   ▄█      █▄   " "  █          █  " "  █          █  " "  █          █  " "   ▀█      █▀   " "     ▀▀▀▀▀▀     ")
  local e2="      " e3="      " shift=0
  case "$eyes" in
    L) shift=-1;; R) shift=1;;
  esac
  case "$eyes" in
    blink) e2="      "; e3="▀▀  ▀▀";;
    happy) e2="      "; e3="◠◠  ◠◠";;
    *)     e2="▐▌  ▐▌"; e3="▐▌  ▐▌";;
  esac
  # particules qui scintillent
  local p1="·" p2="✦" p3="·" p4="✧"
  (( t % 3 == 0 )) && p2="·"; (( t % 4 == 1 )) && p4="✦"; (( t % 5 == 2 )) && p1=" "
  local i line col ch out
  for i in 0 1 2 3 4 5 6; do
    line="${rim[$i]}"; out=""
    for ((col=0; col<16; col++)); do
      ch="${line:col:1}"
      if [ "$ch" != " " ]; then
        out+="$(c "$(orb_rim "$col" "$t")")$ch"
      elif (( i>=1 && i<=5 && col>=3 && col<=12 )) || (( i>=2 && i<=4 && col>=2 && col<=13 )); then
        out+="$(bg $DARK) $N"    # intérieur sombre
      else
        out+=" "
      fi
    done
    at $((r+i)) "$cc"; printf '%s%s' "$out" "$N"
  done
  # yeux (par-dessus l'intérieur), décalés selon le regard
  at $((r+2)) $((cc+5+shift)); printf '%s%s%s%s' "$(bg $DARK)" "$(c 255)$B" "$e2" "$N"
  at $((r+3)) $((cc+5+shift)); printf '%s%s%s%s' "$(bg $DARK)" "$(c 255)$B" "$e3" "$N"
  # particules
  at $((r+0)) $((cc+1));  printf '%s%s%s' "$(c $TUR)" "$p1" "$N"
  at $((r+1)) $((cc+15)); printf '%s%s%s' "$(c $PINK)" "$p2" "$N"
  at $((r+5)) $((cc+0));  printf '%s%s%s' "$(c $VIO)" "$p3" "$N"
  at $((r+6)) $((cc+14)); printf '%s%s%s' "$(c $TUR)" "$p4" "$N"
}
ORB_ROW=2
orb_frame() {  # orb_frame <tick> <mood: work|wait|happy>
  local t=$1 mood=$2 eyes="C"
  case "$mood" in
    work)  eyes="R"; (( t % 40 > 33 )) && eyes="C"; (( t % 97 == 3 )) && eyes="blink";;
    wait)  eyes="C"; (( t % 30 > 22 )) && eyes="L"; (( t % 30 > 26 )) && eyes="R"; (( t % 53 == 5 )) && eyes="blink";;
    happy) eyes="happy";;
  esac
  (( t % 61 == 7 )) && eyes="blink"
  orb "$eyes" "$t" "$ORB_ROW" "$ORB_COL"
}

# ─── barre de progression indéterminée en dégradé ────────────────────────────
bar() {  # bar <tick> <width>
  local t=$1 w=$2 i pos=$(( t % (w*2) )); (( pos >= w )) && pos=$(( w*2 - pos - 1 ))
  local out=""
  for ((i=0;i<w;i++)); do
    local d=$(( i - pos )); (( d < 0 )) && d=$(( -d ))
    if (( d < 5 )); then out+="$(c "${GRAD[$(( (i + t/2) % ${#GRAD[@]} ))]}")━"; else out+="$(c 237)━"; fi
  done
  printf '%s%s' "$out" "$N"
}
bar_done() { local i out=""; for ((i=0;i<$2;i++)); do out+="$(c $OK)━"; done; printf '%s%s' "$out" "$N"; }

# ─── sortie texte (zone sous la bannière) ────────────────────────────────────
LINE=$TEXT_ROW
SCROLL_BOT=$((ROWS-1))
# zone de texte = région de défilement : la bannière et l'orbe restent fixes
newline() { if (( LINE > SCROLL_BOT )); then at $SCROLL_BOT 0; printf '\n'; LINE=$SCROLL_BOT; fi; }
say()  { newline; at $LINE 3; printf '\033[K%s' "$*"; LINE=$((LINE+1)); }
ok()   { say "$(c $OK)✓$N  $*"; }
ko()   { say "$(c $KO)✗$N  $*"; }
blank(){ newline; at $LINE 0; printf '\033[K'; LINE=$((LINE+1)); }
step() { blank; say "$(c $VIO)$B$1$N  $B$2$N"; }
ANSWER=""
ask()  {  # ask "question" → réponse dans $ANSWER (pas de sous-shell, pas d'écho de la touche Entrée)
  newline; at $LINE 3; printf '\033[K   %s' "$1"; tput cnorm
  ANSWER=""; read -rs ANSWER < /dev/tty || ANSWER=""
  printf '%s' "$ANSWER"; tput civis; LINE=$((LINE+1))
}

run() {  # run "message" <mood> cmd…  — exécute en fond, anime orbe + barre
  local msg="$1" mood="$2"; shift 2
  newline; local row=$LINE; LINE=$((LINE+1)); local t=0 w=24
  local mark; mark="$(wc -l < "$LOG" | tr -d ' ')"
  if [ "$DEMO" = 1 ]; then (sleep 2.5) & else ("$@") >>"$LOG" 2>&1 & fi
  local pid=$!
  while kill -0 $pid 2>/dev/null; do
    at $row 3; printf '\033[K   %s  %s' "$(bar $t $w)" "$msg"
    orb_frame $t "$mood"; t=$((t+1)); sleep 0.08
  done
  if wait $pid; then
    local note=""; tail -n +"$((mark+1))" "$LOG" | grep -q '\[SKIP\]' && note="  $(c $MUT)déjà installé$N"
    at $row 3; printf '\033[K   %s  %s%s' "$(bar_done $t $w)" "$msg" "$note"; return 0
  else
    at $row 3; printf '\033[K   %s%s%s  %s' "$(c $KO)" "$(printf '━%.0s' $(seq 1 $w))" "$N" "$msg  $(c $KO)échec$N"
    blank; say "$(c $KO)Ce qui s'est passé (détails complets : $LOG) :$N"
    tail -n +"$((mark+1))" "$LOG" | grep -v '^\s*$' | tail -6 | cut -c1-$((COLS-8)) | while IFS= read -r l; do say "   $(c $MUT)$l$N"; done
    blank; say "Envoie ce fichier à Louqman : $B~/.localflow-install.log$N — et relance la commande plus tard, tout est conservé."
    SUMMARY="$(c $KO)✗ Installation interrompue.$N Détails : $LOG"
    return 1
  fi
}
wait_until() {  # wait_until <mood> <timeout_s> <hint_every_s> "hint" cmd…
  local mood=$1 timeout=$2 every=$3 hint=$4; shift 4
  local t=0 s=0 hrow
  while ! "$@" 2>/dev/null; do
    orb_frame $t "$mood"; t=$((t+1)); sleep 0.08
    if (( t % 12 == 0 )); then s=$((s+1))
      if (( s % every == 0 )); then say "$(c $MUT)$hint$N"; fi
      (( s >= timeout )) && return 1
    fi
  done
  return 0
}
celebrate() {  # petite pluie d'étincelles autour de l'orbe
  [ "$SHOW_ORB" = 1 ] || return 0
  local t i
  for t in $(seq 1 22); do
    orb_frame $t happy
    for i in 1 2 3; do
      local rr=$(( ORB_ROW - 1 + (t*7 + i*13) % 10 )) cc2=$(( ORB_COL - 4 + (t*11 + i*17) % 26 ))
      (( rr < 1 )) && rr=1
      at $rr $cc2; printf '%s%s%s' "$(c "${WARM[$(( (t+i) % 5 ))]}")" "$( (( (t+i) % 2 )) && echo '✦' || echo '·')" "$N"
    done
    sleep 0.07
  done
  sleep 0.2; for i in $(seq 0 8); do at $((ORB_ROW-1+i)) $((ORB_COL-4)); printf '\033[K'; done
  orb_frame 0 happy
}

# ─── écran ───────────────────────────────────────────────────────────────────
tput smcup 2>/dev/null; tput civis; clear
printf '\033[%d;%dr' $((TEXT_ROW+1)) $ROWS   # région de défilement (1-based) : bannière et orbe fixes
at 2 3;  gradient_text "L O C A L F L O W"; printf '   %s%s%s' "$D" "dictée vocale · locale · hors-ligne" "$N"
at 4 3;  printf '%sMaintiens %sfn%s%s, parle, relâche : c'"'"'est collé.%s' "$(c 250)" "$N$B" "$N" "$(c 250)" "$N"
at 5 3;  printf '%sRien ne quitte ton Mac. 5 à 10 min, je te guide.%s' "$(c 250)" "$N"
at 7 3;  printf '%s%s%s' "$(c 237)" "$(printf '─%.0s' $(seq 1 $BANNER_W))" "$N"
orb_frame 0 wait
: > "$LOG"

# 1 ── le Mac
step "1/4" "Ton Mac"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then ko "LocalFlow nécessite un Mac Apple Silicon (M1, M2, M3, M4…)."; exit 1; fi
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
RAM=$(( $(sysctl -n hw.memsize) / 1073741824 )); FREE=$(df -g "$HOME" | awk 'NR==2{print $4}')
ok "$CHIP · ${RAM} Go de RAM · macOS $(sw_vers -productVersion)"
if [ "$FREE" -lt 8 ]; then ko "Il faut 8 Go libres (tu en as ${FREE}). Libère de la place puis relance la commande."; exit 1; fi
ok "${FREE} Go libres sur le disque"

# 2 ── le choix
step "2/4" "Quelle version ?"
say "   $B 1 $N Complète   $(c $MUT)recommandée · ~2,6 Go · nettoyage IA en option$N"
say "   $B 2 $N Légère     $(c $MUT)~1,6 Go · parfaite si 8 Go de RAM$N"
ask "Ton choix, puis Entrée  [1] : "
MIN=""; [ "$ANSWER" = "2" ] && MIN="--minimal"
ok "Version $([ -n "$MIN" ] && echo légère || echo complète)"

# 3 ── l'installation
step "3/4" "Installation  $(c $MUT)— laisse tourner (détails : ~/.localflow-install.log)$N"
mkdir -p "$DEST"
dl() { local tmp src; tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$tmp" || return 1
  src="$(find "$tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
  (cd "$src" && tar -cf - --exclude .venv .) | (cd "$DEST" && tar -xf -); rm -rf "$tmp"; chmod +x "$DEST"/*.sh; }
run "Téléchargement de LocalFlow" work dl || exit 1
cd "$DEST" 2>/dev/null || { [ "$DEMO" = 1 ] || exit 1; }
export LOCALFLOW_WIZARD=1
run "Python et dépendances" work ./setup.sh --only-python || exit 1
run "Modèle vocal  (~1,6 Go, le plus long)" work ./setup.sh --only-whisper || exit 1
[ -z "$MIN" ] && { run "Modèle de nettoyage IA  (~1 Go)" work ./setup.sh --only-qwen || exit 1; }
run "Application + démarrage automatique" work ./setup.sh --only-app $MIN || exit 1

# 4 ── les autorisations
step "4/4" "Deux autorisations macOS  $(c $MUT)— une seule fois$N"
if [ "$DEMO" = 1 ]; then doctor() { (( ++DEMO_N > 25 )); }; DEMO_N=0
else
  PYV="$(.venv/bin/python -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  PYP="$DEST:$DEST/.venv/lib/python$PYV/site-packages"
  doctor() { PYTHONPATH="$PYP" "$DEST/LocalFlow.app/Contents/MacOS/LocalFlow" -m localflow.doctor "$1"; }
fi
say "$B Accessibilité$N  $(c $MUT)— pour écouter fn et coller le texte$N"
say "   J'ouvre les Réglages et le dossier de l'app."
say "   Glisse $B LocalFlow.app $N dans $B Accessibilité $N, active l'interrupteur."
[ "$DEMO" = 1 ] || { open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null; open -R "$DEST/LocalFlow.app" 2>/dev/null; }
if wait_until wait 600 30 "J'attends… Réglages → Confidentialité → Accessibilité → + → LocalFlow.app" doctor accessibility; then
  ok "Accessibilité accordée"; orb_frame 0 happy; sleep 0.6
else ko "Pas d'autorisation après 10 min. Relance la commande quand tu veux, tout est conservé."; exit 1; fi
say "$B Micro$N  $(c $MUT)— clique OK sur « LocalFlow souhaite accéder au micro »$N"
[ "$DEMO" = 1 ] || launchctl kickstart -k "gui/$(id -u)/com.louqui.localflow" 2>/dev/null
[ "$DEMO" = 1 ] && DEMO_N=0
if wait_until wait 300 20 "Pas de fenêtre ? Réglages → Confidentialité → Micro → LocalFlow." doctor microphone; then
  ok "Micro accordé"
else ko "Micro non accordé — active-le dans Réglages → Micro, LocalFlow marchera aussitôt."; fi

# ── fin
celebrate
blank
say "$(c $OK)$B C'est prêt.$N  L'icône 🎙 est dans la barre des menus."
say "   LocalFlow démarre tout seul à chaque session."
blank
say "   $B Maintiens fn $N, parle, relâche   →  le texte est collé"
say "   $B fn + espace $N                    →  mains-libres (fn pour finir)"
say "   $B Double-tap fn $N                  →  panneau : historique, réglages"
say "   $(c $MUT)Dis « à la ligne », « point d'interrogation », « efface ça »…$N"
say "   $(c $MUT)Essaie tout de suite dans un champ de texte.$N"
blank
say "$(c $MUT)Appuie sur Entrée pour fermer cet écran.$N"
read -rs _ < /dev/tty || true
SUMMARY="$(c $OK)${B}✓ LocalFlow est installé.$N  Maintiens fn, parle, relâche · fn+espace = mains-libres · double-tap fn = panneau
  Si un jour ça ne répond plus : Réglages → Confidentialité → Accessibilité / Micro → LocalFlow."
