"""Le menu de la barre de menus, dessiné dans la DA AUR'IA.

Un NSMenu natif n'accepte que des titres attribués : le fond, la surbrillance,
les marges et la coche restent ceux de macOS. Poser Figtree dessus ne suffisait
pas — le menu continuait de parler la langue du système.

On lui donne donc nos propres vues (`NSMenuItem.setView_`), et on redessine la
ligne entière : fond crème, filet, encre chaude, et l'unique orange pour ce qui
est actif. Le prix à payer est qu'une vue doit gérer elle-même sa surbrillance
et son clic, puisque macOS ne s'en occupe plus.
"""

import objc
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSBezierPath,
    NSColor,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMenuItem,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
)

from . import theme

H = 30.0            # hauteur d'une ligne
H_SEP = 11.0        # hauteur d'un filet
H_ENTETE = 46.0
PAD_X = 15.0
LARGEUR_MIN = 260.0
LARGEUR_MAX = 420.0


def _texte(s, size, weight, couleur):
    return NSAttributedString.alloc().initWithString_attributes_(
        s, {NSFontAttributeName: theme.font(size, weight),
            NSForegroundColorAttributeName: couleur})


class _Ligne(NSView):
    """Une entrée de menu : fond, texte, état, chevron de sous-menu."""

    def initWithItem_width_(self, item, width):
        self = objc.super(_Ligne, self).initWithFrame_(NSMakeRect(0, 0, width, H))
        if self is None:
            return None
        self.item = item
        self.survol = False
        return self

    def initWithSeparatorWidth_(self, width):
        self = objc.super(_Ligne, self).initWithFrame_(NSMakeRect(0, 0, width, H_SEP))
        if self is None:
            return None
        self.item = None
        self.survol = False
        return self

    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            self, None))
        objc.super(_Ligne, self).updateTrackingAreas()

    def mouseEntered_(self, event):
        if self.item is not None and self.item.isEnabled():
            self.survol = True
            self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        self.survol = False
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        """macOS ne déclenche plus l'action : la vue s'en charge.

        On referme d'abord le menu, sinon l'action s'exécute pendant le suivi de
        la souris et toute fenêtre ouverte depuis là arrive derrière le menu.
        """
        item = self.item
        if item is None or not item.isEnabled():
            return
        menu = item.menu()
        if menu is not None:
            menu.cancelTracking()
            idx = menu.indexOfItem_(item)
            if idx >= 0:
                menu.performActionForItemAtIndex_(idx)

    def drawRect_(self, rect):
        try:
            self._draw()
        except Exception:
            pass

    @objc.python_method
    def _draw(self):
        b = self.bounds()
        if self.item is None:                      # séparateur : un filet, centré
            theme.ns(theme.FOND_PUR).setFill()
            NSBezierPath.fillRect_(b)
            theme.ns(theme.TRAIT).setFill()
            NSBezierPath.fillRect_(NSMakeRect(PAD_X, b.size.height / 2.0 - 0.5,
                                              b.size.width - 2 * PAD_X, 1.0))
            return

        actif = self.item.isEnabled()
        theme.ns(theme.CARTE if self.survol else theme.FOND_PUR).setFill()
        NSBezierPath.fillRect_(b)

        x = PAD_X
        etat = self.item.state()                   # 1 = coché
        if etat:
            # Interrupteur allumé : le seul élément coloré de la ligne.
            r = 3.5
            theme.ns(theme.O_VITRINE).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, b.size.height / 2.0 - r, r * 2, r * 2)).fill()
        x += 14.0

        couleur = theme.ns(theme.ENCRE if actif else theme.ENCRE_3,
                           1.0 if actif else 0.55)
        t = _texte(self.item.title(), 13.0, 500 if etat else 400, couleur)
        sz = t.size()
        t.drawAtPoint_((x, (b.size.height - sz.height) / 2.0))

        if self.item.hasSubmenu():
            # chevron : deux traits, pas un glyphe système
            cx = b.size.width - PAD_X - 4.0
            cy = b.size.height / 2.0
            p = NSBezierPath.bezierPath()
            p.setLineWidth_(1.4)
            p.setLineCapStyle_(1)
            p.moveToPoint_((cx - 3.0, cy + 3.5))
            p.lineToPoint_((cx + 1.0, cy))
            p.lineToPoint_((cx - 3.0, cy - 3.5))
            theme.ns(theme.ENCRE_3).setStroke()
            p.stroke()


class _Entete(NSView):
    """Le verrou de marque en tête de menu : logotype, FLOW, et l'état du moteur."""

    def initWithWidth_status_(self, width, status):
        self = objc.super(_Entete, self).initWithFrame_(NSMakeRect(0, 0, width, H_ENTETE))
        if self is None:
            return None
        # `status` est un appelable : l'état du moteur change après la
        # construction du menu, une chaîne figée mentirait au bout d'un moment.
        self.status = status
        return self

    def drawRect_(self, rect):
        try:
            self._draw()
        except Exception:
            pass

    @objc.python_method
    def _draw(self):
        b = self.bounds()
        theme.ns(theme.CREME).setFill()
        NSBezierPath.fillRect_(b)
        theme.ns(theme.TRAIT).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, b.size.width, 1.0))

        cap = 15.0
        y = b.size.height - 28.0
        wm = theme.image(theme.WORDMARK)
        x = PAD_X
        if wm is not None:
            sz = wm.size()
            w = cap * (sz.width / sz.height) if sz.height else cap * 3.8
            wm.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(x, y, w, cap), NSMakeRect(0, 0, 0, 0), 2, 0.95)
            x += w + 3.0
            t = _texte("FLOW", cap, 700, theme.ns(theme.ENCRE))
            t.drawAtPoint_((x, y - cap * 0.06))
        else:
            for frag, col, poids in ((theme.NOM_AVANT, theme.ENCRE, 700),
                                     (theme.NOM_APOSTROPHE, theme.O_PETIT, 700),
                                     (theme.NOM_APRES, theme.ENCRE, 700)):
                t = _texte(frag, cap, poids, theme.ns(col))
                t.drawAtPoint_((x, y))
                x += t.size().width

        txt = ""
        try:
            txt = self.status() if callable(self.status) else (self.status or "")
        except Exception:
            pass
        if txt:
            s = _texte(txt, 11.0, 400, theme.ns(theme.ENCRE_3))
            s.drawAtPoint_((PAD_X, 8.0))


def largeur_pour(titres):
    """Largeur commune : la plus longue entrée, bornée."""
    w = LARGEUR_MIN
    for t in titres:
        if not t:
            continue
        w = max(w, _texte(t, 13.0, 500, theme.ns(theme.ENCRE)).size().width + 2 * PAD_X + 46.0)
    return min(w, LARGEUR_MAX)


MARQUEUR = "auriaflow-entete"


def habiller(menu, status=None, profondeur=0):
    """Remplace chaque entrée par une vue de marque. Renvoie le nombre d'entrées.

    Idempotent : rappelée sur un menu déjà habillé, elle ne réempile pas d'en-tête.
    """
    items = [menu.itemAtIndex_(i) for i in range(menu.numberOfItems())
             if menu.itemAtIndex_(i).representedObject() != MARQUEUR]
    largeur = largeur_pour([it.title() for it in items if not it.isSeparatorItem()])
    n = 0
    for it in items:
        if it.isSeparatorItem():
            it.setView_(_Ligne.alloc().initWithSeparatorWidth_(largeur))
        else:
            it.setView_(_Ligne.alloc().initWithItem_width_(it, largeur))
        n += 1
        sub = it.submenu()
        if sub is not None and profondeur < 2:
            n += habiller(sub, "", profondeur + 1)
    if profondeur == 0 and not any(
            menu.itemAtIndex_(i).representedObject() == MARQUEUR
            for i in range(menu.numberOfItems())):
        entete = NSMenuItem.alloc().init()
        entete.setEnabled_(False)
        entete.setRepresentedObject_(MARQUEUR)
        entete.setView_(_Entete.alloc().initWithWidth_status_(largeur, status))
        menu.insertItem_atIndex_(entete, 0)
    return n
