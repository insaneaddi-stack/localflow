"""Bande flottante en bas de l'écran — un seul panneau, plusieurs états :

- idle      : petite barre sombre (l'app est allumée), survol = s'éclaire
- expanded  : panneau « bulles » (historique cliquable, stats, réglages, actions)
- recording : pilule avec halo violet + barres blanches qui suivent la voix
- processing: même pilule, halo rapide, barres qui ondulent

Transitions interpolées à 60 fps (ease-out), ombre portée douce.
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
HOVER_W, HOVER_H = 96.0, 10.0
PILL_W, PILL_H = 124.0, 36.0
PANEL_W, PANEL_H = 720.0, 292.0
MEET_W, MEET_H = 118.0, 22.0          # réunion en cours : point rouge + chrono
OFFER_W, OFFER_H = 400.0, 44.0        # « Réunion détectée — enregistrer ? »
MARGINS = {"idle": 14.0, "hover": 14.0, "recording": 18.0, "processing": 18.0, "expanded": 44.0,
           "meeting": 14.0, "meeting_offer": 18.0}
RED = (1.0, 0.30, 0.32)
FPS = 60.0
ANIM_S = 0.28
BAR_COUNT, BAR_W, BAR_GAP = 5, 3.0, 4.0
SEGMENTS = 140
VIOLET = (0.66, 0.40, 1.00)

def _violet(a):
    r, g, b = VIOLET
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

def _white(a):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a)

def _ease(k):
    return 1 - (1 - k) ** 3

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
        # rectangle de contenu courant (interpolé), centré en bas
        cw, ch = ov.cur_w, ov.cur_h
        content = NSMakeRect((b.size.width - cw) / 2.0, PAD, cw, ch)
        radius = min(ch / 2.0, 22.0)
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(content, radius, radius)

        # ombre douce (plusieurs couches décalées vers le bas)
        for i, (dy, grow, a) in enumerate(((-3, 10, 0.10), (-2, 5, 0.14), (-1, 2, 0.18))):
            sr = NSMakeRect(content.origin.x - grow, content.origin.y - grow + dy, cw + 2 * grow, ch + 2 * grow)
            NSColor.colorWithCalibratedWhite_alpha_(0.0, a).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(sr, radius + grow, radius + grow).fill()

        NSColor.colorWithCalibratedWhite_alpha_(0.05, 0.97).setFill()
        body.fill()
        # liseré
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10 if ov.state in ("idle",) else 0.14).setStroke()
        body.setLineWidth_(1.0)
        body.stroke()

        self.hit_zones = []
        st = ov.state
        k = ov.content_alpha  # 0→1 pendant une transition
        if st in ("recording", "processing"):
            self._draw_glow(content, st)
            self._draw_bars(content, st, k)
        elif st == "expanded":
            self._draw_panel(content, k)
        elif st == "meeting":
            self._draw_meeting(content, k)
        elif st == "meeting_offer":
            self._draw_offer(content, k)
        elif st == "hover":
            self._draw_idle(content, hover=True)
        else:
            self._draw_idle(content, hover=False)

    @objc.python_method
    def _draw_idle(self, pill, hover):
        ov = self.overlay
        # petit point violet qui « respire » doucement : l'app est prête
        cx = pill.origin.x + pill.size.width / 2.0
        cy = pill.origin.y + pill.size.height / 2.0
        pulse = 0.5 + 0.5 * math.sin(ov.phase * 1.6)
        a = (0.55 if hover else 0.35) + 0.25 * pulse
        d = 3.0 if not hover else 4.0
        _violet(a).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - d / 2, cy - d / 2, d, d)).fill()
        # fine ligne lumineuse
        _white(0.10 if not hover else 0.18).setFill()
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
        label = info.get("offer", "Réunion détectée") + " — l'enregistrer ?"
        _draw_text(label, NSMakeRect(x + 16, cy - 8, pill.size.width - 180, 16), _attrs(12.5, 0.94 * k, weight=0.4))
        bx = pill.origin.x + pill.size.width - 18
        w_no = self._chip(bx - 56, cy - 12, "Non", on=None, action="meeting_decline", alpha=k, min_w=56)
        self._chip(bx - 56 - 8 - 64, cy - 12, "Oui", on=True, action="meeting_accept", alpha=k, min_w=64)

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
        # petite orbe-témoin en haut à gauche (allumée / éteinte)
        dot = NSMakeRect(rect.origin.x + 18, rect.origin.y + h - 34, 16, 16)
        (c(0.9 * ka) if on else _white(0.15 * ka)).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(dot).fill()
        if on:
            c(0.25 * ka).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(dot.origin.x - 5, dot.origin.y - 5, 26, 26)).fill()
        # numéro
        na = _attrs(10, 0.30 * ka, weight=0.5)
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

        _draw_text("LocalFlow", NSMakeRect(x0, top - M - 18, 200, 20), _attrs(15, 0.95 * k, weight=0.5))
        _draw_text(data.get("stats_line", ""), NSMakeRect(x0, top - M - 36, inner_w - 80, 14), _attrs(11, 0.40 * k))
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
            rect = NSMakeRect(x0 + i * (tw + gap), ty, tw, th)
            ka = max(0.0, min(1.0, (k - 0.10 * i) / 0.6))
            hovered = self.hover_pt is not None and NSPointInRect(self.hover_pt, rect)
            self._draw_tile(rect, tile, i, ka, hovered)
            self.hit_zones.append((rect, tile["action"], tile.get("payload")))

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
        self.view = _BandView.alloc().initWithFrame_(self._view_rect())
        self.view.overlay = self
        self.panel.setContentView_(self.view)
        self.panel.orderFrontRegardless()
        self._ensure_timer()

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

    def _target_size(self, state):
        w, h = {
            "idle": (IDLE_W, IDLE_H),
            "hover": (HOVER_W, HOVER_H),
            "recording": (PILL_W, PILL_H),
            "processing": (PILL_W, PILL_H),
            "expanded": (PANEL_W, PANEL_H),
            "meeting": (MEET_W, MEET_H),
            "meeting_offer": (OFFER_W, OFFER_H),
        }[state]
        return (w, h, MARGINS[state])

    # ---- états ----

    def _set_state(self, state):
        if state == self.state:
            return
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
            k = min(1.0, (now - self._anim_t0) / ANIM_S)
            e = _ease(k)
            self.cur_w = _lerp(self._from[0], self._to[0], e)
            self.cur_h = _lerp(self._from[1], self._to[1], e)
            self.cur_margin = _lerp(self._from[2], self._to[2], e)
            self.panel.setFrame_display_(self._frame_rect(), False)
            self.content_alpha = max(0.0, (k - 0.35) / 0.65)
            animating = k < 1.0
            if not animating:
                self._anim_t0 = None
                self.content_alpha = 1.0
                self.view.updateTrackingAreas()
        if self.state == "expanded":
            self._update_gaze(now)
        if self.state in ("recording", "meeting"):
            try:
                lv = float(self._level_source())
            except Exception:
                lv = 0.0
            self.level = 0.4 * lv + 0.6 * self.level
            bars = self.bars
            mid = BAR_COUNT // 2
            new = list(bars)
            new[mid] = 0.5 * lv + 0.5 * bars[mid]
            for j in range(1, mid + 1):
                new[mid - j] = 0.7 * bars[mid - j + 1] + 0.3 * bars[mid - j]
                new[mid + j] = 0.7 * bars[mid + j - 1] + 0.3 * bars[mid + j]
            self.bars = new
        # au repos, on redessine juste assez pour la respiration (économie CPU)
        if self.state in ("idle", "meeting") and not animating and int(self.phase * FPS) % (4 if self.state == "idle" else 3) != 0:
            return
        self.view.setNeedsDisplay_(True)
