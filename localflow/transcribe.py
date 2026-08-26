"""Transcription locale via Qwen3-ASR (MLX, 4 bits).

Un seul moteur. Qwen3-ASR est un ASR bâti sur un LLM : il ponctue tout seul,
détecte la langue, et accepte un « contexte » de vocabulaire — ce qui remplace
d'un coup l'initial_prompt de Whisper et le style-prompt qu'il fallait bricoler.

L'audio est passé au modèle directement depuis la mémoire (numpy float32 mono
16 kHz) : pas de fichier temporaire, et surtout pas de dépendance à ffmpeg
(absent du PATH sous launchd).
"""

import re

import mlx.core as mx
import numpy as np

MODEL_ID = "mlx-community/Qwen3-ASR-1.7B-4bit"
SAMPLE_RATE = 16000
ALLOWED_LANGS = ("fr", "en")
CONTEXT_MAX = 700           # le contexte est un prompt système : inutile de le gaver
RETRY_MAX_TOKENS = 1024     # 2e passe quand la sortie semble tronquée


def _as_context(prompt: str) -> str:
    """Dictionnaire perso + corrections apprises → contexte de biasing Qwen3-ASR.

    Le modèle attend des termes séparés par des espaces ; l'appelant nous envoie
    une liste « a, b, c. » (héritage de l'initial_prompt Whisper)."""
    terms = [t.strip() for t in re.split(r"[,\n]", prompt or "") if t.strip()]
    return " ".join(terms).rstrip(".")[:CONTEXT_MAX]


def _looks_broken(text: str, seconds: float) -> str:
    """Renvoie la raison si la transcription semble tronquée/boucle, sinon ''."""
    words = text.split()
    if seconds >= 3.0 and len(words) < max(2, seconds * 0.9):
        return f"trop court ({len(words)} mots pour {seconds:.0f}s)"
    if len(words) >= 9:
        grams = [" ".join(words[i:i + 3]).lower() for i in range(len(words) - 2)]
        top = max(grams.count(g) for g in set(grams))
        if top >= 3:
            return "répétition en boucle"
    return ""


class Transcriber:
    """Qwen3-ASR (MLX) : précis en français, ponctue seul, et accepte un contexte
    de vocabulaire (dictionnaire perso + corrections apprises). Deuxième passe
    automatique si la sortie semble tronquée ou partie en boucle."""

    def __init__(self, model_id: str = MODEL_ID):
        from mlx_qwen3_asr import Session

        self.name = "qwen3-asr"
        self.model_id = model_id
        # Session : le modèle et le tokenizer restent à nous, pas de cache global
        # caché dans la lib (on maîtrise ce qui est chargé, et donc la RAM).
        self.session = Session(model_id, dtype=mx.float16)
        self.last_retry = ""
        self.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))  # chauffe

    def _decode(self, pcm, lang, context, max_new_tokens=None):
        result = self.session.transcribe(
            pcm,
            context=context,
            language=lang,
            max_new_tokens=max_new_tokens,
        )
        text = (result.text or "").strip()
        if len(text.split()) <= 4 and _is_hallucination(text):
            return ""
        return text

    def transcribe(self, audio: np.ndarray, prompt: str = "", language: str = "") -> str:
        """language : 'fr'/'en' pour figer la langue (réunions), sinon détection automatique."""
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        seconds = len(pcm) / SAMPLE_RATE
        lang = language if language in ALLOWED_LANGS else None
        context = _as_context(prompt)

        text = self._decode(pcm, lang, context)
        reason = _looks_broken(text, seconds) if seconds >= 1.0 else ""
        if reason:
            retry = self._decode(pcm, lang, context, max_new_tokens=RETRY_MAX_TOKENS)
            self.last_retry = reason
            if len(retry.split()) >= len(text.split()):
                text = retry
        else:
            self.last_retry = ""
        return text


class StreamSession:
    """Transcription en direct pendant l'enregistrement.

    Utilisation (depuis UN seul thread) :
        s = StreamSession(transcriber)
        s.feed(chunk_float32) ; s.text  → texte provisoire
        final = s.finish()              → texte final
    Le modèle est partagé avec le transcripteur : ne pas appeler transcribe() en
    parallèle (le worker est de toute façon bloqué par _busy).

    ⚠️ APERÇU SEULEMENT. Le décodage par blocs de Qwen3-ASR coupe les phrases aux
    frontières (« on valide. Le budget, vendredi, ») et, sur de gros blocs, réordonne
    carrément les mots. Mesuré : nettement en dessous du mode batch, qui ne met de
    toute façon que ~1 s pour 7 s d'audio. Ce texte s'affiche pendant la dictée, il
    ne doit pas être collé tel quel — voir `live_paste_fast` dans la config.
    """

    CHUNK_S = 4.0            # 2 s coupait à chaque bloc, 6 s partait en réécriture
    UNFIXED_TOKEN_NUM = 15   # garde plus de queue instable = moins de coupures figées
    MAX_CONTEXT_S = 30.0
    MIN_FEED = SAMPLE_RATE // 2  # 0,5 s : bon compromis latence / coût GPU

    def __init__(self, transcriber: "Transcriber", context: str = ""):
        self._session = transcriber.session
        self._state = self._session.init_streaming(
            context=_as_context(context),
            chunk_size_sec=self.CHUNK_S,
            unfixed_token_num=self.UNFIXED_TOKEN_NUM,
            max_context_sec=self.MAX_CONTEXT_S,
            finalization_mode="accuracy",
        )
        self._pending = np.zeros(0, dtype=np.float32)
        self.text = ""
        self._closed = False

    def feed(self, chunk: np.ndarray):
        if self._closed:
            return
        self._pending = np.concatenate([self._pending, np.asarray(chunk, dtype=np.float32)])
        if len(self._pending) >= self.MIN_FEED:
            self._state = self._session.feed_audio(self._pending, self._state)
            self._pending = np.zeros(0, dtype=np.float32)
            self.text = (self._state.text or "").strip()

    def finish(self) -> str:
        if self._closed:
            return self.text
        try:
            if len(self._pending):
                self._state = self._session.feed_audio(self._pending, self._state)
                self._pending = np.zeros(0, dtype=np.float32)
            self._state = self._session.finish_streaming(self._state)
            self.text = (self._state.text or "").strip()
        finally:
            self._closed = True
        return self.text

    def abort(self):
        self._closed = True


_HALLUCINATIONS = {
    "thank you", "thanks", "you", "bye", "thank you for watching", "thanks for watching",
    "merci", "merci d'avoir regardé", "merci à tous", "au revoir", "sous-titres", "sous-titrage",
    "sous-titres réalisés par la communauté d'amara.org", "sous-titrage société radio-canada",
    "amara.org", "abonnez-vous", "à bientôt", "oh", "hmm", "um", "uh", "ah",
}


def _is_hallucination(txt: str) -> bool:
    norm = re.sub(r"[^\w\s']", "", txt.lower()).strip()
    if not norm:
        return True
    if norm in _HALLUCINATIONS:
        return True
    if len(norm.split()) <= 4 and any(norm.startswith(h) for h in ("sous-titr", "thank", "merci d'avoir", "subtitles")):
        return True
    return False
