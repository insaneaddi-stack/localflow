"""Capture micro 16 kHz mono, flux TOUJOURS ouvert + mémoire tampon (pre-roll).

Pourquoi : ouvrir le micro à l'appui coûte ~190 ms, pendant lesquels le début
de la phrase est perdu. Ici le flux tourne en continu dans un anneau d'1 s ;
à l'appui sur fn on récupère les PREROLL_S précédentes.

AGC : gain adaptatif pour la voix lointaine (vers TARGET_RMS, max MAX_GAIN),
limiteur doux, appliqué de façon continue (le gain est déjà réglé au moment
où l'on commence à parler).
"""

import queue
import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
BLOCK = 256            # 16 ms : premier son ~70 ms après l'ouverture
RING_S = 1.5
PREROLL_S = 0.35
TARGET_RMS = 0.06
MAX_GAIN = 25.0
NOISE_FLOOR = 0.002
ATTACK = 0.6
RELEASE = 0.08
VAD_MIN_ABS = 0.0012   # ≈ -58 dBFS dans la bande voix : en dessous, jamais de la voix (AVANT le gain)
VAD_FLOOR_RATIO = 2.5  # voix = au moins 2,5× le bruit ambiant (dans la bande voix)
_VAD_BINS = slice(4, 57)  # FFT 256 pts @16 kHz : 62,5 Hz/bin → 250–3500 Hz

_HP_ALPHA = 1.0 / (1.0 + 2 * np.pi * 80.0 / SAMPLE_RATE)  # passe-haut 80 Hz, 1er ordre

def _soft_limit(x):
    return np.where(np.abs(x) > 0.8, np.sign(x) * (0.8 + 0.2 * np.tanh((np.abs(x) - 0.8) / 0.2)), x)

# ---- périphérique d'entrée par défaut, lu directement dans CoreAudio (toujours à jour) ----
_ca = None

def default_input_id():
    """AudioDeviceID du micro par défaut selon CoreAudio, 0 si inconnu. PortAudio, lui, garde une
    liste périmée après un changement (AirPods…) : on ne lui fait plus confiance pour ça."""
    global _ca
    try:
        import ctypes
        import ctypes.util
        if _ca is None:
            _ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))

        class Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]

        dev = ctypes.c_uint32(0)
        size = ctypes.c_uint32(4)
        a = Addr(int.from_bytes(b"dIn ", "big"), int.from_bytes(b"glob", "big"), 0)
        if _ca.AudioObjectGetPropertyData(1, ctypes.byref(a), 0, None, ctypes.byref(size), ctypes.byref(dev)) != 0:
            return 0
        return int(dev.value)
    except Exception:
        return 0

_open_streams = set()
_pa_lock = threading.Lock()

def reset_portaudio():
    """Réinitialise PortAudio pour qu'il relise les périphériques. Ferme d'abord TOUS nos flux
    (réinitialiser avec un flux ouvert = crash natif) ; leurs propriétaires les rouvrent."""
    with _pa_lock:
        for st in list(_open_streams):
            for op in (st.stop, st.close):
                try:
                    op()
                except Exception:
                    pass
        _open_streams.clear()
        try:
            sd._terminate()
        except Exception:
            pass
        try:
            sd._initialize()
        except Exception:
            pass

def open_input_stream(**kwargs):
    """sd.InputStream(...) démarré, avec un second essai après réinitialisation de PortAudio
    (erreur -9986 typique après un changement de périphérique)."""
    try:
        with _pa_lock:
            stream = sd.InputStream(**kwargs)
            stream.start()
    except Exception:
        reset_portaudio()
        with _pa_lock:
            stream = sd.InputStream(**kwargs)
            stream.start()
    _open_streams.add(stream)
    return stream

def close_input_stream(stream):
    if stream is None:
        return
    _open_streams.discard(stream)
    for op in (stream.stop, stream.close):
        try:
            op()
        except Exception:
            pass

class Recorder:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._device_name = None
        self._ring = np.zeros(int(RING_S * SAMPLE_RATE), dtype=np.float32)
        self._ring_pos = 0
        self._chunks = []
        self._recording = False
        self._level = 0.0
        self._gain = 1.0
        self._last_callback = 0.0
        self._hp_x = 0.0
        self._hp_y = 0.0
        self.last_used = 0.0      # dernier stop() : sert à refermer le micro après inactivité
        self._floor_hist = []     # RMS bruts récents (pour estimer le bruit ambiant)
        self.noise_floor = 0.002
        self.voiced_s = 0.0       # secondes de vraie parole dans l'enregistrement courant
        self.live_queue = None

    # ---- flux permanent ----

    def open(self):
        """Ouvre (ou ré-ouvre) le flux sur le périphérique d'entrée par défaut."""
        with self._lock:
            self._close_stream()
            self._device_id = default_input_id()
            if self._device_id and self._device_id != getattr(self, "_opened_id", None) and getattr(self, "_opened_id", None):
                reset_portaudio()   # le périphérique a changé depuis la dernière ouverture : liste à rafraîchir
            self._opened_id = self._device_id
            try:
                self._device_name = sd.query_devices(kind="input")["name"]
            except Exception:
                self._device_name = None
            self._stream = open_input_stream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=BLOCK, callback=self._callback,
            )
            self._last_callback = time.time()

    def _close_stream(self):
        stream, self._stream = self._stream, None
        close_input_stream(stream)

    def healthy(self):
        """Faux si le flux est mort, muet, ou si le périphérique par défaut a changé (AirPods…)."""
        if self._stream is None:
            return False
        if time.time() - self._last_callback > 2.0:
            return False
        if self._stream not in _open_streams:   # fermé par une réinitialisation PortAudio
            return False
        dev = default_input_id()
        if dev and dev != self._device_id:
            return False
        return True

    def stalled(self, max_gap=1.5):
        """Pendant une dictée : vrai si le micro ne livre plus rien (périphérique parti)."""
        return self._recording and time.time() - self._last_callback > max_gap

    @property
    def open_(self):
        return self._stream is not None

    # ---- callback audio ----

    def _callback(self, indata, frames, time_info, status):
        self._last_callback = time.time()
        mono = indata[:, 0].astype(np.float32)
        # passe-haut : retire grondements, souffle de ventilation, chocs sur la table
        y = np.empty_like(mono)
        px, py = self._hp_x, self._hp_y
        for i in range(len(mono)):
            py = _HP_ALPHA * (py + mono[i] - px)
            px = mono[i]
            y[i] = py
        self._hp_x, self._hp_y = px, py
        mono = y
        rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-9)
        # --- détection de voix sur le signal BRUT (avant gain), bande 250–3500 Hz ---
        spec = np.abs(np.fft.rfft(mono[:256] * np.hanning(min(256, len(mono)))))
        vrms = float(np.sqrt(np.mean(spec[_VAD_BINS] ** 2)) / 64.0 + 1e-9)
        self._floor_hist.append(vrms)
        if len(self._floor_hist) > 200:          # ≈ 3 s de blocs de 16 ms
            self._floor_hist.pop(0)
        if len(self._floor_hist) >= 20:
            self.noise_floor = float(np.percentile(self._floor_hist, 20))
        if self._recording and vrms > max(VAD_MIN_ABS, self.noise_floor * VAD_FLOOR_RATIO):
            self.voiced_s += len(mono) / SAMPLE_RATE
        if rms > NOISE_FLOOR:
            wanted = min(MAX_GAIN, max(1.0, TARGET_RMS / rms))
            speed = ATTACK if wanted < self._gain else RELEASE
            self._gain += (wanted - self._gain) * speed
        boosted = _soft_limit(mono * self._gain)
        # anneau
        n = len(boosted)
        end = self._ring_pos + n
        if end <= len(self._ring):
            self._ring[self._ring_pos:end] = boosted
        else:
            k = len(self._ring) - self._ring_pos
            self._ring[self._ring_pos:] = boosted[:k]
            self._ring[: n - k] = boosted[k:]
        self._ring_pos = end % len(self._ring)
        if self._recording:
            self._chunks.append(boosted)
            self._level = min(1.0, float(np.sqrt(np.mean(boosted ** 2))) * 6.0)
            q = self.live_queue
            if q is not None:
                q.put(boosted)

    def _preroll(self):
        n = int(PREROLL_S * SAMPLE_RATE)
        start = (self._ring_pos - n) % len(self._ring)
        if start + n <= len(self._ring):
            return self._ring[start:start + n].copy()
        return np.concatenate([self._ring[start:], self._ring[: (start + n) % len(self._ring)]])

    # ---- API enregistrement ----

    @property
    def recording(self):
        return self._recording

    def start(self, live=False):
        if self._stream is None or not self.healthy():
            self.open()          # micro fermé (mode économe) ou périphérique changé : (ré)ouverture
        with self._lock:
            if self._recording:
                return
            self.live_queue = queue.Queue() if live else None
            self._chunks = [self._preroll()]
            self.voiced_s = 0.0
            self._recording = True

    @property
    def level(self):
        return self._level if self._recording else 0.0

    @property
    def gain_db(self):
        return 20 * np.log10(max(self._gain, 1e-6))

    def stop(self):
        """Arrête la prise et renvoie l'audio (float32 mono, gain appliqué) ou None."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            self.last_used = time.time()
            if self.live_queue is not None:
                self.live_queue.put(None)
            chunks, self._chunks = self._chunks, []
            if not chunks:
                return None
            return np.concatenate(chunks)

    def cancel(self):
        with self._lock:
            self._recording = False
            self.last_used = time.time()
            if self.live_queue is not None:
                self.live_queue.put(None)
            self._chunks = []

    def close(self):
        with self._lock:
            self._recording = False
            self._close_stream()
