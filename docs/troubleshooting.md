# Troubleshooting

| Symptom | What to check |
|---|---|
| `Port 8765 is already in use` | Another instance is running: close it or change `WEB_PORT` |
| Microphone not found | `python -m sounddevice` must list an input; then `AUDIO_INPUT_DEVICE` |
| No voice | The startup message; `python -m sounddevice` must list an output; the voice pack in `~/.config/monika/voice` |
| `The GPT-SoVITS server (…) is not responding` | Start `api_v2.py` on `127.0.0.1:9880`, or fix `TTS_URL` |
| Neutral voice | The pack has no recording for the tag: `TTS_EMOTION_TAGS=auto` only uses tags that have a file |
| `No GPT-SoVITS voice pack in …` | `references/emotions.json` is missing (see [The GPT-SoVITS voice](voice.md#the-gpt-sovits-voice)) |
| A scanned PDF is still empty | Install `rapidocr-onnxruntime` and `pypdfium2`, keep `RAG_OCR_ENABLED=true`, raise `RAG_OCR_MAX_PAGES` |
| OpenRouter answers 401 | The key is written in `.env` as `API_KEY`; restart after changing it |
| Ollama is unreachable | Start `ollama serve`; the default endpoint is `http://127.0.0.1:11434/v1` |
| An MCP server was edited but tools did not change | `mcp_servers.json` is watched (a couple of seconds); check `python -m monika --tools` and `logs/mcp-<name>.log` |
| Choppy voice | Close the programs saturating the server's GPU |
| An MCP server does not appear | `python -m monika --tools`; a missing `${…}` variable; see `logs/mcp-<name>.log` |
| `create_custom_tools` refuses to test | `bwrap --version` or `docker --version` must answer (bubblewrap or Docker): without a sandbox, no custom tool is created |
| A custom tool disappeared after a restart | Its code changed since it was verified: ask Monika to regenerate it (verified and tested again) |
| The index refuses to open | Embedding model or chunk size changed: `python -m monika --rag-index --reset` |
| Memory no longer updates | `MEMORY_CAPTURE_ENABLED`, `MEMORY_CONSOLIDATE_TURNS`, or the director failed: see the logs; `/memory` stays readable and editable. An old `notes.json` is archived as `notes.legacy-…` |
| Memory recalled by keywords only | `MEMORY_EMBEDDINGS_ENABLED=false`, or the fastembed model could not load: see the logs; BM25 recall continues |
| `/sessions` is empty | No conversation has been saved yet; sessions are always on |
| `Another Monika instance is already using this folder` | A Monika is already running: open its page instead of starting a second one |
| A reminder does not fire | `TASKS_ENABLED=false`, or no Monika process is running: leave `python -m monika` open; `/tasks` shows what is scheduled |
| A reminder arrives late | It fell due while Monika was stopped: next time, leave `python -m monika` running |
| No avatar | WebGL is required in the browser; the dashboard works without it |
