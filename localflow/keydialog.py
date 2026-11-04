"""Saisie de la clé Voxtral : on copie la clé, on clique « Coller la clé ».

Pas de champ de texte : une app de barre de menus n'a pas de menu Édition, donc ⌘V n'y marche
pas toujours dans une fenêtre de saisie. Le presse-papiers + un bouton évitent ça, et la clé
n'est jamais retapée. Les fenêtres sont celles de macOS (osascript), au premier plan.
"""

import json
import re
import subprocess

from . import voxtral

CONSOLE_URL = "https://console.mistral.ai/api-keys"
COLLER, CONSOLE, PLUS_TARD = "Coller la clé", "Ouvrir la console", "Plus tard"
CONSIGNE = ("1. Ouvre la console Mistral → API Keys → Create new key.\n"
            "2. Copie la clé (⌘C).\n"
            f"3. Clique « {COLLER} ».\n\n"
            "Elle reste dans le trousseau de ce Mac.")


def _dialog(message, buttons, default):
    """Texte du bouton cliqué, ou "" (fenêtre fermée, ou 5 min sans réponse)."""
    q = lambda t: json.dumps(t, ensure_ascii=False)
    script = ("tell current application to activate\n"
              f"display dialog {q(message)} with title \"Clé Voxtral\" "
              f"buttons {{{', '.join(q(b) for b in buttons)}}} default button {q(default)} "
              "with icon note giving up after 300")
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    m = re.search(r"button returned:([^,]*)", out.stdout)
    return m.group(1).strip() if m else ""


def _clipboard():
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _open_console():
    subprocess.run(["open", CONSOLE_URL])


def ask_key(notify, dialog=_dialog, clipboard=_clipboard, open_console=_open_console,
            check=voxtral.check_key, save=voxtral.save_key):
    """Boucle de saisie. True si une clé a été enregistrée, False si l'utilisateur remet à plus tard."""
    message = CONSIGNE
    while True:
        rep = dialog(message, (PLUS_TARD, CONSOLE, COLLER), COLLER)
        if rep == CONSOLE:
            open_console()
            continue
        if rep != COLLER:
            return False
        key = clipboard().strip()
        if not voxtral.looks_like_key(key):
            message = f"Le presse-papiers ne contient pas de clé Mistral.\nCopie la clé (⌘C), puis clique « {COLLER} »."
            continue
        valid = check(key)
        if valid is False:
            message = f"Mistral refuse cette clé. Vérifie que tu as copié la bonne (⌘C), puis clique « {COLLER} »."
            continue
        save(key)
        notify("Clé Voxtral enregistrée",
               "Tu peux dicter." if valid else "Je n'ai pas pu la vérifier (pas de réseau ?), elle est enregistrée.")
        return True
