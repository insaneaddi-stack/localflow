"""« ... » avant une majuscule devient un point ; « cloud code » → Claude Code sans toucher « le cloud »."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow.cleanup import _polish
from localflow.dictionary import Dictionary, DEFAULT_CONTENT

class T(unittest.TestCase):
    def test_points(self):
        self.assertEqual(_polish("C'est fini... Ensuite on part"), "C'est fini. Ensuite on part")
        self.assertIn("...", _polish("Je sais pas... peut-être"))
    def test_dico(self):
        p = os.path.join(tempfile.mkdtemp(), "d.txt")
        open(p, "w").write(DEFAULT_CONTENT)
        d = Dictionary(p)
        self.assertEqual(d.apply("ouvre cloud code"), "ouvre Claude Code")
        self.assertEqual(d.apply("tout est dans le cloud"), "tout est dans le cloud")
        self.assertEqual(d.apply("relance local flow"), "relance LocalFlow")

if __name__ == "__main__":
    unittest.main()
