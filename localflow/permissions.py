"""Autorisations macOS, guidées à l'écran.

Fenêtre sombre « Autorisations » : une ligne par autorisation (Accessibilité, Micro, Son
système), témoin vert/rouge mis à jour chaque seconde, un bouton « Autoriser » qui déclenche
la demande OFFICIELLE de macOS (fenêtre système + bon volet des Réglages déjà ouvert sur
LocalFlow). Se ferme toute seule quand tout est vert.

Il n'existe aucun moyen légitime d'accorder Accessibilité sans un geste de l'utilisateur :
on réduit ce geste à « clique Autoriser, puis active l'interrupteur ».
"""

import subprocess
import time

import objc
from AppKit import (
    NSApp,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSTextField,
    NSTimer,
    NSView,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSAppearance,
)
from Foundation import NSObject

from AppKit import NSForegroundColorAttributeName

from .overlay import _attrs, _draw_text, _white

BG = 0.045
W, H = 620.0, 0.0   # H calculée selon le nombre de lignes
ROW_H = 92.0
GREEN = (0.35, 0.95, 0.55)
REDC = (1.0, 0.35, 0.35)
AMBER = (1.0, 0.72, 0.30)

PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "sysaudio": "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture",
}

# ---- état ----

def accessibility_ok():
    try:
        import ApplicationServices as AS
        return bool(AS.AXIsProcessTrusted())
    except Exception:
        return False

def microphone_status():
    """'ok' | 'denied' | 'ask'."""
    try:
        import AVFoundation as AV
        st = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
        return {3: "ok", 2: "denied", 1: "denied"}.get(st, "ask")
    except Exception:
        return "ask"

def request_accessibility():
    """Fenêtre système « LocalFlow souhaite contrôler cet ordinateur » + volet Réglages."""
    try:
        import ApplicationServices as AS
        AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass
    open_pane("accessibility")

def request_microphone(done=None):
    try:
        import AVFoundation as AV
        if microphone_status() == "denied":
            open_pane("microphone")
            return
        AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, done or (lambda g: None))
    except Exception:
        open_pane("microphone")

def open_pane(key):
    subprocess.Popen(["open", PANES[key]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


ROWS = [
    {"key": "accessibility", "title": "Accessibilité",
     "why": "Pour entendre la touche fn et coller le texte là où tu écris.",
     "how": "Clique Autoriser → dans la fenêtre macOS, « Ouvrir les Réglages » → active LocalFlow.",
     "required": True},
    {"key": "microphone", "title": "Micro",
     "why": "Pour t'écouter. Tout est transcrit sur ton Mac, rien ne sort.",
     "how": "Clique Autoriser → « OK » dans la fenêtre macOS.",
     "required": True},
]


class _PermRow(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_PermRow, self).initWithFrame_(frame)
        if self is None:
            return None
        self.spec = {}
        self.ok = False
        self.pending = False
        return self

    def drawRect_(self, r):
        b = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 16, 16)
        NSColor.colorWithCalibratedWhite_alpha_(0.06, 1.0).setFill(); path.fill()
        col = GREEN if self.ok else (AMBER if self.pending else REDC)
        c = lambda a: NSColor.colorWithCalibratedRed_green_blue_alpha_(col[0], col[1], col[2], a)
        (c(0.6) if self.ok else _white(0.08)).setStroke(); path.setLineWidth_(1.0); path.stroke()
        # témoin
        c(0.25).setFill(); NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(18, b.size.height / 2 - 11, 22, 22)).fill()
        c(0.95).setFill(); NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(23, b.size.height / 2 - 6, 12, 12)).fill()
        _draw_text(self.spec.get("title", ""), NSMakeRect(56, b.size.height - 34, 300, 20), _attrs(15, 0.96, weight=0.6))
        state = "Accordée ✓" if self.ok else ("En attente…" if self.pending else "À accorder")
        sa = dict(_attrs(11, 0.9, weight=0.5)); sa[NSForegroundColorAttributeName] = c(0.95)
        _draw_text(state, NSMakeRect(56 + 130, b.size.height - 32, 160, 16), sa)
        _draw_text(self.spec.get("why", ""), NSMakeRect(56, b.size.height - 54, b.size.width - 200, 16), _attrs(12, 0.75))
        _draw_text(self.spec.get("how", ""), NSMakeRect(56, b.size.height - 74, b.size.width - 200, 16), _attrs(11, 0.45))


class _PermBackdrop(NSView):
    def drawRect_(self, r):
        NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0).setFill()
        NSBezierPath.fillRect_(self.bounds())


class PermissionsWindow(NSObject):
    """show(on_done) : s'affiche si une autorisation manque, se ferme quand tout est vert."""

    def initWithIcon_(self, icon_path):
        self = objc.super(PermissionsWindow, self).init()
        self.icon_path = icon_path
        self.window = None
        self.rows = []
        self.buttons = []
        self.timer = None
        self.on_done = None
        self._all_ok_since = None
        return self

    @objc.python_method
    def missing(self):
        out = []
        if not accessibility_ok():
            out.append("accessibility")
        if microphone_status() != "ok":
            out.append("microphone")
        return out

    def _build(self):
        n = len(ROWS)
        h = 150 + n * (ROW_H + 12) + 40
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskFullSizeContentView
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, W, h), style, NSBackingStoreBuffered, False)
        win.setTitle_("Autorisations")
        win.setTitleVisibility_(NSWindowTitleHidden)
        win.setTitlebarAppearsTransparent_(True)
        win.setMovableByWindowBackground_(True)
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        win.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0))
        win.setReleasedWhenClosed_(False)
        win.setLevel_(8)   # au-dessus des fenêtres normales : on ne la perd pas derrière les Réglages
        win.setDelegate_(self)
        win.center()
        content = _PermBackdrop.alloc().initWithFrame_(NSMakeRect(0, 0, W, h))
        win.setContentView_(content)
        # en-tête : icône + titre + sous-titre
        try:
            img = NSImage.alloc().initWithContentsOfFile_(self.icon_path)
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(28, h - 96, 56, 56))
            iv.setImage_(img)
            content.addSubview_(iv)
        except Exception:
            pass
        t = NSTextField.labelWithString_("Deux autorisations, une seule fois")
        t.setFrame_(NSMakeRect(98, h - 70, W - 120, 28)); t.setFont_(NSFont.systemFontOfSize_weight_(20, 0.6)); t.setTextColor_(_white(0.96))
        content.addSubview_(t)
        st = NSTextField.labelWithString_("macOS exige un clic de ta part pour chacune. Cette fenêtre se ferme toute seule quand c'est fait.")
        st.setFrame_(NSMakeRect(98, h - 94, W - 120, 18)); st.setFont_(NSFont.systemFontOfSize_(12)); st.setTextColor_(_white(0.5))
        content.addSubview_(st)
        y = h - 130
        self.rows, self.buttons = [], []
        for i, spec in enumerate(ROWS):
            y -= ROW_H + 12
            row = _PermRow.alloc().initWithFrame_(NSMakeRect(24, y, W - 48, ROW_H))
            row.spec = spec
            content.addSubview_(row)
            b = NSButton.alloc().initWithFrame_(NSMakeRect(W - 48 - 120, y + ROW_H / 2 - 16, 110, 32))
            b.setTitle_("Autoriser"); b.setBezelStyle_(NSBezelStyleRounded)
            b.setTarget_(self); b.setAction_("authorize:"); b.setTag_(i)
            b.setKeyEquivalent_("\r" if i == 0 else "")
            content.addSubview_(b)
            self.rows.append(row); self.buttons.append(b)
        self.footer = NSTextField.labelWithString_("")
        self.footer.setFrame_(NSMakeRect(28, 18, W - 56, 18)); self.footer.setFont_(NSFont.systemFontOfSize_(11.5)); self.footer.setTextColor_(_white(0.45))
        content.addSubview_(self.footer)
        self.window = win

    @objc.python_method
    def show(self, on_done=None):
        if self.window is None:
            self._build()
        self.on_done = on_done
        self._all_ok_since = None
        self.refresh()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, True, lambda t: self.refresh())

    @objc.python_method
    def refresh(self):
        if self.window is None:
            return
        states = {"accessibility": accessibility_ok(), "microphone": microphone_status() == "ok"}
        all_ok = True
        first_missing = None
        for i, (row, b) in enumerate(zip(self.rows, self.buttons)):
            ok = states[row.spec["key"]]
            row.ok = ok
            b.setEnabled_(not ok)
            b.setTitle_("Accordée" if ok else "Autoriser")
            row.setNeedsDisplay_(True)
            if not ok:
                all_ok = False
                if first_missing is None:
                    first_missing = i
        for i, b in enumerate(self.buttons):
            b.setKeyEquivalent_("\r" if i == first_missing else "")
        if all_ok:
            self.footer.setStringValue_("Tout est prêt. Maintiens fn et parle.")
            self._all_ok_since = self._all_ok_since or time.time()
            if time.time() - self._all_ok_since > 1.4:
                self.close()
                if self.on_done:
                    cb, self.on_done = self.on_done, None
                    cb()
        else:
            self.footer.setStringValue_("Astuce : si LocalFlow est déjà dans la liste mais ne marche pas, désactive puis réactive son interrupteur.")

    def authorize_(self, sender):
        key = ROWS[sender.tag()]["key"]
        self.rows[sender.tag()].pending = True
        self.rows[sender.tag()].setNeedsDisplay_(True)
        if key == "accessibility":
            request_accessibility()
        elif key == "microphone":
            request_microphone()

    @objc.python_method
    def close(self):
        if self.timer is not None:
            self.timer.invalidate(); self.timer = None
        if self.window is not None:
            self.window.orderOut_(None)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def windowWillClose_(self, notification):
        if self.timer is not None:
            self.timer.invalidate(); self.timer = None
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
