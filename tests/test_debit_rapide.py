"""Débit rapide accepté, boucle ou débit impossible rejetés (même règle que app.py)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow.transcribe import _looks_broken

def rejete(text, seconds):
    n = len(text.split())
    return n > seconds * 7 + 4 or (n > seconds * 4.5 + 4 and "boucle" in _looks_broken(text, seconds))

class T(unittest.TestCase):
    def test_rapide_accepte(self):
        self.assertFalse(rejete(" ".join(f"mot{i}" for i in range(28)), 4.7))
    def test_boucle_rejetee(self):
        self.assertTrue(rejete("merci beaucoup pour " * 12, 5.0))
    def test_impossible(self):
        self.assertTrue(rejete(" ".join(f"m{i}" for i in range(220)), 13.7))

if __name__ == "__main__":
    unittest.main()
