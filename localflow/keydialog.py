"""Saisie de la clé Voxtral : un champ de texte où l'on colle la clé (⌘V), puis « Valider ».

Les fenêtres sont celles de macOS (osascript), au premier plan : leur champ accepte ⌘V.
"""

import json
import re
import subprocess

from . import voxtral

CONSOLE_URL = "https://console.mistral.ai/api-keys"
VALIDER, CONSOLE, PLUS_TARD = "Valider", "Ouvrir la console", "Plus tard"
COLLER = VALIDER   # ancien nom
CONSIGNE = ("1. Ouvre la console Mistral → API Keys → Create new key.\n"
            "2. Copie la clé, colle-la ci-dessous (⌘V).\n"
            f"3. Clique « {VALIDER} ».\n\n"
            "Elle reste dans le trousseau de ce Mac.")

def _dialog(message, buttons, default):
    """(bouton cliqué, texte du champ) ; ("", "") si fenêtre fermée ou 5 min sans réponse."""
    q = lambda t: json.dumps(t, ensure_ascii=False)
    script = ("tell current application to activate\n"
              f"display dialog {q(message)} default answer \"\" with title \"Clé Voxtral\" "
              f"buttons {{{', '.join(q(b) for b in buttons)}}} default button {q(default)} "
              "with icon note giving up after 300")
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout
    return _parse(out)

def _parse(out):
    if "gave up:true" in out:
        return "", ""
    b = re.search(r"button returned:(.*?), text returned:", out)
    t = re.search(r"text returned:(.*?)(?:, gave up:\w+)?\s*$", out, re.S)
    return (b.group(1).strip() if b else ""), (t.group(1).strip() if t else "")

def _field():
    """Le champ seul (étape du tutoriel) : texte collé, ou "" si annulé."""
    rep, text = _dialog("Colle ta clé Mistral (⌘V), puis « Valider ».", (PLUS_TARD, VALIDER), VALIDER)
    return text if rep == VALIDER else ""

def open_console():
    subprocess.run(["open", CONSOLE_URL])


def clipboard_has_key(clipboard=None):
    """Plus de lecture du presse-papiers (peu fiable) : le tutoriel ouvre d'abord la console."""
    return False


def paste_key(field=_field, check=voxtral.check_key, save=voxtral.save_key):
    """Prend la clé collée dans le champ et la range.
    "ok" | "unverified" (enregistrée, mais Mistral injoignable) | "not_a_key" | "refused"."""
    return _store(field(), check, save)


def _store(key, check, save):
    key = (key or "").strip()
    if not voxtral.looks_like_key(key):
        return "not_a_key"
    valid = check(key)
    if valid is False:
        return "refused"
    save(key)
    return "ok" if valid else "unverified"


def ask_key(notify, dialog=_dialog, open_console=open_console,
            check=voxtral.check_key, save=voxtral.save_key):
    """Boucle de saisie. True si une clé a été enregistrée, False si l'utilisateur remet à plus tard."""
    message = CONSIGNE
    while True:
        rep, text = dialog(message, (PLUS_TARD, CONSOLE, VALIDER), VALIDER)
        if rep == CONSOLE:
            open_console()
            continue
        if rep != VALIDER:
            return False
        res = _store(text, check, save)
        if res == "not_a_key":
            message = f"Ce n'est pas une clé Mistral.\nColle la clé dans le champ (⌘V), puis « {VALIDER} »."
            continue
        if res == "refused":
            message = f"Mistral refuse cette clé. Vérifie-la, recolle-la (⌘V), puis « {VALIDER} »."
            continue
        notify("Clé Voxtral enregistrée",
               "Tu peux dicter." if res == "ok" else "Je n'ai pas pu la vérifier (pas de réseau ?), elle est enregistrée.")
        return True
