#!/bin/bash
# Mise à jour silencieuse de LocalFlow (lancée par l'app, ou à la main : ./update.sh).
# Sûre : le nouveau code est vérifié AVANT de remplacer l'ancien ; sauvegarde + retour arrière si échec.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
REPO="${LOCALFLOW_REPO:-insaneaddi-stack/localflow}"
LOG="$HOME/.localflow-update.log"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') mise à jour depuis $REPO"

TMP="$(mktemp -d)"; BK="$(mktemp -d)"
fail() { echo "✗ $1"; rm -rf "$TMP"; exit 1; }
restore() {
  echo "↩ retour à la version précédente"
  (cd "$BK" && tar -cf - .) | (cd "$ROOT" && tar -xf -)
  ./build-app.sh >/dev/null 2>&1 || true
}

# 1. télécharger
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP" || fail "téléchargement"
SRC="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
SHA="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/commits/main" | sed -n 's/^ *"sha": *"\([0-9a-f]*\)".*/\1/p' | head -1)"
[ -n "$SHA" ] || fail "sha distant introuvable"
[ "$SHA" = "$(cat .installed-sha 2>/dev/null)" ] && { echo "déjà à jour ($SHA)"; rm -rf "$TMP" "$BK"; exit 0; }

# 2. vérifier le nouveau code avant de toucher à quoi que ce soit
for f in "$SRC"/*.sh; do /bin/bash -n "$f" || fail "script invalide : $(basename "$f")"; done
.venv/bin/python -m py_compile "$SRC"/localflow/*.py || fail "code Python invalide"

# 3. sauvegarde de l'actuel (code + scripts + assets), puis remplacement
tar -cf - --exclude .venv --exclude LocalFlow.app --exclude .git . | (cd "$BK" && tar -xf -)
(cd "$SRC" && tar -cf - --exclude .venv .) | (cd "$ROOT" && tar -xf -) || { restore; fail "copie"; }
chmod +x "$ROOT"/*.sh

# 4. dépendances si requirements.txt a changé (sinon instantané), bundle reconstruit ET testé
LOCALFLOW_WIZARD=1 ./setup.sh --only-python || { restore; fail "dépendances"; }
./build-app.sh || { restore; fail "construction du bundle"; }
echo "$SHA" > .installed-sha
echo "$SHA" > .updated-flag
rm -rf "$TMP" "$BK"

# 5. redémarrage via l'agent (sauf en test)
if [ "${LOCALFLOW_NO_AGENT:-0}" != 1 ]; then ./install-agent.sh >/dev/null 2>&1 || true; fi
echo "✓ mis à jour → $SHA"
