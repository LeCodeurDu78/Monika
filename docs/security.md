# Data, privacy and security

Everything is stored in `~/.config/monika` (configurable via `MONIKA_HOME`, folder in `0700`):

| Folder | Contents |
|---|---|
| `memory/` | Events and quoted facts (`notes.json`, `0600`); old notebooks archived next to it |
| `rag/` | Document index (local Qdrant) and SQLite manifest |
| `character/` | `state.json` (emotions and relationship) and `journal.json` (Monika's notes about you), in `0600`; delete them to reset everything |
| `config/settings.json` | Settings changed from `/settings` (`.env` takes precedence), in `0600`; comment `MONIKA_SETTINGS_FILE` to keep it in `MONIKA_HOME` instead |
| `voice/` | Your reference voice, `models/`, `cache/` |
| `tool_results/` | Large tool results (purged after 14 days) |
| `logs/` | `audit.jsonl` and MCP server logs |
| `sessions/` | Conversation history (`sessions.db`, SQLite + FTS5, in `0600`), resumable and deletable |
| `instance.lock` | The single-instance lock: one Monika per data folder (`0600`) |
| `tasks.json` | Reminders and recurring tasks (JSON in `0600`, texts masked before writing) |
| `recipes.json` | Learned recipes: drafts, kept versions, history and usages (JSON in `0600`) |
| `mcp_custom/<name>/` | The MCP servers Monika wrote (`server.py`, `test_server.py`, in `0600`) |
| `custom_tools.json` | Their fingerprints and state (`active`, `disabled`, `refused`), in `0600` |

- **Audit log**: `logs/audit.jsonl`, one JSON per line (tool, arguments, status, duration),
  secrets masked, file in `0600`.
- **What leaves your machine**: your messages and tool results go to the LLM provider
  you configure (including, for tone reading, the journal and memory, short excerpts from your messages); model
  downloads happen on first use. Memory embeddings are computed **on your machine** (fastembed); only the
  model download, on first use, goes over the network.
- **Actions without confirmation**: Monika uses her tools without asking for approval. Choose
  the MCP servers you declare carefully (especially those that write or launch
  commands) and reread the audit log from time to time.

## Access to the page

- **Without `WEB_TOKEN`**, the page is open to anything that reaches the port — memories, journal,
  sessions, settings, sending messages and commands. The API key is never read back,
  everything else is. With a token, every route, every
  static file and the WebSocket require it; it is accepted in the `X-Monika-Token` header, in
  `?token=` or in a cookie (HttpOnly, SameSite strict, one year). Writes (`POST`, `PUT`,
  `DELETE`) and the WebSocket now require a **same-origin** `Origin` (a missing `Origin`, as
  with `curl`, is accepted); a cross-site page gets `403`. Every response carries a
  **Content-Security-Policy** (same-origin scripts, one nonced inline import map, no framing)
  plus `X-Content-Type-Options`, `Referrer-Policy` and `X-Frame-Options`. The token still
  passes once in the URL on first load (history, proxy logs), and an XSS inside the page itself
  would still run with its privileges: keep dependencies and custom MCP servers trustworthy.
  Everything coming from memory is displayed as text, never as HTML.
- **Remote access**: see [the page and the phone](dashboard.md#from-your-phone-tailscale).
  Another device on the local network requires `WEB_HOST=0.0.0.0` **and** `WEB_TOKEN` (without a
  token, the page is open to whoever reaches the port).

