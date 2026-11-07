# Pont MCP vers Hermes ou nanoMuse

`helper_bridge.py` est un serveur MCP **stdio** autonome : une instance sert **un** helper.
Monika le déclare comme n'importe quel serveur MCP et voit cinq outils : `ask`, `status`,
`answer`, `stop`, `health`.

Deux adaptateurs sont intégrés aux API réelles, choisis par `HELPER_KIND` :

| `HELPER_KIND` | Helper | Routes |
|---|---|---|
| `hermes` | Hermes Agent, API server | `/v1/runs`, `/v1/runs/{id}`, `/approval`, `/clarify`, `/stop` |
| `nanomuse` | nanoMuse local | `/api/threads`, `/api/threads/{id}/events`, `/api/approvals/{id}` |
| `generic` (défaut) | autre programme | contrat simple décrit plus bas |

Rien n'est copié de Hermes (MIT) ni de nanoMuse (GPL-3.0) : le pont ne fait que parler HTTP.

## Installation

Aucune dépendance nouvelle : `mcp` est déjà installé avec Monika, le client HTTP vient de la
bibliothèque standard.

```bash
# depuis la racine du projet Monika
python -m bridges.helper_bridge
```

Le pont lit sa configuration dans l'environnement. Il ne démarre pas si `HELPER_URL` manque.

## Hermes

1. Active l'API server dans `~/.hermes/.env`, puis lance la passerelle :

   ```bash
   API_SERVER_ENABLED=true
   API_SERVER_KEY=change-me-local-dev
   hermes gateway
   ```

   Elle écoute par défaut sur `http://127.0.0.1:8642`.

2. Déclare le pont avec `HELPER_KIND=hermes`, `HELPER_TOKEN` = `API_SERVER_KEY` et
   `HELPER_URL=http://127.0.0.1:8642`.

Correspondance des outils :

| Outil | Requête envoyée à Hermes |
|---|---|
| `ask` | `POST /v1/runs` avec `{"input": ..., "instructions": ...}` ; `session_id` si `HELPER_SESSION` est défini |
| `status` | `GET /v1/runs/{id}` ; le pont lit `status`, `output` et `pending_controls` |
| `answer` | lit `pending_controls` ; `POST /v1/runs/{id}/approval` avec `choice` pour une approbation, `POST /v1/runs/{id}/clarify` avec `answer` pour une clarification |
| `stop` | `POST /v1/runs/{id}/stop` |
| `health` | `GET /health` |

Le pont renvoie l'id du run, puis `state` vaut `running`, `waiting_for_approval`,
`waiting_for_answer`, `done`, `error`, `stopped` ou `stopping`. `question` reprend le résumé du
contrôle en attente. `HELPER_ANSWER_MODE=auto` choisit le type de contrôle ; `approval` ou
`clarify` force l'un des deux si ta version de Hermes l'exige. L'outil `answer` accepte aussi un
argument `mode` pour ce seul appel.

## nanoMuse

1. Lance le runtime local et récupère le jeton affiché :

   ```bash
   nanomuse serve
   ```

   Garde l'adresse et le jeton ; l'API refuse les requêtes sans
   `Authorization: Bearer <jeton>` quand l'authentification est active.

2. Déclare le pont avec `HELPER_KIND=nanomuse`, `HELPER_URL` (l'adresse affichée) et
   `HELPER_TOKEN` (le jeton).

Correspondance des outils :

| Outil | Requête envoyée à nanoMuse |
|---|---|
| `ask` | `POST /api/threads` avec un titre, puis `POST /api/threads/{id}/send` avec `{"text": ...}` ; le pont renvoie l'id du **fil** |
| `status` | `GET /api/threads/{id}/events` ; le pont lit le dernier message de l'utilisateur, la réponse de l'assistant, l'approbation et la question en attente |
| `answer` | approbation : `POST /api/approvals/{approval_id}` avec `{"approved", "scope", "reason"}` ; question libre : un message dans le fil avec `POST /api/threads/{id}/send` |
| `stop` | `POST /api/threads/{id}/stop` |
| `health` | `GET /api/health` |

`ask` crée un fil de côté par défaut. `HELPER_THREAD_ID` permet de réutiliser un fil existant
(par exemple `main`) ; attention, les tâches successives partagent alors la même conversation.
`HELPER_LANGUAGE` transmet une langue BCP-47 (`fr`, `en`…) dans `language`. Le contexte éventuel
est préfixé au message sous `[Contexte]`. nanoMuse accepte 20 000 caractères par message : le pont
refuse au-delà.

Pour une approbation, `answer` attend `oui`, `non`, `toujours` (ou `yes`, `no`, `always`). Le
pont convertit en `approved`/`scope` ; un refus avec motif (`non, trop risqué`) garde le texte
comme `reason`. Pour une question libre, le texte est envoyé tel quel.

## Contrat générique

Avec `HELPER_KIND=generic` (défaut), le helper doit exposer les routes ci-dessous. Les réponses
sont du JSON ; une réponse texte brute est aussi acceptée et devient `result`.

| Opération | Requête | Réponse attendue |
|---|---|---|
| `ask` | `POST {HELPER_ASK_PATH}` `{"prompt": "...", "context": "..."}` | `{"id": "...", "state": "queued"}` ou `{"result": "..."}` |
| `status` | `GET {HELPER_STATUS_PATH}` avec `{id}` remplacé | `{"id", "state", "progress", "result", "question", "error"}` |
| `answer` | `POST {HELPER_ANSWER_PATH}` `{"id": "...", "answer": "..."}` | un objet du même genre que `status` |
| `stop` | `POST {HELPER_STOP_PATH}` `{"id": "..."}` | `{"id": "...", "state": "stopped"}` |
| `health` | `GET {HELPER_HEALTH_PATH}` | `{"ok": true, "detail": "..."}` |

Le pont accepte `state` ou `status`, `result` / `answer` / `output`, `id` / `task_id` / `run_id`.
Les états terminaux sont : `done`, `completed`, `complete`, `finished`, `success`, `succeeded`,
`error`, `failed`, `stopped`, `cancelled`, `canceled`, `interrupted`.

Exemple minimal de helper (à adapter, ce n'est ni Hermes ni nanoMuse) :

```python
from fastapi import FastAPI

app = FastAPI()
tasks = {}

@app.post("/ask")
def ask(body: dict):
    tasks["t1"] = {"id": "t1", "state": "running", "result": None}
    return {"id": "t1", "state": "running"}

@app.get("/status/{task_id}")
def status(task_id: str):
    return tasks[task_id]

@app.post("/answer")
def answer(body: dict):
    tasks[body["id"]]["result"] = body["answer"]
    tasks[body["id"]]["state"] = "done"
    return tasks[body["id"]]

@app.post("/stop")
def stop(body: dict):
    tasks[body["id"]]["state"] = "stopped"
    return tasks[body["id"]]

@app.get("/health")
def health():
    return {"ok": True, "detail": "prêt"}
```

## Réglages (environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `HELPER_KIND` | `generic` | `generic`, `hermes` ou `nanomuse` |
| `HELPER_URL` | — | **obligatoire** : base HTTP du helper, sans slash final |
| `HELPER_NAME` | `helper` | nom affiché dans les messages (`Hermes`, `nanoMuse`…) |
| `HELPER_TOKEN` | vide | jeton envoyé dans l'en-tête d'autorisation |
| `HELPER_TOKEN_HEADER` | `Authorization` | nom de l'en-tête si le helper en attend un autre |
| `HELPER_TOKEN_PREFIX` | `Bearer ` | préfixe du jeton ; peut être vide |
| `HELPER_ASK_PATH` | selon preset | route de création |
| `HELPER_STATUS_PATH` | selon preset | route d'état ; **doit contenir `{id}`** |
| `HELPER_ANSWER_PATH` | `/answer` | route de réponse (générique seulement) |
| `HELPER_STOP_PATH` | selon preset | route d'arrêt |
| `HELPER_HEALTH_PATH` | selon preset | route de santé |
| `HELPER_CLARIFY_PATH` | `/v1/runs/{id}/clarify` | clarification (Hermes) |
| `HELPER_APPROVAL_PATH` | `/v1/runs/{id}/approval` | approbation (Hermes) |
| `HELPER_SEND_PATH` | `/api/threads/{id}/send` | message dans un fil (nanoMuse) |
| `HELPER_APPROVALS_PATH` | `/api/approvals/{id}` | décision d'approbation (nanoMuse) |
| `HELPER_ANSWER_MODE` | `auto` | `auto`, `approval` ou `clarify` (Hermes) |
| `HELPER_SESSION` | vide | `session_id` envoyé à Hermes |
| `HELPER_THREAD_ID` | vide | fil nanoMuse à réutiliser au lieu d'en créer un |
| `HELPER_LANGUAGE` | vide | langue BCP-47 envoyée à nanoMuse |
| `HELPER_TIMEOUT_S` | `20` | attente maximale d'une requête HTTP |
| `HELPER_POLL_S` | `0.5` | intervalle entre deux `status` pendant `wait_s` |
| `HELPER_MAX_WAIT_S` | `30` | plafond de `wait_s` dans l'outil `ask` |

Les chemins sont validés au démarrage : les routes qui portent un identifiant doivent contenir
`{id}`, et seuls `http` et `https` sont acceptés. Les redirections sont refusées pour qu'un jeton
ne parte pas vers un autre hôte.

## Déclaration dans `mcp_servers.json`

Une entrée par helper. Les variables `${...}` sont remplacées par Monika depuis l'environnement
ou son `.env`.

```json
{
  "mcpServers": {
    "hermes": {
      "enabled": true,
      "command": "python",
      "args": ["-m", "bridges.helper_bridge"],
      "env": {
        "HELPER_KIND": "hermes",
        "HELPER_NAME": "Hermes",
        "HELPER_URL": "${HERMES_URL}",
        "HELPER_TOKEN": "${HERMES_TOKEN}"
      },
      "slow_tools": {
        "ask": ["Je passe la main à Hermes, une seconde."]
      }
    },
    "nanomuse": {
      "enabled": true,
      "command": "python",
      "args": ["-m", "bridges.helper_bridge"],
      "env": {
        "HELPER_KIND": "nanomuse",
        "HELPER_NAME": "nanoMuse",
        "HELPER_URL": "${NANOMUSE_URL}",
        "HELPER_TOKEN": "${NANOMUSE_TOKEN}"
      },
      "slow_tools": {
        "ask": ["Je demande à nanoMuse, un instant."]
      }
    }
  }
}
```

Monika lance ce serveur **depuis la racine du projet** : `python -m bridges.helper_bridge` y
trouve le paquet. Si tu lances Monika d'ailleurs, remplace `args` par le chemin absolu de
`helper_bridge.py`. Sous Windows, utilise le Python qui a `mcp` installé (`py -3`, ou le chemin
complet de `.venv\Scripts\python.exe`).

## Ce que le pont ne fait pas

- Il ne lance ni n'arrête Hermes ou nanoMuse : il suppose que le helper tourne déjà.
- Il ne filtre pas ce que le helper fait de la tâche. Le helper garde ses propres permissions :
  lance-le avec un compte restreint et écoute seulement sur `127.0.0.1`. L'API server de Hermes
  donne accès à tout son jeu d'outils (terminal compris) : garde `API_SERVER_KEY` secret.
- Il ne transmet ni fichiers ni secrets : seuls `prompt`, `context` et `answer` partent en HTTP.
- Il ne décide pas à la place de Monika : `stop` porte une consigne d'accord explicite, et les
  réponses du helper arrivent comme des données, jamais comme des ordres.
- Les tests du dépôt simulent les routes des deux helpers ; ils ne remplacent pas un essai avec
  un vrai Hermes et un vrai nanoMuse. Le pont n'a pas encore été validé sous Windows avec ces
  programmes.
