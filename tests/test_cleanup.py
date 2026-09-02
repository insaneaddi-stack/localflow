"""Corpus de dictées réelles pour le moteur de nettoyage déterministe.

    python3 -m unittest discover -s tests

Aucune dépendance : cleanup_rules() est du Python pur, pas besoin de MLX.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow.cleanup import _guard_ok, _vocab_keys, cleanup_rules


class Hesitations(unittest.TestCase):
    CASES = [
        ("Alors euh du coup je voulais te dire que le projet avance bien",
         "Alors du coup je voulais te dire que le projet avance bien"),
        ("Um, so I think we should uh probably ship it tomorrow",
         "So I think we should probably ship it tomorrow"),
        ("Bonjour, euh, est-ce que tu peux m'envoyer le fichier",
         "Bonjour, est-ce que tu peux m'envoyer le fichier"),
        # la ponctuation finale portée par l'hésitation est récupérée
        ("Je disais euh.", "Je disais."),
        ("bah je crois que oui", "Je crois que oui"),
        ("ben je crois que oui", "Je crois que oui"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(cleanup_rules(raw), want, msg=raw)


class NeverTouch(unittest.TestCase):
    """Faux positifs interdits : ces phrases doivent ressortir intactes."""

    CASES = [
        "Nous nous sommes vus hier",
        "C'est très très bien",
        "C'est ce que je veux dire",
        "C'est bien, enfin je crois",
        "Je vais au bureau et je vais au marché",
        "Je pense que c'est bien et je pense que c'est mieux",
        "Ben a appelé ce matin",
        "Tu viens, hein ?",
        "Il y a plus plus de monde que prévu",
        "On y va peut-être demain",
        "Je te rappelle tout de suite, c'est-à-dire dans cinq minutes",
        "Non mais attends, je crois qu'on s'est mal compris sur ce point",
        "En fait je pense qu'on devrait attendre un peu",
        "Il m'a dit non, non et non",
        "On en a parlé hier et on en reparlera demain",
        "Il y a deux options : soit on décale, soit on réduit le périmètre",
        "Merci beaucoup pour ton retour, je regarde ça dans la journée",
    ]

    def test_untouched(self):
        for raw in self.CASES:
            self.assertEqual(cleanup_rules(raw), raw, msg=raw)


class Repetitions(unittest.TestCase):
    CASES = [
        ("le le le fichier", "Le fichier"),
        ("je voulais te dire que que le projet avance",
         "Je voulais te dire que le projet avance"),
        ("il faut que je rappelle il faut que je rappelle le client",
         "Il faut que je rappelle le client"),
        ("Il faut partir. Il faut partir maintenant.",
         "Il faut partir maintenant."),
        ("Um, so I I think we should ship it",
         "So I think we should ship it"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(cleanup_rules(raw), want, msg=raw)


class Retakes(unittest.TestCase):
    """Reprises approximatives : le locuteur recommence sa phrase."""

    CASES = [
        ("j'ai envoyé le fichier j'ai envoyé le bon fichier",
         "J'ai envoyé le bon fichier"),
        ("on se voit vendredi, enfin on se voit jeudi matin",
         "On se voit jeudi matin"),
        ("je te l'envoie mardi, non pardon je te l'envoie mercredi",
         "Je te l'envoie mercredi"),
        ("il faut valider le budget, je veux dire il faut valider le budget final",
         "Il faut valider le budget final"),
        ("le rendez-vous est à trois heures, non plutôt le rendez-vous est à quatre heures",
         "Le rendez-vous est à quatre heures"),
        ("je pars lundi en fait je pars mardi", "Je pars mardi"),
        ("il faut relancer le client, enfin non il faut relancer le client demain",
         "Il faut relancer le client demain"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(cleanup_rules(raw), want, msg=raw)


class Stutters(unittest.TestCase):
    CASES = [
        ("je-je-je voulais dire", "Je voulais dire"),
        ("l- le fichier final", "Le fichier final"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(cleanup_rules(raw), want, msg=raw)


class Typography(unittest.TestCase):
    CASES = [
        ("bonjour ,   comment ça va", "Bonjour, comment ça va"),
        ("je disais ,", "Je disais"),
        ("bonjour. comment ça va", "Bonjour. Comment ça va"),
        ("c'est bon.on y va", "C'est bon. On y va"),
        # l'espace fine avant ? ! ; : vient de l'ASR, on n'y touche pas
        ("tu viens ?", "Tu viens ?"),
        ("are you coming?", "Are you coming?"),
        ("", ""),
        ("   ", ""),
        ("euh", ""),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(cleanup_rules(raw), want, msg=repr(raw))


class LlmGuard(unittest.TestCase):
    """La passe IA n'a le droit que de reponctuer et de retirer du bruit."""

    VOCAB = _vocab_keys(["Noto", "MetaMind"])

    ACCEPT = [
        ("ok donc premier point on valide le budget deuxième point il faut que je rappelle le client",
         "Ok donc premier point, on valide le budget. Deuxième point, il faut que je rappelle le client."),
        ("je voulais euh te dire que que le projet avance",
         "Je voulais te dire que le projet avance."),
        ("nous nous sommes vus hier", "Nous nous sommes vus hier."),
        ("noto est pret", "Noto est prêt."),          # accents et casse : variante proche
    ]
    REJECT = [
        # suppression de mots de contenu
        ("je vais au bureau et je vais au marché", "Je vais au bureau et au marché."),
        ("nous nous sommes vus hier", "Nous sommes vus hier."),
        ("c'est très très bien", "C'est très bien."),
        # réécriture pure et simple
        ("je pense que tu as tort", "Je pense que tu as raison."),
        ("on se voit demain", "On se voit demain à la première heure."),
        ("", "Bonjour !"),
    ]

    def test_accept(self):
        for src, out in self.ACCEPT:
            self.assertTrue(_guard_ok(out, src, self.VOCAB), msg=f"{src!r} -> {out!r}")

    def test_reject(self):
        for src, out in self.REJECT:
            self.assertFalse(_guard_ok(out, src, self.VOCAB), msg=f"{src!r} -> {out!r}")


class Performance(unittest.TestCase):
    LONG = (
        "Alors euh je voulais te faire un point sur le projet parce que je pense "
        "que c'est important qu'on soit tous alignés avant la réunion de vendredi. "
        "Du coup le premier sujet c'est le budget, il faut que je rappelle il faut "
        "que je rappelle le client pour valider l'enveloppe. Ensuite hum il y a la "
        "question du planning, on avait dit fin mars mais je pense qu'on va devoir "
        "décaler d'une semaine, enfin on va devoir décaler de deux semaines en fait. "
        "Le troisième point c'est l'équipe, il nous manque un développeur et euh il "
        "faudrait qu'on lance le recrutement rapidement. Voilà, je te laisse me dire "
        "ce que tu en penses et on se cale un créneau la semaine prochaine pour en "
        "reparler tranquillement tous les deux avec le reste de l'équipe si possible."
    )

    def test_under_5ms(self):
        self.assertGreater(len(self.LONG.split()), 130)
        cleanup_rules(self.LONG)  # chauffe le cache de normalisation
        t0 = time.perf_counter()
        for _ in range(20):
            cleanup_rules(self.LONG)
        ms = (time.perf_counter() - t0) / 20 * 1000
        self.assertLess(ms, 5.0, f"{ms:.2f} ms par appel, budget 5 ms")

    def test_no_words_invented(self):
        """Le moteur ne fait que supprimer : jamais un mot qui n'était pas dans la source."""
        import re
        src = set(re.findall(r"\w+", self.LONG.lower()))
        out = set(re.findall(r"\w+", cleanup_rules(self.LONG).lower()))
        self.assertTrue(out <= src, out - src)


if __name__ == "__main__":
    unittest.main()
