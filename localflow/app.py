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
import re
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
from . import theme
from . import timetree
from .calendar_intent import CalendarIntent
from .cleanup import Cleaner, cleanup_rules
from .commands import UNDO, apply_commands
from .config import Config
from .context import frontmost_app, should_type, tone_for
from .dictionary import DICT_PATH, Dictionary
from .history_window import HistoryWindow
from .hotkey import FnListener, fn_down_now
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

# La barre de menus portait des emoji — le dessin de quelqu'un d'autre.
# Elle porte maintenant le monogramme, et l'état se lit à la forme du signe
# posé dessous (voir theme.menubar_icon).
OFFER_TIMEOUT_S = 25     # la proposition « enregistrer la réunion ? » disparaît toute seule

TAP_MAX_S = 0.3          # en dessous : c'est un tap, pas un push-to-talk
DOUBLE_TAP_S = 0.45      # deux taps rapprochés : ouvre/ferme le panneau
TAIL_S = 0.35            # audio conservé après le relâchement (dernier mot)
# Témoin lu par run.sh et update.sh avant de relancer l'agent : tant qu'il est
# frais, une dictée est en cours et un kickstart -k la ferait disparaître —
# c'est arrivé trois fois le 2 septembre, dont une après 25 s de parole.
BUSY_FILE = os.path.expanduser("~/Library/Caches/LocalFlow/busy")
DEBUG_WAV = os.path.expanduser("~/Library/Caches/LocalFlow/last.wav")  # dernière dictée, pour diagnostiquer
MIN_AUDIO_S = 0.35       # ignore les enregistrements plus courts
MIN_VOICED_S = 0.12      # seuil bas : le détecteur ne compte que les pics (silence pur = 0,00 s)
MAX_RECORD_S = 600       # arrêt auto d'une dictée qui n'en finit pas (mains-libres ou fn coincé)
FN_LOST_GRACE_S = 1.5    # délai avant de conclure que le relâchement de fn s'est perdu
FINISH_TIMEOUT_S = 5     # au-delà, la fin de dictée est considérée coincée
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
KEEP_WARM_WIRED_S = 600   # modèle verrouillé en mémoire : simple sonde, qui logue si ça ralentit encore
# Un seuil ABSOLU criait au loup : une dictée de 45 s met légitimement 5 s à
# décoder. Mesuré sur 943 dictées, les lignes « LENT » avaient un meilleur
# rapport temps/audio (0,125) que les normales (0,158) — 115 fausses alertes
# sur 117. On compare donc au temps ATTENDU, qui est déjà calibré sur la machine.
SLOW_FACTOR = 2.5    # au-delà de 2,5 × l'estimation, c'est une vraie anomalie
SLOW_FLOOR_S = 2.0   # et jamais en dessous de 2 s, pour ne pas pinailler

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

def _notify_osascript(title, message):
    """Repli : marche même sans bundle, mais la notification porte l'icône
    d'AppleScript — c'est macOS qui l'attribue au binaire qui l'envoie."""
    safe = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{safe(message)}" with title "{safe(theme.NOM)}" subtitle "{safe(title)}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        print(f"{theme.NOM} — {title}: {message}")

def _notify(title, message):
    """Notification macOS, postée PAR L'APP pour qu'elle porte son icône.

    Tout passait par osascript : macOS attribuait alors la notification au
    binaire AppleScript et affichait son icône générique. Le nom « AUR'IAFLOW »
    n'était que du texte glissé dans le titre. Envoyée depuis le bundle signé,
    elle porte l'icône et le nom de la marque, et le titre redevient libre.
    """
    if title.lower().startswith("erreur") or "indisponible" in title.lower() or "requise" in title.lower():
        _log(f"ERREUR {title}: {message}")

    def poster():
        try:
            rumps.notification(title, "", message)
        except Exception:
            _notify_osascript(title, message)

    try:
        _on_main(poster)      # deliverNotification_ veut le thread principal
    except Exception:
        _notify_osascript(title, message)   # avant que NSApp existe

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

ESC_KEYCODE = 53


def esc_target(recording, finishing, busy, panel_open):
    """Ce que la touche Esc doit faire, ou None si elle doit passer à l'app active.

    « dictation » prime sur « panel » : si une dictée tourne, c'est elle qu'on
    annule. Hors de ces deux cas Esc n'est jamais avalé — sinon on casserait
    l'échappement de tout le système pour l'utilisateur.
    """
    if recording or finishing or busy:
        return "dictation"
    if panel_open:
        return "panel"
    return None


def job_aborted(gen, abort_gen):
    """Vrai si la dictée numéro `gen` a été annulée (elle ou une plus récente)."""
    return gen <= abort_gen


SHIFT_GRACE_S = 0.6      # ⇧ arrivé après fn compte encore comme un accord


def mode_upgrade(mode, elapsed_s, recording, calendar_enabled):
    """⇧ enfoncé APRÈS fn : est-ce encore le même geste ?

    « fn+⇧ en même temps » ne l'est jamais vraiment. Mesuré en usage réel : le
    mode n'était jamais armé, parce que fn descendait systématiquement avant ⇧.
    On accepte donc ⇧ tant qu'on est dans les premières fractions de seconde —
    avant que la phrase ait commencé. Au-delà, c'est un ⇧ sans rapport et le
    mode ne bouge plus.
    """
    if mode == "calendar" or not calendar_enabled or not recording:
        return mode
    return "calendar" if 0.0 <= elapsed_s <= SHIFT_GRACE_S else mode


def dictation_mode(shift_held, calendar_enabled):
    """Où va cette dictée : « calendar » (TimeTree) ou « dictation » (tapée).

    L'accord fn+⇧ se juge au seul instant où fn descend. Réglage éteint ou ⇧
    relâché : on retombe sur la dictée normale — le mode dégradé doit toujours
    être celui qui ne surprend personne.
    """
    return "calendar" if (shift_held and calendar_enabled) else "dictation"


class LocalFlowApp(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)
        self._icon_state = None
        self._set_icon("loading")
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

        # Les fontes sont enregistrées MAINTENANT, pas au premier dessin :
        # un CTFontManagerRegisterFontsForURL déclenché depuis drawRect_ fait de
        # l'entrée/sortie disque au milieu d'une image.
        loaded = theme.register_fonts()
        if len(loaded) < 3:
            _log(f"polices : {len(loaded)}/3 chargées {loaded} — repli sur les polices système")

        self.hands_free = False
        self._press_time = None
        self._record_start = 0.0
        self._suppress_next_release = False
        self._busy = False
        self._busy_since = 0.0
        # Chaque dictée porte un numéro. Esc note celui qu'il annule, et le
        # worker compare : un job annulé ne colle rien, et un job périmé ne
        # vient pas ranger l'UI d'une dictée plus récente.
        self._gen = 0
        self._abort_gen = -1
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
        self.item_calendar = self._toggle_item("Agenda : fn+⇧ envoie dans TimeTree", "calendar_enabled",
                                               self._toggle_calendar)
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
            self.item_calendar,
            None,
            self.item_perms,
            self.item_tutorial,
            self.item_update,
            self.item_auto_update,
            rumps.MenuItem("Quitter", callback=rumps.quit_application),
        ]
        self._brand_menu()
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
        # Même Qwen3-1.7B que le nettoyage : le partage évite un second Go en RAM.
        self.calendar = CalendarIntent(shared=self.cleaner)
        if self.config.calendar_enabled:
            # Journalisé, pas notifié : l'agent redémarre tout seul et une
            # notification à chaque lancement finirait par ne plus être lue.
            for manque in timetree.prerequisites(self.config.calendar_mcp_path):
                _log(f"agenda indisponible : {manque}")
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
        self._mode = "dictation"   # latché au fn down : « calendar » si ⇧ était tenu
        # Le tap tourne sur son propre thread : on renvoie chaque callback sur le thread principal.
        self.listener = FnListener(
            lambda shift: _on_main(lambda: self._on_fn_down(shift)), lambda: _on_main(self._on_fn_up),
            lambda: _on_main(self._on_fn_space), lambda: _on_main(self._on_fn_other), self._on_key,
            lambda: _on_main(self._on_shift),
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
            _notify("AUR'IAFLOW mis à jour", f"Nouvelle version installée ({sha[:7]}).")
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
                    self.item_update.title = "Mise à jour disponible — installer…"
                    if first:
                        _log(f"mise à jour disponible : {sha[:7]}")
                        if self.config.auto_update and not update.IS_DEV:
                            self._auto_update_when_idle()
                        else:
                            _notify("Mise à jour disponible", "Dans le menu de la barre : « Mise à jour disponible — installer… ».")
                else:
                    self._update_sha = None
                    self.item_update.title = "Vérifier les mises à jour"
                    if notify_if_none:
                        _notify("AUR'IAFLOW", "Tu as la dernière version.")
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
        _notify("Mise à jour", "AUR'IAFLOW se met à jour en arrière-plan (quelques secondes)…")
        update.run_silent()

    def _check_update_clicked(self, _item):
        if self._update_sha:
            update.launch()
        else:
            self._check_update(notify_if_none=True)

    def _brand_menu(self):
        """Donne au menu natif nos propres vues.

        Les titres attribués ne suffisaient pas : le fond, la surbrillance, les
        marges et la coche restaient ceux de macOS, et le menu continuait d'être
        la seule surface à ne pas appartenir à la marque. `localflow.menu`
        redessine chaque ligne — et gère lui-même surbrillance et clic, que
        macOS n'assure plus dès qu'une vue est posée.
        """
        try:
            from . import menu as brand_menu

            # rumps range le NSMenu dans son objet Menu : App.menu → Menu,
            # Menu._menu → NSMenu.
            ns_menu = getattr(self.menu, "_menu", None)
            if ns_menu is None:
                return
            n = brand_menu.habiller(ns_menu, lambda: self.item_status.title)
            _log(f"menu : {n} entrées habillées")
        except Exception:
            _log("menu : habillage impossible, on garde le menu système\n"
                 + traceback.format_exc())

    def _mark_busy(self, busy):
        """Pose ou retire le témoin « dictée en cours ». Ne lève jamais."""
        try:
            if busy:
                os.makedirs(os.path.dirname(BUSY_FILE), exist_ok=True)
                with open(BUSY_FILE, "w") as f:
                    f.write(str(int(time.time())))
            elif os.path.exists(BUSY_FILE):
                os.remove(BUSY_FILE)
        except OSError:
            pass

    def _set_icon(self, state):
        """Pose le monogramme dans la barre de menus. Le texte reste vide.

        Si le rendu échoue (monogramme absent, cache non inscriptible), on
        retombe sur le nom du produit plutôt que sur une barre vide.
        """
        if state == self._icon_state:
            return
        self._icon_state = state
        self._mark_busy(state in ("recording", "hands_free", "processing", "meeting"))
        path = theme.menubar_icon(state)
        try:
            if path:
                self.icon = path
                self.title = ""
            else:
                self.icon = None
                self.title = theme.NOM
        except Exception:
            self.title = theme.NOM

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

    def _repair_model_cache(self):
        """Efface les téléchargements interrompus du cache Hugging Face.

        `IncompleteSnapshotError` était la deuxième cause d'échec de démarrage du
        moteur dans le log (14 occurrences) : un téléchargement coupé laisse des
        fichiers `.incomplete`, et réessayer en boucle ne les répare pas — il
        faut les retirer pour que le téléchargement reprenne. On ne touche qu'à
        ces fichiers-là : les poids déjà complets ne sont jamais retéléchargés.
        """
        import glob

        efface = 0
        for base in (os.path.expanduser("~/.cache/huggingface/hub"),
                     os.environ.get("HF_HOME", "")):
            if not base or not os.path.isdir(base):
                continue
            for f in glob.glob(os.path.join(base, "**", "*.incomplete"), recursive=True):
                try:
                    taille = os.path.getsize(f)
                    os.remove(f)
                    efface += 1
                    _log(f"cache modèle : fragment incomplet retiré ({taille // 1024} Ko)")
                except OSError:
                    pass
        return efface

    def _worker(self):
        """Thread de traitement. Ne meurt jamais : relance le chargement du
        modèle en cas d'échec, et survit à toute erreur d'un job."""
        delay = 5
        notified = False
        repare = False
        while self.transcriber is None:
            try:
                from .transcribe import Transcriber

                self.transcriber = Transcriber()
            except Exception as exc:
                _log("échec chargement du moteur:\n" + traceback.format_exc())
                # Une seule tentative de réparation : si elle ne suffit pas, le
                # problème est ailleurs (réseau, disque plein) et effacer en
                # boucle ne ferait que retélécharger sans fin.
                if not repare and "Incomplete" in type(exc).__name__:
                    repare = True
                    if self._repair_model_cache():
                        _log("cache modèle réparé, nouvelle tentative immédiate")
                        delay = 5
                        continue
                # Une seule notification : la boucle peut tourner des heures (modèle en
                # cours de téléchargement, disque plein…), inutile de noyer le Centre de
                # notifications. L'état reste visible dans le menu et dans le log.
                if not notified:
                    _notify("Modèle indisponible", f"AUR'IAFLOW réessaie en boucle — {exc}")
                    notified = True
                _on_main(lambda d=delay: setattr(self.item_status, "title", f"Modèle indisponible — nouvel essai dans {d} s"))
                time.sleep(delay)
                delay = min(delay * 2, 60)

        def ready():
            self._set_icon(self._idle_state())
            self.item_status.title = "Prêt — maintenir fn, ou fn+espace"
            first = not self.config.data.get("onboarded")
            if self.perms.missing():
                self.perms.show(on_done=(self.tutorial.show if first else None))
            elif first:
                self.tutorial.show()

        _on_main(ready)
        _log(f"démarrage: moteur {self.transcriber.name} chargé, prêt")
        wired = getattr(self.transcriber, "wired", 0)
        _log(f"mémoire: modèle verrouillé ({wired / 2**30:.1f} Go), plus de swap possible" if wired
             else "mémoire: verrouillage impossible, keep-warm toutes les 30 s")
        keep_warm_s = KEEP_WARM_WIRED_S if wired else KEEP_WARM_S
        try:
            os.remove(CRASH_FILE)      # démarrage réussi : l'ardoise est effacée
        except OSError:
            pass

        # Qwen se charge après le « prêt » : la première dictée n'attend pas.
        if self.config.cleanup_enabled:
            self.cleaner.preload()
            _log("démarrage: Qwen chargé")

        while True:
            try:
                try:
                    kind, payload = self._jobs.get(timeout=keep_warm_s)
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

    def _on_fn_down(self, shift=False):
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
        self._mode = dictation_mode(shift, self.config.calendar_enabled)
        _log("fn down" + (" + ⇧ → agenda" if self._mode == "calendar" else ""))
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

    def _on_shift(self):
        """⇧ enfoncé pendant que fn l'est déjà : rattrape l'accord fn+⇧."""
        avant = self._mode
        self._mode = mode_upgrade(
            self._mode,
            time.time() - self._press_time if self._press_time else 99.0,
            self.recorder.recording,
            self.config.calendar_enabled,
        )
        if self._mode != avant:
            self.overlay.rec_calendar = True   # point orange : le mode est armé
            _log("⇧ juste après fn → agenda")

    def _on_fn_space(self):
        """fn + espace : bascule en mains-libres (fn seul pour terminer)."""
        if self.hands_free or not self.recorder.recording:
            return
        self.hands_free = True
        self.overlay.rec_hands_free = True   # le fil se coud sous l'onde : ça continue sans toi
        self._suppress_next_release = True
        self._cancel_start_sound_timer()
        self._play(SOUND_START)
        self._set_icon("hands_free")
        _log("mains-libres activé")
        self.tutorial.event("handsfree")

    def _on_key(self, keycode):
        """Esc annule la dictée en cours ; panneau ouvert : 1-4 copie une bulle, Esc ferme.
        Appelé depuis le thread du tap : décision immédiate, action sur le thread principal."""
        if keycode == ESC_KEYCODE:
            target = esc_target(self.recorder.recording, self._finishing, self._busy,
                                self.overlay.state == "expanded")
            if target == "dictation":
                # Posé ici, pas dans _cancel_dictation : le worker peut coller
                # d'un instant à l'autre et il lit ce numéro, pas l'UI.
                self._abort_gen = self._gen
                _on_main(self._cancel_dictation)
                return True     # avalé : l'app active ne voit pas l'Esc
            if target == "panel":
                _on_main(self.overlay.hide)
                return True
            return False
        if self.overlay.state != "expanded":
            return False
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
            self.item_status.title = "Accessibilité manquante — à accorder"
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
                _notify("AUR'IAFLOW redémarre", "La couche audio de macOS s'est figée : redémarrage automatique (~10 s).")
                threading.Timer(1.2, lambda: os._exit(86)).start()
                return
            if self._lost_fn_release():
                _log("santé: relâchement de fn perdu → on termine la dictée")
                self.listener.release()   # le tap croit encore la touche enfoncée
                self._suppress_next_release = True
                self._finish_recording()  # on TERMINE : le texte de l'utilisateur n'est pas jeté
            if not self.recorder.recording:
                if self.recorder.open_ and self.recorder.muted(2.0) \
                        and time.time() - getattr(self, "_mute_reopen_t", 0.0) > 10.0:
                    self._mute_reopen_t = time.time()
                    _log(f"santé: flux muet (zéros exacts depuis {self.recorder.muted_for():.1f} s) → réouverture")
                    self._open_mic()
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

            if self.recorder.recording and time.time() - self._record_start > MAX_RECORD_S:
                _log("limite de durée atteinte, arrêt auto")
                self.hands_free = False
                self._suppress_next_release = True
                self._finish_recording()

            # `_finishing` ne dure normalement que TAIL_S ; s'il se coince (timer perdu,
            # thread principal occupé), _on_fn_down refuse toute nouvelle dictée en silence.
            if self._finishing and time.time() - getattr(self, "_finishing_since", 0) > FINISH_TIMEOUT_S:
                _log("santé: fin de dictée coincée, déblocage forcé")
                self._finishing = False
                self._finish_now()
            if self._busy and time.time() - self._busy_since > BUSY_TIMEOUT_S:
                _log("santé: pipeline coincé, déblocage forcé")
                self._busy = False
                self.overlay.hide()
                self._set_icon(self._idle_state())

            self._meeting_health()

            active = self.recorder.recording or self._busy
            if active:
                self._idle_since = time.time()
            elif self.transcriber is not None and self._icon_state != self._idle_state() \
                    and time.time() - self._idle_since > STALE_UI_S:
                _log("santé: UI bloquée, remise à zéro")
                self.hands_free = False
                self._suppress_next_release = False
                self.listener.release()
                self.overlay.hide()
                self._set_icon(self._idle_state())
        except Exception:
            _log("erreur health_check:\n" + traceback.format_exc())

    # ---------- enregistrement ----------

    def _start_recording(self):
        self._gen += 1
        self._ctx_app = frontmost_app()
        live = self.config.live_enabled and self.transcriber is not None
        try:
            self.recorder.start(live=live)
            if self.recorder.last_reopen:
                _log(f"micro rouvert avant la dictée : {self.recorder.last_reopen}")
        except Exception as exc:
            _log("échec ouverture micro:\n" + traceback.format_exc())
            _notify("Micro indisponible", str(exc))
            return
        self._record_start = time.time()
        self._set_icon("recording")
        self.overlay.begin_recording(hands_free=self.hands_free,
                                     calendar=self._mode == "calendar")
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

    def _lost_fn_release(self, now=None):
        """Vrai si on enregistre en push-to-talk alors que fn n'est plus enfoncé.

        macOS désactive parfois l'event tap tout seul (kCGEventTapDisabledByTimeout
        / ByUserInput), et la touche peut aussi être relâchée pendant la veille : le
        relâchement se perd et `_recording` reste à True pour toujours. Dans cet état
        AUCUN garde-fou ne rattrapait le coup — MAX_RECORD_S ne valait que pour le
        mains-libres, `stalled()` exige que le micro se taise (il continue à débiter),
        la fermeture du micro et la remise à zéro de l'UI sont toutes deux conditionnées
        à « on n'enregistre pas ». Le micro restait donc allumé indéfiniment.
        On interroge l'état physique de la touche, qui, lui, ne se perd pas.
        """
        if not self.recorder.recording or self.hands_free or self._finishing:
            return False
        if (now or time.time()) - self._record_start < FN_LOST_GRACE_S:
            return False
        return not fn_down_now()

    def _cancel_start_sound_timer(self):
        if self._start_sound_timer is not None:
            self._start_sound_timer.cancel()
            self._start_sound_timer = None

    def _cancel_dictation(self):
        """Esc : la dictée en cours est jetée — audio, transcription, collage.

        Ne touche pas à une réunion en cours : c'est long et délibéré, un Esc
        de réflexe coûterait l'enregistrement entier. Le bouton « Arrêter » du
        panneau reste le seul chemin.
        """
        self._abort_gen = self._gen
        self.hands_free = False
        self._finishing = False
        # `_busy` est libéré tout de suite pour que fn reparte sans attendre la
        # fin du décodage ; le job en vol se reconnaîtra périmé à son numéro.
        self._busy = False
        if self.recorder.recording:
            self._cancel_recording()
        else:
            self.overlay.hide()
            self._set_icon(self._idle_state())
        self._play(SOUND_STOP)
        _log("annulé (Esc)")

    def _cancel_recording(self):
        self._cancel_start_sound_timer()
        if self._live is not None:
            self._live.abort = True
        self.recorder.cancel()
        self.overlay.hide()
        self._set_icon(self._idle_state())

    def _finish_recording(self):
        """Laisse TAIL_S d'audio après le relâchement (le dernier mot n'est pas coupé)."""
        self._cancel_start_sound_timer()
        if self._finishing:
            return
        self._finishing = True
        self._finishing_since = time.time()
        self._play(SOUND_STOP)
        t = threading.Timer(TAIL_S, lambda: _on_main(self._finish_now))
        t.daemon = True
        t.start()

    def _finish_now(self):
        self._finishing = False
        if job_aborted(self._gen, self._abort_gen):
            # Esc est tombé pendant les TAIL_S : le minuteur arrive après la bataille.
            return
        audio = self.recorder.stop()
        if audio is None or len(audio) < MIN_AUDIO_S * SAMPLE_RATE:
            _log(f"audio trop court ou vide ({0 if audio is None else len(audio)/SAMPLE_RATE:.2f}s), ignoré")
            if self._live is not None:
                self._live.abort = True
            self.overlay.hide()
            self._set_icon(self._idle_state())
            return
        voiced = self.recorder.voiced_s
        if voiced < MIN_VOICED_S:
            if not np.any(audio):
                # Zéros exacts : ce n'est pas une pièce silencieuse, c'est un flux
                # muet. On le rouvre tout de suite, sans attendre le délai
                # d'inactivité, et on le dit : la phrase est à redire.
                _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s de silence numérique (flux muet depuis "
                     f"{self.recorder.muted_for():.1f} s, ouvert depuis {time.time()-self.recorder.opened_at:.0f} s) → réouverture du micro")
                self._open_mic()
                _notify("Micro muet", "Le micro ne livrait que du silence : il est rouvert. Redis ta phrase.")
            else:
                _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s mais {voiced:.2f}s de voix (bruit {20*np.log10(self.recorder.noise_floor+1e-9):.0f} dBFS) → rien entendu, ignoré")
            self.overlay.hide()
            self._set_icon(self._idle_state())
            return
        _log(f"audio {len(audio)/SAMPLE_RATE:.2f}s (voix {voiced:.1f}s, gain {self.recorder.gain_db:+.0f} dB) → transcription")
        self._set_icon("processing")
        self.overlay.show("processing")
        self.overlay.begin_progress(self._decode_estimate(len(audio) / SAMPLE_RATE))
        self._busy = True
        self._busy_since = time.time()
        self._jobs.put(("audio", {"audio": audio, "live": self._live, "app": self._ctx_app,
                                  "voiced": voiced, "gen": self._gen, "mode": self._mode}))
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
        gen = job.get("gen", 0)
        mode = job.get("mode", "dictation")
        self._save_debug(audio)
        try:
            if job_aborted(gen, self._abort_gen):
                if live is not None:
                    live.abort = True
                _log("annulé (Esc) : dictée jetée avant transcription")
                return
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
            # Dernier contrôle avant tout effet de bord : Esc a pu tomber
            # pendant le décodage, qui n'est pas interruptible en cours de route.
            if job_aborted(gen, self._abort_gen):
                _log("annulé (Esc) : transcription jetée, rien n'est collé")
                return
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

            if mode == "calendar" and text is not UNDO:
                self._to_calendar(text, gen)
                return

            if text is UNDO:
                press_undo()
                _log("commande vocale : annulation (Cmd+Z)")
            elif text:
                if should_type(bundle):
                    # Terminal : un retour à la ligne tapé = Entrée = commande lancée.
                    type_text(re.sub(r"\s*\n+(?:- )?", " ", text))
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
                attendu = self._decode_estimate(seconds)
                _log(f"{how} en {dt:.1f}s via {source} ({words} mots, ton {tone}, app {app_name or '?'})"
                     + (f" — 2e passe : {retry}" if retry else "")
                     + (f" — LENT : {dt:.1f}s pour {attendu:.1f}s attendus — {_mem_state()}"
                        if dt > max(SLOW_FLOOR_S, SLOW_FACTOR * attendu) else ""))
            else:
                _log("transcription vide, rien à coller")
        except Exception as exc:
            _log("erreur pipeline:\n" + traceback.format_exc())
            _notify("Erreur", str(exc))
        finally:
            try:
                import mlx.core as mx
                mx.clear_cache()   # rend les tampons intermédiaires : moins de pages à swapper
            except Exception:
                pass

            # Un job périmé (Esc, puis nouvelle dictée lancée aussitôt) ne doit
            # ni relâcher `_busy` ni ranger l'overlay : ils appartiennent à la
            # dictée en cours, pas à celle qu'on vient d'abandonner.
            if gen == self._gen:
                self._busy = False

                def done():
                    self.overlay.hide()
                    self._set_icon(self._idle_state())

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

    def _toggle_calendar(self, item):
        """Dit tout de suite ce qui manque, plutôt qu'au premier fn+⇧ raté."""
        if not item.state:
            return
        manques = timetree.prerequisites(self.config.calendar_mcp_path)
        if manques:
            _log("agenda : " + " | ".join(manques))
            _notify("Agenda pas prêt", manques[0])

    # ---------- agenda ----------

    def _to_calendar(self, text, gen):
        """Phrase comprise → aperçu annulable → TimeTree. Sur le thread worker.

        L'ordre compte : on chauffe le serveur MCP (lancement + login, ~1 s)
        PENDANT l'aperçu, mais on n'écrit qu'après. L'attente est donc invisible
        et l'annulation reste honnête — rien n'est parti tant qu'Esc est encore
        possible.
        """
        if not text:
            _log("agenda : transcription vide")
            _on_main(self.overlay.hide)
            return

        with self.model_lock:
            event = self.calendar.parse(text)
        if job_aborted(gen, self._abort_gen):
            _log("agenda : annulé (Esc) pendant la lecture de la phrase")
            return

        if event is None:
            # On ne colle rien dans l'app active : personne n'a demandé du texte,
            # on a demandé un événement. Mais la phrase ne doit pas disparaître.
            copy_text(text)
            self.config.add_history(text, app="Agenda")
            _on_main(self.history_window.refresh)
            _log("agenda : aucune date comprise, phrase mise dans le presse-papier")
            _notify("Aucune date comprise", "La phrase est dans le presse-papier.")
            _on_main(self.overlay.hide)
            return

        client = timetree.TimeTreeMCP(self.config.calendar_mcp_path, log=_log)
        chauffe = {"ok": False, "message": "chauffe non terminée"}

        def _chauffer():
            chauffe["ok"], chauffe["message"] = client.warm(self.config.calendar_id)

        fil = threading.Thread(target=_chauffer, daemon=True, name="timetree-warm")
        fil.start()

        delai = self.config.calendar_preview_s
        _on_main(lambda: self.overlay.begin_calendar_preview(
            f"{event['_libelle']} · {event['title']}", delai))
        _log(f"agenda : aperçu {delai:.0f} s — {event['_libelle']}")

        fin = time.time() + delai
        while time.time() < fin:
            if job_aborted(gen, self._abort_gen):
                _log("agenda : annulé (Esc) pendant l'aperçu, rien n'a été écrit")
                client.close()
                _on_main(self.overlay.end_calendar_preview)
                return
            time.sleep(0.05)

        fil.join(timeout=20)
        try:
            if not chauffe["ok"]:
                raise RuntimeError(chauffe["message"])
            ok, message = client.create(self.config.calendar_id, event)
        except Exception as exc:
            ok, message = False, str(exc)
        finally:
            client.close()
            _on_main(self.overlay.end_calendar_preview)

        if ok:
            _log(f"agenda : « {event['_libelle']} » créé")
            _notify("Ajouté à ton agenda", f"{event['_libelle']} · {event['title']}")
        else:
            timetree.log_failure(event, message)
            _log(f"agenda : échec — {message}")
            _notify("Événement non créé", f"{message[:120]} — gardé dans "
                                          f"{os.path.basename(timetree.JOURNAL_ECHECS)}")

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
                {"title": "Historique", "subtitle": f"{t['dictations']} dictées aujourd'hui",
                 "icon": "clock.arrow.circlepath", "on": True, "action": "history"},
                {"title": "Nettoyage IA", "subtitle": "Activé · +0,8 s" if self.config.cleanup_enabled else "Désactivé · instantané",
                 "icon": "wand.and.sparkles",
                 "on": self.config.cleanup_enabled, "action": "toggle", "payload": "cleanup_enabled"},
                {"title": "Réunion", "subtitle": (f"■ Arrêter · {_fmt_ts(self.meeting_rec.meeting.duration_s)}" if self.meeting_rec.active
                                                  else ("Résumé en cours…" if self._meeting_busy else "Micro + son système")),
                 "icon": "stop.circle" if self.meeting_rec.active else "person.wave.2",
                 "on": self.meeting_rec.active, "action": "meeting_toggle"},
                {"title": "Copier", "subtitle": (last[:34] + "…" if len(last) > 34 else last) if last else "Aucune dictée",
                 "icon": "doc.on.doc", "on": bool(last), "action": "copy_last"},
            ],
            "stats_line": f"Aujourd'hui · {t['words']} mots · {t['dictations']} dictées · ≈ {t['saved_min']:.0f} min gagnées",
            "toggles": [
                ("Nettoyage IA", "cleanup_enabled", self.config.cleanup_enabled),
                ("Ton auto", "tone_auto", self.config.tone_auto),
                ("Sons", "sounds_enabled", self.config.sounds_enabled),
                ("Agenda (fn+⇧)", "calendar_enabled", self.config.calendar_enabled),
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
            item = {"cleanup_enabled": self.item_cleanup, "tone_auto": self.item_tone,
                    "sounds_enabled": self.item_sounds, "calendar_enabled": self.item_calendar}[payload]
            item.state = not item.state
            setattr(self.config, payload, bool(item.state))
            if payload == "cleanup_enabled" and item.state:
                self._jobs.put(("preload", None))
            if payload == "calendar_enabled" and item.state:
                self._toggle_calendar(item)
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

    def _idle_state(self):
        return "meeting" if self.meeting_rec.active else "idle"

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
        self._set_icon(self._idle_state())
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
        self._set_icon(self._idle_state())
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
        print("AUR'IAFLOW tourne déjà.", flush=True)
        sys.exit(0)
    try:
        LocalFlowApp().run()
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())
        _signal_boucle_de_plantage()
        raise


CRASH_FILE = os.path.expanduser("~/Library/Caches/LocalFlow/crashes")
CRASH_FENETRE_S = 300      # trois échecs en cinq minutes = ça ne démarre plus
CRASH_SEUIL = 3
CRASH_SILENCE_S = 600      # une notification au plus toutes les dix minutes


def _signal_boucle_de_plantage():
    """Prévient quand l'app ne démarre plus du tout.

    KeepAlive relance l'agent toutes les 5 secondes : une erreur au démarrage
    devient une boucle silencieuse. Le 2 septembre elle a tourné 50 minutes sans
    que rien ne le dise — on s'en aperçoit en essayant de dicter, c'est-à-dire
    trop tard. Trois échecs en cinq minutes déclenchent une notification, avec
    la dernière ligne de l'erreur : de quoi savoir quoi faire sans ouvrir le log.
    """
    try:
        os.makedirs(os.path.dirname(CRASH_FILE), exist_ok=True)
        now = time.time()
        try:
            with open(CRASH_FILE) as f:
                vals = [float(x) for x in f.read().split()]
        except (OSError, ValueError):
            vals = []
        recents = [t for t in vals if now - t < CRASH_FENETRE_S]
        recents.append(now)
        dernier_avis = max((t for t in vals if t < 0), default=0.0)
        with open(CRASH_FILE, "w") as f:
            f.write(" ".join(f"{t:.0f}" for t in recents[-10:]))
            if dernier_avis:
                f.write(f" {dernier_avis:.0f}")
        if len(recents) < CRASH_SEUIL:
            return
        if now + dernier_avis < CRASH_SILENCE_S:   # dernier_avis est stocké négatif
            return
        ligne = (traceback.format_exc().strip().splitlines() or ["erreur inconnue"])[-1]
        _notify("AUR'IAFLOW ne démarre plus", ligne[:180])
        with open(CRASH_FILE, "w") as f:
            f.write(" ".join(f"{t:.0f}" for t in recents[-10:]) + f" {-now:.0f}")
    except Exception:
        pass

if __name__ == "__main__":
    main()
