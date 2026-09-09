"""Flux muet : CoreAudio livre des zéros exacts, le Recorder doit le voir et rouvrir."""
import time
import unittest

import numpy as np

from localflow import audio


class _FakeStream:
    pass


class FluxMuet(unittest.TestCase):
    def setUp(self):
        self.r = audio.Recorder()
        self.r._stream = _FakeStream()      # « ouvert », sans PortAudio
        self.r.opened_at = time.time()

    def _block(self, x):
        self.r._callback(np.full((audio.BLOCK, 1), x, dtype=np.float32), audio.BLOCK, None, None)

    def test_souffle_n_est_pas_muet(self):
        for _ in range(40):
            self._block(1e-6)
        self.assertFalse(self.r.muted(0.0))
        self.assertEqual(self.r.muted_for(), 0.0)

    def test_zeros_exacts_datent_le_mutisme(self):
        self._block(0.0)
        self.assertFalse(self.r.muted(0.5))       # pas encore assez long
        self.r._silent_since -= 1.0
        self.assertTrue(self.r.muted(0.5))
        self.assertFalse(self.r.healthy())        # 2 s : la santé le voit aussi
        self.r._silent_since -= 2.0
        self.assertFalse(self.r.healthy())

    def test_un_bloc_vivant_efface_le_mutisme(self):
        self._block(0.0)
        self.r._silent_since -= 5.0
        self._block(0.01)
        self.assertFalse(self.r.muted(0.0))

    def test_start_rouvre_un_flux_muet(self):
        self._block(0.0)
        self.r._silent_since -= 1.0
        opened = []
        self.r.open = lambda: (opened.append(1), setattr(self.r, "_silent_since", 0.0))
        self.r.start()
        self.assertEqual(opened, [1])
        self.assertIn("muet", self.r.last_reopen)
        self.assertTrue(self.r.recording)
        self.r.cancel()

    def test_start_normal_ne_rouvre_pas(self):
        self._block(0.001)
        self.r._device_id = audio.default_input_id()
        audio._open_streams.add(self.r._stream)
        try:
            opened = []
            self.r.open = lambda: opened.append(1)
            self.r.start()
            self.assertEqual(opened, [])
            self.assertIsNone(self.r.last_reopen)
        finally:
            audio._open_streams.discard(self.r._stream)
            self.r.cancel()


if __name__ == "__main__":
    unittest.main()
