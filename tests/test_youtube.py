"""Sous-titres YouTube : les auto-sous-titres répètent chaque ligne en défilant."""
import unittest

from localflow.youtube import YT_RE, vtt_to_text


class Youtube(unittest.TestCase):
    def test_lignes_repetees_fusionnees(self):
        v = ("WEBVTT\n\n00:00.000 --> 00:01.000\nbonjour à tous\n\n00:01.000 --> 00:02.000\n"
             "bonjour à tous\naujourd'hui on parle\n\n00:02.000 --> 00:03.000\naujourd'hui on parle\nde &amp; dictée")
        self.assertEqual(vtt_to_text(v), "bonjour à tous aujourd'hui on parle de & dictée")

    def test_liens_reconnus(self):
        for u in ("https://www.youtube.com/watch?v=HlpuFFsnadY", "https://youtu.be/HlpuFFsnadY",
                  "https://www.youtube.com/shorts/abcdefgh"):
            self.assertTrue(YT_RE.match(u), u)
        self.assertFalse(YT_RE.match("https://www.youtube.com/@chaine"))


if __name__ == "__main__":
    unittest.main()
