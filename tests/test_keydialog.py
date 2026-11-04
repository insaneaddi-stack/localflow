"""Saisie de la clé Voxtral : fenêtres, presse-papiers et trousseau sont remplacés par des factices."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import keydialog

CLE = "aBcD1234eFgH5678iJkL9012mNoP3456"


def parcours(boutons, presse_papiers, valide=True):
    """Joue la boucle ; renvoie (résultat, messages affichés, clés enregistrées, notifications, console ouverte)."""
    boutons, presse_papiers = list(boutons), list(presse_papiers)
    vus, gardees, notifs, console = [], [], [], []
    res = keydialog.ask_key(
        notify=lambda t, m: notifs.append((t, m)),
        dialog=lambda msg, b, d: (vus.append(msg), boutons.pop(0))[1],
        clipboard=lambda: presse_papiers.pop(0),
        open_console=lambda: console.append(1),
        check=lambda k: valide,
        save=gardees.append,
    )
    return res, vus, gardees, notifs, console


class Saisie(unittest.TestCase):
    def test_copier_puis_coller_enregistre(self):
        res, _, gardees, notifs, _ = parcours([keydialog.COLLER], [CLE + "\n"])
        self.assertTrue(res)
        self.assertEqual(gardees, [CLE])
        self.assertEqual(notifs[0][0], "Clé Voxtral enregistrée")

    def test_plus_tard_n_enregistre_rien(self):
        res, _, gardees, _, _ = parcours([keydialog.PLUS_TARD], [])
        self.assertFalse(res)
        self.assertEqual(gardees, [])

    def test_fenetre_fermee_vaut_plus_tard(self):
        self.assertFalse(parcours([""], [])[0])

    def test_presse_papiers_sans_cle_on_redemande(self):
        res, vus, gardees, _, _ = parcours([keydialog.COLLER, keydialog.COLLER], ["bonjour tout le monde", CLE])
        self.assertTrue(res)
        self.assertEqual(gardees, [CLE])
        self.assertIn("ne contient pas de clé", vus[1])

    def test_cle_refusee_par_mistral_pas_enregistree(self):
        res, vus, gardees, _, _ = parcours([keydialog.COLLER, keydialog.PLUS_TARD], [CLE], valide=False)
        self.assertFalse(res)
        self.assertEqual(gardees, [])
        self.assertIn("refuse", vus[1])

    def test_pas_de_reseau_la_cle_est_gardee_quand_meme(self):
        res, _, gardees, notifs, _ = parcours([keydialog.COLLER], [CLE], valide=None)
        self.assertTrue(res)
        self.assertEqual(gardees, [CLE])
        self.assertIn("pas pu la vérifier", notifs[0][1])

    def test_ouvrir_la_console_puis_coller(self):
        res, vus, _, _, console = parcours([keydialog.CONSOLE, keydialog.COLLER], [CLE])
        self.assertTrue(res)
        self.assertEqual(console, [1])
        self.assertEqual(vus[0], vus[1])   # même consigne après un détour par la console


if __name__ == "__main__":
    unittest.main()
