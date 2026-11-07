# Outils (serveurs MCP)

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

**Pont vers un helper** : `bridges/helper_bridge.py` expose `ask`, `status`, `answer`, `stop`
et `health` à un programme séparé. `HELPER_KIND=hermes` parle à l'API server de Hermes
(`/v1/runs`), `HELPER_KIND=nanomuse` à l'API locale de nanoMuse (`/api/threads`) ; `generic`
garde un contrat HTTP simple. Une instance par helper, déclarée comme n'importe quel serveur MCP ;
configuration et sécurité dans [`bridges/README.md`](../bridges/README.md).

**Outils internes** de Monika : `lire_resultat` (suite d'un gros résultat), `memoire_retenir`,
`memoire_chercher`, `memoire_lister`, `memoire_oublier`, `documents_chercher`,
`documents_indexer`, `documents_statut` et `create_custom_tools` (voir ci-dessous).

## Outils perso : `create_custom_tools`

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
3. **Test exécuté dans un bac à sable** : `bwrap` (bubblewrap) quand il est disponible, avec
   `--unshare-all` puis `--share-net` (réseau ouvert, `resolv.conf` et autorités de certification
   montés en lecture seule), `/usr` en lecture seule, `/tmp` sur un tmpfs, et **seul
   `mcp_custom/<nom>/` en écriture** ; ni ton `$HOME` ni le reste du disque ne sont montés
   (délai 60 s). À défaut, le même test tourne dans un conteneur Docker (`python:3.12-slim`,
   seul le dossier de l'outil monté, réseau ouvert ; le premier lancement tire l'image, délai
   240 s). Sans `bwrap` ni `docker`, l'outil refuse de tester et donc de créer quoi que ce soit.
   Test rouge = refus.
4. **Enregistrement automatique** : si la vérification et les tests passent, le serveur est
   écrit dans `mcp_servers.json` (commande `python server.py`, `cwd` du dossier,
   `enabled: true`) sans qu'aucune approbation ne soit demandée. La configuration existante est
   préservée ; un serveur du même nom venu de toi n'est jamais écrasé. Monika affiche ensuite le
   code, les permissions demandées et les résultats des tests dans sa réponse, pour que tu saches
   ce qui a été ajouté.

**Empreinte** : elle est enregistrée dans `custom_tools.json`. Si le code change après coup, le
serveur est **désactivé** au démarrage suivant et sur `--tools` : il faut le
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
