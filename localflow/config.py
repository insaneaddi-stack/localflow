"""Préférences persistantes (~/.localflow.json)."""

import datetime
import json
import os

CONFIG_PATH = os.path.expanduser("~/.localflow.json")

DEFAULTS = {
    "cleanup_enabled": False,    # Qwen : +1 s ; Qwen3-ASR sort déjà un texte ponctué
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
    "decode_times": [],          # [[durée audio s, temps de décodage s]] : calibre la barre de progression
    "history": [],               # [{"t": iso, "text": str, "app": str}], plus récent en premier
    "stats": {},                 # {"YYYY-MM-DD": {"words": n, "dictations": n, "audio_s": s}}
}

HISTORY_MAX = 300
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

    # ---- historique ----

    @property
    def history(self):
        return list(self.data.get("history", []))

    def add_history(self, text, app=""):
        entry = {
            "t": datetime.datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "app": app,
        }
        history = [entry] + [e for e in self.history if e.get("text") != text]
        self.data["history"] = history[:HISTORY_MAX]
        self.save()

    def clear_history(self):
        self.data["history"] = []
        self.save()

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
