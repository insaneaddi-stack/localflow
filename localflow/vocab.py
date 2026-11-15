"""Vocabulaire qui apprend tout seul : remplit les 100 places du « coup de pouce » Voxtral.

Les mots rares que tu répètes (noms propres, outils, sigles) gagnent des points à chaque
dictée ; les points fondent de moitié tous les 60 jours. Un mot n'entre qu'après avoir été
vu sur 2 jours différents. Ces mots ne servent QU'AU coup de pouce envoyé à Voxtral : ils
ne réécrivent jamais le texte (seul le dictionnaire le fait).

Coût : une passe sur les mots de la dictée, après le collage (~0,05 ms). Aucun minuteur.
Le fichier ~/.localflow.vocab.json est privé (droits 600) et ne contient que des mots.
"""
import difflib
import json
import os
import re
import threading
import time

PATH = os.path.expanduser("~/.localflow.vocab.json")
HALF_DAYS = 60.0      # demi-vie des points
MIN_SCORE = 2.5       # points pour entrer
MIN_DAYS = 2          # jours différents pour entrer
CAP = 12.0            # plafond : une rafale d'un jour ne s'installe pas en tête
TOTAL = 100           # places du coup de pouce
Q_MANUAL, Q_LEARNED, Q_APP = 40, 12, 12

_TOK = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿŒœÆæ0-9][A-Za-zÀ-ÖØ-öø-ÿŒœÆæ0-9'’\-]*")
_SECRET = re.compile(r"https?://|www\.|@|\b(?:sk|mstrl|ghp|gho|xox[bp])[-_]|[A-Za-z0-9_\-]{24,}")
_SENT_START = re.compile(r"(?:^|[.!?…]\s+)([A-Za-zÀ-ÿ][\w'’\-]*)")


def _now_days():
    return time.time() / 86400.0


class Vocab:
    def __init__(self, known_word, path=PATH):
        """known_word(mot) → vrai si c'est un mot courant français/anglais (correcteur macOS)."""
        self.known = known_word
        self.path = path
        self.table = {}        # mot minuscule → [points, jour, nb_jours, forme, {appli: n}]
        self.ban = set()
        self._order = None     # tri mis en cache jusqu'à la prochaine observation (ou au lendemain)
        self._order_day = None
        self._miss_cache = {}
        self._dirty = 0
        self._lock = threading.Lock()
        self._load()

    # ---- disque ----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.table = d.get("table", {})
            self.ban = set(d.get("ban", []))
        except (OSError, ValueError):
            pass

    @property
    def empty(self):
        return not self.table

    def save(self):
        with self._lock:
            data = json.dumps({"table": self.table, "ban": sorted(self.ban)}, ensure_ascii=False)
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)
            self._dirty = 0
        except OSError:
            pass

    # ---- apprentissage ----
    def _rarity(self, word, first):
        k = word.lower()
        if k.isdigit() or sum(c.isdigit() for c in k) > 2 or re.fullmatch(r"\d+(e|er|re|ème|eme)", k):
            return 0.0                                   # nombres, dates, numéros
        if any(c.isdigit() for c in k):
            return 1.0                                   # sigles : R2, M4, H264
        if not (3 <= len(k) <= 24 or (word.isupper() and len(word) >= 2)):
            return 0.0
        if not self.known(word):
            return 1.0                                   # inconnu du correcteur : nom propre, outil
        if word[0].isupper() and not first:
            return 0.5                                   # mot courant, mais en majuscule : « Render »
        return 0.0

    def observe(self, text, app="", sent=(), now=None):
        """Une dictée collée. `sent` : termes envoyés pour CETTE dictée (anti-boucle)."""
        words = _TOK.findall(text or "")
        if len(words) < 6 or _SECRET.search(text):
            return
        sent = {t.lower() for t in sent}
        if sent and sum(w.lower() in sent for w in words) >= len(words) / 2:
            return                                       # la liste se renforcerait toute seule
        firsts = {m.group(1) for m in _SENT_START.finditer(text)}
        now = _now_days() if now is None else now
        app = (app or "").strip("‎‏ ")
        with self._lock:
            for w in set(words):
                k = w.lower()
                if k in self.ban:
                    continue
                r = self._rarity(w, w in firsts)
                if not r:
                    continue
                e = self.table.get(k)
                if e is None:
                    e = self.table[k] = [0.0, now, 0, w, {}]
                if e[2] == 0 or int(now) != int(e[1]):
                    e[2] += 1
                e[0] = e[0] * 0.5 ** ((now - e[1]) / HALF_DAYS) + r
                e[1] = now
                if w[0].isupper() or any(c.isdigit() for c in w):
                    e[3] = w
                if app:
                    e[4][app] = e[4].get(app, 0) + 1
            self._order = None
            self._dirty += 1
        if self._dirty >= 20:
            self.save()

    def forget(self, word):
        k = word.lower()
        with self._lock:
            self.ban.add(k)
            self.table.pop(k, None)
            self._order = None
        self.save()

    # ---- termes à envoyer ----
    def _ranked(self, now):
        if self._order is None or self._order_day != int(now):
            self._order_day = int(now)
            rows = []
            for k, (s, t, days, form, apps) in self.table.items():
                if days < MIN_DAYS:
                    continue
                p = s * 0.5 ** ((now - t) / HALF_DAYS)
                if p < MIN_SCORE:
                    continue
                rows.append((min(p, CAP) * min(1.0, days / 3.0), form, apps))
            rows.sort(key=lambda r: -r[0])
            self._order = rows
        return self._order

    def _misspelling(self, form, manual):
        """« Vixel » à côté de « Vixelle », « Loukman » à côté de « Louqman » : c'est la faute
        de Voxtral, l'envoyer la renforcerait. Mémorisé : la comparaison ne se fait qu'une fois."""
        key = (form.lower(), len(manual))
        hit = self._miss_cache.get(key)
        if hit is None:
            f = form.lower()
            hit = any(m.lower() != f and difflib.SequenceMatcher(None, f, m.lower()).ratio() >= 0.8
                      for m in manual)
            self._miss_cache[key] = hit
        return hit

    def terms(self, app="", manual=(), learned=(), now=None, wrong=()):
        now = _now_days() if now is None else now
        out, seen = [], set()

        def take(items, quota):
            n = 0
            for t in items:
                if n >= quota or len(out) >= TOTAL:
                    return
                if t and t.lower() not in seen and t.lower() not in self.ban:
                    seen.add(t.lower())
                    out.append(t)
                    n += 1

        take(manual, Q_MANUAL)
        take(learned, Q_LEARNED)
        with self._lock:
            ranked = [r for r in self._ranked(now)
                      if r[1].lower() not in wrong and not self._misspelling(r[1], manual)]
        if app:
            take((f for _, f, apps in ranked if apps.get(app, 0) >= 2), Q_APP)
        take((f for _, f, _a in ranked), TOTAL)
        return out


def _demo():
    v = Vocab(lambda w: w.lower() in {"le", "on", "dans", "et", "bien", "est", "the", "render", "ouvre", "puis"}, path="/dev/null")
    d0 = 1000.0
    phrase = "On ouvre Ghostty et Supabase dans Render puis le R2 est bien"
    v.observe(phrase, "Ghostty", now=d0)
    assert v.terms(now=d0) == [], "un seul jour : rien n'entre"
    v.observe(phrase, "Ghostty", now=d0 + 1)
    v.observe(phrase, "Ghostty", now=d0 + 2)
    t = v.terms(app="Ghostty", now=d0 + 2)
    assert set(t) == {"Ghostty", "Supabase", "R2"}, t   # « Render » (mot courant en majuscule) vaut 0,5 : il lui faut plus de fois
    assert "le" not in t and "On" not in t
    v.observe("voici ma clé sk-abcdef pour Zorblax et Zorblax encore ici", now=d0 + 3)
    assert "zorblax" not in v.table, "dictée avec un secret ignorée"
    assert v.terms(now=d0 + 2 + 400) == [], "oublié après 400 jours"
    w = Vocab(lambda x: False, path="/dev/null")
    assert w._misspelling("Vixel", ["Vixelle"]) and not w._misspelling("Vercel", ["Vixelle"])
    print("vocab ok")


if __name__ == "__main__":
    _demo()
