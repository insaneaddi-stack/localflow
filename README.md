# LocalFlow 🎙

Dictée vocale pour macOS (Apple Silicon), propulsée par **Voxtral** (Mistral) — dans l'esprit de Wispr Flow.
Maintiens `fn`, parle, relâche : le texte est collé dans l'app active, en ~0,5 s. Il te faut une clé API Mistral (voir [Moteur](#moteur--voxtral)).

## Installer sur un autre Mac (mini tuto)

Il te faut : un Mac Apple Silicon (M1 ou plus récent) sous macOS 14+, et un compte sur [console.mistral.ai](https://console.mistral.ai).

**1. Lance l'installation.** Ouvre *Terminal* (⌘ + espace, tape « Terminal »), colle cette commande, Entrée :

```bash
curl -fsSL https://raw.githubusercontent.com/insaneaddi-stack/localflow/main/install.sh | bash
```

Ça prend 3 à 5 minutes. Aucun modèle à télécharger. macOS demande ton mot de passe **une seule fois** (certificat local qui garde tes autorisations après chaque mise à jour).

**2. Les deux autorisations.** Une fenêtre *Autorisations* s'ouvre :
- **Accessibilité** : clique *Autoriser* → *Ouvrir les Réglages* → active l'interrupteur **LocalFlow**.
- **Micro** : clique *Autoriser* → *OK* sur « LocalFlow souhaite accéder au micro ».

La fenêtre se ferme toute seule quand les deux sont vertes.

**3. Ta clé Voxtral, directement dans la console.** L'installateur te guide pas à pas :
1. Va sur console.mistral.ai → *API Keys* → *Create new key* (Entrée dans la console ouvre la page pour toi).
2. Copie la clé (⌘C). Si elle est déjà copiée, l'installateur la repère : Entrée suffit.
3. Colle-la dans la console (rien ne s'affiche pendant que tu colles, c'est normal), Entrée. Elle est vérifiée auprès de Mistral puis rangée dans le trousseau du Mac (jamais dans un fichier).

Ensuite, l'installateur te fait **essayer tout de suite** : maintiens `fn`, dis « bonjour, ça marche », relâche. Le texte s'écrit dans la console.

Tu peux passer cette étape (`p`) : une carte du tutoriel dans l'app, ou le menu de la barre → **Clé Voxtral…**, te la redemandera. Le tutoriel de l'app continue ensuite : première dictée, mains-libres, panneau, réunions (menu → *Revoir le tutoriel*).

**4. Essaie.** Clique dans un champ de texte, maintiens `fn`, parle, relâche.

Si quelque chose ne répond plus : Réglages Système → Confidentialité et sécurité → Accessibilité / Micro → LocalFlow. Mise à jour : relance la commande du 1 (ou elle se fait toute seule).

L'app s'installe dans `~/Applications/LocalFlow`. Sans git ni Homebrew : s'il n'y a pas de Python 3.10+, `uv` en installe un tout seul. Les autorisations survivent aux mises à jour (`sign-identity.sh`).

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
| Menu 🎙 → Réunions → Démarrer | Enregistre une réunion (micro + son système), compte rendu Markdown |

## Réunions 🎙●

LocalFlow enregistre aussi tes réunions, **sans bot** (dans l'esprit de Granola / Wispr Flow Notetaker). Le son est transcrit par Voxtral, comme la dictée :

- **Micro + son de l'appel** (Zoom, Meet, Teams, FaceTime, Discord… ou un navigateur) via un petit helper Core Audio (macOS 14.2+). Tu es « Moi », tes interlocuteurs « Eux ».
- **Proposition automatique** quand une app d'appel utilise le micro (bande du bas : Oui / Non), ou à la main : menu 🎙 → Réunions, ou la bulle **Réunion** du panneau (double-tap fn).
- **Transcript en direct** par tours de parole + **bloc-notes** : tape tes notes pendant l'appel, l'IA les enrichit à la fin.
- **Compte rendu** (petit modèle Qwen sur ton Mac, ~1 Go téléchargé la première fois) : Résumé · Décisions · Actions · Questions ouvertes · Dates & chiffres, titre auto.
- **Markdown** dans `~/Documents/LocalFlow Réunions/` (+ audio `.m4a` moi/eux en stéréo) — lisible par Obsidian, Notes, etc. Fenêtre **Mes réunions** : recherche, résumé, « pose une question » sur la réunion.
- La dictée `fn` reste utilisable pendant une réunion. Fin automatique 90 s après la libération du micro.

Autorisation supplémentaire, une fois : **Enregistrement de l'écran et audio système → LocalFlow** (demandée à la première réunion ; sans elle, le son de l'appel est muet et LocalFlow te le dit).

- **Moteur** : Voxtral (`voxtral-mini-latest`), en ligne : il ponctue seul, détecte la langue, sert la dictée et les réunions. Rien n'est gardé en mémoire sur le Mac.
- **Fiabilité** : dictionnaire et corrections apprises envoyés à Voxtral, nouveaux essais automatiques (429, panne réseau, résultat vide), passe-haut 80 Hz + gain automatique ; les 5 derniers enregistrements sont gardés dans `~/Library/Caches/LocalFlow/` pour diagnostiquer, et une dictée que Voxtral n'a pas pu transcrire reste dans `perdues/` et est retentée plus tard.
- **Dictionnaire** : menu 🎙 → Dictionnaire… (`~/.localflow.dict.txt`) — noms propres respectés, `mauvais -> bon`.
- **Apprentissage** : si tu corriges à la main un mot collé, LocalFlow le remarque ; vu 2 fois, il l'applique.
- **Gain automatique** : fonctionne même en parlant loin du Mac.
- **Ton adapté à l'app** (avec Nettoyage IA) : relâché dans Slack/WhatsApp, soigné dans Mail/Notion.
- **Historique** (300 dictées, recherche, clic = copier) et statistiques (mots, temps gagné).
- Le log (`~/.localflow.log`) ne contient jamais le texte dicté.

## Moteur : Voxtral

La dictée et les réunions partent vers **Voxtral** (`voxtral-mini-latest`, ~0,5 s). Il n'y a **aucun modèle local à télécharger** : seuls le Nettoyage IA et les comptes rendus de réunion téléchargent un petit modèle la première fois qu'on s'en sert.

- **Clé** : fenêtre *Clé Voxtral* au premier lancement, puis menu → **Clé Voxtral…** (copie la clé, clique *Coller la clé*). Rangée dans le trousseau macOS, jamais dans le dépôt. En ligne de commande : `security add-generic-password -U -a localflow -s localflow-mistral -w`.
- **Nouveaux essais** : un 429 (« capacité insuffisante » côté Mistral), une erreur serveur, une panne réseau ou un résultat vide sur une vraie dictée sont redemandés jusqu'à 4 fois (1,5 s, 4 s, 8 s, 15 s ; 45 s au plus, en respectant l'attente demandée par l'API).
- **Rien n'est perdu** : si Voxtral ne répond vraiment pas, l'audio est gardé dans `~/Library/Caches/LocalFlow/perdues/` et retenté 1, 5 puis 15 minutes plus tard ; la dictée arrive alors dans l'Historique (le texte n'est jamais collé après coup, ton presse-papiers n'est pas touché). Sans clé, la dictée attend que tu l'ajoutes.
- **Vocabulaire** : dictionnaire + corrections apprises envoyés en `context_bias`. Langue : détection automatique (français et anglais).
- **Vie privée** : l'audio des dictées et des réunions est envoyé à Mistral pour être transcrit. Désactive l'utilisation de tes données pour l'entraînement dans les réglages de confidentialité de la console Mistral.

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
