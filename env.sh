# Variables d'environnement communes (sourcé par build-app.sh, run.sh, install-agent.sh).
# - PYTHONPATH : le projet + le site-packages du venv (le bundle n'est pas un venv)
# - DYLD_FALLBACK_LIBRARY_PATH : libpython pour les Python standalone (uv), liés en chemin relatif
LF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LF_PYVER="$("$LF_ROOT/.venv/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
LF_PYHOME="$("$LF_ROOT/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')"
export PYTHONPATH="$LF_ROOT:$LF_ROOT/.venv/lib/python$LF_PYVER/site-packages"
export DYLD_FALLBACK_LIBRARY_PATH="$LF_PYHOME/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"
export PYTHONHOME="$LF_PYHOME"   # le binaire copié dans le bundle doit savoir où est la stdlib
