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
        "hint": "Esc pour continuer", "wait": "key", "arrow": True,
    },
    {   # 1 — première dictée (attend un vrai collage)
        "title": "Ta première dictée",
        "lines": ["Clique dans un champ de texte (Notes, Messages, n'importe où),",
                  "puis maintiens fn, parle, relâche. Le texte est collé."],
        "hint": "J'attends ta première dictée…   (Esc pour passer)", "wait": "dictated", "arrow": True,
    },
    {   # 2 — mains-libres
        "title": "Mains-libres",
        "lines": ["Pour une longue dictée : appuie sur fn + espace, parle autant",
                  "que tu veux, puis fn pour terminer."],
        "hint": "Essaie, ou Esc pour continuer", "wait": "handsfree", "arrow": True,
    },
    {   # 3 — panneau
        "title": "Le panneau",
        "lines": ["Double-tap sur fn : historique, nettoyage IA, sons,",
                  "et « Copier » la dernière dictée. Touches 1–4, Esc pour fermer."],
        "hint": "Essaie, ou Esc pour continuer", "wait": "panel", "arrow": True,
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
        _draw_text(step["hint"], NSMakeRect(card.origin.x + CARD_W - 20 - hw, card.origin.y + 12, hw + 2, 14), ha)
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

    def show(self):
        if self.panel is None:
            self._build()
        self.active = True
        self.index = 0
        self.alpha = 0.0
        self._step_t0 = time.time()
        self.panel.orderFrontRegardless()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / FPS, True, self._tick)

    def hide(self):
        self.active = False
        if self._timer is not None:
            self._timer.invalidate(); self._timer = None
        if self.panel is not None:
            self.panel.orderOut_(None)

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
        wait = STEPS[self.index]["wait"]
        if wait.startswith("timer:") and now - self._step_t0 > float(wait.split(":")[1]):
            self._advance()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)
