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
| Voice: listening, speaking, GPT-SoVITS, page microphone | [docs/voice.md](docs/voix.md)                |
| Personal memory and documents (RAG) | [docs/memory.md](docs/memoire.md)            |
| MCP tools, built-in tools and custom tools | [docs/tools.md](docs/outils.md)              |
| Agent, context, recipes, sessions, tasks, briefing | [docs/agent.md](docs/agent.md)               |
| Configuration and project structure | [docs/config.md](docs/configuration.md)      |
| Terminal and chat commands | [docs/commands.md](docs/commandes.md)        |
| Avatar, dashboard, settings, phone | [docs/dashboard.md](docs/page.md)            |
| Character: emotions, relationship, journal | [docs/character.md](docs/personnage.md)      |
| Data, privacy, security, sensitive words | [docs/security.md](docs/securite.md)         |
| Troubleshooting | [docs/troubleshooting.md](docs/depannage.md) |

MCP bridge to a helper (Hermes, nanoMuse): [bridges/README.md](bridges/README.md).

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

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit LLM_BASE_URL, OPENAI_API_KEY, MODEL_NAME
python -m monika --config     # check the configuration (secrets masked)
python -m monika               # start Monika: the conversation happens in the page
```

Voice mode needs an extra installation, described in [docs/voix.md](docs/voix.md).

## Running

```bash
python -m monika               # the conversation happens in the page (http://localhost:8765)
./lancer-monika.sh             # starts the GPT-SoVITS voice server if needed, then starts Monika
python -m monika --mode voix   # conversation out loud (answers also shown on screen)
python -m monika --config      # shows the loaded configuration (secrets masked)
```

All other options, chat commands and settings: [docs/commandes.md](docs/commandes.md).
