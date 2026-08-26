"""Bande flottante en bas de l'écran — un seul panneau, plusieurs états :

- idle      : petite barre sombre (l'app est allumée), survol = s'éclaire
- expanded  : panneau « bulles » (historique cliquable, stats, réglages, actions)
- recording : pilule avec halo violet + barres blanches qui suivent la voix
- processing: pilule élargie, halo rapide, barre de progression 0→100 %

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
    NSFont,
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

# ---- géométrie (tailles du contenu, hors marge d'ombre) ----
PAD = 26.0                      # marge autour pour l'ombre et le halo
IDLE_W, IDLE_H = 76.0, 8.0
HOVER_W, HOVER_H = 168.0, 26.0  # au survol on affiche vraiment quoi faire, d'où la place
PILL_W, PILL_H = 184.0, 36.0
PROC_W, PROC_H = 184.0, 36.0    # même largeur que l'enregistrement : pas d'à-coup entre les deux
WAVE_COUNT, WAVE_W, WAVE_GAP = 20, 2.5, 2.6   # mini-forme d'onde défilante
PANEL_W, PANEL_H = 720.0, 292.0
MEET_W, MEET_H = 118.0, 22.0          # réunion en cours : point rouge + chrono
OFFER_W, OFFER_H = 452.0, 44.0        # « Appel X détecté » + [Enregistrer] [Ignorer]
MARGINS = {"idle": 14.0, "hover": 14.0, "recording": 18.0, "processing": 18.0, "expanded": 44.0,
           "meeting": 14.0, "meeting_offer": 18.0}
RED = (1.0, 0.30, 0.32)
FPS = 60.0
ANIM_S = 0.40          # plus long qu'avant, mais 90 % du mouvement tient dans les 120 premières ms
SPRING_OMEGA = 12.0    # raideur : plus haut = démarrage plus sec
SPRING_ZETA = 0.62     # amortissement : ~8 % de dépassement, un seul rebond
ANIM_S_REDUCED = 0.16  # « Réduire les animations » : court et sans rebond
BAR_COUNT, BAR_W, BAR_GAP = 5, 3.0, 4.0
SEGMENTS = 140
VIOLET = (0.66, 0.40, 1.00)

def _violet(a):
    r, g, b = VIOLET
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

def _white(a):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a)

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
    """Ressort amorti, pas un ease-out.

    Un ease-out cubique arrive à destination en ralentissant : correct, mais mou.
    Un ressort part sec, dépasse la cible d'environ 8 %, puis se recale — c'est ce
    dépassement que l'œil lit comme « physique » (Dynamic Island, iOS).
    Solution analytique de l'oscillateur amorti, donc aucun état à maintenir.
    """
    if k >= 1.0:
        return 1.0
    if reduced:
        return 1.0 - (1.0 - k) ** 3     # ease-out franc, sans dépassement
    wd = SPRING_OMEGA * math.sqrt(1.0 - SPRING_ZETA * SPRING_ZETA)
    decay = math.exp(-SPRING_ZETA * SPRING_OMEGA * k)
    return 1.0 - decay * (math.cos(wd * k) + (SPRING_ZETA * SPRING_OMEGA / wd) * math.sin(wd * k))

def _lerp(a, b, k):
    return a + (b - a) * k

def _attrs(size, alpha=0.92, weight=None, truncate=True):
    font = NSFont.systemFontOfSize_weight_(size, weight) if weight is not None else NSFont.systemFontOfSize_(size)
    ps = NSMutableParagraphStyle.alloc().init()
    if truncate:
        ps.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: _white(alpha),
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
        ov = self.overlay
        if ov is None:
            return
        b = self.bounds()
        cw, ch = ov.cur_w, ov.cur_h
        radius = min(ch / 2.0, 22.0)
        if self.in_glass:
            # On EST la contentView du verre : nos bounds sont déjà la pilule, et c'est
            # le verre qui fournit fond, arête et ombre. On ne dessine que le contenu.
            content = NSMakeRect(0, 0, b.size.width, b.size.height)
        else:
            # rectangle de contenu courant (interpolé), centré en bas
            content = NSMakeRect((b.size.width - cw) / 2.0, PAD, cw, ch)
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(content, radius, radius)

        if not self.in_glass:
            # ombre douce (plusieurs couches décalées vers le bas)
            for i, (dy, grow, a) in enumerate(((-3, 10, 0.10), (-2, 5, 0.14), (-1, 2, 0.18))):
                sr = NSMakeRect(content.origin.x - grow, content.origin.y - grow + dy, cw + 2 * grow, ch + 2 * grow)
                NSColor.colorWithCalibratedWhite_alpha_(0.0, a).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(sr, radius + grow, radius + grow).fill()
            NSColor.colorWithCalibratedWhite_alpha_(0.05, 0.62 if ov.blur is not None else 0.97).setFill()
            body.fill()
            NSColor.colorWithCalibratedWhite_alpha_(
                1.0, 0.10 if ov.state in ("idle",) else 0.14).setStroke()
            body.setLineWidth_(1.0)
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
            self._draw_glow(content, st)
            self._draw_progress(content, k)
        elif st == "recording":
            self._draw_glow(content, st)
            self._draw_recording(content, k)
        elif st == "expanded":
            self._draw_panel(content, k)
        elif st == "meeting":
            self._draw_meeting(content, k)
        elif st == "meeting_offer":
            self._draw_offer(content, k)
        elif st == "hover":
            self._draw_idle(content, hover=True, k=k)
        else:
            self._draw_idle(content, hover=False, k=k)

    @objc.python_method
    def _draw_specular(self, pill, radius):
        """Reflet spéculaire qui circule sur l'arête du verre.

        Le verre natif gère la réfraction du fond, mais son reflet est statique.
        Ce qui fait lire « liquide » plutôt que « plastique », c'est un point brillant
        qui se déplace : l'œil en déduit une surface courbe et mobile.
        On réutilise `_perimeter_point`, déjà écrit pour l'orbe.
        """
        ov = self.overlay
        # Arête permanente. Sur fond sombre, le remplissage d'un verre ne se voit pas —
        # c'est le liseré lumineux qui dit « il y a une surface ici ». Sans lui, la
        # pilule se lit comme un aplat gris.
        rim = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(pill.origin.x + 0.5, pill.origin.y + 0.5,
                       pill.size.width - 1.0, pill.size.height - 1.0), radius, radius)
        rim.setLineWidth_(1.0)
        _white(0.22).setStroke()
        rim.stroke()

        # Reflets mobiles : deux arcs opposés qui circulent. Le verre natif a une arête,
        # mais son reflet est fixe — c'est le point brillant QUI BOUGE qui fait lire
        # « liquide » plutôt que « plastique ».
        # Uniquement pendant une action : au repos, un point qui tourne en boucle sur une
        # barre de 8 px n'évoque rien, il attire juste l'œil pour rien.
        if ov.state in ("idle", "hover", "meeting"):
            return
        t0 = (ov.phase * 0.11) % 1.0
        n = 30
        for offset, span, size, alpha in ((0.0, 0.18, 4.2, 0.62), (0.5, 0.12, 2.6, 0.26)):
            for i in range(n):
                f = i / (n - 1.0)
                x, y = _perimeter_point(t0 + offset + f * span, pill)
                edge = math.sin(math.pi * f)
                d = size * edge
                if d < 0.3:
                    continue
                _white(alpha * edge * edge).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(x - d / 2, y - d / 2, d, d)).fill()

    @objc.python_method
    def _draw_idle(self, pill, hover, k=1.0):
        ov = self.overlay
        cx = pill.origin.x + pill.size.width / 2.0
        cy = pill.origin.y + pill.size.height / 2.0
        pulse = 0.5 + 0.5 * math.sin(ov.phase * 1.6)
        if hover:
            # Le survol se contentait de passer l'opacité de 0,35 à 0,55 et le point de
            # 3 à 4 px : indiscernable du repos, et donc aucun indice que c'est cliquable.
            # On dit maintenant explicitement quoi faire.
            dot = 5.0
            _violet((0.85 + 0.15 * pulse) * k).setFill()
            hint = "maintenir fn  ·  double-tap"
            ha = _attrs(11.0, 0.80 * k, weight=0.4, truncate=False)
            hw = _text_width(hint, ha)
            gap = 9.0
            x = cx - (dot + gap + hw) / 2.0
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, cy - dot / 2, dot, dot)).fill()
            _draw_text(hint, NSMakeRect(x + dot + gap, cy - 8.0, hw + 2, 16), ha)
            return
        # au repos : point violet qui « respire » doucement, l'app est prête
        a = (0.35 + 0.25 * pulse) * k
        d = 3.0
        _violet(a).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - d / 2, cy - d / 2, d, d)).fill()
        _white(0.10 * k).setFill()
        lw = pill.size.width * 0.42
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(cx - lw / 2, cy - 0.75, lw, 1.5), 0.75, 0.75).fill()

    @objc.python_method
    def _draw_meeting(self, pill, k):
        """Réunion en cours : point rouge qui pulse, chrono, mini-barres du micro."""
        ov = self.overlay
        info = ov.meeting_info() or {}
        r, g, b = RED
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
            _white((0.55 + 0.4 * lv) * k).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x0 + i * 6, cy - h / 2, 3, h), 1.5, 1.5).fill()

    @objc.python_method
    def _draw_offer(self, pill, k):
        """Proposition : « Réunion Zoom détectée — enregistrer ? [Oui] [Non] »."""
        ov = self.overlay
        info = ov.meeting_info() or {}
        r, g, b = RED
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
    def _draw_glow(self, pill, st):
        ov = self.overlay
        speed = 0.45 if st == "recording" else 1.3
        head = (ov.phase * speed) % 1.0
        energy = 0.45 + 0.55 * min(1.0, ov.level * 1.8)
        if st == "processing":
            energy = 0.75 + 0.25 * math.sin(ov.phase * 5.0)
        spread = 0.045 + 0.035 * energy
        base = 0.035
        for width, amax in ((12.0, 0.05), (5.0, 0.14), (2.2, 0.45), (1.0, 1.0)):
            for i in range(SEGMENTS):
                t0 = i / SEGMENTS
                t1 = (i + 1.2) / SEGMENTS
                d1 = abs(t0 - head) % 1.0
                d1 = min(d1, 1 - d1)
                d2 = abs(t0 - head - 0.5) % 1.0
                d2 = min(d2, 1 - d2)
                glow = math.exp(-(d1 / spread) ** 2) + 0.8 * math.exp(-(d2 / spread) ** 2)
                a = amax * energy * min(1.0, base + glow) * ov.content_alpha
                if a < 0.01:
                    continue
                p = NSBezierPath.bezierPath()
                p.setLineWidth_(width)
                p.setLineCapStyle_(1)
                p.moveToPoint_(_perimeter_point(t0, pill))
                p.lineToPoint_(_perimeter_point(t1, pill))
                _violet(a).setStroke()
                p.stroke()

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
        cy = py + ph / 2.0
        max_h = ph - 14.0

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
            _white(a).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, WAVE_W / 2, WAVE_W / 2).fill()

        elapsed = max(0.0, time.time() - ov.rec_t0) if ov.rec_t0 else 0.0
        clock = f"{int(elapsed) // 60}:{int(elapsed) % 60:02d}"
        # Mains libres : chrono violet — le seul rappel visuel que ça continue sans toi.
        ta = _attrs(11.5, 0.90 * k, weight=0.45, truncate=False)
        if ov.rec_hands_free:
            ta = dict(ta)
            ta[NSForegroundColorAttributeName] = _violet(0.95 * k)
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
        _white(0.13 * k).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(track, track_h / 2, track_h / 2).fill()

        fill_w = max(track_h, track_w * p)   # jamais plus fin que son propre arrondi
        if p > 0.0:
            from AppKit import NSGraphicsContext
            fill = NSMakeRect(track_x, track_y, fill_w, track_h)
            fpath = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill, track_h / 2, track_h / 2)
            ctx = NSGraphicsContext.currentContext()
            ctx.saveGraphicsState()
            fpath.addClip()
            r, g, b = VIOLET
            grad = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                [NSColor.colorWithCalibratedRed_green_blue_alpha_(r * 0.75, g * 0.75, 1.0, 0.95 * k),
                 NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.95 * k)],
                [0.0, 1.0], NSColorSpace.sRGBColorSpace())
            grad.drawInRect_angle_(fill, 0.0)
            # Reflet qui balaie la partie remplie : garde la barre vivante quand la
            # progression est lente, sans jamais laisser croire qu'elle avance.
            if not ov.progress_done:
                sheen_x = track_x + (fill_w + 60.0) * ((ov.phase * 0.65) % 1.0) - 30.0
                sh = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                    [_white(0.0), _white(0.30 * k), _white(0.0)], [0.0, 0.5, 1.0], NSColorSpace.sRGBColorSpace())
                sh.drawInRect_angle_(NSMakeRect(sheen_x, track_y, 60.0, track_h), 0.0)
            ctx.restoreGraphicsState()
            # pointe lumineuse en bout de barre
            _white(0.55 * k).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(track_x + fill_w - track_h, track_y, track_h, track_h)).fill()
            # Éclat d'arrivée : la barre atteignait 100 % puis disparaissait, sans rien
            # marquer. Un halo blanc qui se dilate et s'éteint en 350 ms donne au geste
            # une fin nette — c'est le seul retour visuel que le texte est posé.
            dt = time.time() - ov.done_t0
            if ov.done_t0 and dt < 0.35:
                e = 1.0 - (1.0 - dt / 0.35) ** 2      # ease-out
                grow = 1.0 + 5.0 * e
                _white(0.45 * (1.0 - e) * k).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(track_x - grow, track_y - grow,
                               fill_w + 2 * grow, track_h + 2 * grow),
                    (track_h + 2 * grow) / 2, (track_h + 2 * grow) / 2).fill()

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
            _white(a * k).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, BAR_W / 2, BAR_W / 2).fill()

    # ---- panneau déplié ----

    @objc.python_method
    def _chip(self, x, y, label, on=None, action=None, payload=None, alpha=1.0, min_w=0.0):
        """Petite pilule cliquable. on=None : neutre ; True/False : interrupteur."""
        attrs = _attrs(11.5, 0.92 * alpha, weight=0.3)
        w = max(min_w, _text_width(label, attrs) + 22.0)
        h = 24.0
        rect = NSMakeRect(x, y, w, h)
        hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
        if on is True:
            _violet((0.28 if not hovered else 0.38) * alpha).setFill()
        else:
            _white((0.07 if not hovered else 0.13) * alpha).setFill()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, h / 2, h / 2)
        path.fill()
        if on is True:
            _violet(0.9 * alpha).setStroke()
            path.setLineWidth_(1.0)
            path.stroke()
        _draw_text(label, NSMakeRect(x + 11, y + 4.5, w - 22, h - 8), attrs)
        if action:
            if not getattr(self, "_ghost", False):   # l'état sortant ne doit pas rester cliquable
                self.hit_zones.append((rect, action, payload))
        return w

    @objc.python_method
    def _symbol(self, name, size, alpha):
        """Icône SF Symbol blanche (cache par nom/taille)."""
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
                        NSImageSymbolConfiguration.configurationWithHierarchicalColor_(NSColor.whiteColor()))
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

    # ---- palette d'auras par app (RGB 0-1) ----
    AURAS = {
        "default": (0.55, 0.40, 1.00),   # violet
        "slack": (0.35, 0.95, 0.55),     # vert
        "whatsapp": (0.35, 0.95, 0.55),
        "mail": (0.35, 0.70, 1.00),      # bleu
        "gmail": (0.35, 0.70, 1.00),
        "notes": (1.00, 0.62, 0.30),     # orange
        "notion": (0.95, 0.45, 0.85),    # rose
        "messages": (0.35, 0.95, 0.55),
        "ghostty": (0.55, 0.40, 1.00),
        "code": (0.35, 0.70, 1.00),
    }

    @objc.python_method
    def _aura_color(self, app):
        a = (app or "").lower()
        for key, rgb in self.AURAS.items():
            if key != "default" and key in a:
                return rgb
        return self.AURAS["default"]

    @objc.python_method
    def _draw_card(self, rect, item, idx, ka, hovered, copied):
        """Carte noire à coins ronds avec une aura colorée qui monte du bas."""
        from AppKit import NSGraphicsContext
        r, g, b = self._aura_color(item.get("app", ""))
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 18, 18)
        NSColor.colorWithCalibratedWhite_alpha_(0.055 if not hovered else 0.08, 1.0 * ka).setFill()
        path.fill()
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        path.addClip()
        # aura : halo radial centré en bas, + nappe horizontale
        strength = (0.95 if hovered else 0.75) * ka
        c = lambda a: NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)
        # halo radial : centre sous le bord bas, décroissance douce (paliers eased)
        stops = [c(0.90 * strength), c(0.55 * strength), c(0.28 * strength), c(0.12 * strength), c(0.04 * strength), c(0.0)]
        glow = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
            stops, [0.0, 0.18, 0.38, 0.6, 0.82, 1.0], NSColorSpace.sRGBColorSpace())
        w, h = rect.size.width, rect.size.height
        gr = NSMakeRect(rect.origin.x - w * 0.15, rect.origin.y - h * 1.05, w * 1.3, h * 2.1)
        glow.drawInRect_relativeCenterPosition_(gr, (0.0, 0.0))
        # nappe basse très douce (lueur qui « monte »)
        band = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
            [c(0.45 * strength), c(0.18 * strength), c(0.05 * strength), c(0.0)], [0.0, 0.35, 0.7, 1.0], NSColorSpace.sRGBColorSpace())
        band.drawInRect_angle_(NSMakeRect(rect.origin.x, rect.origin.y, w, h * 0.8), 90.0)
        # grain léger (points fixes, déterministes)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.05 * ka).setFill()
        seed = int(rect.origin.x * 7 + rect.origin.y * 13)
        for n in range(40):
            gx = rect.origin.x + ((seed * 31 + n * 97) % int(rect.size.width))
            gy = rect.origin.y + ((seed * 17 + n * 53) % int(rect.size.height * 0.6))
            NSBezierPath.fillRect_(NSMakeRect(gx, gy, 1, 1))
        ctx.restoreGraphicsState()
        # liseré
        NSColor.colorWithCalibratedWhite_alpha_(1.0, (0.10 if not hovered else 0.18) * ka).setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        if copied:
            c(0.95 * ka).setStroke()
            path.stroke()
        # contenu
        label = (item.get("app") or "Dictée").upper()
        la = _attrs(11, 0.55 * ka, weight=0.5)
        _draw_text(label, NSMakeRect(rect.origin.x + 16, rect.origin.y + rect.size.height - 30, rect.size.width - 90, 14), la)
        right = "Copié" if copied else item.get("when", "")
        ra = _attrs(11, (0.95 if copied else 0.45) * ka, weight=0.5)
        if copied:
            ra = dict(ra); ra[NSForegroundColorAttributeName] = c(0.95 * ka)
        rw = _text_width(right, ra)
        _draw_text(right, NSMakeRect(rect.origin.x + rect.size.width - 16 - rw, rect.origin.y + rect.size.height - 30, rw + 2, 14), ra)
        ta = _attrs(13.5, 0.95 * ka, weight=0.3)
        _draw_text(item.get("text", ""), NSMakeRect(rect.origin.x + 16, rect.origin.y + 16, rect.size.width - 32, 40), ta)
        # numéro (raccourci) en bas à droite, très discret
        na = _attrs(10, 0.30 * ka, weight=0.5)
        _draw_text(str(idx + 1), NSMakeRect(rect.origin.x + rect.size.width - 24, rect.origin.y + 12, 12, 12), na)

    @objc.python_method
    def _draw_orb(self, cx, cy, d, k):
        """Petit personnage : orbe de verre sombre, liseré violet→turquoise, deux yeux
        qui suivent la souris et clignent, quelques particules."""
        from AppKit import NSGraphicsContext
        ov = self.overlay
        rr = d / 2.0
        # halo
        for grow, a in ((10, 0.06), (5, 0.12)):
            _violet(a * k).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - rr - grow, cy - rr - grow, d + 2 * grow, d + 2 * grow)).fill()
        # corps : dégradé radial sombre
        body = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - rr, cy - rr, d, d))
        grad = NSGradient.alloc().initWithColors_([
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.16, 0.14, 0.24, 1.0 * k),
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.05, 0.09, 1.0 * k),
        ])
        grad.drawInBezierPath_relativeCenterPosition_(body, (-0.3, 0.35))
        # liseré coloré : segments violet → turquoise → rose
        segs = 48
        for i in range(segs):
            t = i / segs
            hue = 0.72 + 0.22 * math.sin(2 * math.pi * (t + ov.phase * 0.05))  # 0.5 (turquoise) ↔ 0.94 (rose)
            a = 0.35 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * (t * 2 + ov.phase * 0.15)))
            col = NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(hue % 1.0, 0.75, 1.0, a * k)
            col.setStroke()
            p = NSBezierPath.bezierPath()
            p.setLineWidth_(1.6)
            p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_((cx, cy), rr - 0.8, t * 360 - 1, t * 360 + 360 / segs + 1)
            p.stroke()
        # reflet
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10 * k).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - rr * 0.55, cy + rr * 0.15, rr * 0.9, rr * 0.45)).fill()
        # yeux : suivent la souris
        dx, dy = ov.gaze
        blink = ov.blink_amount()  # 0 ouvert → 1 fermé
        eye_h = max(1.5, (d * 0.30) * (1 - blink))
        eye_w = d * 0.10
        gap = d * 0.16
        ex = cx + dx * d * 0.12
        ey = cy + dy * d * 0.10 - d * 0.02
        NSColor.colorWithCalibratedWhite_alpha_(0.97, 0.97 * k).setFill()
        for sx in (-1, 1):
            rx = ex + sx * gap - eye_w / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(rx, ey - eye_h / 2, eye_w, eye_h), eye_w / 2, eye_w / 2).fill()
        # particules
        for i, (px_, py_, s_) in enumerate(((1.35, 0.9, 2.0), (-1.25, -0.7, 1.5), (0.9, -1.3, 1.2))):
            tw = 0.5 + 0.5 * math.sin(ov.phase * 1.3 + i * 2.1)
            _white((0.25 + 0.55 * tw) * k).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx + px_ * rr - s_ / 2, cy + py_ * rr - s_ / 2, s_, s_)).fill()

    @objc.python_method
    def _draw_tile(self, rect, tile, idx, ka, hovered):
        """Tuile lumineuse : aura colorée (éteinte si réglage off), gros libellé, état."""
        from AppKit import NSGraphicsContext
        r, g, b = tile["color"]
        on = tile.get("on", True)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 22, 22)
        NSColor.colorWithCalibratedWhite_alpha_(0.055 if not hovered else 0.08, ka).setFill()
        path.fill()
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        path.addClip()
        strength = (0.9 if on else 0.18) * (1.1 if hovered else 1.0) * ka
        c = lambda a: NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)
        w, h = rect.size.width, rect.size.height
        glow = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
            [c(0.95 * strength), c(0.55 * strength), c(0.25 * strength), c(0.10 * strength), c(0.03 * strength), c(0.0)],
            [0.0, 0.18, 0.38, 0.6, 0.82, 1.0], NSColorSpace.sRGBColorSpace())
        glow.drawInRect_relativeCenterPosition_(NSMakeRect(rect.origin.x - w * 0.2, rect.origin.y - h * 1.0, w * 1.4, h * 2.0), (0.0, 0.0))
        band = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
            [c(0.40 * strength), c(0.15 * strength), c(0.04 * strength), c(0.0)], [0.0, 0.35, 0.7, 1.0], NSColorSpace.sRGBColorSpace())
        band.drawInRect_angle_(NSMakeRect(rect.origin.x, rect.origin.y, w, h * 0.75), 90.0)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.05 * ka).setFill()
        seed = int(rect.origin.x * 7 + idx * 131)
        for n in range(36):
            gx = rect.origin.x + ((seed * 31 + n * 97) % int(w))
            gy = rect.origin.y + ((seed * 17 + n * 53) % int(h * 0.6))
            NSBezierPath.fillRect_(NSMakeRect(gx, gy, 1, 1))
        ctx.restoreGraphicsState()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, (0.10 if not hovered else 0.20) * ka).setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        # Icône, centrée dans la moitié haute. Elle remplit le grand vide qu'il y avait
        # là et rend les tuiles reconnaissables d'un coup d'œil : avant, les quatre ne
        # se distinguaient que par leur teinte. Elle porte aussi l'état (allumé/éteint),
        # ce qui rend l'ancienne pastille-témoin redondante.
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
        na = _attrs(10.5, 0.42 * ka, weight=0.5)
        _draw_text(str(idx + 1), NSMakeRect(rect.origin.x + w - 26, rect.origin.y + h - 30, 12, 12), na)
        # libellé + état
        _draw_text(tile["title"].upper(), NSMakeRect(rect.origin.x + 18, rect.origin.y + 40, w - 36, 20), _attrs(15, 0.96 * ka, weight=0.6))
        ov = self.overlay
        copied = ov.flash_index == idx and time.time() - ov.flash_t0 < 1.2
        sub = "Copié ✓" if copied else tile.get("subtitle", "")
        sa = _attrs(11.5, (0.95 if copied else 0.55) * ka)
        if copied:
            sa = dict(sa); sa[NSForegroundColorAttributeName] = c(0.95 * ka)
        _draw_text(sub, NSMakeRect(rect.origin.x + 18, rect.origin.y + 20, w - 36, 16), sa)

    @objc.python_method
    def _draw_panel(self, panel, k):
        ov = self.overlay
        data = ov.data()
        px, py, pw, ph = panel.origin.x, panel.origin.y, panel.size.width, panel.size.height
        M = 24.0
        x0 = px + M
        inner_w = pw - 2 * M
        top = py + ph

        # « LocalFlow » suivi de l'état du moteur : `status` était calculé à chaque
        # rafraîchissement et n'était affiché nulle part.
        ta = _attrs(15, 0.95 * k, weight=0.5)
        _draw_text("LocalFlow", NSMakeRect(x0, top - M - 18, 200, 20), ta)
        status = data.get("status", "")
        if status:
            sx = x0 + _text_width("LocalFlow", ta) + 10
            _draw_text(status, NSMakeRect(sx, top - M - 17, inner_w - (sx - x0) - 60, 18),
                       _attrs(11.5, 0.42 * k))
        _draw_text(data.get("stats_line", ""), NSMakeRect(x0, top - M - 36, inner_w - 80, 14), _attrs(11, 0.55 * k))
        orb_d = 36.0
        ov.orb_center = (px + pw - M - orb_d / 2, top - M - orb_d / 2 + 2)
        self._draw_orb(ov.orb_center[0], ov.orb_center[1], orb_d, k)

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
        self.phase = 0.0
        self.level = 0.0
        self.bars = [0.0] * BAR_COUNT
        self.content_alpha = 1.0
        self.flash_index = -1
        self.flash_t0 = 0.0
        self.gaze = (0.0, 0.0)          # direction du regard (-1..1)
        self.orb_center = (0.0, 0.0)    # en coordonnées vue
        self._next_blink = time.time() + 3.0
        self._blink_t0 = None
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
        radius = min(ch / 2.0, 22.0)
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

    def begin_recording(self, hands_free=False):
        """Remet la forme d'onde à plat et démarre le chrono."""
        self.wave = [0.0] * WAVE_COUNT
        self._wave_tick = 0
        self.rec_t0 = time.time()
        self.rec_hands_free = bool(hands_free)

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

    def blink_amount(self):
        if self._blink_t0 is None:
            return 0.0
        t = (time.time() - self._blink_t0) / 0.14
        if t >= 1.0:
            return 0.0
        return math.sin(t * math.pi)  # ferme puis rouvre

    def _update_gaze(self, now):
        """Les yeux suivent la souris (position écran → vue), avec lissage."""
        try:
            loc = NSEvent.mouseLocation()
            f = self.panel.frame()
            vx, vy = loc.x - f.origin.x, loc.y - f.origin.y
            dx, dy = vx - self.orb_center[0], vy - self.orb_center[1]
            dist = math.hypot(dx, dy) or 1.0
            norm = min(1.0, dist / 220.0)
            tx, ty = dx / dist * norm, dy / dist * norm
            self.gaze = (self.gaze[0] + (tx - self.gaze[0]) * 0.25, self.gaze[1] + (ty - self.gaze[1]) * 0.25)
        except Exception:
            pass
        if now >= self._next_blink:
            self._blink_t0 = now
            self._next_blink = now + 2.5 + (now * 7.3) % 3.5  # pseudo-aléatoire 2,5–6 s
        elif self._blink_t0 is not None and now - self._blink_t0 > 0.2:
            self._blink_t0 = None

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
        if self.state == "expanded":
            self._update_gaze(now)
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
