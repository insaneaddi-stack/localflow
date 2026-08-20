"""Tutoriel de première installation, directement à l'écran (pas dans le terminal).

Une carte sombre (même langage que le panneau) posée au-dessus de la barre du bas,
avec l'orbe qui présente et une flèche animée qui pointe vers la barre. Les étapes
avancent quand l'utilisateur FAIT la chose (première dictée, mains-libres, panneau),
Esc passe une étape. Tout est transparent à la souris : rien à cliquer.
"""

import math
import time

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSCursor,
    NSEvent,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSBezierPath,
    NSColor,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTimer,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

from .overlay import _BandView, _attrs, _draw_text, _text_width, _violet, _white, MARGINS, IDLE_H

CARD_W, CARD_H = 520.0, 150.0
GAP_ABOVE_BAR = 64.0      # espace entre la carte et la barre (la flèche vit là)
FPS = 60.0

STEPS = [
    {   # 0 — bienvenue
        "title": "Bienvenue dans LocalFlow",
        "lines": ["Cette petite barre, en bas, c'est LocalFlow. Elle reste là,",
                  "discrète, tant que tu ne parles pas."],
        "hint": "", "wait": "key", "arrow": True,
    },
    {   # 1 — première dictée (attend un vrai collage)
        "title": "Ta première dictée",
        "lines": ["Clique dans un champ de texte (Notes, Messages, n'importe où),",
                  "puis maintiens fn, parle, relâche. Le texte est collé."],
        "hint": "J'attends ta première dictée…", "wait": "dictated", "arrow": True,
    },
    {   # 2 — mains-libres
        "title": "Mains-libres",
        "lines": ["Pour une longue dictée : appuie sur fn + espace, parle autant",
                  "que tu veux, puis fn pour terminer."],
        "hint": "Essaie…", "wait": "handsfree", "arrow": True,
    },
    {   # 3 — panneau
        "title": "Le panneau",
        "lines": ["Double-tap sur fn : historique, nettoyage IA, sons,",
                  "et « Copier » la dernière dictée. Touches 1–4, Esc pour fermer."],
        "hint": "Essaie…", "wait": "panel", "arrow": True,
    },
    {   # 4 — fin
        "title": "C'est tout.",
        "lines": ["Dictionnaire, moteur et réglages : icône 🎙 dans la barre des menus.",
                  "Dis « corrige X en Y » pour lui apprendre un mot. Bonne dictée."],
        "hint": "Se ferme tout seul", "wait": "timer:6", "arrow": False,
    },
]

class _OrbState:
    """État minimal pour réutiliser le dessin de l'orbe du panneau."""
    def __init__(self):
        self.phase = 0.0
        self.gaze = (0.0, -0.4)
        self._blink_t0 = None
        self._next_blink = time.time() + 2.0

    def blink_amount(self):
        if self._blink_t0 is None:
            return 0.0
        t = (time.time() - self._blink_t0) / 0.14
        return 0.0 if t >= 1.0 else math.sin(t * math.pi)

    def look_at(self, dx, dy):
        dist = math.hypot(dx, dy) or 1.0
        norm = min(1.0, dist / 260.0)
        tx, ty = dx / dist * norm, dy / dist * norm
        self.gaze = (self.gaze[0] + (tx - self.gaze[0]) * 0.25, self.gaze[1] + (ty - self.gaze[1]) * 0.25)

    def tick(self, now):
        self.phase = now
        if now >= self._next_blink:
            self._blink_t0 = now
            self._next_blink = now + 2.5 + (now * 7.3) % 3.5
        elif self._blink_t0 is not None and now - self._blink_t0 > 0.2:
            self._blink_t0 = None

class _TutorialView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_TutorialView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.tut = None
        self.overlay = None   # pour _BandView._draw_orb (lit self.overlay.gaze / phase / blink_amount)
        return self

    def drawRect_(self, dirty):
        t = self.tut
        if t is None:
            return
        b = self.bounds()
        k = t.alpha
        step = STEPS[t.index]
        # --- carte
        cx = b.size.width / 2.0
        card = NSMakeRect(cx - CARD_W / 2, t.card_y, CARD_W, CARD_H)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(card, 22, 22)
        for dy, grow, a in ((-4, 12, 0.10), (-2, 6, 0.14)):   # ombre douce
            NSColor.colorWithCalibratedWhite_alpha_(0.0, a * k).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(card.origin.x - grow, card.origin.y - grow + dy, CARD_W + 2 * grow, CARD_H + 2 * grow), 22 + grow, 22 + grow).fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.05, 0.97 * k).setFill()
        path.fill()
        _white(0.14 * k).setStroke(); path.setLineWidth_(1.0); path.stroke()
        # aura violette discrète en bas de la carte
        from AppKit import NSGraphicsContext, NSGradient, NSColorSpace
        ctx = NSGraphicsContext.currentContext(); ctx.saveGraphicsState(); path.addClip()
        r_, g_, b_ = _BandView.AURAS["default"]
        c = lambda a: NSColor.colorWithCalibratedRed_green_blue_alpha_(r_, g_, b_, a * k)
        NSGradient.alloc().initWithColors_atLocations_colorSpace_([c(0.28), c(0.08), c(0.0)], [0.0, 0.5, 1.0], NSColorSpace.sRGBColorSpace()) \
            .drawInRect_angle_(NSMakeRect(card.origin.x, card.origin.y, CARD_W, CARD_H * 0.6), 90.0)
        ctx.restoreGraphicsState()
        # --- orbe (à gauche), textes
        orb_d = 40.0
        ox, oy = card.origin.x + 38, card.origin.y + CARD_H - 46
        _BandView._draw_orb(self, ox, oy, orb_d, k)
        tx = card.origin.x + 76
        _draw_text(step["title"], NSMakeRect(tx, card.origin.y + CARD_H - 44, CARD_W - 96, 22), _attrs(15.5, 0.96 * k, weight=0.6))
        y = card.origin.y + CARD_H - 70
        for line in step["lines"]:
            _draw_text(line, NSMakeRect(tx, y, CARD_W - 96, 17), _attrs(12.5, 0.85 * k))
            y -= 19
        # progression (points) + aide
        n = len(STEPS)
        px = card.origin.x + 22
        for i in range(n):
            (_violet(0.95 * k) if i == t.index else _white(0.18 * k)).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(px + i * 12, card.origin.y + 16, 6, 6)).fill()
        ha = _attrs(10.5, 0.45 * k)
        hw = _text_width(step["hint"], ha)
        _draw_text(step["hint"], NSMakeRect(card.origin.x + CARD_W - 20 - BTN_W - 14 - hw, card.origin.y + 17, hw + 2, 14), ha)
        # --- flèche animée vers la barre du bas
        if step["arrow"]:
            bob = 6.0 * math.sin(t.phase * 3.2)
            x0 = cx
            y_top = card.origin.y - 10 - bob
            y_tip = t.target_y + 14 - bob
            if y_top - y_tip > 18:
                for width, a in ((9.0, 0.12), (4.0, 0.30), (1.8, 0.95)):
                    _violet(a * k).setStroke()
                    p = NSBezierPath.bezierPath(); p.setLineWidth_(width); p.setLineCapStyle_(1)
                    p.moveToPoint_((x0, y_top)); p.lineToPoint_((x0, y_tip + 10)); p.stroke()
                    head = NSBezierPath.bezierPath(); head.setLineWidth_(width); head.setLineCapStyle_(1); head.setLineJoinStyle_(1)
                    head.moveToPoint_((x0 - 8, y_tip + 18)); head.lineToPoint_((x0, y_tip + 8)); head.lineToPoint_((x0 + 8, y_tip + 18))
                    head.stroke()

BTN_W, BTN_H = 112.0, 30.0

class _ButtonView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_ButtonView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.tut = None
        self.hover = False
        self.label = "Suivant"
        self._tracking = None
        return self

    def acceptsFirstMouse_(self, event):
        return True

    def updateTrackingAreas(self):
        if self._tracking is not None:
            self.removeTrackingArea_(self._tracking)
        self._tracking = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect, self, None)
        self.addTrackingArea_(self._tracking)

    def mouseEntered_(self, event):
        self.hover = True; NSCursor.pointingHandCursor().set(); self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.hover = False; NSCursor.arrowCursor().set(); self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        if self.tut is not None:
            self.tut.event("key")

    def drawRect_(self, dirty):
        b = self.bounds()
        k = self.tut.alpha if self.tut else 1.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), BTN_H / 2, BTN_H / 2)
        _violet((0.40 if self.hover else 0.28) * k).setFill(); path.fill()
        _violet(0.9 * k).setStroke(); path.setLineWidth_(1.0); path.stroke()
        a = _attrs(12, 0.96 * k, weight=0.5)
        w = _text_width(self.label, a)
        _draw_text(self.label, NSMakeRect(b.size.width / 2 - w / 2, b.size.height / 2 - 8, w + 2, 16), a)

class Tutorial:
    """show() au premier lancement ; event('dictated'|'handsfree'|'panel'|'key') fait avancer."""

    def __init__(self, config, on_done=None):
        self.config = config
        self.on_done = on_done
        self.index = 0
        self.alpha = 0.0
        self.phase = 0.0
        self.active = False
        self._timer = None
        self._step_t0 = 0.0
        self._t0 = time.time()
        self.orb = _OrbState()
        self.panel = None
        self.view = None

    # ---- fenêtre plein écran transparente, traversante ----
    def _build(self):
        screen = NSScreen.mainScreen()
        vf = screen.visibleFrame() if screen else NSMakeRect(0, 0, 1440, 900)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            vf, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSStatusWindowLevel + 1)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHasShadow_(False)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)
        self.view = _TutorialView.alloc().initWithFrame_(NSMakeRect(0, 0, vf.size.width, vf.size.height))
        self.view.tut = self
        self.view.overlay = self.orb
        self.panel.setContentView_(self.view)
        # géométrie : la barre réduite est à MARGINS['idle'] du bas de l'écran visible
        self.target_y = MARGINS["idle"] + IDLE_H          # haut de la barre, en coordonnées fenêtre (origine = bas du visibleFrame)
        self.card_y = self.target_y + GAP_ABOVE_BAR
        self._vf = vf
        # bouton cliquable : sa propre petite fenêtre (le reste de l'écran reste traversant)
        bx = vf.origin.x + vf.size.width / 2.0 + CARD_W / 2 - 20 - BTN_W
        by = vf.origin.y + self.card_y + 10
        self.btn_panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(bx, by, BTN_W, BTN_H), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.btn_panel.setOpaque_(False)
        self.btn_panel.setBackgroundColor_(NSColor.clearColor())
        self.btn_panel.setLevel_(NSStatusWindowLevel + 2)
        self.btn_panel.setHasShadow_(False)
        self.btn_panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)
        self.btn = _ButtonView.alloc().initWithFrame_(NSMakeRect(0, 0, BTN_W, BTN_H))
        self.btn.tut = self
        self.btn_panel.setContentView_(self.btn)

    def show(self):
        if self.panel is None:
            self._build()
        self.active = True
        self.index = 0
        self.alpha = 0.0
        self._step_t0 = time.time()
        self.panel.orderFrontRegardless()
        self.btn_panel.orderFrontRegardless()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / FPS, True, self._tick)

    def hide(self):
        self.active = False
        if self._timer is not None:
            self._timer.invalidate(); self._timer = None
        if self.panel is not None:
            self.panel.orderOut_(None)
            self.btn_panel.orderOut_(None)

    def finish(self):
        self.config.data["onboarded"] = True
        self.config.save()
        self.hide()
        if self.on_done:
            self.on_done()

    def _advance(self):
        if self.index + 1 >= len(STEPS):
            self.finish(); return
        self.index += 1
        self.alpha = 0.0
        self._step_t0 = time.time()

    def event(self, name):
        """'key' (Entrée/Esc), 'dictated', 'handsfree', 'panel'."""
        if not self.active:
            return False
        step = STEPS[self.index]
        if name == "key" or step["wait"] == name:
            self._advance()
            return True
        return False

    def _tick(self, timer):
        now = time.time()
        self.phase = now - self._t0
        self.orb.tick(self.phase)
        self.alpha = min(1.0, self.alpha + 1.0 / (FPS * 0.28))
        # les yeux suivent la souris (position écran → position de l'orbe)
        try:
            loc = NSEvent.mouseLocation()
            ox = self._vf.origin.x + self._vf.size.width / 2.0 - CARD_W / 2 + 38
            oy = self._vf.origin.y + self.card_y + CARD_H - 46
            self.orb.look_at(loc.x - ox, loc.y - oy)
        except Exception:
            pass
        step = STEPS[self.index]
        label = "Terminer" if self.index == len(STEPS) - 1 else ("Passer" if step["wait"] in ("dictated",) else "Suivant")
        if self.btn.label != label:
            self.btn.label = label
        self.btn.setNeedsDisplay_(True)
        wait = STEPS[self.index]["wait"]
        if wait.startswith("timer:") and now - self._step_t0 > float(wait.split(":")[1]):
            self._advance()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)
