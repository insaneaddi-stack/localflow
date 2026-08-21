"""Détection d'une réunion en cours, sans aucune autorisation supplémentaire :

  « une app d'appel tourne »  +  « le micro est utilisé par une autre app »  ⇒  réunion probable.

- Apps d'appel : Zoom, Teams, FaceTime, Webex, Discord, Slack, Google Meet (app PWA)…
- Navigateur (Meet/Teams web) : on ne peut pas lire l'onglet sans autorisation d'enregistrement
  d'écran ; on se contente de « navigateur au premier plan + micro utilisé ».
- Micro utilisé : propriété CoreAudio `kAudioDevicePropertyDeviceIsRunningSomewhere` du
  périphérique d'entrée par défaut (vrai aussi quand NOTRE dictée tourne → l'app passe
  `ignore=True` dans ce cas).

Pendant l'enregistrement, c'est NOTRE micro qui est ouvert : « micro utilisé » ne dit plus
rien. La fin automatique repose donc sur l'app d'appel : si celle qui tournait au départ
(Zoom, Teams…) se ferme pendant APP_GONE_END_S, la réunion est terminée. Réunion dans un
navigateur : pas de fin auto, l'utilisateur termine à la main.
"""

import time

CALL_APPS = {
    "us.zoom.xos": "Zoom",
    "com.microsoft.teams2": "Teams",
    "com.microsoft.teams": "Teams",
    "com.apple.FaceTime": "FaceTime",
    "com.cisco.webexmeetingsapp": "Webex",
    "com.webex.meetingmanager": "Webex",
    "com.hnc.Discord": "Discord",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.google.Chrome.app.kjgfgldnnfoeklkmfkjfagphfepbbdan": "Google Meet",
    "com.skype.skype": "Skype",
    "com.whereby.app": "Whereby",
    "com.loom.desktop": "Loom",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "ru.keepcoder.Telegram": "Telegram",
    "com.ringcentral.glip": "RingCentral",
    "com.around.app": "Around",
    "com.livestorm.app": "Livestorm",
}
BROWSERS = {
    "com.google.Chrome": "Chrome", "com.apple.Safari": "Safari", "org.mozilla.firefox": "Firefox",
    "com.microsoft.edgemac": "Edge", "com.brave.Browser": "Brave", "company.thebrowser.Browser": "Arc",
    "com.vivaldi.Vivaldi": "Vivaldi", "com.operasoftware.Opera": "Opera", "com.apple.SafariTechnologyPreview": "Safari",
}
MIC_BUSY_START_S = 4.0      # micro utilisé depuis au moins 4 s avant de proposer
APP_GONE_END_S = 20.0       # l'app d'appel du départ a disparu depuis 20 s ⇒ la réunion est finie
REOFFER_S = 20 * 60         # ne pas re-proposer la même app avant 20 min après un refus

def _fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")

def mic_in_use():
    """Vrai si un processus (nous compris) lit le micro par défaut. False si indéterminable."""
    try:
        import CoreAudio as CA
        addr = (CA.kAudioHardwarePropertyDefaultInputDevice, CA.kAudioObjectPropertyScopeGlobal, CA.kAudioObjectPropertyElementMain)
        err, _, dev = CA.AudioObjectGetPropertyData(CA.kAudioObjectSystemObject, addr, 0, None, 4, None)
        if err or dev is None:
            return False
        dev_id = int.from_bytes(dev, "little") if isinstance(dev, (bytes, bytearray)) else int(dev)
        addr = (_fourcc("gone"), CA.kAudioObjectPropertyScopeGlobal, CA.kAudioObjectPropertyElementMain)  # kAudioDevicePropertyDeviceIsRunningSomewhere
        err, _, val = CA.AudioObjectGetPropertyData(dev_id, addr, 0, None, 4, None)
        if err or val is None:
            return False
        return bool(int.from_bytes(val, "little") if isinstance(val, (bytes, bytearray)) else int(val))
    except Exception:
        return _mic_in_use_ctypes()

def _mic_in_use_ctypes():
    try:
        import ctypes
        import ctypes.util
        ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))

        class Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]

        glob, main_ = _fourcc("glob"), 0
        dev = ctypes.c_uint32(0)
        size = ctypes.c_uint32(4)
        a = Addr(_fourcc("dIn "), glob, main_)
        if ca.AudioObjectGetPropertyData(1, ctypes.byref(a), 0, None, ctypes.byref(size), ctypes.byref(dev)) != 0:
            return False
        val = ctypes.c_uint32(0)
        size = ctypes.c_uint32(4)
        a = Addr(_fourcc("gone"), glob, main_)
        if ca.AudioObjectGetPropertyData(dev.value, ctypes.byref(a), 0, None, ctypes.byref(size), ctypes.byref(val)) != 0:
            return False
        return bool(val.value)
    except Exception:
        return False

def running_call_app():
    """(nom, bundle) de la première app d'appel lancée, sinon None."""
    try:
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            bid = app.bundleIdentifier() or ""
            if bid in CALL_APPS:
                return CALL_APPS[bid], bid
    except Exception:
        pass
    return None

def frontmost_browser():
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        bid = app.bundleIdentifier() or ""
        if bid in BROWSERS:
            return BROWSERS[bid], bid
    except Exception:
        pass
    return None


class MeetingDetector:
    """poll(ignore_mic) → None | ("offer", nom_app) | ("ended", None). Appeler toutes les 2-3 s."""

    def __init__(self):
        self.busy_since = None
        self.free_since = None
        self.declined = {}       # bundle → instant du refus
        self.offered = None      # bundle proposé en ce moment
        self.start_app = None    # (nom, bundle) de l'app d'appel au début de l'enregistrement
        self.gone_since = None

    def decline(self, bundle):
        self.declined[bundle or "?"] = time.time()
        self.offered = None

    def began(self, app_name=""):
        """L'enregistrement démarre : mémorise l'app d'appel à surveiller (si native)."""
        found = running_call_app()
        self.start_app = found if (found and (not app_name or found[0] == app_name)) else None
        self.gone_since = None
        self.busy_since = self.free_since = None
        self.offered = None

    def poll(self, ignore_mic=False, recording=False):
        now = time.time()
        if recording:
            if self.start_app is None:
                return None
            name, bid = self.start_app
            try:
                from AppKit import NSWorkspace
                alive = any((a.bundleIdentifier() or "") == bid for a in NSWorkspace.sharedWorkspace().runningApplications())
            except Exception:
                alive = True
            if alive:
                self.gone_since = None
            else:
                self.gone_since = self.gone_since or now
                if now - self.gone_since > APP_GONE_END_S:
                    self.start_app = None
                    return ("ended", name)
            return None
        busy = False if ignore_mic else mic_in_use()
        if busy:
            self.busy_since = self.busy_since or now
            self.free_since = None
        else:
            self.free_since = self.free_since or now
            self.busy_since = None
        if not busy or now - self.busy_since < MIC_BUSY_START_S:
            self.offered = None
            return None
        app = running_call_app() or frontmost_browser()
        if app is None:
            return None
        name, bid = app
        if now - self.declined.get(bid, 0) < REOFFER_S or self.offered == bid:
            return None
        self.offered = bid
        return ("offer", name)
