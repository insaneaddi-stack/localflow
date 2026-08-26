"""Détection d'une réunion en cours, sans aucune autorisation supplémentaire.

  « CETTE app capture le micro en ce moment »  ⇒  elle est en appel.

Un seul signal, exact, au lieu de deux devinés. CoreAudio (macOS 14.4+) énumère les
processus audio et dit lesquels capturent l'entrée — on obtient donc le PID de l'app
réellement en communication.

Ce que ça corrige, et pourquoi l'ancienne version se trompait tout le temps :

- Elle nommait la mauvaise app. `running_call_app()` renvoyait la première app d'appel
  LANCÉE. WhatsApp, Slack, Discord, Telegram tournent en permanence : ils étaient donc
  désignés dès que n'importe quoi touchait au micro.
- Elle ne savait pas qui utilisait le micro, seulement que « quelqu'un » l'utilisait —
  d'où le paramètre `ignore_mic` quand notre dictée tournait, qui aveuglait la détection
  au lieu de la préciser. On exclut maintenant notre propre PID, exactement.
- La réunion ne se terminait jamais seule. La fin auto exigeait que l'app d'appel QUITTE ;
  personne ne quitte WhatsApp, donc l'enregistrement tournait jusqu'au plafond de 4 h.
  On surveille maintenant l'arrêt de la CAPTURE, ce qui arrive à chaque fin d'appel.

Repli : sur un système où l'énumération par processus échoue, on retombe sur l'ancien
couple « app lancée + micro occupé ».
"""

import os
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
MIC_BUSY_START_S = 4.0      # l'app capture depuis au moins 4 s avant de proposer
APP_GONE_END_S = 20.0       # elle a cessé de capturer depuis 20 s ⇒ l'appel est fini
REOFFER_S = 20 * 60         # ne pas re-proposer la même app avant 20 min après un refus

def _fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")


# ---- qui capture le micro, exactement ----

def _capturing_pids():
    """PID des processus qui capturent l'entrée audio en ce moment.

    kAudioHardwarePropertyProcessObjectList ('prs#') énumère les objets processus ;
    kAudioProcessPropertyIsRunningInput ('piri') dit lesquels capturent l'entrée.
    Renvoie None si l'API n'est pas exploitable — l'appelant retombe sur l'ancien test.
    """
    try:
        import ctypes
        import ctypes.util

        ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))

        class Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]

        glob = _fourcc("glob")
        addr = Addr(_fourcc("prs#"), glob, 0)
        size = ctypes.c_uint32(0)
        if ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(size)) or not size.value:
            return None
        n = size.value // 4
        objs = (ctypes.c_uint32 * n)()
        if ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), objs):
            return None
        out = []
        for obj in objs:
            a = Addr(_fourcc("piri"), glob, 0)
            val = ctypes.c_uint32(0)
            sz = ctypes.c_uint32(4)
            if ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(sz), ctypes.byref(val)) or not val.value:
                continue
            a = Addr(_fourcc("ppid"), glob, 0)
            pid = ctypes.c_int32(0)
            sz = ctypes.c_uint32(4)
            if ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(sz), ctypes.byref(pid)):
                continue
            out.append(int(pid.value))
        return out
    except Exception:
        return None


def capturing_apps(exclude_self=True):
    """[(nom, bundle)] des apps connues qui capturent le micro. None si indéterminable.

    Le PID vient de CoreAudio, le bundle de NSRunningApplication : on évite ainsi de
    gérer la durée de vie d'un CFString renvoyé par AudioObjectGetPropertyData.
    """
    pids = _capturing_pids()
    if pids is None:
        return None
    if exclude_self:
        me = os.getpid()
        pids = [p for p in pids if p != me]
    if not pids:
        return []
    try:
        from AppKit import NSWorkspace

        by_pid = {}
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            by_pid[int(app.processIdentifier())] = app.bundleIdentifier() or ""
    except Exception:
        return None
    out = []
    for p in pids:
        bid = by_pid.get(p, "")
        if not bid:
            continue
        name = CALL_APPS.get(bid) or BROWSERS.get(bid)
        if name:
            out.append((name, bid))
    return out

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
        """L'enregistrement démarre : mémorise l'app d'appel à surveiller."""
        caps = capturing_apps()
        found = None
        if caps:
            # celle qui capture, et si l'app est nommée, celle qui porte ce nom
            found = next((c for c in caps if not app_name or c[0] == app_name), caps[0])
        elif caps is None:
            legacy = running_call_app()
            found = legacy if (legacy and (not app_name or legacy[0] == app_name)) else None
        self.start_app = found
        self.gone_since = None
        self.busy_since = self.free_since = None
        self.offered = None

    def poll(self, ignore_mic=False, recording=False):
        now = time.time()
        caps = capturing_apps()          # None = API indisponible → repli
        if recording:
            return self._poll_recording(now, caps)
        if caps is None:
            return self._poll_legacy(now, ignore_mic)

        # `ignore_mic` n'a plus lieu d'être : notre propre PID est déjà exclu, donc la
        # détection reste fiable pendant qu'on dicte.
        if not caps:
            self.busy_since = None
            self.offered = None
            return None
        name, bid = caps[0]
        if self.busy_since is None or self.offered != bid:
            self.busy_since = now
        if now - self.busy_since < MIC_BUSY_START_S:
            return None
        if now - self.declined.get(bid, 0) < REOFFER_S or self.offered == bid:
            return None
        self.offered = bid
        return ("offer", name)

    def _poll_recording(self, now, caps):
        """Pendant l'enregistrement : l'appel est fini quand l'app CESSE DE CAPTURER.

        L'ancienne version attendait qu'elle quitte — ce qui n'arrive jamais pour
        WhatsApp ou Slack, donc l'enregistrement ne s'arrêtait pas de lui-même.
        """
        if self.start_app is None:
            return None
        name, bid = self.start_app
        if caps is None:
            try:
                from AppKit import NSWorkspace
                still = any((a.bundleIdentifier() or "") == bid
                            for a in NSWorkspace.sharedWorkspace().runningApplications())
            except Exception:
                still = True
        else:
            still = any(c[1] == bid for c in caps)
        if still:
            self.gone_since = None
            return None
        self.gone_since = self.gone_since or now
        if now - self.gone_since > APP_GONE_END_S:
            self.start_app = None
            return ("ended", name)
        return None

    def _poll_legacy(self, now, ignore_mic):
        """Repli quand l'énumération par processus est indisponible : ancien
        comportement, « app d'appel lancée + micro occupé »."""
        busy = False if ignore_mic else mic_in_use()
        if busy:
            self.busy_since = self.busy_since or now
        else:
            self.busy_since = None
            self.offered = None
            return None
        if now - self.busy_since < MIC_BUSY_START_S:
            return None
        app = running_call_app() or frontmost_browser()
        if app is None:
            return None
        name, bid = app
        if now - self.declined.get(bid, 0) < REOFFER_S or self.offered == bid:
            return None
        self.offered = bid
        return ("offer", name)
