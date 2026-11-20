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


class TutorielGroq(unittest.TestCase):
    """La fenêtre de passage à Groq : ouvrir la page, refuser une non-clé, enregistrer une bonne clé."""

    def run_dialog(self, reponses, check=True):
        from localflow import keydialog
        vus, ouvert, ranges, notes = [], [], [], []
        it = iter(reponses)
        def dialog(message, buttons, default, title):
            vus.append(message)
            return next(it)
        ok = keydialog.ask_groq_key(lambda *a: notes.append(a), dialog=dialog, open_page=lambda: ouvert.append(1),
                                    check=lambda k: check, save=ranges.append)
        return ok, vus, ouvert, ranges, notes

    def test_parcours_complet(self):
        cle = "gsk_" + "a" * 52
        ok, vus, ouvert, ranges, notes = self.run_dialog([("Ouvrir Groq", ""), ("Valider", "pas une clé"), ("Valider", cle)])
        self.assertTrue(ok)
        self.assertEqual(ouvert, [1])
        self.assertIn("gsk_", vus[2])          # 2e passage : « ce n'est pas une clé Groq »
        self.assertEqual(ranges, [cle])
        self.assertEqual(notes[0][0], "Groq branché")

    def test_plus_tard(self):
        ok, _, _, ranges, _ = self.run_dialog([("Plus tard", "")])
        self.assertFalse(ok)
        self.assertEqual(ranges, [])

    def test_cle_refusee(self):
        cle = "gsk_" + "b" * 52
        ok, vus, _, ranges, _ = self.run_dialog([("Valider", cle), ("Plus tard", "")], check=False)
        self.assertFalse(ok)
        self.assertIn("refuse", vus[1])
        self.assertEqual(ranges, [])
