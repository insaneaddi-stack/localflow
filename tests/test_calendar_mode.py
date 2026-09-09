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
    from localflow.app import dictation_mode
    from localflow.hotkey import FLAG_FN, FLAG_SHIFT
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


class Drapeaux(unittest.TestCase):
    def test_fn_et_shift_sont_distincts(self):
        """Deux masques différents : sans ça, le latch verrait ⇧ à chaque fn."""
        self.assertNotEqual(FLAG_FN, FLAG_SHIFT)
        self.assertFalse(FLAG_FN & FLAG_SHIFT)


if __name__ == "__main__":
    unittest.main()
