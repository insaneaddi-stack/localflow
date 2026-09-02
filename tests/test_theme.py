"""Les invariants du système AUR'IA, ceux qu'une retouche ne doit pas casser."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import theme


def _hex_rgb(h):
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


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

    def test_encre_3_passe_AA(self):
        """La production a corrigé ce que le kit annonçait à tort.

        Le kit donne ENCRE_3 = #8A837D à 4,5:1 ; mesurée, elle tombe à 3,60 sur
        le fond crème — sous le seuil AA, alors que son emploi déclaré est
        « discret, légendes », donc du petit texte. Le CSS de production est
        passé à #6E6862 en le disant : « 5,0:1 sur #F4F4F4 — #8A837D tombait à
        3,40 ». Ce test empêche de revenir à la valeur du kit.
        """
        self.assertGreaterEqual(contraste(theme.ENCRE_3, theme.FOND), 4.5)
        self.assertLess(contraste(_hex_rgb("8A837D"), theme.FOND), 4.5)

    def test_italique_passe_le_seuil_gros_texte(self):
        """Même histoire pour le grand italique.

        La production mesure sur #F4F4F4, le fond de la section concernée, et
        non sur le crème : « 3,17:1 — #F66000 tombait à 2,90 ». Recalculé ici
        sur ce même fond, sinon la comparaison ne veut rien dire.
        """
        gris = _hex_rgb("F4F4F4")
        self.assertGreaterEqual(contraste(theme.O_ITALIQUE, gris), 3.0)
        self.assertLess(contraste(_hex_rgb("F66000"), gris), 3.0)

    def test_pas_de_pilule(self):
        """« Rayon 8px, pas de pilule » — la règle est écrite deux fois en production."""
        self.assertLessEqual(theme.RAYON_CARTE, 10.0)
        self.assertLessEqual(theme.RAYON_FLOTTANT, 8.0)

    def test_ombre_chaude_pas_orange_pur(self):
        """L'ombre de production est un brun chaud : un orange pur ne creuse pas."""
        r, g, b = theme.OMBRE["couleur"]
        self.assertTrue(r > g > b, "l'ombre doit rester chaude")
        self.assertLess(r, 0.75, "un orange vif à cette place rayonne au lieu de creuser")


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
