# Monika

Assistante IA personnelle, locale et en français, pour Linux (Python 3.12+).
Inspirée du personnage de Monika (DDLC). Usage strictement personnel.

Monika discute dans le terminal ou à la voix (avec **ta** voix clonée), agit grâce à des outils
MCP, se souvient de ce qui compte pour toi, retrouve tes documents
et s'affiche sous forme d'avatar 3D avec un petit tableau de bord dans le navigateur.
Tout est stocké chez toi ; seul le fournisseur LLM que tu configures reçoit tes messages.

> **État : projet terminé.** Socle et configuration, mode texte en streaming, client MCP,
> registre d'outils audité, boucle ReAct, gestion du contexte, prompt système, mémoire rapide,
> documents personnels (RAG), reconnaissance vocale, synthèse vocale, avatar et tableau de bord.

## Sommaire

1. [Prérequis](#prérequis)
2. [Installation et premier lancement](#installation-et-premier-lancement)
3. [Commandes](#commandes)
4. [Configuration](#configuration)
5. [Outils (serveurs MCP)](#outils-serveurs-mcp)
6. [Agent, contexte et prompt](#agent-contexte-et-prompt)
7. [Mémoire rapide](#mémoire-rapide)
8. [Documents personnels (RAG)](#documents-personnels-rag)
9. [Voix : écoute et parole](#voix--écoute-et-parole)
10. [Avatar et tableau de bord](#avatar-et-tableau-de-bord)
11. [Données, vie privée et sécurité](#données-vie-privée-et-sécurité)
12. [Dépannage](#dépannage)

## Prérequis

- **Linux** avec PipeWire ou PulseAudio (le son passe par `parec` et `pacat`, paquet `libpulse`) ;
  vérifie avec `pactl info`.
- **Python 3.12+**.
- Un accès à une **API compatible OpenAI** (clé et URL dans `.env`).
- Pour la voix : `ffmpeg`, PyTorch, et idéalement un GPU (4 Go suffisent ; sinon la synthèse
  tourne sur le CPU, plus lentement).
- Pour les serveurs MCP de l'exemple : Node.js (`npx`).
- Espace disque pour les modèles téléchargés au premier usage : environ 1 Go (mémoire), 2 Go
  (documents), 2 Go (voix) ; une connexion est nécessaire à ce moment-là.

## Installation et premier lancement

Lance toujours Monika **depuis le dossier du projet** (`.env` y est cherché ; le projet n'est pas
prévu pour être installé comme paquet).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis édite LLM_BASE_URL, OPENAI_API_KEY, MODEL_NAME
python -m monika --config     # vérifie la configuration (secrets masqués)
python -m monika --mode texte # discussion dans le terminal (/aide pour les commandes)
```

Le mode voix demande une installation supplémentaire, décrite dans [Voix](#voix--écoute-et-parole).

## Commandes

| Commande | Effet |
|---|---|
| `python -m monika` | Demande le mode au lancement (texte ou voix) |
| `python -m monika --mode texte` | Discussion dans le terminal, réponses en streaming |
| `python -m monika --mode voix` | Conversation à voix haute (réponses aussi à l'écran) |
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
| `/contexte` | Taille estimée de la conversation |
| `/memoire`, `/oublier ID` | Ce que Monika sait de toi ; supprime un souvenir |
| `/indexer [dossier]`, `/documents` | Lance l'indexation en arrière-plan ; état de l'index |
| `/effacer` | Oublie la conversation en cours (et son résumé) |
| `/quitter` (ou `/q`, Ctrl+D) | Quitte |

Ctrl+C interrompt la réponse en cours.

## Configuration

Tous les réglages sont dans `.env` (modèle commenté : `.env.example`, qui reprend exactement les
valeurs par défaut du code). Les plus utiles :

| Réglage | Rôle |
|---|---|
| `LLM_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` | Fournisseur et modèle (API compatible OpenAI) |
| `MONIKA_HOME` | Dossier des données (défaut `~/.config/monika`) |
| `USER_NAME` | Ton prénom, utilisé dans le prompt système |
| `MAX_STEPS` | Appels au modèle par tour (défaut 100) |
| `CONTEXT_BUDGET_TOKENS` | Seuil de résumé automatique (défaut 150 000) |
| `FAST_MEMORY_ENABLED`, `MEMORY_CAPTURE_ENABLED` | Mémoire rapide et capture automatique |
| `RAG_ENABLED`, `RAG_ROOTS`, `RAG_EXCLUDE` | Documents personnels |
| `STT_MODEL`, `TTS_ENABLED`, `TTS_ACCEPT_LICENSE` | Voix |
| `WEB_ENABLED`, `WEB_PORT` | Page locale (défaut 8765) |
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
- **Outils lents** (phrase d'attente après 1,5 s) : `"slow": true` pour tout un serveur,
  `"slow_tools": ["a", "b"]` pour certains outils, ou `"slow_tools": {"a": ["phrase 1",
  "phrase 2"]}` pour fournir des variantes de phrases.
- Les outils sont nommés `serveur__outil`. Les erreurs d'outil sont renvoyées au modèle en
  texte ; les images et sons renvoyés ne lui sont pas transmis (le texte l'indique).

**Outils internes** de Monika : `lire_resultat` (suite d'un gros résultat), `memoire_retenir`,
`memoire_chercher`, `memoire_lister`, `memoire_oublier`, `documents_chercher`,
`documents_indexer`, `documents_statut`, `taches_lister`, `taches_creer`, `taches_modifier`,
`taches_supprimer`, `taches_resultats`.

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
- **Ctrl+C** annule l'outil en cours. Si des actions avaient déjà été faites, une note les
  rappelle au modèle pour qu'il ne les croie pas annulées.
- **Prompt** reconstruit à chaque tour : personnalité, méthode de travail, règle « données, pas
  ordres », date en français et contexte du système (OS, utilisateur, dossier de travail).
- **Gros résultats** : au-delà de `TOOL_RESULT_MAX_CHARS` (20 000), le résultat est tronqué
  (début + fin) et le texte complet est sauvegardé dans `~/.config/monika/tool_results`
  (fichiers privés, purgés après `TOOL_RESULTS_KEEP_DAYS` = 14 jours). Le modèle lit la suite
  avec `lire_resultat(id, debut, longueur)`.
- **Budget de contexte** : la taille est estimée avant chaque appel (environ 3,3 caractères par
  token : une estimation, pas un comptage exact). Au-dessus de `CONTEXT_BUDGET_TOKENS`
  (150 000, très en dessous de la fenêtre de 1 M), les anciens échanges sont résumés par le
  modèle ; on garde au moins `CONTEXT_KEEP_RECENT_TOKENS` récents, avec une coupure toujours
  entre deux tours, jamais au milieu d'un appel d'outil. Si un seul tour est énorme, ses plus
  anciens résultats d'outils sont retirés (les 3 derniers restent). Si le résumé échoue, tu en
  es informé et la conversation continue sans compactage.

## Mémoire rapide

mem0 + Qdrant en mode local (`~/.config/monika/memory/qdrant`), embeddings calculés **sur le
CPU** avec fastembed (aucune VRAM utilisée).

- **Premier lancement** : téléchargement du modèle d'embeddings (~1 Go par défaut, ~0,2 Go pour
  `paraphrase-multilingual-MiniLM-L12-v2`). Hors ligne, le démarrage peut attendre plusieurs
  dizaines de secondes avant d'abandonner la mémoire avec un message clair ; Monika fonctionne
  sans.
- **Injection automatique** : à chaque message, les faits sont ajoutés au prompt système (jamais
  à l'historique) dans un bloc `<memoire>` présenté comme des notes, pas des ordres. Jusqu'à
  `MEMORY_INJECT_ALL_MAX` (30) faits, tous sont injectés ; au-delà, seuls les plus pertinents
  (`MEMORY_RECALL_LIMIT`, au-dessus de `MEMORY_RECALL_THRESHOLD`).
- **Capture automatique** en arrière-plan après chaque échange (message d'au moins
  `MEMORY_CAPTURE_MIN_CHARS` caractères) : le LLM extrait les faits durables que **tu** as
  énoncés. Les résultats d'outils ne sont jamais envoyés à l'extraction. Cela coûte un appel LLM
  par échange : `MEMORY_LLM_MODEL` permet un modèle moins cher, `MEMORY_CAPTURE_ENABLED=false`
  coupe la capture. À la sortie, Monika attend la fin des captures en cours.
- **Gestion** : `/memoire`, `/oublier ID`, outils `memoire_*`, ou le tableau de bord (modifier
  ou supprimer un souvenir).
- **Changer `EMBEDDING_MODEL`** change la taille des vecteurs : supprime alors
  `~/.config/monika/memory/qdrant`. Une seule instance de Monika à la fois peut utiliser ce
  dossier.

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
sudo pacman -S libpulse ffmpeg   # parec/pacat (audio) et FFmpeg (requis par torchcodec)
pip install torch torchaudio     # version CUDA par défaut sous Linux ; AVANT la ligne suivante
pip install -r requirements.txt  # ajoute coqui-tts[codec] (et torchcodec si PyTorch >= 2.9)
```

1. Dépose ton enregistrement dans `~/.config/monika/voice/` (`.wav` conseillé ; `.flac .mp3 .ogg
   .m4a` acceptés ; 6 à 30 secondes de voix claire, sans musique ni bruit ; plusieurs fichiers
   sont combinés). Les sous-dossiers (`models/`, `cache/`) sont ignorés. Un `.wav` de moins de
   3 s est refusé.
2. Accepte la licence : XTTS v2 est publié sous la **CPML de Coqui** (usage non commercial,
   https://coqui.ai/cpml). Monika ne télécharge le modèle (~1,9 Go, dans
   `~/.config/monika/voice/models/xtts`) que si `.env` contient `TTS_ACCEPT_LICENSE=true`.
3. Lance `python -m monika --mode voix` : au premier lancement, XTTS se charge et Monika annonce
   l'appareil utilisé (GPU ou CPU) et le temps de chargement.

### Écoute (`--mode voix`)

- **Micro** : `parec` lit la source par défaut de PipeWire/PulseAudio, déjà en 16 kHz mono. Une
  autre source : `pactl list short sources`, puis `AUDIO_INPUT_DEVICE=nom_de_la_source`. Un micro
  absent ou refusé est signalé tout de suite, avant tout téléchargement de modèle.
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

XTTS v2 clone ta voix et parle **phrase par phrase pendant que le modèle écrit encore** ; le son
sort par `pacat`.

- **Modes** : Monika parle en mode voix et en mode texte (`TTS_TEXT_MODE=false` la rend muette
  en mode texte). L'écran garde le texte tel quel ; la voix reçoit une version nettoyée : plus de
  Markdown, de listes, de titres ni d'emojis ; les liens gardent leur texte, les URL deviennent
  « un lien », les chemins gardent leur nom de fichier ; `21°C`, `20 %`, `14:30` se disent « 21
  degrés », « 20 pour cent », « 14 h 30 » ; un bloc de code n'est jamais lu (Monika dit une fois
  qu'elle le laisse à l'écran).
- **Coupures** : Ctrl+C pendant qu'elle parle coupe la voix, la session continue ; en mode
  texte, envoyer un nouveau message coupe aussi ce qui se disait.
- **Mémoire GPU (4 Go)** : avec `TTS_DEVICE=auto`, si la mémoire GPU manque au chargement, XTTS
  passe sur le CPU (plus lent) et Monika le dit. Si la voix hache : `TTS_STREAM_CHUNK=40`
  (lecture plus lisse, premier son un peu plus tardif).
- **Panne de voix** (modèle absent, GPU saturé, sortie audio perdue) : le message est affiché en
  français, les réponses restent à l'écran ; après trois échecs de suite Monika cesse de parler
  jusqu'au prochain lancement.
- **Cache** : l'analyse de ta voix est gardée dans `~/.config/monika/voice/cache` et refaite
  automatiquement si tu changes d'enregistrement.
- **Sortie audio** : `AUDIO_OUTPUT_DEVICE` (liste : `pactl list short sinks`), vide = défaut.

## Avatar et tableau de bord

Au lancement, Monika ouvre une page à `http://localhost:8765` (`WEB_PORT` ; `WEB_ENABLED=false`
pour la couper). À gauche l'avatar 3D, à droite le tableau de bord.

- **Avatar** : animations `Idle` (repos) et `Thinking` (réflexion), clignement des yeux
  aléatoire. Un voyant indique « Au repos », « J'écoute », « Je réfléchis » ou « Je parle »
  (d'après le son qui sort réellement des haut-parleurs).
- **Tableau de bord**, volontairement réduit à deux blocs : *Contexte* (taille de la
  conversation) et *Mémoire* (modifier ou supprimer chaque souvenir ; la modification recalcule
  son indexation).
- **Sécurité** : le serveur n'écoute que sur `127.0.0.1` ; les requêtes dont l'en-tête `Host` ou
  `Origin` n'est pas ton ordinateur sont refusées ; Three.js est embarqué
  (`monika/web/static/vendor/three`), la page ne charge rien depuis Internet (CSP stricte) ; tout
  ce qui vient de la mémoire est affiché comme du texte, jamais comme du HTML. Les seules
  actions possibles depuis la page (modifier ou supprimer un souvenir) exigent un jeton tiré au hasard à chaque lancement, fourni uniquement à la page servie,
  et une origine valide. Aucune commande ne peut être envoyée à l'agent depuis la page.
- **Limites du modèle `static/model.glb`** : pas de visèmes ni de paupières, et l'os de sa
  mâchoire (`CC_Base_JawRoot`) n'a aucun poids sur le maillage, donc la bouche ne bouge pas ; le
  clignement aplatit les yeux au lieu de baisser une paupière. WebGL est nécessaire dans le
  navigateur ; sans lui, la page l'indique et le tableau de bord marche quand même.
- **Licence de Three.js** : MIT, texte dans `monika/web/static/vendor/three/LICENSE`.

## Données, vie privée et sécurité

Tout est stocké dans `~/.config/monika` (réglable via `MONIKA_HOME`, dossier en `0700`) :

| Dossier | Contenu |
|---|---|
| `memory/` | Mémoire rapide (Qdrant local) |
| `rag/` | Index de documents (Qdrant local) et manifeste SQLite |
| `voice/` | Ta voix de référence, `models/`, `cache/` |
| `tool_results/` | Gros résultats d'outils (purgés après 14 jours) |
| `logs/` | `audit.jsonl` et journaux des serveurs MCP |
| `sessions/` | Réservé (vide pour l'instant) |

- **Journal d'audit** : `logs/audit.jsonl`, un JSON par ligne (outil, arguments, statut, durée),
  secrets masqués, fichier en `0600`.
- **Ce qui sort de ta machine** : tes messages et les résultats d'outils vont au fournisseur
  LLM que tu configures ; les téléchargements de modèles ont lieu au premier usage. La
  télémétrie de mem0 est désactivée.
- **Actions sans confirmation** : Monika utilise ses outils sans demander l'accord. Choisis
  donc avec soin les serveurs MCP que tu déclares (surtout ceux qui écrivent ou qui lancent des
  commandes) et relis le journal d'audit de temps en temps.

## Dépannage

| Symptôme | Piste |
|---|---|
| `Le port 8765 est déjà utilisé` | Une autre instance tourne : ferme-la ou change `WEB_PORT` |
| Micro non trouvé | `pactl info` doit répondre ; `pactl list short sources` puis `AUDIO_INPUT_DEVICE` |
| Pas de voix | Vérifie `TTS_ACCEPT_LICENSE=true`, ton enregistrement dans `voice/`, et le message affiché au lancement |
| Voix qui hache | `TTS_STREAM_CHUNK=40`, ou `TTS_DEVICE=cpu` si le GPU est saturé |
| Un serveur MCP n'apparaît pas | `python -m monika --tools` ; variable `${…}` absente ; voir `logs/mcp-<nom>.log` |
| Index qui refuse de s'ouvrir | Modèle ou taille de passages changés : `python -m monika --rag-index --reset` |
| Mémoire en erreur après changement de `EMBEDDING_MODEL` | Supprime `~/.config/monika/memory/qdrant` |
| Avatar absent | WebGL est requis dans le navigateur ; le tableau de bord marche sans |