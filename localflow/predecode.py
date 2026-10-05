"""Transcription anticipée des longues dictées.

Mesuré (oct. 2026) : 114 s de parole → 22,6 s d'attente après le relâchement, car tout
est décodé d'un bloc à la fin. Ici, pendant l'enregistrement, on transcrit les segments
déjà terminés en coupant sur une PAUSE (jamais au milieu d'un mot) : au relâchement il
ne reste que la fin, et l'attente ne dépend plus de la longueur de la dictée.

Pourquoi des pauses et pas des blocs fixes : le décodage par blocs de Qwen3-ASR coupe
les phrases et réordonne les mots (voir StreamSession) ; une pause d'au moins 0,4 s est
presque toujours une frontière de phrase, chaque segment est donc une dictée complète.
"""
import threading
import time

import numpy as np

SR = 16000
SEG_MIN_S = 20.0    # en dessous, rien à gagner : le décodage final est déjà rapide
SEG_MAX_S = 45.0    # au-delà, on coupe au moment le plus calme même sans vraie pause
GAP_S = 0.4         # largeur de la fenêtre de pause
TAIL_GUARD_S = 1.0  # ne jamais couper dans la dernière seconde : la phrase continue peut-être
PAUSE_RATIO = 0.25  # pause = énergie < 25 % de l'énergie médiane du segment


def find_split(audio, start, force=False):
    """Indice où couper `audio` après `start`, au centre de la pause la plus calme ; None si
    aucune pause nette et `force` faux."""
    lo = start + int(SEG_MIN_S * SR)
    hi = min(len(audio) - int(TAIL_GUARD_S * SR), start + int(SEG_MAX_S * SR))
    w = int(GAP_S * SR)
    if hi - lo < w:
        return None
    step = SR // 20   # 50 ms
    seg = audio[lo:hi]
    n = (len(seg) - w) // step + 1
    if n <= 0:
        return None
    e = np.array([np.mean(seg[i * step:i * step + w] ** 2) for i in range(n)])
    i = int(np.argmin(e))
    ref = float(np.median(audio[start:hi] ** 2)) + 1e-12
    if not force and e[i] > PAUSE_RATIO * ref:
        return None
    return lo + i * step + w // 2


class PreDecoder:
    """Tourne pendant l'enregistrement ; `finish(audio)` (thread worker) renvoie le texte complet."""

    def __init__(self, recorder, transcriber, model_lock, prompt_fn):
        self.recorder, self.transcriber, self.lock, self.prompt_fn = recorder, transcriber, model_lock, prompt_fn
        self.committed = 0
        self.texts = []
        self.segments = 0
        self.working = False   # un segment est en cours de décodage
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="predecode")
        self._thread.start()

    def _run(self):
        while not self._stop.wait(1.0):
            audio = self.recorder.peek()
            force = len(audio) - self.committed > (SEG_MAX_S + TAIL_GUARD_S) * SR
            cut = find_split(audio, self.committed, force=force)
            if cut is None:
                continue
            self.working = True
            try:
                with self.lock:
                    text = self.transcriber.transcribe(audio[self.committed:cut], prompt=self.prompt_fn())
                if text:
                    self.texts.append(text)
                self.committed = cut
                self.segments += 1
            finally:
                self.working = False

    def stop(self):
        self._stop.set()

    def finish(self, audio):
        """Attend le segment en cours, décode le reste et renvoie (texte, secondes décodées à la fin).
        À appeler SANS tenir model_lock : le thread peut l'attendre pour finir son segment."""
        self._stop.set()
        self._thread.join(timeout=60)
        rest = audio[self.committed:]
        tail = ""
        if len(rest) > 0.3 * SR:
            with self.lock:
                tail = self.transcriber.transcribe(rest, prompt=self.prompt_fn())
        return " ".join(t for t in self.texts + [tail] if t).strip(), len(rest) / SR
