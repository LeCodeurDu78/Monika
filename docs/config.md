# Configuration and project structure

## Configuration

Every setting lives in `.env` (commented template: `.env.example`, which matches the code's
default values exactly). The most useful ones:

| Setting | Purpose |
|---|---|
| `LANGUAGE` | Language of the interface, prompts and lexicons: `fr` (default) or `en` |
| `LLM_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` | Provider and model (OpenAI-compatible API) |
| `REFLECTION_MODEL` | Model for the reflection role (memory, summaries, nightly review); empty = `MODEL_NAME` |
| `SURFACE_LLM_MODEL`, `SURFACE_PROMPT`, `SURFACE_TIMEOUT_S` | Surface role: an immediate line before the tools (empty = disabled) |
| `REFLECTION_ENABLED`, `REFLECTION_SPEAK`, `REFLECTION_PROMPT` | Advice prepared between turns and spontaneous speech (disabled by default) |
| `MONIKA_HOME` | Data folder (default `~/.config/monika`) |
| `USER_NAME` | Your first name, used in the system prompt |
| `PERSONA_TEXT` | Free-form instructions about her behaviour, added to her prompt (settings page) |
| `MAX_STEPS` | Model calls per turn (default 100) |
| `ACTION_LLM_ENABLED`, `ACTION_LLM_MODEL` | Action role: the avatar's gesture chain after the reply (disabled by default) |
| `CONTEXT_BUDGET_TOKENS` | Automatic summarization threshold (default 150,000) |
| `SESSIONS_ENABLED`, `SESSIONS_KEEP_DAYS` | Conversation history on disk (disabled by default; 30-day retention) |
| `TASKS_ENABLED`, `TASKS_MAX` | Reminders and recurring tasks (disabled by default, 50 tasks max) |
| `BRIEFING_ENABLED`, `BRIEFING_TIME`, `BRIEFING_DAYS` | Morning briefing: weather, calendar, mail and the day's tasks (disabled by default, 07:30) |
| `BRIEFING_CITY`, `BRIEFING_SECTIONS` | Weather city (Open-Meteo, no key) and requested sections |
| `FAST_MEMORY_ENABLED`, `MEMORY_CAPTURE_ENABLED` | Evidence memory and automatic capture |
| `MEMORY_EMBEDDINGS_ENABLED`, `MEMORY_EMBEDDING_MODEL` | Hybrid recall: fastembed embeddings (empty = `RAG_EMBEDDING_MODEL`); `false` = BM25 only |
| `RAG_ENABLED`, `RAG_ROOTS`, `RAG_EXCLUDE`, `RAG_MIN_SCORE` | Personal documents (including the relevance threshold) |
| `STT_MODEL`, `TTS_ENABLED` | Voice |
| `TTS_URL`, `TTS_SPEAKERS_DIR`, `TTS_CHARACTER_ID` | GPT-SoVITS: API address and voice pack |
| `TTS_LANGUAGE`, `TTS_EMOTION_TAGS`, `TTS_RENDER` | Spoken language, emotion tags, request splitting |
| `TTS_PAUSE_STYLE`, `TTS_PAUSE_SCALE`, `TTS_PAUSE_MAX_S` | Pauses between sentences: `neutre` (nothing) or `naturel` |
| `WEB_ENABLED`, `WEB_PORT`, `WEB_HOST` | Local page (default 8765, on `127.0.0.1` only) |
| `WEB_TOKEN` | Page access token (empty = none); set it as soon as `WEB_HOST` is no longer local |
| `SENSITIVE_WORDS_ENABLED` | Sensitive-word filter (the list is in `words.json`) |
| `RECIPES_ENABLED`, `RECIPES_MIN_TOOLS`, `RECIPES_INJECT_MAX`, `RECIPES_MAX` | Learned recipes: proposal after a successful turn, minimum tools, recipes injected per turn, total |
| `LOG_LEVEL`, `AUDIT_ENABLED` | Logs |

An invalid value is refused at startup with a clear message (for example
`LOOP_STOP_AFTER` must be greater than `LOOP_WARN_AFTER`, and
`CONTEXT_KEEP_RECENT_TOKENS` < `CONTEXT_BUDGET_TOKENS` < `CONTEXT_WINDOW_TOKENS`).

## Project structure

```
monika/        Entry point (python -m monika), configuration (config.py), paths (paths.py), embeddings.py,
               instance lock (lock.py), phone link (phone.py)
agent/         ReAct loop (loop.py), LLM client (llm.py), context (context.py), prompt (prompt.py),
               persistent sessions (sessions.py), scheduled tasks (tasks.py), loop detection,
               waiting phrases, system information
tools/         Audited tool registry (registry.py), MCP client (mcp_client.py), built-in tools (internal.py)
security/      Secret masking (masking.py), "data, not orders" envelope (untrusted.py), audit (audit.py)
bridges/       Standalone MCP bridge to an HTTP helper (Hermes, nanoMuse): ask, status, answer, stop, health
memory/        Evidence memory: events, quoted facts, corrections, hybrid recall (notes.py)
               and background capture (capture.py)
rag/           Personal documents: scan, read, chunk, index, hybrid search, reranking
character/     Emotions, relationship (favour), journal, avatar gestures
voice/         Microphone, speech detection, Whisper, GPT-SoVITS (HTTP client), audio playback, cleanup
               of spoken text
modes/         Conversation turn and commands (text.py), voice reader (voice.py)
web/           Local server (server.py), dashboard (dashboard.py), live broadcast (hub.py),
               page conversation (chat.py), page voice (voice.py) and page (static/:
               index.html, css/, js/, avatar/, clips/, vendor/)
```

The packages (`agent/`, `rag/`, `web/`…) sit at the project root: always run Monika
**from this folder**, with `python -m monika`.
