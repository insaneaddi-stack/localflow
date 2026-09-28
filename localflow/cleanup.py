"""Nettoyage du texte dicté.

Deux étages :

1. `cleanup_rules()` — moteur déterministe, ~1 ms, toujours actif. Il ne fait
   que **supprimer** : hésitations, bégaiements, répétitions de mots ou de
   phrases entières, reprises approximatives (« j'ai envoyé le fichier, j'ai
   envoyé le bon fichier »). Aucun mot n'est jamais réécrit ni inventé.
2. `Cleaner` — passe LLM optionnelle (Qwen3-1.7B 4 bits, ~0,8 s) qui reponctue et
   segmente en phrases. Activée par le toggle « Nettoyage IA ». Son garde-fou
   n'autorise que la ponctuation, la casse, les accents et le retrait de bruit :
   toute réécriture fait retomber sur la sortie des règles.

   Mesuré sur 15 dictées types (M4, sept. 2026) : Qwen3-1.7B 0,79 s et 13/15
   sorties retenues ; Qwen3-0.6B 0,31 s mais 5/15 seulement — il réécrit trop
   (« nous nous sommes vus » → « nous sommes vus »), donc on paie la latence
   pour rien deux fois sur trois. D'où le 1.7B.
"""

import difflib
import re
import threading
import unicodedata

LLM_MODEL_ID = "mlx-community/Qwen3-1.7B-4bit"

SYSTEM_PROMPT = (
    "Tu nettoies des transcriptions de dictée vocale (français ou anglais). "
    "Supprime toutes les hésitations (euh, hum, um, uh...), les faux départs, "
    "les tics de langage inutiles et les mots répétés par erreur. "
    "Corrige la ponctuation. Mets en page selon le contenu : si la dictée énumère "
    "plusieurs éléments (courses, étapes, points, options), fais une liste à puces "
    "« - », un élément par ligne, après sa phrase d'introduction ; sinon garde des "
    "paragraphes normaux. Garde tous les autres mots exactement tels quels : "
    "ne reformule pas, ne traduis pas, ne réponds jamais au contenu. "
    "Réponds uniquement avec le texte nettoyé, rien d'autre."
)

TONE_HINTS = {
    "casual": (
        "Contexte : messagerie instantanée. Garde le registre oral et le tutoiement, "
        "ponctuation légère, pas de point final obligatoire, pas de formules de politesse ajoutées."
    ),
    "formal": (
        "Contexte : e-mail ou document. Ponctuation soignée, phrases complètes, "
        "corrige les accords évidents (pluriels, genre) sans changer les mots."
    ),
    "neutral": "",
}

FEW_SHOT = [
    (
        "Alors euh du coup je voulais te dire que que le projet euh avance bien",
        "Alors je voulais te dire que le projet avance bien.",
    ),
    (
        "Um, so I I think we should uh probably ship it tomorrow yeah",
        "So I think we should probably ship it tomorrow.",
    ),
    (
        "Bonjour, euh, est-ce que tu peux m'envoyer le le fichier hum le fichier final",
        "Bonjour, est-ce que tu peux m'envoyer le fichier final ?",
    ),
    (
        "Ok donc euh premier point on valide le budget. Euh deuxième point il faut "
        "que que je rappelle le client. Et euh voilà on fait le point vendredi",
        "Ok donc :\n- Premier point, on valide le budget.\n- Deuxième point, il faut "
        "que je rappelle le client.\n\nEt voilà, on fait le point vendredi.",
    ),
    (
        "Alors la procédure c'est d'abord tu ouvres le fichier ensuite tu le signes et enfin tu me le renvoies",
        "Alors la procédure c'est :\n- d'abord, tu ouvres le fichier ;\n- ensuite, tu le signes ;\n"
        "- et enfin, tu me le renvoies.",
    ),
    (
        "Pour ce soir il faut acheter du pain des œufs euh du lait et du beurre",
        "Pour ce soir, il faut acheter :\n- du pain\n- des œufs\n- du lait\n- du beurre",
    ),
]


# ---------------------------------------------------------------- règles ----

# Un mot = lettres/chiffres, éventuellement liés par apostrophe ou trait d'union :
# « aujourd'hui », « peut-être », « je-je-je » comptent chacun pour un jeton.
_WORD_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿŒœ0-9]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿŒœ0-9]+)*"
)

# Hésitations pures. Chaque motif exige la consonne qui distingue le tic du mot :
# « e+u+h+ » et non « e+u+h* », sinon « eu » (j'ai eu) disparaîtrait.
_FILLER_RE = re.compile(
    r"^(?:e+u+h+|h+e+u+h*|h+u+m+|h+e+m+|u+h+m*|u+m+|e+r+m*|m+h+|m{2,}h*|h+m+)$"
)

# Répétitions légitimes à ne PAS dédoublonner (« nous nous sommes », « très très »)
_KEEP_DOUBLE = {
    "nous", "vous", "on", "en", "ca", "la", "tres", "bien", "tout", "plus",
    "non", "oui", "vite", "doucement", "petit", "had", "that", "is",
    "very", "so", "no", "yes", "really",
}

# Une reprise qui se termine par une conjonction n'en est pas une :
# « je vais au bureau ET je vais au marché » est une phrase, pas un bégaiement.
_COORD = {
    "et", "ou", "mais", "donc", "puis", "ensuite", "car", "ni",
    "and", "or", "but", "then", "so", "because",
}

# Marqueurs d'auto-correction, tolérés entre les deux prises d'une reprise.
_REPAIR_PHRASES = [
    ("je", "veux", "dire"),
    ("cest", "a", "dire"),
    ("en", "fait"),
    ("i", "mean"),
]
# Mots-marqueurs, enchaînables : « non pardon », « enfin non », « ou plutôt ».
_REPAIR_WORDS = {"enfin", "pardon", "non", "plutot", "disons", "bref", "sorry", "rather"}

_MAX_RUN = 8        # longueur maxi d'une prise comparée
_MAX_ABANDONED = 12 # longueur maxi d'une amorce abandonnée jetée d'un bloc
_MAX_GAP = 14       # au-delà, deux occurrences d'un mot ne sont plus une reprise

_key_cache = {}


def _key(word: str) -> str:
    """Forme normalisée d'un mot : minuscule, sans accent ni apostrophe."""
    k = _key_cache.get(word)
    if k is None:
        n = unicodedata.normalize("NFD", word.lower())
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        k = re.sub(r"[^a-z0-9]", "", n)
        _key_cache[word] = k
    return k


def _split(text: str):
    """Texte → [[mot, suite], ...] où « suite » = ponctuation et espaces qui suivent.

    Supprimer un mot emporte sa ponctuation : « le fichier, le fichier final »
    perd bien la virgule avec la première prise.
    """
    chunks, pos = [], 0
    for m in _WORD_RE.finditer(text):
        if chunks:
            chunks[-1][1] = text[pos:m.start()]
        chunks.append([m.group(0), ""])
        pos = m.end()
    if chunks:
        chunks[-1][1] = text[pos:]
    return chunks


def _drop_fillers(chunks):
    out = []
    for word, trail in chunks:
        k = _key(word)
        drop = (
            bool(_FILLER_RE.match(k))
            or k == "bah"
            or (k == "ben" and word.islower())       # « Ben » reste un prénom
            or (k == "hein" and "?" not in trail)    # « tu viens, hein ? » reste
        )
        if not drop:
            out.append([word, trail])
            continue
        # L'hésitation portait la fin de phrase : on récupère sa ponctuation.
        final = "".join(c for c in trail if c in ".!?…")
        if final and out:
            out[-1][1] = out[-1][1].rstrip() + final + " "
    return out


def _fix_stutters(chunks):
    """« je-je-je » → « je », « l- le » → « le »."""
    out = []
    for idx, (word, trail) in enumerate(chunks):
        if "-" in word:
            parts = [p for p in word.split("-") if p]
            low = [p.lower() for p in parts]
            if len(parts) > 1 and len(set(low)) == 1:
                word = parts[-1]
            elif (
                len(parts) == 2
                and len(low[0]) <= 2
                and len(low[1]) > len(low[0])
                and low[1].startswith(low[0])
            ):
                word = parts[-1]
        nxt = _key(chunks[idx + 1][0]) if idx + 1 < len(chunks) else ""
        cur = _key(word)
        if (
            trail.lstrip().startswith("-")
            and 0 < len(cur) <= 2
            and len(nxt) > len(cur)
            and nxt.startswith(cur)
        ):
            continue
        out.append([word, trail])
    return out


def _ends_sentence(trail: str) -> bool:
    return any(c in trail for c in ".!?…")


def _drop_abandoned(chunks):
    """« Alors je voulais dire que non, pardon. Est-ce que… » → l'amorce saute.

    Ici rien ne se ressemble entre les deux phrases : le locuteur n'a pas repris
    son idée, il en a changé. Le seul indice est le marqueur d'auto-correction
    qui termine la phrase abandonnée. On exige deux marqueurs enchaînés — un
    seul, « je crois que non. », est une réponse et pas une amorce — la fin de
    phrase juste derrière, et une phrase qui suit.
    """
    keys = [_key(c[0]) for c in chunks]
    n = len(keys)
    dropped = [False] * n
    i = 0
    while i < n:
        if keys[i] not in _REPAIR_WORDS:
            i += 1
            continue
        j = i
        while j < n and keys[j] in _REPAIR_WORDS:
            j += 1
        if j - i >= 2 and j < n and _ends_sentence(chunks[j - 1][1]):
            start = i
            while start > 0 and not _ends_sentence(chunks[start - 1][1]):
                start -= 1
            if j - start <= _MAX_ABANDONED:
                for k in range(start, j):
                    dropped[k] = True
        i = j
    return [c for k, c in enumerate(chunks) if not dropped[k]]


def _repairs_at(keys, j, aggressive):
    """Longueurs de marqueur d'auto-correction présentes à l'indice j (plus long d'abord)."""
    if aggressive:
        for phrase in _REPAIR_PHRASES:
            if tuple(keys[j:j + len(phrase)]) == phrase:
                yield len(phrase)
        b = 0
        while b < 3 and j + b < len(keys):
            if keys[j + b] in _REPAIR_WORDS:
                b += 1
            elif keys[j + b] == "ou" and keys[j + b + 1:j + b + 2] == ["plutot"]:
                b += 1   # « ou » n'est un marqueur que devant « plutôt »
            else:
                break
        for length in range(b, 0, -1):
            yield length
    yield 0


def _find_retake(keys, i, same, aggressive):
    """Si keys[i:] commence par une prise abandonnée, renvoie (indice de reprise, floue ?).

    Une prise A = keys[i:i+L] est abandonnée si elle est immédiatement suivie
    (éventuellement via un marqueur « enfin », « non pardon »...) d'une prise B
    qui commence par le même mot et lui ressemble assez. Une reprise à
    l'identique est certaine ; une reprise approximative est signalée « floue »
    pour que l'appelant puisse plafonner ce qu'elles retirent au total.
    """
    n = len(keys)
    cands = same.get(keys[i])
    if not cands:
        return None
    near = {k for k in cands if i < k <= i + _MAX_GAP}
    if not near:
        return None
    for L in range(min(_MAX_RUN if aggressive else 3, n - i - 1), 0, -1):
        j = i + L
        a = keys[i:j]
        for b in _repairs_at(keys, j, aggressive):
            k = j + b
            if k not in near:
                continue
            if keys[k:k + L] == a:                       # reprise à l'identique
                if L == 1 and b == 0 and keys[i] in _KEEP_DOUBLE:
                    continue
                return k, False
            if not aggressive or L < 3:
                continue
            if b == 0 and a[-1] in _COORD:               # « ... et je vais ... »
                continue
            for m in range(L, L + 4):
                if k + m > n:
                    break
                # Un marqueur d'auto-correction est déjà une preuve : seuil bas.
                # Sinon on exige que la reprise soit plus longue (le locuteur
                # complète), ou une quasi-identité sur une longue prise.
                thr = 0.60 if b else (0.72 if m > L else 0.86)
                if difflib.SequenceMatcher(None, a, keys[k:k + m]).ratio() >= thr:
                    return k, True
    return None


def _dedupe(chunks, aggressive):
    keys = [_key(c[0]) for c in chunks]
    same = {}
    for idx, k in enumerate(keys):
        same.setdefault(k, []).append(idx)
    out, i, n, fuzzy = [], 0, len(keys), 0
    while i < n:
        found = _find_retake(keys, i, same, aggressive)
        if found is not None and found[0] > i:
            k, approx = found
            if approx:
                fuzzy += k - i
            i = k                      # chunks[i:k] : prise abandonnée, jetée
            continue
        out.append(chunks[i])
        i += 1
    return out, fuzzy


def _polish(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\s+([,.])", r"\1", text)          # pas d'espace avant , et .
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r",\s*(?=[.!?;:])", "", text)
    text = re.sub(r"(?<=[.!?])\s*,", "", text)
    text = re.sub(r"([.!?…])([A-Za-zÀ-ÖØ-öø-ÿŒœ])", r"\1 \2", text)
    text = re.sub(r"^[\s,;:.…\-]+", "", text)
    text = re.sub(r"[\s,;:]+$", "", text)
    text = re.sub(
        r"([.!?…]\s+)([a-zà-öø-ÿœ])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )
    text = text.strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _pipeline(text: str, aggressive: bool):
    """Renvoie (texte nettoyé, nombre de mots retirés par une reprise approximative)."""
    chunks = _split(text)
    if not chunks:
        return "", (0, 0)
    total = len(chunks)
    chunks = _drop_fillers(chunks)
    if not chunks:
        return "", (0, 0)
    chunks = _fix_stutters(chunks)
    if aggressive:
        # Hors du budget des reprises floues : cette règle a ses propres verrous
        # (deux marqueurs, fin de phrase, 12 mots au plus).
        chunks = _drop_abandoned(chunks)
        if not chunks:
            return "", (0, 0)
    chunks, fuzzy = _dedupe(chunks, aggressive)
    return _polish("".join(w + t for w, t in chunks)), (fuzzy, total)


_EN_HINTS = {"the", "and", "is", "are", "you", "to", "of", "it", "have", "with", "for", "this", "that", "we", "i"}
_FR_HINTS = {"le", "la", "les", "et", "est", "je", "tu", "de", "des", "un", "une", "pour", "que", "on", "il", "ca"}


def to_digits(text: str) -> str:
    """« vingt-trois » → « 23 », « quinze pour cent » → « 15 % ».

    Les petits nombres isolés (« un chat », « deux fois ») restent en lettres :
    text2num ne les convertit pas, ce qui évite de casser les articles.
    """
    try:
        from text_to_num import alpha2digit
    except ImportError:   # installation antérieure sans text2num : on ne touche à rien
        return text
    keys = [_key(w) for w in _WORD_RE.findall(text)]
    # Une seule langue par dictée : en français « cent », en anglais « cents »
    # ne veulent pas dire la même chose.
    lang = "en" if sum(k in _EN_HINTS for k in keys) > sum(k in _FR_HINTS for k in keys) else "fr"
    out = re.sub(r"(\d)ème\b", r"\1e", alpha2digit(text, lang))   # « 3ème » → « 3e »
    return re.sub(r"(\d)\s*(?:pour 100|percent|per cent)\b", r"\1 %" if lang == "fr" else r"\1%", out)


def cleanup_rules(text: str) -> str:
    """Nettoyage déterministe du texte dicté (~1 ms). Ne réécrit rien, hormis les nombres en chiffres."""
    text = (text or "").strip()
    if not text:
        return ""
    out, (fuzzy, total) = _pipeline(text, aggressive=True)
    # Garde-fou : les reprises à l'identique sont certaines et peuvent retirer
    # la moitié du texte sans risque. Les reprises approximatives, elles, ont un
    # budget : une seule (8 mots au plus) passe toujours, mais une règle qui
    # s'emballe sur un long texte fait repasser le tout en conservateur.
    if total and fuzzy > max(_MAX_RUN, 0.35 * total):
        out = _pipeline(text, aggressive=False)[0]
    return to_digits(out)


# --------------------------------------------------------------- passe IA ----

def _vocab_keys(vocab) -> set:
    """Tous les mots du dictionnaire perso, normalisés."""
    return {_key(t) for v in (vocab or []) for t in _WORD_RE.findall(str(v))}


def _removable(word: str, prev_kept: str, next_kept: str) -> bool:
    """Un mot que le LLM a le droit de supprimer.

    Hésitation, marqueur d'auto-correction, ou doublon collé à un mot gardé.
    Tout le reste est du contenu : « je vais au bureau et [je vais] au marché »
    ou « nous [nous] sommes vus » doivent faire rejeter la sortie.
    """
    if _FILLER_RE.match(word) or word in _REPAIR_WORDS or word in ("bah", "ben", "hein"):
        return True
    if word in _KEEP_DOUBLE:
        return False
    return word == prev_kept or word == next_kept


def _guard_ok(output: str, source: str, allowed: set) -> bool:
    """Le LLM n'a le droit que de reponctuer, recasser et retirer du bruit.

    On aligne les deux suites de mots : toute insertion, toute suppression d'un
    mot de contenu, toute substitution qui n'est pas une variante proche (accent,
    accord, casse) veut dire que le modèle a réécrit la dictée — on le jette.
    """
    src = [_key(w) for w in _WORD_RE.findall(source)]
    out = [_key(w) for w in _WORD_RE.findall(output)]
    if not out:
        return False
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, src, out).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            if not all(w in allowed for w in out[j1:j2]):
                return False
        elif tag == "delete":
            run = src[i1:i2]
            # Amorce abandonnée : la coupe se termine sur un marqueur
            # (« ... que non, pardon »). Le LLM a le droit de la jeter en bloc.
            if run[-1] in _REPAIR_WORDS and len(run) <= _MAX_ABANDONED:
                continue
            prev = src[i1 - 1] if i1 else ""
            nxt = src[i2] if i2 < len(src) else ""
            if not all(w in allowed or _removable(w, prev, nxt) for w in src[i1:i2]):
                return False
        else:  # replace
            a, b = src[i1:i2], out[j1:j2]
            if len(a) != len(b):
                return False
            for x, y in zip(a, b):
                if x == y or y in allowed:
                    continue
                if len(y) >= 3 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.8:
                    continue
                return False
    return True


class Cleaner:
    """Charge Qwen3-1.7B-4bit à la première utilisation (~1 Go RAM)."""

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def preload(self):
        try:
            self._load()
        except Exception:
            pass

    def _load(self):
        with self._lock:
            if self._model is None:
                from mlx_lm import load

                self._model, self._tokenizer = load(LLM_MODEL_ID)

    def clean(self, text: str, tone: str = "neutral", vocab=None) -> str:
        """vocab : liste de mots/noms propres à respecter (dictionnaire perso)."""
        text = text.strip()
        if not text:
            return text
        # Les règles font déjà les hésitations, répétitions et reprises ;
        # le LLM ne s'occupe que de ce qui demande de comprendre la phrase.
        pre = cleanup_rules(text)
        if not pre:
            return pre
        text = pre
        vocab = list(vocab or [])
        try:
            self._load()
            from mlx_lm import generate

            system = SYSTEM_PROMPT
            hint = TONE_HINTS.get(tone, "")
            if hint:
                system += " " + hint
            if vocab:
                system += " Noms propres et termes à orthographier exactement ainsi : " + ", ".join(vocab[:60]) + "."
            messages = [{"role": "system", "content": system}]
            for raw, cleaned in FEW_SHOT:
                messages.append({"role": "user", "content": raw})
                messages.append({"role": "assistant", "content": cleaned})
            messages.append({"role": "user", "content": text})
            try:
                prompt = self._tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=False,
                    enable_thinking=False,
                )
            except TypeError:
                # Tokenizer sans support enable_thinking
                prompt = self._tokenizer.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False
                )

            max_tokens = min(1024, len(self._tokenizer.encode(text)) * 2 + 64)
            output = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                verbose=False,
            ).strip()

            if "</think>" in output:
                output = output.split("</think>")[-1].strip()
            output = to_digits(output)   # le LLM remet volontiers les nombres en lettres

            # Garde-fous : sortie vide, taille aberrante, ou mots inventés
            # (le LLM ne doit jamais introduire de mot absent de la dictée)
            allowed = _vocab_keys(vocab)
            if "\n- " in output:   # une liste à puces remplace les « et » de l'énumération
                allowed |= {"et", "and", "puis", "then"}
            if (
                not output
                or len(output) > int(1.5 * len(text)) + 40
                or len(output) < int(0.5 * len(text))
                or not _guard_ok(output, text, allowed)
            ):
                return text
            return output
        except Exception:
            return text
