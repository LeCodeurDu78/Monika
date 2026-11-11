# Monika

A personal AI assistant that runs locally and speaks French.
Inspired by the character Monika (DDLC). Strictly personal use.

## Overview

Monika talks to you from a page in your browser or by voice (with **your** cloned voice), acts through MCP tools,
remembers what matters to you, finds your documents, and appears as a 3D avatar with a small dashboard in the
browser. Everything is stored on your machine; only the LLM provider you configure receives your messages.

- **Conversation** in the page, with commands (`/help`, `/sessions`, `/remind`…) and a 🎙 button
  to talk into the browser microphone.
- **Voice**: listening with Whisper and speech with GPT-SoVITS, started separately.
- **Tools**: MCP servers declared in a file, plus built-in tools (memory, documents)
  and tools Monika can write for herself.
- **Memory**: durable facts, each backed by your exact sentence, with corrections and hybrid
  recall, plus an index of your documents (RAG) with citations.
- **Autonomous**: reminders and recurring tasks, a morning briefing, learned recipes.
- **Character**: emotions, relationship and journal, visible on the avatar and in the dashboard.

## Documentation

| Topic | Document                                     |
|---|----------------------------------------------|
| Voice: listening, speaking, GPT-SoVITS, page microphone | [docs/voice.md](docs/voice.md) |
| Personal memory and documents (RAG), OCR | [docs/memory.md](docs/memory.md) |
| MCP tools, built-in tools and custom tools | [docs/tools.md](docs/tools.md) |
| Agent, context, recipes, sessions, tasks, briefing | [docs/agent.md](docs/agent.md) |
| Configuration, providers and project structure | [docs/config.md](docs/config.md) |
| Terminal and chat commands | [docs/commands.md](docs/commands.md) |
| Avatar, dashboard, settings, phone | [docs/dashboard.md](docs/dashboard.md) |
| Character: emotions, relationship, journal | [docs/character.md](docs/character.md) |
| Data, privacy and security | [docs/security.md](docs/security.md) |
| Updates | [docs/updating.md](docs/updating.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |

Built-in MCP bridge to Hermes / nanoMuse (`HERMES_URL`, `NANOMUSE_URL`): [bridges/README.md](bridges/README.md).

## Installation

Always run Monika **from the project folder** (`.env` is looked up there; the project is not
meant to be installed as a package).

- **Python 3.12+**.
- Access to an **OpenAI-compatible API** (key and URL in `.env`).
- For voice: `ffmpeg`, PyTorch, and ideally a GPU (4 GB is enough; otherwise synthesis
  runs on the CPU, more slowly); sound goes through `sounddevice`/PortAudio (cross-platform;
  on Linux, install the PortAudio library).
- For the example MCP servers: Node.js (`npx`).
- Disk space for models downloaded on first use: about 2 GB (documents),
  2 GB (voice); a connection is needed at that moment. Memory reuses the document
  embedding model (~1 GB) on its first exchange, unless `MEMORY_EMBEDDINGS_ENABLED=false`.

The quickest path is the installer; it creates the venv, installs the requirements, copies
`.env` and `config/settings.json`, then runs the first-run wizard:

```bash
./scripts/install.sh         # add --voice to install GPT-SoVITS in vendor/
```

Or do it by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # or let `python -m monika --setup` write it
cp config/settings.example.json config/settings.json
python -m monika --setup      # language, provider, key, optional voice
python -m monika --config     # check the configuration (secrets masked)
python -m monika               # start Monika: the conversation happens in the page
```

**Providers.** **OpenAI Custom** (`LLM_PROVIDER=custom`) keeps your own OpenAI-compatible endpoint.
`openrouter` and `ollama` fill the address and model automatically:

```bash
# OpenRouter
LLM_PROVIDER=openrouter
API_KEY=sk-or-...

# Ollama, locally
LLM_PROVIDER=ollama            # http://127.0.0.1:11434/v1, qwen2.5:7b
```

The settings page has the same choice, with the key field. A provider change needs a restart.

To see the wizard without touching your configuration, `python -m monika --setup --demo` asks the
same questions in a temporary folder and prints the `.env` and `settings.json` it would have
written (the key is masked). `./scripts/install.sh --demo` shows the installer steps and then runs
that demo wizard, installing nothing.

**Speech recognition** uses **faster-whisper**, running locally on the CPU; the model
(`STT_MODEL`, `small` by default) is downloaded on first use and `STT_LANGUAGE` sets the spoken
language (empty follows `LANGUAGE`).

**GPT-SoVITS** now lives in the project:

```bash
./scripts/install_gptsovits.sh          # vendor/GPT-SoVITS, pinned commit
./scripts/install_gptsovits.sh --models # also the pretrained weights
./lancer-monika.sh                      # reuses vendor/GPT-SoVITS by default
```

**Scanned PDFs** are read with RapidOCR (`pypdfium2` renders the page, `rapidocr-onnxruntime`
recognises it). Tune it with `RAG_OCR_ENABLED`, `RAG_OCR_MIN_CHARS`, `RAG_OCR_MAX_PAGES` and
`RAG_OCR_DPI`.

**Interface language**: French or English. Use the selector on `/settings` or append
`?lang=en` to the page address; prompts and built-in lexicons follow `LANGUAGE`.

**Updates**: `python -m monika --update` compares the checkout with its git remote and prints
the commands; it never pulls by itself. Details: [docs/updating.md](docs/updating.md).

## Running

```bash
python -m monika               # the conversation happens in the page (http://localhost:8765)
./lancer-monika.sh             # starts the GPT-SoVITS voice server if needed, then starts Monika
python -m monika --mode voix   # conversation out loud (answers also shown on screen)
python -m monika --config      # shows the loaded configuration (secrets masked)
```

All other options, chat commands and settings: [docs/commandes.md](docs/commands.md).
