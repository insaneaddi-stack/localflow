"""Le pré-roll ne ressort jamais l'audio d'un flux précédent."""
import os, sys, unittest
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localflow import audio

class T(unittest.TestCase):
    def test_preroll_neuf(self):
        r = audio.Recorder()
        bloc = np.full((audio.BLOCK, 1), 0.3, np.float32)
        for _ in range(40):
            r._callback(bloc, audio.BLOCK, None, None)      # « dictée précédente »
        r._filled = 0                                        # ce que fait _open_impl
        self.assertEqual(len(r._preroll()), 0)
        for _ in range(2):
            r._callback(bloc, audio.BLOCK, None, None)
        self.assertEqual(len(r._preroll()), 2 * audio.BLOCK)

if __name__ == "__main__":
    unittest.main()
