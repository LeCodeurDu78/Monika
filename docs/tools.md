# Tools (MCP servers)

External tools come only from MCP servers, declared in
`~/.config/monika/mcp_servers.json` (template: `mcp_servers.example.json`, same format as
Claude Desktop; other location: `MCP_SERVERS_FILE`), plus the two built-in helpers Hermes
and nanoMuse (see below). Two transports: local command
(stdio) or `url` (streamable HTTP); a server cannot have both.

- **Variables**: `${VARIABLE}` in the `command`, `args`, `env`, `cwd`, `url` and
  `headers` fields are replaced from the environment or, failing that, from the project's `.env`
  (useful for API keys: `BRAVE_API_KEY=...` in `.env`, `"${BRAVE_API_KEY}"` in the server). A
  missing variable disables that server with a clear message.
- **Robustness**: a failed server does not prevent the others from starting. `"enabled": false`
  disables a server; `"start_timeout_s": 90` gives more time to a slow start (for
  example `npx` the first time).
- **Hot reload**: `mcp_servers.json` is watched while Monika runs. An edit closes the sessions,
  re-reads the file and reconnects within a couple of seconds — no restart, no lost conversation.
  The tools of a removed server disappear from the model at the same time.
- **Limiting tools**: the schema of every tool is sent to the model on **every** request
  (a large server can weigh tens of thousands of tokens). `"enabled_tools": ["a", "b"]`
  keeps only those tools from a server; `"disabled_tools": ["c"]` removes some (and
  takes precedence over `enabled_tools`). The names are the server's, without the `server__`
  prefix. `/context` shows what each source weighs and the heaviest tools; `--tools` and
  startup report how many tools are excluded and flag unknown names.
- **Slow tools** (waiting phrase after 1.5 s): `"slow": true` for a whole server,
  `"slow_tools": ["a", "b"]` for specific tools, or `"slow_tools": {"a": ["phrase 1",
  "phrase 2"]}` to provide phrase variants.
- Tools are named `server__tool`. Tool errors are returned to the model as
  text; images and sounds returned are not passed to it (the text says so).

**Bridge to a helper**: `bridges/helper_bridge.py` exposes `ask`, `status`, `answer`, `stop`
and `health` to a separate program. **Hermes and nanoMuse are built in**: set `HERMES_URL`
(or `NANOMUSE_URL`), plus the token, in `.env`; nothing to declare in `mcp_servers.json`.
Monika chooses on her own when to launch a run with `ask`, without waiting for an explicit
request; `stop` and approvals still need your agreement.
The `hermes` preset talks to Hermes's API server (`/v1/runs`), `nanomuse` to nanoMuse's
local API (`/api/threads`); a `generic` helper keeps a simple HTTP contract and is still
declared like any other MCP server. Configuration and security in
[`bridges/README.md`](../bridges/README.md).

**Monika's built-in tools**: `lire_resultat` (follow-up on a large result), `memoire_retenir`,
`memoire_chercher`, `memoire_lister`, `memoire_oublier`, `documents_chercher`,
`documents_indexer`, `documents_statut` and `create_custom_tools` (see below).

## Custom tools: `create_custom_tools`

When an action is missing from her tools, Monika can write **her own MCP server**. The
`create_custom_tools` tool takes `name`, `description`, `code` (`server.py`) and `test`
(`test_server.py`); the sequence is fixed and no step can be skipped:

1. **Generation** in `~/.config/monika/mcp_custom/<name>/` (`server.py`, `test_server.py`, in
   `0600` inside a `0700` folder).
2. **Static verification** without execution: syntax, forbidden escape imports
   (`subprocess`, `ctypes`, `cffi`, `importlib`, `pickle`, `multiprocessing`, `pty`, `marshal`,
   `shelve`, `shutil`), `eval`/`exec`/`compile`/`__import__`, `os.system`, `os.popen`,
   `os.exec*`, `os.fork` calls. **The network is allowed** (`requests`, `httpx`, `urllib`, `socket`,
   `aiohttp`…): a tool that cannot reach any API would be useless. The server must
   expose its tools with the `mcp` package. A refusal leaves the code on disk for review,
   but nothing is registered. The server is written with the installed API:
   `from mcp.server.mcpserver import MCPServer` (mcp 2.x).
3. **Test run in a sandbox**: `bwrap` (bubblewrap) when available, with
   `--unshare-all` then `--share-net` (network open, `resolv.conf` and certificate authorities
   mounted read-only), `/usr` read-only, `/tmp` on a tmpfs, and **only
   `mcp_custom/<name>/` writable**; neither your `$HOME` nor the rest of the disk is mounted
   (60 s timeout). Failing that, the same test runs in a Docker container (`python:3.12-slim`,
   only the tool's folder mounted, network open; the first launch pulls the image, 240 s timeout).
   Without `bwrap` or `docker`, the tool refuses to test and therefore to create anything.
   Red test = refusal.
4. **Automatic registration**: if verification and tests pass, the server is
   written to `mcp_servers.json` (`python server.py` command, `cwd` of the folder,
   `enabled: true`) without asking for any approval. The existing configuration is
   preserved; a server of the same name that came from you is never overwritten. Monika then shows the
   code, the requested permissions and the test results in her reply, so you know
   what was added.

**Fingerprint**: it is recorded in `custom_tools.json`. If the code changes afterwards, the
server is **disabled** at the next startup and on `--tools`: it must be
regenerated, verified and tested again before being re-registered. A server in `mcp_custom/`
absent from the index is removed from the configuration.

**Commands**: `/custom-tools` (list, status, fingerprint), `/custom-tools code NAME` (the real
code and test on disk), `disable NAME`, `delete NAME`.

**From the web page**: nothing extra to enable. The page and the terminal go through the same agent
and the same tool registry, so `create_custom_tools` works there identically; the
`/custom-tools …` commands are typed in the *Conversation* block (their answer appears there), and the
**Custom tools** panel of the dashboard shows the list and status, refreshed with the rest of the page.

**Limits, worth knowing**:

- The sandbox protects the **test's** execution: during the test, the server sees neither your
  `$HOME` nor the rest of the disk and writes only in its folder (it keeps `/usr` and the venv
  read-only, that is where Python and `mcp` live). The network, however, is open.
- The **registered** server then runs like an ordinary MCP server, with your rights: it
  sees your disk and can reach the internet. Read the code of a tool that writes files.
- **Nothing asks for your approval**, neither to create nor to run: that is the price of the direct flow.
  Read the code whenever you want (`/custom-tools code NAME`), and remove a tool with
  `disable NAME` or `delete NAME`.
- Static verification is a list of refusals, not a proof: exotic dynamic constructs can
  get around it. The sandbox and your review are what provide security.
- A newly registered server is available only after **restarting** Monika; the tools
  of servers already running remain those from startup.
- **Network**: nothing to configure, it is allowed for the test as for the registered
  tool; `requests`, `httpx` or `urllib` can reach real APIs.
