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
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MACOS_MAJOR" -lt 14 ]; then
  echo "❌ LocalFlow nécessite macOS 14 (Sonoma) ou plus récent — tu as macOS $(sw_vers -productVersion). Mets à jour via Réglages → Général → Mise à jour." >&2; exit 1
fi
case "$(pwd)" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "❌ Installe LocalFlow hors de Bureau/Documents/Téléchargements (macOS bloque l'agent là-dedans)." >&2
    echo "   Exemple : mv \"$(pwd)\" ~/Applications/LocalFlow && cd ~/Applications/LocalFlow && ./setup.sh" >&2; exit 1;;
esac

find_python() {
  [ "${LOCALFLOW_FORCE_UV:-0}" = 1 ] && return 1   # test : simule un Mac sans Python
  # Mac neuf : /usr/bin/python3 est un leurre qui ouvre « installer les outils développeur ».
  # On ne l'essaie que si les Command Line Tools sont déjà là.
  local cands="python3.12 python3.13 python3.11 python3.10 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13"
  xcode-select -p >/dev/null 2>&1 && cands="$cands python3"
  for p in $cands; do
    local path; path="$(command -v "$p" 2>/dev/null)" || continue
    [ "$path" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1 && continue
    if "$path" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)' 2>/dev/null; then
      echo "$path"; return 0
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

STAMP=.venv/.requirements.sha
REQ_SHA="$(shasum -a 256 requirements.txt | cut -c1-16)"
if want python; then
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$REQ_SHA" ] && .venv/bin/python -c "import mlx_whisper, rumps, sounddevice, AVFoundation" 2>/dev/null; then
    echo "==> Dépendances Python : déjà installées [SKIP]"
  else
    echo "==> Dépendances Python…"
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/python -m pip install -r requirements.txt -q
    echo "$REQ_SHA" > "$STAMP"
  fi
fi
model_cached() {  # model_cached <repo> : vrai si les poids sont déjà dans le cache Hugging Face
  local d="$HOME/.cache/huggingface/hub/models--${1//\//--}"
  [ -d "$d/snapshots" ] && find -L "$d/snapshots" -name "*.safetensors" -size +1M 2>/dev/null | grep -q . && [ -z "$(find "$d" -name '*.incomplete' 2>/dev/null)" ]
}
if want whisper; then
  if model_cached mlx-community/whisper-large-v3-turbo; then echo "==> Modèle Whisper : déjà présent [SKIP]"
  else
    echo "==> Modèle Whisper large-v3-turbo (~1,6 Go, une seule fois)…"
    .venv/bin/python -c "import mlx_whisper, numpy as np; mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo='mlx-community/whisper-large-v3-turbo', language='fr')" >/dev/null
  fi
fi
if want qwen && [ "$MINIMAL" = 0 ]; then
  if model_cached mlx-community/Qwen3-1.7B-4bit; then echo "==> Modèle Qwen : déjà présent [SKIP]"
  else
    echo "==> Modèle Qwen3-1.7B 4-bit pour le nettoyage IA (~1 Go)…"
    .venv/bin/python -c "from mlx_lm import load; load('mlx-community/Qwen3-1.7B-4bit')" >/dev/null
  fi
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
