#!/bin/bash
# Installation en une commande (aucun prérequis, même pas git) :
#   curl -fsSL https://raw.githubusercontent.com/insaneaddi-stack/localflow/main/install.sh | bash
set -euo pipefail
REPO="${LOCALFLOW_REPO:-insaneaddi-stack/localflow}"
DEST="${LOCALFLOW_DIR:-$HOME/Applications/LocalFlow}"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "❌ LocalFlow nécessite un Mac Apple Silicon." >&2; exit 1
fi
echo "==> Téléchargement de LocalFlow dans $DEST…"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
# met à jour les fichiers du dépôt sans toucher au .venv existant
(cd "$SRC" && tar -cf - --exclude .venv .) | (cd "$DEST" && tar -xf -)
rm -rf "$TMP"
cd "$DEST"
chmod +x *.sh
exec ./setup.sh "$@"
