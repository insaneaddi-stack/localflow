"""Dictionnaire personnel : ~/.localflow.dict.txt

Format (une entrée par ligne, # = commentaire) :
    Noto                    ← mot à protéger : les variantes proches sont corrigées
    whisper flow -> Wispr Flow   ← remplacement explicite (insensible à la casse)
"""

import difflib
import os
import re

DICT_PATH = os.path.expanduser("~/.localflow.dict.txt")

DEFAULT_CONTENT = """# Dictionnaire LocalFlow — un mot ou un nom par ligne.
# « mauvais -> bon » force un remplacement. Sans flèche, les mots proches sont corrigés.
# Rechargé automatiquement à chaque dictée.
LocalFlow
whisper flow -> Wispr Flow
whisperflow -> Wispr Flow
"""

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿœæŒÆ0-9'’-]+")

class Dictionary:
    def __init__(self, path=DICT_PATH):
        self.path = path
        self._mtime = None
        self.words = []        # mots protégés, casse d'origine
        self.replacements = [] # (regex compilé, remplacement)
        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_CONTENT)
            except OSError:
                pass
        self.reload()

    def reload(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        words, repl = [], []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "->" in line:
                        bad, good = (s.strip() for s in line.split("->", 1))
                        if bad and good:
                            repl.append((re.compile(r"\b" + re.escape(bad) + r"\b", re.IGNORECASE), good))
                            words.append(good)
                    else:
                        words.append(line)
        except OSError:
            return
        self.words = list(dict.fromkeys(words))
        self.replacements = repl

    def vocab_words(self):
        """Tous les tokens du vocabulaire (pour le garde-fou LLM)."""
        out = set()
        for w in self.words:
            out.update(t.lower() for t in _WORD_RE.findall(w))
        return out

    def apply(self, text: str) -> str:
        self.reload()
        if not text:
            return text
        for rx, good in self.replacements:
            text = rx.sub(good, text)
        # correction floue : mot transcrit proche d'un mot du dictionnaire (1 seul mot)
        singles = [w for w in self.words if " " not in w and len(w) >= 4]
        if not singles:
            return text
        lowered = {w.lower(): w for w in singles}

        def fix(m):
            tok = m.group(0)
            low = tok.lower()
            if low in lowered:
                return lowered[low]  # bonne orthographe, mais on impose la casse
            if len(tok) < 4:
                return tok
            best = difflib.get_close_matches(low, lowered.keys(), n=1, cutoff=0.8)
            return lowered[best[0]] if best else tok

        return _WORD_RE.sub(fix, text)
