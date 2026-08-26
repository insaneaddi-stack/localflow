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

from .audio import SAMPLE_RATE, Recorder, audio_stuck
from .cleanup import Cleaner, cleanup_rules
from .commands import UNDO, apply_commands
from .config import Config
from .context import frontmost_app, should_type, tone_for
from .dictionary import DICT_PATH, Dictionary
from .history_window import HistoryWindow
from .hotkey import FnListener
from .learning import Learner, parse_learn_command
from .meeting import DEFAULT_FOLDER, MeetingIndex, MeetingRecorder, write_markdown, _fmt_ts
from .meeting_detect import MeetingDetector
from .meeting_window import LiveMeetingWindow, MeetingsWindow
from .summarize import MODELS as SUMMARY_MODELS, Summarizer
from . import sounds
from . import sysaudio
from . import update
from .tutorial import Tutorial
from .overlay import Overlay
from .permissions import PermissionsWindow
from .paste import copy_text, paste_text, press_undo, type_text

ICON_LOADING = "⏳"
ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_HANDS_FREE = "🔴∞"
ICON_PROCESSING = "💭"
ICON_MEETING = "🎙●"
OFFER_TIMEOUT_S = 25     # la proposition « enregistrer la réunion ? » disparaît toute seule

TAP_MAX_S = 0.3          # en dessous : c'est un tap, pas un push-to-talk
DOUBLE_TAP_S = 0.45      # deux taps rapprochés : ouvre/ferme le panneau
TAIL_S = 0.35            # audio conservé après le relâchement (dernier mot)
DEBUG_WAV = os.path.expanduser("~/Library/Caches/LocalFlow/last.wav")  # dernière dictée, pour diagnostiquer
MIN_AUDIO_S = 0.35       # ignore les enregistrements plus courts
MIN_VOICED_S = 0.12      # seuil bas : le détecteur ne compte que les pics (silence pur = 0,00 s)
MAX_RECORD_S = 600       # mains-libres : arrêt auto après 10 min
LIVE_JOIN_S = 15         # attente max du thread « direct » en fin de dictée

SOUND_START = sounds.START
SOUND_STOP = sounds.STOP

ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon_1024.png")
LOG_PATH = os.path.expanduser("~/.localflow.log")
LOCK_PATH = os.path.expanduser("~/.localflow.lock")
BUSY_TIMEOUT_S = 90  # au-delà, on considère le pipeline coincé et on se débloque
HEALTH_EVERY_S = 2
MIC_LINGER_S = 15        # micro gardé ouvert après une dictée (enchaînements sans latence), puis fermé
STALE_UI_S = 8       # overlay/icône restés bloqués sans enregistrement ni traitement
KEEP_WARM_S = 30     # au repos : micro-inférence périodique pour que macOS ne swappe pas le modèle
SLOW_S = 3.0         # au-delà, on note l'état mémoire dans le log (diagnostic)

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

def _mem_state():
    """Swap utilisé / libre (diagnostic des lenteurs)."""
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=2).stdout
        return "swap " + " ".join(out.split())
    except Exception:
        return "swap ?"

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
        sounds.preload()          # en RAM tout de suite : le premier fn ne doit rien attendre
        self.recorder = Recorder()
        self.cleaner = Cleaner()
        self.dictionary = Dictionary()
        self.learner = Learner(self.config, _notify, _log)
        self.transcriber = None   # Qwen3-ASR : dictée, direct et réunions
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
        self.item_fast = self._toggle_item("Coller le texte du direct (moins précis)", "live_paste_fast")
        self.item_tone = self._toggle_item("Ton adapté à l'app", "tone_auto")
        self.item_sounds = self._toggle_item("Sons", "sounds_enabled")
        self.item_mic = self._toggle_item("Micro toujours prêt (point orange permanent)", "mic_always_on", self._toggle_mic)
        self.item_panel = rumps.MenuItem("Panneau (double-tap fn)", callback=lambda _i: self.overlay.toggle_expanded())
        self.item_history = rumps.MenuItem("Historique…", callback=self._open_history)
        self.item_dict = rumps.MenuItem("Dictionnaire…", callback=self._open_dictionary)
        self.item_update = rumps.MenuItem("Vérifier les mises à jour", callback=self._check_update_clicked)
        self.item_tutorial = rumps.MenuItem("Revoir le tutoriel", callback=lambda _i: self.tutorial.show())
        self.item_perms = rumps.MenuItem("Autorisations…", callback=lambda _i: self.perms.show())
        self.item_auto_update = self._toggle_item("Mises à jour automatiques", "auto_update")

        # ---- réunions ----
        self.item_meet = rumps.MenuItem("Réunions", callback=None)
        self.item_meet_start = rumps.MenuItem("Démarrer une réunion", callback=self._meeting_toggle_clicked)
        self.item_meet_window = rumps.MenuItem("Mes réunions…", callback=lambda _i: self.meetings_window.show())
        self.item_meet_folder = rumps.MenuItem("Ouvrir le dossier", callback=lambda _i: subprocess.Popen(["open", self._meeting_folder()]))
        self.item_meet_detect = self._toggle_item("Proposer quand un appel démarre", "meeting_auto_detect")
        self.item_meet_audio = self._toggle_item("Garder l'audio (.m4a)", "meeting_keep_audio")
        self.item_meet_model = rumps.MenuItem("Qualité du résumé", callback=None)
        self._summary_items = {}
        for key, label in (("qwen-1.7b", "Standard — Qwen3 1.7B (déjà installé)"),
                           ("qwen-4b", "Meilleur — Qwen3 4B (~2,5 Go, téléchargé à la demande)")):
            it = rumps.MenuItem(label, callback=lambda item, k=key: self._set_summary_model(k))
            it.state = self.config.meeting_summary_model == key
            self._summary_items[key] = it
            self.item_meet_model.add(it)
        self.item_meet_lang = rumps.MenuItem("Langue des réunions", callback=None)
        self._lang_items = {}
        for key, label in (("fr", "Français"), ("en", "Anglais"), ("", "Automatique (par tour de parole)")):
            it = rumps.MenuItem(label, callback=lambda item, k=key: self._set_meeting_lang(k))
            it.state = self.config.meeting_language == key
            self._lang_items[key] = it
            self.item_meet_lang.add(it)
        for it in (self.item_meet_start, self.item_meet_window, self.item_meet_folder, None, self.item_meet_detect,
                   self.item_meet_audio, self.item_meet_model, self.item_meet_lang):
            self.item_meet.add(it) if it is not None else self.item_meet.add(rumps.separator)

        self.menu = [
            self.item_status,
            self.item_stats,
            None,
            self.item_panel,
            self.item_history,
            self.item_dict,
            self.item_meet,
            None,
            self.item_cleanup,
            self.item_tone,
            self.item_live,
            self.item_fast,
            self.item_sounds,
            self.item_mic,
            None,
            self.item_perms,
            self.item_tutorial,
            self.item_update,
            self.item_auto_update,
            rumps.MenuItem("Quitter", callback=rumps.quit_application),
        ]
        self._refresh_stats()

        self.history_window = HistoryWindow.alloc().initWithConfig_notify_(self.config, _notify)
        self.perms = PermissionsWindow.alloc().initWithIcon_(ICON_PATH)
        self.tutorial = Tutorial(self.config)
        self.overlay = Overlay(self._overlay_level, self._panel_data, self._panel_action)
        self.overlay.meeting_info = self._meeting_info

        # Réunions : enregistreur (micro + son système), résumé, index, détection, fenêtres
        self.model_lock = threading.Lock()   # Qwen3-ASR partagé entre dictée, direct et réunion
        self.meeting_rec = MeetingRecorder(self._meeting_transcribe, self.model_lock, _log, prompt=self._asr_prompt,
                                           on_segment=lambda seg: _on_main(self.live_window.refresh),
                                           on_error=lambda msg: _notify("Réunion", msg),
                                           language=self.config.meeting_language)
        self.summarizer = Summarizer(self.config.meeting_summary_model, _log, shared=self.cleaner)
        self.meeting_index = MeetingIndex()
        self.detector = MeetingDetector()
        self._offer_t0 = 0.0
        self._offer_app = ""
        self._offer_bundle = ""
        self._meeting_busy = False
        self.live_window = LiveMeetingWindow.alloc().initWithCallbacks_({
            "stop": self._meeting_stop, "cancel": self._meeting_cancel, "notes": self.meeting_rec.set_notes})
        self.meetings_window = MeetingsWindow.alloc().initWithIndex_callbacks_(self.meeting_index, {
            "ask": self._meeting_ask, "delete": self._meeting_delete, "notify": _notify, "folder": self._meeting_folder})
        self._meeting_log_status()
        self._last_tap = 0.0
        self._finishing = False
        # Le tap tourne sur son propre thread : on renvoie chaque callback sur le thread principal.
        self.listener = FnListener(
            lambda: _on_main(self._on_fn_down), lambda: _on_main(self._on_fn_up),
            lambda: _on_main(self._on_fn_space), lambda: _on_main(self._on_fn_other), self._on_key,
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
        delay = 5
        notified = False
        while self.transcriber is None:
            try:
                from .transcribe import Transcriber

                self.transcriber = Transcriber()
            except Exception as exc:
                _log("échec chargement du moteur:\n" + traceback.format_exc())
                # Une seule notification : la boucle peut tourner des heures (modèle en
                # cours de téléchargement, disque plein…), inutile de noyer le Centre de
                # notifications. L'état reste visible dans le menu et dans le log.
                if not notified:
                    _notify("Modèle indisponible", f"LocalFlow réessaie en boucle — {exc}")
                    notified = True
                _on_main(lambda d=delay: setattr(self.item_status, "title", f"⚠️ Modèle : nouvel essai dans {d}s"))
                time.sleep(delay)
                delay = min(delay * 2, 60)

        def ready():
            self.title = self._idle_icon()
            self.item_status.title = "Prêt — maintenir fn, ou fn+espace"
            first = not self.config.data.get("onboarded")
            if self.perms.missing():
                self.perms.show(on_done=(self.tutorial.show if first else None))
            elif first:
                self.tutorial.show()

        _on_main(ready)
        _log(f"démarrage: moteur {self.transcriber.name} chargé, prêt")

        # Qwen se charge après le « prêt » : la première dictée n'attend pas.
        if self.config.cleanup_enabled:
            self.cleaner.preload()
            _log("démarrage: Qwen chargé")

        while True:
            try:
                try:
                    kind, payload = self._jobs.get(timeout=KEEP_WARM_S)
                except queue.Empty:
                    self._keep_warm()
                    continue
                if kind == "preload":
                    self.cleaner.preload()
                elif kind == "audio":
                    self._process(payload)
            except Exception:
                _log("erreur worker (ignorée):\n" + traceback.format_exc())
                self._busy = False

    def _keep_warm(self):
        """Garde les poids de Qwen3-ASR « chauds » : sous pression mémoire (swap), macOS expulse
        les pages inutilisées et la dictée suivante met 5–15 s à les recharger."""
        if self.recorder.recording or self._busy or self.meeting_rec.active:
            return
        try:
            t0 = time.time()
            with self.model_lock:
                self.transcriber.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), language="fr")
            dt = time.time() - t0
            if 1.5 < dt < 120:   # au-delà : le Mac dormait pendant l'inférence, pas une lenteur réelle
                _log(f"keep-warm lent ({dt:.1f}s) : modèle rechargé depuis le swap — {_mem_state()}")
        except Exception:
            pass

    # ---------- estimation du temps de décodage ----------

    # Le décodage suit de près une droite : un coût fixe (mel + encodeur) plus un
    # coût proportionnel à la durée. Mesuré au départ sur ce Mac ; ces deux valeurs
    # ne servent que tant qu'on n'a pas assez de vraies dictées pour les remplacer.
    DECODE_A0, DECODE_B0 = 0.15, 0.12
    DECODE_SAMPLES = 24        # fenêtre glissante
    DECODE_MIN_FIT = 5         # en dessous, une régression ne vaut rien

    def _decode_estimate(self, audio_s):
        """Durée de décodage attendue, en secondes, pour `audio_s` d'audio."""
        a, b = self.DECODE_A0, self.DECODE_B0
        pts = [p for p in self.config.data.get("decode_times", []) if len(p) == 2]
        if len(pts) >= self.DECODE_MIN_FIT:
            n = len(pts)
            sx = sum(p[0] for p in pts)
            sy = sum(p[1] for p in pts)
            sxx = sum(p[0] * p[0] for p in pts)
            sxy = sum(p[0] * p[1] for p in pts)
            den = n * sxx - sx * sx
            if den > 1e-6:
                nb = (n * sxy - sx * sy) / den
                na = (sy - nb * sx) / n
                if nb > 0.01:            # une pente nulle ou négative = données aberrantes
                    a, b = max(0.0, na), nb
        return max(0.30, a + b * audio_s)

    def _record_decode_time(self, audio_s, elapsed):
        """Mémorise (durée audio, temps réel) pour affiner les prochaines estimations."""
        if audio_s <= 0.2 or not (0.05 <= elapsed <= 120):
            return                       # dictée minuscule, ou Mac qui dormait : inexploitable
        pts = [p for p in self.config.data.get("decode_times", []) if len(p) == 2]
        pts.append([round(audio_s, 2), round(elapsed, 3)])
        self.config.data["decode_times"] = pts[-self.DECODE_SAMPLES:]
        self.config.save()

    def _asr_prompt(self):
        """Contexte passé à Qwen3-ASR : dictionnaire + corrections apprises."""
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
        self.overlay.rec_hands_free = True   # le chrono passe en violet : ça continue sans toi
        self._suppress_next_release = True
        self._cancel_start_sound_timer()
        self._play(SOUND_START)
        self.title = ICON_HANDS_FREE
        _log("mains-libres activé")
        self.tutorial.event("handsfree")

    def _on_key(self, keycode):
        """Panneau ouvert : 1-4 copie une bulle, Esc ferme. Sinon on ne touche à rien.
        Appelé depuis le thread du tap : décision immédiate, action sur le thread principal."""
        if self.overlay.state != "expanded":
            return False
        if keycode == 53:  # Esc
            _on_main(self.overlay.hide)
            return True
        idx = {18: 0, 19: 1, 20: 2, 21: 3}.get(keycode)  # touches 1-4 (position physique)
        if idx is not None:
            def act():
                tiles = self._panel_data().get("tiles", [])
                if idx < len(tiles):
                    self.overlay._action(tiles[idx]["action"], tiles[idx].get("payload"))
            _on_main(act)
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
                # Fenêtre guidée : témoins en direct + bouton qui déclenche la demande officielle de macOS
                # (qui crée elle-même l'entrée Accessibilité ; une entrée ajoutée à la main peut rester périmée).
                self.perms.show()

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
            # PortAudio figé (retour de veille) : rien ne peut le débloquer depuis le processus →
            # redémarrage propre, le LaunchAgent relance en ~5 s et le modèle recharge.
            stuck = audio_stuck()
            if stuck > 12:
                _log(f"audio figé depuis {stuck:.0f}s (PortAudio, retour de veille ?) → redémarrage automatique")
                _notify("LocalFlow redémarre", "La couche audio de macOS s'est figée : redémarrage automatique (~10 s).")
                threading.Timer(1.2, lambda: os._exit(86)).start()
                return
            if not self.recorder.recording:
                if self.config.mic_always_on:
                    if not self.recorder.healthy():
                        self._open_mic()  # flux mort ou périphérique changé (AirPods…)
                elif self.recorder.open_:
                    if time.time() - self.recorder.last_used > MIC_LINGER_S or not self.recorder.healthy():
                        self.recorder.close(wait=False)  # mode économe, sans jamais bloquer le thread principal
                        _log("micro refermé (inactivité)")
            if not self._listener_ok:
                self._start_listener()
            else:
                state = self.listener.ensure_enabled()
                if state:
                    _log(f"santé: event tap {state}")

            if self.recorder.stalled() and time.time() - self._record_start > 2.0:
                _log("santé: micro coupé pendant la dictée (périphérique parti ?) → annulation, réouverture")
                self.hands_free = False
                self._suppress_next_release = True
                self._cancel_recording()
                _notify("Micro coupé", "Le micro a disparu pendant la dictée (AirPods ?). Réessaie, il est rouvert.")
                self._open_mic()

            if self.hands_free and self.recorder.recording:
                if time.time() - self._record_start > MAX_RECORD_S:
                    _log("mains-libres: limite de durée atteinte, arrêt auto")
                    self.hands_free = False
                    self._finish_recording()

            if self._busy and time.time() - self._busy_since > BUSY_TIMEOUT_S:
                _log("santé: pipeline coincé, déblocage forcé")
                self._busy = False
                self.overlay.hide()
                self.title = self._idle_icon()

            self._meeting_health()

            active = self.recorder.recording or self._busy
            if active:
                self._idle_since = time.time()
            elif self.transcriber is not None and self.title != self._idle_icon() \
                    and time.time() - self._idle_since > STALE_UI_S:
                _log("santé: UI bloquée, remise à zéro")
                self.hands_free = False
                self._suppress_next_release = False
                self.listener.release()
                self.overlay.hide()
                self.title = self._idle_icon()
        except Exception:
            _log("erreur health_check:\n" + traceback.format_exc())

    # ---------- enregistrement ----------

    def _start_recording(self):
        self._ctx_app = frontmost_app()
        live = self.config.live_enabled and self.transcriber is not None
        try:
            self.recorder.start(live=live)
        except Exception as exc:
            _log("échec ouverture micro:\n" + traceback.format_exc())
            _notify("Micro indisponible", str(exc))
            return
        self._record_start = time.time()
        self.title = ICON_RECORDING
        self.overlay.begin_recording(hands_free=self.hands_free)
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
            session = StreamSession(self.transcriber, context=self._asr_prompt())
            while not run.abort:
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
                # Le direct et la dictée finale partagent une seule Session Qwen3-ASR :
                # on sérialise chaque appel MLX, sinon les deux threads se marchent dessus.
                with self.model_lock:
                    session.feed(np.concatenate(parts))
                if session.text != last_shown:
                    last_shown = session.text
                    _on_main(lambda t=last_shown: self.overlay.set_text(t))
            if run.abort:
                session.abort()
            else:
                with self.model_lock:
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
        self.title = self._idle_icon()

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
            self.title = self._idle_icon()
            return
        voiced = self.recorder.voiced_s
        if voiced < MIN_VOICED_S:
            _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s mais {voiced:.2f}s de voix (bruit {20*np.log10(self.recorder.noise_floor+1e-9):.0f} dBFS) → rien entendu, ignoré")
            self.overlay.hide()
            self.title = self._idle_icon()
            return
        _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s (voix {voiced:.1f}s, gain {self.recorder.gain_db:+.0f} dB) → transcription")
        self.title = ICON_PROCESSING
        self.overlay.show("processing")
        self.overlay.begin_progress(self._decode_estimate(len(audio) / SAMPLE_RATE))
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
                if live is not None and not live.done.is_set():
                    # Le direct traîne : on le coupe avant de reprendre le modèle à notre compte.
                    live.abort = True
                    live.done.wait(LIVE_JOIN_S)
                with self.model_lock:
                    t_dec = time.time()
                    text = self.transcriber.transcribe(audio, prompt=self._asr_prompt())
                    # Mesuré autour du seul décodage : le temps d'attente du direct ou
                    # du verrou fausserait l'estimation des prochaines dictées.
                    self._record_decode_time(len(audio) / SAMPLE_RATE, time.time() - t_dec)
                source = self.transcriber.name
            _on_main(self.overlay.end_progress)

            seconds = len(audio) / SAMPLE_RATE
            if text and len(text.split()) > seconds * 4.5 + 4:   # > 4,5 mots/s : impossible
                _log(f"rejeté : {len(text.split())} mots pour {seconds:.1f}s d'audio (hallucination probable)")
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
                dt = time.time() - t0
                _log(f"{how} en {dt:.1f}s via {source} ({words} mots, ton {tone}, app {app_name or '?'})"
                     + (f" — 2e passe : {retry}" if retry else "")
                     + (f" — LENT : {_mem_state()}" if dt > SLOW_S else ""))
            else:
                _log("transcription vide, rien à coller")
        except Exception as exc:
            _log("erreur pipeline:\n" + traceback.format_exc())
            _notify("Erreur", str(exc))
        finally:
            self._busy = False
            try:
                import mlx.core as mx
                mx.clear_cache()   # rend les tampons intermédiaires : moins de pages à swapper
            except Exception:
                pass

            def done():
                self.overlay.hide()
                self.title = self._idle_icon()

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
        return {
            "status": "Prêt · Qwen3-ASR" if self.transcriber is not None else "Chargement…",
            "icon": ICON_PATH,
            "tiles": [
                {"title": "Historique", "subtitle": f"{t['dictations']} dictées aujourd'hui", "color": (0.55, 0.40, 1.00),
                 "icon": "clock.arrow.circlepath", "on": True, "action": "history"},
                {"title": "Nettoyage IA", "subtitle": "Activé · +1 s" if self.config.cleanup_enabled else "Désactivé · instantané",
                 "color": (0.35, 0.95, 0.55), "icon": "wand.and.sparkles",
                 "on": self.config.cleanup_enabled, "action": "toggle", "payload": "cleanup_enabled"},
                {"title": "Réunion", "subtitle": (f"■ Arrêter · {_fmt_ts(self.meeting_rec.meeting.duration_s)}" if self.meeting_rec.active
                                                  else ("Résumé en cours…" if self._meeting_busy else "Micro + son système")),
                 "color": (1.00, 0.30, 0.32) if self.meeting_rec.active else (1.00, 0.62, 0.30),
                 "icon": "stop.circle" if self.meeting_rec.active else "person.wave.2",
                 "on": self.meeting_rec.active, "action": "meeting_toggle"},
                {"title": "Copier", "subtitle": (last[:34] + "…" if len(last) > 34 else last) if last else "Aucune dictée",
                 "color": (0.35, 0.70, 1.00), "icon": "doc.on.doc", "on": bool(last), "action": "copy_last"},
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
        elif action == "meeting_toggle":
            self.overlay.hide()
            self._meeting_toggle_clicked(None)
        elif action == "meeting_accept":
            app = self._offer_app
            self._offer_app = ""
            self.overlay.hide()
            self._meeting_start(app=app)
        elif action == "meeting_decline":
            self.detector.decline(self._offer_bundle)
            self._offer_app = ""
            self.overlay.hide()
        elif action == "dict":
            self.overlay.hide()
            subprocess.Popen(["open", "-t", DICT_PATH])

    def _open_history(self, _item):
        self.history_window.show()

    def _open_dictionary(self, _item):
        subprocess.Popen(["open", "-t", DICT_PATH])

    # ---------- réunions ----------

    def _idle_icon(self):
        return ICON_MEETING if self.meeting_rec.active else ICON_IDLE

    def _overlay_level(self):
        if self.recorder.recording:
            return self.recorder.level
        return self.meeting_rec.level if self.meeting_rec.active else 0.0

    def _meeting_info(self):
        m = self.meeting_rec.meeting
        return {
            "clock": _fmt_ts(m.duration_s) if (m and self.meeting_rec.active) else "00:00",
            "sys_level": self.meeting_rec.sys_level if self.meeting_rec.active else 0.0,
            "offer": f"Réunion {self._offer_app} détectée" if self._offer_app else "Réunion détectée",
        }

    def _meeting_folder(self):
        f = self.config.meeting_folder or DEFAULT_FOLDER
        try:
            os.makedirs(f, exist_ok=True)
        except OSError:
            pass
        return f

    def _meeting_transcribe(self, audio, prompt, language=""):
        if self.transcriber is None:
            raise RuntimeError("moteur non chargé")
        return self.transcriber.transcribe(audio, prompt=prompt, language=language)

    def _meeting_log_status(self):
        if not sysaudio.macos_ok():
            _log("réunions : son système indisponible (macOS < 14.2) — micro seul")
        elif sysaudio.helper_path() is None:
            _log("réunions : helper audiotap introuvable — micro seul (relance ./setup.sh)")

    def _set_summary_model(self, key):
        self.config.meeting_summary_model = key
        self.summarizer.set_model(key)
        for k, it in self._summary_items.items():
            it.state = k == key
        if key == "qwen-4b":
            _notify("Résumé", "Qwen3 4B (~2,5 Go) sera téléchargé à la fin de la prochaine réunion.")

    def _set_meeting_lang(self, key):
        self.config.meeting_language = key
        self.meeting_rec.language = key
        for k, it in self._lang_items.items():
            it.state = k == key

    def _meeting_health(self):
        """Appelé toutes les 2 s : détection d'appel, expiration de la proposition, fin auto."""
        rec = self.meeting_rec
        if self.overlay.state == "meeting_offer" and time.time() - self._offer_t0 > OFFER_TIMEOUT_S:
            self._offer_app = ""
            self.overlay.hide()
        if not self.config.meeting_auto_detect or self.transcriber is None:
            return
        # ignore_mic ne sert plus que sur le chemin de repli : la détection par processus
        # exclut déjà notre propre PID, donc elle reste fiable pendant qu'on dicte.
        ignore = self.recorder.open_ or rec.active
        res = self.detector.poll(ignore_mic=ignore, recording=rec.active)
        if res is None:
            return
        kind, name = res
        if kind == "offer" and not rec.active and not self._meeting_busy and self.overlay.state in ("idle", "hover"):
            self._offer_app = name
            self._offer_bundle = self.detector.offered
            self._offer_t0 = time.time()
            self.overlay.offer_meeting()
            _log(f"réunion détectée ({name}) : proposition affichée")
        elif kind == "ended" and rec.active:
            _log(f"réunion : {name} fermée → arrêt automatique")
            _notify("Réunion terminée", f"{name} est fermée : je termine et je résume.")
            self._meeting_stop()

    def _meeting_toggle_clicked(self, _item):
        if self.meeting_rec.active:
            self._meeting_stop()
        elif not self._meeting_busy:
            self._meeting_start()

    def _meeting_start(self, app=""):
        if self.meeting_rec.active or self._meeting_busy:
            return
        if self.transcriber is None:
            _notify("Réunion", "Le moteur de transcription n'est pas encore chargé.")
            return
        if not app:
            from .meeting_detect import running_call_app, frontmost_browser
            found = running_call_app() or frontmost_browser()
            app = found[0] if found else ""
        try:
            m = self.meeting_rec.start(app=app)
            self.detector.began(app)
        except Exception as exc:
            _log("réunion : démarrage impossible\n" + traceback.format_exc())
            _notify("Réunion", f"Impossible de démarrer : {exc}")
            return
        self.item_meet_start.title = "■ Arrêter la réunion"
        self.title = self._idle_icon()
        self.overlay.set_meeting(True)
        self.overlay.refresh()
        self.live_window.show(m)
        self._play(SOUND_START)
        if self.meeting_rec.mic_error:
            _notify("Réunion", "Micro indisponible : seul le son système est enregistré.")
        if self.meeting_rec.tap_warning:
            _notify("Réunion", self.meeting_rec.tap_warning)
        self.tutorial.event("meeting")
        threading.Timer(25, lambda: _on_main(self._meeting_check_tap)).start()

    def _meeting_check_tap(self):
        w = self.meeting_rec.tap_warning
        if self.meeting_rec.active and w and "autorisation" in w:
            _notify("Son système muet", "Réglages → Confidentialité → Enregistrement de l'écran et audio système → LocalFlow.")
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _meeting_cancel(self):
        if not self.meeting_rec.active:
            return
        self.meeting_rec.stop(wait_s=2)
        self.meeting_rec.cleanup_cache(keep_as="last-cancelled")   # audio gardé pour diagnostiquer
        self._meeting_reset_ui()
        _log("réunion annulée")

    def _meeting_reset_ui(self):
        self.item_meet_start.title = "Démarrer une réunion"
        self.overlay.set_meeting(False)
        self.overlay.refresh()
        self.title = self._idle_icon()
        self.live_window.close()

    def _meeting_stop(self):
        rec = self.meeting_rec
        if not rec.active or self._meeting_busy:
            return
        self._meeting_busy = True
        self.item_meet_start.title = "Résumé en cours…"
        self.live_window.set_busy(True, "Fin de la transcription, puis résumé… (quelques dizaines de secondes)")
        self._play(SOUND_STOP)

        def work():
            m = None
            try:
                m = rec.stop()
                _on_main(lambda: self.overlay.set_meeting(False))   # UI : thread principal uniquement
                _on_main(lambda: self.live_window.set_busy(True, f"{len(m.segments)} tours transcrits · rédaction du compte rendu…"))
                folder = self._meeting_folder()
                summary = ""
                if m.segments:
                    try:
                        summary = self.summarizer.summarize(m.plain_transcript(), notes=m.notes, vocab=self.dictionary.words)
                    except Exception:
                        _log("réunion : résumé impossible\n" + traceback.format_exc())
                    try:
                        m.title = self.summarizer.title(m.plain_transcript(), summary) or (m.app and f"Réunion {m.app}") or "Réunion"
                    except Exception:
                        m.title = (m.app and f"Réunion {m.app}") or "Réunion"
                else:
                    m.title = (m.app and f"Réunion {m.app}") or "Réunion"
                m.summary = summary
                path = write_markdown(m, folder)
                if self.config.meeting_keep_audio and m.duration_s > 5:
                    rec.export_audio(folder)
                    write_markdown(m, folder)   # ré-écrit avec le lien audio
                first = self.summarizer.first_line(summary) if summary else (m.plain_transcript()[:160] if m.segments else "")
                self.meeting_index.add(m, first)
                rec.cleanup_cache(keep_as="last")   # me.wav / them.wav de la dernière réunion (diagnostic)
                _log(f"réunion enregistrée : {os.path.basename(path)}")

                def done():
                    self._meeting_busy = False
                    self._meeting_reset_ui()
                    self.meetings_window.current = None
                    self.meetings_window.show()
                    self.overlay.refresh()
                _on_main(done)
                _notify("Compte rendu prêt", m.title)
            except Exception as exc:
                _log("réunion : erreur de fin\n" + traceback.format_exc())
                _notify("Réunion", f"Erreur en fin de réunion : {exc}. L'audio est dans ~/Library/Caches/LocalFlow/meetings.")

                def fail():
                    self._meeting_busy = False
                    self._meeting_reset_ui()
                _on_main(fail)

        threading.Thread(target=work, daemon=True).start()

    def _meeting_ask(self, entry, question):
        """Depuis un thread de la fenêtre Réunions."""
        try:
            with open(entry["path"], "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return "Fichier introuvable."
        summary, transcript = text, ""
        if "## Transcript" in text:
            summary, transcript = text.split("## Transcript", 1)
        return self.summarizer.ask(question, transcript or text, summary)

    def _meeting_delete(self, entry):
        self.meeting_index.remove(entry.get("id"), delete_files=True)
        _notify("Supprimée", entry.get("title", "Réunion"))

    def _play(self, sound):
        if self.config.sounds_enabled:
            sounds.play(sound)

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
