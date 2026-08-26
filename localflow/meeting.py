"""Enregistrement d'une réunion : micro (« Moi ») + son système (« Eux »), transcription
au fil de l'eau par tours de parole, notes tapées, puis résumé et fichier Markdown.

Architecture (tout en threads, rien ne bloque l'UI ni la dictée) :
  mic  (sounddevice) ─┐                     ┌─ segmenteur « moi » ─┐
                      ├─ _writer (250 ms) ──┤                      ├─ file → _transcribe_loop → segments[]
  sys  (audiotap)    ─┘   + WAV sur disque  └─ segmenteur « eux » ─┘

- Les deux pistes sont écrites en continu en WAV 16 kHz dans le cache : 2 h de réunion
  ne tiennent pas en RAM, et rien n'est perdu si l'app plante.
- Un tour de parole = voix détectée (bande 250–3500 Hz, seuil relatif au bruit) jusqu'à
  un silence de 0,7 s, ou 30 s max (coupé au dernier creux). Transcrit en ~1–2 s.
- Anti-écho : sur haut-parleurs, le micro entend aussi « eux ». Si l'enveloppe du micro
  suit celle du système (corrélation > 0,6), le tour micro est ignoré.
- Le modèle Qwen3-ASR est partagé avec la dictée via `model_lock` : la dictée attend au
  pire un segment (≈ 1–2 s).
"""

import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import wave

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 320                     # 20 ms
CACHE_DIR = os.path.expanduser("~/Library/Caches/LocalFlow/meetings")
INDEX_PATH = os.path.expanduser("~/.localflow-meetings.json")
DEFAULT_FOLDER = os.path.expanduser("~/Documents/LocalFlow Réunions")
MAX_MEETING_S = 4 * 3600
SEG_MAX_S = 30.0
SEG_SOFT_S = 18.0               # au-delà, on coupe au premier creux ≥ 0,3 s
SILENCE_END_S = 1.2             # pause qui clôt un tour (plus long = phrases moins hachées)
PREROLL_S = 0.30
MIN_SEG_S = 0.8
VAD_MIN_ABS = 0.0012
VAD_FLOOR_RATIO = 2.5
_VAD_BINS = slice(5, 70)        # FFT 320 pts @16 kHz : 50 Hz/bin → 250–3500 Hz
_WIN = np.hanning(BLOCK).astype(np.float32)

def _band_rms(block):
    spec = np.abs(np.fft.rfft(block * _WIN))
    return float(np.sqrt(np.mean(spec[_VAD_BINS] ** 2)) / 80.0 + 1e-9)

def _fmt_ts(s):
    s = int(s)
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"

def _safe_name(s):
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s).strip()
    return re.sub(r"\s+", " ", s)[:80] or "Réunion"


class _Segmenter:
    """Découpe une piste en tours de parole. feed(block) → liste de (t0, t1, audio)."""

    def __init__(self, who):
        self.who = who
        self.samples = 0            # position absolue (échantillons) de la piste
        self.floor_hist = []
        self.noise_floor = 0.002
        self.voiced = False
        self.buf = []               # blocs du tour courant
        self.buf_start = 0
        self.silence = 0.0
        self.pre = []               # pré-roll (blocs)
        self.cut_candidate = None   # (index de bloc) dernier creux après SEG_SOFT_S
        self.env = []               # enveloppe (RMS bande) par bloc, pour l'anti-écho

    def feed(self, block):
        out = []
        n = len(block)
        v = _band_rms(block)
        self.env.append(v)
        self.floor_hist.append(v)
        if len(self.floor_hist) > 150:   # 3 s
            self.floor_hist.pop(0)
        if len(self.floor_hist) >= 25:
            self.noise_floor = float(np.percentile(self.floor_hist, 20))
        speaking = v > max(VAD_MIN_ABS, self.noise_floor * VAD_FLOOR_RATIO)
        if not self.voiced:
            self.pre.append(block)
            if len(self.pre) > int(PREROLL_S * SAMPLE_RATE / BLOCK):
                self.pre.pop(0)
            if speaking:
                self.voiced = True
                self.buf = list(self.pre)
                self.buf_start = self.samples - sum(len(b) for b in self.pre)
                self.buf.append(block)
                self.silence = 0.0
                self.cut_candidate = None
        else:
            self.buf.append(block)
            self.silence = 0.0 if speaking else self.silence + n / SAMPLE_RATE
            length = sum(len(b) for b in self.buf) / SAMPLE_RATE
            if length >= SEG_SOFT_S and not speaking and self.silence >= 0.3:
                self.cut_candidate = len(self.buf)
            if self.silence >= SILENCE_END_S:
                out.append(self._emit(len(self.buf) - int(self.silence * SAMPLE_RATE / BLOCK) + int(0.4 * SAMPLE_RATE / BLOCK)))
            elif length >= SEG_MAX_S:
                out.append(self._emit(self.cut_candidate or len(self.buf)))
        self.samples += n
        return [o for o in out if o is not None]

    def _emit(self, upto):
        upto = max(1, min(upto, len(self.buf)))
        audio = np.concatenate(self.buf[:upto])
        rest = self.buf[upto:]
        t0 = self.buf_start / SAMPLE_RATE
        t1 = t0 + len(audio) / SAMPLE_RATE
        self.buf_start += len(audio)
        self.buf = rest
        self.cut_candidate = None
        if not rest:
            self.voiced = False
            self.pre = []
        if len(audio) / SAMPLE_RATE < MIN_SEG_S:
            return None
        return (t0, t1, audio)

    def flush(self):
        if self.voiced and self.buf:
            seg = self._emit(len(self.buf))
            self.voiced = False
            self.buf = []
            return [seg] if seg else []
        return []


class Meeting:
    """Données d'une réunion en cours ou terminée."""

    def __init__(self, mid, title="", app=""):
        self.id = mid
        self.title = title
        self.app = app
        self.started = datetime.datetime.now()
        self.ended = None
        self.segments = []          # {"t0","t1","who","text"}
        self.notes = ""
        self.summary = ""           # Markdown (sections)
        self.path = ""              # fichier .md final
        self.audio_path = ""
        self.duration_s = 0.0

    def transcript_text(self, with_times=True):
        lines = []
        for s in sorted(self.segments, key=lambda s: s["t0"]):
            who = "Moi" if s["who"] == "me" else "Eux"
            lines.append((f"[{_fmt_ts(s['t0'])}] " if with_times else "") + f"**{who}** : {s['text']}")
        return "\n".join(lines)

    def plain_transcript(self):
        return "\n".join(("Moi" if s["who"] == "me" else "Eux") + " : " + s["text"]
                         for s in sorted(self.segments, key=lambda s: s["t0"]))

    def word_count(self):
        return sum(len(s["text"].split()) for s in self.segments)


class MeetingRecorder:
    """Une instance par app ; start()/stop(). Callbacks appelés depuis des threads."""

    def __init__(self, transcribe, model_lock, log, prompt=lambda: "", on_segment=None, on_error=None, language="fr"):
        self._transcribe = transcribe          # fn(audio, prompt, language) -> str
        self.language = language               # "fr" / "en" / "" (auto)
        self._lock = model_lock
        self._log = log
        self._prompt = prompt
        self._on_segment = on_segment or (lambda seg: None)
        self._on_error = on_error or (lambda msg: None)
        self.meeting = None
        self.active = False
        self._stop_evt = threading.Event()
        self._mic = None
        self._mic_q = queue.Queue()
        self._tap = None
        self._seg_q = queue.Queue()
        self._threads = []
        self._wav = {}
        self._written = {"me": 0, "them": 0}
        self._seg = {}
        self.level = 0.0
        self.sys_level = 0.0
        self.mic_error = ""
        self.tap_warning = ""
        self._tap_restarts = 0

    # ---- démarrage ----

    def start(self, title="", app=""):
        if self.active:
            return self.meeting
        from .sysaudio import SystemAudioTap, available
        mid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        m = Meeting(mid, title=title, app=app)
        m.dir = os.path.join(CACHE_DIR, mid)
        os.makedirs(m.dir, exist_ok=True)
        self.meeting = m
        self._stop_evt.clear()
        self._written = {"me": 0, "them": 0}
        self._seg = {"me": _Segmenter("me"), "them": _Segmenter("them")}
        self._tap_restarts = 0
        self.tap_warning = ""
        self.mic_error = ""
        for who in ("me", "them"):
            w = wave.open(os.path.join(m.dir, f"{who}.wav"), "wb")
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
            self._wav[who] = w
        # micro : flux indépendant de la dictée
        from .audio import open_input_stream
        self._mic_q = queue.Queue()
        try:
            self._mic = open_input_stream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          blocksize=BLOCK, callback=self._mic_cb)
        except Exception as exc:
            self.mic_error = str(exc)
            self._log(f"réunion : micro indisponible ({exc})")
            self._mic = None
        # son système
        self._tap = None
        if available():
            try:
                self._tap = SystemAudioTap(self._log)
                self._tap.start()
            except Exception as exc:
                self._log(f"réunion : son système indisponible ({exc})")
                self.tap_warning = "Son système indisponible"
        else:
            self.tap_warning = "Son système : macOS 14.2+ requis"
        self.active = True
        self._threads = [
            threading.Thread(target=self._writer, daemon=True),
            threading.Thread(target=self._transcribe_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._save_state()
        self._log(f"réunion démarrée ({mid}, app {app or '?'})")
        return m

    def _mic_cb(self, indata, frames, t, status):
        self._mic_q.put(indata[:, 0].astype(np.float32).copy())

    # ---- boucle d'écriture + segmentation ----

    def _writer(self):
        m = self.meeting
        mic_gain = 1.0
        pending = {"me": np.zeros(0, np.float32), "them": np.zeros(0, np.float32)}
        last_sys = time.time()
        last_mic = time.time()
        while not self._stop_evt.is_set():
            time.sleep(0.25)
            try:
                # micro mort (périphérique changé, PortAudio réinitialisé) → réouverture
                if self._mic is not None and (not self._mic.active or time.time() - last_mic > 3.0):
                    from .audio import close_input_stream, open_input_stream
                    close_input_stream(self._mic)
                    self._mic = None
                    try:
                        self._mic = open_input_stream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                                      blocksize=BLOCK, callback=self._mic_cb)
                        last_mic = time.time()
                        self._log("réunion : micro rouvert")
                    except Exception as exc:
                        self._log(f"réunion : micro perdu ({exc})")
                # micro
                parts = []
                while True:
                    try:
                        parts.append(self._mic_q.get_nowait())
                    except queue.Empty:
                        break
                if parts:
                    last_mic = time.time()
                    mic = np.concatenate(parts)
                    rms = float(np.sqrt(np.mean(mic ** 2)) + 1e-9)
                    if rms > 0.002:
                        wanted = min(12.0, max(1.0, 0.06 / rms))
                        mic_gain += (wanted - mic_gain) * (0.5 if wanted < mic_gain else 0.1)
                    self.level = min(1.0, rms * mic_gain * 6.0)
                    pending["me"] = np.concatenate([pending["me"], np.clip(mic * mic_gain, -1, 1)])
                # système
                if self._tap is not None:
                    blocks = self._tap.read()
                    ended = any(b is None for b in blocks)
                    blocks = [b for b in blocks if b is not None]
                    if blocks:
                        pending["them"] = np.concatenate([pending["them"]] + blocks)
                        last_sys = time.time()
                    self.sys_level = self._tap.level
                    if ended or not self._tap.running:
                        self._restart_tap()
                    if self._tap is not None and self._tap.silent_s > 20 and self._tap.samples > 0 and not self.tap_warning:
                        self.tap_warning = "Son système muet : vérifie l'autorisation « Enregistrement audio système »"
                        self._log("réunion : tap silencieux > 20 s (autorisation ?)")
                # aligne « eux » sur « moi » si le tap a décroché (comble par du silence)
                gap = (self._written["me"] + len(pending["me"])) - (self._written["them"] + len(pending["them"]))
                if gap > SAMPLE_RATE * 2 and time.time() - last_sys > 1.5:
                    pending["them"] = np.concatenate([pending["them"], np.zeros(int(gap), np.float32)])
                # écrit + segmente par blocs de 20 ms
                for who in ("me", "them"):
                    buf = pending[who]
                    n = (len(buf) // BLOCK) * BLOCK
                    if n == 0:
                        continue
                    chunk, pending[who] = buf[:n], buf[n:]
                    self._wav[who].writeframes((chunk * 32767).astype(np.int16).tobytes())
                    self._written[who] += n
                    seg = self._seg[who]
                    for i in range(0, n, BLOCK):
                        for t0, t1, audio in seg.feed(chunk[i:i + BLOCK]):
                            self._seg_q.put((who, t0, t1, audio))
                m.duration_s = self._written["me"] / SAMPLE_RATE
                if m.duration_s > MAX_MEETING_S:
                    self._log("réunion : durée max atteinte")
                    self._on_error("Durée maximale (4 h) atteinte — réunion arrêtée.")
                    threading.Thread(target=self.stop, daemon=True).start()
                    return
            except Exception as exc:
                import traceback
                self._log("réunion : erreur writer\n" + traceback.format_exc())
                self._on_error(f"Enregistrement interrompu : {exc}")
                time.sleep(1)

    def _restart_tap(self):
        from .sysaudio import SystemAudioTap
        code = self._tap.exit_code if self._tap else None
        try:
            self._tap.stop()
        except Exception:
            pass
        if self._tap_restarts >= 20:
            self.tap_warning = "Son système perdu"
            self._tap = None
            return
        self._tap_restarts += 1
        self._log(f"réunion : relance du tap (code {code}, n°{self._tap_restarts})")
        time.sleep(0.3)
        try:
            self._tap = SystemAudioTap(self._log)
            self._tap.start()
        except Exception as exc:
            self._log(f"réunion : relance du tap impossible ({exc})")
            self._tap = None

    # ---- anti-écho ----

    def _is_echo(self, t0, t1):
        """Vrai si le micro, entre t0 et t1, ne fait que répéter le son système."""
        me, them = self._seg["me"].env, self._seg["them"].env
        a, b = int(t0 * SAMPLE_RATE / BLOCK), int(t1 * SAMPLE_RATE / BLOCK)
        if b - a < 15 or b > len(me):
            return False
        lag_max = 8   # ±160 ms
        best = 0.0
        x = np.array(me[a:b])
        if x.std() < 1e-9:
            return False
        x = (x - x.mean()) / x.std()
        for lag in range(-lag_max, lag_max + 1):
            s, e = a + lag, b + lag
            if s < 0 or e > len(them):
                continue
            y = np.array(them[s:e])
            if y.std() < 1e-9 or y.mean() < VAD_MIN_ABS:
                continue
            y = (y - y.mean()) / y.std()
            best = max(best, float(np.mean(x * y)))
        return best > 0.6

    # ---- transcription ----

    def _transcribe_loop(self):
        from .transcribe import _is_hallucination
        while True:
            item = self._seg_q.get()
            if item is None:
                return
            who, t0, t1, audio = item
            try:
                if who == "me" and self._tap is not None and self._is_echo(t0, t1):
                    self._log(f"réunion : tour micro {_fmt_ts(t0)} ignoré (écho du son système)")
                    continue
                peak = float(np.max(np.abs(audio)) or 1.0)
                if peak < 0.3:
                    audio = audio * (0.5 / peak)
                # Contexte : dictionnaire seul. On y ajoutait la fin du tour précédent
                # (astuce initial_prompt de Whisper), mais Qwen3-ASR attend là des TERMES
                # de vocabulaire, pas de la prose : une phrase tronquée n'y apporte aucune
                # continuité et pousse le modèle à la prolonger.
                prompt = self._prompt()
                with self._lock:
                    try:
                        text = self._transcribe(audio, prompt, self.language).strip()
                    except TypeError:   # moteur sans paramètre de langue
                        text = self._transcribe(audio, prompt).strip()
                secs = t1 - t0
                if not text or _is_hallucination(text) or len(text.split()) > secs * 4.5 + 4:
                    continue
                seg = {"t0": round(t0, 2), "t1": round(t1, 2), "who": who, "text": text}
                self.meeting.segments.append(seg)
                self._on_segment(seg)
                if len(self.meeting.segments) % 5 == 0:
                    self._save_state()
            except Exception:
                import traceback
                self._log("réunion : erreur transcription\n" + traceback.format_exc())

    def _save_state(self):
        """Sauvegarde intermédiaire (reprise possible après crash)."""
        m = self.meeting
        try:
            with open(os.path.join(m.dir, "state.json"), "w", encoding="utf-8") as f:
                json.dump({"id": m.id, "title": m.title, "app": m.app, "started": m.started.isoformat(timespec="seconds"),
                           "segments": m.segments, "notes": m.notes}, f, ensure_ascii=False)
        except OSError:
            pass

    def set_notes(self, text):
        if self.meeting is not None:
            self.meeting.notes = text
            try:
                with open(os.path.join(self.meeting.dir, "notes.md"), "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass

    # ---- arrêt ----

    def stop(self, wait_s=120.0):
        """Arrête la capture, finit de transcrire. Retourne le Meeting (résumé non fait)."""
        if not self.active:
            return self.meeting
        self.active = False
        self._stop_evt.set()
        m = self.meeting
        m.ended = datetime.datetime.now()
        for t in self._threads[:1]:
            t.join(timeout=3)
        if self._mic is not None:
            from .audio import close_input_stream
            close_input_stream(self._mic)
            self._mic = None
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:
                pass
        # derniers tours
        for who in ("me", "them"):
            for t0, t1, audio in self._seg[who].flush():
                self._seg_q.put((who, t0, t1, audio))
        self._seg_q.put(None)
        self._threads[1].join(timeout=wait_s)
        for w in self._wav.values():
            try:
                w.close()
            except Exception:
                pass
        self._wav = {}
        m.duration_s = max(self._written["me"], self._written["them"]) / SAMPLE_RATE
        self._save_state()
        self._log(f"réunion arrêtée : {_fmt_ts(m.duration_s)}, {len(m.segments)} tours, {m.word_count()} mots")
        return m

    # ---- fichiers ----

    def export_audio(self, folder):
        """Mixe moi (gauche) + eux (droite) en .m4a via afconvert (toujours présent sur macOS)."""
        m = self.meeting
        try:
            me = self._read_wav(os.path.join(m.dir, "me.wav"))
            them = self._read_wav(os.path.join(m.dir, "them.wav"))
            n = max(len(me), len(them))
            if n == 0:
                return ""
            stereo = np.zeros((n, 2), np.int16)
            stereo[:len(me), 0] = me
            stereo[:len(them), 1] = them
            tmp = os.path.join(m.dir, "mix.wav")
            with wave.open(tmp, "wb") as w:
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                w.writeframes(stereo.tobytes())
            os.makedirs(os.path.join(folder, "audio"), exist_ok=True)
            out = os.path.join(folder, "audio", os.path.splitext(os.path.basename(m.path or m.id))[0] + ".m4a")
            r = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000", tmp, out],
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                self._log(f"afconvert: {r.stderr.strip()}")
                out = os.path.join(folder, "audio", os.path.basename(out).replace(".m4a", ".wav"))
                shutil.copy(tmp, out)
            m.audio_path = out
            return out
        except Exception as exc:
            self._log(f"export audio impossible : {exc}")
            return ""

    @staticmethod
    def _read_wav(path):
        try:
            with wave.open(path, "rb") as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        except Exception:
            return np.zeros(0, np.int16)

    def cleanup_cache(self, keep_as=""):
        """Supprime le cache de la réunion ; keep_as='last-cancelled' le garde (diagnostic) à la place."""
        m = self.meeting
        try:
            if keep_as:
                dst = os.path.join(CACHE_DIR, keep_as)
                shutil.rmtree(dst, ignore_errors=True)
                os.replace(m.dir, dst)
            else:
                shutil.rmtree(m.dir, ignore_errors=True)
        except Exception:
            pass


# ---- Markdown + index ----

def write_markdown(m, folder):
    os.makedirs(folder, exist_ok=True)
    title = m.title or "Réunion"
    base = f"{m.started:%Y-%m-%d %H-%M} {_safe_name(title)}"
    path = os.path.join(folder, base + ".md")
    k = 2
    while os.path.exists(path) and getattr(m, "path", "") != path:
        path = os.path.join(folder, f"{base} ({k}).md"); k += 1
    m.path = path
    audio_rel = os.path.relpath(m.audio_path, folder) if m.audio_path else ""
    front = [
        "---",
        f"title: \"{title}\"",
        f"date: {m.started:%Y-%m-%d %H:%M}",
        f"duration: {_fmt_ts(m.duration_s)}",
        f"app: {m.app or ''}",
        "participants: [Moi, Eux]",
        f"audio: \"{audio_rel}\"" if audio_rel else "audio: \"\"",
        f"words: {m.word_count()}",
        "source: LocalFlow",
        "---",
        "",
        f"# {title}",
        "",
        f"*{m.started:%A %d %B %Y, %H:%M} · {_fmt_ts(m.duration_s)}" + (f" · {m.app}" if m.app else "") + "*",
        "",
    ]
    body = []
    if m.summary:
        body += [m.summary.strip(), ""]
    if m.notes.strip():
        body += ["## Mes notes", "", m.notes.strip(), ""]
    body += ["## Transcript", "", m.transcript_text() or "_(vide)_", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(front + body))
    return path


class MeetingIndex:
    """~/.localflow-meetings.json : liste légère des réunions (plus récente en premier)."""

    def __init__(self):
        self.items = []
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.items = data
        except Exception:
            self.items = []

    def save(self):
        tmp = INDEX_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, INDEX_PATH)
        except OSError:
            pass

    def add(self, m, summary_line=""):
        entry = {
            "id": m.id, "t": m.started.isoformat(timespec="seconds"), "title": m.title or "Réunion",
            "path": m.path, "audio": m.audio_path, "duration_s": round(m.duration_s), "words": m.word_count(),
            "app": m.app, "summary": summary_line[:200],
        }
        self.items = [entry] + [e for e in self.items if e.get("id") != m.id]
        self.save()

    def remove(self, mid, delete_files=False):
        for e in self.items:
            if e.get("id") == mid and delete_files:
                for p in (e.get("path"), e.get("audio")):
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
        self.items = [e for e in self.items if e.get("id") != mid]
        self.save()

    def existing(self):
        """Entrées dont le fichier existe encore."""
        return [e for e in self.items if e.get("path") and os.path.exists(e["path"])]
