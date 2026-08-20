"""Commandes vocales, appliquées après le nettoyage.

- Ponctuation / mise en page dictée : « à la ligne », « point d'interrogation »…
- Annulation : si toute la dictée est « efface ça » / « annule », on renvoie UNDO.
"""

import re

UNDO = object()

_UNDO_PHRASES = {
    "efface ça", "efface", "efface tout", "annule", "annule ça", "supprime ça",
    "supprime", "delete that", "scratch that", "undo", "undo that", "erase that",
}

# (motif, remplacement). \s*[.,!?]? absorbe la ponctuation que le LLM a pu ajouter.
_RULES = [
    (r"[ \t]*[,.]?[ \t]*\b(nouveau|nouvelle) paragraphe\b[ \t]*[.,!?]?[ \t]*", "\n\n"),
    (r"[ \t]*[,.]?[ \t]*\bnew paragraph\b[ \t]*[.,!?]?[ \t]*", "\n\n"),
    (r"[ \t]*[,.]?[ \t]*\bà la ligne\b[ \t]*[.,!?]?[ \t]*", "\n"),
    (r"[ \t]*[,.]?[ \t]*\bnew line\b[ \t]*[.,!?]?[ \t]*", "\n"),
    (r"[ \t]*[,.]?[ \t]*\bpoint d'interrogation\b[ \t]*[.,!?]?", "?"),
    (r"[ \t]*[,.]?[ \t]*\bquestion mark\b[ \t]*[.,!?]?", "?"),
    (r"[ \t]*[,.]?[ \t]*\bpoint d'exclamation\b[ \t]*[.,!?]?", "!"),
    (r"[ \t]*[,.]?[ \t]*\bexclamation mark\b[ \t]*[.,!?]?", "!"),
    (r"[ \t]*[,.]?[ \t]*\bpoints? de suspension\b[ \t]*[.,!?]?", "…"),
    (r"[ \t]*[,.]?[ \t]*\bdeux[- ]points\b[ \t]*[.,!?]?", " :"),
    (r"[ \t]*[,.]?[ \t]*\bpoint[- ]virgule\b[ \t]*[.,!?]?", " ;"),
    (r"\bouvrez? (les |la )?(guillemets?|parenthèses?)\b[ \t]*[.,!?]?[ \t]*", lambda m: " « " if "guill" in m.group(2) else " ("),
    (r"[ \t]*[,.]?[ \t]*\bfermez? (les |la )?(guillemets?|parenthèses?)\b[ \t]*[.,!?]?", lambda m: " »" if "guill" in m.group(2) else ")"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _RULES]

def _normalize(s):
    return re.sub(r"[^\w\s]", "", s.lower().replace("’", "'")).strip()

def apply_commands(text: str):
    if not text:
        return text
    if _normalize(text) in _UNDO_PHRASES:
        return UNDO
    for rx, repl in _COMPILED:
        text = rx.sub(repl, text)
    # Majuscule après une fin de phrase / un retour à la ligne
    text = re.sub(r"([.!?…]\s+|\n+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()
