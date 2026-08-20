# LocalFlow 🎙

Dictée vocale **locale, gratuite et 100 % hors-ligne** pour macOS (Apple Silicon) — dans l'esprit de Wispr Flow.
Maintiens `fn`, parle, relâche : le texte est collé dans l'app active, en ~0,7 s. Rien ne quitte ton Mac.

## Installation (une commande)

```bash
curl -fsSL https://raw.githubusercontent.com/insaneaddi-stack/localflow/main/install.sh | bash
```

Ça installe dans `~/Applications/LocalFlow`, télécharge les modèles (~2,6 Go, une seule fois) et lance l'app.
Sans git ni Homebrew : s'il n'y a pas de Python 3.10+, `uv` en installe un tout seul.
Version légère (sans nettoyage IA, ~1,6 Go) : ajoute `-s -- --minimal` à la fin de la commande.

**Une fois, à la fin de l'installation** (les Réglages s'ouvrent tout seuls) :
1. Confidentialité et sécurité → **Accessibilité** → `+` → `LocalFlow.app` (dans `~/Applications/LocalFlow`) → activer.
2. Accepter la demande **Micro** au premier appui sur `fn`.

Prérequis : Mac Apple Silicon (M1+), macOS 14+, 16 Go de RAM conseillés.

## Utilisation

| Geste | Effet |
|---|---|
| Maintenir `fn` → parler → relâcher | Texte collé dans l'app active |
| `fn` + `espace` | Mains-libres (re-appuyer sur `fn` pour finir, 10 min max) |
| Double-tap `fn` | Panneau : Historique · Nettoyage IA · Sons · Copier la dernière (touches 1–4, esc) |
| `fn` + autre touche (`fn+←`…) | Pas de dictée, la touche agit normalement |
| « à la ligne », « nouveau paragraphe », « point d'interrogation »… | Commandes vocales |
| « efface ça » | Annule le dernier collage |
| « corrige mail en main » | Apprend une correction |

- **Moteur** (menu 🎙 → Moteur) : Équilibré = Whisper large-v3-turbo (~0,7 s) · Précision max = Whisper large-v3 (~2 s, 3 Go, téléchargé à la demande) · Rapide = Parakeet (~0,4 s).
- **Fiabilité** : prompt de style + dictionnaire donnés à Whisper, repli automatique en température, seconde passe en contexte complet si la sortie semble tronquée, passe-haut 80 Hz + gain automatique ; les 5 derniers enregistrements sont gardés dans `~/Library/Caches/LocalFlow/` pour diagnostiquer.
- **Dictionnaire** : menu 🎙 → Dictionnaire… (`~/.localflow.dict.txt`) — noms propres respectés, `mauvais -> bon`.
- **Apprentissage** : si tu corriges à la main un mot collé, LocalFlow le remarque ; vu 2 fois, il l'applique.
- **Gain automatique** : fonctionne même en parlant loin du Mac.
- **Ton adapté à l'app** (avec Nettoyage IA) : relâché dans Slack/WhatsApp, soigné dans Mail/Notion.
- **Historique** (300 dictées, recherche, clic = copier) et statistiques (mots, temps gagné).
- Le log (`~/.localflow.log`) ne contient jamais le texte dicté.

## Commandes utiles

```bash
cd ~/Applications/LocalFlow
./run.sh          # (re)lancer
./uninstall.sh    # retirer l'agent (garde le dossier)
```

Mise à jour : relance la commande d'installation (le `.venv` et les modèles sont conservés).

## Notes techniques

- Le dossier doit rester **hors** Bureau/Documents/Téléchargements (macOS y bloque les agents de session).
- `LocalFlow.app` est un bundle signé ad hoc autour du Python du venv : après `./build-app.sh`, macOS redemande Accessibilité et Micro.
- Tourne via un LaunchAgent (`com.louqui.localflow`) : démarre à la session, se relance en cas de plantage, se répare seul (touche fn, micro débranché, UI figée).
- `brew upgrade python` casse le venv : relance `./setup.sh`.

Licence MIT.
