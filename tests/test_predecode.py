"""Transcription anticipée : coupe sur une vraie pause, jamais dans la parole ni en fin d'audio."""
import threading
import time
import unittest

import numpy as np

from localflow.predecode import SR, PreDecoder, find_split


def parole(s, seed=0):
    return np.random.default_rng(seed).normal(0, 0.1, int(s * SR)).astype(np.float32)


def pause(s):
    return np.zeros(int(s * SR), dtype=np.float32) + 1e-4


class Decoupe(unittest.TestCase):
    def test_coupe_au_milieu_de_la_pause(self):
        a = np.concatenate([parole(25), pause(0.8), parole(10, 1)])
        cut = find_split(a, 0)
        self.assertIsNotNone(cut)
        self.assertTrue(25 * SR <= cut <= 25.8 * SR, cut / SR)

    def test_trop_court_pas_de_coupe(self):
        self.assertIsNone(find_split(np.concatenate([parole(10), pause(1), parole(5)]), 0))

    def test_pas_de_pause_pas_de_coupe_sauf_force(self):
        a = parole(50)
        self.assertIsNone(find_split(a, 0))
        self.assertIsNotNone(find_split(a, 0, force=True))

    def test_jamais_dans_la_derniere_seconde(self):
        a = np.concatenate([parole(25), pause(0.5)])   # la pause est en toute fin : la phrase peut continuer
        self.assertIsNone(find_split(a, 0))


class Assemblage(unittest.TestCase):
    def test_segments_puis_reste_dans_l_ordre(self):
        audio = np.concatenate([parole(22), pause(0.8), parole(5, 1)])

        class Rec:
            def peek(self):
                return audio

        class T:
            n = 0
            def transcribe(self, a, prompt=""):
                T.n += 1
                return f"seg{T.n}"

        pre = PreDecoder(Rec(), T(), threading.Lock(), lambda: "")
        deadline = time.time() + 5
        while not pre.segments and time.time() < deadline:
            time.sleep(0.05)
        text, rest = pre.finish(audio)
        self.assertEqual(text, "seg1 seg2")
        self.assertLess(rest, 6.5)


if __name__ == "__main__":
    unittest.main()
