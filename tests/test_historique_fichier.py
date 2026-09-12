"""L'historique vit dans son propre fichier, en append.

Il grossit de ~80 lignes par jour ; le fichier de réglages, lui, ne bouge
presque jamais. Les séparer permet de ne plus réécrire tout le JSON à chaque
dictée, et de lever le plafond de 300 qui ne couvrait même pas quatre jours.

    python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import config as cfg


class HistoriqueSurDisque(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self._vrais = (cfg.CONFIG_PATH, cfg.HISTORY_PATH)
        cfg.CONFIG_PATH = os.path.join(self.dossier, "reglages.json")
        cfg.HISTORY_PATH = os.path.join(self.dossier, "historique.jsonl")

    def tearDown(self):
        cfg.CONFIG_PATH, cfg.HISTORY_PATH = self._vrais
        shutil.rmtree(self.dossier, ignore_errors=True)

    def ecrire_reglages(self, data):
        with open(cfg.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def lignes_du_fichier(self):
        with open(cfg.HISTORY_PATH, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    # ---- déménagement ----

    def test_ancien_historique_demenage_et_garde_son_ordre(self):
        self.ecrire_reglages({"history": [
            {"t": "2026-09-12T10:00:00", "text": "le plus récent", "app": "Chrome"},
            {"t": "2026-09-11T10:00:00", "text": "le plus ancien", "app": "Code"},
        ]})
        c = cfg.Config()
        self.assertEqual([e["text"] for e in c.history], ["le plus récent", "le plus ancien"])
        # la clé a quitté les réglages, et le fichier garde le plus ancien en tête
        with open(cfg.CONFIG_PATH, encoding="utf-8") as f:
            self.assertNotIn("history", json.load(f))
        self.assertEqual([e["text"] for e in self.lignes_du_fichier()],
                         ["le plus ancien", "le plus récent"])

    def test_demenagement_ne_double_pas_au_deuxieme_lancement(self):
        self.ecrire_reglages({"history": [{"t": "2026-09-12T10:00:00", "text": "une", "app": ""}]})
        cfg.Config()
        c = cfg.Config()
        self.assertEqual(len(c.history), 1)
        self.assertEqual(len(self.lignes_du_fichier()), 1)

    # ---- écriture ----

    def test_ajout_en_tete_et_relecture_identique(self):
        c = cfg.Config()
        for mot in ("un", "deux", "trois"):
            c.add_history(mot, app="Chrome")
        self.assertEqual([e["text"] for e in c.history], ["trois", "deux", "un"])
        self.assertEqual([e["text"] for e in cfg.Config().history], ["trois", "deux", "un"])

    def test_un_texte_repete_remonte_sans_se_dupliquer(self):
        c = cfg.Config()
        for mot in ("Parfait.", "autre chose", "Parfait."):
            c.add_history(mot)
        self.assertEqual([e["text"] for e in c.history], ["Parfait.", "autre chose"])
        self.assertEqual([e["text"] for e in cfg.Config().history], ["Parfait.", "autre chose"])

    def test_les_reglages_ne_grossissent_plus_a_chaque_dictee(self):
        c = cfg.Config()
        c.save()
        avant = os.path.getsize(cfg.CONFIG_PATH)
        for i in range(50):
            c.add_history("une dictée assez longue pour peser, numéro %d" % i)
        self.assertEqual(os.path.getsize(cfg.CONFIG_PATH), avant)
        self.assertEqual(len(c.history), 50)

    # ---- robustesse ----

    def test_une_ligne_abimee_n_emporte_pas_les_autres(self):
        with open(cfg.HISTORY_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": "2026-09-12T09:00:00", "text": "avant"}) + "\n")
            f.write('{"t": "tronqu\n')                       # coupure d'écriture
            f.write(json.dumps({"t": "2026-09-12T11:00:00", "text": "après"}) + "\n")
        self.assertEqual([e["text"] for e in cfg.Config().history], ["après", "avant"])

    def test_plafond_en_memoire(self):
        vrai = cfg.HISTORY_MAX
        cfg.HISTORY_MAX = 5
        try:
            c = cfg.Config()
            for i in range(20):
                c.add_history(f"dictée {i}")
            self.assertEqual(len(c.history), 5)
            self.assertEqual(c.history[0]["text"], "dictée 19")
        finally:
            cfg.HISTORY_MAX = vrai

    def test_effacer(self):
        c = cfg.Config()
        c.add_history("quelque chose")
        c.clear_history()
        self.assertEqual(c.history, [])
        self.assertEqual(cfg.Config().history, [])

    def test_sans_fichier_du_tout(self):
        self.assertEqual(cfg.Config().history, [])


if __name__ == "__main__":
    unittest.main()
