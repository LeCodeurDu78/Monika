# Data, privacy and security

Everything is stored in `~/.config/monika` (configurable via `MONIKA_HOME`, folder in `0700`):

| Folder | Contents |
|---|---|
| `memory/` | Events and quoted facts (`notes.json`, `0600`); old notebooks archived next to it |
| `rag/` | Document index (local Qdrant) and SQLite manifest |
| `character/` | `state.json` (emotions and relationship) and `journal.json` (Monika's notes about you), in `0600`; delete them to reset everything |
| `settings.json` | Settings changed from `/settings` (`.env` takes precedence), in `0600` |
| `words.json` | Your sensitive-word list (empty at first, three levels), in `0600` |
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
  sessions, settings, sending messages and commands. The API key is never read back
  (the page only receives a boolean), everything else is. With a token, every route, every
  static file and the WebSocket require it; it is accepted in the `X-Monika-Token` header, in
  `?token=` or in a cookie (HttpOnly, SameSite strict, one year). There is still **no `Origin`
  check and no security header (CSP)**: a page open in this browser can still act on your
  behalf, and the token passes once in the URL (history, proxy logs) on first
  load. Everything coming from memory is still displayed as text, never as HTML.
- **Remote access**: see [the page and the phone](page.md#from-your-phone-tailscale).
  Another device on the local network requires `WEB_HOST=0.0.0.0` **and** `WEB_TOKEN` (without a
  token, the page is open to whoever reaches the port).

## Sensitive words

A deterministic filter (no model call) spots a word from **your** list in your message and colours
the reply's tone, without ever blocking or filtering it. The repository ships **no** word: the list
is yours, and it decides what counts.

- **Three levels.** `mild`: nothing mechanical (at most a pout). `bad`: a calm-tone block in the
  prompt and a pout. `severe`: a tone block that asks her not to engage on that ground (without
  reproach or retaliation), crossed arms, and a line in the audit log. The most
  serious level wins when several words cross.
- **The list**: `~/.config/monika/words.json`, created empty on first launch (with a note), then
  editable from the **Settings** page (one word per line, 64 characters and 500 words per level at
  most). It is reloaded **live**: saving rebuilds the next turn's filter without
  restarting. `SENSITIVE_WORDS_ENABLED=false` turns the filter off entirely.
- **Recognition**: accents and capitals, apostrophes and elisions (`l'idiot`, `t'es con`), leetspeak
  (`cl@nker`, `cl4nker`, `1d10t`), glue characters (`- _ + .`), zero-width
  characters, simple plurals (`idiots`). Whole words only: `rigger` does not match `trigger`.
- **What is injected**: a block written in the code, never the detected word (no prompt
  reinjection), and only for that turn. The audit log contains only the level, never the word or the
  sentence.
- **What is not done**: the model's reply is not censored and the turn is never blocked;
  French false positives (`merde` sometimes affectionate) remain possible, hence the `mild` level with no
  mechanical effect and the switch.
