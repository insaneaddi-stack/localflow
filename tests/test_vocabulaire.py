"""Les deux garde-fous qui empêchent LocalFlow d'abîmer le texte.

  - est_un_mot_coupe : un retour à la ligne relu via Accessibilité n'est pas
    une correction de l'utilisateur.
  - la correction floue du dictionnaire ne touche pas un vrai mot.

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow.learning import diff_corrections, est_un_mot_coupe


class MotCoupe(unittest.TestCase):
    """« produit » → « pro duit » : mise en page, pas correction."""

    COUPURES = [
        ("produit", "pro duit"),
        ("diagrammes", "diag rammes"),
        ("compliqué", "compli qué"),
        ("LocalFlow", "Local Flow"),
    ]
    VRAIES_CORRECTIONS = [
        ("mail", "main"),              # un mot pour un autre
        ("whisper flow", "Wispr Flow"),
        ("wispr flow", "WisprFlow"),   # le sens inverse reste apprenable
        ("produit", "produits"),       # mêmes morceaux, lettres différentes
    ]

    def test_coupures_rejetees(self):
        for bad, good in self.COUPURES:
            self.assertTrue(est_un_mot_coupe(bad, good), f"{bad} → {good}")

    def test_corrections_conservees(self):
        for bad, good in self.VRAIES_CORRECTIONS:
            self.assertFalse(est_un_mot_coupe(bad, good), f"{bad} → {good}")

    def test_diff_ignore_le_retour_a_la_ligne(self):
        """Le champ relu a coupé « produit » : rien ne doit être appris."""
        colle = "Explique-moi comment ce produit fonctionne vraiment"
        relu = "Explique-moi comment ce pro\nduit fonctionne vraiment"
        self.assertEqual(diff_corrections(colle, relu), [])

    def test_diff_voit_encore_une_vraie_correction(self):
        colle = "Envoie ça par mail demain matin sans faute"
        relu = "Envoie ça par main demain matin sans faute"
        self.assertIn(("mail", "main"), diff_corrections(colle, relu))


class BouclierDuDictionnaire(unittest.TestCase):
    """La correction floue ne réécrit jamais un mot qui existe."""

    DICO = ("AUR'IA\nLinki\nClaude\nFirecrawl\nDataMind\n"
            "oria -> AUR'IA\n"          # trop loin pour le flou (0,60) : règle explicite
            "calot -> Kalo\n")       # explicite = explicite, même sur un vrai mot

    @classmethod
    def setUpClass(cls):
        from localflow.dictionary import Dictionary

        fd, cls.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cls.DICO)
        cls.dic = Dictionary(path=cls.path)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.path)

    # Ces mots-là ressemblent autant aux entrées du dictionnaire que les
    # écorchures ci-dessous (0,80 à 0,89 de similarité) : seule leur existence
    # les distingue.
    INTOUCHABLES = [
        "on aura plus accès",
        "envoie-moi le link",
        "de l'eau chaude",
        "prends-en note",
        "toute la classe",
    ]
    A_CORRIGER = [
        ("la campagne Linky", "Linki"),
        ("teste Firecrall", "Firecrawl"),
        ("dans Datamined", "DataMind"),
    ]

    def test_vrais_mots_intacts(self):
        for phrase in self.INTOUCHABLES:
            self.assertEqual(self.dic.apply(phrase), phrase)

    def test_ecorchures_corrigees(self):
        for phrase, attendu in self.A_CORRIGER:
            self.assertIn(attendu, self.dic.apply(phrase), phrase)

    def test_regle_explicite_passe_outre_le_bouclier(self):
        """« mauvais -> bon » est un ordre : il s'applique même à un vrai mot."""
        self.assertEqual(self.dic.apply("on regarde Oria"), "on regarde AUR'IA")
        self.assertEqual(self.dic.apply("calot ça"), "Kalo ça")


if __name__ == "__main__":
    unittest.main()
