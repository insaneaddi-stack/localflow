"""L'arithmétique des dates ne passe jamais par le LLM : elle est ici, et testée.

Tout ce qui suit est pur. `now` est toujours fourni, jamais lu à l'horloge —
c'est ce qui permet de rejouer un dimanche soir ou un passage à l'heure d'hiver.
"""

import datetime
import os
import sys
import unittest
from datetime import timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow.calendar_intent import (
    build_event, clean_title, extract_json, humanize, resolve_day, resolve_when,
)

TZ = ZoneInfo("Europe/Paris")


def ms(annee, mois, jour, h=0, mn=0):
    """Horodatage attendu, exprimé en UTC explicite : aucun aller-retour par
    zoneinfo, sinon le test validerait le code avec le code."""
    return int(datetime.datetime(annee, mois, jour, h, mn, tzinfo=timezone.utc).timestamp() * 1000)


class ExtraitJson(unittest.TestCase):
    def test_nu(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_entoure_de_bavardage(self):
        self.assertEqual(extract_json('Voici :\n```json\n{"a": 1}\n```\nVoilà.'), {"a": 1})

    def test_apres_reflexion(self):
        self.assertEqual(extract_json('<think>hmm</think>{"a": 2}'), {"a": 2})

    def test_accolade_dans_une_chaine(self):
        self.assertEqual(extract_json('{"titre": "Point } final"}'), {"titre": "Point } final"})

    def test_genere_coupe(self):
        self.assertIsNone(extract_json('{"titre": "Réunion", "jour":'))

    def test_rien_a_prendre(self):
        for cas in ("", None, "désolé, je ne peux pas", "[1, 2]"):
            self.assertIsNone(extract_json(cas), msg=repr(cas))


class ResoutLeJour(unittest.TestCase):
    # mercredi 9 septembre 2026
    TODAY = datetime.date(2026, 9, 9)

    def test_reperes(self):
        self.assertEqual(resolve_day("aujourd'hui", self.TODAY), self.TODAY)
        self.assertEqual(resolve_day("demain", self.TODAY), datetime.date(2026, 9, 10))
        self.assertEqual(resolve_day("après-demain", self.TODAY), datetime.date(2026, 9, 11))

    def test_prochain_jour_nomme(self):
        self.assertEqual(resolve_day("vendredi", self.TODAY), datetime.date(2026, 9, 11))
        self.assertEqual(resolve_day("lundi", self.TODAY), datetime.date(2026, 9, 14))
        self.assertEqual(resolve_day("lundi prochain", self.TODAY), datetime.date(2026, 9, 14))

    def test_le_jour_meme_renvoie_a_la_semaine_suivante(self):
        """Dire « mercredi » un mercredi ne veut jamais dire « dans dix minutes »."""
        self.assertEqual(resolve_day("mercredi", self.TODAY), datetime.date(2026, 9, 16))

    def test_date_explicite(self):
        self.assertEqual(resolve_day("2026-09-18", self.TODAY), datetime.date(2026, 9, 18))

    def test_illisible(self):
        for cas in (None, "", "un de ces quatre", "2026-13-45", "mardigras"):
            self.assertIsNone(resolve_day(cas, self.TODAY), msg=repr(cas))


class ResoutLeMoment(unittest.TestCase):
    NOW = datetime.datetime(2026, 9, 9, 18, 46, tzinfo=TZ)   # mercredi soir

    def test_demain_14h_est_bien_midi_utc(self):
        """Le 10 septembre, Paris est à UTC+2 : 14 h locales = 12 h UTC."""
        q = resolve_when("demain", "14:00", 60, False, self.NOW)
        self.assertEqual(q["start_ms"], ms(2026, 9, 10, 12, 0))
        self.assertEqual(q["end_ms"], ms(2026, 9, 10, 13, 0))
        self.assertFalse(q["all_day"])
        self.assertEqual(q["start_timezone"], "Europe/Paris")

    def test_heure_dite_a_la_francaise(self):
        for ecriture in ("14h30", "14:30", "14 h 30", "14.30"):
            q = resolve_when("demain", ecriture, 60, False, self.NOW)
            self.assertEqual(q["start_ms"], ms(2026, 9, 10, 12, 30), msg=ecriture)

    def test_journee_entiere_est_minuit_utc(self):
        q = resolve_when("demain", None, 60, True, self.NOW)
        self.assertTrue(q["all_day"])
        self.assertEqual(q["start_ms"], ms(2026, 9, 10))
        self.assertEqual(q["end_ms"], q["start_ms"])   # TimeTree : début = fin
        self.assertEqual(q["start_timezone"], "UTC")

    def test_sans_heure_bascule_en_journee_entiere(self):
        q = resolve_when("vendredi", None, 60, False, self.NOW)
        self.assertTrue(q["all_day"])

    def test_passage_a_l_heure_d_hiver(self):
        """Le 25 octobre 2026 Paris repasse à UTC+1 : 14 h locales = 13 h UTC."""
        avant = resolve_when("2026-10-24", "14:00", 60, False, self.NOW)
        apres = resolve_when("2026-10-26", "14:00", 60, False, self.NOW)
        self.assertEqual(avant["start_ms"], ms(2026, 10, 24, 12, 0))
        self.assertEqual(apres["start_ms"], ms(2026, 10, 26, 13, 0))

    def test_duree(self):
        q = resolve_when("demain", "09:00", 120, False, self.NOW)
        self.assertEqual(q["end_ms"] - q["start_ms"], 120 * 60 * 1000)

    def test_duree_aberrante_retombe_sur_une_heure(self):
        for mauvaise in (0, -30, 5000, "deux heures", None):
            q = resolve_when("demain", "09:00", mauvaise, False, self.NOW)
            self.assertEqual(q["end_ms"] - q["start_ms"], 60 * 60 * 1000, msg=repr(mauvaise))

    def test_refuse_le_passe_lointain(self):
        self.assertIsNone(resolve_when("2020-01-01", "10:00", 60, False, self.NOW))

    def test_tolere_ce_matin(self):
        """Dicter à 18 h un créneau de 9 h le jour même reste légitime."""
        q = resolve_when("aujourd'hui", "09:00", 60, False, self.NOW)
        self.assertIsNotNone(q)

    def test_refuse_le_futur_lointain(self):
        self.assertIsNone(resolve_when("2030-01-01", "10:00", 60, False, self.NOW))

    def test_jour_incomprehensible(self):
        self.assertIsNone(resolve_when("un de ces jours", "10:00", 60, False, self.NOW))


class NettoieLeTitre(unittest.TestCase):
    def test_retire_la_commande(self):
        self.assertEqual(clean_title("mets-moi un événement déjeuner avec César"),
                         "Déjeuner avec César")
        self.assertEqual(clean_title("ajoute un rendez-vous dentiste"), "Dentiste")

    def test_majuscule_et_ponctuation(self):
        self.assertEqual(clean_title("  point d'équipe.  "), "Point d'équipe")

    def test_vide(self):
        for cas in (None, "", "   ", "mets-moi un événement"):
            self.assertIsNone(clean_title(cas), msg=repr(cas))

    def test_tronque(self):
        long = "a" * 400
        self.assertLessEqual(len(clean_title(long)), 201)


class ConstruitLEvenement(unittest.TestCase):
    NOW = datetime.datetime(2026, 9, 9, 18, 46, tzinfo=TZ)

    BRUT = {"titre": "Présenter le directeur commercial à César", "jour": "demain",
            "heure": "14:00", "duree_min": 60, "journee_entiere": False}

    def test_cas_nominal(self):
        ev = build_event(self.BRUT, self.NOW)
        self.assertEqual(ev["title"], "Présenter le directeur commercial à César")
        self.assertEqual(ev["start_ms"], ms(2026, 9, 10, 12, 0))
        self.assertEqual(ev["_libelle"], "Demain 14:00")
        self.assertNotIn("_debut_local", ev)   # pas d'objet datetime jusqu'à TimeTree

    def test_sans_titre(self):
        self.assertIsNone(build_event({**self.BRUT, "titre": ""}, self.NOW))

    def test_sans_jour(self):
        self.assertIsNone(build_event({**self.BRUT, "jour": None}, self.NOW))

    def test_pas_un_dict(self):
        for cas in (None, "demain 14h", [], 42):
            self.assertIsNone(build_event(cas, self.NOW), msg=repr(cas))


class Libelle(unittest.TestCase):
    NOW = datetime.datetime(2026, 9, 9, 18, 46, tzinfo=TZ)

    def test_reperes_proches(self):
        d = lambda j, h=14: datetime.datetime(2026, 9, j, h, 0, tzinfo=TZ)
        self.assertEqual(humanize(d(9), False, self.NOW), "Aujourd'hui 14:00")
        self.assertEqual(humanize(d(10), False, self.NOW), "Demain 14:00")
        self.assertEqual(humanize(d(11), False, self.NOW), "Après-demain 14:00")
        self.assertEqual(humanize(d(14), False, self.NOW), "Lundi 14:00")

    def test_au_dela_de_la_semaine(self):
        d = datetime.datetime(2026, 9, 18, 7, 0, tzinfo=TZ)
        self.assertEqual(humanize(d, False, self.NOW), "Vendredi 18 sept. 07:00")

    def test_journee_entiere_sans_heure(self):
        d = datetime.datetime(2026, 9, 10, 0, 0, tzinfo=TZ)
        self.assertEqual(humanize(d, True, self.NOW), "Demain")


if __name__ == "__main__":
    unittest.main()
