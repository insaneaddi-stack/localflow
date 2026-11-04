"""Tutoriel de première installation, directement à l'écran (pas dans le terminal).

Une carte du système (crème, filet, ombre chaude) posée au-dessus de la barre du
bas, le monogramme qui présente et une flèche animée vers la barre. Les étapes
avancent quand l'utilisateur FAIT la chose (première dictée, mains-libres, panneau)
ou clique le bouton de la carte. Le reste de l'écran est transparent à la souris.

Étape « Ta clé Voxtral » (absente si la clé est déjà là) : « Ouvrir la console » puis
« Coller la clé » (lit le presse-papiers), ou « Plus tard ».
"""

import math
import threading
import time

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSCursor,
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

from . import keydialog, theme, voxtral
from .overlay import _BandView, _attrs, _draw_text, _text_width, _orange, _encre, MARGINS, IDLE_H

CARD_W, CARD_H = 520.0, 150.0
GAP_ABOVE_BAR = 64.0      # espace entre la carte et la barre (la flèche vit là)
FPS = 60.0

STEPS = [
    {   # 0 — bienvenue
        "title": "Bienvenue dans AUR'IAFLOW",
        "lines": ["Cette petite barre, en bas, c'est AUR'IAFLOW. Elle reste là,",
                  "discrète, tant que tu ne parles pas."],
        "hint": "", "wait": "key", "arrow": True,
    },
    {   # clé Voxtral : l'étape disparaît si la clé est déjà dans le trousseau
        "title": "Ta clé Voxtral",
        "lines": ["Ouvre la console Mistral, crée une clé (API Keys), copie-la (⌘C).",
                  "Reviens ici et clique « Coller la clé »."],
        "hint": "", "wait": "key_set", "arrow": False, "needs_key": True,
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
    {   # 4 — réunions
        "title": "Réunions",
        "lines": ["Quand un appel démarre (Zoom, Meet, Teams…), AUR'IAFLOW propose de l'enregistrer :",
                  "micro + son de l'appel, transcript en direct, compte rendu en Markdown."],
        "hint": "Aussi dans le panneau (tuile Réunion) et le menu de la barre", "wait": "key", "arrow": True,
    },
    {   # 5 — fin
        "title": "C'est tout.",
        "lines": ["Dictionnaire, clé Voxtral et réglages : le A dans la barre des menus.",
                  "Dis « corrige X en Y » pour lui apprendre un mot. Bonne dictée."],
        "hint": "Se ferme tout seul", "wait": "timer:6", "arrow": False,
    },
]

class _TutorialView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_TutorialView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.tut = None
        return self

    def drawRect_(self, dirty):
        t = self.tut
        if t is None:
            return
        b = self.bounds()
        k = t.alpha
        step = t.steps[t.index]
        # --- carte
        cx = b.size.width / 2.0
        card = NSMakeRect(cx - CARD_W / 2, t.card_y, CARD_W, CARD_H)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            card, theme.RAYON_CARTE, theme.RAYON_CARTE)
        # L'élévation du système : l'ombre brune chaude, l'anneau d'un pixel, le filet.
        from AppKit import NSGraphicsContext, NSShadow
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.saveGraphicsState()
            sh = NSShadow.alloc().init()
            sh.setShadowOffset_((0.0, theme.OMBRE["dy"]))
            sh.setShadowBlurRadius_(theme.OMBRE["flou"])
            sh.setShadowColor_(theme.ns(theme.OMBRE["couleur"], theme.OMBRE["alpha"] * k))
            sh.set()
        theme.ns(theme.FOND, k).setFill()
        path.fill()
        if ctx is not None:
            ctx.restoreGraphicsState()
        theme.ns(theme.OMBRE_ANNEAU["couleur"], theme.OMBRE_ANNEAU["alpha"] * k).setStroke()
        path.setLineWidth_(theme.FILET * 2); path.stroke()
        theme.ns(theme.TRAIT, k).setStroke()
        path.setLineWidth_(theme.FILET); path.stroke()
        # --- le monogramme (à gauche), textes
        d = 34.0
        _BandView._draw_mono(self, theme.image(theme.MONOGRAMME),
                             card.origin.x + 38 - d / 2, card.origin.y + CARD_H - 46 - d / 2, d, k)
        tx = card.origin.x + 76
        _draw_text(step["title"], NSMakeRect(tx, card.origin.y + CARD_H - 44, CARD_W - 96, 22),
                   _attrs(17, 0.96 * k, weight=600, serif=True))
        y = card.origin.y + CARD_H - 70
        for line in step["lines"]:
            _draw_text(line, NSMakeRect(tx, y, CARD_W - 96, 17), _attrs(12.5, 0.85 * k))
            y -= 19
        if t.status and step["wait"] == "key_set":   # retour de l'étape clé : vérification, erreur…
            _draw_text(t.status, NSMakeRect(tx, y, CARD_W - 96, 17),
                       _attrs(12.5, 0.95 * k, weight=600, color=_orange(0.95 * k)))
        # progression (points) + aide
        n = len(t.steps)
        px = card.origin.x + 22
        for i in range(n):
            (_orange(0.95 * k) if i == t.index else _encre(0.18 * k)).setFill()
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
                    _orange(a * k).setStroke()
                    p = NSBezierPath.bezierPath(); p.setLineWidth_(width); p.setLineCapStyle_(1)
                    p.moveToPoint_((x0, y_top)); p.lineToPoint_((x0, y_tip + 10)); p.stroke()
                    head = NSBezierPath.bezierPath(); head.setLineWidth_(width); head.setLineCapStyle_(1); head.setLineJoinStyle_(1)
                    head.moveToPoint_((x0 - 8, y_tip + 18)); head.lineToPoint_((x0, y_tip + 8)); head.lineToPoint_((x0 + 8, y_tip + 18))
                    head.stroke()

BTN_W, BTN_H = 136.0, 30.0
LATER_W = 84.0

class _ButtonView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_ButtonView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.tut = None
        self.hover = False
        self.label = "Suivant"
        self.action = "main"      # "main" ou "later" (« Plus tard », discret)
        self.ghost = False
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
            self.tut.button(self.action)

    def drawRect_(self, dirty):
        b = self.bounds()
        k = self.tut.alpha if self.tut else 1.0
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), theme.RAYON_BOUTON, theme.RAYON_BOUTON)
        if self.ghost:
            theme.ns(theme.TRAIT, k).setStroke(); path.setLineWidth_(theme.FILET); path.stroke()
            a = _attrs(12, (0.9 if self.hover else 0.65) * k, weight=500)
        else:
            theme.ns(theme.O_PETIT if self.hover else theme.O_BOUTON, k).setFill(); path.fill()
            a = _attrs(12, k, weight=600, color=theme.ns(theme.BLANC, 0.96 * k))
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
        self.panel = None
        self.view = None
        self.steps = list(STEPS)
        self.status = ""            # ligne de retour de l'étape clé
        self.key_action = None      # posé par l'app : key_action(done) lit le presse-papiers hors fil principal
        self._key_ready = False     # la console a été ouverte (ou une clé est déjà copiée) : le bouton colle
        self._busy = False

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
        self.panel.setContentView_(self.view)
        # géométrie : la barre réduite est à MARGINS['idle'] du bas de l'écran visible
        self.target_y = MARGINS["idle"] + IDLE_H          # haut de la barre, en coordonnées fenêtre (origine = bas du visibleFrame)
        self.card_y = self.target_y + GAP_ABOVE_BAR
        self._vf = vf
        # bouton cliquable : sa propre petite fenêtre (le reste de l'écran reste traversant)
        bx = vf.origin.x + vf.size.width / 2.0 + CARD_W / 2 - 20 - BTN_W
        by = vf.origin.y + self.card_y + 10
        self.btn_panel, self.btn = self._button(bx, by, BTN_W, "Suivant", "main", False)
        self.later_panel, self.later = self._button(bx - 8 - LATER_W, by, LATER_W, "Plus tard", "later", True)

    def _button(self, x, y, w, label, action, ghost):
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, BTN_H), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(NSStatusWindowLevel + 2)
        panel.setHasShadow_(False)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)
        view = _ButtonView.alloc().initWithFrame_(NSMakeRect(0, 0, w, BTN_H))
        view.tut, view.label, view.action, view.ghost = self, label, action, ghost
        panel.setContentView_(view)
        return panel, view

    def show(self):
        if self.panel is None:
            self._build()
        self.active = True
        self.steps = self._visible_steps()
        self.index = 0
        self.alpha = 0.0
        self._enter_step()
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
            self.later_panel.orderOut_(None)

    def finish(self):
        self.config.data["onboarded"] = True
        self.config.save()
        self.hide()
        if self.on_done:
            self.on_done()

    def _advance(self):
        if self.index + 1 >= len(self.steps):
            self.finish(); return
        self.index += 1
        self.alpha = 0.0
        self._enter_step()

    @staticmethod
    def _visible_steps():
        """Toutes les étapes, sauf celle de la clé quand elle est déjà dans le trousseau."""
        return [s for s in STEPS if not (s.get("needs_key") and voxtral.api_key())]

    def _enter_step(self):
        self._step_t0 = time.time()
        self.status = ""
        self._busy = False
        key_step = self.steps[self.index]["wait"] == "key_set"
        if key_step:   # une clé déjà copiée : le bouton colle tout de suite
            self._key_ready = keydialog.clipboard_has_key()
        if self.panel is not None:
            if key_step:
                self.later_panel.orderFrontRegardless()
            else:
                self.later_panel.orderOut_(None)

    def button(self, action="main"):
        """Clic sur un bouton de la carte."""
        if not self.active:
            return
        if self.steps[self.index]["wait"] != "key_set":
            self.event("key"); return
        if action == "later":
            self._advance(); return
        if self._busy:
            return
        if not self._key_ready:
            keydialog.open_console()
            self._key_ready = True
            self.status = "Copie la clé (⌘C), puis clique « Coller la clé »."
            return
        self._busy, self.status = True, "Vérification…"
        self.key_action(self._key_done)

    def _key_done(self, result):
        self._busy = False
        if result in ("ok", "unverified"):
            self._advance(); return
        self.status = {"not_a_key": "Rien à coller : copie d'abord la clé (⌘C).",
                       "refused": "Mistral refuse cette clé : vérifie ta copie."}.get(result, "Échec : réessaie.")

    def event(self, name):
        """'key' (bouton Suivant), 'dictated', 'handsfree', 'panel'."""
        if not self.active:
            return False
        step = self.steps[self.index]
        if name == "key" or step["wait"] == name:
            self._advance()
            return True
        return False

    def _tick(self, timer):
        now = time.time()
        self.phase = now - self._t0
        self.alpha = min(1.0, self.alpha + 1.0 / (FPS * 0.28))
        step = self.steps[self.index]
        wait = step["wait"]
        if wait == "key_set":
            label = "Coller la clé" if self._key_ready else "Ouvrir la console"
            if voxtral.api_key():   # la clé est arrivée par ailleurs (menu « Clé Voxtral… »)
                self._advance(); return
        else:
            label = "Terminer" if self.index == len(self.steps) - 1 else ("Passer" if wait == "dictated" else "Suivant")
        if self.btn.label != label:
            self.btn.label = label
        self.btn.setNeedsDisplay_(True)
        self.later.setNeedsDisplay_(True)
        if wait.startswith("timer:") and now - self._step_t0 > float(wait.split(":")[1]):
            self._advance()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)
