"""Écriture dans TimeTree, en passant par le serveur MCP du dépôt TIMETREE.

Le serveur ne parle que le stdio JSON-RPC : on le lance en sous-processus, on
échange quelques lignes, on le laisse mourir. Un processus par événement, et
c'est volontaire — le client TypeScript ne sait pas se reconnecter quand sa
session TimeTree expire (`isAuthenticated()` est un booléen local, aucun 401
n'est rattrapé), donc un serveur maintenu en vie finit par répondre 401 jusqu'au
redémarrage. Un processus neuf part toujours d'un login neuf.

Le coût de ce login est payé pendant l'aperçu : `warm()` est appelé dès que la
phrase est comprise, `create()` seulement si l'aperçu n'a pas été annulé.

Sur les reprises : réessayer une écriture à l'aveugle peut créer un doublon si
la première requête est bien arrivée mais que la réponse s'est perdue. Avant
chaque nouvelle tentative, on relit donc l'agenda pour voir si l'événement y est
déjà.
"""

import json
import os
import queue
import subprocess
import threading
import time

JOURNAL_ECHECS = os.path.expanduser("~/.localflow.calendar-failed.jsonl")
SERVICE_TROUSSEAU = "LocalFlow TimeTree"

REPONSE_TIMEOUT_S = 75.0     # le serveur coupe ses propres requêtes à 60 s
TENTATIVES = 3


def credentials():
    """(email, mot de passe) depuis le trousseau macOS, ou (None, None).

    Jamais dans ~/.localflow.json : ce fichier est en clair et synchronisé.
    """
    try:
        mdp = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE_TROUSSEAU, "-w"],
            capture_output=True, text=True, timeout=5)
        if mdp.returncode != 0:
            return None, None
        infos = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE_TROUSSEAU],
            capture_output=True, text=True, timeout=5)
        email = ""
        for ligne in infos.stdout.splitlines():
            if '"acct"' in ligne and "=" in ligne:
                email = ligne.split("=", 1)[1].strip().strip('"')
                break
        return (email or None), (mdp.stdout.strip() or None)
    except (OSError, subprocess.SubprocessError):
        return None, None


def prerequisites(server_path):
    """Ce qui manque pour écrire dans l'agenda, en clair. Liste vide = prêt."""
    manques = []
    if not server_path or not os.path.exists(server_path):
        manques.append(f"serveur MCP introuvable ({server_path or 'chemin vide'}) — "
                       "lance `npm ci && npm run build` dans le dépôt TIMETREE")
    if not _node():
        manques.append("node introuvable dans le PATH de l'agent")
    email, mdp = credentials()
    if not (email and mdp):
        manques.append(f"identifiants absents du trousseau (service « {SERVICE_TROUSSEAU} ») — "
                       "lance ./install-timetree.sh")
    return manques


def _node():
    for chemin in ("/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node"):
        if os.path.exists(chemin):
            return chemin
    return None


def log_failure(event, raison):
    """Un événement qui n'a pas pu partir n'est jamais perdu en silence."""
    try:
        with open(JOURNAL_ECHECS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "raison": raison, "event": event},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_result(reponse):
    """Réponse MCP → (ok, charge utile ou message d'erreur).

    Le serveur ne lève jamais : il renvoie `isError: true` dans le contenu, et
    le texte porte lui-même un JSON avec son propre `success`. Un aller-retour
    réussi ne veut donc pas dire que l'événement existe.
    """
    if not isinstance(reponse, dict):
        return False, "réponse illisible"
    if "error" in reponse:
        err = reponse["error"]
        return False, str(err.get("message", err) if isinstance(err, dict) else err)
    resultat = reponse.get("result")
    if not isinstance(resultat, dict):
        return False, "réponse sans résultat"
    contenu = resultat.get("content") or []
    texte = ""
    for bloc in contenu:
        if isinstance(bloc, dict) and bloc.get("type") == "text":
            texte = bloc.get("text", "")
            break
    if not texte:
        # Un résultat sans contenu n'apprend rien. Sur une écriture, l'appeler
        # « succès » ferait disparaître l'événement sans un mot.
        return False, "réponse vide"
    charge = None
    if texte:
        try:
            charge = json.loads(texte)
        except ValueError:
            charge = None
    if resultat.get("isError"):
        message = texte
        if isinstance(charge, dict):
            message = charge.get("error") or charge.get("message") or texte
        return False, message or "erreur sans message"
    if isinstance(charge, dict) and charge.get("success") is False:
        return False, str(charge.get("error") or charge.get("message") or "échec")
    return True, charge if charge is not None else texte


def is_retryable(message):
    """Vrai seulement si l'on est sûr que rien n'a été écrit côté TimeTree.

    Un timeout n'est PAS dans la liste : la requête a pu aboutir sans que la
    réponse revienne. On préfère alors relire l'agenda plutôt que réécrire.
    """
    m = (message or "").lower()
    return any(marqueur in m for marqueur in (
        "econnrefused", "enotfound", "econnreset", "getaddrinfo",
        "network error", "socket hang up", "processus", "fetch failed",
    ))


class TimeTreeMCP:
    """Un serveur MCP le temps d'un événement."""

    def __init__(self, server_path, log=None):
        self.server_path = server_path
        self._log = log or (lambda *a: None)
        self._proc = None
        self._out = queue.Queue()
        self._id = 0
        self._lock = threading.Lock()

    # ---- cycle de vie ----

    def _spawn(self):
        self.close()   # un serveur mort laisse ses tuyaux ouverts : on les rend d'abord
        email, mdp = credentials()
        if not (email and mdp):
            raise RuntimeError("identifiants TimeTree absents du trousseau")
        node = _node()
        if not node:
            raise RuntimeError("node introuvable")
        env = dict(os.environ, TIMETREE_EMAIL=email, TIMETREE_PASSWORD=mdp)
        self._proc = subprocess.Popen(
            [node, self.server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, text=True, bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True,
                         name="timetree-mcp").start()

    def _pump(self, proc):
        """Le serveur envoie ses journaux sur stderr : stdout ne porte que du JSON-RPC."""
        try:
            for ligne in proc.stdout:
                ligne = ligne.strip()
                if ligne:
                    self._out.put(ligne)
        except (OSError, ValueError):
            pass
        finally:
            self._out.put(None)   # pipe fermé : le processus est mort

    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for tuyau in (proc.stdin, proc.stdout):
            try:
                tuyau.close()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ---- JSON-RPC ----

    def _send(self, message):
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _call(self, methode, params=None, timeout=REPONSE_TIMEOUT_S):
        with self._lock:
            self._id += 1
            ident = self._id
            self._send({"jsonrpc": "2.0", "id": ident, "method": methode,
                        "params": params or {}})
        fin = time.time() + timeout
        while time.time() < fin:
            try:
                ligne = self._out.get(timeout=max(0.1, fin - time.time()))
            except queue.Empty:
                break
            if ligne is None:
                raise RuntimeError("le serveur MCP s'est arrêté (processus mort)")
            try:
                message = json.loads(ligne)
            except ValueError:
                continue
            if message.get("id") == ident:
                return message
            # notification ou réponse d'un autre appel : on continue d'écouter
        raise TimeoutError(f"pas de réponse du serveur MCP en {timeout:.0f} s")

    def _tool(self, nom, arguments, timeout=REPONSE_TIMEOUT_S):
        return read_result(self._call("tools/call",
                                      {"name": nom, "arguments": arguments}, timeout))

    # ---- usage ----

    def warm(self, calendar_id):
        """Lance le serveur, ouvre la session TimeTree, vérifie l'agenda.

        Appelé pendant l'aperçu : le login (~1 s) est payé là, pas au moment où
        l'utilisateur a laissé passer l'événement. Renvoie (ok, message).
        """
        try:
            if not self.alive():
                self._spawn()
                self._call("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "localflow", "version": "1"},
                }, timeout=20)
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            # `list_calendars` force le login : c'est lui qui coûte, autant le
            # payer maintenant. Il valide aussi que l'agenda visé existe encore.
            ok, charge = self._tool("list_calendars", {}, timeout=30)
            if not ok:
                return False, str(charge)
            if isinstance(charge, dict):
                ids = {str(c.get("id")) for c in charge.get("calendars", [])}
                if ids and str(calendar_id) not in ids:
                    return False, f"agenda {calendar_id} introuvable sur ce compte"
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def already_there(self, calendar_id, event):
        """L'événement est-il déjà dans l'agenda ? (avant de réessayer d'écrire)"""
        try:
            ok, charge = self._tool("get_events", {
                "calendar_id": str(calendar_id),
                "start_after": max(0, int(event["start_ms"]) - 60_000),
                "limit": 20,
            }, timeout=30)
            if not ok or not isinstance(charge, dict):
                return False
            for ev in charge.get("events", []):
                if ev.get("title") == event["title"]:
                    return True
        except Exception:
            pass
        return False

    def create(self, calendar_id, event):
        """Crée l'événement. Renvoie (ok, message)."""
        arguments = {
            "calendar_id": int(calendar_id),
            "title": event["title"],
            "start_at": int(event["start_ms"]),
            "end_at": int(event["end_ms"]),
            "all_day": bool(event["all_day"]),
            "start_timezone": event["start_timezone"],
            "end_timezone": event["end_timezone"],
        }
        dernier = ""
        for tentative in range(1, TENTATIVES + 1):
            if tentative > 1:
                # La tentative précédente a pu aboutir sans qu'on l'apprenne.
                if self.already_there(calendar_id, event):
                    self._log("timetree: l'événement était déjà passé, pas de doublon")
                    return True, "déjà créé"
                time.sleep(2 ** (tentative - 2))
            try:
                if not self.alive():
                    ok, message = self.warm(calendar_id)
                    if not ok:
                        dernier = message
                        continue
                ok, charge = self._tool("create_event", arguments)
                if ok:
                    return True, ""
                dernier = str(charge)
            except Exception as exc:
                dernier = str(exc)
            self._log(f"timetree: tentative {tentative}/{TENTATIVES} échouée — {dernier}")
            if not is_retryable(dernier):
                break
        return False, dernier or "échec inconnu"
