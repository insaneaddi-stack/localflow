"""Transcription Voxtral (API Mistral) en premier, Qwen3-ASR local en repli.

Pourquoi : Qwen3-ASR occupe ~1,6 Go. Sur un Mac de 16 Go bien chargé, macOS le
pousse dans le swap ; mesuré en octobre 2026, 785 « keep-warm lent » (jusqu'à
102 s) et des dictées collées 15 à 92 s après le relâchement. Voxtral répond en
~0,5 s sans rien garder en mémoire. Qwen n'est chargé qu'au premier repli.

Le repli est silencieux : erreur réseau, délai dépassé, 429, clé refusée ou
résultat vide → la même dictée part dans Qwen local. Aucune dictée n'est perdue.

L'audio part depuis la mémoire, en WAV 16 bits construit à la volée : pas de
fichier temporaire, pas de ffmpeg (absent du PATH sous launchd).
"""

import io
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

URL = "https://api.mistral.ai/v1/audio/transcriptions"
MODEL = "voxtral-mini-latest"
KEYCHAIN_SERVICE = "localflow-mistral"
SAMPLE_RATE = 16000
TIMEOUT_S = 6.0            # dictée courte ; on ajoute du temps selon la longueur de l'audio
TIMEOUT_PER_AUDIO_S = 0.15  # envoi (~32 Ko/s d'audio) + décodage côté serveur
TIMEOUT_MAX_S = 60.0
COOLDOWN_S = 60.0          # après une panne réseau ou un 429 : local direct pendant 1 min
BIAS_MAX = 100             # termes de vocabulaire envoyés (context_bias)

_key = None


def api_key():
    """Clé Mistral : trousseau macOS (service localflow-mistral), sinon $MISTRAL_API_KEY."""
    global _key
    if _key is None:
        try:
            out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                 capture_output=True, text=True, timeout=5)
            _key = out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            _key = ""
        _key = _key or os.environ.get("MISTRAL_API_KEY", "").strip()
    return _key


def wav_bytes(audio):
    """float32 mono 16 kHz → fichier WAV PCM 16 bits, en mémoire."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def bias_terms(prompt):
    """« AUR'IA, Wispr Flow, Noto. » → ["AUR'IA", "Wispr", "Flow", "Noto"].

    L'API refuse un terme qui contient une virgule ou une espace : les noms en
    plusieurs mots sont envoyés mot par mot."""
    out = []
    for t in re.split(r"[,\s]+", prompt or ""):
        t = t.strip().rstrip(".")
        if t and t not in out:
            out.append(t)
    return out[:BIAS_MAX]


def _multipart(fields, audio_wav):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictee.wav"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + audio_wav + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


def transcribe_api(audio, prompt="", language="", key=None, timeout=None):
    """Texte Voxtral. Lève en cas d'erreur réseau / HTTP / délai dépassé.

    language vide = détection automatique : mesuré, aussi juste que « fr » sur
    du français, et ne déforme pas l'anglais."""
    key = key or api_key()
    if not key:
        raise RuntimeError("clé Mistral absente du trousseau")
    seconds = len(audio) / SAMPLE_RATE
    if timeout is None:
        timeout = min(TIMEOUT_MAX_S, TIMEOUT_S + TIMEOUT_PER_AUDIO_S * seconds)
    fields = [("model", MODEL)]
    if language:
        fields.append(("language", language))
    fields += [("context_bias", t) for t in bias_terms(prompt)]
    boundary, body = _multipart(fields, wav_bytes(audio))
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.load(r).get("text") or "").strip()


class Engine:
    """Même interface que transcribe.Transcriber (transcribe, name, last_retry).

    `local` : fonction qui rend le Transcriber Qwen, chargé à la demande.
    `use_api` faux : tout passe en local (réglage « Moteur : local »)."""

    def __init__(self, local, log, use_api=True):
        self._local = local
        self._log = log
        self.use_api = use_api
        self.name = "voxtral"
        self.last_retry = ""
        self._cooldown_until = 0.0

    def _api_text(self, audio, prompt, language):
        """Texte Voxtral, ou None s'il faut passer en local (raison journalisée)."""
        if not self.use_api or time.time() < self._cooldown_until:
            return None
        from .transcribe import _is_hallucination, _recites_context

        t0 = time.time()
        try:
            text = transcribe_api(audio, prompt, language)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                self._cooldown_until = time.time() + COOLDOWN_S
            self._log(f"voxtral : HTTP {exc.code} → repli local")
            return None
        except Exception as exc:   # réseau coupé, DNS, délai dépassé, clé absente
            self._cooldown_until = time.time() + COOLDOWN_S
            self._log(f"voxtral : {type(exc).__name__} après {time.time() - t0:.1f}s → repli local")
            return None
        if len(text.split()) <= 4 and _is_hallucination(text):
            text = ""
        if text and _recites_context(text, " ".join(bias_terms(prompt))):
            text = ""
        if not text:
            self._log("voxtral : résultat vide → repli local")
            return None
        return text

    def transcribe(self, audio, prompt="", language=""):
        text = self._api_text(audio, prompt, language)
        if text is not None:
            self.name, self.last_retry = "voxtral", ""
            return text
        local = self._local()
        text = local.transcribe(audio, prompt=prompt, language=language)
        self.name = local.name + (" (repli)" if self.use_api else "")
        self.last_retry = local.last_retry
        return text
