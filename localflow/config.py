"""Préférences persistantes (~/.localflow.json)."""

import datetime
import json
import os

CONFIG_PATH = os.path.expanduser("~/.localflow.json")

DEFAULTS = {
    "cleanup_enabled": False,    # Qwen : +1 s ; Whisper sort déjà un texte propre
    "sounds_enabled": True,
    "live_enabled": False,       # transcription en direct (streaming)
    "live_paste_fast": True,     # coller le texte du direct (sinon re-transcrit en fin)
    "tone_auto": True,           # adapter le ton à l'app active
    "engine": "whisper",         # "whisper" (précis) ou "parakeet" (rapide)
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

    @property
    def engine(self):
        return self.data.get("engine", "whisper")

    @engine.setter
    def engine(self, value):
        self.data["engine"] = value
        self.save()

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
