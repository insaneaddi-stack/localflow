"""Apprentissage des corrections.

Deux sources :
1. Automatique : après un collage, au prochain appui sur fn on relit (via
   Accessibilité) le champ où le texte a été collé. Si un mot a été modifié
   à la main (« mail » → « main »), on le note. Vu 2 fois → appliqué ensuite.
2. Commande vocale : « corrige mail en main » → appliqué immédiatement.

Stockage : config["learned"] = {"mail": {"to": "main", "n": 2}}
"""

import difflib
import re
import threading
import time

LEARN_AFTER = 2          # observations avant d'appliquer automatiquement
MAX_AGE_S = 30 * 60      # on ne compare plus un collage vieux de plus de 30 min
MAX_PHRASE = 3           # longueur max, en mots, d'une correction apprise
# Ressemblance minimale entre le mot dicté et le mot tapé pour y voir une correction
# et non une réécriture. Mesuré sur des cas réels : les vraies corrections tombent à
# 0,75 et plus (mail→main), les reformulations à 0,56 et moins (absolument→vraiment).
MIN_RATIO = 0.6
_WORD = re.compile(r"[A-Za-zÀ-ÿœæŒÆ'’-]+")


def _phrase_re(bad: str) -> str:
    """Motif d'une entrée apprise : les espaces internes acceptent tout blanc,
    sinon « Wispr Flow » ne se retrouve pas s'il a été transcrit sur deux lignes."""
    return r"\b" + r"\s+".join(re.escape(p) for p in bad.split()) + r"\b"

def read_focused_text():
    """Texte du champ qui a le focus (None si indisponible)."""
    try:
        import ApplicationServices as AX

        system = AX.AXUIElementCreateSystemWide()
        AX.AXUIElementSetMessagingTimeout(system, 0.5)
        err, focused = AX.AXUIElementCopyAttributeValue(system, AX.kAXFocusedUIElementAttribute, None)
        if err or focused is None:
            return None
        err, value = AX.AXUIElementCopyAttributeValue(focused, AX.kAXValueAttribute, None)
        if err or not isinstance(value, str):
            return None
        return value
    except Exception:
        return None

def diff_corrections(pasted: str, current: str):
    """Mots remplacés 1 pour 1 entre le texte collé et le champ actuel."""
    a = _WORD.findall(pasted)
    b = _WORD.findall(current)
    if len(a) < 2 or not b:
        return []
    al = [w.lower() for w in a]
    bl = [w.lower() for w in b]
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    matched = sum(bk.size for bk in sm.get_matching_blocks())
    if matched < 0.6 * len(a):
        return []  # le champ ne contient plus (ou pas) notre texte
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        # On acceptait uniquement 1 mot ↔ 1 mot, ce qui laissait passer tous les
        # noms composés (« whisper flow » → « Wispr Flow ») — justement ceux qu'on
        # a le plus besoin d'apprendre.
        if tag != "replace":
            continue
        if not (1 <= i2 - i1 <= MAX_PHRASE and 1 <= j2 - j1 <= MAX_PHRASE):
            continue
        bad, good = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if len(bad) < 3 or len(good) < 2 or bad.lower() == good.lower():
            continue
        if difflib.SequenceMatcher(None, bad.lower(), good.lower()).ratio() < MIN_RATIO:
            continue  # trop différent : probablement une reformulation, pas une correction
        out.append((bad, good))
    return out

def parse_learn_command(text: str):
    """« corrige mail en main » / « remplace X par Y » → (bad, good) ou None."""
    t = text.strip().rstrip(".!").replace("’", "'")
    m = re.match(r"^(?:corrige|remplace|correction)\s+(?:le mot\s+)?(.+?)\s+(?:en|par)\s+(.+)$", t, re.IGNORECASE)
    if not m:
        return None
    bad, good = m.group(1).strip(" «»\"'"), m.group(2).strip(" «»\"'")
    if not bad or not good or len(bad.split()) > 3 or len(good.split()) > 3:
        return None
    return bad, good

class Learner:
    def __init__(self, config, notify, log):
        self.config = config
        self.notify = notify
        self.log = log
        self._last = None
        self._lock = threading.Lock()
        self.config.data.setdefault("learned", {})

    # ---- mémoire du dernier collage ----

    def remember_paste(self, text: str, bundle: str):
        with self._lock:
            self._last = (text, bundle, time.time())

    def check_async(self):
        """À appeler au prochain fn : compare le champ actuel au dernier collage."""
        with self._lock:
            last, self._last = self._last, None
        if not last or time.time() - last[2] > MAX_AGE_S:
            return
        threading.Thread(target=self._check, args=(last,), daemon=True).start()

    def _check(self, last):
        try:
            current = read_focused_text()
            if not current:
                return
            for bad, good in diff_corrections(last[0], current):
                self.observe(bad, good)
        except Exception as exc:
            self.log(f"apprentissage: erreur {exc}")

    # ---- base apprise ----

    def observe(self, bad: str, good: str, force: bool = False):
        learned = self.config.data.setdefault("learned", {})
        key = bad.lower()

        # Démotion. Si on avait appris good → bad et que l'utilisateur défait ce
        # remplacement, la règle est mauvaise : on la supprime AU LIEU d'ajouter
        # son inverse. Sans ça les deux règles coexistaient et se battaient dans
        # apply(), le résultat dépendant de l'ordre du dictionnaire.
        inverse = learned.get(good.lower())
        if inverse is not None and inverse.get("to", "").lower() == key:
            del learned[good.lower()]
            self.config.save()
            self.log(f"apprentissage: « {good} » → « {bad} » retirée (corrigée en sens inverse)")
            return

        entry = learned.get(key, {"to": good, "n": 0})
        if entry["to"].lower() != good.lower():
            entry = {"to": good, "n": 0}  # nouvelle cible : on repart
        was_active = entry["n"] >= LEARN_AFTER
        entry["n"] = LEARN_AFTER if force else entry["n"] + 1
        learned[key] = entry
        self.config.save()
        if entry["n"] >= LEARN_AFTER:
            self.log(f"apprentissage: « {bad} » → « {good} » actif")
            if not was_active:   # une seule notification, au passage à l'état actif
                self.notify("Correction apprise", f"« {bad} » → « {good} »")
        else:
            # Pas de notification tant que ce n'est qu'une observation : on n'a rien
            # changé pour l'utilisateur, et une notif par frappe corrigée est un enfer.
            self.log(f"apprentissage: « {bad} » → « {good} » remarqué ({entry['n']}/{LEARN_AFTER})")

    def forget(self, bad: str):
        if self.config.data.get("learned", {}).pop(bad.lower(), None) is not None:
            self.config.save()

    def active(self):
        return {k: v["to"] for k, v in self.config.data.get("learned", {}).items() if v.get("n", 0) >= LEARN_AFTER}

    def apply(self, text: str) -> str:
        if not text:
            return text
        # Les entrées longues d'abord : sinon un mot appris isolé consomme une partie
        # d'une expression apprise et l'empêche de se déclencher.
        for bad, good in sorted(self.active().items(), key=lambda kv: -len(kv[0].split())):
            def repl(m, good=good):
                w = m.group(0)
                if w.isupper():
                    return good.upper()
                if w[0].isupper():
                    return good[0].upper() + good[1:]
                return good
            text = re.sub(_phrase_re(bad), repl, text, flags=re.IGNORECASE)
        return text
