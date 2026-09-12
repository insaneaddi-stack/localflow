"""Navigation au clavier dans la fenêtre Historique.

La partie testable est la sélection : ce que vise ⏎ à tout moment, et ce
qu'elle devient quand la recherche filtre la liste sous elle.

    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from AppKit import NSMakeRect

    from localflow.history_window import _ListView
except ImportError as exc:                              # AppKit absent
    raise unittest.SkipTest(f"AppKit non importable ici : {exc}")


def liste(n):
    v = _ListView.alloc().initWithFrame_(NSMakeRect(0, 0, 700, 10))
    v.set_rows([{"text": f"ligne {i}", "app": ""} for i in range(n)])
    return v


class Selection(unittest.TestCase):

    def test_entree_sans_choix_vise_la_plus_recente(self):
        """Ouvrir puis ⏎ tout de suite : on recolle la dernière dictée."""
        self.assertEqual(liste(5).current()["text"], "ligne 0")

    def test_premier_bas_choisit_la_premiere_ligne(self):
        """↓ ne doit pas sauter la ligne 0 en partant de « rien de choisi »."""
        v = liste(5)
        v.move_selection(1)
        self.assertEqual(v.selected, 0)

    def test_les_bornes_tiennent(self):
        v = liste(3)
        for _ in range(10):
            v.move_selection(1)
        self.assertEqual(v.selected, 2)
        for _ in range(10):
            v.move_selection(-1)
        self.assertEqual(v.selected, 0)

    def test_la_recherche_ne_laisse_pas_la_selection_dans_le_vide(self):
        """On choisit la ligne 4, puis on tape : il n'en reste que deux."""
        v = liste(5)
        v.selected = 4
        v.set_rows([{"text": "ligne 0", "app": ""}, {"text": "ligne 1", "app": ""}])
        self.assertEqual(v.selected, 1)
        self.assertEqual(v.current()["text"], "ligne 1")

    def test_liste_vide(self):
        v = liste(0)
        self.assertIsNone(v.current())
        self.assertFalse(v.move_selection(1))


if __name__ == "__main__":
    unittest.main()
