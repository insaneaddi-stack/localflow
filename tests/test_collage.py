"""Le presse-papiers d'origine n'est rendu qu'une fois le collage vu dans le champ.

0,6 s fixe était trop court pour une app lente : elle lisait le presse-papiers déjà
restauré et collait l'ancien contenu.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from localflow.paste import pasted_in, wait_until_pasted
except ImportError as exc:                              # AppKit/Quartz absents
    raise unittest.SkipTest(f"localflow.paste non importable ici : {exc}")


class Collage(unittest.TestCase):
    def test_texte_vu_dans_le_champ(self):
        self.assertTrue(pasted_in("on cale un point lundi", "Salut,\non cale un\npoint lundi"))
        self.assertFalse(pasted_in("on cale un point lundi", "Salut,"))
        self.assertFalse(pasted_in("texte", None))

    def test_restauration_des_que_le_collage_est_vu(self):
        lectures = iter(["", "", "avant on cale un point lundi"])
        t0 = time.time()
        self.assertTrue(wait_until_pasted("on cale un point lundi", max_s=1.5,
                                          read=lambda: next(lectures, "avant on cale un point lundi")))
        self.assertLess(time.time() - t0, 1.0)

    def test_champ_illisible_attend_le_maximum(self):
        t0 = time.time()
        self.assertFalse(wait_until_pasted("texte", max_s=0.3, read=lambda: None))
        self.assertGreaterEqual(time.time() - t0, 0.3)


if __name__ == "__main__":
    unittest.main()
