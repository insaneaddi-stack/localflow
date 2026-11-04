"""Voxtral, seul moteur de dictée : on redemande, et si rien ne vient l'exception remonte.

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

AUDIO = np.zeros(16000, dtype=np.float32)          # 1 s
LONG = np.zeros(16000 * 8, dtype=np.float32)       # 8 s : une vraie dictée


def http(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(voxtral.URL, code, "x", headers, io.BytesIO(b'{"message": "rate limited"}'))


def setUpModule():
    patcher = mock.patch.object(voxtral.time, "sleep")   # les pauses entre essais ne ralentissent pas les tests
    sleep = patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    globals()["SLEEP"] = sleep


def moteur():
    return voxtral.Engine(lambda m: None)


class Redemande(unittest.TestCase):
    def setUp(self):
        SLEEP.reset_mock()

    def test_reponse_directe(self):
        with mock.patch.object(voxtral, "transcribe_api", return_value="Bonjour Louqman.") as api:
            self.assertEqual(moteur().transcribe(AUDIO), "Bonjour Louqman.")
        self.assertEqual(api.call_count, 1)

    def test_429_puis_succes(self):
        # Le cas du log : un 429 passager ne doit plus faire perdre la dictée.
        with mock.patch.object(voxtral, "transcribe_api", side_effect=[http(429), "Bonjour Louqman."]) as api:
            self.assertEqual(moteur().transcribe(AUDIO), "Bonjour Louqman.")
        self.assertEqual(api.call_count, 2)

    def test_429_sans_fin_leve_apres_tous_les_essais(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=http(429)) as api:
            with self.assertRaises(RuntimeError):
                moteur().transcribe(AUDIO)
        self.assertEqual(api.call_count, 1 + len(voxtral.RETRY_PAUSES_S))

    def test_retry_after_respecte_sans_double_attente(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=[http(429, "6"), "ok"]):
            moteur().transcribe(AUDIO)
        attentes = [c.args[0] for c in SLEEP.call_args_list]
        self.assertAlmostEqual(sum(attentes), 6.0)   # 4,5 s + la pause de 1,5 s, pas 7,5 s

    def test_panne_reseau_redemandee(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=[TimeoutError(), "Rétabli."]) as api:
            self.assertEqual(moteur().transcribe(AUDIO), "Rétabli.")
        self.assertEqual(api.call_count, 2)

    def test_cle_refusee_pas_de_nouvel_essai(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=http(401)) as api:
            with self.assertRaises(RuntimeError):
                moteur().transcribe(AUDIO)
        self.assertEqual(api.call_count, 1)

    def test_silence_court_n_est_pas_une_erreur(self):
        for sortie in ("", "  ", "Merci d'avoir regardé."):
            with mock.patch.object(voxtral, "transcribe_api", return_value=sortie) as api:
                self.assertEqual(moteur().transcribe(AUDIO), "", sortie)
            self.assertEqual(api.call_count, 1, sortie)

    def test_resultat_vide_sur_vraie_dictee_redemande(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=["", "Voilà le mensuel."]) as api:
            self.assertEqual(moteur().transcribe(LONG), "Voilà le mensuel.")
        self.assertEqual(api.call_count, 2)

    def test_resultat_vide_sur_vraie_dictee_leve(self):
        # 30 s de parole qui reviennent vides : on garde l'audio plutôt que de coller "rien".
        with mock.patch.object(voxtral, "transcribe_api", return_value=""):
            with self.assertRaises(RuntimeError):
                moteur().transcribe(LONG)


class PlusTard(unittest.TestCase):
    def test_dictee_perdue_retrouvee_au_deuxieme_passage(self):
        e = moteur()
        with mock.patch.object(voxtral, "transcribe_api", side_effect=[http(401), "Retrouvée."]):
            self.assertEqual(voxtral.retry_later(e, AUDIO), "Retrouvée.")
        self.assertEqual([c.args[0] for c in SLEEP.call_args_list][:1], [voxtral.LATER_PAUSES_S[0]])

    def test_toujours_rien_rend_none(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=http(401)) as api:
            self.assertIsNone(voxtral.retry_later(moteur(), AUDIO))
        self.assertEqual(api.call_count, len(voxtral.LATER_PAUSES_S))


class Cle(unittest.TestCase):
    def test_cle_absente_pas_de_nouvel_essai(self):
        with mock.patch.object(voxtral, "transcribe_api", side_effect=voxtral.MissingKey()) as api:
            with self.assertRaises(voxtral.MissingKey):
                moteur().transcribe(AUDIO)
        self.assertEqual(api.call_count, 1)

    def test_forme_d_une_cle(self):
        self.assertTrue(voxtral.looks_like_key("aBcD1234eFgH5678iJkL9012mNoP3456"))
        self.assertTrue(voxtral.looks_like_key("  aBcD1234eFgH5678iJkL9012mNoP3456\n"))
        for faux in ("", "bonjour", "deux mots dans le presse-papiers", "a" * 200, "clé-avec-accent-et-tirets-1234567890"):
            self.assertFalse(voxtral.looks_like_key(faux), faux)

    def test_verification_de_la_cle(self):
        with mock.patch.object(voxtral.urllib.request, "urlopen"):
            self.assertIs(voxtral.check_key("k" * 32), True)
        with mock.patch.object(voxtral.urllib.request, "urlopen", side_effect=http(401)):
            self.assertIs(voxtral.check_key("k" * 32), False)
        with mock.patch.object(voxtral.urllib.request, "urlopen", side_effect=http(503)):
            self.assertIsNone(voxtral.check_key("k" * 32))
        with mock.patch.object(voxtral.urllib.request, "urlopen", side_effect=OSError()):
            self.assertIsNone(voxtral.check_key("k" * 32))

    def test_enregistrement_dans_le_trousseau(self):
        ok = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(voxtral.subprocess, "run", return_value=ok) as run:
            voxtral.save_key("  " + "k" * 32 + "\n")
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/bin/security", "add-generic-password"])
        self.assertIn(voxtral.KEYCHAIN_SERVICE, args)
        self.assertEqual(args[-1], "k" * 32)
        self.assertEqual(voxtral.api_key(), "k" * 32)   # utilisée tout de suite, sans redémarrage
        voxtral._key = None

    def test_trousseau_refuse(self):
        ko = mock.Mock(returncode=1, stderr="denied")
        with mock.patch.object(voxtral.subprocess, "run", return_value=ko):
            with self.assertRaises(RuntimeError):
                voxtral.save_key("k" * 32)


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
