"""Une fermeture de micro figée (CoreAudio) ne bloque plus rien.

Avant : Pa_StopStream ne revenait pas, le fil des opérations audio restait bloqué,
la dictée suivante attendait son micro 6 s puis était perdue, et l'app se tuait.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import audio


class FluxFige:
    def __init__(self):
        self.libere = threading.Event()

    def stop(self):
        self.libere.wait(5)

    def close(self):
        pass


class FluxNormal:
    fermé = False

    def stop(self):
        pass

    def close(self):
        self.fermé = True


class Fermeture(unittest.TestCase):
    def test_fermeture_figee_rend_la_main(self):
        flux = FluxFige()
        t0 = time.time()
        audio.close_input_stream(flux, wait_s=0.2)
        self.assertLess(time.time() - t0, 1.0)
        self.assertGreater(audio.hung_close_s(), 0.0)   # l'app saura redémarrer au repos
        flux.libere.set()
        time.sleep(0.1)
        self.assertEqual(audio.hung_close_s(), 0.0)      # revenue : plus rien de figé

    def test_fermeture_normale(self):
        flux = FluxNormal()
        audio.close_input_stream(flux)
        self.assertTrue(flux.fermé)
        self.assertEqual(audio.hung_close_s(), 0.0)


if __name__ == "__main__":
    unittest.main()
