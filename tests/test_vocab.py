"""Vocabulaire auto : règles d'entrée, oubli, secrets, fautes, et renvoi sans vocabulaire si refusé."""
import os, sys, unittest, urllib.error
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow import vocab, voxtral
import numpy as np

class T(unittest.TestCase):
    def test_demo(self):
        vocab._demo()

    def test_renvoi_sans_vocabulaire(self):
        appels = []
        def api(audio, prompt="", language="", key=None, timeout=None):
            appels.append(prompt)
            if prompt:
                raise urllib.error.HTTPError("u", 400, "bad", {}, None)
            return "bonjour à tous"
        with mock.patch.object(voxtral, "transcribe_api", api), mock.patch.object(voxtral, "_detail", lambda e: "400"):
            txt = voxtral.Engine(lambda m: None).transcribe(np.zeros(16000, np.float32), prompt="Noto, Vixelle.")
        self.assertEqual(txt, "bonjour à tous")
        self.assertEqual(appels, ["Noto, Vixelle.", ""])

if __name__ == "__main__":
    unittest.main()
