"""DA AUR'IA appliquée à LocalFlow — source de vérité unique.

Transposé de « AUR'IA — Kit de marque 2026 » (brandbook/kit/index.html).

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

FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts"
)

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
    """NSFont du système AUR'IA, avec repli sur les polices du Mac si absentes."""
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
ENCRE_3 = _hex("8A837D")     # discret, légendes, 4,5:1

# Les cinq oranges — un emploi chacun, et pas un de plus
O_VITRINE = _hex("FF6A00")   # décor, icônes, illustrations. Jamais du texte.
O_ITALIQUE = _hex("F66000")  # le grand italique des titres
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
HUD_ENCRE_3 = _hex("8A837D")  # le neutre médian ne bouge pas
HUD_ORANGE = O_VITRINE       # sur sombre, c'est le plus clair qui porte

BLANC = (1.0, 1.0, 1.0)


def ns(rgb, alpha=1.0):
    """NSColor sRGB depuis un triplet du système."""
    from AppKit import NSColor

    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


# ----------------------------------------------------------------- formes ----

RAYON_CARTE = 14.0      # cartes : 14 à 16 px
RAYON_CARTE_MAX = 16.0
RAYON_BOUTON = 10.0     # action principale ; la pilule est réservée aux fins de section
FILET = 1.0             # « le filet remplace l'ombre »
FOCUS_EPAISSEUR = 2.0   # anneau : 2 px #BE4400, écart 4 px, le même partout
FOCUS_ECART = 4.0
FIL_EPAISSEUR = 5.0     # le fil : #FB5F00, 5 px, extrémités arrondies

# L'ombre unique du brand book : teintée orange, réservée à ce qui flotte
# vraiment. Elle est spécifiée sur le fond crème, où l'orange assombrit.
OMBRE = {"dy": -10.0, "flou": 26.0, "couleur": (232 / 255, 93 / 255, 0.0), "alpha": 0.20}

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
EASE_DOUCE = (0.32, 0.72, 0.0, 1.0)     # la courbe maison
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
