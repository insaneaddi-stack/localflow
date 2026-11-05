"""Saisie de la clé Voxtral : fenêtres, presse-papiers et trousseau sont remplacés par des factices."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import keydialog

CLE = "aBcD1234eFgH5678iJkL9012mNoP3456"


def parcours(boutons, champs, valide=True):
    """Joue la boucle ; renvoie (résultat, messages affichés, clés enregistrées, notifications, console ouverte)."""
    boutons, champs = list(boutons), list(champs)
    vus, gardees, notifs, console = [], [], [], []
    res = keydialog.ask_key(
        notify=lambda t, m: notifs.append((t, m)),
        dialog=lambda msg, b, d: (vus.append(msg), (boutons.pop(0), champs.pop(0) if champs else ""))[1],
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
        self.assertIn("pas une clé", vus[1])

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
        res, vus, _, _, console = parcours([keydialog.CONSOLE, keydialog.COLLER], ["", CLE])
        self.assertTrue(res)
        self.assertEqual(console, [1])
        self.assertEqual(vus[0], vus[1])   # même consigne après un détour par la console


class CollerLaCle(unittest.TestCase):
    def test_resultats(self):
        gardees = []
        def coller(presse, valide):
            return keydialog.paste_key(lambda: presse, lambda k: valide, gardees.append)
        self.assertEqual(coller(CLE, True), "ok")
        self.assertEqual(coller(CLE, None), "unverified")
        self.assertEqual(coller("bonjour tout le monde", True), "not_a_key")
        self.assertEqual(coller(CLE, False), "refused")
        self.assertEqual(gardees, [CLE, CLE])   # seules les clés acceptées (ou invérifiables) sont rangées

    def test_cle_au_format_mstrl_acceptee(self):
        from localflow import voxtral
        self.assertTrue(voxtral.looks_like_key("mstrl_AAAAbbbbCCCCddddEEEEffffGGGG_00hhH1"))

    def test_lecture_de_la_reponse_osascript(self):
        self.assertEqual(keydialog._parse(f"button returned:Valider, text returned:{CLE}\n"), ("Valider", CLE))
        self.assertEqual(keydialog._parse(f"button returned:Valider, text returned: {CLE} , gave up:false\n"), ("Valider", CLE))
        self.assertEqual(keydialog._parse("button returned:, text returned:, gave up:true\n"), ("", ""))


if __name__ == "__main__":
    unittest.main()
