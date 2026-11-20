"""Transcription Voxtral (API Mistral), seul moteur de dictée.

Pas de repli local : Qwen3-ASR occupait ~1,6 Go et faisait swapper le Mac (octobre 2026 :
785 « keep-warm lent », dictées collées 15 à 92 s après le relâchement). Voxtral répond
en ~0,5 s sans rien garder en mémoire.

Un 429, un 5xx, une panne réseau ou un résultat vide sur une vraie dictée sont redemandés
(RETRY_PAUSES_S, dans la limite de RETRY_BUDGET_S). Si Voxtral ne répond toujours pas,
l'exception remonte : l'appli garde l'audio (perdues/) au lieu de le jeter.

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
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
GROQ_KEYCHAIN_SERVICE = "localflow-groq"
MODELS_URL = "https://api.mistral.ai/v1/models"   # GET gratuit : sert à vérifier une clé
SAMPLE_RATE = 16000
TIMEOUT_S = 6.0            # dictée courte ; on ajoute du temps selon la longueur de l'audio
TIMEOUT_PER_AUDIO_S = 0.15  # envoi (~32 Ko/s d'audio) + décodage côté serveur
TIMEOUT_MAX_S = 60.0
RETRY_PAUSES_S = (1.5, 4.0, 8.0, 15.0)   # pauses avant chaque nouvel essai
RETRY_BUDGET_S = 45.0      # on n'attend pas plus longtemps que ça avant d'abandonner (audio gardé)
RETRY_AFTER_MAX_S = 20.0   # plafond de l'attente demandée par l'API (en-tête Retry-After)
LATER_PAUSES_S = (60.0, 300.0, 900.0)   # dictée perdue : nouvel essai 1, 5 puis 15 min plus tard
EMPTY_MIN_S = 5.0          # sous cette durée, un résultat vide est un silence ; au-dessus, un échec
BIAS_MAX = 100             # termes de vocabulaire envoyés (context_bias)

_key = None
_key_looked = 0.0
KEY_RECHECK_S = 2.0   # sans clé, on relit le trousseau au plus toutes les 2 s : l'installateur ou le menu peut l'avoir ajoutée


class MissingKey(RuntimeError):
    """Pas de clé Mistral : redemander ne sert à rien, il faut la saisir (menu « Clé Voxtral… »)."""


def api_key():
    """Clé Mistral : trousseau macOS (service localflow-mistral), sinon $MISTRAL_API_KEY."""
    global _key, _key_looked
    if _key or (_key is not None and time.time() - _key_looked < KEY_RECHECK_S):
        return _key
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=5)
        _key = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        _key = ""
    _key = _key or os.environ.get("MISTRAL_API_KEY", "").strip()
    _key_looked = time.time()
    return _key


def looks_like_key(text):
    """Une clé Mistral : lettres, chiffres, « _ » ou « - » (ex. mstrl_…), sans espace."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{20,120}", (text or "").strip()))


def check_key(key, timeout=8.0):
    """True si Mistral accepte la clé, False si elle est refusée, None si on n'a pas pu vérifier (réseau)."""
    req = urllib.request.Request(MODELS_URL, headers={"Authorization": f"Bearer {key.strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code in (401, 403) else None
    except Exception:
        return None


def save_key(key):
    """Range la clé dans le trousseau macOS (jamais dans un fichier) et l'utilise tout de suite."""
    global _key
    key = key.strip()
    out = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-a", "localflow",
                          "-s", KEYCHAIN_SERVICE, "-w", key], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError("trousseau : " + out.stderr.strip()[:120])
    _key = key


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


def groq_key():
    """Clé Groq : trousseau macOS (service localflow-groq), sinon $GROQ_API_KEY. Vide = Voxtral seul."""
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", GROQ_KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=5)
        k = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        k = ""
    return k or os.environ.get("GROQ_API_KEY", "").strip()


def looks_like_groq_key(text):
    """Une clé Groq commence par gsk_ (ex. gsk_AbC…), sans espace."""
    return bool(re.fullmatch(r"gsk_[A-Za-z0-9]{20,120}", (text or "").strip()))


def check_groq_key(key, timeout=8.0):
    """True si Groq accepte la clé, False si refusée, None si on n'a pas pu vérifier (réseau)."""
    req = urllib.request.Request("https://api.groq.com/openai/v1/models",
                                 headers={"Authorization": f"Bearer {key.strip()}", "User-Agent": "LocalFlow/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code == 401 else None
    except Exception:
        return None


def save_groq_key(key):
    """Range la clé Groq dans le trousseau macOS (jamais dans un fichier)."""
    out = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-a", "localflow",
                          "-s", GROQ_KEYCHAIN_SERVICE, "-w", key.strip()], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError("trousseau : " + out.stderr.strip()[:120])


def groq_api(audio, prompt, language, key, timeout):
    """Texte Whisper chez Groq (gratuit, 2 000 dictées/jour). Le vocabulaire passe en « prompt » (Whisper)."""
    fields = [("model", GROQ_MODEL), ("temperature", "0")]
    if language:
        fields.append(("language", language))
    if prompt:
        fields.append(("prompt", ", ".join(bias_terms(prompt))[:600]))
    boundary, body = _multipart(fields, wav_bytes(audio))
    req = urllib.request.Request(GROQ_URL, data=body, method="POST", headers={
        "User-Agent": "LocalFlow/1.0",   # sans ça, Cloudflare (devant Groq) refuse « Python-urllib » : 403, code 1010
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.load(r).get("text") or "").strip()


def transcribe_api(audio, prompt="", language="", key=None, timeout=None):
    """Texte : Groq (gratuit) d'abord, Voxtral en secours (Mistral a coupé ses clés gratuites le 20/11/2026).
    Lève en cas d'erreur réseau / HTTP / délai dépassé.

    language vide = détection automatique : mesuré, aussi juste que « fr » sur
    du français, et ne déforme pas l'anglais."""
    seconds = len(audio) / SAMPLE_RATE
    if timeout is None:
        timeout = min(TIMEOUT_MAX_S, TIMEOUT_S + TIMEOUT_PER_AUDIO_S * seconds)
    gk = groq_key()
    if gk:
        try:
            return groq_api(audio, prompt, language, gk, timeout)
        except Exception as groq_exc:
            try:
                return _voxtral_api(audio, prompt, language, key, timeout)
            except Exception:
                raise groq_exc   # secours absent ou refusé : l'erreur de Groq remonte (Engine redemande si passagère)
    return _voxtral_api(audio, prompt, language, key, timeout)


def _voxtral_api(audio, prompt, language, key, timeout):
    key = key or api_key()
    if not key:
        raise MissingKey("clé Mistral absente du trousseau")
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


def _detail(exc):
    """HTTP 429 (Retry-After: 2) {"message": ...} : de quoi comprendre un refus au prochain passage."""
    try:
        body = exc.read(300).decode("utf-8", "replace").replace("\n", " ")
    except Exception:
        body = ""
    return f"HTTP {exc.code} (Retry-After: {exc.headers.get('Retry-After')}) {body}".strip()


class Engine:
    """Même interface que transcribe.Transcriber (transcribe, name, last_retry)."""

    use_api = True
    name = "voxtral"
    last_retry = ""

    def __init__(self, log):
        self._log = log

    def transcribe(self, audio, prompt="", language="", empty_ok=False):
        """empty_ok : un vide est accepté tout de suite (fin silencieuse d'une longue dictée déjà transcrite).
        Texte Voxtral ; lève RuntimeError si l'API n'a pas répondu dans RETRY_BUDGET_S."""
        from .transcribe import _is_hallucination, _recites_context

        seconds = len(audio) / SAMPLE_RATE
        debut = time.time()
        raison = ""
        for n in range(len(RETRY_PAUSES_S) + 1):
            pause = 0.0
            if n:
                pause = RETRY_PAUSES_S[n - 1]
                if n > 1 and time.time() - debut + pause > RETRY_BUDGET_S:
                    break
                time.sleep(pause)
            t0 = time.time()
            try:
                text = transcribe_api(audio, prompt, language)
            except urllib.error.HTTPError as exc:
                raison = _detail(exc)
                self._log(f"voxtral : {raison}")
                if exc.code in (400, 422) and prompt:
                    prompt = ""   # coup de pouce refusé : on renvoie sans, la dictée ne se perd jamais à cause de lui
                    self._log("voxtral : vocabulaire refusé, renvoi sans")
                    continue
                if exc.code != 429 and exc.code < 500:
                    break   # clé refusée, requête invalide : redemander ne change rien
                try:
                    retry_after = min(float(exc.headers.get("Retry-After")), RETRY_AFTER_MAX_S)
                except (TypeError, ValueError):
                    retry_after = 0.0
                suivante = RETRY_PAUSES_S[n] if n < len(RETRY_PAUSES_S) else 0.0
                if retry_after > suivante:   # l'API demande d'attendre plus que notre pause
                    time.sleep(retry_after - suivante)
                continue
            except MissingKey:
                raise
            except Exception as exc:   # réseau coupé, DNS, délai dépassé
                raison = f"{type(exc).__name__} après {time.time() - t0:.1f}s"
                self._log(f"voxtral : {raison}")
                continue
            if len(text.split()) <= 4 and _is_hallucination(text):
                text = ""
            if text and _recites_context(text, " ".join(bias_terms(prompt))):
                text = ""
            if text or seconds < EMPTY_MIN_S or empty_ok:
                if n:
                    self._log(f"voxtral : réponse obtenue au {n + 1}e essai")
                return text
            raison = "résultat vide"
            self._log(f"voxtral : résultat vide pour {seconds:.0f}s d'audio")
        raise RuntimeError(f"Voxtral n'a pas répondu ({raison})")


def retry_later(engine, audio, prompt=""):
    """Dictée que Voxtral n'a pas pu transcrire : on la redemande plus tard (réseau revenu,
    quota reposé). Texte, ou None si Voxtral ne répond toujours pas (l'audio reste gardé)."""
    for pause in LATER_PAUSES_S:
        time.sleep(pause)
        try:
            return engine.transcribe(audio, prompt=prompt)
        except Exception:
            continue
    return None
