"""Mise à jour : compare le commit installé (.installed-sha) au dernier commit GitHub.

- check() : renvoie le sha distant si une mise à jour existe, sinon None (silencieux hors-ligne).
- launch() : ouvre le Terminal et relance l'assistant d'installation (qui conserve venv et modèles).
"""

import json
import os
import subprocess
import urllib.request

REPO = os.environ.get("LOCALFLOW_REPO", "insaneaddi-stack/localflow")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_FILE = os.path.join(ROOT, ".installed-sha")
INSTALL_CMD = f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash"

def installed_sha() -> str:
    try:
        with open(SHA_FILE) as f:
            return f.read().strip()
    except OSError:
        pass
    try:  # dépôt git (développement)
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def remote_sha(timeout=6) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/main",
        headers={"User-Agent": "LocalFlow", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("sha", "")

def check():
    """sha distant si mise à jour disponible, None sinon. Ne lève jamais."""
    try:
        local = installed_sha()
        remote = remote_sha()
        if remote and local and remote != local:
            return remote
    except Exception:
        pass
    return None

def launch():
    """Lance la mise à jour dans le Terminal (l'assistant relance LocalFlow à la fin)."""
    script = f'tell application "Terminal" to activate\ntell application "Terminal" to do script "{INSTALL_CMD}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
