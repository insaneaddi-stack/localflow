"""Résumé de réunion en local (Qwen3 via mlx-lm), façon Granola / Hyprnote :

- sections fixes : Résumé · Décisions · Actions · Questions ouvertes · Dates & chiffres ;
- si l'utilisateur a tapé des notes pendant la réunion, elles sont conservées telles quelles
  et enrichies à partir du transcript (« Améliorer les notes ») ;
- transcript long → map-reduce (notes partielles par tranche, puis fusion) ;
- titre court généré ; `ask()` pour poser une question sur la réunion.

Modèle par défaut : Qwen3-1.7B-4bit (déjà installé pour le nettoyage). Option 4B (~2,5 Go)
pour des résumés nettement meilleurs, téléchargé à la demande.
"""

import os
import re
import threading

MODELS = {
    "qwen-1.7b": "mlx-community/Qwen3-1.7B-4bit",
    "qwen-4b": "mlx-community/Qwen3-4B-4bit",
}
CHUNK_WORDS = 2200

SYSTEM = (
    "Tu es un assistant qui rédige des comptes rendus de réunion en français, à partir d'une "
    "transcription automatique (elle peut contenir des erreurs de reconnaissance ; corrige-les "
    "quand le sens est évident). « Moi » est l'utilisateur, « Eux » ses interlocuteurs. "
    "Sois factuel et concis : n'invente jamais rien qui ne soit pas dans la transcription, "
    "ne commente pas, ne salue pas. Réponds uniquement en Markdown avec exactement les sections demandées."
)

SUMMARY_INSTRUCTIONS = """Rédige le compte rendu avec EXACTEMENT ces sections Markdown (garde les titres tels quels, laisse « — » si une section est vide) :

## Résumé
3 à 5 phrases : de quoi il s'agissait, ce qui a été dit d'important, l'issue.

## Décisions
- Une puce par décision prise (formulation affirmative).

## Actions
- [ ] Qui · quoi · pour quand (si connu). Une puce par action. « Moi » si c'est à l'utilisateur de le faire.

## Questions ouvertes
- Points laissés en suspens, désaccords, à clarifier.

## Dates & chiffres clés
- Dates, délais, montants, quantités mentionnés (avec leur contexte en quelques mots)."""

NOTES_INSTRUCTIONS = """L'utilisateur a pris ces notes pendant la réunion :

<notes>
{notes}
</notes>

Réécris-les : garde chaque point de l'utilisateur, dans son ordre et avec ses mots (une puce par point), et complète chacun en dessous par des sous-puces avec les précisions de la transcription qui s'y rapportent (qui, quoi, quand, chiffres). N'ajoute aucun point qui ne soit pas dans ses notes, n'invente rien. Réponds uniquement avec la liste Markdown."""

PARTIAL_INSTRUCTIONS = """Voici la partie {i}/{n} d'une réunion. Extrais en Markdown, sous forme de puces courtes et factuelles : « ## Points », « ## Décisions », « ## Actions », « ## Questions », « ## Dates & chiffres ». Laisse « — » si vide."""

MERGE_INSTRUCTIONS = """Voici des notes partielles, par ordre chronologique, d'une même réunion. Fusionne-les (sans doublons, sans perdre d'information) en un compte rendu final.

""" + SUMMARY_INSTRUCTIONS

TITLE_INSTRUCTIONS = "Donne un titre court (3 à 6 mots, sans guillemets ni point final, en français) à cette réunion. Réponds uniquement avec le titre."

ASK_SYSTEM = (
    "Tu réponds à des questions sur une réunion à partir de son compte rendu et de sa transcription. "
    "Réponds en français, brièvement, en citant ce qui a été dit quand c'est utile. "
    "Si l'information n'y est pas, dis-le simplement."
)

SECTION_RE = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)


class Summarizer:
    def __init__(self, model_key="qwen-1.7b", log=None, shared=None):
        self.model_key = model_key if model_key in MODELS else "qwen-1.7b"
        self._log = log or (lambda m: None)
        self._shared = shared     # Cleaner : même Qwen 1.7B déjà chargé → pas de 2e copie en RAM
        self._model = None
        self._tok = None
        self._loaded_id = None
        self._lock = threading.Lock()

    # ---- modèle ----

    def set_model(self, key):
        if key in MODELS:
            self.model_key = key

    def _load(self):
        mid = MODELS[self.model_key]
        with self._lock:
            if self._model is None or self._loaded_id != mid:
                sh = self._shared
                if self.model_key == "qwen-1.7b" and sh is not None and getattr(sh, "_model", None) is not None:
                    self._model, self._tok, self._loaded_id = sh._model, sh._tokenizer, mid
                    return
                from mlx_lm import load
                if self.model_key != "qwen-1.7b":
                    os.environ.pop("HF_HUB_OFFLINE", None)   # téléchargement à la demande
                self._model, self._tok = load(mid)
                self._loaded_id = mid
                self._log(f"résumé : modèle {mid} chargé")

    def _chat(self, system, user, max_tokens=1400):
        self._load()
        from mlx_lm import generate
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        except TypeError:
            prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        with self._lock:
            out = generate(self._model, self._tok, prompt=prompt, max_tokens=max_tokens, verbose=False)
        if "</think>" in out:
            out = out.split("</think>")[-1]
        return out.strip()

    # ---- API ----

    def summarize(self, transcript, notes="", vocab=None):
        """transcript : texte « Moi : … / Eux : … ». Retourne le Markdown des sections."""
        transcript = (transcript or "").strip()
        if not transcript:
            return ""
        hint = ""
        if vocab:
            hint = "\nNoms propres à orthographier ainsi : " + ", ".join(list(vocab)[:60]) + "."
        md = self._summary_md(transcript, hint)
        if notes.strip():
            try:
                enriched = self._chat(SYSTEM + hint, NOTES_INSTRUCTIONS.format(notes=notes.strip())
                                      + "\n\n<transcription>\n" + " ".join(transcript.split()[:6000]) + "\n</transcription>", max_tokens=900)
                enriched = self._tidy(enriched)
                if enriched:
                    md = "## Mes notes (enrichies)\n" + enriched + "\n\n" + md
            except Exception as exc:
                self._log(f"résumé : enrichissement des notes impossible ({exc})")
        return md

    def _summary_md(self, transcript, hint):
        words = transcript.split()
        if len(words) > CHUNK_WORDS * 1.3:
            chunks = [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), CHUNK_WORDS)]
            partials = []
            for i, ch in enumerate(chunks, 1):
                self._log(f"résumé : partie {i}/{len(chunks)}")
                partials.append(self._chat(SYSTEM + hint, PARTIAL_INSTRUCTIONS.format(i=i, n=len(chunks)) + "\n\n<transcription>\n" + ch + "\n</transcription>", max_tokens=700))
            body = "\n\n---\n\n".join(partials)
            md = self._chat(SYSTEM + hint, MERGE_INSTRUCTIONS + "\n\n<notes_partielles>\n" + body + "\n</notes_partielles>")
        else:
            md = self._chat(SYSTEM + hint, SUMMARY_INSTRUCTIONS + "\n\n<transcription>\n" + transcript + "\n</transcription>")
        return self._tidy(md)

    def title(self, transcript, summary=""):
        src = (summary or transcript or "").strip()
        if not src:
            return ""
        t = self._chat(SYSTEM, TITLE_INSTRUCTIONS + "\n\n" + src[:3000], max_tokens=24)
        t = t.strip().strip('"«»').strip().rstrip(".")
        t = t.splitlines()[0] if t else ""
        return t[:60]

    def ask(self, question, transcript, summary=""):
        ctx = ""
        if summary:
            ctx += "<compte_rendu>\n" + summary + "\n</compte_rendu>\n\n"
        ctx += "<transcription>\n" + self._relevant(transcript, question) + "\n</transcription>"
        return self._chat(ASK_SYSTEM, ctx + "\n\nQuestion : " + question.strip(), max_tokens=500)

    # ---- utilitaires ----

    @staticmethod
    def _relevant(transcript, question, max_words=2500):
        """Si le transcript est long : garde les lignes qui partagent des mots avec la question."""
        lines = transcript.splitlines()
        if len(transcript.split()) <= max_words:
            return transcript
        q = {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", question.lower())}
        scored = []
        for i, ln in enumerate(lines):
            words = set(re.findall(r"[a-zà-ÿ0-9]{4,}", ln.lower()))
            scored.append((len(words & q), i))
        keep = sorted(i for _, i in sorted(scored, reverse=True)[:80])
        out, count = [], 0
        for i in keep:
            out.append(lines[i]); count += len(lines[i].split())
            if count > max_words:
                break
        return "\n".join(out)

    @staticmethod
    def _tidy(md):
        md = md.strip()
        md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md).strip()
        # compte rendu : doit commencer à la première section « ## »
        first = SECTION_RE.search(md or "")
        if first and first.start() > 0 and "## Résumé" in md:
            md = md[first.start():]
        return md

    @staticmethod
    def sections(md):
        """{'Résumé': '...', 'Décisions': '...'} à partir du Markdown."""
        out, pos = {}, [(m.group(1), m.start(), m.end()) for m in SECTION_RE.finditer(md or "")]
        for i, (name, s, e) in enumerate(pos):
            end = pos[i + 1][1] if i + 1 < len(pos) else len(md)
            out[name] = md[e:end].strip()
        return out

    @staticmethod
    def first_line(md):
        secs = Summarizer.sections(md)
        txt = secs.get("Résumé") or (md or "")
        txt = re.sub(r"[#*_>\-\[\]]+", "", txt).strip()
        return txt.split("\n")[0][:200]
