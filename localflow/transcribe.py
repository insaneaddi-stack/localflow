"""Transcription locale via Parakeet TDT 0.6B v3 (MLX).

L'audio est passé au modèle directement depuis la mémoire : pas de fichier
temporaire, et surtout pas de dépendance à ffmpeg (absent du PATH sous launchd).
"""

import threading

import mlx.core as mx
import numpy as np
import parakeet_mlx.parakeet as _pk

MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"
SAMPLE_RATE = 16000
CHUNK_S = 120.0  # découpe les longs enregistrements (mains-libres)

_MEM_PREFIX = "localflow-mem-"  # sans "/" : Path() normaliserait la clé
_buffers = {}
_buffers_lock = threading.Lock()
_orig_load_audio = _pk.load_audio

def _load_audio(path, sample_rate, dtype):
    """Remplace parakeet_mlx.parakeet.load_audio : sert nos buffers mémoire,
    délègue à l'original (ffmpeg) pour de vrais fichiers."""
    key = str(path)
    with _buffers_lock:
        buf = _buffers.get(key)
    if buf is not None:
        return mx.array(buf).astype(mx.float32)  # comme l'original : toujours float32
    return _orig_load_audio(path, sample_rate, dtype)

_pk.load_audio = _load_audio

class Transcriber:
    name = "parakeet"

    def __init__(self):
        from parakeet_mlx import from_pretrained

        self.model = from_pretrained(MODEL_ID)

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> str:
        """Transcrit un buffer float32 mono 16 kHz. Détection FR/EN automatique."""
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        key = f"{_MEM_PREFIX}{id(pcm)}-{threading.get_ident()}"
        with _buffers_lock:
            _buffers[key] = pcm
        try:
            result = self.model.transcribe(key, chunk_duration=CHUNK_S)
            return result.text.strip()
        finally:
            with _buffers_lock:
                _buffers.pop(key, None)


class StreamSession:
    """Transcription en direct pendant l'enregistrement.

    Utilisation (depuis UN seul thread) :
        s = StreamSession(transcriber)
        s.feed(chunk_float32) ; s.text  → texte provisoire
        final = s.finish()              → texte final, libère le modèle
    Le modèle bascule en attention locale pendant la session : ne pas appeler
    transcribe() en parallèle (le worker est de toute façon bloqué par _busy).
    """

    CONTEXT = (256, 256)

    def __init__(self, transcriber: "Transcriber"):
        self._ctx = transcriber.model.transcribe_stream(context_size=self.CONTEXT, depth=1)
        self._stream = self._ctx.__enter__()
        self._pending = np.zeros(0, dtype=np.float32)
        self.text = ""
        self._closed = False

    MIN_FEED = SAMPLE_RATE // 2  # 0,5 s : bon compromis latence / coût GPU

    def feed(self, chunk: np.ndarray):
        if self._closed:
            return
        self._pending = np.concatenate([self._pending, np.asarray(chunk, dtype=np.float32)])
        if len(self._pending) >= self.MIN_FEED:
            self._stream.add_audio(mx.array(self._pending))
            self._pending = np.zeros(0, dtype=np.float32)
            self.text = self._stream.result.text.strip()

    def finish(self) -> str:
        if self._closed:
            return self.text
        try:
            # Le reste + 1 s de silence pour forcer la finalisation des derniers mots
            tail = np.concatenate([self._pending, np.zeros(SAMPLE_RATE, dtype=np.float32)])
            self._stream.add_audio(mx.array(tail))
            self.text = self._stream.result.text.strip()
        finally:
            self._closed = True
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass
        return self.text

    def abort(self):
        if not self._closed:
            self._closed = True
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass


WHISPER_MODEL_ID = "mlx-community/whisper-large-v3-turbo"
WHISPER_MIN_FRAMES = 1500   # contexte audio minimal (15 s) ; plus court = risque de coupures
WHISPER_MARGIN_FRAMES = 300 # marge après la fin de la parole (3 s)
LANG_DETECT_S = 8.0         # détection FR/EN sur les premières secondes seulement
ALLOWED_LANGS = ("fr", "en")

def _patch_mlx_whisper():
    """Deux optimisations (idée « audio_ctx » de whisper.cpp) :
    - l'encodeur accepte un contexte plus court que 30 s ;
    - le padding s'adapte à la durée réelle au lieu de 30 s fixes.
    Gain : ~1,7 s → ~0,45 s par dictée sur M4, sans perte mesurée."""
    import sys
    import mlx.nn as nn
    import mlx_whisper.whisper as W
    from mlx_whisper.audio import N_FRAMES

    T = sys.modules["mlx_whisper.transcribe"]
    if getattr(T, "_localflow_patched", False):
        return

    def enc_call(self, x):
        x = nn.gelu(self.conv1(x))
        x = nn.gelu(self.conv2(x))
        x = x + self._positional_embedding[: x.shape[1]]
        for block in self.blocks:
            x, _, _ = block(x)
        return self.ln_post(x)

    W.AudioEncoder.__call__ = enc_call
    orig = T.pad_or_trim

    def pad_dyn(array, length=N_FRAMES, *, axis=-1):
        if length == N_FRAMES and axis == -2:
            n = array.shape[axis]
            target = min(N_FRAMES, max(WHISPER_MIN_FRAMES, n + WHISPER_MARGIN_FRAMES))
            target += target % 2
            return orig(array, target, axis=axis)
        return orig(array, length, axis=axis)

    T.pad_or_trim = pad_dyn
    T._localflow_patched = True

class WhisperTranscriber:
    """Whisper large-v3-turbo (MLX) : plus précis en français que Parakeet,
    et accepte un contexte (dictionnaire perso) qui oriente l'orthographe."""

    name = "whisper"

    def __init__(self):
        import mlx_whisper
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        _patch_mlx_whisper()
        self._mw = mlx_whisper
        self._mx = mx
        self._model = ModelHolder.get_model(WHISPER_MODEL_ID, mx.float16)
        # chauffe (compilation des kernels) : une détection + une transcription
        self.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))

    def detect_language(self, pcm: np.ndarray) -> str:
        """FR ou EN, sur les premières secondes, avec un contexte minuscule (~0,1 s)."""
        from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim

        try:
            head = pcm[: int(SAMPLE_RATE * LANG_DETECT_S)]
            mel = log_mel_spectrogram(head, n_mels=self._model.dims.n_mels)
            n = mel.shape[-2] + 50
            n += n % 2
            seg = pad_or_trim(mel, max(500, n), axis=-2).astype(self._mx.float16)
            _, probs = self._model.detect_language(seg)
            best = max(ALLOWED_LANGS, key=lambda l: probs.get(l, 0.0))
            return best
        except Exception:
            return "fr"

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> str:
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        lang = self.detect_language(pcm)
        kwargs = dict(
            path_or_hf_repo=WHISPER_MODEL_ID,
            language=lang,
            without_timestamps=True,          # moins de tokens à générer
            temperature=0.0,
            condition_on_previous_text=False,  # évite les boucles/hallucinations
            compression_ratio_threshold=2.4,
        )
        if prompt:
            kwargs["initial_prompt"] = prompt[:600]
        result = self._mw.transcribe(pcm, **kwargs)
        segs = result.get("segments") or []
        if not segs:
            return result.get("text", "").strip()
        # Sur le silence/bruit, whisper-turbo « invente » (« Thank you. », « you »…)
        # avec no_speech_prob=0 : on filtre sur la confiance et une liste noire.
        kept = []
        for seg in segs:
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            short = len(txt.split()) <= 2
            # faible confiance : on ne jette QUE les bribes (jamais une phrase entière)
            if short and seg.get("avg_logprob", 0.0) < -0.9:
                continue
            if short and _is_hallucination(txt):
                continue
            kept.append(txt)
        return " ".join(kept).strip()

_HALLUCINATIONS = {
    "thank you", "thanks", "you", "bye", "thank you for watching", "thanks for watching",
    "merci", "merci d'avoir regardé", "merci à tous", "au revoir", "sous-titres", "sous-titrage",
    "sous-titres réalisés par la communauté d'amara.org", "sous-titrage société radio-canada",
    "amara.org", "abonnez-vous", "à bientôt", "oh", "hmm", "um", "uh", "ah",
}

def _is_hallucination(txt: str) -> bool:
    import re
    norm = re.sub(r"[^\w\s']", "", txt.lower()).strip()
    if not norm:
        return True
    if norm in _HALLUCINATIONS:
        return True
    if len(norm.split()) <= 4 and any(norm.startswith(h) for h in ("sous-titr", "thank", "merci d'avoir", "subtitles")):
        return True
    return False
