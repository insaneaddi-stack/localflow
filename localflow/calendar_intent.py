"""De la phrase dictée à l'événement structuré.

Deux étages, comme pour le nettoyage :

1. `resolve_when()` / `build_event()` — déterministes, purs, testés. Ce sont eux
   qui calculent les dates.
2. `CalendarIntent` — passe LLM (Qwen3-1.7B 4 bits, le même modèle que le
   nettoyage) qui ne fait qu'une chose : découper la phrase en descripteurs
   RELATIFS (« demain », « 14:00 », 60 min).

Le partage est volontaire. Un modèle de 1,7 milliard de paramètres se trompe
sur « on est mardi, dans combien de temps est jeudi prochain » ; il ne se trompe
pas sur « quel mot de cette phrase désigne le jour ». On lui laisse la lecture,
on garde l'arithmétique.

Rien ne part vers l'agenda si le moindre garde-fou lâche : pas de titre, pas de
jour compris, date aberrante — on renvoie None et l'appelant prévient.
"""

import datetime
import json
import re
import threading
from zoneinfo import ZoneInfo

LLM_MODEL_ID = "mlx-community/Qwen3-1.7B-4bit"   # déjà chargé par cleanup.py

TZ = ZoneInfo("Europe/Paris")

DUREE_DEFAUT_MIN = 60
TITRE_MAX = 200
# Fenêtre de confiance : au-delà, on suppose que le modèle a inventé une date.
PASSE_MAX = datetime.timedelta(days=1)
FUTUR_MAX = datetime.timedelta(days=730)

JOURS = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

SYSTEM_PROMPT = (
    "Tu extrais un événement d'agenda d'une phrase dictée en français. "
    "Réponds UNIQUEMENT avec un objet JSON, sans texte autour, sans balise de code.\n"
    "Clés attendues :\n"
    '  "titre"           : ce qui se passe, formulé court et à l\'infinitif ou en nom. '
    "Retire les mots de commande (« mets-moi un événement », « ajoute dans mon agenda »).\n"
    '  "jour"            : UNIQUEMENT l\'un de : "aujourd\'hui", "demain", "après-demain", '
    'un nom de jour ("lundi"..."dimanche"), ou une date "AAAA-MM-JJ". Jamais un calcul.\n'
    '  "heure"           : "HH:MM" en 24 h, ou null si la phrase n\'en donne pas.\n'
    '  "duree_min"       : durée en minutes (entier), 60 par défaut.\n'
    '  "journee_entiere" : true si aucune heure n\'est donnée.\n'
    "N'invente jamais de jour ni d'heure absents de la phrase : mets null."
)

FEW_SHOT = [
    (
        "mets-moi un événement demain à 14h il faut que je présente le directeur commercial à César",
        '{"titre": "Présenter le directeur commercial à César", "jour": "demain", '
        '"heure": "14:00", "duree_min": 60, "journee_entiere": false}',
    ),
    (
        "ajoute dans mon agenda vendredi rendu du NEC",
        '{"titre": "Rendu du NEC", "jour": "vendredi", "heure": null, '
        '"duree_min": 60, "journee_entiere": true}',
    ),
    (
        "note un point d’équipe lundi prochain à 9h30 pendant deux heures",
        '{"titre": "Point d\'équipe", "jour": "lundi", "heure": "09:30", '
        '"duree_min": 120, "journee_entiere": false}',
    ),
]


def extract_json(output: str):
    """Le premier objet JSON d'une sortie de LLM, ou None.

    Les modèles encadrent volontiers leur réponse (```json, « Voici : »…). On
    prend du premier { à l'accolade qui le referme, sans faire confiance au
    reste.
    """
    if not output:
        return None
    if "</think>" in output:
        output = output.split("</think>")[-1]
    debut = output.find("{")
    if debut < 0:
        return None
    profondeur, dans_texte, echappe = 0, False, False
    for i, c in enumerate(output[debut:], start=debut):
        if dans_texte:
            if echappe:
                echappe = False
            elif c == "\\":
                echappe = True
            elif c == '"':
                dans_texte = False
            continue
        if c == '"':
            dans_texte = True
        elif c == "{":
            profondeur += 1
        elif c == "}":
            profondeur -= 1
            if profondeur == 0:
                try:
                    valeur = json.loads(output[debut:i + 1])
                except ValueError:
                    return None
                return valeur if isinstance(valeur, dict) else None
    return None   # accolade jamais refermée : génération coupée


def _sans_accents(texte):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower())
                   if unicodedata.category(c) != "Mn")


# Repères temporels admis dans la phrase. Sans l'un d'eux, aucun événement ne
# part — quoi qu'en dise le modèle.
_MARQUEURS = re.compile(
    r"\b(aujourd'?hui|demain|apres-?demain|surlendemain|ce soir|ce matin|"
    r"cet? (?:apres-midi|midi)|midi|minuit|"
    r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|"
    r"semaine|mois|janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|"
    r"octobre|novembre|decembre|"
    r"\d{1,2}\s*[h:]\s*\d{0,2}|\d{1,2}\s*heures?|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2})\b"
)


def mentions_time(texte):
    """La phrase parle-t-elle vraiment d'un moment ?

    Garde-fou du même esprit que celui du nettoyage, qui interdit au LLM
    d'inventer des mots : ici on lui interdit d'inventer une date. « Bonjour
    comment ça va » lui faisait produire un événement pour aujourd'hui — sans
    ce contrôle, une phrase dictée par erreur atterrit dans l'agenda.
    """
    return bool(_MARQUEURS.search(_sans_accents(texte or "")))


def find_day(texte):
    """Le jour lu DANS la phrase, prioritaire sur celui du modèle.

    Mesuré : sur « rappelle-moi d'appeler César demain matin à 9h », Qwen3-1.7B
    répond « aujourd'hui ». Il lit bien l'heure et le titre, mais pas le jour ;
    on ne le lui demande donc plus quand la phrase le dit noir sur blanc.
    Renvoie un descripteur que resolve_day() sait lire, ou None.
    """
    t = _sans_accents(texte or "")
    if re.search(r"\b(apres-?demain|surlendemain)\b", t):
        return "après-demain"
    if re.search(r"\bdemain\b", t):
        return "demain"
    if re.search(r"\b(aujourd'?hui|ce soir|ce matin|cet? apres-midi|ce midi|tout a l'heure)\b", t):
        return "aujourd'hui"
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return m.group(1)
    for nom in JOURS:
        if re.search(r"\b" + _sans_accents(nom) + r"\b", t):
            return nom
    return None


def _parse_heure(heure):
    """« 14:00 », « 14h30 », « 9 h » → (h, m). None si illisible."""
    if heure is None:
        return None
    s = str(heure).strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2})[:h.]?(\d{2})?$", s)
    if not m:
        return None
    h = int(m.group(1))
    mn = int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None
    return h, mn


def resolve_day(jour, today):
    """Descripteur de jour → date. None si on ne comprend pas.

    Un nom de jour seul désigne TOUJOURS sa prochaine occurrence à venir : dire
    « mardi » un mardi veut dire le mardi suivant, jamais dans dix minutes.
    """
    if jour is None:
        return None
    s = str(jour).strip().lower()
    if not s:
        return None
    if s in ("aujourd'hui", "aujourd’hui", "ce jour"):
        return today
    if s == "demain":
        return today + datetime.timedelta(days=1)
    if s in ("après-demain", "apres-demain", "surlendemain"):
        return today + datetime.timedelta(days=2)

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    mot = s.replace(" prochain", "").replace(" qui vient", "").strip()
    if mot in JOURS:
        delta = (JOURS[mot] - today.weekday()) % 7
        return today + datetime.timedelta(days=delta or 7)
    return None


def resolve_when(jour, heure, duree_min, journee_entiere, now):
    """Descripteurs relatifs → horodatages TimeTree (ms), ou None.

    `now` est un datetime AWARE (Europe/Paris) : la fonction ne lit jamais
    l'horloge elle-même, c'est ce qui la rend testable au passage à l'heure
    d'hiver comme un dimanche à 23 h.
    """
    jour_d = resolve_day(jour, now.date())
    if jour_d is None:
        return None

    hm = _parse_heure(heure)
    tout_le_jour = bool(journee_entiere) or hm is None

    if tout_le_jour:
        # TimeTree stocke une journée entière à minuit UTC, début = fin.
        minuit = datetime.datetime(jour_d.year, jour_d.month, jour_d.day,
                                   tzinfo=datetime.timezone.utc)
        ms = int(minuit.timestamp() * 1000)
        debut_local = datetime.datetime(jour_d.year, jour_d.month, jour_d.day, tzinfo=TZ)
        quand = {"start_ms": ms, "end_ms": ms, "all_day": True,
                 "start_timezone": "UTC", "end_timezone": "UTC",
                 "_debut_local": debut_local}
    else:
        h, mn = hm
        debut = datetime.datetime(jour_d.year, jour_d.month, jour_d.day, h, mn, tzinfo=TZ)
        try:
            duree = int(duree_min)
        except (TypeError, ValueError):
            duree = DUREE_DEFAUT_MIN
        if not (1 <= duree <= 24 * 60):
            duree = DUREE_DEFAUT_MIN
        fin = debut + datetime.timedelta(minutes=duree)
        quand = {"start_ms": int(debut.timestamp() * 1000),
                 "end_ms": int(fin.timestamp() * 1000),
                 "all_day": False,
                 "start_timezone": "Europe/Paris", "end_timezone": "Europe/Paris",
                 "_debut_local": debut}

    ecart = quand["_debut_local"] - now
    if ecart < -PASSE_MAX or ecart > FUTUR_MAX:
        return None   # date aberrante : on n'écrit rien
    return quand


def clean_title(titre):
    """Titre présentable, ou None. Le modèle rend parfois la phrase entière."""
    if not titre:
        return None
    t = " ".join(str(titre).split())
    t = re.sub(r"^(mets?|ajoute|note|crée|rajoute)[- ]?(moi|nous)?\s+"
               r"(un|une|le|la)?\s*(événement|evenement|rendez-vous|rdv|rappel)?\s*",
               "", t, flags=re.IGNORECASE).strip()
    t = t.strip(" .,:;-–—")
    if not t:
        return None
    if len(t) > TITRE_MAX:
        t = t[:TITRE_MAX].rstrip() + "…"
    return t[0].upper() + t[1:] if t else None


def build_event(brut, now, texte=""):
    """dict du LLM + phrase d'origine + instant présent → événement, ou None.

    Pure : c'est ici que se jouent tous les garde-fous, et c'est ce qu'on teste.
    `texte` est la phrase dictée ; quand elle est fournie, elle fait autorité
    sur le modèle pour tout ce qu'elle dit explicitement.
    """
    if not isinstance(brut, dict):
        return None
    if texte and not mentions_time(texte):
        return None            # aucun repère temporel : le modèle a inventé
    titre = clean_title(brut.get("titre") or brut.get("title"))
    if not titre:
        return None
    jour = find_day(texte) or brut.get("jour")
    quand = resolve_when(jour, brut.get("heure"),
                         brut.get("duree_min", DUREE_DEFAUT_MIN),
                         brut.get("journee_entiere", False), now)
    if quand is None:
        return None
    debut_local = quand.pop("_debut_local")
    quand.update({"title": titre, "_libelle": humanize(debut_local, quand["all_day"], now)})
    return quand


def humanize(debut, tout_le_jour, now):
    """« Demain 14:00 », « Jeudi 18 sept. », pour l'aperçu avant écriture."""
    JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
               "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    delta = (debut.date() - now.date()).days
    if delta == 0:
        jour = "Aujourd'hui"
    elif delta == 1:
        jour = "Demain"
    elif delta == 2:
        jour = "Après-demain"
    elif 0 < delta < 7:
        jour = JOURS_FR[debut.weekday()].capitalize()
    else:
        jour = f"{JOURS_FR[debut.weekday()].capitalize()} {debut.day} {MOIS_FR[debut.month - 1]}"
    return jour if tout_le_jour else f"{jour} {debut:%H:%M}"


class CalendarIntent:
    """Lit la phrase avec Qwen3-1.7B. Partage le modèle du nettoyage si possible."""

    def __init__(self, shared=None):
        self._shared = shared      # un cleanup.Cleaner déjà chargé
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def _load(self):
        if self._shared is not None:
            self._shared._load()
            return self._shared._model, self._shared._tokenizer
        with self._lock:
            if self._model is None:
                from mlx_lm import load

                self._model, self._tokenizer = load(LLM_MODEL_ID)
        return self._model, self._tokenizer

    def parse(self, text, now=None):
        """Phrase dictée → événement prêt à écrire, ou None si rien de sûr."""
        text = (text or "").strip()
        if not text:
            return None
        now = now or datetime.datetime.now(TZ)
        try:
            model, tokenizer = self._load()
            from mlx_lm import generate

            aujourdhui = (f"Nous sommes le {now:%Y-%m-%d}, "
                          f"un {['lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche'][now.weekday()]}, "
                          f"il est {now:%H:%M}.")
            messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + aujourdhui}]
            for phrase, sortie in FEW_SHOT:
                messages.append({"role": "user", "content": phrase})
                messages.append({"role": "assistant", "content": sortie})
            messages.append({"role": "user", "content": text})
            try:
                prompt = tokenizer.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False, enable_thinking=False)
            except TypeError:
                prompt = tokenizer.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False)

            sortie = generate(model, tokenizer, prompt=prompt,
                              max_tokens=220, verbose=False).strip()
            return build_event(extract_json(sortie), now, texte=text)
        except Exception:
            return None
