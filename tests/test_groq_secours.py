"""Groq d'abord, Voxtral en secours ; si les deux échouent, l'erreur de Groq remonte (20/11/2026)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from localflow import voxtral

AUDIO = np.zeros(16000, dtype=np.float32)


class GroqSecours(unittest.TestCase):
    def run_with(self, groq, mistral, gk="g"):
        with mock.patch.object(voxtral, "groq_key", return_value=gk), \
             mock.patch.object(voxtral, "groq_api", side_effect=groq), \
             mock.patch.object(voxtral, "_voxtral_api", side_effect=mistral):
            return voxtral.transcribe_api(AUDIO)

    def test_groq_ok(self):
        self.assertEqual(self.run_with(["bonjour"], AssertionError("pas appelé")), "bonjour")

    def test_secours_mistral(self):
        self.assertEqual(self.run_with(RuntimeError("groq 429"), ["secours"]), "secours")

    def test_les_deux_tombent_erreur_groq(self):
        with self.assertRaisesRegex(RuntimeError, "groq 429"):
            self.run_with(RuntimeError("groq 429"), RuntimeError("402"))

    def test_sans_groq_voxtral(self):
        self.assertEqual(self.run_with(AssertionError("pas appelé"), ["voxtral"], gk=""), "voxtral")


if __name__ == "__main__":
    unittest.main()
