"""Fenêtres du module Réunions — même langage que l'Historique (noir profond, cartes à aura).

- LiveMeetingWindow : réunion en cours. Gauche : transcript live (Moi / Eux). Droite : bloc-notes
  (sauvegardé en continu). Bas : état + « Terminer & résumer ».
- MeetingsWindow : liste des réunions (cartes), détail à droite (résumé, actions, question à l'IA,
  boutons Copier / Ouvrir / Révéler / Supprimer).
"""

import datetime
import os
import subprocess
import time

import objc
from AppKit import (
    NSApp,
    NSAppearance,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPointInRect,
    NSScrollView,
    NSSearchField,
    NSTextField,
    NSTextView,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSTrackingMouseMoved,
    NSView,
    NSViewHeightSizable,
    NSViewMaxXMargin,
    NSViewMinXMargin,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSCursor,
)
from Foundation import NSObject, NSAttributedString, NSMutableAttributedString

from .overlay import _attrs, _draw_text, _text_width, _white
from .paste import copy_text

BG = 0.045
M = 24.0
ROW_H = 78.0
ME_COLOR = (0.66, 0.40, 1.00)
THEM_COLOR = (0.35, 0.85, 0.95)
RED = (1.0, 0.30, 0.32)

def _rgb(c, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(c[0], c[1], c[2], a)

def _fmt_dur(s):
    s = int(s or 0)
    return f"{s // 3600} h {(s % 3600) // 60:02d}" if s >= 3600 else f"{s // 60} min"

def _fmt_date(iso):
    try:
        d = datetime.datetime.fromisoformat(iso)
    except Exception:
        return ""
    today = datetime.date.today()
    if d.date() == today:
        return "Aujourd'hui " + d.strftime("%H:%M")
    if d.date() == today - datetime.timedelta(days=1):
        return "Hier " + d.strftime("%H:%M")
    return d.strftime("%d/%m/%Y %H:%M")

def _textview(frame, editable, font_size=13.0):
    scroll = NSScrollView.alloc().initWithFrame_(frame)
    scroll.setHasVerticalScroller_(True)
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(0)
    scroll.setScrollerStyle_(1)
    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, frame.size.width, frame.size.height))
    tv.setEditable_(editable)
    tv.setSelectable_(True)
    tv.setRichText_(False)
    tv.setDrawsBackground_(False)
    tv.setFont_(NSFont.systemFontOfSize_(font_size))
    tv.setTextColor_(_white(0.92))
    tv.setInsertionPointColor_(_white(0.9))
    tv.setTextContainerInset_((10, 10))
    tv.setAutoresizingMask_(NSViewWidthSizable)
    tv.setVerticallyResizable_(True)
    tv.setHorizontallyResizable_(False)
    tv.textContainer().setWidthTracksTextView_(True)
    tv.setAutomaticQuoteSubstitutionEnabled_(False)
    tv.setAutomaticDashSubstitutionEnabled_(False)
    scroll.setDocumentView_(tv)
    return scroll, tv

def _button(title, target, action, frame):
    b = NSButton.alloc().initWithFrame_(frame)
    b.setTitle_(title)
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setTarget_(target)
    b.setAction_(action)
    b.setFont_(NSFont.systemFontOfSize_(12.5))
    return b

class _MeetCard(NSView):
    """Fond arrondi sombre avec liseré (conteneur d'une zone)."""

    def initWithFrame_(self, frame):
        self = objc.super(_MeetCard, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title = ""
        self.color = ME_COLOR
        return self

    def drawRect_(self, r):
        b = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 16, 16)
        NSColor.colorWithCalibratedWhite_alpha_(0.06, 1.0).setFill(); path.fill()
        _white(0.08).setStroke(); path.setLineWidth_(1.0); path.stroke()
        if self.title:
            _rgb(self.color, 0.95).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(16, b.size.height - 24, 7, 7)).fill()
            _draw_text(self.title.upper(), NSMakeRect(30, b.size.height - 28, b.size.width - 40, 14), _attrs(10.5, 0.5, weight=0.5))

class _MeetBackdrop(NSView):
    def drawRect_(self, r):
        NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0).setFill()
        NSBezierPath.fillRect_(self.bounds())

class _MeetHeader(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_MeetHeader, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title = ""
        self.subtitle = ""
        self.dot = None   # couleur d'un témoin (réunion en cours)
        self.phase0 = time.time()
        return self

    def drawRect_(self, r):
        b = self.bounds()
        x = M
        if self.dot is not None:
            import math
            pulse = 0.5 + 0.5 * math.sin((time.time() - self.phase0) * 2.2)
            _rgb(self.dot, 0.25 + 0.2 * pulse).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 2, b.size.height - 56, 16, 16)).fill()
            _rgb(self.dot, 0.95).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x + 2, b.size.height - 52, 8, 8)).fill()
            x += 22
        _draw_text(self.title, NSMakeRect(x, b.size.height - 62, b.size.width - x - M, 28), _attrs(22, 0.96, weight=0.6))
        _draw_text(self.subtitle, NSMakeRect(M, b.size.height - 80, b.size.width - 2 * M, 16), _attrs(11.5, 0.42))


def _window(title, w, h, delegate):
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
             | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
    win.setTitle_(title)
    win.setTitleVisibility_(NSWindowTitleHidden)
    win.setTitlebarAppearsTransparent_(True)
    win.setMovableByWindowBackground_(True)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    win.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(BG, 1.0))
    win.setReleasedWhenClosed_(False)
    win.setDelegate_(delegate)
    win.setMinSize_((720, 460))
    win.center()
    content = _MeetBackdrop.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    win.setContentView_(content)
    return win, content


# =====================================================================================
class LiveMeetingWindow(NSObject):
    """Réunion en cours. show()/refresh()/close() depuis le thread principal."""

    W, H = 1000.0, 680.0

    def initWithCallbacks_(self, cb):
        self = objc.super(LiveMeetingWindow, self).init()
        self.cb = cb          # {"stop": fn(), "notes": fn(text), "cancel": fn()}
        self.window = None
        self._timer = None
        self._last_n = -1
        return self

    def _build(self):
        W, H = self.W, self.H
        win, content = _window("Réunion en cours", W, H, self)
        self.header = _MeetHeader.alloc().initWithFrame_(NSMakeRect(0, H - 100, W, 100))
        self.header.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self.header.dot = RED
        content.addSubview_(self.header)

        top, bottom = H - 104, 70
        left_w = (W - 3 * M) * 0.56
        # transcript
        self.card_t = _MeetCard.alloc().initWithFrame_(NSMakeRect(M, bottom, left_w, top - bottom))
        self.card_t.title = "Transcript"
        self.card_t.color = THEM_COLOR
        self.card_t.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self.card_t)
        sc, self.transcript = _textview(NSMakeRect(6, 6, left_w - 12, top - bottom - 40), False, 13.0)
        sc.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.card_t.addSubview_(sc)
        # notes
        right_x = 2 * M + left_w
        right_w = W - right_x - M
        self.card_n = _MeetCard.alloc().initWithFrame_(NSMakeRect(right_x, bottom, right_w, top - bottom))
        self.card_n.title = "Mes notes  ·  tape librement, l'IA les enrichira à la fin"
        self.card_n.color = ME_COLOR
        self.card_n.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable | NSViewMinXMargin)
        content.addSubview_(self.card_n)
        sc2, self.notes = _textview(NSMakeRect(6, 6, right_w - 12, top - bottom - 40), True, 13.5)
        sc2.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.notes.setDelegate_(self)
        self.card_n.addSubview_(sc2)
        # bas
        self.status = NSTextField.labelWithString_("")
        self.status.setFrame_(NSMakeRect(M, 24, W - 420, 20))
        self.status.setTextColor_(_white(0.5))
        self.status.setFont_(NSFont.systemFontOfSize_(11.5))
        self.status.setAutoresizingMask_(NSViewWidthSizable | NSViewMaxXMargin)
        content.addSubview_(self.status)
        self.btn_stop = _button("Terminer & résumer", self, "stopClicked:", NSMakeRect(W - M - 180, 18, 180, 32))
        self.btn_stop.setAutoresizingMask_(NSViewMinXMargin)
        self.btn_stop.setKeyEquivalent_("\r")
        content.addSubview_(self.btn_stop)
        self.btn_cancel = _button("Annuler", self, "cancelClicked:", NSMakeRect(W - M - 180 - 100, 18, 92, 32))
        self.btn_cancel.setAutoresizingMask_(NSViewMinXMargin)
        content.addSubview_(self.btn_cancel)
        self.window = win

    # ---- API ----

    @objc.python_method
    def show(self, meeting):
        if self.window is None:
            self._build()
        self.meeting = meeting
        self._last_n = -1
        self.notes.setString_(meeting.notes or "")
        self.refresh()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.notes)
        if self._timer is None:
            from AppKit import NSTimer
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, True, lambda t: self.refresh())

    @objc.python_method
    def refresh(self, status=""):
        if self.window is None or not self.window.isVisible():
            return
        m = getattr(self, "meeting", None)
        if m is None:
            return
        from .meeting import _fmt_ts
        self.header.title = m.title or "Réunion en cours"
        self.header.subtitle = f"{_fmt_ts(m.duration_s)}  ·  {len(m.segments)} tours  ·  {m.word_count()} mots" + (f"  ·  {m.app}" if m.app else "")
        self.header.setNeedsDisplay_(True)
        if status:
            self.status.setStringValue_(status)
        n = len(m.segments)
        if n != self._last_n:
            self._last_n = n
            self._render_transcript(m)

    @objc.python_method
    def _render_transcript(self, m):
        out = NSMutableAttributedString.alloc().init()
        from .meeting import _fmt_ts
        segs = sorted(m.segments, key=lambda s: s["t0"])
        if not segs:
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                "La transcription apparaît ici quelques secondes après chaque prise de parole.\n\nMoi = ton micro · Eux = le son de l'appel.",
                {NSFontAttributeName: NSFont.systemFontOfSize_(13), NSForegroundColorAttributeName: _white(0.4)}))
        for s in segs:
            who = "Moi" if s["who"] == "me" else "Eux"
            col = _rgb(ME_COLOR if s["who"] == "me" else THEM_COLOR, 0.95)
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                f"{_fmt_ts(s['t0'])}  ", {NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.3), NSForegroundColorAttributeName: _white(0.35)}))
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                who + "\n", {NSFontAttributeName: NSFont.systemFontOfSize_weight_(12, 0.6), NSForegroundColorAttributeName: col}))
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                s["text"] + "\n\n", {NSFontAttributeName: NSFont.systemFontOfSize_(13.5), NSForegroundColorAttributeName: _white(0.92)}))
        self.transcript.textStorage().setAttributedString_(out)
        self.transcript.scrollRangeToVisible_((out.length(), 0))

    @objc.python_method
    def set_busy(self, busy, text=""):
        if self.window is None:
            return
        self.btn_stop.setEnabled_(not busy)
        self.btn_cancel.setEnabled_(not busy)
        self.notes.setEditable_(not busy)
        if text:
            self.status.setStringValue_(text)

    @objc.python_method
    def close(self):
        if self._timer is not None:
            self._timer.invalidate(); self._timer = None
        if self.window is not None:
            self.window.orderOut_(None)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # ---- événements ----

    def textDidChange_(self, notification):
        try:
            self.cb["notes"](self.notes.string())
        except Exception:
            pass

    def stopClicked_(self, sender):
        self.cb["stop"]()

    def cancelClicked_(self, sender):
        self.cb["cancel"]()

    def windowWillClose_(self, notification):
        if self._timer is not None:
            self._timer.invalidate(); self._timer = None
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


# =====================================================================================
class _MeetingList(NSView):
    """Cartes : titre, date · durée, première ligne du résumé."""

    def initWithFrame_(self, frame):
        self = objc.super(_MeetingList, self).initWithFrame_(frame)
        if self is None:
            return None
        self.rows = []
        self.selected = -1
        self.hover_pt = None
        self.on_select = None
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
        if 0 <= i < len(self.rows):
            self.selected = i
            self.setNeedsDisplay_(True)
            if self.on_select:
                self.on_select(self.rows[i])

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
            rect = NSMakeRect(8, y + 4, w - 16, ROW_H - 8)
            hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
            sel = i == self.selected
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 14, 14)
            NSColor.colorWithCalibratedWhite_alpha_(0.09 if sel else (0.075 if hovered else 0.055), 1.0).setFill(); path.fill()
            (_rgb(ME_COLOR, 0.8) if sel else _white(0.12 if hovered else 0.07)).setStroke(); path.setLineWidth_(1.0); path.stroke()
            _rgb(ME_COLOR, 0.95 if sel else 0.6).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(rect.origin.x + 16, rect.origin.y + 16, 7, 7)).fill()
            _draw_text(e.get("title") or "Réunion", NSMakeRect(rect.origin.x + 32, rect.origin.y + 10, rect.size.width - 44, 18), _attrs(13.5, 0.95, weight=0.55))
            meta = f"{_fmt_date(e.get('t', ''))}  ·  {_fmt_dur(e.get('duration_s'))}" + (f"  ·  {e.get('app')}" if e.get("app") else "")
            _draw_text(meta, NSMakeRect(rect.origin.x + 32, rect.origin.y + 30, rect.size.width - 44, 14), _attrs(10.5, 0.45))
            _draw_text(e.get("summary") or "", NSMakeRect(rect.origin.x + 32, rect.origin.y + 47, rect.size.width - 44, 16), _attrs(11.5, 0.7))


class MeetingsWindow(NSObject):
    """Liste + détail. cb : {"ask": fn(entry, question) -> str (thread), "delete": fn(entry), "notify": fn(t, m)}"""

    W, H = 1060.0, 700.0

    def initWithIndex_callbacks_(self, index, cb):
        self = objc.super(MeetingsWindow, self).init()
        self.index = index
        self.cb = cb
        self.window = None
        self.current = None
        return self

    def _build(self):
        W, H = self.W, self.H
        win, content = _window("Réunions", W, H, self)
        self.header = _MeetHeader.alloc().initWithFrame_(NSMakeRect(0, H - 100, W, 100))
        self.header.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self.header.title = "Réunions"
        content.addSubview_(self.header)
        top = H - 104
        list_w = 340.0
        self.search = NSSearchField.alloc().initWithFrame_(NSMakeRect(M, top - 30, list_w, 28))
        self.search.setPlaceholderString_("Rechercher…")
        self.search.setTarget_(self); self.search.setAction_("searchChanged:")
        self.search.setAutoresizingMask_(NSViewMinYMargin)
        self.search.setFocusRingType_(1)
        content.addSubview_(self.search)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(M - 8, 20, list_w + 16, top - 60))
        scroll.setHasVerticalScroller_(True); scroll.setDrawsBackground_(False); scroll.setScrollerStyle_(1)
        scroll.setAutoresizingMask_(NSViewHeightSizable)
        self.list = _MeetingList.alloc().initWithFrame_(NSMakeRect(0, 0, list_w + 16, 10))
        self.list.setAutoresizingMask_(NSViewWidthSizable)
        self.list.on_select = self._select
        scroll.setDocumentView_(self.list)
        content.addSubview_(scroll)
        # détail
        dx = M + list_w + 24
        dw = W - dx - M
        self.card = _MeetCard.alloc().initWithFrame_(NSMakeRect(dx, 20, dw, top - 20))
        self.card.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self.card)
        self.d_title = NSTextField.labelWithString_("")
        self.d_title.setFrame_(NSMakeRect(18, top - 20 - 44, dw - 36, 26))
        self.d_title.setFont_(NSFont.systemFontOfSize_weight_(18, 0.6)); self.d_title.setTextColor_(_white(0.96))
        self.d_title.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self.card.addSubview_(self.d_title)
        self.d_meta = NSTextField.labelWithString_("")
        self.d_meta.setFrame_(NSMakeRect(18, top - 20 - 64, dw - 36, 16))
        self.d_meta.setFont_(NSFont.systemFontOfSize_(11.5)); self.d_meta.setTextColor_(_white(0.45))
        self.d_meta.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self.card.addSubview_(self.d_meta)
        sc, self.d_text = _textview(NSMakeRect(6, 110, dw - 12, top - 20 - 64 - 120), False, 13.0)
        sc.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.card.addSubview_(sc)
        # question
        self.q = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 68, dw - 36 - 110, 30))
        self.q.setPlaceholderString_("Pose une question sur cette réunion… (↩)")
        self.q.setFont_(NSFont.systemFontOfSize_(13)); self.q.setBezelStyle_(1)
        self.q.setTarget_(self); self.q.setAction_("askClicked:")
        self.q.setAutoresizingMask_(NSViewWidthSizable)
        self.card.addSubview_(self.q)
        b = _button("Demander", self, "askClicked:", NSMakeRect(dw - 18 - 100, 67, 100, 32))
        b.setAutoresizingMask_(NSViewMinXMargin)
        self.card.addSubview_(b)
        x = 18
        for title, sel, w in (("Copier le résumé", "copyClicked:", 130), ("Ouvrir", "openClicked:", 80),
                              ("Dans le Finder", "revealClicked:", 120), ("Supprimer", "deleteClicked:", 100)):
            bt = _button(title, self, sel, NSMakeRect(x, 20, w, 30))
            self.card.addSubview_(bt)
            x += w + 8
        self.window = win

    # ---- données ----

    def refresh(self):
        if self.window is None:
            return
        q = (self.search.stringValue() or "").strip().lower()
        rows = self.index.existing()
        if q:
            rows = [e for e in rows if q in (e.get("title", "") + " " + e.get("summary", "") + " " + e.get("app", "")).lower()]
        self.list.set_rows(rows)
        n = len(rows)
        total = sum(e.get("duration_s", 0) for e in rows)
        self.header.subtitle = f"{n} réunion{'s' if n != 1 else ''}  ·  {_fmt_dur(total)} enregistrées  ·  dossier : {self.cb['folder']()}"
        self.header.setNeedsDisplay_(True)
        if self.current is None and rows:
            self.list.selected = 0
            self._select(rows[0])
        elif self.current is not None:
            ids = [e.get("id") for e in rows]
            if self.current.get("id") in ids:
                self.list.selected = ids.index(self.current.get("id"))
            else:
                self.current = None
                self.d_title.setStringValue_(""); self.d_meta.setStringValue_(""); self.d_text.setString_("")

    @objc.python_method
    def _select(self, entry):
        self.current = entry
        self.d_title.setStringValue_(entry.get("title") or "Réunion")
        self.d_meta.setStringValue_(f"{_fmt_date(entry.get('t', ''))}  ·  {_fmt_dur(entry.get('duration_s'))}  ·  {entry.get('words', 0)} mots")
        text = ""
        try:
            with open(entry["path"], "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = "(fichier introuvable)"
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                text = parts[2].strip()
        self.d_text.setString_(text)
        self.d_text.scrollRangeToVisible_((0, 0))

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

    # ---- actions ----

    def copyClicked_(self, sender):
        if self.current:
            copy_text(self.d_text.string())
            self.cb["notify"]("Copié", "Le compte rendu est dans le presse-papiers.")

    def openClicked_(self, sender):
        if self.current:
            subprocess.Popen(["open", self.current["path"]])

    def revealClicked_(self, sender):
        if self.current:
            subprocess.Popen(["open", "-R", self.current["path"]])

    def deleteClicked_(self, sender):
        if self.current:
            self.cb["delete"](self.current)
            self.current = None
            self.refresh()

    def askClicked_(self, sender):
        q = (self.q.stringValue() or "").strip()
        if not q or not self.current:
            return
        self.q.setEnabled_(False)
        entry = self.current
        cur = self.d_text.string()
        self.d_text.setString_(cur + f"\n\n---\n\n**Question :** {q}\n\n_Je réfléchis…_")
        self.d_text.scrollRangeToVisible_((len(self.d_text.string()), 0))

        def work():
            try:
                ans = self.cb["ask"](entry, q)
            except Exception as exc:
                ans = f"(erreur : {exc})"

            def done():
                base = self.d_text.string().replace("_Je réfléchis…_", ans.strip() or "(pas de réponse)")
                self.d_text.setString_(base)
                self.d_text.scrollRangeToVisible_((len(base), 0))
                self.q.setStringValue_("")
                self.q.setEnabled_(True)
            from AppKit import NSOperationQueue
            NSOperationQueue.mainQueue().addOperationWithBlock_(done)
        import threading
        threading.Thread(target=work, daemon=True).start()
