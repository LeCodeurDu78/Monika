# Données, vie privée et sécurité

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
| `instance.lock` | Le verrou d'instance unique : un seul Monika par dossier de données (`0600`) |
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

## Accès à la page

- **Sans `WEB_TOKEN`**, la page est ouverte à tout ce qui atteint le port — souvenirs, journal,
  sessions, réglages, envoi de messages et de commandes. La clé API n'est jamais relue
  (la page ne reçoit qu'un booléen), tout le reste l'est. Avec un jeton, chaque route, chaque
  fichier statique et le WebSocket le demandent ; il est accepté en en-tête `X-Monika-Token`, en
  `?token=` ou en cookie (HttpOnly, SameSite strict, un an). Il reste **pas de contrôle d'`Origin`
  ni d'en-tête de sécurité (CSP)** : une page ouverte dans ce navigateur peut encore agir à ta
  place, et le jeton passe une fois dans l'URL (historique, journaux du proxy) au premier
  chargement. Tout ce qui vient de la mémoire reste affiché comme du texte, jamais comme du HTML.
- **Accès distant** : voir [la page et le téléphone](page.md#depuis-le-téléphone-tailscale).
  Un autre appareil du réseau local demande `WEB_HOST=0.0.0.0` **et** `WEB_TOKEN` (sans jeton, la
  page est ouverte à qui atteint le port).

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
