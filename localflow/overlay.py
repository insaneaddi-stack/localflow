"""Bande flottante en bas de l'écran — un seul panneau, plusieurs états :

- idle      : petite barre sombre (l'app est allumée), survol = s'éclaire
- expanded  : panneau « bulles » (historique cliquable, stats, réglages, actions)
- recording : carte crème, forme d'onde à l'encre, le fil orange en mains-libres
- processing: carte de même largeur, barre de progression 0→100 %

Transitions interpolées à 60 fps (ressort amorti), ombre portée douce.
À utiliser uniquement depuis le thread principal.
"""

import math
import time

import objc
from AppKit import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSColorSpace,
    NSCursor,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskRightMouseDown,
    NSGradient,
    NSImage,
    NSImageSymbolConfiguration,
    NSCompositingOperationSourceOver,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSPanel,
    NSParagraphStyleAttributeName,
    NSPointInRect,
    NSScreen,
    NSStatusWindowLevel,
    NSStringDrawingUsesLineFragmentOrigin,
    NSTimer,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSTrackingMouseMoved,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

from . import theme

# ---- géométrie (tailles du contenu, hors marge d'ombre) ----
PAD = 26.0                      # marge autour pour l'ombre
IDLE_W, IDLE_H = 76.0, 8.0
HOVER_W, HOVER_H = 168.0, 26.0  # au survol on affiche vraiment quoi faire, d'où la place
PILL_W, PILL_H = 184.0, 36.0
PROC_W, PROC_H = 184.0, 36.0    # même largeur que l'enregistrement : pas d'à-coup entre les deux
WAVE_COUNT, WAVE_W, WAVE_GAP = 20, 2.5, 2.6   # mini-forme d'onde défilante
PANEL_W, PANEL_H = 720.0, 292.0
MEET_W, MEET_H = 118.0, 22.0          # réunion en cours : point rouge + chrono
OFFER_W, OFFER_H = 452.0, 44.0        # « Appel X détecté » + [Enregistrer] [Ignorer]
CAL_W, CAL_H = 560.0, 44.0            # aperçu agenda : « Demain 14:00 · Titre » + décompte
MARGINS = {"idle": 14.0, "hover": 14.0, "recording": 18.0, "processing": 18.0, "expanded": 44.0,
           "meeting": 14.0, "meeting_offer": 18.0, "calendar_preview": 18.0}

# Le système AUR'IA n'a pas de rouge : « ce qui demande de l'attention est orange ».
# Et il n'a qu'une seule action colorée par écran — d'où un seul accent ici, la
# distinction entre états passant par la FORME (le fil pour le mains-libres).
ORANGE = theme.O_VITRINE
FIL = theme.O_FIL

# Le verre natif fournirait son propre fond ; la DA demande l'encre chaude de la
# marque. On garde le conteneur (le reste du code s'appuie dessus) mais aucun
# matériau : la pilule est peinte ici, filet compris.
USE_MATERIAL = False
FPS = 60.0
ANIM_S = theme.D_ETAT          # changement d'état : une des cinq durées du système
ANIM_S_REDUCED = theme.D_DOIGT # « Réduire les animations » : la plus courte
BAR_COUNT, BAR_W, BAR_GAP = 5, 3.0, 4.0
SEGMENTS = 140

def _orange(a):
    """L'orange de décor. Jamais du texte : il ne fait que 3,0:1 sur le crème."""
    return theme.ns(ORANGE, a)

def _orange_t(a):
    """L'orange qui a le droit de porter du petit texte — 5,2:1."""
    return theme.ns(theme.O_PETIT, a)

def _fil(a):
    return theme.ns(FIL, a)

def _encre(a):
    """L'encre du système. Chaude, tirée vers le brun, jamais noire."""
    return theme.ns(theme.ENCRE, a)

def _encre2(a):
    return theme.ns(theme.ENCRE_2, a)

def _trait(a):
    return theme.ns(theme.TRAIT, a)

def _reduce_motion():
    """Réglage macOS « Réduire les animations » (Accessibilité → Affichage).

    Relu à chaque transition : l'utilisateur peut le changer sans relancer l'app.
    Un rebond de 8 % est précisément ce que ce réglage sert à supprimer — pour qui
    en a besoin, c'est une gêne physique, pas une préférence esthétique.
    """
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


def _ease(k, reduced=False):
    """La courbe « arrivée » du système : ce qui se pose.

    Le ressort qui était ici dépassait la cible de 8 % pour se lire « physique ».
    AUR'IA l'interdit — « aucun rebond, aucun ressort » — et la courbe d'arrivée
    (.16, 1, .3, 1) donne le même départ sec sans jamais dépasser.
    """
    if k >= 1.0:
        return 1.0
    if reduced:
        return 1.0 - (1.0 - k) ** 3
    return theme.ease_arrivee(k)

def _lerp(a, b, k):
    return a + (b - a) * k

def _attrs(size, alpha=0.92, weight=None, truncate=True, color=None, serif=False, italic=False):
    """Attributs de texte du système. Figtree partout, Newsreader pour les titres.

    Les appels d'origine passent une graisse AppKit (−1…1) ; AUR'IA raisonne en
    400/500/600/700. On accepte les deux plutôt que de réécrire trente appels.
    """
    if weight is None:
        w = 400
    elif weight < 1.0:
        w = 400 if weight <= 0.35 else (500 if weight < 0.5 else 600)
    else:
        w = int(weight)
    font = theme.font(size, w, serif=serif, italic=italic)
    ps = NSMutableParagraphStyle.alloc().init()
    if truncate:
        ps.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: color or _encre(alpha),
        NSParagraphStyleAttributeName: ps,
    }

def _draw_text(text, rect, attrs):
    s = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    s.drawWithRect_options_(rect, NSStringDrawingUsesLineFragmentOrigin)

def _text_width(text, attrs):
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs).size().width

def _perimeter_point(t, rect):
    """Point sur le contour d'une pilule, t ∈ [0,1) dans le sens horaire depuis le haut-gauche."""
    x, y, w, h = rect.origin.x, rect.origin.y, rect.size.width, rect.size.height
    r = h / 2.0
    straight = max(0.0, w - 2 * r)
    arc = math.pi * r
    total = 2 * straight + 2 * arc
    d = (t % 1.0) * total
    if d < straight:
        return (x + r + d, y + h)
    d -= straight
    if d < arc:
        a = math.pi / 2 - d / r
        return (x + w - r + r * math.cos(a), y + r + r * math.sin(a))
    d -= arc
    if d < straight:
        return (x + w - r - d, y)
    d -= straight
    a = -math.pi / 2 - d / r
    return (x + r + r * math.cos(a), y + r + r * math.sin(a))

class _BandView(NSView):
    """Dessine tous les états. `overlay` fournit état, géométrie et données."""

    def initWithFrame_(self, frame):
        self = objc.super(_BandView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.overlay = None
        self.hit_zones = []   # [(rect, action, payload)] recalculé à chaque dessin du panneau
        self.hover_pt = None
        self._tracking = None
        self.in_glass = False  # True quand on est la contentView d'un NSGlassEffectView
        return self

    def acceptsFirstMouse_(self, event):
        return True

    def updateTrackingAreas(self):
        if self._tracking is not None:
            self.removeTrackingArea_(self._tracking)
        self._tracking = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            NSTrackingMouseEnteredAndExited | NSTrackingMouseMoved | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            self, None,
        )
        self.addTrackingArea_(self._tracking)

    # ---- souris ----

    def mouseEntered_(self, event):
        self.overlay._on_hover(True)

    def mouseExited_(self, event):
        self.hover_pt = None
        self.overlay._on_hover(False)
        self.setNeedsDisplay_(True)

    def mouseMoved_(self, event):
        self.hover_pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        over = any(NSPointInRect(self.hover_pt, z[0]) for z in self.hit_zones)
        (NSCursor.pointingHandCursor() if over or self.overlay.state in ("idle", "hover") else NSCursor.arrowCursor()).set()
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        for rect, action, payload in self.hit_zones:
            if NSPointInRect(pt, rect):
                self.overlay._action(action, payload)
                return
        self.overlay._on_click()

    # ---- dessin ----

    def drawRect_(self, dirty):
        """Un plantage ici tue l'app entière.

        AppKit transforme une exception Python remontée d'un dessin en
        NSException, et `+[NSApplication _crashOnException:]` termine le
        processus — un .ips, la barre de menus qui disparaît, la dictée en cours
        perdue. À 60 images par seconde, sur des tailles interpolées qui peuvent
        rendre un rectangle négatif le temps d'une image, ça n'est pas un risque
        théorique : c'est arrivé le 2 septembre. On préfère une image manquante
        et une ligne dans le log.
        """
        try:
            self._draw(dirty)
        except Exception:
            import traceback
            now = time.time()
            if now - getattr(_BandView, "_last_draw_err", 0.0) > 10.0:
                _BandView._last_draw_err = now
                print("[overlay] erreur de dessin (image sautée) :\n"
                      + traceback.format_exc(), flush=True)

    @objc.python_method
    def _draw(self, dirty):
        ov = self.overlay
        if ov is None:
            return
        b = self.bounds()
        cw, ch = ov.cur_w, ov.cur_h
        # « Rayon 8px, pas de pilule » : la règle vaut pour tout ce qui flotte.
        radius = theme.RAYON_CARTE if ch > 60.0 else theme.RAYON_FLOTTANT
        if self.in_glass:
            # On EST la contentView du verre : nos bounds sont déjà la pilule, et c'est
            # le verre qui fournit fond, arête et ombre. On ne dessine que le contenu.
            content = NSMakeRect(0, 0, b.size.width, b.size.height)
        else:
            # rectangle de contenu courant (interpolé), centré en bas
            content = NSMakeRect((b.size.width - cw) / 2.0, PAD, cw, ch)
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(content, radius, radius)

        if not self.in_glass:
            # L'élévation de production : une ombre brune chaude, un anneau
            # d'un pixel, un filet. Les trois couches noires empilées d'avant
            # étaient une profondeur peinte, ce que le système refuse.
            from AppKit import NSGraphicsContext, NSShadow
            # `currentContext()` est nul quand drawRect_ est appelé à la main,
            # hors cycle d'affichage — c'est ce que fait l'auto-test. L'ombre
            # saute alors, le reste doit passer.
            ctx = NSGraphicsContext.currentContext()
            if ctx is not None:
                ctx.saveGraphicsState()
                sh = NSShadow.alloc().init()
                sh.setShadowOffset_((0.0, theme.OMBRE["dy"]))
                sh.setShadowBlurRadius_(theme.OMBRE["flou"])
                sh.setShadowColor_(theme.ns(theme.OMBRE["couleur"], theme.OMBRE["alpha"]))
                sh.set()
            theme.ns(theme.FOND, 1.0).setFill()
            body.fill()
            if ctx is not None:
                ctx.restoreGraphicsState()
            # Le filet, puis l'anneau d'un pixel qui double l'ombre en production.
            theme.ns(theme.OMBRE_ANNEAU["couleur"], theme.OMBRE_ANNEAU["alpha"]).setStroke()
            body.setLineWidth_(theme.FILET * 2)
            body.stroke()
            _trait(1.0).setStroke()
            body.setLineWidth_(theme.FILET)
            body.stroke()
        else:
            # Le reflet mobile reste à nous : le verre natif a une arête, mais son
            # reflet est fixe. C'est le point brillant qui BOUGE qui fait lire « liquide ».
            self._draw_specular(content, radius)

        self.hit_zones = []
        # Fondu croisé. `content_alpha` retombait à 0 d'un seul coup au changement
        # d'état : l'ancien contenu disparaissait en une frame, puis le nouveau
        # arrivait en fondu — un pop très visible sur enregistrement → transcription.
        # On dessine donc l'ancien état par-dessous tant qu'il s'efface.
        if ov.fade_out > 0.01 and ov.prev_state and ov.prev_state != ov.state:
            self._ghost = True
            try:
                self._draw_state(ov.prev_state, content, ov.fade_out)
            finally:
                self._ghost = False
        self._draw_state(ov.state, content, ov.content_alpha)

    @objc.python_method
    def _draw_state(self, st, content, k):
        """Dessine le contenu d'un état à l'opacité k. `k` porte tout le fondu :
        aucune fonction de dessin ne doit supposer qu'elle est à pleine opacité."""
        if k <= 0.01:
            return
        if st == "processing":
            self._draw_progress(content, k)
        elif st == "recording":
            self._draw_recording(content, k)
        elif st == "expanded":
            self._draw_panel(content, k)
        elif st == "meeting":
            self._draw_meeting(content, k)
        elif st == "meeting_offer":
            self._draw_offer(content, k)
        elif st == "calendar_preview":
            self._draw_calendar(content, k)
        elif st == "hover":
            self._draw_idle(content, hover=True, k=k)
        else:
            self._draw_idle(content, hover=False, k=k)

    @objc.python_method
    def _draw_specular(self, pill, radius):
        """Le filet de la pilule quand le fond est fourni par un matériau.

        Il y avait ici un reflet spéculaire qui circulait sur l'arête pour faire
        lire « liquide ». C'est une lueur, et le système l'exclut : « pour
        détacher un élément, une élévation, un filet, une ombre orange très
        douce. Jamais une lueur. » Il ne reste donc que le filet.
        """
        rim = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(pill.origin.x + 0.5, pill.origin.y + 0.5,
                       pill.size.width - 1.0, pill.size.height - 1.0), radius, radius)
        rim.setLineWidth_(theme.FILET)
        _trait(1.0).setStroke()
        rim.stroke()

    @objc.python_method
    def _draw_idle(self, pill, hover, k=1.0):
        """Au repos et au survol, c'est le monogramme qui tient la carte.

        Il y avait ici un point violet qui respirait. « Le A seul » est la forme
        que la marque réserve à l'onglet, à l'avatar et à l'icône : une pastille
        de 18 px posée au bord de l'écran est exactement ce cas-là.
        """
        ov = self.overlay
        cx = pill.origin.x + pill.size.width / 2.0
        cy = pill.origin.y + pill.size.height / 2.0
        mono = theme.image(theme.MONOGRAMME)

        if hover:
            hint = "Maintenir fn  ·  double-tap"
            ha = _attrs(11.5, 0.95 * k, weight=500, truncate=False,
                        color=_encre2(0.95 * k))
            hw = _text_width(hint, ha)
            d, gap = 15.0, 10.0
            x = cx - (d + gap + hw) / 2.0
            self._draw_mono(mono, x, cy - d / 2, d, k)
            _draw_text(hint, NSMakeRect(x + d + gap, cy - 8.0, hw + 2, 16), ha)
            return

        # Au repos la carte fait 8 px de haut : le monogramme n'y tiendrait pas.
        # Un point suffit, et il reste le seul élément coloré de l'écran.
        d = 3.5
        _orange(0.95 * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - d / 2, cy - d / 2, d, d)).fill()
        _trait(0.9 * k).setFill()
        lw = pill.size.width * 0.42
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(cx - lw / 2, cy - 0.5, lw, 1.0), 0.5, 0.5).fill()

    @objc.python_method
    def _draw_lockup(self, x, y, cap, k):
        """Le logotype AUR'IA suivi de FLOW. Renvoie la largeur occupée.

        Si le wordmark manque, on écrit le nom en Figtree avec l'apostrophe en
        orange — le brand book l'exige : « elle est orange, ou réservée en
        blanc. Jamais noire, jamais d'une autre teinte. »
        """
        wm = theme.image(theme.WORDMARK)
        fa = _attrs(cap, 0.95 * k, weight=700, truncate=False)
        if wm is not None:
            sz = wm.size()
            w = cap * (sz.width / sz.height) if sz.height else cap * 3.8
            wm.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(x, y - cap * 0.06, w, cap), NSMakeRect(0, 0, 0, 0), 2, 0.95 * k)
            fw = _text_width("FLOW", fa)
            _draw_text("FLOW", NSMakeRect(x + w + 3, y - cap * 0.06, fw + 2, cap * 1.4), fa)
            return w + 3 + fw
        oa = _attrs(cap, 0.95 * k, weight=700, truncate=False, color=_orange_t(0.95 * k))
        w1 = _text_width(theme.NOM_AVANT, fa)
        w2 = _text_width(theme.NOM_APOSTROPHE, oa)
        _draw_text(theme.NOM_AVANT, NSMakeRect(x, y, w1 + 2, cap * 1.4), fa)
        _draw_text(theme.NOM_APOSTROPHE, NSMakeRect(x + w1, y, w2 + 2, cap * 1.4), oa)
        w3 = _text_width(theme.NOM_APRES, fa)
        _draw_text(theme.NOM_APRES, NSMakeRect(x + w1 + w2, y, w3 + 2, cap * 1.4), fa)
        return w1 + w2 + w3

    @objc.python_method
    def _draw_mono(self, mono, x, y, d, k):
        """Le monogramme, ou un disque orange s'il manque."""
        if mono is not None:
            mono.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(x, y, d, d), NSMakeRect(0, 0, 0, 0), 2, k)
            return
        _orange(0.95 * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, y, d, d)).fill()

    @objc.python_method
    def _draw_meeting(self, pill, k):
        """Réunion en cours : point rouge qui pulse, chrono, mini-barres du micro."""
        ov = self.overlay
        info = ov.meeting_info() or {}
        r, g, b = ORANGE
        pulse = 0.5 + 0.5 * math.sin(ov.phase * 2.2)
        cx = pill.origin.x + 14
        cy = pill.origin.y + pill.size.height / 2.0
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, (0.18 + 0.18 * pulse) * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 7, cy - 7, 14, 14)).fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, (0.8 + 0.2 * pulse) * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 3.5, cy - 3.5, 7, 7)).fill()
        txt = info.get("clock", "00:00")
        _draw_text(txt, NSMakeRect(cx + 12, cy - 7, 60, 14), _attrs(11.5, 0.92 * k, weight=0.5))
        # 3 mini-barres : niveau micro (moi) / système (eux)
        lv = 0.5 * ov.level + 0.5 * float(info.get("sys_level", 0.0))
        x0 = pill.origin.x + pill.size.width - 30
        for i in range(3):
            h = 3.0 + 8.0 * max(0.0, min(1.0, lv * (1.4 - 0.3 * abs(i - 1))))
            _encre((0.55 + 0.4 * lv) * k).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x0 + i * 6, cy - h / 2, 3, h), 1.5, 1.5).fill()

    @objc.python_method
    def _draw_offer(self, pill, k):
        """Proposition : « Réunion Zoom détectée — enregistrer ? [Oui] [Non] »."""
        ov = self.overlay
        info = ov.meeting_info() or {}
        r, g, b = ORANGE
        cy = pill.origin.y + pill.size.height / 2.0
        x = pill.origin.x + 18
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.9 * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, cy - 4, 8, 8)).fill()
        # « Google Meet — l'enregistrer ? » se lisait mal ; et « Oui / Non » n'indique
        # pas ce qui va se passer. On nomme l'action sur le bouton.
        app = info.get("offer", "").strip()
        label = f"Appel {app} détecté" if app else "Appel détecté"
        # largeur du texte déduite de celle des boutons, sinon les deux se chevauchent
        w_ign, w_rec, gap = 76.0, 96.0, 8.0
        buttons_w = w_ign + gap + w_rec
        text_w = pill.size.width - 18 - 16 - buttons_w - 18 - 12
        if text_w < 8.0 or pill.size.height < 20.0:
            return          # pilule encore en pleine transition (voir _draw_progress)
        _draw_text(label, NSMakeRect(x + 16, cy - 8, text_w, 16), _attrs(12.5, 0.94 * k, weight=0.4))
        bx = pill.origin.x + pill.size.width - 18
        self._chip(bx - w_ign, cy - 12, "Ignorer", on=None, action="meeting_decline", alpha=k, min_w=w_ign)
        self._chip(bx - w_ign - gap - w_rec, cy - 12, "Enregistrer", on=True, action="meeting_accept", alpha=k, min_w=w_rec)

    @objc.python_method
    def _draw_calendar(self, pill, k):
        """Aperçu avant écriture : « Demain 14:00 · Titre » et le temps qui reste.

        Volontairement non cliquable : la bande ne doit jamais intercepter un
        clic destiné au Dock (même raison que pour le survol). La seule sortie
        est Esc, donc elle est écrite noir sur crème plutôt que sous-entendue.
        """
        ov = self.overlay
        apercu = ov.calendar_preview or {}
        r, g, b = ORANGE
        cy = pill.origin.y + pill.size.height / 2.0
        x = pill.origin.x + 18
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.9 * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, cy - 4, 8, 8)).fill()

        restant = max(0.0, apercu.get("until", 0.0) - time.time())
        note = f"Esc annule · {restant:0.0f} s" if restant >= 1.0 else "Envoi…"
        attrs_note = _attrs(11.5, 0.62 * k, weight=0.5)
        w_note = _text_width(note, attrs_note) + 6

        text_w = pill.size.width - 18 - 16 - w_note - 18
        if text_w < 8.0 or pill.size.height < 20.0:
            return          # pilule encore en pleine transition (voir _draw_progress)
        _draw_text(apercu.get("label", ""), NSMakeRect(x + 16, cy - 8, text_w, 16),
                   _attrs(12.5, 0.94 * k, weight=0.4, truncate=True))
        _draw_text(note, NSMakeRect(pill.origin.x + pill.size.width - 18 - w_note, cy - 8, w_note, 16),
                   attrs_note)

    @objc.python_method
    def _draw_recording(self, pill, k):
        """Forme d'onde défilante + chrono.

        Avant : cinq barres en éventail, sans durée. En mains-libres (jusqu'à 10 min)
        on n'avait donc aucun moyen de savoir depuis quand ça tournait. La géométrie
        est calée sur celle de la transcription — même piste, même emplacement du
        chiffre à droite — pour qu'il n'y ait aucun saut entre les deux états.
        """
        ov = self.overlay
        px, py = pill.origin.x, pill.origin.y
        pw, ph = pill.size.width, pill.size.height
        m, num_w = 16.0, 40.0
        track_x = px + m
        # pw est interpolé pendant la transition : il part de la taille de l'état
        # précédent (8 pt au repos) et une soustraction nue donnerait une largeur
        # négative, donc un NSRect invalide et une exception dans drawRect_.
        track_w = pw - 2 * m - num_w - 8.0
        if track_w < 8.0 or ph < 16.0:
            return
        cy = py + ph / 2.0 + 3.0   # 3 px de plus haut : le fil passe dessous
        max_h = ph - 20.0

        if ov.rec_calendar:
            # Un point orange dans la marge gauche : cette dictée ne sera pas
            # tapée, elle part dans l'agenda. Sans ce témoin, on ne l'apprend
            # qu'à la fin — et c'est trop tard pour se corriger.
            r, g, b = ORANGE
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.95 * k).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(px + 6.0, py + ph / 2.0 - 3.0, 6.0, 6.0)).fill()

        total = WAVE_COUNT * WAVE_W + (WAVE_COUNT - 1) * WAVE_GAP
        x0 = track_x + (track_w - total) / 2.0
        # Entrée en éventail depuis le centre : les barres apparaissaient d'un bloc.
        # 220 ms, et le décalage part du milieu — c'est de là que naît le son.
        since = (time.time() - ov.rec_t0) if ov.rec_t0 else 99.0
        mid = (WAVE_COUNT - 1) / 2.0
        for i, lv in enumerate(ov.wave):
            grow = max(0.0, min(1.0, (since - abs(i - mid) / mid * 0.10) / 0.22))
            h = max(2.0, min(1.0, lv * 1.6) * max_h) * (grow * grow * (3.0 - 2.0 * grow))
            h = max(2.0, h)
            # les plus anciennes (à gauche) s'effacent : le sens de lecture est évident
            a = (0.30 + 0.70 * (i / max(1, WAVE_COUNT - 1))) * k
            r = NSMakeRect(x0 + i * (WAVE_W + WAVE_GAP), cy - h / 2.0, WAVE_W, h)
            _encre(a).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, WAVE_W / 2, WAVE_W / 2).fill()

        # Mains-libres : le fil se coud sous la forme d'onde, puis ne bouge plus.
        # C'était un chrono violet. Le système n'a qu'un accent et distingue les
        # états par la forme : le fil dit « ça continue sans toi », ce qui est
        # exactement son emploi dans la marque — la ligne qui relie sans rompre.
        if ov.rec_hands_free:
            if not ov.hf_t0:
                ov.hf_t0 = time.time()
            e = theme.ease_arrivee(min(1.0, (time.time() - ov.hf_t0) / theme.D_RECIT))
            fp = NSBezierPath.bezierPath()
            fp.setLineWidth_(3.0)          # 5 px sur une piste de 36, c'est trop
            fp.setLineCapStyle_(1)         # extrémités arrondies
            n = 28
            for i in range(n + 1):
                t = i / n
                if t > e:
                    break
                x = track_x + track_w * t
                y = py + 6.0 + 1.8 * math.sin(t * math.pi * 2.0)
                fp.moveToPoint_((x, y)) if i == 0 else fp.lineToPoint_((x, y))
            if fp.elementCount() > 1:
                _fil(0.95 * k).setStroke()
                fp.stroke()
        else:
            ov.hf_t0 = 0.0

        elapsed = max(0.0, time.time() - ov.rec_t0) if ov.rec_t0 else 0.0
        clock = f"{int(elapsed) // 60}:{int(elapsed) % 60:02d}"
        ta = _attrs(11.5, 0.90 * k, weight=0.45, truncate=False)
        _draw_text(clock, NSMakeRect(px + pw - m - num_w, py + (ph - 15) / 2.0, num_w, 15), ta)

    @objc.python_method
    def _draw_progress(self, pill, k):
        """Barre 0→100 % pendant la transcription.

        Remplace les cinq barres qui oscillaient sur un sinus : joli, mais ça
        n'indiquait rien — impossible de savoir si on en avait pour 0,5 s ou 5 s.
        """
        ov = self.overlay
        p = max(0.0, min(1.0, ov.progress_p))
        px, py = pill.origin.x, pill.origin.y
        pw, ph = pill.size.width, pill.size.height

        pct = f"{int(p * 100 + 0.5)} %"
        pa = _attrs(11.0, 0.90 * k, weight=0.45, truncate=False)
        num_w = 40.0
        m = 16.0
        track_x = px + m
        track_w = pw - 2 * m - num_w - 8.0
        if track_w < 8.0 or ph < 16.0:
            return          # pilule encore en pleine transition : rien à dessiner
        track_h = 5.0
        track_y = py + (ph - track_h) / 2.0

        track = NSMakeRect(track_x, track_y, track_w, track_h)
        _encre(0.13 * k).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(track, track_h / 2, track_h / 2).fill()

        fill_w = max(track_h, track_w * p)   # jamais plus fin que son propre arrondi
        if p > 0.0:
            from AppKit import NSGraphicsContext
            fill = NSMakeRect(track_x, track_y, fill_w, track_h)
            fpath = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill, track_h / 2, track_h / 2)
            ctx = NSGraphicsContext.currentContext()
            ctx.saveGraphicsState()
            fpath.addClip()
            # Aplat orange, sans dégradé : chaque orange du système a un seul
            # emploi, en mélanger deux pour faire joli les vide tous les deux.
            _orange(0.95 * k).setFill()
            NSBezierPath.fillRect_(fill)
            # Reflet qui balaie la partie remplie : garde la barre vivante quand la
            # progression est lente, sans jamais laisser croire qu'elle avance.
            if not ov.progress_done:
                sheen_x = track_x + (fill_w + 60.0) * ((ov.phase * 0.65) % 1.0) - 30.0
                sh = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                    [_encre(0.0), _encre(0.30 * k), _encre(0.0)], [0.0, 0.5, 1.0], NSColorSpace.sRGBColorSpace())
                sh.drawInRect_angle_(NSMakeRect(sheen_x, track_y, 60.0, track_h), 0.0)
            ctx.restoreGraphicsState()
            # Arrivée : le fil se coud d'un bout à l'autre de la piste.
            # C'était un halo blanc qui se dilatait — le système exclut la lueur,
            # et il donne justement au fil cet emploi : « elle se dessine une fois
            # à l'arrivée, puis ne bouge plus ». Même geste, même durée, sans lueur.
            dt = time.time() - ov.done_t0
            if ov.done_t0 and dt < theme.D_ETAT:
                e = theme.ease_arrivee(dt / theme.D_ETAT)
                # Le fil traverse toute la pilule, pas seulement la piste : la
                # barre est déjà pleine et orange, un trait posé dessus ne se
                # verrait pas. Et traverser une frontière est précisément le seul
                # privilège que le système accorde au fil.
                fp = NSBezierPath.bezierPath()
                fp.setLineWidth_(theme.FIL_EPAISSEUR)
                fp.setLineCapStyle_(1)          # extrémités arrondies
                x0 = px + ph / 2.0
                y = py + 4.0
                fp.moveToPoint_((x0, y))
                fp.lineToPoint_((x0 + (pw - ph) * e, y))
                _fil(0.95 * (1.0 - 0.5 * e) * k).setStroke()
                fp.stroke()

        _draw_text(pct, NSMakeRect(px + pw - m - num_w, py + (ph - 15) / 2.0, num_w, 15), pa)

    @objc.python_method
    def _draw_bars(self, pill, st, k):
        ov = self.overlay
        total = BAR_COUNT * BAR_W + (BAR_COUNT - 1) * BAR_GAP
        x0 = pill.origin.x + (pill.size.width - total) / 2.0
        cy = pill.origin.y + pill.size.height / 2.0
        max_h = max(4.0, pill.size.height - 16.0)
        for i in range(BAR_COUNT):
            if st == "processing":
                lv = 0.25 + 0.55 * (0.5 + 0.5 * math.sin(ov.phase * 7.0 - i * 0.9))
                a = 0.75
            else:
                lv = ov.bars[i]
                a = 0.55 + 0.45 * min(1.0, lv * 1.5)
            h = max(3.0, lv * max_h)
            r = NSMakeRect(x0 + i * (BAR_W + BAR_GAP), cy - h / 2.0, BAR_W, h)
            _encre(a * k).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, BAR_W / 2, BAR_W / 2).fill()

    # ---- panneau déplié ----

    @objc.python_method
    def _chip(self, x, y, label, on=None, action=None, payload=None, alpha=1.0, min_w=0.0):
        """Bouton du système. on=True : l'unique action colorée, aplat #C94E00.

        Rayon 10 px, pas de pilule. Le neutre est une carte à filet, jamais un
        deuxième aplat coloré — « une seule action colorée par écran ».
        """
        actif = on is True
        attrs = _attrs(11.5, 0.95 * alpha, weight=600,
                       color=(theme.ns(theme.BLANC, 0.98 * alpha) if actif
                              else _encre2(0.95 * alpha)))
        w = max(min_w, _text_width(label, attrs) + 26.0)
        h = 26.0
        rect = NSMakeRect(x, y, w, h)
        hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, theme.RAYON_BOUTON, theme.RAYON_BOUTON)
        if actif:
            # #C94E00 porte du blanc à 4,6:1 ; au survol on va vers #BE4400.
            theme.ns(theme.O_PETIT if hovered else theme.O_BOUTON, alpha).setFill()
            path.fill()
        else:
            theme.ns(theme.CARTE if hovered else theme.FOND_PUR, alpha).setFill()
            path.fill()
            _trait(alpha).setStroke()
            path.setLineWidth_(theme.FILET)
            path.stroke()
        _draw_text(label, NSMakeRect(x + 13, y + 5.5, w - 26, h - 10), attrs)
        if action:
            if not getattr(self, "_ghost", False):   # l'état sortant ne doit pas rester cliquable
                self.hit_zones.append((rect, action, payload))
        return w

    @objc.python_method
    def _symbol(self, name, size, alpha):
        """Icône SF Symbol à l'encre du système (cache par nom/taille)."""
        cache = getattr(self, "_sym_cache", None)
        if cache is None:
            cache = self._sym_cache = {}
        key = (name, size)
        if key not in cache:
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
            if img is not None:
                cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, 0.3)
                try:
                    cfg = cfg.configurationByApplyingConfiguration_(
                        NSImageSymbolConfiguration.configurationWithHierarchicalColor_(
                            theme.ns(theme.ENCRE)))
                except Exception:
                    pass
                img = img.imageWithSymbolConfiguration_(cfg)
            cache[key] = img
        return cache[key]

    @objc.python_method
    def _draw_symbol(self, name, x, y, size, alpha):
        img = self._symbol(name, size, alpha)
        if img is None:
            return 0.0
        sz = img.size()
        img.drawInRect_fromRect_operation_fraction_(NSMakeRect(x, y, sz.width, sz.height), NSMakeRect(0, 0, 0, 0),
                                                    NSCompositingOperationSourceOver, alpha)
        return sz.width

    # Il y avait ici une aura par app — violet, vert, bleu, rose, orange — qui
    # montait du bas de chaque carte. Deux règles du système l'excluent : « une
    # seule action colorée par écran », et « pour détacher un élément : une
    # élévation, un filet, une ombre. Jamais une lueur. » Les cartes se
    # distinguent maintenant par leur filet et leur élévation ; l'app est dite
    # en toutes lettres, ce qui est de toute façon plus lisible qu'une teinte
    # qu'il fallait apprendre.
    AURAS = {"default": theme.O_VITRINE}

    @objc.python_method
    def _aura_color(self, app):
        return self.AURAS["default"]

    @objc.python_method
    def _draw_card(self, rect, item, idx, ka, hovered, copied):
        """Carte du système : fond clair, filet 1 px, rien d'autre.

        Le halo radial, la nappe montante et le grain qui étaient ici sont
        exactement ce que le brand book range sous « la profondeur ne vient pas
        d'un aplat de couleur ». Ce qui détache la carte, c'est son filet.
        """
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, theme.RAYON_CARTE, theme.RAYON_CARTE)
        theme.ns(theme.CARTE if hovered else theme.FOND_PUR, ka).setFill()
        path.fill()
        theme.ns(theme.O_PETIT if copied else theme.TRAIT, ka).setStroke()
        path.setLineWidth_(theme.FILET * (2.0 if copied else 1.0))
        path.stroke()

        label = (item.get("app") or "Dictée").upper()
        _draw_text(label, NSMakeRect(rect.origin.x + 16, rect.origin.y + rect.size.height - 30,
                                     rect.size.width - 90, 14),
                   _attrs(10.5, ka, weight=600, color=theme.ns(theme.ENCRE_3, ka)))
        right = "Copié" if copied else item.get("when", "")
        ra = _attrs(10.5, ka, weight=600,
                    color=theme.ns(theme.O_PETIT if copied else theme.ENCRE_3, ka))
        rw = _text_width(right, ra)
        _draw_text(right, NSMakeRect(rect.origin.x + rect.size.width - 16 - rw,
                                     rect.origin.y + rect.size.height - 30, rw + 2, 14), ra)
        _draw_text(item.get("text", ""), NSMakeRect(rect.origin.x + 16, rect.origin.y + 16,
                                                    rect.size.width - 32, 40),
                   _attrs(13.5, ka, color=theme.ns(theme.ENCRE, ka)))
        _draw_text(str(idx + 1), NSMakeRect(rect.origin.x + rect.size.width - 24,
                                            rect.origin.y + 12, 12, 12),
                   _attrs(10, ka, weight=600, color=theme.ns(theme.ENCRE_3, 0.7 * ka)))

    @objc.python_method
    def _draw_tile(self, rect, tile, idx, ka, hovered):
        """Tuile du système : une carte à filet, l'icône et le libellé à l'encre.

        Il y avait ici une aura par tuile — violet, vert, rouge, bleu — un
        dégradé lumineux et un grain. Le système n'a aucune de ces couleurs et
        exclut la lueur. L'état se lit à l'encre : pleine quand c'est actif,
        éteinte sinon. L'orange ne vient que sur ce qui vient d'être fait.
        """
        on = tile.get("on", True)
        ov = self.overlay
        copied = ov.flash_index == idx and time.time() - ov.flash_t0 < 1.2
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, theme.RAYON_CARTE, theme.RAYON_CARTE)
        theme.ns(theme.CARTE if hovered else theme.FOND_PUR, ka).setFill()
        path.fill()
        theme.ns(theme.O_PETIT if copied else (theme.TRAIT_FORT if hovered else theme.TRAIT), ka).setStroke()
        path.setLineWidth_(theme.FILET * (2.0 if copied else 1.0))
        path.stroke()
        w, h = rect.size.width, rect.size.height
        # Icône, centrée dans la moitié haute : c'est elle qui rend les tuiles
        # reconnaissables d'un coup d'œil, et elle porte l'état (allumé/éteint).
        icon = tile.get("icon")
        if icon:
            img = self._symbol(icon, 30, 1.0)
            if img is not None:
                sz = img.size()
                self._draw_symbol(icon,
                                  rect.origin.x + (w - sz.width) / 2.0,
                                  rect.origin.y + h * 0.55 - sz.height / 2.0,
                                  30, (0.95 if on else 0.30) * ka)
        # numéro
        _draw_text(str(idx + 1), NSMakeRect(rect.origin.x + w - 26, rect.origin.y + h - 30, 12, 12),
                   _attrs(10.5, ka, weight=600, color=theme.ns(theme.ENCRE_3, 0.7 * ka)))
        # libellé + état
        _draw_text(tile["title"].upper(), NSMakeRect(rect.origin.x + 18, rect.origin.y + 40, w - 36, 20),
                   _attrs(15, ka, weight=600, color=theme.ns(theme.ENCRE if on else theme.ENCRE_3, ka)))
        sub = "Copié" if copied else tile.get("subtitle", "")
        _draw_text(sub, NSMakeRect(rect.origin.x + 18, rect.origin.y + 20, w - 36, 16),
                   _attrs(11.5, ka, color=theme.ns(theme.O_PETIT if copied else theme.ENCRE_3, ka)))

    @objc.python_method
    def _draw_panel(self, panel, k):
        ov = self.overlay
        data = ov.data()
        px, py, pw, ph = panel.origin.x, panel.origin.y, panel.size.width, panel.size.height
        M = 24.0
        x0 = px + M
        inner_w = pw - 2 * M
        top = py + ph

        # Le verrou de marque : le logotype AUR'IA, puis FLOW à la même hauteur de
        # capitale. « Le mot » est la forme par défaut d'un en-tête ; le produit
        # dérivé s'accroche derrière plutôt que de refaire un dessin.
        lw = self._draw_lockup(x0, top - M - 16, 15.0, k)
        status = data.get("status", "")
        if status:
            sx = x0 + lw + 12
            _draw_text(status, NSMakeRect(sx, top - M - 17, inner_w - (sx - x0) - 60, 18),
                       _attrs(11.5, 0.42 * k))
        _draw_text(data.get("stats_line", ""), NSMakeRect(x0, top - M - 36, inner_w - 80, 14), _attrs(11, 0.55 * k))

        tiles = data.get("tiles", [])
        gap = 14.0
        n = max(1, len(tiles))
        tw = (inner_w - gap * (n - 1)) / n
        th = ph - M - 56.0 - 40.0
        ty = py + 36.0
        for i, tile in enumerate(tiles):
            ka = max(0.0, min(1.0, (k - 0.10 * i) / 0.6))
            # Les tuiles ne faisaient que se fondre, en décalé. Elles montent aussi
            # maintenant : 22 px en ease-out, ce qui donne au panneau une direction —
            # un fondu seul se lit comme une image qui charge, pas comme une ouverture.
            dy = (1.0 - (1.0 - (1.0 - ka) ** 3)) * 22.0
            rect = NSMakeRect(x0 + i * (tw + gap), ty - dy, tw, th)
            hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
            self._draw_tile(rect, tile, i, ka, hovered)
            if not getattr(self, "_ghost", False):
                # zone cliquable posée à l'arrivée, pas sur la position transitoire
                self.hit_zones.append((NSMakeRect(x0 + i * (tw + gap), ty, tw, th),
                                       tile["action"], tile.get("payload")))

        hint = "1–4   ·   esc"
        ha = _attrs(10.5, 0.28 * k, weight=0.4)
        hw = _text_width(hint, ha)
        _draw_text(hint, NSMakeRect(px + pw - M - hw, py + 12, hw + 2, 14), ha)

class Overlay:
    """Fenêtre sans bordure, non-activante, sur tous les Spaces."""

    def __init__(self, level_source, data_source=None, on_action=None):
        self._level_source = level_source
        self._data_source = data_source or (lambda: {})
        self._on_action = on_action or (lambda a, p: None)
        self._timer = None
        self._t0 = time.time()
        self._click_monitor = None

        self.state = "idle"
        self.base_state = "idle"        # "meeting" pendant une réunion : état de repos
        self.meeting_info = lambda: {}  # fourni par l'app : {"clock", "sys_level", "offer"}
        self.calendar_preview = None    # {"label", "until"} pendant l'aperçu agenda
        self.phase = 0.0
        self.level = 0.0
        self.bars = [0.0] * BAR_COUNT
        self.content_alpha = 1.0
        self.flash_index = -1
        self.flash_t0 = 0.0
        self._data_cache = None
        self._data_cache_t = 0.0

        # fondu croisé entre deux états (voir drawRect_)
        self.prev_state = None
        self.fade_out = 0.0
        self._reduced = _reduce_motion()

        # enregistrement : historique de niveau (forme d'onde) + chrono
        self.wave = [0.0] * WAVE_COUNT
        self._wave_tick = 0
        self.rec_t0 = 0.0
        self.rec_hands_free = False
        self.rec_calendar = False   # point orange : cette dictée part dans l'agenda
        self.hf_t0 = 0.0

        # progression de la transcription (voir begin_progress)
        self.progress_t0 = 0.0
        self.progress_expected = 1.0
        self.progress_p = 0.0        # valeur lissée, celle qui est dessinée
        self.progress_done = False
        self.done_t0 = 0.0           # instant d'arrivée du texte (éclat de fin)

        self.cur_w, self.cur_h = IDLE_W, IDLE_H
        self.cur_margin = MARGINS["idle"]
        self._from = (IDLE_W, IDLE_H, self.cur_margin)
        self._to = (IDLE_W, IDLE_H, self.cur_margin)
        self._anim_t0 = None

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._frame_rect(),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setHasShadow_(False)
        self.panel.setIgnoresMouseEvents_(True)   # transparent à la souris sauf panneau ouvert
        self.panel.setAcceptsMouseMovedEvents_(True)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        )
        # Matériau de fond. Le fond était un aplat (blanc 0,05 à 97 %) : sur une fenêtre
        # claire, la bande faisait un rectangle noir posé là.
        # 1er choix : NSGlassEffectView — le Liquid Glass natif de macOS 26. Ce n'est pas
        #   une imitation : réfraction, reflet spéculaire sur les bords et ombre interne
        #   sont calculés par le système, et composés par le WindowServer (CPU nul).
        # 2e choix : NSVisualEffectView (flou HUD) sur macOS 15 et antérieurs.
        # 3e choix : l'ancien aplat opaque.
        # Le matériau doit être SOUS la vue de dessin : en AppKit une sous-vue se dessine
        # par-dessus le drawRect_ de son parent, d'où le conteneur.
        container = NSView.alloc().initWithFrame_(self._view_rect())
        self.blur = None
        self.glass = None
        try:
            if not USE_MATERIAL:
                # La DA demande l'encre chaude de la marque : ni verre ni flou ne
                # peuvent la porter, ils fournissent leur propre fond. On peint
                # la pilule nous-mêmes (voir drawRect_).
                raise RuntimeError("matériau désactivé par la DA")
            import objc as _objc
            Glass = _objc.lookUpClass("NSGlassEffectView")
            g = Glass.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
            try:
                # « Clear » plutôt que « Regular » : nettement plus transparent, donc
                # la réfraction du fond se voit vraiment. Regular est un verre dépoli,
                # Clear est du verre.
                from AppKit import NSGlassEffectViewStyleClear
                g.setStyle_(NSGlassEffectViewStyleClear)
            except Exception:
                pass
            try:
                g.set_contentLensing_(True)
            except Exception:
                pass
            # NB : ne PAS appeler set_variant_ — il partage son stockage avec `style`
            # et remet silencieusement le verre en « Regular » (vérifié à l'exécution).
            # PAS de teinte. Un violet sombre à 20 % ne se lit pas comme une couleur sur
            # fond noir : il se lit comme un voile gris, et c'est lui qui salissait tout.
            # Le verre non teinté laisse passer ce qu'il y a derrière — c'est le but.
            # NSGlassEffectView habille sa VUE DE CONTENU : sans contentView il ne rend
            # rien. On lui en donne une, transparente — le dessin réel reste au-dessus,
            # ce qui préserve le halo qui déborde de la pilule pendant l'enregistrement.
            container.addSubview_(g)
            self.glass = g
        except Exception:
            try:
                if not USE_MATERIAL:
                    raise RuntimeError("matériau désactivé par la DA")
                from AppKit import (NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
                                    NSVisualEffectStateActive, NSVisualEffectMaterialHUDWindow)
                blur = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
                blur.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                blur.setMaterial_(NSVisualEffectMaterialHUDWindow)
                blur.setState_(NSVisualEffectStateActive)
                blur.setWantsLayer_(True)
                blur.layer().setMasksToBounds_(True)
                container.addSubview_(blur)
                self.blur = blur
            except Exception:
                pass    # ni verre ni flou : le fond opaque ci-dessous suffit

        self.container = container
        self.view = _BandView.alloc().initWithFrame_(self._view_rect())
        self.view.overlay = self
        if self.glass is not None:
            # Le contenu va DANS le verre : c'est la seule façon d'être réfracté.
            # En frère posé au-dessus (ce que je faisais), AppKit ne garantit rien —
            # le verre habille sa contentView, point.
            self.view.in_glass = True
            self.glass.setContentView_(self.view)
        else:
            container.addSubview_(self.view)
        self.panel.setContentView_(container)
        self.panel.orderFrontRegardless()
        self._sync_blur()
        self._ensure_timer()

    def _sync_blur(self):
        """Cale le matériau de fond sur la pilule courante (taille interpolée incluse).

        Appelé à chaque frame d'animation : le verre suit le ressort, dépassement et
        rayon d'angle compris, sinon on verrait le contenu se déformer hors du verre.
        """
        cw, ch = self.cur_w, self.cur_h
        b = self.container.bounds()
        rect = NSMakeRect((b.size.width - cw) / 2.0, PAD, cw, ch)
        radius = theme.RAYON_CARTE if ch > 60.0 else theme.RAYON_FLOTTANT
        if self.glass is not None:
            self.glass.setFrame_(rect)
            try:
                self.glass.setCornerRadius_(radius)
            except Exception:
                pass
            # La vue de contenu ne suit pas le verre toute seule : sans ça elle reste à
            # sa taille d'origine et le verre ne s'applique que sur ce carré-là.
            cv = self.glass.contentView()
            if cv is not None:
                cv.setFrame_(NSMakeRect(0, 0, cw, ch))
        elif self.blur is not None:
            self.blur.setFrame_(rect)
            self.blur.layer().setCornerRadius_(radius)

    # ---- géométrie ----

    def _view_rect(self):
        return NSMakeRect(0, 0, PANEL_W + 2 * PAD, PANEL_H + 2 * PAD)

    def _frame_rect(self):
        screen = NSScreen.mainScreen()
        vf = screen.visibleFrame() if screen else NSMakeRect(0, 0, 1440, 900)
        w, h = PANEL_W + 2 * PAD, PANEL_H + 2 * PAD
        x = vf.origin.x + (vf.size.width - w) / 2.0
        y = vf.origin.y + self.cur_margin - PAD
        return NSMakeRect(x, y, w, h)

    # ---- enregistrement ----

    def begin_recording(self, hands_free=False, calendar=False):
        self.hf_t0 = 0.0
        """Remet la forme d'onde à plat et démarre le chrono."""
        self.wave = [0.0] * WAVE_COUNT
        self._wave_tick = 0
        self.rec_t0 = time.time()
        self.rec_hands_free = bool(hands_free)
        self.rec_calendar = bool(calendar)

    # ---- progression de la transcription ----

    def begin_progress(self, expected_s):
        """Démarre la barre. `expected_s` = durée de décodage estimée (voir Learner
        côté app : elle est apprise sur les dictées précédentes, pas devinée)."""
        self.progress_t0 = time.time()
        self.progress_expected = max(0.25, float(expected_s))
        self.progress_p = 0.0
        self.progress_done = False

    def end_progress(self):
        """Le texte est là : la barre file vers 100 % au lieu d'être coupée net."""
        self.progress_done = True
        self.done_t0 = time.time()

    def _progress_target(self, now):
        """Cible instantanée, avant lissage.

        1 - exp(-2.3·t) où t = écoulé / estimé : la courbe atteint 90 % à l'instant
        estimé et continue de ramper (99 % au double) sans jamais toucher 100 %.
        Une barre qui plafonne à 100 % alors que ça calcule encore est pire que pas
        de barre du tout — ici le dépassement d'estimation reste lisible.
        """
        if self.progress_done:
            return 1.0
        t = (now - self.progress_t0) / self.progress_expected
        return 1.0 - math.exp(-2.3 * max(0.0, t))

    def _target_size(self, state):
        w, h = {
            "idle": (IDLE_W, IDLE_H),
            "hover": (HOVER_W, HOVER_H),
            "recording": (PILL_W, PILL_H),
            "processing": (PROC_W, PROC_H),
            "expanded": (PANEL_W, PANEL_H),
            "meeting": (MEET_W, MEET_H),
            "meeting_offer": (OFFER_W, OFFER_H),
            "calendar_preview": (CAL_W, CAL_H),
        }[state]
        return (w, h, MARGINS[state])

    # ---- états ----

    def _set_state(self, state):
        if state == self.state:
            return
        self.prev_state = self.state
        self.fade_out = 1.0
        self._reduced = _reduce_motion()
        self.state = state
        self._from = (self.cur_w, self.cur_h, self.cur_margin)
        self._to = self._target_size(state)
        self._anim_t0 = time.time()
        self.content_alpha = 0.0
        if state == "recording":
            self.bars = [0.0] * BAR_COUNT
        self.panel.setIgnoresMouseEvents_(state not in ("expanded", "meeting_offer"))
        self.panel.setFrame_display_(self._frame_rect(), True)
        self.panel.orderFrontRegardless()
        if state == "expanded":
            self._install_click_monitor()
            self._data_cache = None
        else:
            self._remove_click_monitor()
        self.view.hit_zones = []
        self._ensure_timer()

    def show(self, mode):
        """API app : 'recording' ou 'processing'."""
        try:
            self._set_state(mode)
        except Exception:
            import traceback
            traceback.print_exc()

    def hide(self):
        """API app : fin de dictée → retour à l'état de repos (barre réduite, ou réunion en cours)."""
        try:
            self._set_state(self.base_state)
        except Exception:
            import traceback
            traceback.print_exc()

    def set_meeting(self, active):
        """Réunion en cours : la bande affiche le chrono au repos."""
        self.base_state = "meeting" if active else "idle"
        if self.state in ("idle", "meeting", "meeting_offer", "hover"):
            self._set_state(self.base_state)

    def offer_meeting(self):
        if self.state in ("idle", "hover", "meeting"):
            self._set_state("meeting_offer")

    def set_text(self, text):
        """Conservé pour compatibilité : plus de texte en direct."""

    def begin_calendar_preview(self, label, seconds):
        """API app : montre l'événement compris et le temps qu'il reste pour Esc."""
        self.calendar_preview = {"label": label, "until": time.time() + seconds}
        self._set_state("calendar_preview")

    def end_calendar_preview(self):
        self.calendar_preview = None
        if self.state == "calendar_preview":
            self._set_state(self.base_state)

    def toggle_expanded(self):
        self._set_state(self.base_state if self.state == "expanded" else "expanded")

    def _on_hover(self, inside):
        pass  # le survol ne fait rien : la bande ne doit jamais gêner le Dock

    def _on_click(self):
        pass  # ouverture uniquement par double-tap fn (ou le menu)

    def _action(self, action, payload):
        if action == "copy":
            self.flash_index = payload
            self.flash_t0 = time.time()
        self._on_action(action, payload)
        self._data_cache = None
        self.view.setNeedsDisplay_(True)

    def refresh(self):
        self._data_cache = None
        self.view.setNeedsDisplay_(True)

    def data(self):
        now = time.time()
        if self._data_cache is None or now - self._data_cache_t > 2.0:
            try:
                self._data_cache = self._data_source() or {}
            except Exception:
                self._data_cache = {}
            self._data_cache_t = now
        return self._data_cache

    # ---- clic hors du panneau → repli ----

    def _install_click_monitor(self):
        if self._click_monitor is not None:
            return

        def handler(event):
            try:
                if event.window() is not self.panel:
                    self._set_state(self.base_state)
            except Exception:
                pass

        self._click_handler = handler
        self._click_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown, handler
        )

    def _remove_click_monitor(self):
        if self._click_monitor is not None:
            NSEvent.removeMonitor_(self._click_monitor)
            self._click_monitor = None

    # ---- boucle d'animation ----

    def _ensure_timer(self):
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / FPS, True, self._tick)

    def _tick(self, timer):
        now = time.time()
        self.phase = now - self._t0
        animating = False
        if self._anim_t0 is not None:
            reduced = getattr(self, "_reduced", False)
            k = min(1.0, (now - self._anim_t0) / (ANIM_S_REDUCED if reduced else ANIM_S))
            e = _ease(k, reduced)
            self.cur_w = _lerp(self._from[0], self._to[0], e)
            self.cur_h = _lerp(self._from[1], self._to[1], e)
            self.cur_margin = _lerp(self._from[2], self._to[2], e)
            self.panel.setFrame_display_(self._frame_rect(), False)
            self._sync_blur()
            # Fondu croisé à somme constante. Le contenu attendait 35 % de l'animation
            # avant d'apparaître : ça se lisait comme de la latence. Et faire décroître
            # le sortant indépendamment du montant creusait la luminosité au milieu
            # (somme mesurée à 0,62). Ici sortant + entrant = 1 à chaque frame.
            # Lissé en smoothstep : une rampe linéaire fait « claquer » les extrémités.
            x = min(1.0, max(0.0, k / 0.28))
            s = x * x * (3.0 - 2.0 * x)
            self.content_alpha = s
            self.fade_out = 1.0 - s
            animating = k < 1.0
            if not animating:
                self._anim_t0 = None
                self.content_alpha = 1.0
                self.fade_out = 0.0
                self.prev_state = None
                self.view.updateTrackingAreas()
        if self.state == "processing":
            # Lissage exponentiel vers la cible : la cible ne recule jamais, donc la
            # barre non plus. Rattrapage plus vif une fois le texte arrivé, pour que
            # la fin se sente franche au lieu de traîner.
            target = self._progress_target(now)
            self.progress_p += (target - self.progress_p) * (0.40 if self.progress_done else 0.16)
        if self.state in ("recording", "meeting"):
            try:
                lv = float(self._level_source())
            except Exception:
                lv = 0.0
            # Lissage asymétrique : attaque quasi instantanée, retombée douce. C'est ce que
            # font les vu-mètres audio, et c'est ce qui rend le niveau « vivant » — un
            # lissage symétrique rate les attaques et donne un mètre pâteux.
            self.level = (0.75 * lv + 0.25 * self.level) if lv > self.level else (0.15 * lv + 0.85 * self.level)
            bars = self.bars
            mid = BAR_COUNT // 2
            new = list(bars)
            new[mid] = (0.8 * lv + 0.2 * bars[mid]) if lv > bars[mid] else (0.2 * lv + 0.8 * bars[mid])
            for j in range(1, mid + 1):
                new[mid - j] = 0.7 * bars[mid - j + 1] + 0.3 * bars[mid - j]
                new[mid + j] = 0.7 * bars[mid + j - 1] + 0.3 * bars[mid + j]
            self.bars = new
            # Forme d'onde défilante : on empile le niveau à 20 Hz (1 tick sur 3), soit
            # ~1,3 s d'historique visible. À 60 Hz elle défilerait trop vite pour être lue.
            self._wave_tick += 1
            if self._wave_tick % 3 == 0:
                self.wave = self.wave[1:] + [self.level]
        # au repos, on redessine juste assez pour la respiration (économie CPU)
        if self.state in ("idle", "meeting") and not animating and int(self.phase * FPS) % (4 if self.state == "idle" else 3) != 0:
            return
        self.view.setNeedsDisplay_(True)
