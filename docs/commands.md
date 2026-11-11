# Commands

## Terminal

| Command | Effect |
|---|---|
| `python -m monika` | Starts Monika locally: the conversation happens in the page (the process lives as long as the page is open) |
| `./lancer-monika.sh` | Starts the GPT-SoVITS voice server if needed, checks that it answers, then starts Monika (same options as below) |
| `python -m monika --mode voix` | Conversation out loud (answers also shown on screen) |
| `./scripts/install.sh` | Creates the venv, installs dependencies, `.env`, `config/settings.json`; then the wizard |
| `./scripts/install.sh --demo` | Shows the installer steps and runs the wizard in demo mode; installs nothing |
| `python -m monika --setup` | First-run wizard: language, provider, key, name, voice |
| `python -m monika --setup --demo` | Same wizard in a temporary folder: prints the `.env` and `settings.json` it would write (key masked), changes nothing |
| `python -m monika --update` | Compares the checkout with the git remote; prints the update commands (never pulls) |
| `python -m monika --config` | Shows the loaded configuration (secrets masked) |
| `python -m monika --roles` | The model roles and the model each one uses |
| `python -m monika --telephone` | The `tailscale serve` command and the HTTPS link to open from your phone |
| `python -m monika --tools` | Lists the MCP servers and their tools |
| `python -m monika --call NAME '{"arg": 1}'` | Calls a tool by hand |
| `python -m monika --rag-index [FOLDER ...]` | Indexes your documents (`--reset`: starts from an empty index) |
| `python -m monika --rag-search WORD ...` | Searches the index, with citations |
| `python -m monika --rag-status` | Status of the document index |

## In the chat

`/help` lists them; the old French names are still accepted.

| Command | Effect |
|---|---|
| `/tools` (`/outils`) | List of available tools |
| `/context` (`/contexte`) | Estimated conversation size, split between prompt, tools (by source, with the heaviest) and history |
| `/memory` (`/memoire`), `/forget ID` (`/oublier ID`) | What Monika knows about you; deletes a memory |
| `/relationship` (`/relation`) | Where your relationship stands (level, favour, mood); `/relationship reset` (`reinitialiser`) starts over |
| `/journal` | The notes Monika has written about you; `/journal forget ID` deletes one, `/journal clear` deletes them all |
| `/nightly` (`/journal-nuit`) | The nightly note in Obsidian; `/nightly show DATE`, `delete DATE`, `accept ID`, `refuse ID` |
| `/sessions` | Saved sessions, most recent first |
| `/resume ID` (`/reprendre ID`) | Resumes a session: its history and summary are reloaded |
| `/search WORD …` (`/chercher`) | Searches recorded turns (full text) |
| `/session forget ID` (`oublier`) | Deletes a session, its turns and its tool calls |
| `/index [folder]` (`/indexer`), `/documents` | Starts indexing in the background; index status |
| `/remind WHEN : TEXT` (`/rappel`) | Schedules a reminder ("/remind in 20 minutes: drink water") |
| `/tasks` (`/taches`), `/task cancel ID` (`/tache annuler ID`) | Scheduled tasks; deletes one |
| `/briefing` | The morning briefing right now, without waiting for the scheduled time |
| `/custom-tools` (`/outils-perso`) | The MCP servers Monika has written: list, `code NAME` (read the code and test), `disable NAME`, `delete NAME` |
| `/recipes` (`/recettes`) | My learned procedures: list; `keep ID`, `refuse ID`, `forget ID` |
| `/stop` | Stops everything running (reply, voice, indexing) and says what |
| `/clear` (`/effacer`) | Forgets the current conversation (and its summary) |
| `/quit` (`/quitter`, `/q`, Ctrl+D) | Quits |

Ctrl+C interrupts the current reply **and** stops background work (voice,
indexing); memory captures already started run to completion.
