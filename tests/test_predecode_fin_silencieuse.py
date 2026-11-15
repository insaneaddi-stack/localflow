"""Une longue dictée qui finit par du silence ne perd plus ses segments déjà transcrits."""
import os, sys, threading, unittest
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow.predecode import PreDecoder

class Rec:
    def peek(self): return np.zeros(10, np.float32)

class MoteurFinEnPanne:
    def transcribe(self, audio, prompt="", empty_ok=False):
        raise RuntimeError("Voxtral n'a pas répondu (résultat vide)")

class MoteurVide:
    def __init__(self): self.empty_ok = None
    def transcribe(self, audio, prompt="", empty_ok=False):
        self.empty_ok = empty_ok; return ""

class FinSilencieuse(unittest.TestCase):
    def _pre(self, moteur):
        p = PreDecoder(Rec(), moteur, threading.Lock(), lambda: "")
        p.texts, p.committed = ["segment déjà transcrit"], 16000 * 40
        return p

    def test_fin_en_panne_garde_les_segments(self):
        texte, _ = self._pre(MoteurFinEnPanne()).finish(np.zeros(16000 * 48, np.float32))
        self.assertEqual(texte, "segment déjà transcrit")

    def test_fin_vide_acceptee_sans_nouveaux_essais(self):
        m = MoteurVide()
        texte, _ = self._pre(m).finish(np.zeros(16000 * 48, np.float32))
        self.assertTrue(m.empty_ok)
        self.assertEqual(texte, "segment déjà transcrit")

    def test_sans_segment_l_echec_remonte(self):
        p = PreDecoder(Rec(), MoteurFinEnPanne(), threading.Lock(), lambda: "")
        with self.assertRaises(RuntimeError):
            p.finish(np.zeros(16000 * 8, np.float32))

if __name__ == "__main__":
    unittest.main()
