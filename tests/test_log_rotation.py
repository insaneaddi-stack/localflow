"""~/.localflow.log ne grossit plus sans fin : au-delà du plafond, il passe en .1."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from localflow import app
except ImportError as exc:
    raise unittest.SkipTest(f"localflow.app non importable ici : {exc}")


class Rotation(unittest.TestCase):
    def test_plafond_puis_une_sauvegarde(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "lf.log")
            with mock.patch.object(app, "LOG_PATH", log), mock.patch.object(app, "LOG_MAX_BYTES", 200):
                for i in range(30):
                    app._log(f"ligne {i:02d} " + "x" * 20)
            self.assertTrue(os.path.exists(log + ".1"))
            self.assertLessEqual(os.path.getsize(log), 200 + 60)
            self.assertLessEqual(os.path.getsize(log + ".1"), 200 + 60)
            self.assertEqual(sorted(os.listdir(d)), ["lf.log", "lf.log.1"])   # une seule sauvegarde


if __name__ == "__main__":
    unittest.main()
