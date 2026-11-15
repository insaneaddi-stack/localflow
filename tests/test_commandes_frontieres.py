"""Les commandes vocales ne se déclenchent pas au milieu d'une phrase normale."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow.commands import apply_commands

class T(unittest.TestCase):
    def test_phrase_normale(self):
        self.assertEqual(apply_commands("Il a marqué deux points ce soir"), "Il a marqué deux points ce soir")
    def test_commande_avec_pause(self):
        self.assertEqual(apply_commands("Voici la liste, deux points, le pain"), "Voici la liste : le pain")
        self.assertEqual(apply_commands("Bonjour, à la ligne, comment ça va ?"), "Bonjour\nComment ça va ?")

if __name__ == "__main__":
    unittest.main()
