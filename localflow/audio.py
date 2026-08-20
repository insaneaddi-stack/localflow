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
BLOCK = 800            # 50 ms
RING_S = 1.5
PREROLL_S = 0.35
TARGET_RMS = 0.06
MAX_GAIN = 25.0
NOISE_FLOOR = 0.002
ATTACK = 0.6
RELEASE = 0.08

def _soft_limit(x):
    return np.where(np.abs(x) > 0.8, np.sign(x) * (0.8 + 0.2 * np.tanh((np.abs(x) - 0.8) / 0.2)), x)

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
        self.live_queue = None

    # ---- flux permanent ----

    def open(self):
        """Ouvre (ou ré-ouvre) le flux sur le périphérique d'entrée par défaut."""
        with self._lock:
            self._close_stream()
            try:
                self._device_name = sd.query_devices(kind="input")["name"]
            except Exception:
                self._device_name = None
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=BLOCK, callback=self._callback,
            )
            self._stream.start()
            self._last_callback = time.time()

    def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        for op in (stream.stop, stream.close):
            try:
                op()
            except Exception:
                pass

    def healthy(self):
        """Faux si le flux est mort ou si le périphérique par défaut a changé (AirPods…)."""
        if self._stream is None:
            return False
        if time.time() - self._last_callback > 2.0:
            return False
        try:
            if sd.query_devices(kind="input")["name"] != self._device_name:
                return False
        except Exception:
            pass
        return True

    @property
    def open_(self):
        return self._stream is not None

    # ---- callback audio ----

    def _callback(self, indata, frames, time_info, status):
        self._last_callback = time.time()
        mono = indata[:, 0].astype(np.float32)
        rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-9)
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
        with self._lock:
            if self._stream is None:
                raise RuntimeError("micro fermé")
            if self._recording:
                return
            self.live_queue = queue.Queue() if live else None
            self._chunks = [self._preroll()]
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
            if self.live_queue is not None:
                self.live_queue.put(None)
            chunks, self._chunks = self._chunks, []
            if not chunks:
                return None
            return np.concatenate(chunks)

    def cancel(self):
        with self._lock:
            self._recording = False
            if self.live_queue is not None:
                self.live_queue.put(None)
            self._chunks = []

    def close(self):
        with self._lock:
            self._recording = False
            self._close_stream()
