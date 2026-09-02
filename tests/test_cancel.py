"""Esc annule la dictée en cours.

Les deux décisions qui comptent sont pures et testées ici :
  - esc_target : Esc est-il avalé, et pour quoi faire ?
  - job_aborted : ce job doit-il encore coller quelque chose ?

Le reste (AppKit, micro, modèle) n'est pas simulable ici ; l'auto-test complet
de l'app le couvre au démarrage.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from localflow.app import ESC_KEYCODE, esc_target, job_aborted
except ImportError as exc:                              # AppKit/rumps absents
    raise unittest.SkipTest(f"localflow.app non importable ici : {exc}")


class EscTarget(unittest.TestCase):
    """(enregistre, fin de dictée, transcrit, panneau ouvert) → cible"""

    CASES = [
        # rien en cours : Esc DOIT passer à l'app active, sinon on casse
        # l'échappement du système (vim, modales, recherche…)
        ((False, False, False, False), None),
        # les trois états d'une dictée
        ((True,  False, False, False), "dictation"),   # enregistrement / mains-libres
        ((False, True,  False, False), "dictation"),   # les TAIL_S après le relâchement
        ((False, False, True,  False), "dictation"),   # transcription en cours
        # panneau seul
        ((False, False, False, True),  "panel"),
        # la dictée prime sur le panneau
        ((True,  False, False, True),  "dictation"),
        ((False, False, True,  True),  "dictation"),
    ]

    def test_cases(self):
        for args, want in self.CASES:
            self.assertEqual(esc_target(*args), want, msg=str(args))

    def test_keycode(self):
        self.assertEqual(ESC_KEYCODE, 53)


class JobAborted(unittest.TestCase):
    def test_annulee(self):
        self.assertTrue(job_aborted(3, 3))          # Esc sur cette dictée-ci

    def test_perimee(self):
        self.assertTrue(job_aborted(2, 3))          # une dictée plus récente a été annulée

    def test_vivante(self):
        self.assertFalse(job_aborted(4, 3))         # lancée après l'annulation
        self.assertFalse(job_aborted(1, -1))        # aucune annulation

    def test_aucune_annulation_par_defaut(self):
        """_abort_gen part à -1 : la toute première dictée (gen 1) doit vivre."""
        self.assertFalse(job_aborted(1, -1))


if __name__ == "__main__":
    unittest.main()
