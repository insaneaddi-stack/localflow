"""L'accord fn+⇧ envoie la dictée vers l'agenda plutôt que sous le curseur.

La décision est pure et tient en une fonction : dictation_mode. Le reste du
geste (event tap, latch au fn down) demande un vrai clavier ; l'auto-test
complet de l'app le couvre.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from localflow.app import SHIFT_GRACE_S, dictation_mode, mode_upgrade
    from localflow.hotkey import FLAG_FN, FLAG_SHIFT, SHIFT_KEYCODES
except ImportError as exc:                              # AppKit/rumps absents
    raise unittest.SkipTest(f"localflow non importable ici : {exc}")


class DictationMode(unittest.TestCase):
    """(⇧ tenu au fn down, réglage agenda actif) → destination"""

    CASES = [
        ((True,  True),  "calendar"),    # l'accord, réglage actif
        ((False, True),  "dictation"),   # fn seul : la dictée normale ne bouge pas
        ((True,  False), "dictation"),   # réglage éteint : ⇧ ne fait rien de spécial
        ((False, False), "dictation"),
    ]

    def test_cases(self):
        for args, want in self.CASES:
            self.assertEqual(dictation_mode(*args), want, msg=str(args))

    def test_defaut_sans_surprise(self):
        """Tout ce qui n'est pas un franc oui doit taper le texte, pas l'envoyer.

        Un mode calendrier déclenché par erreur écrit dans un agenda partagé ;
        une dictée tapée par erreur s'annule avec Cmd+Z.
        """
        for douteux in (None, 0, "", []):
            self.assertEqual(dictation_mode(douteux, True), "dictation", msg=repr(douteux))


class ShiftApresFn(unittest.TestCase):
    """Le défaut vu en usage réel : fn descend toujours avant ⇧.

    Le 9 sept. 2026, aucune dictée n'a armé le mode — le journal ne montrait que
    des « fn down » nus. Juger l'accord au seul instant du fn down ne marche pas :
    « en même temps » ne l'est jamais.
    """

    def test_shift_juste_apres_arme_le_mode(self):
        self.assertEqual(mode_upgrade("dictation", 0.05, True, True), "calendar")
        self.assertEqual(mode_upgrade("dictation", SHIFT_GRACE_S - 0.01, True, True), "calendar")

    def test_shift_trop_tard_ne_change_rien(self):
        """⇧ au milieu d'une phrase de dix secondes n'a rien à voir avec le geste."""
        self.assertEqual(mode_upgrade("dictation", 4.0, True, True), "dictation")
        self.assertEqual(mode_upgrade("dictation", SHIFT_GRACE_S + 0.01, True, True), "dictation")

    def test_sans_enregistrement_en_cours(self):
        self.assertEqual(mode_upgrade("dictation", 0.05, False, True), "dictation")

    def test_reglage_eteint(self):
        self.assertEqual(mode_upgrade("dictation", 0.05, True, False), "dictation")

    def test_deja_arme_reste_arme(self):
        """⇧ relâché puis repressé ne doit pas désarmer ce qui l'était."""
        self.assertEqual(mode_upgrade("calendar", 9.0, True, True), "calendar")

    def test_les_deux_chemins_convergent(self):
        """⇧ avant fn ou juste après : même résultat, c'est le même geste."""
        avant = dictation_mode(True, True)
        apres = mode_upgrade(dictation_mode(False, True), 0.05, True, True)
        self.assertEqual(avant, apres, "calendar")


class Drapeaux(unittest.TestCase):
    def test_les_deux_touches_majuscule(self):
        """⇧ gauche et ⇧ droite : un clavier en a deux, le geste doit marcher avec les deux."""
        self.assertEqual(SHIFT_KEYCODES, (56, 60))

    def test_fn_et_shift_sont_distincts(self):
        """Deux masques différents : sans ça, le latch verrait ⇧ à chaque fn."""
        self.assertNotEqual(FLAG_FN, FLAG_SHIFT)
        self.assertFalse(FLAG_FN & FLAG_SHIFT)


if __name__ == "__main__":
    unittest.main()
