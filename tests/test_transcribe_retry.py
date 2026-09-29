"""La 2e passe Qwen3-ASR ne se relance que sur un décodage tronqué.

Le décodage est glouton : sans troncature, relancer ressort le même texte et
double le temps d'attente. Le modèle est remplacé par une fausse session.
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy as np
    from localflow.transcribe import RETRY_MAX_TOKENS, SAMPLE_RATE, Transcriber
except ImportError as exc:                              # MLX absent
    raise unittest.SkipTest(f"localflow.transcribe non importable ici : {exc}")

# « est-ce que » trois fois : _looks_broken y voit une boucle.
LONG = ("est-ce que tu viens demain, est-ce que tu peux passer, "
        "est-ce que tu as le dossier pour le client vendredi matin")


class FakeSession:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def transcribe(self, pcm, context="", language=None, max_new_tokens=None):
        self.calls.append(max_new_tokens)
        text, truncated = self.outputs.pop(0)
        return SimpleNamespace(text=text, truncated=truncated)


def transcriber(outputs):
    t = Transcriber.__new__(Transcriber)       # pas de chargement du modèle
    t.session = FakeSession(outputs)
    t.last_retry = ""
    return t


AUDIO = np.zeros(SAMPLE_RATE * 8, dtype=np.float32)


class Retry(unittest.TestCase):
    def test_boucle_apparente_sans_troncature_une_seule_passe(self):
        t = transcriber([(LONG, False)])
        self.assertEqual(t.transcribe(AUDIO), LONG)
        self.assertEqual(t.session.calls, [None])
        self.assertEqual(t.last_retry, "")

    def test_trop_court_sans_troncature_une_seule_passe(self):
        t = transcriber([("bonjour", False)])
        self.assertEqual(t.transcribe(AUDIO), "bonjour")
        self.assertEqual(len(t.session.calls), 1)

    def test_tronque_relance_avec_budget_elargi(self):
        t = transcriber([("on valide le budget et", True), ("on valide le budget et on se voit vendredi", False)])
        self.assertEqual(t.transcribe(AUDIO), "on valide le budget et on se voit vendredi")
        self.assertEqual(t.session.calls, [None, RETRY_MAX_TOKENS])
        self.assertTrue(t.last_retry)

    def test_tronque_garde_la_passe_la_plus_longue(self):
        t = transcriber([("on valide le budget et", True), ("on", False)])
        self.assertEqual(t.transcribe(AUDIO), "on valide le budget et")


if __name__ == "__main__":
    unittest.main()
