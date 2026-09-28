"""Choix du micro : casque Bluetooth → micro du Mac, sauf capot fermé ou micro intégré muet."""
import time
import unittest

from localflow import audio


class ChoixMicro(unittest.TestCase):
    def setUp(self):
        self._orig = (audio.lid_closed, audio.sd.query_devices, audio.open_input_stream, audio.default_input_id)
        self.devices = [
            {"name": "AirPods de louqman", "max_input_channels": 1},
            {"name": "Haut-parleurs MacBook Air", "max_input_channels": 0},
            {"name": "Micro MacBook Air", "max_input_channels": 1},
        ]
        audio.sd.query_devices = lambda *a, **k: self.devices
        audio.lid_closed = lambda: False

    def tearDown(self):
        audio.lid_closed, audio.sd.query_devices, audio.open_input_stream, audio.default_input_id = self._orig

    def test_casque_bluetooth_reconnu(self):
        self.assertTrue(audio._is_bluetooth("AirPods de louqman"))
        self.assertTrue(audio._is_bluetooth("Beats Studio Buds"))
        self.assertFalse(audio._is_bluetooth("Micro MacBook Air"))
        self.assertFalse(audio._is_bluetooth("Shure MV7"))
        self.assertFalse(audio._is_bluetooth(None))

    def test_micro_integre_trouve(self):
        self.assertEqual(audio.builtin_input_index(), 2)

    def test_capot_ferme_pas_de_micro_integre(self):
        audio.lid_closed = lambda: True
        self.assertIsNone(audio.builtin_input_index())

    def test_micro_integre_muet_repli_sur_le_casque(self):
        r = audio.Recorder()
        r._stream = object()
        r.using_builtin = True
        r._silent_since = time.time() - 1.0
        r._close_stream = lambda: setattr(r, "_stream", None)
        opened = {}
        audio.open_input_stream = lambda **kw: opened.update(kw) or object()
        audio.default_input_id = lambda: 1
        audio.sd.query_devices = lambda *a, **k: (
            {"name": "AirPods de louqman"} if k.get("kind") == "input" else self.devices)
        r._open_impl()
        self.assertGreater(r._builtin_bad_until, time.time())
        self.assertIsNone(opened["device"])   # micro par défaut = le casque
        self.assertFalse(r.using_builtin)


if __name__ == "__main__":
    unittest.main()
