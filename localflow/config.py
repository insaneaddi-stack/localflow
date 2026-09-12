"""Préférences persistantes (~/.localflow.json)."""

import datetime
import json
import os

CONFIG_PATH = os.path.expanduser("~/.localflow.json")
# L'historique vit à part, en append : il grossit tous les jours, alors que le
# reste du fichier de réglages ne change presque jamais. Les garder ensemble
# obligeait à réécrire tout le JSON à chaque dictée — et imposait un plafond
# si bas (300) qu'à 80 dictées par jour il ne restait pas quatre jours.
HISTORY_PATH = os.path.expanduser("~/.localflow.history.jsonl")

DEFAULTS = {
    "cleanup_enabled": False,    # Qwen : +0,8 s ; Qwen3-ASR sort déjà un texte ponctué
    "sounds_enabled": True,
    "live_enabled": False,       # transcription en direct (streaming)
    "live_paste_fast": False,    # coller le texte du direct : Qwen3-ASR décode par blocs et
                                 # coupe les phrases — le batch ne met qu'~1 s, ça ne vaut plus le coup
    "tone_auto": True,           # adapter le ton à l'app active
    "auto_update": True,         # mise à jour automatique en arrière-plan
    "mic_always_on": False,      # micro ouvert en permanence (pré-roll permanent) ; sinon ouvert à la demande
    "meeting_auto_detect": True, # proposer d'enregistrer quand une app d'appel utilise le micro
    "meeting_summary_model": "qwen-1.7b",   # ou "qwen-4b" (~2,5 Go, meilleurs résumés)
    "meeting_language": "fr",    # "fr" / "en" / "" (auto) : langue figée pour les réunions
    "meeting_folder": "",        # vide = ~/Documents/LocalFlow Réunions
    "meeting_keep_audio": True,  # garder l'audio .m4a à côté du .md
    "calendar_enabled": False,   # fn+⇧ envoie la dictée vers TimeTree au lieu de la taper
    "calendar_id": "1001056215", # Calendrier Louqman
    "calendar_preview_s": 3.0,   # aperçu annulable (Esc) avant que l'événement parte
    "calendar_mcp_path": "~/Desktop/Projects/TIMETREE/dist/index.js",   # serveur MCP TimeTree
    "decode_times": [],          # [[durée audio s, temps de décodage s]] : calibre la barre de progression
    "stats": {},                 # {"YYYY-MM-DD": {"words": n, "dictations": n, "audio_s": s}}
}

HISTORY_MAX = 20000       # ~8 mois à 80 dictées par jour
HISTORY_TRIM = 30000      # au-delà, on réécrit le fichier (au démarrage seulement)
TYPING_WPM = 40.0  # vitesse de frappe moyenne pour estimer le temps gagné

def _bool_prop(key):
    def fget(self):
        return bool(self.data.get(key, DEFAULTS[key]))

    def fset(self, value):
        self.data[key] = bool(value)
        self.save()

    return property(fget, fset)

class Config:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                self.data.update(stored)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        # migration : ancien historique = liste de chaînes
        hist = self.data.get("history") or []
        if hist and isinstance(hist[0], str):
            now = datetime.datetime.now().isoformat(timespec="seconds")
            self.data["history"] = [{"t": now, "text": t, "app": ""} for t in hist]
        self._purger_coupures_apprises()
        self._history = self._charger_historique()
        self._demenager_historique()

    # ---- fichier d'historique (append) ----

    def _charger_historique(self):
        """Lit le .jsonl et rend la liste en mémoire : plus récent d'abord,
        un seul exemplaire par texte."""
        lignes = []
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                for ligne in f:
                    ligne = ligne.strip()
                    if not ligne:
                        continue
                    try:
                        e = json.loads(ligne)
                    except json.JSONDecodeError:
                        continue   # une ligne tronquée n'emporte pas les autres
                    if isinstance(e, dict) and e.get("text"):
                        lignes.append(e)
        except OSError:
            return []
        vus, out = set(), []
        for e in reversed(lignes):
            if e["text"] in vus:
                continue
            vus.add(e["text"])
            out.append(e)
            if len(out) >= HISTORY_MAX:
                break
        # Le fichier garde les doublons que la lecture écarte : on ne le réécrit
        # que quand il déborde vraiment, et jamais en cours de route.
        if len(lignes) > HISTORY_TRIM:
            self._reecrire_historique(out)
        return out

    def _reecrire_historique(self, entrees):
        """Réécriture atomique, du plus ancien au plus récent."""
        tmp = HISTORY_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for e in reversed(entrees):
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, HISTORY_PATH)
        except OSError:
            pass

    def _demenager_historique(self):
        """Sort l'historique du fichier de réglages, une fois pour toutes."""
        anciennes = self.data.pop("history", None)
        if not anciennes:
            return
        connus = {e["text"] for e in self._history}
        a_ecrire = [e for e in reversed(anciennes)
                    if isinstance(e, dict) and e.get("text") and e["text"] not in connus]
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                for e in a_ecrire:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            return
        self._history = self._charger_historique()
        self.save()

    def _purger_coupures_apprises(self):
        """Jette les corrections qui ne sont qu'un mot coupé en deux.

        learning.py ne les apprend plus, mais celles déjà en base attendaient
        d'atteindre le seuil pour se mettre à taper « pro duit » à la place de
        « produit ». On les retire une bonne fois.
        """
        from .learning import est_un_mot_coupe

        learned = self.data.get("learned")
        if not isinstance(learned, dict):
            return
        morts = [bad for bad, e in learned.items()
                 if isinstance(e, dict) and est_un_mot_coupe(bad, str(e.get("to", "")))]
        for bad in morts:
            del learned[bad]
        if morts:
            self.save()

    def save(self):
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)  # écriture atomique : jamais de JSON corrompu
        except OSError:
            pass

    cleanup_enabled = _bool_prop("cleanup_enabled")
    sounds_enabled = _bool_prop("sounds_enabled")
    live_enabled = _bool_prop("live_enabled")
    live_paste_fast = _bool_prop("live_paste_fast")
    tone_auto = _bool_prop("tone_auto")
    auto_update = _bool_prop("auto_update")
    mic_always_on = _bool_prop("mic_always_on")
    meeting_auto_detect = _bool_prop("meeting_auto_detect")
    meeting_keep_audio = _bool_prop("meeting_keep_audio")
    calendar_enabled = _bool_prop("calendar_enabled")

    def _str_prop(key):
        def fget(self):
            return str(self.data.get(key, DEFAULTS[key]) or "")

        def fset(self, value):
            self.data[key] = str(value or "")
            self.save()

        return property(fget, fset)

    meeting_summary_model = _str_prop("meeting_summary_model")
    meeting_language = _str_prop("meeting_language")
    meeting_folder = _str_prop("meeting_folder")
    calendar_id = _str_prop("calendar_id")

    @property
    def calendar_mcp_path(self):
        return os.path.expanduser(str(self.data.get("calendar_mcp_path")
                                      or DEFAULTS["calendar_mcp_path"]))

    @property
    def calendar_preview_s(self):
        try:
            return max(0.0, float(self.data.get("calendar_preview_s", DEFAULTS["calendar_preview_s"])))
        except (TypeError, ValueError):
            return DEFAULTS["calendar_preview_s"]

    # ---- historique ----

    @property
    def history(self):
        return list(self._history)

    def add_history(self, text, app=""):
        entry = {
            "t": datetime.datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "app": app,
        }
        self._history = [entry] + [e for e in self._history if e.get("text") != text]
        del self._history[HISTORY_MAX:]
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def clear_history(self):
        self._history = []
        try:
            os.remove(HISTORY_PATH)
        except OSError:
            pass

    # ---- statistiques ----

    def add_stat(self, words: int, audio_s: float):
        day = datetime.date.today().isoformat()
        stats = self.data.setdefault("stats", {})
        d = stats.setdefault(day, {"words": 0, "dictations": 0, "audio_s": 0.0})
        d["words"] += int(words)
        d["dictations"] += 1
        d["audio_s"] += float(audio_s)
        self.save()

    def weekly(self):
        """7 derniers jours (du plus ancien à aujourd'hui) : [(date, words, dictations)]."""
        stats = self.data.get("stats", {})
        today = datetime.date.today()
        out = []
        for k in range(6, -1, -1):
            d = today - datetime.timedelta(days=k)
            e = stats.get(d.isoformat(), {})
            out.append((d, int(e.get("words", 0)), int(e.get("dictations", 0))))
        return out

    def top_apps(self, n=3, days=7):
        """Apps les plus dictées sur `days` jours, d'après l'historique : [(app, count)]."""
        since = datetime.datetime.now() - datetime.timedelta(days=days)
        counts = {}
        for e in self.history:
            try:
                if datetime.datetime.fromisoformat(e.get("t", "")) < since:
                    continue
            except Exception:
                pass
            app = e.get("app") or "—"
            counts[app] = counts.get(app, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])[:n]

    def stats_summary(self):
        """{'today': {...}, 'week': {...}, 'all': {...}} avec minutes gagnées."""
        stats = self.data.get("stats", {})
        today = datetime.date.today()
        out = {}
        for label, days in (("today", 0), ("week", 6), ("all", None)):
            agg = {"words": 0, "dictations": 0, "audio_s": 0.0}
            for day, d in stats.items():
                try:
                    age = (today - datetime.date.fromisoformat(day)).days
                except ValueError:
                    continue
                if days is not None and age > days:
                    continue
                for k in agg:
                    agg[k] += d.get(k, 0)
            typing_min = agg["words"] / TYPING_WPM
            agg["saved_min"] = max(0.0, typing_min - agg["audio_s"] / 60.0)
            out[label] = agg
        return out
