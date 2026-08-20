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

IS_DEV = os.path.isdir(os.path.join(ROOT, ".git"))
UPDATED_FLAG = os.path.join(ROOT, ".updated-flag")

def installed_sha() -> str:
    try:
        with open(SHA_FILE) as f:
            return f.read().strip()
    except OSError:
        pass
    if IS_DEV:
        try:
            return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
    return ""

def _known_locally(sha: str) -> bool:
    """Dépôt git : le commit distant est-il déjà dans l'historique local (= on est en avance) ?"""
    if not IS_DEV:
        return False
    try:
        subprocess.run(["git", "-C", ROOT, "fetch", "-q", "origin", "main"], capture_output=True, timeout=15)
        return subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False

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
        if remote and local and remote != local and not _known_locally(remote):
            return remote
    except Exception:
        pass
    return None

def run_silent():
    """Mise à jour automatique en arrière-plan (update.sh) ; l'app est relancée à la fin."""
    subprocess.Popen(["/bin/bash", os.path.join(ROOT, "update.sh")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)

def just_updated() -> str:
    """sha si l'app vient d'être mise à jour (drapeau posé par update.sh), puis efface le drapeau."""
    try:
        with open(UPDATED_FLAG) as f:
            sha = f.read().strip()
        os.remove(UPDATED_FLAG)
        return sha
    except OSError:
        return ""

def launch():
    """Lance la mise à jour dans le Terminal (l'assistant relance LocalFlow à la fin)."""
    script = f'tell application "Terminal" to activate\ntell application "Terminal" to do script "{INSTALL_CMD}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
