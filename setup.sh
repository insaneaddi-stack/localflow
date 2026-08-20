#!/bin/bash
# Installation complète de LocalFlow sur un Mac Apple Silicon (même vierge).
#   ./setup.sh            installation standard (Whisper + nettoyage IA)
#   ./setup.sh --minimal  sans Qwen (nettoyage IA) ni Parakeet : ~1,6 Go au lieu de ~4,8 Go
set -euo pipefail
cd "$(dirname "$0")"
MINIMAL=0; ONLY=""
for a in "$@"; do case "$a" in --minimal) MINIMAL=1;; --only-*) ONLY="${a#--only-}";; esac; done
want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "❌ LocalFlow nécessite un Mac Apple Silicon (M1 ou plus récent)." >&2; exit 1
fi
case "$(pwd)" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "❌ Installe LocalFlow hors de Bureau/Documents/Téléchargements (macOS bloque l'agent là-dedans)." >&2
    echo "   Exemple : mv \"$(pwd)\" ~/Applications/LocalFlow && cd ~/Applications/LocalFlow && ./setup.sh" >&2; exit 1;;
esac

find_python() {
  for p in python3.12 python3.13 python3.11 python3.10 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)' 2>/dev/null; then
      command -v "$p"; return 0
    fi
  done
  return 1
}

if want python && [ ! -x .venv/bin/python ]; then
  if PYTHON="$(find_python)"; then
    echo "==> Python trouvé : $PYTHON"; "$PYTHON" -m venv .venv
  else
    echo "==> Aucun Python 3.10–3.13 trouvé, installation de uv (télécharge Python tout seul)…"
    if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
    "$UV" venv --python 3.12 --seed .venv
  fi
fi

if want python; then
  echo "==> Dépendances Python…"
  .venv/bin/python -m pip install --upgrade pip -q
  .venv/bin/python -m pip install -r requirements.txt -q
fi
if want whisper; then
  echo "==> Modèle Whisper large-v3-turbo (~1,6 Go, une seule fois)…"
  .venv/bin/python -c "import mlx_whisper, numpy as np; mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo='mlx-community/whisper-large-v3-turbo', language='fr')" >/dev/null
fi
if want qwen && [ "$MINIMAL" = 0 ]; then
  echo "==> Modèle Qwen3-1.7B 4-bit pour le nettoyage IA (~1 Go)…"
  .venv/bin/python -c "from mlx_lm import load; load('mlx-community/Qwen3-1.7B-4bit')" >/dev/null
fi
if want app; then
  echo "==> Bundle LocalFlow.app…"
  ./build-app.sh
  echo "==> Touche Globe 🌐 → « Ne rien faire » (sinon macOS ouvre les emoji à chaque dictée)…"
  defaults write com.apple.HIToolbox AppleFnUsageType -int 0 || true
  echo "==> Agent de session…"
  ./install-agent.sh
fi
[ -n "$ONLY" ] && exit 0
[ "${LOCALFLOW_WIZARD:-0}" = 1 ] && exit 0

cat <<'MSG'

✅ LocalFlow est installé et lancé (icône 🎙 dans la barre des menus).

Dernière étape, à faire UNE fois — les Réglages Système vont s'ouvrir :
  1. Confidentialité et sécurité → Accessibilité → + → LocalFlow.app (dans ce dossier) → activer
  2. Accepte la demande « Micro » au premier appui sur fn
Ensuite : maintiens fn, parle, relâche. Double-tap fn = panneau. fn+espace = mains-libres.
MSG
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
open -R "$(pwd)/LocalFlow.app" 2>/dev/null || true
