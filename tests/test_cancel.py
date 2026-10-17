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
    from localflow.app import ESC_KEYCODE, esc_target, esc_victims, job_aborted, same_app, warm_interval
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
        self.assertTrue(job_aborted(3, {3}))         # Esc sur cette dictée-ci

    def test_vivante(self):
        self.assertFalse(job_aborted(4, {3}))        # lancée après l'annulation
        self.assertFalse(job_aborted(1, set()))      # aucune annulation

    def test_precedente_encore_en_transcription_survit(self):
        """Dictée 2 lancée pendant que la 1 se transcrit, Esc pendant la 2 :
        seule la 2 est jetée, la 1 doit être collée."""
        self.assertEqual(esc_victims(True, 2, {1}), {2})
        self.assertFalse(job_aborted(1, esc_victims(True, 2, {1})))

    def test_esc_sans_enregistrement_annule_ce_qui_se_transcrit(self):
        self.assertEqual(esc_victims(False, 2, {1, 2}), {1, 2})


class MemeApp(unittest.TestCase):
    def test_coller_seulement_dans_l_app_de_depart(self):
        self.assertTrue(same_app("com.google.Chrome", "com.google.Chrome"))
        self.assertFalse(same_app("com.google.Chrome", "com.microsoft.VSCode"))

    def test_app_inconnue_on_colle(self):
        self.assertTrue(same_app("", "com.google.Chrome"))
        self.assertTrue(same_app("com.google.Chrome", ""))


class KeepWarm(unittest.TestCase):
    def test_lente_double_jusqu_a_10_min(self):
        self.assertEqual(warm_interval(30, 5.0), 60)
        self.assertEqual(warm_interval(480, 40.0), 600)
        self.assertEqual(warm_interval(600, 40.0), 600)

    def test_rapide_revient_a_30_s(self):
        self.assertEqual(warm_interval(600, 0.2), 30)


if __name__ == "__main__":
    unittest.main()
