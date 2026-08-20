"""LocalFlow — dictée vocale locale façon Wispr Flow.

Maintenir fn : dicter. Relâcher : le texte est collé dans l'app active.
fn + espace : mode mains-libres (re-appuyer sur fn pour terminer).
fn + autre touche (fn+←, fn+⌫…) : annule, la touche fait son action normale.
"""

import datetime
import faulthandler
import fcntl
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import traceback

import numpy as np
import rumps
from AppKit import NSApp, NSImage, NSOperationQueue

from .audio import SAMPLE_RATE, Recorder
from .cleanup import Cleaner, cleanup_rules
from .commands import UNDO, apply_commands
from .config import Config
from .context import frontmost_app, should_type, tone_for
from .dictionary import DICT_PATH, Dictionary
from .history_window import HistoryWindow
from .hotkey import FnListener
from .learning import Learner, parse_learn_command
from . import update
from .tutorial import Tutorial
from .overlay import Overlay
from .paste import copy_text, paste_text, press_undo, type_text

ICON_LOADING = "⏳"
ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_HANDS_FREE = "🔴∞"
ICON_PROCESSING = "💭"

TAP_MAX_S = 0.3          # en dessous : c'est un tap, pas un push-to-talk
DOUBLE_TAP_S = 0.45      # deux taps rapprochés : ouvre/ferme le panneau
TAIL_S = 0.35            # audio conservé après le relâchement (dernier mot)
DEBUG_WAV = os.path.expanduser("~/Library/Caches/LocalFlow/last.wav")  # dernière dictée, pour diagnostiquer
MIN_AUDIO_S = 0.35       # ignore les enregistrements plus courts
MIN_VOICED_S = 0.30      # sans au moins 0,3 s de vraie voix, on ne transcrit pas (anti-hallucination)
MAX_RECORD_S = 600       # mains-libres : arrêt auto après 10 min
LIVE_JOIN_S = 15         # attente max du thread « direct » en fin de dictée

SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"

ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon_1024.png")
LOG_PATH = os.path.expanduser("~/.localflow.log")
LOCK_PATH = os.path.expanduser("~/.localflow.lock")
BUSY_TIMEOUT_S = 90  # au-delà, on considère le pipeline coincé et on se débloque
HEALTH_EVERY_S = 2
MIC_LINGER_S = 90        # micro gardé ouvert après une dictée (enchaînements sans latence), puis fermé
STALE_UI_S = 8       # overlay/icône restés bloqués sans enregistrement ni traitement

try:
    _log_file = open(LOG_PATH, "a")
    faulthandler.register(signal.SIGUSR1, file=_log_file, all_threads=True)
except OSError:
    _log_file = None

def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass

def _on_main(fn):
    def safe():
        try:
            fn()
        except Exception:
            _log("erreur UI:\n" + traceback.format_exc())

    NSOperationQueue.mainQueue().addOperationWithBlock_(safe)

def _notify(title, message):
    """Notification macOS via osascript (fiable même sans bundle .app)."""
    if title.lower().startswith("erreur") or "indisponible" in title.lower() or "requise" in title.lower():
        _log(f"ERREUR {title}: {message}")
    safe = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{safe(message)}" with title "LocalFlow" subtitle "{safe(title)}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        print(f"LocalFlow — {title}: {message}")

def _acquire_single_instance():
    """Empêche deux LocalFlow en parallèle (= double collage)."""
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd

class _LiveRun:
    """État d'une session de transcription en direct (un thread par dictée)."""

    def __init__(self):
        self.text = ""
        self.done = threading.Event()
        self.abort = False
        self.thread = None

class LocalFlowApp(rumps.App):
    def __init__(self):
        super().__init__(ICON_LOADING, quit_button=None)
        self.config = Config()
        self.recorder = Recorder()
        self.cleaner = Cleaner()
        self.dictionary = Dictionary()
        self.learner = Learner(self.config, _notify, _log)
        self.transcriber = None   # moteur final (Whisper ou Parakeet)
        self.parakeet = None      # Parakeet : moteur rapide / transcription en direct
        try:  # icône de l'app (Dock quand une fenêtre est ouverte, Cmd+Tab)
            icon = NSImage.alloc().initWithContentsOfFile_(ICON_PATH)
            if icon:
                NSApp.setApplicationIconImage_(icon)
        except Exception:
            pass

        self.hands_free = False
        self._press_time = None
        self._record_start = 0.0
        self._suppress_next_release = False
        self._busy = False
        self._busy_since = 0.0
        self._start_sound_timer = None
        self._live = None
        self._ctx_app = ("", "")

        # ---- menu ----
        self.item_status = rumps.MenuItem("Chargement du modèle…")
        self.item_stats = rumps.MenuItem("Statistiques : —", callback=self._open_history)
        self.item_cleanup = self._toggle_item("Nettoyage IA", "cleanup_enabled", self._toggle_cleanup)
        self.item_live = self._toggle_item("Transcription en direct", "live_enabled")
        self.item_fast = self._toggle_item("Coller le direct (plus rapide)", "live_paste_fast")
        self.item_tone = self._toggle_item("Ton adapté à l'app", "tone_auto")
        self.item_sounds = self._toggle_item("Sons", "sounds_enabled")
        self.item_mic = self._toggle_item("Micro toujours prêt (point orange permanent)", "mic_always_on", self._toggle_mic)
        self.item_engine = rumps.MenuItem("Moteur", callback=None)
        self._engine_items = {}
        for key, label in (("whisper", "Équilibré — Whisper turbo (~0,7 s)"),
                           ("whisper-max", "Précision max — Whisper large-v3 (~2 s, 3 Go)"),
                           ("parakeet", "Rapide — Parakeet (~0,4 s, moins précis)")):
            it = rumps.MenuItem(label, callback=lambda item, k=key: self._set_engine(k))
            it.state = self.config.engine == key
            self._engine_items[key] = it
            self.item_engine.add(it)
        self.item_panel = rumps.MenuItem("Panneau (double-tap fn)", callback=lambda _i: self.overlay.toggle_expanded())
        self.item_history = rumps.MenuItem("Historique…", callback=self._open_history)
        self.item_dict = rumps.MenuItem("Dictionnaire…", callback=self._open_dictionary)
        self.item_update = rumps.MenuItem("Vérifier les mises à jour", callback=self._check_update_clicked)
        self.item_tutorial = rumps.MenuItem("Revoir le tutoriel", callback=lambda _i: self.tutorial.show())
        self.item_auto_update = self._toggle_item("Mises à jour automatiques", "auto_update")

        self.menu = [
            self.item_status,
            self.item_stats,
            None,
            self.item_panel,
            self.item_history,
            self.item_dict,
            None,
            self.item_cleanup,
            self.item_tone,
            self.item_live,
            self.item_fast,
            self.item_engine,
            self.item_sounds,
            self.item_mic,
            None,
            self.item_tutorial,
            self.item_update,
            self.item_auto_update,
            rumps.MenuItem("Quitter", callback=rumps.quit_application),
        ]
        self._refresh_stats()

        self.history_window = HistoryWindow.alloc().initWithConfig_notify_(self.config, _notify)
        self.tutorial = Tutorial(self.config)
        self.overlay = Overlay(lambda: self.recorder.level, self._panel_data, self._panel_action)
        self._last_tap = 0.0
        self._finishing = False
        self.listener = FnListener(
            self._on_fn_down, self._on_fn_up, self._on_fn_space, self._on_fn_other, self._on_key
        )
        self._listener_ok = False
        self._start_listener()

        # Santé : tap fn, UI bloquée, mains-libres trop long, fn coincé
        self._request_mic_permission()
        if self.config.mic_always_on:
            self._open_mic()
        self._health_timer = rumps.Timer(self._health_check, HEALTH_EVERY_S)
        self._health_timer.start()
        self._idle_since = time.time()

        self._jobs = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        # Mises à jour : au démarrage (après 20 s) puis toutes les 6 h, silencieux hors-ligne
        self._update_sha = None
        self._updating = False
        sha = update.just_updated()
        if sha:
            _log(f"mis à jour → {sha[:7]}")
            _notify("LocalFlow mis à jour", f"Nouvelle version installée ({sha[:7]}).")
        threading.Timer(20, self._check_update).start()
        self._update_timer = rumps.Timer(lambda _t: self._check_update(), 6 * 3600)
        self._update_timer.start()

    def _check_update(self, notify_if_none=False):
        def work():
            sha = update.check()
            def apply():
                if sha:
                    first = self._update_sha != sha
                    self._update_sha = sha
                    self.item_update.title = "⬆️ Mise à jour disponible — installer…"
                    if first:
                        _log(f"mise à jour disponible : {sha[:7]}")
                        if self.config.auto_update and not update.IS_DEV:
                            self._auto_update_when_idle()
                        else:
                            _notify("Mise à jour disponible", "Menu 🎙 → Mise à jour disponible — installer…")
                else:
                    self._update_sha = None
                    self.item_update.title = "Vérifier les mises à jour"
                    if notify_if_none:
                        _notify("LocalFlow", "Tu as la dernière version.")
            _on_main(apply)
        threading.Thread(target=work, daemon=True).start()

    def _auto_update_when_idle(self):
        """Lance update.sh dès que l'utilisateur n'est pas en train de dicter."""
        if self._updating:
            return
        if self.recorder.recording or self._busy or self.hands_free:
            threading.Timer(30, lambda: _on_main(self._auto_update_when_idle)).start()
            return
        self._updating = True
        _log("mise à jour automatique lancée")
        _notify("Mise à jour", "LocalFlow se met à jour en arrière-plan (quelques secondes)…")
        update.run_silent()

    def _check_update_clicked(self, _item):
        if self._update_sha:
            update.launch()
        else:
            self._check_update(notify_if_none=True)

    def _toggle_item(self, title, key, extra=None):
        def cb(item):
            item.state = not item.state
            setattr(self.config, key, bool(item.state))
            if extra:
                extra(item)

        item = rumps.MenuItem(title, callback=cb)
        item.state = getattr(self.config, key)
        return item

    # ---------- worker ----------

    def _worker(self):
        """Thread de traitement. Ne meurt jamais : relance le chargement du
        modèle en cas d'échec, et survit à toute erreur d'un job."""
        import numpy as np

        delay = 5
        while self.transcriber is None:
            try:
                if self.config.engine in ("whisper", "whisper-max"):
                    try:
                        from .transcribe import WhisperTranscriber
                        if self.config.engine == "whisper-max":
                            os.environ.pop("HF_HUB_OFFLINE", None)  # téléchargement à la demande autorisé
                        self.transcriber = WhisperTranscriber(self.config.engine)
                    except Exception:
                        _log("Whisper indisponible, repli sur Parakeet:\n" + traceback.format_exc())
                        _notify("Whisper indisponible", "Repli sur Parakeet (moins précis).")
                if self.transcriber is None:
                    self.transcriber = self._load_parakeet()
            except Exception as exc:
                _log("échec chargement du moteur:\n" + traceback.format_exc())
                _notify("Modèle indisponible", f"Nouvel essai dans {delay}s — {exc}")
                _on_main(lambda: setattr(self.item_status, "title", f"⚠️ Modèle : nouvel essai dans {delay}s"))
                time.sleep(delay)
                delay = min(delay * 2, 60)

        def ready():
            self.title = ICON_IDLE
            self.item_status.title = "Prêt — maintenir fn, ou fn+espace"
            if not self.config.data.get("onboarded"):
                self.tutorial.show()

        _on_main(ready)
        _log(f"démarrage: moteur {self.transcriber.name} chargé, prêt")
        if self.config.live_enabled and self.parakeet is None:
            self._jobs.put(("load_parakeet", None))

        # Qwen se charge après le « prêt » : la première dictée n'attend pas.
        if self.config.cleanup_enabled:
            self.cleaner.preload()
            _log("démarrage: Qwen chargé")

        while True:
            try:
                kind, payload = self._jobs.get()
                if kind == "preload":
                    self.cleaner.preload()
                elif kind == "load_parakeet":
                    if self.parakeet is None:
                        self._load_parakeet()
                elif kind == "audio":
                    self._process(payload)
            except Exception:
                _log("erreur worker (ignorée):\n" + traceback.format_exc())
                self._busy = False

    def _load_parakeet(self):
        """Charge Parakeet (une fois) et chauffe le mode direct. Retourne l'instance."""
        from .transcribe import StreamSession, Transcriber
        import numpy as np

        if self.parakeet is None:
            t = Transcriber()
            t.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
            s = StreamSession(t)
            s.feed(np.zeros(SAMPLE_RATE, dtype=np.float32))
            s.finish()
            self.parakeet = t
            _log("Parakeet chargé")
        return self.parakeet

    def _set_engine(self, key):
        if key == self.config.engine:
            return
        self.config.engine = key
        for k, it in self._engine_items.items():
            it.state = k == key
        msg = "Le modèle (~3 Go) sera téléchargé au redémarrage." if key == "whisper-max" else "Pris en compte au redémarrage."
        _notify("Moteur", msg + " Relance : menu Quitter (relance automatique).")

    def _asr_prompt(self):
        """Contexte passé à Whisper : dictionnaire + corrections apprises."""
        words = list(self.dictionary.words) + list(self.learner.active().values())
        return ", ".join(dict.fromkeys(words)) + "." if words else ""

    # ---------- touche fn ----------

    def _on_fn_down(self):
        if self._busy and time.time() - self._busy_since > BUSY_TIMEOUT_S:
            _log("watchdog: pipeline coincé, déblocage forcé")
            self._busy = False
        if self.hands_free:
            self.hands_free = False
            self._suppress_next_release = True
            self._finish_recording()
            return
        if self.transcriber is None or self._busy or self._finishing:
            _log(f"fn ignoré (modèle prêt: {self.transcriber is not None}, busy: {self._busy})")
            return
        _log("fn down")
        self.learner.check_async()  # a-t-on corrigé à la main le dernier collage ?
        self._press_time = time.time()
        self._start_recording()

    def _on_fn_up(self):
        if self._suppress_next_release:
            self._suppress_next_release = False
            return
        if self.hands_free or self._press_time is None or not self.recorder.recording:
            return
        now = time.time()
        if now - self._press_time < TAP_MAX_S:
            self._cancel_recording()  # simple tap : rien…
            if now - self._last_tap < DOUBLE_TAP_S:
                self._last_tap = 0.0
                self.overlay.toggle_expanded()  # …double tap : panneau
                self.tutorial.event("panel")
            else:
                self._last_tap = now
            return
        self._finish_recording()

    def _on_fn_space(self):
        """fn + espace : bascule en mains-libres (fn seul pour terminer)."""
        if self.hands_free or not self.recorder.recording:
            return
        self.hands_free = True
        self._suppress_next_release = True
        self._cancel_start_sound_timer()
        self._play(SOUND_START)
        self.title = ICON_HANDS_FREE
        _log("mains-libres activé")
        self.tutorial.event("handsfree")

    def _on_key(self, keycode):
        """Panneau ouvert : 1-4 copie une bulle, Esc ferme. Sinon on ne touche à rien."""
        if self.overlay.state != "expanded":
            return False
        if keycode == 53:  # Esc
            self.overlay.hide()
            return True
        idx = {18: 0, 19: 1, 20: 2, 21: 3}.get(keycode)  # touches 1-4 (position physique)
        if idx is not None:
            tiles = self._panel_data().get("tiles", [])
            if idx < len(tiles):
                self.overlay._action(tiles[idx]["action"], tiles[idx].get("payload"))
            return True
        return False

    def _on_fn_other(self):
        """fn + autre touche (fn+←, fn+⌫…) : ce n'était pas une dictée."""
        if self.hands_free or not self.recorder.recording:
            return
        self._suppress_next_release = True
        self._cancel_recording()

    # ---------- santé ----------

    def _start_listener(self):
        try:
            self.listener.start()
            self._listener_ok = True
        except Exception as exc:
            self._listener_ok = False
            if time.time() - getattr(self, "_tap_log_t", 0) > 60:  # pas de spam : 1 ligne / min
                self._tap_log_t = time.time()
                _log(f"event tap indisponible: {exc}")
            self.item_status.title = "⚠️ Autorisation Accessibilité manquante"
            if not getattr(self, "_perm_prompted", False):  # une seule fois, pas toutes les 2 s
                self._perm_prompted = True
                _notify("Autorisation requise", "Ajoute LocalFlow/Python dans Accessibilité et Surveillance de l'entrée.")
                subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _request_mic_permission(self):
        """Déclenche explicitement la demande macOS « LocalFlow souhaite accéder au micro »."""
        try:
            import AVFoundation as AV
            status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
            _log(f"micro: statut autorisation = {status} (3 = autorisé)")
            if status != 3:
                def done(granted):
                    _log(f"micro: autorisation {'accordée' if granted else 'refusée'}")
                    if granted:
                        _on_main(self._open_mic)
                AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, done)
        except Exception as exc:
            _log(f"micro: demande d'autorisation impossible ({exc})")

    def _open_mic(self):
        try:
            self.recorder.open()
            _log(f"micro ouvert : {self.recorder._device_name}")
        except Exception as exc:
            _log(f"micro indisponible : {exc}")
            if not getattr(self, "_mic_prompted", False):
                self._mic_prompted = True
                _notify("Micro indisponible", str(exc))

    def _health_check(self, _timer):
        try:
            if not self.recorder.recording:
                if self.config.mic_always_on:
                    if not self.recorder.healthy():
                        self._open_mic()  # flux mort ou périphérique changé (AirPods…)
                elif self.recorder.open_:
                    if time.time() - self.recorder.last_used > MIC_LINGER_S or not self.recorder.healthy():
                        self.recorder.close()  # mode économe : on referme après inactivité
                        _log("micro refermé (inactivité)")
            if not self._listener_ok:
                self._start_listener()
            else:
                state = self.listener.ensure_enabled()
                if state:
                    _log(f"santé: event tap {state}")

            if self.hands_free and self.recorder.recording:
                if time.time() - self._record_start > MAX_RECORD_S:
                    _log("mains-libres: limite de durée atteinte, arrêt auto")
                    self.hands_free = False
                    self._finish_recording()

            if self._busy and time.time() - self._busy_since > BUSY_TIMEOUT_S:
                _log("santé: pipeline coincé, déblocage forcé")
                self._busy = False
                self.overlay.hide()
                self.title = ICON_IDLE

            active = self.recorder.recording or self._busy
            if active:
                self._idle_since = time.time()
            elif self.transcriber is not None and self.title != ICON_IDLE \
                    and time.time() - self._idle_since > STALE_UI_S:
                _log("santé: UI bloquée, remise à zéro")
                self.hands_free = False
                self._suppress_next_release = False
                self.listener.release()
                self.overlay.hide()
                self.title = ICON_IDLE
        except Exception:
            _log("erreur health_check:\n" + traceback.format_exc())

    # ---------- enregistrement ----------

    def _start_recording(self):
        self._ctx_app = frontmost_app()
        live = self.config.live_enabled and self.parakeet is not None
        if self.config.live_enabled and self.parakeet is None:
            self._jobs.put(("load_parakeet", None))
        try:
            self.recorder.start(live=live)
        except Exception as exc:
            _log("échec ouverture micro:\n" + traceback.format_exc())
            _notify("Micro indisponible", str(exc))
            return
        self._record_start = time.time()
        self.title = ICON_RECORDING
        self.overlay.show("recording")
        if live:
            self._live = _LiveRun()
            self._live.thread = threading.Thread(
                target=self._live_loop, args=(self._live, self.recorder.live_queue), daemon=True
            )
            self._live.thread.start()
        else:
            self._live = None
        # Son de début différé : un simple tap ne fait pas de bruit
        self._cancel_start_sound_timer()
        self._start_sound_timer = threading.Timer(TAP_MAX_S, self._play, (SOUND_START,))
        self._start_sound_timer.daemon = True
        self._start_sound_timer.start()

    def _live_loop(self, run, q):
        """Thread « direct » : consomme le micro, transcrit au fil de l'eau."""
        from .transcribe import StreamSession

        session = None
        last_shown = ""
        try:
            session = StreamSession(self.parakeet)
            while True:
                chunk = q.get()
                if chunk is None:
                    break
                # rattrape le retard : avale tout ce qui est déjà en file
                parts = [chunk]
                try:
                    while True:
                        nxt = q.get_nowait()
                        if nxt is None:
                            q.put(None)
                            break
                        parts.append(nxt)
                except queue.Empty:
                    pass
                import numpy as np
                session.feed(np.concatenate(parts))
                if session.text != last_shown:
                    last_shown = session.text
                    _on_main(lambda t=last_shown: self.overlay.set_text(t))
            if run.abort:
                session.abort()
            else:
                run.text = session.finish()
        except Exception:
            _log("erreur direct (on retombera sur la transcription complète):\n" + traceback.format_exc())
            if session is not None:
                session.abort()
            run.text = ""
        finally:
            run.done.set()

    def _cancel_start_sound_timer(self):
        if self._start_sound_timer is not None:
            self._start_sound_timer.cancel()
            self._start_sound_timer = None

    def _cancel_recording(self):
        self._cancel_start_sound_timer()
        if self._live is not None:
            self._live.abort = True
        self.recorder.cancel()
        self.overlay.hide()
        self.title = ICON_IDLE

    def _finish_recording(self):
        """Laisse TAIL_S d'audio après le relâchement (le dernier mot n'est pas coupé)."""
        self._cancel_start_sound_timer()
        if self._finishing:
            return
        self._finishing = True
        self._play(SOUND_STOP)
        t = threading.Timer(TAIL_S, lambda: _on_main(self._finish_now))
        t.daemon = True
        t.start()

    def _finish_now(self):
        self._finishing = False
        audio = self.recorder.stop()
        if audio is None or len(audio) < MIN_AUDIO_S * SAMPLE_RATE:
            _log(f"audio trop court ou vide ({0 if audio is None else len(audio)/SAMPLE_RATE:.2f}s), ignoré")
            if self._live is not None:
                self._live.abort = True
            self.overlay.hide()
            self.title = ICON_IDLE
            return
        voiced = self.recorder.voiced_s
        if voiced < MIN_VOICED_S:
            _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s mais {voiced:.2f}s de voix (bruit {20*np.log10(self.recorder.noise_floor+1e-9):.0f} dBFS) → rien entendu, ignoré")
            self.overlay.hide()
            self.title = ICON_IDLE
            return
        _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s (voix {voiced:.1f}s, gain {self.recorder.gain_db:+.0f} dB) → transcription")
        self.title = ICON_PROCESSING
        self.overlay.show("processing")
        self._busy = True
        self._busy_since = time.time()
        self._jobs.put(("audio", {"audio": audio, "live": self._live, "app": self._ctx_app, "voiced": voiced}))
        self._live = None

    # ---------- pipeline ----------

    def _save_debug(self, audio):
        """Garde les 5 derniers enregistrements (last.wav = le plus récent) pour diagnostiquer."""
        try:
            import wave
            d = os.path.dirname(DEBUG_WAV)
            os.makedirs(d, exist_ok=True)
            for i in range(4, 0, -1):
                src = os.path.join(d, f"last-{i}.wav") if i > 1 else DEBUG_WAV
                dst = os.path.join(d, f"last-{i + 1}.wav")
                if os.path.exists(src):
                    os.replace(src, dst)
            with wave.open(DEBUG_WAV, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        except Exception:
            pass

    def _process(self, job):
        audio, live, (bundle, app_name) = job["audio"], job["live"], job["app"]
        voiced = job.get("voiced", len(audio) / SAMPLE_RATE)
        self._save_debug(audio)
        try:
            t0 = time.time()
            text = ""
            if live is not None and live.done.wait(LIVE_JOIN_S) and live.text and self.config.live_paste_fast:
                text = live.text
                source = "direct"
            else:
                text = self.transcriber.transcribe(audio, prompt=self._asr_prompt())
                source = self.transcriber.name

            if text and len(text.split()) > max(6, voiced * 5.0):
                _log(f"rejeté : {len(text.split())} mots pour {voiced:.1f}s de voix (hallucination probable)")
                text = ""
            text = self.learner.apply(self.dictionary.apply(text))
            learn = parse_learn_command(text)
            if learn:
                self.learner.observe(learn[0], learn[1], force=True)
                return
            tone = tone_for(bundle) if self.config.tone_auto else "neutral"
            if text:
                if self.config.cleanup_enabled:
                    text = self.cleaner.clean(text, tone=tone, vocab=self.dictionary.words)
                else:
                    text = cleanup_rules(text)
            text = self.learner.apply(self.dictionary.apply(text))  # le LLM a pu ré-écorcher un nom
            text = apply_commands(text)

            if text is UNDO:
                press_undo()
                _log("commande vocale : annulation (Cmd+Z)")
            elif text:
                if should_type(bundle):
                    type_text(text)
                    how = "tapé"
                else:
                    paste_text(text)
                    how = "collé"
                self.learner.remember_paste(text, bundle)
                _on_main(lambda: self.tutorial.event("dictated"))
                words = len(text.split())
                self.config.add_history(text, app=app_name)
                self.config.add_stat(words, len(audio) / SAMPLE_RATE)
                _on_main(self._refresh_stats)
                _on_main(self.history_window.refresh)
                _on_main(self.overlay.refresh)
                # Le texte dicté n'est PAS écrit dans le log (vie privée).
                retry = getattr(self.transcriber, "last_retry", "")
                _log(f"{how} en {time.time()-t0:.1f}s via {source} ({words} mots, ton {tone}, app {app_name or '?'})"
                     + (f" — 2e passe : {retry}" if retry else ""))
            else:
                _log("transcription vide, rien à coller")
        except Exception as exc:
            _log("erreur pipeline:\n" + traceback.format_exc())
            _notify("Erreur", str(exc))
        finally:
            self._busy = False

            def done():
                self.overlay.hide()
                self.title = ICON_IDLE

            _on_main(done)

    # ---------- menu ----------

    def _toggle_mic(self, item):
        if item.state:
            self._open_mic()
        else:
            self.recorder.close()

    def _toggle_cleanup(self, item):
        if item.state:
            self._jobs.put(("preload", None))

    def _refresh_stats(self):
        try:
            t = self.config.stats_summary()["today"]
            self.item_stats.title = (
                f"Aujourd'hui : {t['words']} mots · {t['dictations']} dictées · ≈ {t['saved_min']:.0f} min gagnées"
            )
        except Exception:
            pass

    # ---------- panneau (bande du bas) ----------

    def _panel_data(self):
        import datetime as _dt

        def when(iso):
            try:
                d = _dt.datetime.fromisoformat(iso)
            except Exception:
                return ""
            if d.date() == _dt.date.today():
                return d.strftime("%H:%M")
            return d.strftime("%d/%m %H:%M")

        t = self.config.stats_summary()["today"]
        hist = self.config.history
        last = hist[0]["text"] if hist else ""
        engine = "Whisper" if self.transcriber is None or self.transcriber.name == "whisper" else "Parakeet"
        return {
            "status": f"Prêt · {engine}" if self.transcriber is not None else "Chargement…",
            "icon": ICON_PATH,
            "tiles": [
                {"title": "Historique", "subtitle": f"{t['dictations']} dictées aujourd'hui", "color": (0.55, 0.40, 1.00),
                 "on": True, "action": "history"},
                {"title": "Nettoyage IA", "subtitle": "Activé · +1 s" if self.config.cleanup_enabled else "Désactivé · instantané",
                 "color": (0.35, 0.95, 0.55), "on": self.config.cleanup_enabled, "action": "toggle", "payload": "cleanup_enabled"},
                {"title": "Sons", "subtitle": "Activés" if self.config.sounds_enabled else "Silencieux",
                 "color": (1.00, 0.62, 0.30), "on": self.config.sounds_enabled, "action": "toggle", "payload": "sounds_enabled"},
                {"title": "Copier", "subtitle": (last[:34] + "…" if len(last) > 34 else last) if last else "Aucune dictée",
                 "color": (0.35, 0.70, 1.00), "on": bool(last), "action": "copy_last"},
            ],
            "stats_line": f"Aujourd'hui · {t['words']} mots · {t['dictations']} dictées · ≈ {t['saved_min']:.0f} min gagnées",
            "toggles": [
                ("Nettoyage IA", "cleanup_enabled", self.config.cleanup_enabled),
                ("Ton auto", "tone_auto", self.config.tone_auto),
                ("Sons", "sounds_enabled", self.config.sounds_enabled),
            ],
            "actions": [("Re-coller", "repaste", "arrow.uturn.backward"), ("Historique", "history", "clock"), ("Dictionnaire", "dict", "book")],
        }

    def _panel_action(self, action, payload):
        if action == "copy":
            hist = self.config.history
            if 0 <= payload < len(hist):
                copy_text(hist[payload]["text"])
        elif action == "copy_last":
            hist = self.config.history
            if hist:
                copy_text(hist[0]["text"])
                self.overlay.flash_index = 3
                self.overlay.flash_t0 = time.time()
        elif action == "toggle":
            item = {"cleanup_enabled": self.item_cleanup, "tone_auto": self.item_tone, "sounds_enabled": self.item_sounds}[payload]
            item.state = not item.state
            setattr(self.config, payload, bool(item.state))
            if payload == "cleanup_enabled" and item.state:
                self._jobs.put(("preload", None))
        elif action == "repaste":
            hist = self.config.history
            if hist:
                self.overlay.hide()
                text = hist[0]["text"]
                threading.Timer(0.15, lambda: paste_text(text)).start()
        elif action == "history":
            self.overlay.hide()
            self.history_window.show()
        elif action == "dict":
            self.overlay.hide()
            subprocess.Popen(["open", "-t", DICT_PATH])

    def _open_history(self, _item):
        self.history_window.show()

    def _open_dictionary(self, _item):
        subprocess.Popen(["open", "-t", DICT_PATH])

    def _play(self, sound):
        if self.config.sounds_enabled:
            subprocess.Popen(
                ["afplay", "-v", "0.4", sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

def main():
    if _acquire_single_instance() is None:
        print("LocalFlow tourne déjà.", flush=True)
        sys.exit(0)
    try:
        LocalFlowApp().run()
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())
        raise

if __name__ == "__main__":
    main()
