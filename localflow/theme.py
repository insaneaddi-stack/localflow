"""DA AUR'IA appliquée à LocalFlow — source de vérité unique.

Source : **le site en production** (`AURIA MVP/landing/styles.css`), pas le kit.
L'archive DA du dépôt AUR'IA le dit noir sur blanc : « c'est le site en
production qui fait foi, pas les documents ». Le kit (27 août) est en retard sur
trois points, et le `:root` en tête de styles.css est un vestige mort (Anton,
#FF4A00, Inter) : la page ne charge que Newsreader et Figtree.

Les trois écarts, tous vérifiés dans le CSS vivant :

- ENCRE_3 : #8A837D → #6E6862. Le commentaire de production dit « 5,0:1 sur
  #F4F4F4 — #8A837D tombait à 3,40 ». C'est exactement le défaut que la mesure
  avait sorti ici ; il est déjà corrigé chez toi.
- O_ITALIQUE : #F66000 → #EE5800, « 3,17:1 — #F66000 tombait à 2,90 ».
- L'ombre teintée n'est pas l'orange du kit mais un brun chaud
  rgba(160, 80, 20, …) doublé d'un anneau d'un pixel. Un orange pur ne creuse
  pas, il rayonne.

Et une règle de forme que le kit ne dit pas, écrite deux fois dans le CSS :
**« Rayon 8px, pas de pilule. »**

Deux surfaces, un seul système :

- **Les fenêtres** (historique, réunion, tutoriel, permissions) sont des pages.
  Elles prennent le crème du brandbook tel quel.
- **Le HUD** flotte au-dessus de n'importe quelle app : une pastille crème
  opaque au-dessus d'un éditeur sombre ferait tache. Il prend donc l'encre, mais
  l'encre CHAUDE de la marque (#1A1614), jamais un gris froid.

Le brandbook ne définit pas de surface sombre. La transposition suit une règle
unique : **les neutres gardent leur teinte et s'inversent en clarté**. Le fond
crème devient l'encre du texte, l'encre devient le fond ; l'encre 3 (#8A837D),
qui est le neutre médian, ne bouge pas. Aucun gris bleuté n'entre — le
brandbook le dit : « un gris bleuté trahit un composant importé d'ailleurs ».

Les oranges, eux, ne s'inversent pas mais leur emploi se décale : sur fond clair
le petit texte prend l'orange le plus SOMBRE (#BE4400, 5,2:1) ; sur fond sombre
c'est l'inverse, le plus CLAIR porte (#FF6A00 sur #1A1614 = 6,6:1).
"""

import os

# ---------------------------------------------------------------- polices ----

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
FONTS_DIR = os.path.join(_ASSETS, "fonts")
BRAND_DIR = os.path.join(_ASSETS, "brand")

# Le nom du produit. Le brand book réserve « AURIA » sans apostrophe aux prompts
# et à l'oral (la synthèse vocale l'épelle) ; à l'écrit c'est AUR'IA, et
# l'apostrophe est orange ou réservée en blanc, jamais d'une autre teinte.
NOM = "AUR'IAFLOW"
NOM_AVANT, NOM_APOSTROPHE, NOM_APRES = "AUR", "'", "IAFLOW"

# Instances nommées telles que CoreText les expose une fois les variables
# enregistrées (vérifié : NSFontManager.availableMembersOfFontFamily_).
SERIF_TITRE = "NewsreaderRoman-SemiBold"   # Newsreader 600 — titres
SERIF_ITAL = "Newsreader16pt-Italic"       # Newsreader 400 italique — la 2e ligne
SANS = {                                   # Figtree — tout le reste
    400: "Figtree-Regular",
    500: "Figtree-Medium",
    600: "Figtree-SemiBold",
    700: "Figtree-Bold",
}
SERIF_SECOURS = "Georgia"

_registered = None


def register_fonts():
    """Charge Newsreader et Figtree pour ce processus. Idempotent, ne lève jamais.

    Enregistrement à l'exécution plutôt que via ATSApplicationFontsPath : l'app
    tourne depuis le dépôt (voir install-agent.sh), le bundle ne contient pas les
    Resources. Renvoie la liste des fichiers effectivement chargés.
    """
    global _registered
    if _registered is not None:
        return _registered
    done = []
    try:
        import CoreText as CT
        from Foundation import NSURL

        for name in sorted(os.listdir(FONTS_DIR)):
            if not name.lower().endswith((".ttf", ".otf")):
                continue
            url = NSURL.fileURLWithPath_(os.path.join(FONTS_DIR, name))
            ok, _ = CT.CTFontManagerRegisterFontsForURL(
                url, CT.kCTFontManagerScopeProcess, None
            )
            if ok:
                done.append(name)
    except Exception:
        pass
    _registered = done
    return done


def font(size, weight=400, serif=False, italic=False):
    """NSFont du système AUR'IA, avec repli sur les polices du Mac si absentes.

    Ne lève jamais : elle est appelée depuis drawRect_, à 60 images par seconde,
    et une exception qui remonte d'un dessin fait planter l'app entière (AppKit
    la transforme en NSException, `_crashOnException:` fait le reste).
    """
    try:
        return _font(size, weight, serif, italic)
    except Exception:
        from AppKit import NSFont

        return NSFont.systemFontOfSize_(size)


def _font(size, weight, serif, italic):
    from AppKit import NSFont, NSFontWeightRegular, NSFontWeightSemibold

    register_fonts()
    if serif:
        name = SERIF_ITAL if italic else SERIF_TITRE
        f = NSFont.fontWithName_size_(name, size)
        if f is not None:
            return f
        f = NSFont.fontWithName_size_(SERIF_SECOURS, size)
        if f is not None:
            return f
    else:
        # On ne descend pas sous 400 : le système n'utilise que 400 à 700.
        key = min(SANS, key=lambda w: abs(w - weight))
        f = NSFont.fontWithName_size_(SANS[key], size)
        if f is not None:
            return f
    return NSFont.systemFontOfSize_weight_(
        size, NSFontWeightSemibold if weight >= 600 else NSFontWeightRegular
    )


# --------------------------------------------------------------- couleurs ----

def _hex(h):
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


# Les fonds du brandbook
FOND = _hex("FEFAF6")        # blanc chaud, le fond principal
FOND_PUR = _hex("FFFDFB")    # presque blanc, sections claires
CREME = _hex("FCF2EC")       # crème, sections en image
CARTE = _hex("FDF3E9")       # le fond des cartes
TRAIT = _hex("EDE3D8")       # le filet, 1 px
TRAIT_FORT = _hex("E0D0BF")

# Les encres — « le noir n'est jamais pur : il est chaud, tiré vers le brun »
ENCRE = _hex("1A1614")       # titres, 17:1
ENCRE_2 = _hex("5F5B57")     # texte courant, 7:1
ENCRE_3 = _hex("6E6862")     # discret, légendes — 5,0:1 (production)

# Les cinq oranges — un emploi chacun, et pas un de plus
O_VITRINE = _hex("FF6A00")   # décor, icônes, illustrations. Jamais du texte.
O_ITALIQUE = _hex("EE5800")  # le grand italique des titres — 3,17:1 (production)
O_GROS = _hex("D24E00")      # texte orange à partir de 24 px
O_BOUTON = _hex("C94E00")    # fond de bouton, blanc dessus
O_PETIT = _hex("BE4400")     # petit texte, liens, anneau de focus
O_FIL = _hex("FB5F00")       # le tracé du fil, uniquement

# La surface sombre : mêmes teintes, clarté inversée (voir l'en-tête)
HUD_FOND = _hex("1A1614")    # l'encre devient le fond
HUD_CARTE = _hex("241E1B")   # la carte, un cran au-dessus
HUD_SURVOL = _hex("2D2521")
HUD_TRAIT = _hex("332B26")   # le filet, côté sombre
HUD_ENCRE = _hex("FEFAF6")   # le fond crème devient l'encre
HUD_ENCRE_2 = _hex("C8BDB4")
HUD_ENCRE_3 = _hex("8A837D")  # côté sombre, le neutre médian d'origine tient (4,81:1)
HUD_ORANGE = O_VITRINE       # sur sombre, c'est le plus clair qui porte

BLANC = (1.0, 1.0, 1.0)


_images = {}


def image(name):
    """Un visuel de marque depuis assets/brand/ (monogramme, wordmark). None si absent."""
    if name not in _images:
        try:
            from AppKit import NSImage

            _images[name] = NSImage.alloc().initWithContentsOfFile_(
                os.path.join(BRAND_DIR, name))
        except Exception:
            _images[name] = None
    return _images[name]


MONOGRAMME = "monogram-orange.png"   # « le A seul : onglet, avatar, icône »
WORDMARK = "wordmark-noir-detoure.png"


def ns(rgb, alpha=1.0):
    """NSColor sRGB depuis un triplet du système."""
    from AppKit import NSColor

    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


# ----------------------------------------------------------------- formes ----

# « Rayon 8px, pas de pilule » — la règle est écrite deux fois dans le CSS de
# production. Le kit parlait de cartes à 14-16 px et d'une pilule pour les fins
# de section : la production a resserré et supprimé la pilule.
RAYON_CARTE = 10.0      # rectangles à coins doux
RAYON_FLOTTANT = 8.0    # ce qui flotte : pastilles, HUD
RAYON_BOUTON = 10.0
FILET = 1.0             # « le filet remplace l'ombre »
FOCUS_EPAISSEUR = 2.0   # anneau : 2 px #BE4400, écart 4 px, le même partout
FOCUS_ECART = 4.0
FIL_EPAISSEUR = 5.0     # le fil : #FB5F00, 5 px, extrémités arrondies

# L'ombre unique du brand book : teintée orange, réservée à ce qui flotte
# vraiment. Elle est spécifiée sur le fond crème, où l'orange assombrit.
OMBRE = {"dy": -14.0, "flou": 40.0, "couleur": (160 / 255, 80 / 255, 20 / 255), "alpha": 0.34}
# L'anneau d'un pixel qui double l'ombre en production.
OMBRE_ANNEAU = {"couleur": (201 / 255, 78 / 255, 0.0), "alpha": 0.09}

# Sur la surface sombre, la même ombre ne peut plus assombrir : elle rayonne, et
# se lit exactement comme la lueur que le système interdit. La règle des neutres
# s'applique donc aussi à l'ombre — même teinte chaude, clarté inversée : c'est
# un brun très sombre qui porte l'élévation, l'orange reste au crème.
OMBRE_HUD = {"dy": -10.0, "flou": 22.0, "couleur": (0.05, 0.035, 0.03), "alpha": 0.55}


# --------------------------------------------------------------- mouvement ----

# Cinq durées, et pas une de plus. Si un mouvement n'entre dans aucune case,
# c'est qu'il n'a pas de raison d'être.
D_DOIGT = 0.120     # le doigt appuie
D_SURVOL = 0.180    # survol, couleur
D_ETAT = 0.320      # changement d'état
D_RECIT = 0.520     # étape du récit
D_ENTREE = 0.880    # entrée à l'écran

EASE_STANDARD = (0.2, 0.7, 0.3, 1.0)    # retours courts
EASE_DOUCE = (0.32, 0.72, 0.0, 1.0)     # la courbe maison — 35 emplois en production
EASE_ARRIVEE = (0.16, 1.0, 0.3, 1.0)    # ce qui se pose


def bezier(x1, y1, x2, y2):
    """Courbe CSS cubic-bezier → f(t) ∈ [0,1]. Aucun rebond : le système l'interdit.

    Les points de contrôle donnent x(t) et y(t) ; il faut inverser x pour trouver
    le paramètre, d'où Newton (la dérivée est connue) puis bissection en secours
    quand la pente s'annule.
    """
    def cx(t):
        return ((1 - t) ** 3 * 0 + 3 * (1 - t) ** 2 * t * x1
                + 3 * (1 - t) * t * t * x2 + t ** 3)

    def cy(t):
        return ((1 - t) ** 3 * 0 + 3 * (1 - t) ** 2 * t * y1
                + 3 * (1 - t) * t * t * y2 + t ** 3)

    def dcx(t):
        return (3 * (1 - t) ** 2 * x1 + 6 * (1 - t) * t * (x2 - x1)
                + 3 * t * t * (1 - x2))

    def f(x):
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        t = x
        for _ in range(6):
            d = dcx(t)
            if abs(d) < 1e-6:
                break
            t2 = t - (cx(t) - x) / d
            if not 0.0 <= t2 <= 1.0:
                break
            t = t2
        else:
            return cy(t)
        lo, hi = 0.0, 1.0
        t = x
        for _ in range(24):
            if cx(t) < x:
                lo = t
            else:
                hi = t
            t = (lo + hi) / 2
        return cy(t)

    return f


ease_standard = bezier(*EASE_STANDARD)
ease_douce = bezier(*EASE_DOUCE)
ease_arrivee = bezier(*EASE_ARRIVEE)


# ---------------------------------------------------- barre de menus ----

ICON_CACHE = os.path.expanduser("~/Library/Caches/LocalFlow/menubar")


def menubar_icon(state, size=18.0):
    """Le monogramme, plus un signe d'état à côté. Renvoie un chemin PNG, ou None.

    La barre de menus portait des emoji — ⏳ 🎙 🔴 💭 — c'est-à-dire le dessin de
    quelqu'un d'autre. « Le A seul » est la forme que la marque réserve à
    l'icône. Le signe se pose À CÔTÉ et non dessous : le A doit garder ses
    proportions (le fichier est en 256 × 244, le forcer au carré l'écrase).

    Les états se distinguent par la forme du signe et par un seul orange —
    jamais par un rouge, le système n'en a pas.
    """
    try:
        from AppKit import (NSImage, NSBezierPath, NSMakeRect, NSBitmapImageRep,
                            NSPNGFileType, NSCompositingOperationSourceOver)

        os.makedirs(ICON_CACHE, exist_ok=True)
        path = os.path.join(ICON_CACHE, f"{state}-{int(size)}-v2.png")
        if os.path.exists(path):
            return path
        mono = image(MONOGRAMME)
        if mono is None:
            return None
        sz = mono.size()
        ratio = (sz.width / sz.height) if sz.height else 1.0

        h = size
        aw = h * ratio                      # largeur du A à hauteur pleine
        signe = state not in ("idle", "loading")
        w = aw + (h * 0.62 if signe else 0.0)

        img = NSImage.alloc().initWithSize_((w, h))
        img.lockFocus()
        alpha = {"loading": 0.35, "processing": 0.75}.get(state, 1.0)
        mono.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, aw, h), NSMakeRect(0, 0, 0, 0),
            NSCompositingOperationSourceOver, alpha)

        cx = aw + h * 0.31                  # centre de la zone du signe
        cy = h * 0.5
        if state == "recording":
            r = h * 0.155
            ns(O_VITRINE, 1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - r, cy - r, r * 2, r * 2)).fill()
        elif state == "hands_free":
            # le fil : ça continue sans toi
            fp = NSBezierPath.bezierPath()
            fp.setLineWidth_(max(1.6, h * 0.11))
            fp.setLineCapStyle_(1)
            # « Une seule courbe » : une ondulation franche, pas un gribouillis —
            # à 18 px un S se referme et devient une tache.
            fp.moveToPoint_((aw + h * 0.09, cy - h * 0.09))
            fp.curveToPoint_controlPoint1_controlPoint2_(
                (aw + h * 0.53, cy + h * 0.09),
                (aw + h * 0.24, cy + h * 0.16),
                (aw + h * 0.38, cy - h * 0.16))
            ns(O_FIL, 1.0).setStroke()
            fp.stroke()
        elif state == "meeting":
            r = h * 0.17
            ring = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - r, cy - r, r * 2, r * 2))
            ring.setLineWidth_(max(1.3, h * 0.09))
            ns(O_VITRINE, 1.0).setStroke()
            ring.stroke()
        elif state == "processing":
            # trois points : le travail est en cours, rien à décider
            r = h * 0.075
            for k in (-1, 0, 1):
                ns(O_VITRINE, 0.45 + 0.25 * (k + 1)).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(cx - r + k * h * 0.20, cy - r, r * 2, r * 2)).fill()
        img.unlockFocus()

        rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
        data = rep.representationUsingType_properties_(NSPNGFileType, {})
        if data is None or not data.writeToFile_atomically_(path, True):
            return None
        return path
    except Exception:
        return None


def attributed(text, size, weight=400, color=None, serif=False, italic=False):
    """Chaîne attribuée dans les caractères du système (menus natifs, champs)."""
    from AppKit import (NSAttributedString, NSFontAttributeName,
                        NSForegroundColorAttributeName)

    attrs = {NSFontAttributeName: font(size, weight, serif=serif, italic=italic)}
    if color is not None:
        attrs[NSForegroundColorAttributeName] = color
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)
