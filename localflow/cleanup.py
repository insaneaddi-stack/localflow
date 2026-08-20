"""Nettoyage du texte dicté : LLM léger (Qwen3-1.7B 4-bit) avec fallback regex."""

import difflib
import re
import threading

LLM_MODEL_ID = "mlx-community/Qwen3-1.7B-4bit"

SYSTEM_PROMPT = (
    "Tu nettoies des transcriptions de dictée vocale (français ou anglais). "
    "Supprime toutes les hésitations (euh, hum, um, uh...), les faux départs, "
    "les tics de langage inutiles et les mots répétés par erreur. "
    "Corrige la ponctuation. Garde tous les autres mots exactement tels quels : "
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
        "Ok donc premier point, on valide le budget. Deuxième point, il faut que je "
        "rappelle le client. Et voilà, on fait le point vendredi.",
    ),
]

_FILLERS = re.compile(
    r"\b(euh+|heu+|hum+|hem+|uh+|um+|uhm+|erm+|mmh+)\b[,.]?\s*", re.IGNORECASE
)


# Répétitions légitimes à ne PAS dédoublonner (« nous nous sommes », « très très »)
_KEEP_DOUBLE = {
    "nous", "vous", "on", "en", "ça", "là", "très", "bien", "tout", "plus",
    "non", "oui", "vite", "doucement", "petit", "très", "had", "that", "is",
    "very", "so", "no", "yes", "really",
}

def _dedupe(match):
    word = match.group(1)
    return match.group(0) if word.lower() in _KEEP_DOUBLE else word

def cleanup_rules(text: str) -> str:
    """Nettoyage minimal par règles (utilisé si le LLM est désactivé ou échoue)."""
    text = _FILLERS.sub("", text)
    text = re.sub(r"\b(\w{2,})( \1\b)+", _dedupe, text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = text.strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _words(text: str) -> set:
    """Mots normalisés (sans casse, apostrophes ni ponctuation) pour le garde-fou."""
    text = text.lower().replace("'", " ").replace("’", " ")
    return set(re.findall(r"[a-zà-ÿœæ0-9]+", text))

def _guard_ok(output: str, source: str, allowed: set) -> bool:
    """Chaque mot de sortie doit venir de la source, du dictionnaire, ou être une
    variante proche d'un mot source (accord, accent). Sinon le LLM a inventé."""
    src = _words(source) | allowed
    for w in _words(output):
        if w in src or w.isdigit():
            continue
        if len(w) >= 4 and difflib.get_close_matches(w, src, n=1, cutoff=0.8):
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
        # Les règles retirent déjà les « euh »/« um » de façon fiable ;
        # le LLM ne s'occupe que des faux départs, répétitions et ponctuation.
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

            # Garde-fous : sortie vide, taille aberrante, ou mots inventés
            # (le LLM ne doit jamais introduire de mot absent de la dictée)
            allowed = set()
            for v in vocab:
                allowed |= _words(v)
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
