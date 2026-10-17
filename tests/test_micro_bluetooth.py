"""Casque Bluetooth par défaut : on dicte avec le micro intégré, pas avec celui du casque."""
import unittest

from localflow import audio

BLUE, BUILTIN, USB = (int.from_bytes(t, "big") for t in (b"blue", b"bltn", b"usb "))
MAC, AIRPODS, YETI = ("Micro MacBook Air", BUILTIN), ("AirPods", BLUE), ("Yeti", USB)


class MicroBluetooth(unittest.TestCase):
    def test_airpods_par_defaut_donne_le_micro_du_mac(self):
        self.assertEqual(audio.pick_input([AIRPODS, MAC], "AirPods"), "Micro MacBook Air")

    def test_micro_non_bluetooth_reste_le_micro_par_defaut(self):
        self.assertIsNone(audio.pick_input([AIRPODS, MAC], "Micro MacBook Air"))
        self.assertIsNone(audio.pick_input([AIRPODS, MAC, YETI], "Yeti"))

    def test_sans_micro_integre_on_garde_le_casque(self):
        self.assertIsNone(audio.pick_input([AIRPODS], "AirPods"))


    def test_capot_ferme_on_garde_le_casque(self):
        # Capot fermé, le micro intégré est coupé : le forcer donnerait du silence.
        self.assertIsNone(audio.pick_input([AIRPODS, MAC], "AirPods", lid_closed=True))

if __name__ == "__main__":
    unittest.main()
