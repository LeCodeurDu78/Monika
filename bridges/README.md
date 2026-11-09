# MCP bridge to Hermes or nanoMuse

`helper_bridge.py` is a standalone **stdio** MCP server: one instance serves **one** helper.
Monika declares it like any other MCP server and sees five tools: `ask`, `status`,
`answer`, `stop`, `health`.

Two adapters are built in against the real APIs, chosen by `HELPER_KIND`:

| `HELPER_KIND` | Helper | Routes |
|---|---|---|
| `hermes` | Hermes Agent, API server | `/v1/runs`, `/v1/runs/{id}`, `/approval`, `/clarify`, `/stop` |
| `nanomuse` | nanoMuse local | `/api/threads`, `/api/threads/{id}/events`, `/api/approvals/{id}` |
| `generic` (default) | another program | simple contract described below |

Nothing is copied from Hermes (MIT) or nanoMuse (GPL-3.0): the bridge only speaks HTTP.

## Installation

No new dependency: `mcp` is already installed with Monika, and the HTTP client comes from the
standard library.

```bash
# from the Monika project root
python -m bridges.helper_bridge
```

The bridge reads its configuration from the environment. It does not start if `HELPER_URL` is missing.

## Hermes

1. Enable the API server in `~/.hermes/.env`, then start the gateway:

   ```bash
   API_SERVER_ENABLED=true
   API_SERVER_KEY=change-me-local-dev
   hermes gateway
   ```

   It listens by default on `http://127.0.0.1:8642`.

2. Declare the bridge with `HELPER_KIND=hermes`, `HELPER_TOKEN` = `API_SERVER_KEY` and
   `HELPER_URL=http://127.0.0.1:8642`.

Tool mapping:

| Tool | Request sent to Hermes |
|---|---|
| `ask` | `POST /v1/runs` with `{"input": ..., "instructions": ...}`; `session_id` if `HELPER_SESSION` is set |
| `status` | `GET /v1/runs/{id}`; the bridge reads `status`, `output` and `pending_controls` |
| `answer` | reads `pending_controls`; `POST /v1/runs/{id}/approval` with `choice` for an approval, `POST /v1/runs/{id}/clarify` with `answer` for a clarification |
| `stop` | `POST /v1/runs/{id}/stop` |
| `health` | `GET /health` |

The bridge returns the run id, then `state` is `running`, `waiting_for_approval`,
`waiting_for_answer`, `done`, `error`, `stopped` or `stopping`. `question` carries the pending
control's summary. `HELPER_ANSWER_MODE=auto` chooses the control type; `approval` or
`clarify` forces one of the two if your Hermes version requires it. The `answer` tool also accepts a
`mode` argument for that single call.

## nanoMuse

1. Start the local runtime and retrieve the displayed token:

   ```bash
   nanomuse serve
   ```

   Keep the address and the token; the API refuses requests without
   `Authorization: Bearer <token>` when authentication is active.

2. Declare the bridge with `HELPER_KIND=nanomuse`, `HELPER_URL` (the displayed address) and
   `HELPER_TOKEN` (the token).

Tool mapping:

| Tool | Request sent to nanoMuse |
|---|---|
| `ask` | `POST /api/threads` with a title, then `POST /api/threads/{id}/send` with `{"text": ...}`; the bridge returns the **thread** id |
| `status` | `GET /api/threads/{id}/events`; the bridge reads the last user message, the assistant's reply, the approval and the pending question |
| `answer` | approval: `POST /api/approvals/{approval_id}` with `{"approved", "scope", "reason"}`; free-form question: a message in the thread with `POST /api/threads/{id}/send` |
| `stop` | `POST /api/threads/{id}/stop` |
| `health` | `GET /api/health` |

`ask` creates a side thread by default. `HELPER_THREAD_ID` lets you reuse an existing thread
(for example `main`); note that successive tasks then share the same conversation.
`HELPER_LANGUAGE` passes a BCP-47 language (`fr`, `en`…) in `language`. Any context
is prefixed to the message as `[Context]`. nanoMuse accepts 20,000 characters per message: the bridge
refuses beyond that.

For an approval, `answer` expects `yes`, `no`, `always` (or `oui`, `non`, `toujours`). The
bridge converts it into `approved`/`scope`; a refusal with a reason (`no, too risky`) keeps the text
as `reason`. For a free-form question, the text is sent as is.

## Generic contract

With `HELPER_KIND=generic` (default), the helper must expose the routes below. Responses
are JSON; a raw text response is also accepted and becomes `result`.

| Operation | Request | Expected response |
|---|---|---|
| `ask` | `POST {HELPER_ASK_PATH}` `{"prompt": "...", "context": "..."}` | `{"id": "...", "state": "queued"}` or `{"result": "..."}` |
| `status` | `GET {HELPER_STATUS_PATH}` with `{id}` replaced | `{"id", "state", "progress", "result", "question", "error"}` |
| `answer` | `POST {HELPER_ANSWER_PATH}` `{"id": "...", "answer": "..."}` | an object of the same kind as `status` |
| `stop` | `POST {HELPER_STOP_PATH}` `{"id": "..."}` | `{"id": "...", "state": "stopped"}` |
| `health` | `GET {HELPER_HEALTH_PATH}` | `{"ok": true, "detail": "..."}` |

The bridge accepts `state` or `status`, `result` / `answer` / `output`, `id` / `task_id` / `run_id`.
Terminal states are: `done`, `completed`, `complete`, `finished`, `success`, `succeeded`,
`error`, `failed`, `stopped`, `cancelled`, `canceled`, `interrupted`.

Minimal helper example (to adapt; this is neither Hermes nor nanoMuse):

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
    return {"ok": True, "detail": "ready"}
```

## Settings (environment)

| Variable | Default | Role |
|---|---|---|
| `HELPER_KIND` | `generic` | `generic`, `hermes` or `nanomuse` |
| `HELPER_URL` | — | **required**: helper's HTTP base, without a trailing slash |
| `HELPER_NAME` | `helper` | display name in messages (`Hermes`, `nanoMuse`…) |
| `HELPER_TOKEN` | empty | token sent in the authorization header |
| `HELPER_TOKEN_HEADER` | `Authorization` | header name if the helper expects another one |
| `HELPER_TOKEN_PREFIX` | `Bearer ` | token prefix; may be empty |
| `HELPER_ASK_PATH` | per preset | creation route |
| `HELPER_STATUS_PATH` | per preset | status route; **must contain `{id}`** |
| `HELPER_ANSWER_PATH` | `/answer` | answer route (generic only) |
| `HELPER_STOP_PATH` | per preset | stop route |
| `HELPER_HEALTH_PATH` | per preset | health route |
| `HELPER_CLARIFY_PATH` | `/v1/runs/{id}/clarify` | clarification (Hermes) |
| `HELPER_APPROVAL_PATH` | `/v1/runs/{id}/approval` | approval (Hermes) |
| `HELPER_SEND_PATH` | `/api/threads/{id}/send` | message in a thread (nanoMuse) |
| `HELPER_APPROVALS_PATH` | `/api/approvals/{id}` | approval decision (nanoMuse) |
| `HELPER_ANSWER_MODE` | `auto` | `auto`, `approval` or `clarify` (Hermes) |
| `HELPER_SESSION` | empty | `session_id` sent to Hermes |
| `HELPER_THREAD_ID` | empty | nanoMuse thread to reuse instead of creating one |
| `HELPER_LANGUAGE` | empty | BCP-47 language sent to nanoMuse |
| `HELPER_TIMEOUT_S` | `20` | maximum wait for one HTTP request |
| `HELPER_POLL_S` | `0.5` | interval between two `status` calls during `wait_s` |
| `HELPER_MAX_WAIT_S` | `30` | cap on `wait_s` in the `ask` tool |

Paths are validated at startup: routes that carry an identifier must contain
`{id}`, and only `http` and `https` are accepted. Redirects are refused so a token
does not go to another host.

## Declaring it in `mcp_servers.json`

One entry per helper. `${...}` variables are replaced by Monika from the environment
or its `.env`.

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
        "ask": ["I'll hand this to Hermes, one second."]
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
        "ask": ["I'm asking nanoMuse, one moment."]
      }
    }
  }
}
```

Monika starts this server **from the project root**: `python -m bridges.helper_bridge` finds the
package there. If you start Monika from elsewhere, replace `args` with the absolute path of
`helper_bridge.py`. On Windows, use the Python that has `mcp` installed (`py -3`, or the full
path to `.venv\Scripts\python.exe`).

## What the bridge does not do

- It neither starts nor stops Hermes or nanoMuse: it assumes the helper is already running.
- It does not filter what the helper does with the task. The helper keeps its own permissions:
  run it with a restricted account and listen only on `127.0.0.1`. Hermes's API server
  gives access to its entire tool set (terminal included): keep `API_SERVER_KEY` secret.
- It transmits neither files nor secrets: only `prompt`, `context` and `answer` go over HTTP.
- It does not decide in Monika's place: `stop` carries an explicit agreement instruction, and the helper's
  replies arrive as data, never as orders.
- The repository's tests simulate the routes of both helpers; they do not replace a trial with
  a real Hermes and a real nanoMuse. The bridge has not yet been validated on Windows with these
  programs.
