"""Les invariants du système AUR'IA, ceux qu'une retouche ne doit pas casser."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import theme


def _lum(rgb):
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


NEUTRES = {
    "FOND": theme.FOND, "FOND_PUR": theme.FOND_PUR, "CREME": theme.CREME,
    "CARTE": theme.CARTE, "TRAIT": theme.TRAIT, "TRAIT_FORT": theme.TRAIT_FORT,
    "ENCRE": theme.ENCRE, "ENCRE_2": theme.ENCRE_2, "ENCRE_3": theme.ENCRE_3,
    "HUD_FOND": theme.HUD_FOND, "HUD_CARTE": theme.HUD_CARTE,
    "HUD_SURVOL": theme.HUD_SURVOL, "HUD_TRAIT": theme.HUD_TRAIT,
    "HUD_ENCRE": theme.HUD_ENCRE, "HUD_ENCRE_2": theme.HUD_ENCRE_2,
    "HUD_ENCRE_3": theme.HUD_ENCRE_3,
}


class Palette(unittest.TestCase):
    def test_aucun_gris_froid(self):
        """« Un gris bleuté trahit un composant importé d'ailleurs. »

        Toutes les neutres du système sont chaudes : rouge ≥ vert ≥ bleu.
        """
        for name, rgb in NEUTRES.items():
            r, g, b = rgb
            self.assertTrue(r >= g >= b, msg=f"{name} {rgb} n'est pas une neutre chaude")

    def test_aucun_noir_pur(self):
        for name, rgb in NEUTRES.items():
            self.assertNotEqual(rgb, (0.0, 0.0, 0.0), msg=name)

    def test_cinq_oranges_distincts(self):
        oranges = [theme.O_VITRINE, theme.O_ITALIQUE, theme.O_GROS,
                   theme.O_BOUTON, theme.O_PETIT, theme.O_FIL]
        self.assertEqual(len(set(oranges)), len(oranges))

    def test_contraste_texte_sur_fond_clair(self):
        """Ratios réels, mesurés contre le fond crème du système.

        Le brand book annonce ses ratios contre du BLANC PUR, pas contre son
        propre #FEFAF6 : ENCRE_2 y est donné à 7:1 et vaut 6,73 sur blanc,
        6,48 sur crème. L'écart est sans conséquence — on reste très au-dessus
        du seuil AA — mais on encode la mesure, pas l'annonce.
        """
        for name, encre, mini in [("ENCRE", theme.ENCRE, 15.0),
                                  ("ENCRE_2", theme.ENCRE_2, 6.4),
                                  ("O_GROS", theme.O_GROS, 4.0),
                                  ("O_BOUTON", theme.O_BOUTON, 4.4),
                                  ("O_PETIT", theme.O_PETIT, 5.0)]:
            self.assertGreaterEqual(contraste(encre, theme.FOND), mini, msg=name)

    def test_encre_3_ne_porte_pas_de_petit_texte(self):
        """ENCRE_3 est annoncée à 4,5:1 mais vaut 3,60 sur le fond crème.

        C'est SOUS le seuil AA (4,5:1) alors que son emploi déclaré est
        « discret, légendes » — donc du petit texte. Dans LocalFlow les légendes
        prennent ENCRE_2 ; ENCRE_3 reste pour le décoratif et les traits.
        Ce test verrouille le constat : s'il casse, c'est que la valeur a bougé.
        """
        self.assertLess(contraste(theme.ENCRE_3, theme.FOND), 4.5)
        self.assertGreaterEqual(contraste(theme.ENCRE_2, theme.FOND), 4.5)

    def test_contraste_texte_sur_hud(self):
        """Sur fond sombre l'échelle s'inverse : c'est l'orange CLAIR qui porte."""
        self.assertGreaterEqual(contraste(theme.HUD_ENCRE, theme.HUD_FOND), 15.0)
        self.assertGreaterEqual(contraste(theme.HUD_ENCRE_2, theme.HUD_FOND), 7.0)
        self.assertGreaterEqual(contraste(theme.HUD_ORANGE, theme.HUD_FOND), 4.5)
        # et l'orange sombre, lui, ne passe PAS sur du sombre — d'où la règle
        self.assertLess(contraste(theme.O_PETIT, theme.HUD_FOND), 4.5)


class Mouvement(unittest.TestCase):
    COURBES = {"standard": theme.ease_standard, "douce": theme.ease_douce,
               "arrivée": theme.ease_arrivee}

    def test_aucun_rebond(self):
        """« Aucun rebond, aucun ressort. » La courbe ne sort jamais de [0,1]."""
        for name, f in self.COURBES.items():
            for i in range(201):
                v = f(i / 200)
                self.assertGreaterEqual(v, -1e-9, msg=f"{name} à t={i/200}")
                self.assertLessEqual(v, 1.0 + 1e-9, msg=f"{name} à t={i/200}")

    def test_monotone(self):
        for name, f in self.COURBES.items():
            vals = [f(i / 200) for i in range(201)]
            for a, b in zip(vals, vals[1:]):
                self.assertGreaterEqual(b, a - 1e-9, msg=name)

    def test_bornes(self):
        for name, f in self.COURBES.items():
            self.assertAlmostEqual(f(0.0), 0.0, places=6, msg=name)
            self.assertAlmostEqual(f(1.0), 1.0, places=6, msg=name)

    def test_cinq_durees(self):
        durees = [theme.D_DOIGT, theme.D_SURVOL, theme.D_ETAT, theme.D_RECIT, theme.D_ENTREE]
        self.assertEqual(durees, sorted(durees))
        self.assertEqual(len(set(durees)), 5)


class Polices(unittest.TestCase):
    def setUp(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("AppKit indisponible (lancer via .venv/bin/python)")

    def test_fichiers_presents(self):
        for f in ("Figtree.ttf", "Newsreader.ttf", "Newsreader-Italic.ttf"):
            self.assertTrue(os.path.exists(os.path.join(theme.FONTS_DIR, f)), msg=f)

    def test_licences_presentes(self):
        """Newsreader et Figtree sont sous OFL : la licence voyage avec la fonte."""
        for f in ("OFL-newsreader.txt", "OFL-figtree.txt"):
            self.assertTrue(os.path.exists(os.path.join(theme.FONTS_DIR, f)), msg=f)

    def test_resolution(self):
        self.assertEqual(theme.font(15, 400).fontName(), "Figtree-Regular")
        self.assertEqual(theme.font(15, 700).fontName(), "Figtree-Bold")
        self.assertEqual(theme.font(28, serif=True).fontName(), "NewsreaderRoman-SemiBold")
        self.assertEqual(theme.font(28, serif=True, italic=True).fontName(), "Newsreader16pt-Italic")

    def test_jamais_None(self):
        for size in (10, 15, 44):
            for w in (400, 500, 600, 700):
                self.assertIsNotNone(theme.font(size, w))
                self.assertIsNotNone(theme.font(size, w, serif=True))


if __name__ == "__main__":
    unittest.main()
