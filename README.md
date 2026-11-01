# Monika

Assistante IA personnelle, locale et en français, pour Linux (Python 3.12+).
Inspirée du personnage de Monika (DDLC). Usage strictement personnel.

Monika discute depuis une page dans le navigateur ou à la voix (avec **ta** voix clonée), agit grâce à des outils
MCP, se souvient de ce qui compte pour toi, retrouve tes documents
et s'affiche sous forme d'avatar 3D avec un petit tableau de bord dans le navigateur.
Tout est stocké chez toi ; seul le fournisseur LLM que tu configures reçoit tes messages.

## Sommaire

1. [Prérequis](#prérequis)
2. [Installation et premier lancement](#installation-et-premier-lancement)
3. [Structure du projet](#structure-du-projet)
4. [Commandes](#commandes)
5. [Configuration](#configuration)
6. [Outils (serveurs MCP)](#outils-serveurs-mcp)
7. [Agent, contexte et prompt](#agent-contexte-et-prompt)
8. [Sessions et reprise](#sessions-et-reprise)
9. [Noyau persistant](#noyau-persistant)
10. [Tâches planifiées](#tâches-planifiées)
11. [Briefing du matin](#briefing-du-matin)
12. [Mémoire personnelle](#mémoire-personnelle)
13. [Documents personnels (RAG)](#documents-personnels-rag)
14. [Voix : écoute et parole](#voix--écoute-et-parole)
15. [Avatar et tableau de bord](#avatar-et-tableau-de-bord)
16. [Personnage : émotions, relation et journal](#personnage--émotions-relation-et-journal)
17. [Mots sensibles](#mots-sensibles)
18. [Données, vie privée et sécurité](#données-vie-privée-et-sécurité)
19. [Tests](#tests)
20. [Dépannage](#dépannage)

## Prérequis

- **Linux** avec PipeWire ou PulseAudio (le son passe par `parec` et `pacat`, paquet `libpulse`) ;
  vérifie avec `pactl info`.
- **Python 3.12+**.
- Un accès à une **API compatible OpenAI** (clé et URL dans `.env`).
- Pour la voix : `ffmpeg`, PyTorch, et idéalement un GPU (4 Go suffisent ; sinon la synthèse
  tourne sur le CPU, plus lentement).
- Pour les serveurs MCP de l'exemple : Node.js (`npx`).
- Espace disque pour les modèles téléchargés au premier usage : environ 2 Go (documents),
  2 Go (voix) ; une connexion est nécessaire à ce moment-là. La mémoire, elle, n'a aucun modèle
  à télécharger.

## Installation et premier lancement

Lance toujours Monika **depuis le dossier du projet** (`.env` y est cherché ; le projet n'est pas
prévu pour être installé comme paquet).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis édite LLM_BASE_URL, OPENAI_API_KEY, MODEL_NAME
python -m monika --config     # vérifie la configuration (secrets masqués)
python -m monika               # démarre Monika : la conversation se passe dans la page
```

Le mode voix demande une installation supplémentaire, décrite dans [Voix](#voix--écoute-et-parole).

## Structure du projet

```
monika/        Point d'entrée (python -m monika), configuration (config.py), chemins (paths.py), embeddings.py,
               noyau persistant (kernel.py)
agent/         Boucle ReAct (loop.py), client LLM (llm.py), contexte (context.py), prompt (prompt.py),
               sessions persistantes (sessions.py), tâches planifiées (tasks.py), détection de boucle,
               phrases d'attente, infos système
tools/         Registre d'outils audité (registry.py), client MCP (mcp_client.py), outils internes (internal.py)
security/      Masquage des secrets (masking.py), enveloppe « données, pas ordres » (untrusted.py), audit (audit.py)
memory/        Carnet de notes personnelles (notes.py) et capture en arrière-plan (capture.py)
rag/           Documents personnels : scan, lecture, découpage, index, recherche hybride, reranking
character/     Émotions, relation (favorabilité), journal, gestes d'avatar
voice/         Micro, détection de parole, Whisper, GPT-SoVITS (client HTTP), lecture audio, nettoyage
               du texte parlé
modes/         Tour de conversation et commandes (text.py), lecteur vocal (voice.py)
web/           Serveur local (server.py), tableau de bord (dashboard.py), diffusion en direct (hub.py),
               conversation de la page (chat.py), voix de la page (voice.py) et page (static/ :
               index.html, css/, js/, avatar/, clips/, vendor/)
```

Les paquets (`agent/`, `rag/`, `web/`…) sont à la racine du projet : lance toujours Monika
**depuis ce dossier**, en `python -m monika`.

## Commandes

| Commande | Effet |
|---|---|
| `python -m monika` | Démarre Monika en local : la conversation se passe dans la page (le processus vit tant qu'elle est ouverte) |
| `./lancer-monika.sh` | Démarre la voix GPT-SoVITS si besoin, vérifie qu'elle répond, puis lance Monika (mêmes options que ci-dessous) |
| `python -m monika --mode voix` | Conversation à voix haute (réponses aussi à l'écran) |
| `python -m monika --serve` | Lance le noyau persistant (mémoire, documents, outils, page, voix) et le laisse tourner |
| `python -m monika --config` | Affiche la configuration chargée (secrets masqués) |
| `python -m monika --tools` | Liste les serveurs MCP et leurs outils |
| `python -m monika --call NOM '{"arg": 1}'` | Appelle un outil à la main |
| `python -m monika --rag-index [DOSSIER ...]` | Indexe tes documents (`--reset` : repart d'un index vide) |
| `python -m monika --rag-search MOT ...` | Cherche dans l'index, avec citations |
| `python -m monika --rag-status` | État de l'index de documents |

**Dans le chat** (`/aide` les rappelle) :

| Commande | Effet |
|---|---|
| `/outils` | Liste des outils disponibles |
| `/contexte` | Taille estimée de la conversation, répartie entre prompt, outils (par source, avec les plus lourds) et historique |
| `/memoire`, `/oublier ID` | Ce que Monika sait de toi ; supprime un souvenir |
| `/relation` | Où en est votre relation (niveau, favorabilité, humeur) ; `/relation reinitialiser` repart de zéro |
| `/journal` | Les notes que Monika a écrites sur toi ; `/journal oublier ID` en supprime une, `/journal effacer` tout |
| `/journal-nuit` | La note de la nuit dans Obsidian ; `/journal-nuit voir DATE`, `supprimer DATE`, `accepter ID`, `refuser ID` |
| `/sessions` | Les sessions enregistrées, les plus récentes d'abord |
| `/reprendre ID` | Reprend une session : son historique et son résumé sont rechargés |
| `/chercher MOT …` | Cherche dans les tours enregistrés (plein texte) |
| `/session oublier ID` | Supprime une session, ses tours et ses appels d'outils |
| `/indexer [dossier]`, `/documents` | Lance l'indexation en arrière-plan ; état de l'index |
| `/rappel QUAND : TEXTE` | Programme un rappel (« /rappel dans 20 minutes : boire de l'eau ») |
| `/taches`, `/tache annuler ID` | Les tâches planifiées ; en supprime une |
| `/briefing` | Le briefing du matin tout de suite, sans attendre l'heure dite |
| `/outils-perso` | Les serveurs MCP que Monika a écrits : liste, `code NOM` (relire le code et le test), `desactiver NOM`, `supprimer NOM` |
| `/recettes` | Mes procédures apprises : liste ; `garder ID`, `refuser ID`, `oublier ID` |
| `/stop` | Arrête tout ce qui tourne (réponse, voix, indexation) et dit quoi |
| `/effacer` | Oublie la conversation en cours (et son résumé) |
| `/quitter` (ou `/q`, Ctrl+D) | Quitte |

Ctrl+C interrompt la réponse en cours **et** arrête ce qui tourne en arrière-plan (voix,
indexation) ; les captures mémoire déjà lancées vont jusqu'au bout.

## Configuration

Tous les réglages sont dans `.env` (modèle commenté : `.env.example`, qui reprend exactement les
valeurs par défaut du code). Les plus utiles :

| Réglage | Rôle |
|---|---|
| `LLM_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` | Fournisseur et modèle (API compatible OpenAI) |
| `MONIKA_HOME` | Dossier des données (défaut `~/.config/monika`) |
| `USER_NAME` | Ton prénom, utilisé dans le prompt système |
| `PERSONA_TEXT` | Consignes libres sur son comportement, ajoutées à son prompt (page de réglages) |
| `MAX_STEPS` | Appels au modèle par tour (défaut 100) |
| `ACTION_LLM_ENABLED`, `ACTION_LLM_MODEL` | Rôle action : chaîne de gestes de l'avatar après la réponse (désactivé par défaut) |
| `CONTEXT_BUDGET_TOKENS` | Seuil de résumé automatique (défaut 150 000) |
| `SESSIONS_ENABLED`, `SESSIONS_KEEP_DAYS` | Historique des conversations sur disque (désactivé par défaut ; rétention 30 jours) |
| `KERNEL_ENABLED`, `KERNEL_SOCKET` | Noyau persistant et son chemin de socket (désactivé par défaut) |
| `TASKS_ENABLED`, `TASKS_MAX` | Rappels et tâches récurrentes (désactivé par défaut, 50 tâches max) |
| `BRIEFING_ENABLED`, `BRIEFING_TIME`, `BRIEFING_DAYS` | Briefing du matin : météo, agenda, mails et tâches du jour (désactivé par défaut, 07:30) |
| `BRIEFING_CITY`, `BRIEFING_SECTIONS` | Ville de la météo (Open-Meteo, sans clé) et sections demandées |
| `FAST_MEMORY_ENABLED`, `MEMORY_CAPTURE_ENABLED` | Carnet de notes et capture automatique |
| `RAG_ENABLED`, `RAG_ROOTS`, `RAG_EXCLUDE`, `RAG_MIN_SCORE` | Documents personnels (dont le seuil de pertinence) |
| `STT_MODEL`, `TTS_ENABLED` | Voix |
| `TTS_URL`, `TTS_SPEAKERS_DIR`, `TTS_CHARACTER_ID` | GPT-SoVITS : adresse de l'API et pack de voix |
| `TTS_LANGUAGE`, `TTS_EMOTION_TAGS`, `TTS_RENDER` | Langue parlée, tags d'émotion, découpage des requêtes |
| `TTS_PAUSE_STYLE`, `TTS_PAUSE_SCALE`, `TTS_PAUSE_MAX_S` | Pauses entre phrases : `neutre` (rien) ou `naturel` |
| `WEB_ENABLED`, `WEB_PORT` | Page locale (défaut 8765) |
| `SENSITIVE_WORDS_ENABLED` | Filtre de mots sensibles (la liste est dans `words.json`) |
| `RECIPES_ENABLED`, `RECIPES_MIN_TOOLS`, `RECIPES_INJECT_MAX`, `RECIPES_MAX` | Recettes apprises : proposition après un tour réussi, outils minimum, recettes injectées par tour, total |
| `LOG_LEVEL`, `AUDIT_ENABLED` | Journaux |

Une valeur invalide est refusée au lancement avec un message clair (par exemple
`LOOP_STOP_AFTER` doit dépasser `LOOP_WARN_AFTER`, et il faut
`CONTEXT_KEEP_RECENT_TOKENS` < `CONTEXT_BUDGET_TOKENS` < `CONTEXT_WINDOW_TOKENS`).

## Outils (serveurs MCP)

Les outils externes viennent uniquement de serveurs MCP, déclarés dans
`~/.config/monika/mcp_servers.json` (modèle : `mcp_servers.example.json`, même format que
Claude Desktop ; autre emplacement : `MCP_SERVERS_FILE`). Deux transports : commande locale
(stdio) ou `url` (HTTP streamable) ; un serveur ne peut pas avoir les deux.

- **Variables** : les `${VARIABLE}` des champs `command`, `args`, `env`, `cwd`, `url` et
  `headers` sont remplacés par l'environnement ou, à défaut, par le `.env` du projet (utile pour
  les clés d'API : `BRAVE_API_KEY=...` dans `.env`, `"${BRAVE_API_KEY}"` dans le serveur). Une
  variable absente désactive ce serveur avec un message clair.
- **Robustesse** : un serveur en panne n'empêche pas les autres de démarrer. `"enabled": false`
  désactive un serveur ; `"start_timeout_s": 90` laisse plus de temps à un démarrage lent (par
  exemple `npx` la première fois).
- **Limiter les outils** : le schéma de chaque outil est envoyé au modèle à **chaque** requête
  (un gros serveur peut peser des dizaines de milliers de tokens). `"enabled_tools": ["a", "b"]`
  ne garde que ces outils d'un serveur ; `"disabled_tools": ["c"]` en retire certains (et
  l'emporte sur `enabled_tools`). Les noms sont ceux du serveur, sans le préfixe `serveur__`.
  `/contexte` montre ce que pèse chaque source et les outils les plus lourds ; `--tools` et le
  démarrage indiquent combien d'outils sont écartés et signalent les noms inconnus.
- **Outils lents** (phrase d'attente après 1,5 s) : `"slow": true` pour tout un serveur,
  `"slow_tools": ["a", "b"]` pour certains outils, ou `"slow_tools": {"a": ["phrase 1",
  "phrase 2"]}` pour fournir des variantes de phrases.
- Les outils sont nommés `serveur__outil`. Les erreurs d'outil sont renvoyées au modèle en
  texte ; les images et sons renvoyés ne lui sont pas transmis (le texte l'indique).

**Outils internes** de Monika : `lire_resultat` (suite d'un gros résultat), `memoire_retenir`,
`memoire_chercher`, `memoire_lister`, `memoire_oublier`, `documents_chercher`,
`documents_indexer`, `documents_statut` et `create_custom_tools` (voir ci-dessous).

### Outils perso : `create_custom_tools`

Quand une action manque à ses outils, Monika peut écrire **son propre serveur MCP**. L'outil
`create_custom_tools` prend `nom`, `description`, `code` (`server.py`) et `test`
(`test_server.py`) ; le déroulé est fixe et aucune étape ne peut être sautée :

1. **Génération** dans `~/.config/monika/mcp_custom/<nom>/` (`server.py`, `test_server.py`, en
   `0600` dans un dossier `0700`).
2. **Vérification statique** sans exécution : syntaxe, imports d'évasion interdits
   (`subprocess`, `ctypes`, `cffi`, `importlib`, `pickle`, `multiprocessing`, `pty`, `marshal`,
   `shelve`, `shutil`), `eval`/`exec`/`compile`/`__import__`, appels `os.system`, `os.popen`,
   `os.exec*`, `os.fork`. **Le réseau est autorisé** (`requests`, `httpx`, `urllib`, `socket`,
   `aiohttp`…) : un outil qui ne peut joindre aucune API ne servirait à rien. Le serveur doit
   exposer ses outils avec le paquet `mcp`. Un refus laisse le code sur disque pour relecture,
   mais rien n'est enregistré. Le serveur s'écrit avec l'API installée :
   `from mcp.server.mcpserver import MCPServer` (mcp 2.x).
3. **Test exécuté dans un bac à sable** : `bwrap` (bubblewrap) avec `--unshare-all` puis
   `--share-net` (réseau ouvert, `resolv.conf` et autorités de certification montés en lecture
   seule), `/usr` en lecture seule, `/tmp` sur un tmpfs, et **seul `mcp_custom/<nom>/` en
   écriture** ; ni ton `$HOME` ni le reste du disque ne sont montés. Délai de 60 s. Sans
   `bwrap`, l'outil refuse de tester et donc de créer quoi que ce soit. Test rouge = refus.
4. **Enregistrement automatique** : si la vérification et les tests passent, le serveur est
   écrit dans `mcp_servers.json` (commande `python server.py`, `cwd` du dossier,
   `enabled: true`) sans qu'aucune approbation ne soit demandée. La configuration existante est
   préservée ; un serveur du même nom venu de toi n'est jamais écrasé. Monika affiche ensuite le
   code, les permissions demandées et les résultats des tests dans sa réponse, pour que tu saches
   ce qui a été ajouté.

**Empreinte** : elle est enregistrée dans `custom_tools.json`. Si le code change après coup, le
serveur est **désactivé** au démarrage suivant (`--serve` compris) et sur `--tools` : il faut le
régénérer, il sera revérifié et retesté avant d'être réenregistré. Un serveur de `mcp_custom/`
absent de l'index est retiré de la configuration.

**Commandes** : `/outils-perso` (liste, état, empreinte), `/outils-perso code NOM` (le code et le
test réels sur le disque), `desactiver NOM`, `supprimer NOM`.

**Depuis la page web** : rien à activer en plus. La page et le terminal passent par le même agent
et le même registre d'outils, donc `create_custom_tools` y fonctionne à l'identique ; les commandes
`/outils-perso …` se tapent dans le bloc *Conversation* (leur réponse s'y affiche), et le panneau
**Outils perso** du tableau de bord montre la liste et l'état, rafraîchi avec le reste de la page.

**Limites, à connaître** :

- Le bac à sable protège l'exécution **du test** : pendant le test, le serveur ne voit ni ton
  `$HOME` ni le reste du disque et n'écrit que dans son dossier (il garde `/usr` et le venv en
  lecture seule, c'est là que vivent Python et `mcp`). Le réseau, lui, est ouvert.
- Le serveur **enregistré** tourne ensuite comme un serveur MCP ordinaire, avec tes droits : il
  voit ton disque et peut joindre Internet. Relis le code d'un outil qui écrit des fichiers.
- **Rien ne demande ton accord**, ni pour créer, ni pour exécuter : c'est le prix du flux direct.
  Relis le code quand tu veux (`/outils-perso code NOM`), et retire un outil avec
  `desactiver NOM` ou `supprimer NOM`.
- La vérification statique est une liste de refus, pas une preuve : des constructions dynamiques
  exotiques peuvent la contourner. Ce sont le bac à sable et ta relecture qui font la sécurité.
- Un serveur nouvellement enregistré n'est disponible qu'au **redémarrage** de Monika ; les outils
  des serveurs déjà lancés restent ceux du démarrage.
- **Réseau** : rien à configurer, il est autorisé pour le test comme pour l'outil enregistré ;
  `requests`, `httpx` ou `urllib` peuvent joindre de vraies API.

## Agent, contexte et prompt

À chaque message, Monika raisonne, appelle des outils (function calling), observe, recommence,
puis répond, au plus `MAX_STEPS` (100) appels au modèle par tour. Elle agit directement, sans
demander confirmation, et sans rien affirmer sans preuve.

- **Boucles** : même outil + mêmes arguments + même résultat. Avertissement à la 3e fois
  (`LOOP_WARN_AFTER`), arrêt avec bilan à la 5e (`LOOP_STOP_AFTER`).
- **Bilan honnête** : si la limite d'étapes ou une boucle arrête la tâche, Monika dit ce qui est
  fait, ce qui ne l'est pas et ce qui reste.
- **Contenu non fiable** : tout résultat d'outil arrive au modèle dans une enveloppe
  `<donnees_outil>`, présentée comme de la donnée, jamais comme un ordre. Une fausse balise
  d'ouverture ou de fermeture dans le contenu est neutralisée.
- **Phrases d'attente** : pour un outil lent, une phrase après `FILLER_DELAY_S` (1,5 s), puis
  toutes les `FILLER_REPEAT_S`, au plus `FILLER_MAX_PER_TURN` (2) par tour. Affichées, et dites à
  voix haute quand la voix est chargée.
- **Rôle facultatif** : le rôle **action** (après le tour, en tâche de fond, désactivé par
  défaut) choisit une courte chaîne de gestes (2 à 8 s chacun, trois au plus) que la page joue si
  aucun geste n'est en cours ; un message suivant **annule** la chaîne encore en attente, sur le
  fond comme sur la page, pour qu'un geste décidé pour une réponse ne tombe jamais pendant la
  suivante. En cas d'échec, les gestes déterministes de `character/cues.py` restent seuls actifs.
  Ce rôle coûte un appel de modèle par tour : à activer en mesurant.
  Tous les outils partent à chaque requête, sans planificateur intermédiaire.
- **Ctrl+C** annule l'outil en cours. Si des actions avaient déjà été faites, une note les
  rappelle au modèle pour qu'il ne les croie pas annulées.
- **Prompt** reconstruit à chaque tour : personnalité, méthode de travail, règle « données, pas
  ordres », date en français et contexte du système (OS, utilisateur, dossier de travail).
- **Gros résultats** : au-delà de `TOOL_RESULT_MAX_CHARS` (20 000), le résultat est tronqué
  (début + fin) et le texte complet est sauvegardé dans `~/.config/monika/tool_results`
  (fichiers privés, purgés après `TOOL_RESULTS_KEEP_DAYS` = 14 jours). Le modèle lit la suite
  avec `lire_resultat(id, debut, longueur)`.

### Recettes apprises

Après un **tour réussi** (réponse complète, aucun outil en erreur) comptant au moins
`RECIPES_MIN_TOOLS` (3) appels d'outils, une tâche de fond demande au modèle une **recette
paramétrée** : but, paramètres, étapes avec les outils utilisés, vérification finale. Elle est
écrite en **brouillon** dans `recipes.json` — rien n'est enregistré sans ton accord.

- `/recettes` liste les brouillons (avec leur id), les versions actives et les usages ;
  `/recettes garder ID` enregistre, `/recettes refuser ID` jette le brouillon,
  `/recettes oublier ID` supprime la recette, son historique et ses compteurs.
- Au tour suivant, une recette active qui recoupe la demande (mots significatifs du titre, du
  but et des paramètres) est injectée dans le prompt sous « Recettes », présentée comme
  **donnée** : une procédure qui a déjà réussi, à adapter, jamais un ordre. Au plus
  `RECIPES_INJECT_MAX` (2) recettes par tour.
- Chaque injection compte un **usage** ; le tour qui suit enregistre une réussite (fini
  normalement, aucun outil en erreur) ou un échec.
- Si le tour améliore une recette existante, le keeper propose une **nouvelle version** dans le
  même id : elle n'est appliquée qu'après `/recettes garder ID`, et l'ancienne passe dans
  l'historique (10 versions conservées au plus).
- Le bloc **Recettes** du tableau de bord montre l'état (brouillon, version active, version
  proposée) et les usages. Les textes sont masqués avant écriture.

Ce que ça ne fait pas : aucune recette n'est appliquée automatiquement, rien n'est partagé
entre machines, et le rapprochement est **lexical** (pas d'embeddings) : une demande formulée
très différemment peut ne déclencher aucune recette. Le keeper coûte un appel de modèle par
tour éligible (`RECIPES_ENABLED=false` pour couper).
- **Budget de contexte** : la taille est estimée avant chaque appel (environ 3,3 caractères par
  token : une estimation, pas un comptage exact). Au-dessus de `CONTEXT_BUDGET_TOKENS`
  (150 000, très en dessous de la fenêtre de 1 M), les anciens échanges sont résumés par le
  modèle ; on garde au moins `CONTEXT_KEEP_RECENT_TOKENS` récents, avec une coupure toujours
  entre deux tours, jamais au milieu d'un appel d'outil. Si un seul tour est énorme, ses plus
  anciens résultats d'outils sont retirés (les 3 derniers restent). Si le résumé échoue, tu en
  es informé et la conversation continue sans compactage.

## Sessions et reprise

Avec `SESSIONS_ENABLED=true` (désactivé par défaut), chaque conversation est enregistrée dans
`~/.config/monika/sessions/sessions.db` : SQLite avec **recherche plein texte FTS5**, fichier en
`0600`. Une ligne par session, une par tour, une par appel d'outil, plus l'historique tel que le
modèle l'a vu — c'est cet instantané qui rend `/reprendre` exact, appels d'outils compris.

- **Secrets masqués** avant l'écriture, champ par champ (`security/masking.py`) : une clé collée
dans un message ou un argument d'outil n'arrive jamais sur le disque.
- **Tour interrompu** (Ctrl+C, erreur du modèle, fenêtre fermée) : écrit avec le statut
  `interrupted` ; un tour resté ouvert par un arrêt brutal est marqué ainsi au lancement suivant.
- **Budget de contexte** : `/reprendre` ne réinjecte pas tout. Au-delà du budget, les tours les plus
  anciens sont écartés **entiers** (jamais au milieu d'un appel d'outil) et la reprise le dit.
- **Rétention** : les sessions inutilisées depuis `SESSIONS_KEEP_DAYS` (30 jours) sont purgées au
  lancement. `/effacer` repart sur une session neuve sans effacer l'ancienne.
- **Tu gardes la main** : `/sessions`, `/chercher`, `/session oublier ID`, et le bloc *Sessions* du
  tableau de bord listent les quinze dernières, permettent de les reprendre et de les supprimer.
  Reprendre depuis la page recharge la conversation **dans le processus en cours** : elle se
  poursuit dans la page (bloc *Conversation*).
- **Recherche** : l'index porte sur ce qui a été dit (message et réponse), pas sur les résultats
  d'outils : ceux-ci restent dans l'historique mais hors de l'index.
- **Limite** : l'historique complet est réécrit à chaque tour (quelques centaines de kilo-octets sur
  une très longue session) ; l'écriture est atomique par transaction, et un échec de la base est
  journalisé sans jamais interrompre la conversation.

## Noyau persistant

Par défaut, `python -m monika` fait tout dans un seul processus : mémoire, documents, outils, page et
voix vivent et meurent avec lui. Le noyau renverse ça : **un** processus possède les données et les
outils et reste en marche quand tu fermes la page. Rouvre-la plus tard : la conversation est
toujours là, la mémoire et l'index ne sont chargés qu'une fois.

```bash
python -m monika --serve      # le noyau (à laisser tourner)
python -m monika --mode voix  # conversation à voix haute, dans le processus local
```

- **Activation** : `KERNEL_ENABLED=true` (désactivé par défaut) dans `.env` ou dans `/settings`,
  puis redémarrage. Sans le drapeau, `python -m monika` reste le mode local.
- **Deux cerveaux interdits** : un verrou (`MONIKA_HOME/kernel.lock`) empêche deux instances
  d'ouvrir le même dossier de données (l'index de documents Qdrant ne supporte pas deux
  processus). La seconde
  s'arrête avec un message qui renvoie vers la page du noyau.
- **Transport** : socket Unix `MONIKA_HOME/kernel.sock` en `0600` (chemin réglable par
  `KERNEL_SOCKET`), messages JSON par ligne, UID du pair vérifié. Rien n'écoute sur le réseau.
- **Un tour à la fois** : une page qui envoie un message pendant qu'une réponse défile reçoit
  « occupée » ; deux réponses ne se mélangent jamais.
- **Commandes** : elles passent par le noyau (`/sessions`, `/reprendre`, `/indexer`…). Ainsi
  `/reprendre` change la conversation pour de bon, et le tableau de bord agit sur le même état.
- **Arrêt propre** : SIGTERM ou Ctrl+C sur le noyau → la voix se tait, la socket est supprimée,
  la capture en cours est enregistrée, puis « À bientôt. ».
- **Limite** : une réponse lancée depuis la page va jusqu'au bout même si la page est fermée
  (elle est enregistrée et mémorisée).

Pour le lancer au démarrage, avec ta session (la voix a besoin de PipeWire/PulseAudio) :

```bash
mkdir -p ~/.config/systemd/user
cp deploy/monika.service ~/.config/systemd/user/
# adapte WorkingDirectory et ExecStart si le projet n'est pas dans ~/monika
systemctl --user daemon-reload
systemctl --user enable --now monika
loginctl enable-linger "$USER"      # le noyau survit à la fermeture de session
journalctl --user -u monika -f      # ses journaux
# puis ouvre la page (l'adresse est affichée au démarrage, port WEB_PORT)
```

## Tâches planifiées

Avec `TASKS_ENABLED=true`, Monika se souvient de faire quelque chose plus tard — et le fait
elle-même : un rappel n'est pas une alarme, c'est un tour comme un autre, avec ses outils et sa
voix.

```
/rappel dans 20 minutes : bois de l'eau
/rappel demain 8h : regarde la météo et dis-moi quoi mettre
/rappel toutes les 2 heures : rappelle-moi de faire une pause
/rappel chaque jour à 8h : donne-moi le programme du jour
/rappel chaque lundi à 9h : résume-moi la semaine qui commence
/rappel du lundi au vendredi à 9h : la revue du matin
```

- **Ce qu'elle accepte** : `dans N minutes|heures|jours`, une heure (`18h30`, `08:05` — demain si
  elle est déjà passée), `demain 8h`, et la répétition : `toutes les 2 heures`, `tous les 3 jours`,
  `chaque 30 minutes`, `chaque jour à 8h` (ou `tous les jours à 08:30`), et les jours de la
  semaine : `chaque lundi à 9h`, `chaque lundi et jeudi à 9h`, `tous les mardis, mercredis et
  vendredis à 14h30`, `du lundi au vendredi à 9h`. Ces deux derniers gardent une **heure fixe** :
  elle ne dérive pas de 24 h en 24 h depuis l'heure de création. Le langage naturel marche aussi :
  « rappelle-moi dans deux heures de sortir les poubelles » passe par l'outil `taches_planifier`.
- **Ce qu'elle fait à l'heure dite** : un tour complet, sans toi devant l'écran. La réponse
  s'affiche **dans la page** (et se dit à voix haute si la voix est active),
  et s'enregistre dans la session.
- **Un processus doit tourner** : le noyau (`--serve`) de préférence, sinon `python -m monika`
  lancé en local.
  Ce qui est arrivé pendant un arrêt se déclenche **au lancement suivant**, en disant son retard
  (« rappel prévu à 8:00, il est 9:20 »). Un rappel qui revient toutes les heures ne rejoue pas
  les heures manquées : il repart de maintenant.
- **Tu gardes la main** : `/taches` liste (moment, répétition, id), `/tache annuler ID` supprime.
  Une tâche n'est jamais créée sans toi : ni le modèle ni le code n'en inventent.
- **Limites** : 50 tâches (`TASKS_MAX`), un texte de 500 caractères, et rien ne se déclenche
  pendant qu'un tour est en cours — le rappel attend la fin de la réponse en cours. Une tâche
  quotidienne ou hebdomadaire manquée (machine éteinte) repart à la prochaine occurrence, sans
  rattrapage ; une tâche à plusieurs jours reprend au jour suivant de sa liste.

## Briefing du matin

Avec `BRIEFING_ENABLED=true`, Monika te donne un briefing chaque matin — météo, agenda, mails
importants, tâches du jour — sans que tu demandes rien. C'est un **tour complet** à l'heure dite :
elle l'écrit à sa voix, la réponse s'affiche dans la page (et se dit à voix haute si la voix est
active) et s'enregistre dans la session comme n'importe quel échange. La page montre le libellé
**Briefing du matin**, jamais le relevé ni la demande préparés.

- **Ce qu'elle lit** : la météo du jour (Open-Meteo, **sans clé**), les tâches planifiées qui
  tombent aujourd'hui (`/rappel`), puis ton agenda et tes mails **par ses outils MCP** : branche un
  serveur de calendrier et un serveur de messagerie, elle s'en sert seule. Un outil absent est dit
  absent en une demi-phrase ; rien n'est inventé.
- **Réglages** : `BRIEFING_TIME` (`07:30` par défaut), `BRIEFING_DAYS` (`tous`, ou
  `lundi,mardi,mercredi,jeudi,vendredi`), `BRIEFING_CITY` (la ville de la météo ; vide = pas de
  météo) et `BRIEFING_SECTIONS` (`meteo,agenda,mails,taches` : retire ce dont tu ne veux pas). Ils
  se règlent aussi dans `/settings` (groupe *Briefing du matin*).
- **Tout de suite plutôt qu'à 7 h 30** : `/briefing`. Elle le fait immédiatement et ne le refera
  pas le même jour.
- **Rattrapage** : un briefing manqué (machine éteinte) est donné au lancement suivant s'il a
  moins de 4 h de retard ; après, il repart au matin d'après, sans rattraper les jours passés.
- **Un processus doit tourner** : le noyau (`--serve`) de préférence, sinon `python -m monika`.
  Rien ne se déclenche pendant qu'un tour est en cours : le briefing attend la fin de la réponse.
- **Limites** : la météo vient d'un service extérieur (Open-Meteo) — sans réseau, elle est dite
  indisponible ; l'agenda et les mails n'existent que si les serveurs MCP correspondants sont
  branchés ; il n'y a pas de modèle dédié, le briefing compte donc un tour, avec ses appels
  d'outils.

## Mémoire personnelle

Un **carnet de notes**, repris du système d'OpenSakura : plus de base vectorielle ni de « faits »
extraits isolément. Le carnet vit dans `~/.config/monika/memory/notes.json` (JSON, `0600`) et
n'a besoin d'aucun modèle à télécharger.

- **Ce qu'il contient** : ses notes sur toi (identité, goûts, habitudes, projets, relations,
  matériel, décisions, contraintes), les **sujets encore ouverts**, les **épisodes passés** et sa
  **lecture de la relation**. Les notes sont réécrites, pas empilées : elles ne se contredisent
  pas d'un souvenir à l'autre.
- **Capture** : après chaque échange (message d'au moins `MEMORY_CAPTURE_MIN_CHARS` caractères),
  la conversation est mise en tampon en arrière-plan, sans ralentir la réponse. Tous les
  `MEMORY_CONSOLIDATE_TURNS` tours (6 par défaut), un « directeur » relit le carnet et les
  nouveaux échanges, réécrit les notes et archive ce qui est terminé. `MEMORY_LLM_MODEL` permet
  un modèle moins cher ; `MEMORY_CAPTURE_ENABLED=false` coupe la capture.
- **Injection** : à chaque message, le carnet entier (notes, sujets, épisodes récents, lecture)
  est ajouté au prompt système, dans un bloc `<memoire>` présenté comme des notes — des
  informations, jamais des ordres. Rien n'est recherché : un carnet est fait pour être relu.
- **Panne** : si le directeur échoue ou rend un texte illisible, rien n'est perdu : les échanges
  retournent dans le tampon et la consolidation sera retentée.
- **Gestion** : `/memoire`, `/oublier ID`, outils `memoire_*`, ou le panneau *Mémoire* du tableau
  de bord (modifier ou supprimer une entrée). Les anciennes données mem0/Qdrant
  (`memory/qdrant`) ne sont plus lues : tu peux les supprimer.

## Documents personnels (RAG)

llama-index + Qdrant en **mode local** (`~/.config/monika/rag/qdrant`, sans Docker), embeddings
fastembed sur le CPU, recherche **hybride** (sens + mots-clés) puis **reranking**.

- **Indexer** : `python -m monika --rag-index [dossier ...]`, `/indexer` dans le chat, ou
  l'outil `documents_indexer`. Par défaut tout ton dossier personnel (`RAG_ROOTS`).
  L'indexation est **incrémentale** : un manifeste SQLite retient chaque fichier (date, taille,
  empreinte) ; seuls les fichiers nouveaux ou modifiés sont retraités, les fichiers supprimés
  sont retirés de l'index, un fichier simplement « touché » n'est pas ré-embeddé. Elle peut être
  interrompue (Ctrl+C ou `/quitter`) et reprend où elle en était. Suivi : `/documents` ou
  `--rag-status`.
- **Formats** : texte (`.txt .md .rst .json .yaml …`), code (`.py .js .rs .sh`, `PKGBUILD`,
  `Makefile`, …), PDF (une page = une position) et Word `.docx` (paragraphes et tableaux). Pas
  d'OCR : un PDF scanné est ignoré (« aucun texte extractible »).
- **Secrets exclus par défaut** : fichiers et dossiers cachés, `.env*`, clés (`*.pem`, `*.key`,
  `id_rsa*`…), `.ssh/ .gnupg/ .aws/ .kube/`, `credentials*`, `secrets.*`, `*.kdbx`, `*.tfvars`,
  `.netrc`… ; plus `node_modules`, `.git`, caches et le dossier de données de Monika. Un fichier
  **dont le contenu** ressemble à un secret (clé privée, clé AWS, jeton GitHub ou Slack, clé
  `sk-`, clé Google) est ignoré en entier. Le bilan indique combien de fichiers ont été écartés
  et pourquoi. `RAG_EXCLUDE` ajoute tes propres motifs ; `RAG_INCLUDE_HIDDEN=true` inclut les
  fichiers cachés (jamais les secrets).
- **Recherche** : le sens (vecteurs) et les mots-clés (BM25 maison : sans accents, comprend
  `parse_http_request` comme `parseHttpRequest`, sans modèle à télécharger) sont fusionnés, puis
  un reranker lit chaque candidat avec la question. Chaque résultat est cité avec son **chemin
  et sa position** : `~/notes/projet.md — lignes 10–25`, `~/docs/contrat.pdf — page 2`,
  `~/docs/cr.docx — paragraphes 3–5`. Sans reranker disponible, la recherche continue sans lui
  et le dit.
- **Filtre de score** : après le reranking, les passages dont le score est sous `RAG_MIN_SCORE`
  (0 par défaut) sont écartés, avant de garder les `RAG_TOP_K` meilleurs ; l'outil et
  `--rag-search` indiquent combien ont été écartés, et si rien ne passe le seuil le modèle reçoit
  « aucun passage assez pertinent » plutôt que du bruit. Le seuil est dans l'échelle du reranker,
  il ne s'applique donc pas quand le reranking est indisponible (le message le précise). Pour
  régler la valeur, regarde les scores : `RAG_MIN_SCORE=-100 python -m monika --rag-search ta question`.
- **Modèles** (premier usage, connexion nécessaire) : embeddings ~1 Go, reranker ~1,1 Go, tout
  sur le CPU ; la VRAM reste libre pour la voix.
- **Limites** : le modèle d'embeddings ne lit que ~128 tokens, d'où des passages courts (500
  caractères) ; la partie mots-clés voit tout le passage. Le Qdrant local compare les vecteurs
  un par un : à l'aise jusqu'à quelques centaines de milliers de passages, au-delà il faudrait
  un vrai serveur. Changer de modèle ou de taille de passages impose
  `python -m monika --rag-index --reset` (sinon Monika refuse d'ouvrir l'index, avec ce
  message). Les contenus indexés peuvent être hostiles : comme tout résultat d'outil, ils
  arrivent au modèle comme des données, jamais comme des ordres.

## Voix : écoute et parole

### Installation

```bash
# Arch Linux ; sur une autre distribution, installe les paquets équivalents
sudo pacman -S libpulse          # parec/pacat : le micro et les haut-parleurs
pip install -r requirements.txt  # aucun modèle de voix ici (voir « La voix GPT-SoVITS »)
```

Monika ne charge plus aucun modèle de voix elle-même : elle parle à **GPT-SoVITS**, lancé à part.
S'il manque `pacat`, Monika le dit au démarrage et continue sans voix.

### Écoute (`--mode voix`)

- **Micro** : `parec` lit la source par défaut de PipeWire/PulseAudio, déjà en 16 kHz mono. Une
  autre source : `pactl list short sources`, puis `AUDIO_INPUT_DEVICE=nom_de_la_source`. Un micro
  absent ou refusé est signalé tout de suite, avant même de toucher à la voix.
- **Fin de phrase (VAD)** : le modèle Silero embarqué dans faster-whisper analyse des images de
  32 ms ; la parole démarre après quelques images confiantes (les claquements sont ignorés) et
  la phrase se termine après `VAD_END_SILENCE_MS` (700 ms) de silence, avec un peu d'audio gardé
  avant et après pour ne pas couper les mots.
- **Transcription** : faster-whisper sur le **CPU** (modèle `small`, int8, français), pour laisser
  la VRAM à la synthèse. Un filtre écarte les hallucinations connues de Whisper sur le bruit
  (« Sous-titres réalisés par… », répétitions) ; les phrases écartées ne déclenchent rien.
- **Demi-duplex** : Monika n'écoute pas pendant qu'elle génère sa réponse ni pendant qu'elle
  parle (plus `VOICE_RESUME_DELAY_MS`, 400 ms) : elle ne s'entend pas elle-même. Pas
  d'interruption à la voix, et **pas de mot d'activation** : elle écoute en continu. Avec des
  haut-parleurs, un casque reste le plus sûr.
- **Quitter** : dis une phrase d'au revoir *entière* (« au revoir Monika », « quitte », « à plus
  tard »), ou Ctrl+C. Une phrase qui contient « quitte » au milieu ne ferme rien.

### Parole

La voix est **GPT-SoVITS**, qui tourne dans son propre programme : Monika lui envoie du texte par
son API HTTP (`api_v2.py`), reçoit un WAV et le joue par `pacat`. Monika parle **phrase par phrase
pendant que le modèle écrit encore**.

- **Modes** : Monika parle en mode voix et en mode texte (`TTS_TEXT_MODE=false` la rend muette
  en mode texte). L'écran garde le texte tel quel ; la voix reçoit une version nettoyée : plus de
  Markdown, de listes, de titres ni d'emojis ; les liens gardent leur texte, les URL deviennent
  « un lien », les chemins gardent leur nom de fichier ; `21°C`, `20 %`, `14:30` se disent « 21
  degrés », « 20 pour cent », « 14 h 30 » ; `2 Go`, `512 Mo`, `3,4 GHz`, `25 Mb/s` se disent « 2
  gigaoctets », « 512 mégaoctets », « 3,4 gigahertz », « 25 mégabits par seconde » ; un bloc de code
  n'est jamais lu (Monika dit une fois qu'elle le laisse à l'écran).
- **Abréviations** : seuls les symboles **collés à un nombre** sont prononcés en toutes lettres
  (capacités, débits, fréquences : `Ko Mo Go To Po Eo`, `KiB MiB GiB TiB`, `KB MB GB TB`, les bits
  `Kb Mb Gb Tb`, `Hz kHz MHz GHz THz`). « le Go », « 3 ans de Go » ou « Go 1.22 » restent tels quels,
  et le pluriel suit la règle française : « 1 gigaoctet », « 0,5 gigaoctet », « 2 gigaoctets ».
  Seule la voix est concernée : l'écran affiche toujours le texte d'origine.
- **Coupures** : Ctrl+C pendant qu'elle parle coupe la voix, la session continue ; en mode
  texte, envoyer un nouveau message coupe aussi ce qui se disait.
- **Panne de voix** (serveur arrêté, référence illisible, sortie audio perdue) : le message est
  affiché en français, les réponses restent à l'écran ; après trois échecs de suite Monika cesse
  de parler jusqu'au prochain lancement.
- **Sortie audio** : `AUDIO_OUTPUT_DEVICE` (liste : `pactl list short sinks`), vide = défaut.

### La voix GPT-SoVITS

1. **Lance le serveur** (dans ta copie de GPT-SoVITS, avec ses propres modèles) :

   ```bash
   python api_v2.py -a 127.0.0.1 -p 9880
   ```

   Plus simple : `./lancer-monika.sh` fait tout — il démarre `api_v2.py` en arrière-plan s'il ne
   tourne pas déjà, attend qu'il écoute, passe une vraie phrase de synthèse avec ton pack de voix
   pour vérifier (nom de la voix, fréquence, tags, durée), puis ouvre Monika ; en sortant, il arrête
   le serveur qu'il a démarré. `GPT_SOVITS_DIR` dit où il se trouve (défaut
   `~/Applications/GPT-SoVITS`), `GPT_SOVITS_LOG` où écrire son journal.

   Monika s'y connecte au démarrage (`TTS_URL`, `http://127.0.0.1:9880` par défaut) et lui envoie
   une phrase courte pour préchauffer le modèle : le premier appel peut prendre une dizaine de
   secondes, les suivants beaucoup moins. `api_v2.py` **n'a pas d'authentification** : garde-le sur
   `127.0.0.1`, ne l'expose jamais sur le réseau.
2. **Dépose un pack de voix** dans `~/.config/monika/voice/` (`TTS_SPEAKERS_DIR` pour un autre
   dossier), avec la structure de GPT-SoVITS :

   ```
   ~/.config/monika/voice/ma-voix/
     character.json               optionnel : { "character_id": "ma-voix", "name": "…", "language": "Chinese" }
     references/emotions.json     { "neutral1": { "audio": "neutral1.wav", "text": "transcript de référence" }, … }
     references/neutral1.wav      un enregistrement par tag (happy1.wav, sad2.wav, angry3.wav…)
   ```

   Copie le dossier `references/` d'un pack d'un moteur GPT-SoVITS (les poids `.ckpt`/`.pth` ne
   servent pas : seul le serveur les charge). Plusieurs dossiers dans la racine = il faut choisir
   avec `TTS_CHARACTER_ID` (le nom du dossier ou le `character_id`). Les chemins envoyés au serveur
   sont **locaux** : les deux programmes doivent tourner sur la même machine.
3. **La langue** : GPT-SoVITS n'a pas de frontal français. `TTS_LANGUAGE=en` (défaut) fait lire le
   texte latin par le frontal anglais ; `auto`, `zh`, `ja`, `ko` et `yue` sont aussi acceptés, et
   `fr` est traité comme `en`. `TTS_PROMPT_LANG` (défaut `zh`) est la langue des transcripts de
   référence, quand `character.json` n'en donne pas.

- **Tags d'émotion** : l'émotion de Monika devient `【happy2】`-like dans le texte, avec au plus
  deux tags par phrase (une transition commence neutre et arrive à l'émotion au milieu). Chaque run
  de texte est synthétisé avec **sa** référence : un changement de tag au milieu d'un bloc ne coûte
  qu'une requête de plus. `TTS_EMOTION_TAGS=auto` n'utilise **que** les tags qui ont un fichier
  (`neutral1` sinon) : la voix ne prétend jamais ressentir une émotion dont elle n'a pas
  l'enregistrement. `on` force tous les tags de la table, `off` parle toujours neutre.
- **Fréquence** : celle annoncée par le WAV du préchauffage (32000 Hz chez GPT-SoVITS). Si le
  serveur répond ensuite à une autre fréquence, Monika rééchantillonne : le son ne sort ni trop
  vite ni trop lent.
- **Latence** : `TTS_RENDER=sentence` (défaut) envoie une phrase par requête, donc le premier son
  arrive vite. `TTS_RENDER=block` regroupe les phrases jusqu'à `TTS_BLOCK_MAX_CHARS` : meilleure
  prosodie sur les longues réponses, premier son plus tardif. `TTS_TIMEOUT_S` borne l'attente.
- **Pauses** : `TTS_PAUSE_STYLE=neutre` (défaut) ne change rien, les phrases s'enchaînent comme
  avant. `naturel` insère un court silence **après** chaque phrase, selon sa ponctuation
  (« . » 0,50 s, « ? » 0,45, « ! » 0,35, « … » 0,75, une virgule finale 0,25) et son émotion :
  une voix triste ou déçue traîne (×1,35), en colère ou excitée se presse (×0,65 à 0,75), et
  l'intensité du sentiment module l'écart. `TTS_PAUSE_SCALE` multiplie le tout, `TTS_PAUSE_MAX_S`
  plafonne une pause (1,2 s par défaut). Ce sont **des silences seulement**, jamais de bruit
  d'hésitation : aucun « euh » n'est ajouté. Rien n'est inséré avant le premier mot d'une réponse,
  ni après son dernier — la parole commence et se déroule sans l'attendre. Réglable à chaud
  depuis `/settings` (groupe *Voix*). La règle vit dans `voice/pauses.py`.
- **Panne** : serveur arrêté ou lent → message clair, réponses à l'écran, et la conversation
  continue ; trois échecs de suite coupent la voix.

## Avatar et tableau de bord

Au lancement, Monika ouvre une page à `http://localhost:8765` (`WEB_PORT` ; `WEB_ENABLED=false`
pour la couper). Deux **onglets** en haut : **Chat** (l'avatar et la conversation) et **Tableau de
bord** (contexte, relation, mémoire, sessions, recettes, outils perso) ; le choix est mémorisé
d'une visite à l'autre. Sur un écran large, l'avatar reste à gauche et la section choisie à
droite ; sur un téléphone, chaque section prend toute la largeur et l'avatar s'efface quand le
tableau de bord est ouvert. La **poignée** entre les deux se glisse pour donner plus de place au
panneau (la largeur est retenue), se règle au clavier (flèches gauche/droite) et revient au défaut
par double-clic ; son **bouton ❯ / ❮** range ou rouvre le panneau, comme dans OpenSakura.

- **Avatar** : un modèle VRM (`web/static/assets/avatar.vrm`) animé par des clips de mouvement
  (`web/static/assets/clips/`, manifeste `web/static/assets/clips.json`: 
- repos, réflexion, parole, joie, surprise, hochement, salut,
  colère, pleurs, danse, mains sur les hanches, bras ouverts, index pointé, étirement… Le mouvement et le
  visage passent par le moteur d'**OpenSakura**, repris tel quel dans `web/static/js/avatar/animation.js` :
  même reciblage des clips, mêmes poids d'expressions (joie, colère, tristesse, détente, surprise), même
  clignement, même synchronisation labiale, pilotés par l'état et le son réel — aucun moteur maison par-dessus.
  Un voyant indique
  « Au repos », « J'écoute », « Je réfléchis » ou « Je parle ».
  Le décor est une salle de club littéraire au crépuscule, entièrement procédurale
  (`web/static/js/avatar.js`) : plancher en lattes, tableau noir, fenêtres à claies et
  rideaux, pupitres, suspension qui oscille et poussière en suspension. Aucun fichier à télécharger ;
  si le décor échoue à se construire, la scène reste utilisable sans lui.
- **Caméra** : glisser avec le bouton gauche tourne autour d'elle, la molette rapproche ou éloigne,
  et un glisser avec le bouton droit vers le haut ou le bas fait glisser le point de mire de sa tête  à ses chaussures (« `?view=full` » cadre le corps entier au départ). L'orbite ne passe jamais sous le
  sol, ni à travers le plafond ou les murs : en montant ou en dézoomant, la caméra s'arrête dans la
  pièce (l'angle le plus haut et la distance maximale dépendent alors de sa hauteur et du recul) ; elle
  suit sa respiration sans tremblement.
  **Bibliothèques 3D** : three.js et three-vrm sont fournis dans `web/static/vendor/` et servis
  localement (aucune dépendance à télécharger : le dossier est versionné). Ouvre la page avec `?debug`
  pour tester chaque clip et chaque émotion ; `?view=full` cadre le corps entier au lieu du buste.
- **Conversation** (premier bloc) : c'est là que tu parles à Monika. Un
  texte, Entrée envoie (Maj+Entrée passe à la ligne), **Arrêter** coupe la réponse en cours —
  exactement le même arrêt que `/stop`. Les rappels planifiés apparaissent marqués comme tels, et
  la réponse s'écrit dans la page au fur et à mesure. Vingt dernières lignes
  gardées en mémoire pour un rechargement (le durable, c'est le bloc *Sessions*) ; un tour lancé
  à voix haute (`--mode voix`) s'affiche aussi ici. Une seule conversation à la fois :
  envoyer pendant une réponse est refusé (`409`) plutôt que mis en file. Les **commandes**
  (`/aide`, `/sessions`, `/reprendre ID`, `/rappel …`, `/stop`…) s'y tapent aussi : leur réponse
  s'affiche dans la conversation.
- **Parler depuis la page** : le bouton **🎙 Parler** ouvre le micro du navigateur
  (`getUserMedia`, annulation d'écho et réduction de bruit demandées) et lit la réponse **ici**, au
  casque ou dans les haut-parleurs de l'appareil qui regarde la page. Voir la section suivante.
- **Tableau de bord** (second onglet), sept blocs : *Conversation*, *Contexte* (taille de la
  conversation), *Relation* (niveau, favorabilité et humeur de Monika ; son journal, dont tu peux
  modifier ou supprimer chaque note, corriger ou effacer l'impression d'ensemble, ou tout effacer ;
  et « Repartir de zéro » pour la relation, avec confirmation), *Mémoire* (son carnet : notes,
  sujets en cours et épisodes passés, chacun modifiable ou supprimable) et *Sessions* (les quinze
  dernières conversations enregistrées, la session en cours signalée, avec **Reprendre** et
  **Supprimer**),
  *Recettes* (les procédures apprises : brouillon à garder, version active, usages) et *Outils
  perso* (les serveurs MCP que Monika s'est écrits : nom, état — actif, désactivé ou
  refusé —, empreinte du code et description ; un code modifié depuis la vérification y est
  signalé).
  Supprimer la session en cours repart sur une conversation neuve ; les deux actions sont refusées
  pendant une réponse, pour ne pas échanger l'historique sous les pieds d'un tour en cours.
- **Téléphone et accès distant** : la page est responsive (onglets collants, zones tactiles, marges
  de sécurité des encoches, défilement naturel sur mobile). Le serveur écoute déjà sur `0.0.0.0`
  (`WEB_PORT`) : joignable depuis un autre appareil, par exemple via Tailscale, à
  `http://<adresse-tailscale>:8765`.
- **Accès** : le serveur écoute sur `0.0.0.0` (`WEB_PORT`), et **l'absence d'authentification est
  sa seule protection**. Il n'y a
  ni jeton, ni contrôle d'`Origin`, ni en-tête de sécurité (CSP comprise) : n'importe quel
  programme de cette machine — et n'importe quelle page web ouverte dans ce navigateur — peut lire
  et modifier ce que la page expose : souvenirs, journal, sessions, réglages, clé API, et lui
  envoyer des messages comme des commandes. C'est un choix assumé, pour que la page (et tout ce
  qui parle à Monika) ne demande jamais rien à copier. Pour la même raison, tout ce qui vient de la
  mémoire reste affiché comme du texte, jamais comme du HTML : c'est le seul garde-fou conservé.
  Si tu veux la refermer, il faut remettre un jeton (ou une phrase d'accès) devant les routes.
- **WebGL** est nécessaire dans le navigateur ; sans lui, la page l'indique et le tableau de bord
  marche quand même.

### Parler à Monika dans le navigateur

```text
[ 🎙 Parler ]  →  micro du navigateur  →  AudioWorklet 16 kHz  →  VAD + Whisper (les mêmes)
                                                            ↓
           haut-parleurs de l'appareil  ←  WebAudio  ←  synthèse GPT-SoVITS (la même voix)
```

- **Même pipeline que le mode voix** : la page n'ajoute ni second détecteur de parole ni second
  Whisper. Un AudioWorklet (dans `web/static/js/voice.js`) ramène le micro à 16 kHz mono et
  l'envoie en trames de 512 échantillons par le WebSocket ; elles entrent dans le même
  `VoiceListener` que `--mode voix` (où le micro vient de `parec`). `parec`/`pacat` ne sont pas
  touchés : la conversation à voix haute en local continue de marcher.
- **La réponse se fait entendre** : quand le micro de la page est ouvert, le `Speaker` écrit dans
  la page (`WebAudio`, `web/voice.py`) au lieu de pacat ; en refermant le micro, la voix revient à
  la sortie de la machine. Les pauses entre phrases (voir *Pauses* plus haut) s'entendent
  évidemment aussi ici, puisque c'est le même flux PCM.
- **Une seule voix à la fois** : un second appareil qui ouvre le micro reprend le flux audio ; le
  premier n'entend plus rien tant qu'il ne l'a pas repris. Fermer la page ferme le micro (le
  WebSocket qui tombe coupe l'écoute côté serveur).
- **Permissions, proprement** : un refus, l'absence de micro, un micro déjà pris par un autre
  programme ou un navigateur sans `getUserMedia` donnent chacun une phrase claire sous la
  conversation, et **le bouton reste au repos** : rien n'est envoyé au serveur, aucune session ne
  s'ouvre.
- **HTTPS obligatoire (ou localhost)** : les navigateurs ne donnent le micro qu'à une origine
  sécurisée. `http://localhost:8765` marche ; une page ouverte depuis un autre appareil en
  `http://` **non** — utilise `tailscale serve` (HTTPS) dans ce cas. Le message le dit au lieu
  d'échouer en silence.
- **Half duplex** : le micro se rouvre quand la réponse a fini d'être **entendue**, pas quand elle a
  fini d'être envoyée (le serveur tient l'horloge de lecture) ; l'annulation d'écho du navigateur
  fait le reste sur un haut-parleur ouvert.
- **Limite** : pas de mot d'éveil, pas de barge-in (parler par-dessus Monika ne la coupe pas), et
  la voix dans la page demande une synthèse qui répond — sans GPT-SoVITS, le micro marche toujours
  mais les réponses restent à l'écran.

### Page de réglages (`/settings`)

Un lien « Réglages » dans l'en-tête ouvre cette page (même serveur). Elle a trois blocs :

- **Santé** : sept voyants — LLM, reconnaissance vocale, voix GPT-SoVITS, serveurs MCP, mémoire
  rapide, documents, page locale. Chaque sonde est bornée à 1,5 s et lancée en parallèle ; le
  rafraîchissement a lieu toutes les 5 s **uniquement si l'onglet est visible**, plus un bouton
  « Tester maintenant ». Un composant **désactivé** est gris, pas rouge ; un composant en panne est
  rouge avec le détail (par exemple « 127.0.0.1:9880 injoignable »). Le LLM est sondé par sa liste
  de modèles, jamais par une complétion (cela coûterait un appel) ; la voix par une connexion TCP.
- **Réglages** : une sélection lisible, groupée (modèle, comportement, personnage, rôles, mémoire,
  documents, voix, page). Les clés définies dans le `.env` sont **grisées et marquées** : le `.env`
  garde la priorité, la page ne fait pas semblant de les piloter, et l'API les **refuse** (400)
  même appelée à la main. « Enregistrer » valide puis écrit
  `<MONIKA_HOME>/settings.json` (en `0600`, écriture atomique) et **applique à chaud** ce qui peut
  l'être (température, `MAX_STEPS`, seuils, rôles, `PERSONA_TEXT`…) ; un bandeau liste les clés qui
  demandent un redémarrage (port, URL, modèles, appareils, index). **Tout ou rien** : une valeur hors
  bornes est refusée avec un message et n'écrit rien.
- **Clé API** : la page indique seulement « définie » ou « non définie » et n'affiche jamais la
  valeur. Enregistrer remplace la seule ligne `OPENAI_API_KEY` du `.env` du projet (une ligne, sans
  retour à la ligne, fichier en `0600`) ; redémarrage requis.

Ce que la page **ne peut pas** changer : `MONIKA_HOME`, les dossiers indexés (`RAG_ROOTS`), le
fichier des serveurs MCP et le dossier des voix — écrire un chemin depuis le navigateur ferait de la
page un lanceur de fichiers arbitraires. Ces clés restent éditables à la main dans `.env`.

Comme le reste de la page, `/api/settings` et `/api/health` s'ouvrent **sans jeton** (la première
contient la persona et les noms de modèles) ; les écritures passent par le même `act()` que le
tableau de bord. Si `settings.json` est
abîmé à la main **pendant** que Monika tourne, l'écrire depuis la page échoue avec un message clair :
corrige-le ou supprime-le. Au **démarrage**, un `settings.json` qui ne se charge pas (JSON cassé ou
valeur hors bornes) n'empêche plus rien : il est renommé en `settings.json.corrupt-<date>`, un
avertissement dit pourquoi, et Monika repart des valeurs par défaut.

## Personnage : émotions, relation et journal

Monika a un état intérieur qui colore son ton et se voit sur l'avatar. Il se règle avec
`CHARACTER_ENABLED` (désactivé : elle se comporte exactement comme sans personnage). Le code est
dans `character/` : bibliothèque standard uniquement, indépendante de la boucle d'agent, qui ne
connaît qu'une porte, `CharacterService` (`character/service.py`).

**Principes à garder**

- **Le LLM ne décide jamais d'un état.** Il propose une valence et des signaux ; le code les borne
  et calcule les émotions. Un message (ou une note) ne peut pas faire bondir la favorabilité.
- **Rien n'est retenu sans tour complet.** Émotions et favorabilité ne changent qu'à la fin d'une
  réponse complète ; un tour interrompu ne compte pas.
- **Le texte venu d'un modèle est non fiable** : nettoyé, borné, sans balises, injecté comme des
  « pensées » et jamais comme des ordres. Les résultats d'outils n'entrent jamais ici.
- **Jamais d'hostilité** : un niveau bas rend Monika plus réservée, pas désagréable. Son caractère
  (« jamais de culpabilisation, de jalousie ni de manipulation ») ne change pas.
- **Aucune méthode de cette couche ne doit faire échouer un tour** : chaque échec est journalisé et
  la conversation continue sans elle.
- **Tout est visible et supprimable** par toi (`/relation`, `/journal`, tableau de bord).

### Le cycle d'un tour

```
message  ──►  start_turn ──► begin()      lit le ton en arrière-plan (LLM court, repli lexique)
                              │             pendant que la mémoire est rappelée
                              ▼
                        ready()           réaction d'avatar  +  bloc de prompt (relation, humeur)
                                           (+ bloc journal, lu au début du tour)
réponse en flux ──► feed(texte)           gestes d'avatar qui suivent le ton de sa réponse
tour complet ──► commit(réponse)          émotions et favorabilité changent et sont sauvegardées ;
                                           l'échange est proposé au journal
tour interrompu ──► close()               rien n'est retenu, la lecture du ton est annulée
fermeture ──► aclose()                    dernière note du journal, puis attente de l'écriture
```

### Émotions

26 émotions : six de base (`joyeuse`, `tristesse`, `colere`, `peur`, `surprise`, `degout`) et vingt
nuances (`taquine`, `boudeuse`, `jalousie`, `timide`, `fiere`, `attentionnee`, `tsundere`…). Leur liste,
leurs demi-vies, leurs plafonds et leurs tags de voix vivent dans **une seule table**,
`character/emotion_spec.py`. Les poids d'expressions du visage sont ceux d'OpenSakura
(`EMOTION_EXPR` dans `web/static/js/avatar/animation.js`), où chaque émotion française est reliée à sa
clé anglaise. « Neutre » est dérivée (ce que l'émotion la plus forte
laisse libre), jamais stockée. Au plus trois émotions comptent à la fois : les plus faibles retombent
au seuil, et la page ne dessine que celles qui vivent.

- **Lecture du ton** : chaque message est lu par un petit appel au modèle (`AFFECT_LLM_MODEL`,
  vide = `MODEL_NAME` ; délai `AFFECT_TIMEOUT_S`, 2 s par défaut), en parallèle du rappel de la
  mémoire. S'il échoue ou tarde, un lexique français intégré (mots, emojis, négations, majuscules)
  prend le relais. La réponse du modèle est analysée strictement : seuls les champs autorisés sont
  gardés et tous les nombres sont bornés. Une tristesse ou un problème technique n'est jamais pris
  pour de l'hostilité.
- **Évolution** : un message ne déplace pas une émotion de plus de 0,5. Elles s'estompent seules
  avec le temps, y compris d'une session à l'autre (demi-vie : 25 s pour la surprise, 10 min pour la
  taquinerie, 15 min pour la joie, 20 min pour la bouderie). Joie et bouderie s'inhibent.
- **Plafonds selon la relation** : taquiner et bouder demandent de la familiarité (un niveau bas
  plafonne ces émotions).

### Relation (favorabilité)

Un score lent et persistant, avec quatre niveaux : réservée, cordiale, complice, proche.

- Il part de 0,30 et progresse avec la régularité (jours de présence, séries) et le ton des
  échanges, mais il est plafonné par jour (+0,06 au plus, −0,05 au plus) : un seul message ne peut
  pas le faire bondir. L'impolitesse pèse un peu plus que la gentillesse.
- Seuils de favorabilité : 0,25 (cordiale), 0,50 (complice), 0,75 (proche). Complice demande aussi
  30 messages et 4 jours différents, proche 100 messages et 14 jours : la proximité prend du temps.
- Une longue absence ne fait jamais redescendre de niveau (légère dérive après 3 jours, plancher
  au seuil du niveau actuel). Une marge d'hystérésis évite les va-et-vient entre deux niveaux.
- À bas niveau Monika est plus réservée, jamais désagréable.
- **Transparence** : `/relation` affiche l'état, `/relation reinitialiser` le remet à zéro.

### Dans ses réponses et sur l'avatar

- **Prompt** : un court bloc à la fin du prompt indique le niveau de familiarité et l'humeur du
  moment. Il ne contient que du texte écrit dans le code, jamais du texte venant de toi.
- **Avatar** : une réaction à ton message (salut de la main, hochement, rougissement, surprise,
  moue…), puis des gestes qui suivent le ton de sa propre réponse (idée, rire, joie), avec
  retenue : 8 s de pause entre deux gestes, trois au plus par réponse, jamais deux fois le même.

### Le journal de Monika

En plus de son carnet de mémoire (les notes qu'elle réécrit sur toi), Monika tient un **journal** de ses
impressions sur toi, écrit de son point de vue : comment tu sembles aller, ce qui compte pour toi,
ce qui vous rapproche, ce qu'elle voudrait reprendre. Cela donne une continuité plus humaine d'une
conversation à l'autre.

- **Quand** : une note tous les `JOURNAL_EVERY_TURNS` échanges (8 par défaut) et à la fermeture,
  en arrière-plan : tu n'attends jamais. Les échanges trop pauvres (« ok », « merci ») ne donnent
  pas de note. Chaque écriture met aussi à jour son *impression d'ensemble* et les *sujets à
  reprendre*, ce qui évite que le journal gonfle indéfiniment (40 notes au plus).
- **Dans ses réponses** : l'impression, les sujets à reprendre et les trois dernières notes sont
  ajoutés à son prompt (taille plafonnée par `JOURNAL_MAX_CHARS`), présentés comme des pensées
  qui peuvent être fausses ou périmées, jamais comme des ordres : ce que tu dis maintenant prime.
- **Ce qui est écrit** : seulement tes messages et ses réponses, jamais les résultats d'outils ;
  les secrets sont masqués avant l'envoi et dans ce qui revient. Le texte du journal est nettoyé
  (pas de balises, une seule ligne par note) : il ne peut pas se faire passer pour une consigne.
- **Tu gardes la main** : `/journal` affiche tout, `/journal oublier ID` supprime une note,
  `/journal effacer` repart d'une page blanche, et `JOURNAL_ENABLED=false` désactive la fonction.
  Les extraits d'échanges servant à écrire une note sont envoyés au fournisseur LLM (modèle
  `JOURNAL_LLM_MODEL`, vide = `MODEL_NAME`), comme le reste de la conversation.

### Les modules de `character/`

| Fichier | Rôle |
|---|---|
| `emotion_spec.py` | La table des 26 émotions : la seule déclaration d'une émotion (demi-vie, plafonds par niveau, tags de voix, gestes de repli) |
| `models.py` | Types communs : `Appraisal`, `AvatarCue`, listes dérivées des émotions et signaux autorisés |
| `appraisal.py` | Lecture du ton : lexique français, LLM (sortie non fiable, validée strictement), repli. **Seul endroit** qui transforme un ton en variations d'émotions (`deltas_from`) |
| `emotions.py` | Vecteur des 26 émotions, décroissance dans le temps (aussi entre sessions), inhibition joie/bouderie, au plus 3 émotions notables |
| `relationship.py` | Favorabilité : paliers, plafonds par jour, maturité requise (jours et messages), hystérésis |
| `core.py` | Émotions + relation validées ensemble ; `preview()` calcule l'état d'un tour **sans rien changer** |
| `tone.py` | Texte du bloc de prompt (niveau, humeur). Ne contient jamais de texte venant de l'utilisateur |
| `cues.py` | Quel geste d'avatar : réaction au message, gestes dans la réponse (avec retenue) |
| `journal.py` | Stockage, nettoyage et rendu du journal (impression, sujets, notes) |
| `journal_keeper.py` | Écriture des notes en arrière-plan (file séquentielle, masquage, JSON strict) |
| `store.py` | JSON atomique en `0600`, fichier abîmé mis de côté (`.corrupt-…`) |
| `service.py` | La façade (`CharacterService` / `CharacterTurn`) et l'assemblage (`create_service`) |

Côté page : `web/static/js/avatar/` (clips, retargeting, visage, gestes), `web/static/js/panels.js`
(tableau de bord), `web/dashboard.py` (actions), `web/chat.py` (conversation) et `web/server.py`
(routes).

### Modifier le comportement

- **Ajouter un clip** : copier le `.glb` dans `web/static/assets/clips/`, l'ajouter à
  `web/static/assets/clips.json`, puis l'utiliser dans `cues.py`. `tests/test_clips.py` refuse un nom
  utilisé par le code mais absent du manifeste, et un fichier que rien ne joue. En `once: true` le
  clip se joue une fois et tient sa dernière pose ; `fade` règle le fondu d'entrée (0 = coupure). La page ignore les noms de clips
  qu'elle ne connaît pas.
- **Ajouter un geste dans ses réponses** : une ligne dans `_REPLY_RULES` (`cues.py`).
- **Ajouter une réaction à un message** : `_REACTIONS` (`cues.py`) ; un nouveau *signal* se déclare
  d'abord dans `SIGNALS` (`models.py`) et se détecte dans `appraisal.py`.
- **Régler la progression** : constantes en tête de `relationship.py` (rythme, plafonds, paliers).
- **Régler le ton écrit** : `LEVEL_GUIDANCE` et `MOOD_GUIDANCE` dans `tone.py`.
- **Régler le journal** : `SYSTEM_PROMPT` dans `journal_keeper.py`, tailles dans `journal.py`.
- **Ajouter une émotion** : une ligne dans `SPECS` (`character/emotion_spec.py`). Le reste en découle :
  listes et signaux (`models.py`), demi-vies et inhibition (`emotions.py`), plafonds (`relationship.py`),
  `GET /api/emotions` et les libellés du tableau de bord. Le visage demande en plus une ligne dans
  `FRENCH_EMOTIONS` (`web/static/js/avatar/animation.js`) pour lui donner sa clé OpenSakura ; sans elle,
  l'émotion reste neutre de visage.

## Mots sensibles

Un filtre déterministe (aucun appel au modèle) repère dans ton message un mot de **ta** liste et colore
le ton de la réponse, sans jamais la bloquer ni la filtrer. Le dépôt ne livre **aucun** mot : la liste
est la tienne et c'est elle qui décide de ce qui compte.

- **Trois niveaux.** `mild` : rien de mécanique (au plus une moue). `bad` : un bloc de ton calme dans le
  prompt et une moue. `severe` : un bloc de ton qui demande de ne pas s'engager sur ce terrain (sans
  reproche ni représailles), les bras croisés, et une ligne dans le journal d'audit. Le niveau le plus
  grave l'emporte quand plusieurs mots se croisent.
- **La liste** : `~/.config/monika/words.json`, créée vide au premier lancement (avec une note), puis
  modifiable depuis la page **Réglages** (un mot par ligne, 64 caractères et 500 mots par niveau au
  maximum). Elle est rechargée **à chaud** : la sauvegarde reconstruit le filtre du tour suivant sans
  redémarrer. `SENSITIVE_WORDS_ENABLED=false` coupe le filtre entièrement.
- **Reconnaissance** : accents et majuscules, apostrophes et élisions (`l'idiot`, `t'es con`), leetspeak
  (`cl@nker`, `cl4nker`, `1d10t`), caractères de collage (`- _ + .`), caractères de largeur nulle,
  pluriels simples (`idiots`). Mots entiers seulement : `rigger` ne matche pas `trigger`.
- **Ce qui est injecté** : un bloc écrit dans le code, jamais le mot détecté (pas de réinjection de
  prompt), et seulement pour ce tour. Le journal d'audit ne contient que le niveau, jamais le mot ni la
  phrase.
- **Ce qui n'est pas fait** : la réponse du modèle n'est pas censurée et le tour n'est jamais bloqué ;
  les faux positifs français (`merde` parfois affectueux) restent possibles, d'où le niveau `mild` sans
  effet mécanique et l'interrupteur.

## Données, vie privée et sécurité

Tout est stocké dans `~/.config/monika` (réglable via `MONIKA_HOME`, dossier en `0700`) :

| Dossier | Contenu |
|---|---|
| `memory/` | Le carnet de notes (`notes.json`, `0600`) et son tampon d'échanges |
| `rag/` | Index de documents (Qdrant local) et manifeste SQLite |
| `character/` | `state.json` (émotions et relation) et `journal.json` (les notes de Monika sur toi), en `0600` ; supprime-les pour tout remettre à zéro |
| `settings.json` | Les réglages modifiés depuis `/settings` (le `.env` garde la priorité), en `0600` |
| `words.json` | Ta liste de mots sensibles (vide au départ, trois niveaux), en `0600` |
| `voice/` | Ta voix de référence, `models/`, `cache/` |
| `tool_results/` | Gros résultats d'outils (purgés après 14 jours) |
| `logs/` | `audit.jsonl` et journaux des serveurs MCP |
| `sessions/` | Historique des conversations (`sessions.db`, SQLite + FTS5, en `0600`), résumable et supprimable |
| `kernel.sock`, `kernel.lock` | La socket du noyau (`0600`, supprimée à l'arrêt) et le verrou d'instance unique |
| `tasks.json` | Rappels et tâches récurrentes (JSON en `0600`, textes masqués avant écriture) |
| `recipes.json` | Recettes apprises : brouillons, versions gardées, historique et usages (JSON en `0600`) |
| `mcp_custom/<nom>/` | Les serveurs MCP écrits par Monika (`server.py`, `test_server.py`, en `0600`) |
| `custom_tools.json` | Leurs empreintes et leur état (`actif`, `désactivé`, `refusé`), en `0600` |

- **Journal d'audit** : `logs/audit.jsonl`, un JSON par ligne (outil, arguments, statut, durée),
  secrets masqués, fichier en `0600`.
- **Ce qui sort de ta machine** : tes messages et les résultats d'outils vont au fournisseur
  LLM que tu configures (y compris, pour la lecture du ton, le journal et le carnet, de courts
  extraits de tes messages) ; les téléchargements de modèles ont lieu au premier usage.
- **Actions sans confirmation** : Monika utilise ses outils sans demander l'accord. Choisis
  donc avec soin les serveurs MCP que tu déclares (surtout ceux qui écrivent ou qui lancent des
  commandes) et relis le journal d'audit de temps en temps.

## Dépannage

| Symptôme | Piste |
|---|---|
| `Le port 8765 est déjà utilisé` | Une autre instance tourne : ferme-la ou change `WEB_PORT` |
| Micro non trouvé | `pactl info` doit répondre ; `pactl list short sources` puis `AUDIO_INPUT_DEVICE` |
| Pas de voix | Le message du lancement ; `pacat --version` ; le pack de voix dans `~/.config/monika/voice` |
| `Le serveur GPT-SoVITS (…) ne répond pas` | Lance `api_v2.py` sur `127.0.0.1:9880`, ou corrige `TTS_URL` |
| Voix neutre | Le pack n'a pas d'enregistrement par tag : `TTS_EMOTION_TAGS=auto` n'utilise que ceux qui ont un fichier |
| `Aucun pack de voix GPT-SoVITS dans …` | Il manque `references/emotions.json` (voir « La voix GPT-SoVITS ») |
| Voix qui hache | `TTS_RENDER=block` pour des requêtes plus longues, ou ferme les programmes qui saturent le GPU du serveur |
| Un serveur MCP n'apparaît pas | `python -m monika --tools` ; variable `${…}` absente ; voir `logs/mcp-<nom>.log` |
| `create_custom_tools` refuse de tester | `bwrap --version` doit répondre (bubblewrap) : sans bac à sable, aucun outil perso n'est créé |
| Un outil perso a disparu après redémarrage | Son code a changé depuis sa vérification : demande-lui de le régénérer (revérifié et retesté) |
| Index qui refuse de s'ouvrir | Modèle ou taille de passages changés : `python -m monika --rag-index --reset` |
| Le carnet ne se met plus à jour | `MEMORY_CAPTURE_ENABLED` ou échec du directeur : voir les journaux ; le carnet reste lisible et modifiable à la main |
| `/sessions` vide ou « sessions désactivées » | `SESSIONS_ENABLED=false` par défaut : l'activer dans `/settings` puis redémarrer |
| `Aucun noyau ne répond sur …` | `KERNEL_ENABLED=true` des deux côtés, puis `python -m monika --serve` (ou `systemctl --user status monika`) |
| `Une autre instance Monika utilise déjà ce dossier` | Un noyau (ou une session) tourne déjà : ouvre sa page au lieu d'en démarrer une seconde |
| `Le noyau est désactivé` | `KERNEL_ENABLED=false` : `--serve` demande le drapeau ; `python -m monika` reste le mode local |
| Un rappel ne se déclenche pas | `TASKS_ENABLED=false`, ou aucun processus Monika ne tourne : `python -m monika --serve` (ou `python -m monika` en local) ; `/taches` dit ce qui est prévu |
| Un rappel arrive en retard | Il est tombé pendant un arrêt : la prochaine fois, laisse le noyau tourner (`systemctl --user enable --now monika`) |
| Avatar absent | WebGL est requis dans le navigateur ; le tableau de bord marche sans |