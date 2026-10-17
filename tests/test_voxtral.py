"""Voxtral d'abord, Qwen local en repli : la dictée n'est jamais perdue.

L'API est remplacée par une fonction factice ; aucun appel réseau.
"""

import io
import os
import sys
import unittest
import urllib.error
import wave
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from localflow import voxtral


class FauxQwen:
    name = "qwen3-asr"
    last_retry = ""

    def __init__(self):
        self.appels = 0

    def transcribe(self, audio, prompt="", language=""):
        self.appels += 1
        return "texte local"


def moteur(use_api=True):
    qwen = FauxQwen()
    charge = []

    def local():
        charge.append(1)
        return qwen

    return voxtral.Engine(local, lambda m: None, use_api=use_api), qwen, charge


AUDIO = np.zeros(16000, dtype=np.float32)


class Repli(unittest.TestCase):
    def test_voxtral_repond_qwen_jamais_charge(self):
        e, qwen, charge = moteur()
        with mock.patch.object(voxtral, "transcribe_api", return_value="Bonjour Louqman."):
            self.assertEqual(e.transcribe(AUDIO), "Bonjour Louqman.")
        self.assertEqual(e.name, "voxtral")
        self.assertEqual(charge, [])   # Qwen reste hors de la RAM

    def test_erreur_reseau_repli_puis_pause(self):
        e, qwen, _ = moteur()
        with mock.patch.object(voxtral, "transcribe_api", side_effect=TimeoutError()) as api:
            self.assertEqual(e.transcribe(AUDIO), "texte local")
            self.assertEqual(e.transcribe(AUDIO), "texte local")
        self.assertEqual(api.call_count, 1)   # 2e dictée : local direct, pas 6 s d'attente de plus
        self.assertEqual(e.name, "qwen3-asr (repli)")

    def test_429_repli(self):
        e, qwen, _ = moteur()
        err = urllib.error.HTTPError(voxtral.URL, 429, "Too Many Requests", {}, io.BytesIO())
        with mock.patch.object(voxtral, "transcribe_api", side_effect=err):
            self.assertEqual(e.transcribe(AUDIO), "texte local")
        self.assertEqual(qwen.appels, 1)

    def test_resultat_vide_ou_hallucination_repli(self):
        for sortie in ("", "  ", "Merci d'avoir regardé."):
            e, qwen, _ = moteur()
            with mock.patch.object(voxtral, "transcribe_api", return_value=sortie):
                self.assertEqual(e.transcribe(AUDIO), "texte local", sortie)

    def test_moteur_local_n_appelle_pas_l_api(self):
        e, qwen, _ = moteur(use_api=False)
        with mock.patch.object(voxtral, "transcribe_api") as api:
            self.assertEqual(e.transcribe(AUDIO), "texte local")
        api.assert_not_called()
        self.assertEqual(e.name, "qwen3-asr")


class Format(unittest.TestCase):
    def test_vocabulaire_sans_espace_ni_virgule(self):
        # L'API refuse un terme contenant une espace ou une virgule (erreur 3051).
        self.assertEqual(voxtral.bias_terms("AUR'IA, Wispr Flow, Noto, AUR'IA."),
                         ["AUR'IA", "Wispr", "Flow", "Noto"])
        self.assertEqual(voxtral.bias_terms(""), [])

    def test_wav_en_memoire(self):
        audio = np.linspace(-1, 1, 1600, dtype=np.float32)
        with wave.open(io.BytesIO(voxtral.wav_bytes(audio))) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                             (1, 2, 16000, 1600))


if __name__ == "__main__":
    unittest.main()
