"""Capture du son système (ce que le Mac joue : Zoom, Meet, Teams…) via le helper
Swift `audiotap` (Core Audio Process Tap, macOS 14.2+), et — en mode `with_mic` —
du micro EN MÊME TEMPS, par le même agrégat Core Audio.

Le mode `with_mic` existe parce que le micro (PortAudio) et le tap sont sinon deux
flux à deux horloges sans horodatage commun : mesuré sur un vrai appel, les deux
pistes dérivaient de 0,46 s à 1,48 s l'une par rapport à l'autre. Un seul IOProc
règle le problème par construction. Le helper écrit alors du stéréo entrelacé
(canal 0 = micro, canal 1 = système) et `read()` rend des blocs (n, 2).

Le helper écrit du PCM float32 16 kHz sur stdout ; on le lit dans un thread
et on empile des blocs numpy dans une file. Il quitte tout seul si notre stdin
se ferme (crash du parent) ou si la sortie par défaut change (AirPods…) — code 4,
le superviseur (meeting.py) le relance alors.

Autorisation : « Enregistrement audio système » (Réglages → Confidentialité →
Enregistrement de l'écran et audio système). Elle est attribuée à LocalFlow.app,
donc le helper doit être lancé par le binaire du bundle (cas normal).
Sans autorisation, macOS ne refuse pas : il livre du silence → `silent_s` permet
de le détecter et de prévenir l'utilisateur.
"""

import os
import platform
import queue
import subprocess
import sys
import threading
import time

import numpy as np

SAMPLE_RATE = 16000
READ_BYTES = SAMPLE_RATE * 4 // 10   # 100 ms par lecture

def _candidates():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))          # LocalFlow.app/Contents/MacOS
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # dossier du projet
    return [
        os.path.join(os.path.dirname(exe_dir), "Helpers", "audiotap"),
        os.path.join(root, "LocalFlow.app", "Contents", "Helpers", "audiotap"),
        os.path.join(root, "helpers", "audiotap", "audiotap"),
    ]

def helper_path():
    for p in _candidates():
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None

def macos_ok():
    try:
        major, minor = (int(x) for x in platform.mac_ver()[0].split(".")[:2])
        return (major, minor) >= (14, 2)
    except Exception:
        return False

def available():
    return macos_ok() and helper_path() is not None

def probe(timeout=8.0):
    """Crée le tap une fois (déclenche la demande d'autorisation). (ok, message)."""
    h = helper_path()
    if not macos_ok():
        return False, "macOS 14.2 minimum"
    if h is None:
        return False, "helper audiotap introuvable"
    try:
        r = subprocess.run([h, "--probe"], capture_output=True, text=True, timeout=timeout)
        msg = (r.stderr or "").strip().splitlines()
        return r.returncode == 0, (msg[-1] if msg else f"code {r.returncode}")
    except Exception as exc:
        return False, str(exc)


class SystemAudioTap:
    """start() / read() / stop(). `queue` reçoit des np.float32 16 kHz :
    des blocs (n,) en mono, ou (n, 2) — micro, système — quand with_mic=True."""

    def __init__(self, log=None, with_mic=False):
        self._log = log or (lambda m: None)
        self.with_mic = with_mic
        self.channels = 2 if with_mic else 1
        self.mic_missing = False   # le helper n'a pas pu mettre le micro dans l'agrégat
        self.mic_level = 0.0
        self._proc = None
        self._thread = None
        self.queue = queue.Queue()
        self.exit_code = None
        self.ready = False
        self.error = ""
        self.samples = 0
        self.started_at = 0.0
        self.last_sample_at = 0.0
        self.silent_s = 0.0      # silence strict consécutif (autorisation manquante ?)
        self.level = 0.0
        self.device = ""

    def start(self):
        h = helper_path()
        if h is None:
            raise RuntimeError("helper audiotap introuvable")
        self.exit_code = None
        self.ready = False
        self.error = ""
        self.started_at = time.time()
        self.last_sample_at = 0.0
        self.mic_missing = False
        argv = [h, "--rate", str(SAMPLE_RATE)]
        if self.with_mic:
            argv.append("--with-mic")
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        threading.Thread(target=self._read_err, daemon=True).start()
        self._thread = threading.Thread(target=self._read_out, daemon=True)
        self._thread.start()

    @property
    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def _read_err(self):
        proc = self._proc
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("READY"):
                    self.ready = True
                    for part in line.split():
                        if part.startswith("device="):
                            self.device = part[7:]
                        elif part.startswith("outch="):
                            self.channels = int(part[6:] or 1)
                    self._log(f"audiotap prêt ({self.device}, {self.channels} canaux)")
                elif line.startswith("ERROR"):
                    self.error = line[6:]
                    if "mic-missing" in self.error:
                        # Le cadrage stéréo est conservé (canal micro muet) : le parent
                        # ouvrira son propre micro et ignorera ce canal.
                        self.mic_missing = True
                        self._log("audiotap : micro absent de l'agrégat, repli sur PortAudio")
                    else:
                        self._log(f"audiotap erreur : {self.error}")
        except Exception:
            pass

    def _read_out(self):
        proc = self._proc
        rest = b""
        try:
            while True:
                data = proc.stdout.read(READ_BYTES * self.channels)
                if not data:
                    break
                data, rest = rest + data, b""
                # Une trame = `channels` float32 : on ne coupe jamais au milieu.
                frame = 4 * self.channels
                extra = len(data) % frame
                if extra:
                    data, rest = data[:-extra], data[-extra:]
                if not data:
                    continue
                block = np.frombuffer(data, dtype=np.float32).copy()
                if self.channels == 2:
                    block = block.reshape(-1, 2)
                    sysc = block[:, 1]
                    self.mic_level = min(1.0, float(np.sqrt(np.mean(block[:, 0] ** 2))) * 6.0)
                else:
                    sysc = block
                if not len(sysc):
                    continue
                self.samples += len(sysc)
                self.last_sample_at = time.time()
                rms = float(np.sqrt(np.mean(sysc ** 2)))
                self.level = min(1.0, rms * 6.0)
                if rms < 1e-6:
                    self.silent_s += len(sysc) / SAMPLE_RATE
                else:
                    self.silent_s = 0.0
                self.queue.put(block)
        except Exception as exc:
            self._log(f"audiotap lecture interrompue : {exc}")
        finally:
            try:
                self.exit_code = proc.wait(timeout=2)
            except Exception:
                self.exit_code = -1
            self.queue.put(None)

    def stalled(self, grace=4.0, gap=4.0):
        """Vrai si le helper vit mais ne livre RIEN — cas qui n'était pas détecté.

        Le tap peut être accepté (`--probe` réussit, READY est écrit) et pourtant
        l'IOProc de l'agrégat ne se déclenche jamais : observé sur ce Mac après
        plusieurs jours d'uptime, en mono comme en stéréo. On récoltait alors du
        silence pur pendant toute la réunion sans le moindre avertissement, puisque
        `silent_s` ne compte que les blocs REÇUS — et il n'en arrivait aucun.
        """
        if not self.running:
            return False
        now = time.time()
        if not self.samples:
            return now - self.started_at > grace
        return now - self.last_sample_at > gap

    def read(self):
        """Tous les blocs disponibles (liste, éventuellement vide). None dans la liste = fin."""
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()   # le helper quitte proprement
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    # python -m localflow.sysaudio --test 5 [--with-mic]
    #   → ~/Library/Caches/LocalFlow/tap-test.wav (stéréo : gauche micro, droite système)
    import wave
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    tap = SystemAudioTap(print, with_mic="--with-mic" in sys.argv)
    tap.start()
    chunks = []
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(0.1)
        chunks += [c for c in tap.read() if c is not None]
    tap.stop()
    audio = np.concatenate(chunks) if chunks else np.zeros((0, tap.channels), np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]
    out = os.path.expanduser("~/Library/Caches/LocalFlow/tap-test.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with wave.open(out, "wb") as w:
        w.setnchannels(audio.shape[1]); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    rms = [float(np.sqrt(np.mean(audio[:, c] ** 2))) if len(audio) else 0.0 for c in range(audio.shape[1])]
    noms = ["micro", "système"] if audio.shape[1] == 2 else ["système"]
    print(f"{len(audio)/SAMPLE_RATE:.1f}s, " + ", ".join(f"{n} rms {r:.4f}" for n, r in zip(noms, rms)) + f" → {out}")
