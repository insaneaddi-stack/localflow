"""Fenêtre Historique — même langage que le panneau : noir profond, cartes à aura,
survol, clic = copier, recherche, stats. Dessin custom (pas de NSTableView)."""

import datetime
import time

import objc
from AppKit import (
    NSApp,
    NSAppearance,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSColorSpace,
    NSCursor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSMakeRect,
    NSPointInRect,
    NSScrollView,
    NSSearchField,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSTrackingMouseMoved,
    NSView,
    NSViewHeightSizable,
    NSViewMaxYMargin,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
)
from Foundation import NSObject

from .overlay import _BandView, _attrs, _draw_text, _text_width, _white
from .paste import copy_text

W, H = 760.0, 580.0
M = 28.0
ROW_H = 66.0
BG = 0.045

def _fmt_time(iso):
    try:
        d = datetime.datetime.fromisoformat(iso)
    except Exception:
        return ""
    today = datetime.date.today()
    if d.date() == today:
        return d.strftime("%H:%M")
    if d.date() == today - datetime.timedelta(days=1):
        return "Hier " + d.strftime("%H:%M")
    return d.strftime("%d/%m %H:%M")

def _fmt_min(m):
    return f"{m:.0f} min" if m < 60 else f"{m/60:.1f} h"

class _ListView(NSView):
    """Liste de cartes, dessinée à la main, repère inversé (haut → bas)."""

    def initWithFrame_(self, frame):
        self = objc.super(_ListView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.rows = []
        self.hover_pt = None
        self.flash_index = -1
        self.flash_t0 = 0.0
        self.on_copy = None
        self._tracking = None
        return self

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def updateTrackingAreas(self):
        if self._tracking is not None:
            self.removeTrackingArea_(self._tracking)
        self._tracking = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), NSTrackingMouseEnteredAndExited | NSTrackingMouseMoved | NSTrackingActiveAlways | NSTrackingInVisibleRect, self, None)
        self.addTrackingArea_(self._tracking)

    def mouseMoved_(self, event):
        self.hover_pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        NSCursor.pointingHandCursor().set()
        self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.hover_pt = None
        NSCursor.arrowCursor().set()
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        i = int(pt.y // ROW_H)
        if 0 <= i < len(self.rows) and self.on_copy:
            self.flash_index, self.flash_t0 = i, time.time()
            self.on_copy(self.rows[i])
            self.setNeedsDisplay_(True)
            self.performSelector_withObject_afterDelay_("refresh:", None, 1.25)

    def refresh_(self, _):
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_rows(self, rows):
        self.rows = rows
        h = max(ROW_H * len(rows), 10.0)
        f = self.frame()
        self.setFrame_(NSMakeRect(0, 0, f.size.width, h))
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        w = self.bounds().size.width
        for i, e in enumerate(self.rows):
            y = i * ROW_H
            if y + ROW_H < dirty.origin.y or y > dirty.origin.y + dirty.size.height:
                continue
            rect = NSMakeRect(M, y + 4, w - 2 * M, ROW_H - 8)
            hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
            copied = self.flash_index == i and time.time() - self.flash_t0 < 1.2
            r, g, b = _BandView.AURAS["default"] if not hasattr(_BandView, "AURAS") else _BandView._aura_color(_BandView, e.get("app", ""))
            c = lambda a: NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 14, 14)
            NSColor.colorWithCalibratedWhite_alpha_(0.075 if hovered else 0.06, 1.0).setFill()
            path.fill()
            # aura : à gauche, discrète (plus forte au survol)
            from AppKit import NSGraphicsContext
            ctx = NSGraphicsContext.currentContext(); ctx.saveGraphicsState(); path.addClip()
            st = 0.55 if hovered else 0.32
            grad = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                [c(0.9 * st), c(0.35 * st), c(0.08 * st), c(0.0)], [0.0, 0.3, 0.65, 1.0], NSColorSpace.sRGBColorSpace())
            grad.drawInRect_relativeCenterPosition_(NSMakeRect(rect.origin.x - 120, rect.origin.y - 60, 300, rect.size.height + 120), (0.0, 0.0))
            ctx.restoreGraphicsState()
            _white(0.14 if hovered else 0.07).setStroke(); path.setLineWidth_(1.0); path.stroke()
            if copied:
                c(0.95).setStroke(); path.stroke()
            # témoin couleur
            c(0.95).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(rect.origin.x + 18, rect.origin.y + rect.size.height / 2 - 4, 8, 8)).fill()
            # textes
            label = (e.get("app") or "Dictée").upper()
            _draw_text(label, NSMakeRect(rect.origin.x + 40, rect.origin.y + 10, 260, 14), _attrs(10.5, 0.5, weight=0.5))
            right = "Copié ✓" if copied else _fmt_time(e.get("t", ""))
            ra = _attrs(10.5, 0.95 if copied else 0.4, weight=0.5)
            if copied:
                ra = dict(ra); ra[NSForegroundColorAttributeName] = c(0.95)
            rw = _text_width(right, ra)
            _draw_text(right, NSMakeRect(rect.origin.x + rect.size.width - 18 - rw, rect.origin.y + 10, rw + 2, 14), ra)
            _draw_text(e.get("text", "").replace("\n", " ⏎ "),
                       NSMakeRect(rect.origin.x + 40, rect.origin.y + 28, rect.size.width - 60, 18), _attrs(13, 0.93))

class _Backdrop(NSView):
    def drawRect_(self, r):
        NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0).setFill()
        NSBezierPath.fillRect_(self.bounds())

class HistoryWindow(NSObject):
    """Créée à la demande. show() depuis le thread principal."""

    def initWithConfig_notify_(self, config, notify):
        self = objc.super(HistoryWindow, self).init()
        self.config = config
        self.notify = notify
        self.window = None
        return self

    def _build(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        win.setTitle_("Historique")
        win.setTitleVisibility_(NSWindowTitleHidden)
        win.setTitlebarAppearsTransparent_(True)
        win.setMovableByWindowBackground_(True)
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        win.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0))
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        win.setMinSize_((560, 360))
        win.center()
        content = _Backdrop.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        win.setContentView_(content)

        self.header = _Header.alloc().initWithFrame_(NSMakeRect(0, H - 120, W, 120))
        self.header.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        content.addSubview_(self.header)

        self.search = NSSearchField.alloc().initWithFrame_(NSMakeRect(M, H - 112, 300, 30))
        self.search.setPlaceholderString_("Rechercher…")
        self.search.setTarget_(self); self.search.setAction_("searchChanged:")
        self.search.setAutoresizingMask_(NSViewMinYMargin)
        self.search.setFont_(NSFont.systemFontOfSize_(13))
        self.search.setFocusRingType_(1)  # none
        content.addSubview_(self.search)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 16, W, H - 136))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setScrollerStyle_(1)  # overlay
        self.list = _ListView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 10))
        self.list.setAutoresizingMask_(NSViewWidthSizable)
        self.list.on_copy = self._copy_entry
        scroll.setDocumentView_(self.list)
        content.addSubview_(scroll)
        self.window = win

    @objc.python_method
    def _copy_entry(self, entry):
        copy_text(entry.get("text", ""))

    def refresh(self):
        if self.window is None:
            return
        q = (self.search.stringValue() or "").strip().lower()
        rows = self.config.history
        if q:
            rows = [e for e in rows if q in e.get("text", "").lower() or q in (e.get("app") or "").lower()]
        self.list.set_rows(rows)
        s = self.config.stats_summary()
        t, a = s["today"], s["all"]
        self.header.title = "Historique"
        self.header.subtitle = (f"Aujourd'hui · {t['words']} mots · {t['dictations']} dictées · ≈ {_fmt_min(t['saved_min'])} gagnées"
                                f"      ·      Total · {a['words']} mots · ≈ {_fmt_min(a['saved_min'])}")
        self.header.count = f"{len(rows)} dictée{'s' if len(rows) != 1 else ''}"
        self.header.setNeedsDisplay_(True)

    def show(self):
        if self.window is None:
            self._build()
        self.refresh()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def windowWillClose_(self, notification):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def searchChanged_(self, sender):
        self.refresh()

class _Header(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_Header, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title = "Historique"
        self.subtitle = ""
        self.count = ""
        return self

    def drawRect_(self, r):
        b = self.bounds()
        _draw_text(self.title, NSMakeRect(M, b.size.height - 62, 300, 28), _attrs(22, 0.96, weight=0.6))
        _draw_text(self.subtitle, NSMakeRect(M, b.size.height - 80, b.size.width - 2 * M, 16), _attrs(11.5, 0.42))
        ca = _attrs(11.5, 0.42)
        cw = _text_width(self.count, ca)
        _draw_text(self.count, NSMakeRect(b.size.width - M - cw, 12, cw + 2, 16), ca)
