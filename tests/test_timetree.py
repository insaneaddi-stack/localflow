"""Le client MCP : lecture des réponses, reprises, et pas de doublon.

Le serveur MCP réel n'est pas joignable ici (il faudrait un compte TimeTree et
le réseau). On en lance un faux, qui parle le même JSON-RPC sur stdin/stdout et
qu'on pilote pour rejouer les pannes : erreur réseau, refus d'autorisation,
processus qui meurt, réponse perdue après une écriture réussie.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localflow import timetree
from localflow.timetree import TimeTreeMCP, is_retryable, read_result

# Faux serveur MCP. `scenario` décide de ce qu'il fait subir au client.
FAUX_SERVEUR = r'''
import json, sys

scenario = sys.argv[1]
cree = 0

def repond(ident, charge, erreur=False):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, "result": {
        "content": [{"type": "text", "text": json.dumps(charge, ensure_ascii=False)}],
        "isError": erreur}}) + "\n")
    sys.stdout.flush()

for ligne in sys.stdin:
    ligne = ligne.strip()
    if not ligne:
        continue
    msg = json.loads(ligne)
    ident = msg.get("id")
    methode = msg.get("method")
    if methode == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident,
                                     "result": {"protocolVersion": "2024-11-05"}}) + "\n")
        sys.stdout.flush()
        continue
    if ident is None:
        continue
    nom = (msg.get("params") or {}).get("name")
    if nom == "list_calendars":
        repond(ident, {"calendars": [{"id": "123456789", "name": "Mon calendrier"}]})
    elif nom == "get_events":
        if scenario == "perdu-mais-ecrit":
            repond(ident, {"events": [{"title": "Déjeuner avec Paul"}]})
        else:
            repond(ident, {"events": []})
    elif nom == "create_event":
        cree += 1
        if scenario == "ok":
            repond(ident, {"success": True, "event": {"uuid": "abc"}})
        elif scenario == "reseau-puis-ok":
            if cree == 1:
                repond(ident, {"error": "Network error: ECONNREFUSED"}, erreur=True)
            else:
                repond(ident, {"success": True, "event": {"uuid": "abc"}})
        elif scenario == "interdit":
            repond(ident, {"error": "Authentication failed / CSRF token missing"}, erreur=True)
        elif scenario == "perdu-mais-ecrit":
            repond(ident, {"error": "Network error: socket hang up"}, erreur=True)
        elif scenario == "meurt":
            sys.exit(1)
    else:
        repond(ident, {"success": True})
'''

EVENT = {"title": "Déjeuner avec Paul", "start_ms": 1789027200000, "end_ms": 1789030800000,
         "all_day": False, "start_timezone": "Europe/Paris", "end_timezone": "Europe/Paris"}


class ClientDeTest(TimeTreeMCP):
    """Même client, mais qui lance le faux serveur au lieu de node + TimeTree."""

    def __init__(self, script, scenario):
        super().__init__(script)
        self.scenario = scenario
        self.creations = 0

    def _spawn(self):
        self.close()
        self._proc = subprocess.Popen(
            [sys.executable, self.server_path, self.scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()

    def _tool(self, nom, arguments, timeout=timetree.REPONSE_TIMEOUT_S):
        if nom == "create_event":
            self.creations += 1
        return super()._tool(nom, arguments, timeout)


class LitLaReponse(unittest.TestCase):
    """Le serveur ne lève jamais : tout se lit dans le contenu de la réponse."""

    def _reponse(self, charge, erreur=False):
        return {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": json.dumps(charge)}], "isError": erreur}}

    def test_succes(self):
        ok, charge = read_result(self._reponse({"success": True, "event": {"uuid": "x"}}))
        self.assertTrue(ok)
        self.assertEqual(charge["event"]["uuid"], "x")

    def test_is_error(self):
        ok, message = read_result(self._reponse({"error": "Invalid calendar"}, erreur=True))
        self.assertFalse(ok)
        self.assertEqual(message, "Invalid calendar")

    def test_succes_faux_sans_is_error(self):
        """Le piège : transport OK, isError absent, mais success vaut False."""
        ok, message = read_result(self._reponse({"success": False, "error": "refusé"}))
        self.assertFalse(ok)
        self.assertEqual(message, "refusé")

    def test_erreur_jsonrpc(self):
        ok, message = read_result({"jsonrpc": "2.0", "id": 1,
                                   "error": {"code": -32601, "message": "Method not found"}})
        self.assertFalse(ok)
        self.assertIn("Method not found", message)

    def test_reponses_malformees(self):
        for cas in (None, {}, {"result": "texte"}, {"result": {}}):
            ok, _ = read_result(cas)
            self.assertFalse(ok, msg=repr(cas))

    def test_texte_non_json(self):
        rep = {"result": {"content": [{"type": "text", "text": "bonjour"}]}}
        ok, charge = read_result(rep)
        self.assertTrue(ok)
        self.assertEqual(charge, "bonjour")


class Reprises(unittest.TestCase):
    def test_sur_erreur_reseau(self):
        for message in ("Network error: ECONNREFUSED", "getaddrinfo ENOTFOUND",
                        "socket hang up", "fetch failed"):
            self.assertTrue(is_retryable(message), msg=message)

    def test_jamais_sur_un_timeout(self):
        """La requête a pu aboutir : réécrire créerait un doublon."""
        self.assertFalse(is_retryable("pas de réponse du serveur MCP en 75 s"))
        self.assertFalse(is_retryable("Request timeout after 60000ms"))

    def test_jamais_sur_un_refus(self):
        self.assertFalse(is_retryable("Authentication failed / CSRF token missing"))
        self.assertFalse(is_retryable("Invalid calendar"))
        self.assertFalse(is_retryable(""))


class BoutEnBout(unittest.TestCase):
    """Le vrai protocole, contre le faux serveur."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.script = os.path.join(cls.dir.name, "faux_mcp.py")
        with open(cls.script, "w", encoding="utf-8") as f:
            f.write(FAUX_SERVEUR)
        cls._vrai_sleep = timetree.time.sleep
        timetree.time.sleep = lambda s: None      # pas d'attente de backoff en test

    @classmethod
    def tearDownClass(cls):
        timetree.time.sleep = cls._vrai_sleep
        cls.dir.cleanup()

    def client(self, scenario):
        c = ClientDeTest(self.script, scenario)
        self.addCleanup(c.close)
        return c

    def test_chauffe_puis_cree(self):
        c = self.client("ok")
        ok, message = c.warm("123456789")
        self.assertTrue(ok, msg=message)
        ok, message = c.create("123456789", EVENT)
        self.assertTrue(ok, msg=message)
        self.assertEqual(c.creations, 1)

    def test_la_note_est_transmise(self):
        """La phrase dictée doit arriver jusqu'à TimeTree, pas s'arrêter ici."""
        vus = {}
        c = self.client("ok")
        vrai = c._tool
        c._tool = lambda nom, args, timeout=None: (vus.update({nom: args}) or
                                                   vrai(nom, args, timeout or timetree.REPONSE_TIMEOUT_S))
        c.warm("123456789")
        c.create("123456789", dict(EVENT, note="demain 14h déjeuner avec Paul au bureau"))
        self.assertEqual(vus["create_event"]["note"], "demain 14h déjeuner avec Paul au bureau")

    def test_sans_note_pas_de_champ_vide(self):
        vus = {}
        c = self.client("ok")
        vrai = c._tool
        c._tool = lambda nom, args, timeout=None: (vus.update({nom: args}) or
                                                   vrai(nom, args, timeout or timetree.REPONSE_TIMEOUT_S))
        c.warm("123456789")
        c.create("123456789", dict(EVENT, note=""))
        self.assertNotIn("note", vus["create_event"])

    def test_agenda_absent_du_compte(self):
        c = self.client("ok")
        ok, message = c.warm("999999")
        self.assertFalse(ok)
        self.assertIn("introuvable", message)

    def test_erreur_reseau_puis_succes(self):
        c = self.client("reseau-puis-ok")
        self.assertTrue(c.warm("123456789")[0])
        ok, message = c.create("123456789", EVENT)
        self.assertTrue(ok, msg=message)
        self.assertEqual(c.creations, 2)

    def test_refus_ne_rejoue_pas(self):
        """Un CSRF invalide ne se répare pas en réessayant : on s'arrête tout de suite."""
        c = self.client("interdit")
        self.assertTrue(c.warm("123456789")[0])
        ok, message = c.create("123456789", EVENT)
        self.assertFalse(ok)
        self.assertEqual(c.creations, 1)
        self.assertIn("Authentication failed", message)

    def test_reponse_perdue_apres_ecriture_ne_cree_pas_de_doublon(self):
        """Le cas qui compte : TimeTree a bien écrit, la réponse s'est perdue.
        La relecture doit voir l'événement et s'arrêter là."""
        c = self.client("perdu-mais-ecrit")
        self.assertTrue(c.warm("123456789")[0])
        ok, message = c.create("123456789", EVENT)
        self.assertTrue(ok)
        self.assertEqual(message, "déjà créé")
        self.assertEqual(c.creations, 1)      # une seule écriture tentée

    def test_serveur_mort(self):
        c = self.client("meurt")
        self.assertTrue(c.warm("123456789")[0])
        ok, message = c.create("123456789", EVENT)
        self.assertFalse(ok)
        self.assertTrue(message)


class Journal(unittest.TestCase):
    def test_ecrit_une_ligne_rejouable(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "echecs.jsonl")
            vrai, timetree.JOURNAL_ECHECS = timetree.JOURNAL_ECHECS, chemin
            try:
                timetree.log_failure(EVENT, "réseau coupé")
                timetree.log_failure(EVENT, "encore")
            finally:
                timetree.JOURNAL_ECHECS = vrai
            with open(chemin, encoding="utf-8") as f:
                lignes = f.read().strip().split("\n")
            self.assertEqual(len(lignes), 2)
            entree = json.loads(lignes[0])
            self.assertEqual(entree["raison"], "réseau coupé")
            self.assertEqual(entree["event"]["title"], "Déjeuner avec Paul")

    def test_un_disque_plein_ne_casse_rien(self):
        vrai, timetree.JOURNAL_ECHECS = timetree.JOURNAL_ECHECS, "/introuvable/x/y.jsonl"
        try:
            timetree.log_failure(EVENT, "peu importe")   # ne doit pas lever
        finally:
            timetree.JOURNAL_ECHECS = vrai


if __name__ == "__main__":
    unittest.main()
