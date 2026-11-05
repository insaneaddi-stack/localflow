"""Étape « Ta clé Voxtral » du tutoriel : enchaînement des boutons (sans fenêtre, AppKit non affiché)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from localflow import tutorial
except ImportError as exc:                              # AppKit absent
    raise unittest.SkipTest(f"localflow.tutorial non importable ici : {exc}")


class Cfg:
    def __init__(self):
        self.data = {}

    def save(self):
        pass


def tuto(presse_papiers_a_une_cle=False, resultat="ok"):
    t = tutorial.Tutorial(Cfg())
    t.active = True
    t.steps = list(tutorial.STEPS)
    t.index = 1                                          # l'étape clé suit la bienvenue
    assert t.steps[1]["wait"] == "key_set"
    with mock.patch.object(tutorial.keydialog, "clipboard_has_key", return_value=presse_papiers_a_une_cle):
        t._enter_step()
    t.key_action = lambda done: done(resultat)
    return t


class EtapeCle(unittest.TestCase):
    def test_console_puis_coller_avance(self):
        t = tuto()
        with mock.patch.object(tutorial.keydialog, "open_console") as ouvre:
            t.button()                                   # 1er clic : ouvre la console
        ouvre.assert_called_once()
        self.assertEqual(t.index, 1)
        t.button()                                       # 2e clic : colle la clé
        self.assertEqual(t.index, 2)

    def test_cle_deja_copiee_le_premier_clic_colle(self):
        t = tuto(presse_papiers_a_une_cle=True)
        with mock.patch.object(tutorial.keydialog, "open_console") as ouvre:
            t.button()
        ouvre.assert_not_called()
        self.assertEqual(t.index, 2)

    def test_presse_papiers_vide_on_reste_avec_un_message(self):
        t = tuto(presse_papiers_a_une_cle=True, resultat="not_a_key")
        t.button()
        self.assertEqual(t.index, 1)
        self.assertIn("recolle", t.status)

    def test_cle_refusee_on_reste(self):
        t = tuto(presse_papiers_a_une_cle=True, resultat="refused")
        t.button()
        self.assertEqual(t.index, 1)
        self.assertIn("refuse", t.status)

    def test_plus_tard_passe_l_etape(self):
        t = tuto()
        t.button("later")
        self.assertEqual(t.index, 2)

    def test_etape_absente_quand_la_cle_existe(self):
        with mock.patch.object(tutorial.voxtral, "api_key", return_value="k" * 32):
            steps = tutorial.Tutorial._visible_steps()
        self.assertTrue(all(s["wait"] != "key_set" for s in steps))
        self.assertEqual(len(steps), len(tutorial.STEPS) - 1)

    def test_etape_presente_sans_cle(self):
        with mock.patch.object(tutorial.voxtral, "api_key", return_value=""):
            steps = tutorial.Tutorial._visible_steps()
        self.assertEqual(len(steps), len(tutorial.STEPS))

    def test_autres_etapes_inchangees(self):
        t = tuto()
        t.index = 2
        self.assertTrue(t.event("dictated"))             # première dictée → étape suivante
        self.assertEqual(t.index, 3)


if __name__ == "__main__":
    unittest.main()
