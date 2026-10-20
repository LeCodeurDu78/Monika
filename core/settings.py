"""Monika's centralised configuration, via pydantic-settings."""

from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Dossier applicatif -----------------------------------------------------
    APP_DIR: Path = Field(default=Path.home() / ".monika")

    # --- LLM ---------------------------------------------------------------------
    MODEL_NAME: str = "qwen3.8-27b"
    VISION_MODEL_NAME: str = "gemini-3.5-flash"
    LLM_BASE_URL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    LLM_VISION_URL: Optional[str] = None
    OPENAI_API_KEY_VISION: Optional[str] = None

    LLM_TEMPERATURE: Optional[float] = 0.3
    LLM_TIMEOUT_SECONDS: float = 120
    LLM_MAX_RETRIES: int = 3
    LLM_EXTRA_BODY_JSON: Optional[str] = None
    LLM_PASS_REASONING: bool = True
    LLM_STREAM: bool = True

    # --- Contexte de conversation ----------------------------------------------------
    TIMEZONE: str = "Europe/Paris"
    CONTEXT_MAX_TOKENS: int = 100000
    CONTEXT_MAX_MESSAGES: int = 200
    CONTEXT_KEEP_USER_TURNS: int = 5
    # During a long task (a single user message), old steps are summarised
    # while keeping at least this many recent messages untouched.
    CONTEXT_KEEP_RECENT_MESSAGES: int = 40
    CONTEXT_PROTECT_LAST_MESSAGES: int = 20
    TOOL_RESULT_MAX_CHARS: int = 30000
    TOOL_RESULT_OLD_MAX_CHARS: int = 8000
    # A truncated result is first saved to disk: the model can re-read it with read_file/grep.
    TOOL_RESULT_SPILL_TO_DISK: bool = True
    TOOL_RESULT_SPILL_RETENTION_DAYS: int = 7

    # --- Boucle d'agent ----------------------------------------------------------------
    # The same tool call (same arguments) that already returned the same result twice is not run a third time:
    # the model gets a warning instead of burning its steps in a loop.
    LOOP_REPEAT_LIMIT: int = 3
    # Tools flagged `lent`: the waiting phrase is only said if the tool exceeds this delay, and at most
    # WAIT_PHRASE_MAX_PER_TURN times per user message.
    WAIT_PHRASE_DELAY_SECONDS: float = 1.5
    WAIT_PHRASE_MAX_PER_TURN: int = 2

    # --- run_script ------------------------------------------------------------------
    SCRIPT_TIMEOUT_SECONDS: int = 120
    SCRIPT_MAX_TIMEOUT_SECONDS: int = 900

    # --- Dossiers projets ------------------------------------------------------------
    # Previously not configurable (os.path.expanduser hard-coded): now overridable.
    CODE_BASE_DIR: Path = Field(default=Path.home() / "Documents" / "Code")
    OBSIDIAN_BASE_DIR: Path = Field(default=Path.home() / "Documents" / "Obsidian")

    # --- E-mail --------------------------------------------------------------------
    EMAIL_USER: Optional[str] = None
    EMAIL_PASS: Optional[str] = None
    IMAP_SERVER: str = "imap.gmail.com"
    SMTP_SERVER: str = "smtp.gmail.com"

    # --- Discord (bot) ------------------------------------------------------------
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_DEFAULT_CHANNEL_ID: Optional[str] = None  # channel used when no recipient is given
    # Listener: Monika answers Discord messages. WARNING: whoever can write to her controls her (files,
    # commands...). Disabled by default; only the IDs listed here are listened to.
    DISCORD_LISTEN: bool = False
    DISCORD_ALLOWED_USER_IDS: str = ""  # comma-separated IDs; the first one receives notifications
    DISCORD_LISTEN_CHANNEL_IDS: str = ""  # channels where she answers without an @mention (in addition to DMs and @mentions)
    # Reading channel text without an @mention requires enabling "Message Content Intent" in the Discord portal.
    DISCORD_MESSAGE_CONTENT_INTENT: bool = False
    DISCORD_FORWARD_SCHEDULED: bool = True  # also DM the result of scheduled tasks
    # Attachments received on Discord: images (vision analysis), PDF / DOCX / text (extraction),
    # audio and voice messages (Whisper transcription). Files are kept in APP_DIR/discord_inbox.
    DISCORD_ATTACHMENTS_ENABLED: bool = True
    DISCORD_ATTACHMENT_MAX_MB: float = 20
    DISCORD_ATTACHMENT_MAX_FILES: int = 5
    DISCORD_ATTACHMENT_MAX_CHARS: int = 12000  # extracted text kept per file (the rest stays readable on disk)
    DISCORD_ATTACHMENT_RETENTION_DAYS: int = 7

    # --- Discord voice calls ---------------------------------------------------------
    # Needs `PyNaCl` and `discord-ext-voice-recv` (see requirements.txt) and the bot's "Connect" and "Speak"
    # permissions. Monika joins a voice channel and talks with you there (local STT/TTS, same as voice mode).
    # Bots cannot ring a phone: "calling you" = a DM with the channel link + joining the channel.
    DISCORD_CALLS_ENABLED: bool = False
    DISCORD_CALL_CHANNEL_ID: Optional[str] = None  # voice channel used when you are not in one yet
    DISCORD_CALL_AUTOJOIN: bool = False  # join automatically when an allowed user enters a voice channel
    DISCORD_CALL_RING_TIMEOUT_SECONDS: int = 300  # how long Monika waits in the channel after calling you
    DISCORD_CALL_LEAVE_AFTER_SECONDS: int = 30  # hang up this long after the last allowed user left

    # --- Morning briefing ---------------------------------------------------------------
    BRIEFING_ENABLED: bool = True
    BRIEFING_TIME: str = "08:00"  # HH:MM, local time (TIMEZONE)
    BRIEFING_CITY: Optional[str] = None  # weather city; empty = the city Monika knows from memory
    BRIEFING_CATCHUP_HOURS: int = 4  # started late? still deliver it if less than this many hours have passed
    BRIEFING_FORWARD_DISCORD: bool = True  # also send it by DM

    # --- Voice: streaming TTS -------------------------------------------------------------
    # Speak sentence by sentence while the model is still writing.
    VOICE_STREAM_TTS: bool = True
    VOICE_STREAM_MIN_CHARS: int = 30  # do not synthesize fragments shorter than this
    VOICE_STREAM_MAX_CHARS: int = 220  # split sentences longer than this at a comma or space
    # Utterance segmentation for Discord calls.
    VOICE_MIN_SPEECH_MS: int = 250  # shorter noises are ignored
    VOICE_PREROLL_MS: int = 300  # audio kept from before the speech onset

    # --- Voix : reconnaissance (STT, faster-whisper) ---------------------------------
    VOICE_STT_LANGUAGE: str = "fr"
    VOICE_STT_MODEL: str = "small"
    VOICE_STT_DEVICE: str = "cuda"
    VOICE_STT_COMPUTE_TYPE: str = "int8"

    # --- Voice: synthesis (TTS, XTTS v2) ----------------------------------------------
    VOICE_XTTS_MODEL: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    VOICE_XTTS_DEVICE: str = "cuda"
    VOICE_XTTS_LANGUAGE: Optional[str] = None
    VOICE_XTTS_SPEAKER_WAV: Optional[Path] = None

    # --- Voix : capture audio (VAD) ------------------------------------------------
    FRAME_MS: int = 30
    SAMPLE_RATE: int = 16000
    VOICE_SILENCE_MS: int = 900
    VOICE_VAD_AGGRESSIVENESS: int = 2
    VOICE_MAX_RECORD_SECONDS: float = 20

    # --- Background tasks --------------------------------------------------------------
    SCHEDULER_CHECK_INTERVAL_SECONDS: int = 30

    # --- Tâches de fond (sous-agent qui travaille pendant que la conversation continue) ---
    BACKGROUND_ENABLED: bool = True
    BACKGROUND_MAX_WORKERS: int = 2  # tasks running at the same time (they share the LLM server)
    BACKGROUND_MAX_ACTIVE: int = 4  # running + waiting; beyond that the agent is told to refuse or cancel one
    BACKGROUND_MAX_STEPS: int = 60  # tool-call rounds of one task
    BACKGROUND_MAX_MINUTES: int = 30  # a task still running after this is stopped before its next action

    # --- Journal d'actions et tableau de bord (http://AVATAR_HOST:AVATAR_PORT/dashboard) ---
    ACTION_LOG_ENABLED: bool = True
    ACTION_LOG_RETENTION_DAYS: int = 30
    DASHBOARD_ENABLED: bool = True  # served by the avatar server, loopback address only

    # --- Browser control -------------------------------------------
    BROWSER_HEADLESS: bool = False

    # --- 3D avatar (animated companion) --------------------------------------------------
    AVATAR_ENABLED: bool = True
    AVATAR_HOST: str = "127.0.0.1"
    AVATAR_PORT: int = 4228
    AVATAR_OPEN_BROWSER: bool = True

    # --- Memory: automatic capture ------------------------------------------------
    MEMORY_FLUSH_EVERY_TURNS: int = 3
    MEMORY_SHUTDOWN_TIMEOUT_SECONDS: float = 30

    # --- Quick memory: mem0 (facts and preferences about the user) -----------------
    MEM0_ENABLED: bool = True
    MEM0_USER_ID: str = "user"
    MEM0_TOP_K: int = 5
    MEM0_MIN_SCORE: float = 0.4
    MEM0_LLM_MODEL: Optional[str] = None

    # --- Quick memory: mem0 (embeddings) -----------------
    MEM0_EMBEDDER_PROVIDER: str = "huggingface"
    MEM0_EMBEDDER_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    MEM0_EMBEDDER_DIMS: int = 384
    MEM0_EMBEDDER_DEVICE: str = "cuda"
    MEM0_EMBEDDER_BASE_URL: Optional[str] = None
    MEM0_EMBEDDER_API_KEY: Optional[str] = None

    # --- Long-term memory: Hindsight (projects, decisions, past exchanges) ----------------
    HINDSIGHT_ENABLED: bool = True
    HINDSIGHT_BASE_URL: str = "http://localhost:8888"
    HINDSIGHT_API_KEY: Optional[str] = None
    HINDSIGHT_BANK_ID: str = "monika"
    HINDSIGHT_TIMEOUT: float = 30
    HINDSIGHT_REFLECT_TIMEOUT: float = 180

    # --- RAG: search in personal documents --------------------------------
    RAG_ENABLED: bool = True
    # Scanned folders (JSON in .env, e.g. RAG_ROOTS='["/home/adam"]'). Default: the whole home folder.
    RAG_ROOTS: list[Path] = Field(default_factory=lambda: [Path.home()])
    # Qdrant: recommended server (docker compose -f dev_animations/docker-compose.qdrant.yml up -d).
    # Local mode (empty RAG_QDRANT_URL) is a brute-force in-memory search: only for < 20,000 chunks.
    RAG_QDRANT_URL: Optional[str] = "http://localhost:6333"
    RAG_QDRANT_API_KEY: Optional[str] = None
    RAG_QDRANT_PATH: Optional[Path] = None  # local mode only; default: APP_DIR/rag/qdrant
    RAG_COLLECTION: str = "monika_documents"
    # Embeddings (e5 needs the "query: " / "passage: " prefixes, handled in rag/models.py)
    RAG_EMBED_MODEL: str = "intfloat/multilingual-e5-base"
    RAG_EMBED_DIMS: int = 768
    RAG_EMBED_DEVICE: str = "cuda"
    RAG_EMBED_BATCH: int = 16
    # Reranker (cross-encoder)
    RAG_RERANK_ENABLED: bool = True
    RAG_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RAG_RERANK_DEVICE: str = "cuda"
    RAG_RERANK_MIN_SCORE: float = 0.01  # sigmoid score 0..1: below it, the chunk is dropped (to calibrate)
    # Recherche
    RAG_CANDIDATES: int = 30  # chunks retrieved (dense + BM25 fused) before reranking
    RAG_TOP_K: int = 5
    RAG_MAX_PER_FILE: int = 2
    # Chunking (in characters; ~1200 chars ≈ 300-350 tokens, under e5's 512 limit)
    RAG_CHUNK_CHARS: int = 1200
    RAG_CHUNK_OVERLAP_CHARS: int = 150
    RAG_MIN_CHUNK_CHARS: int = 120
    # Exploration
    RAG_EXTENSIONS: list[str] = Field(
        default_factory=lambda: [
            ".md", ".markdown", ".txt", ".rst", ".org", ".html", ".htm", ".pdf", ".docx",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".go", ".rs",
            ".sh", ".sql", ".yml", ".yaml", ".toml",
        ]
    )
    RAG_EXCLUDE_DIRS: list[str] = Field(
        default_factory=lambda: [
            "node_modules", "__pycache__", "venv", "site-packages", "dist", "build", "target",
            "vendor", "snap", "Trash", "lost+found", "__MACOSX",
        ]
    )
    RAG_INCLUDE_HIDDEN: bool = False  # folders/files starting with "." (.ssh, .config, .mozilla...)
    RAG_SKIP_SECRETS: bool = False  # True: skip chunks that look like keys/passwords
    RAG_MAX_FILE_MB: float = 2
    RAG_MAX_BINARY_MB: float = 50  # PDF / DOCX
    RAG_MAX_PDF_PAGES: int = 1000
    RAG_SCAN_INTERVAL_SECONDS: int = 900
    RAG_SCAN_INITIAL_DELAY_SECONDS: int = 60
    RAG_FILE_PAUSE_SECONDS: float = 0.05  # short pause between files so the GPU/CPU is not saturated

    @model_validator(mode="after")
    def _resolve_dependent_defaults(self) -> "Settings":
        if self.VOICE_XTTS_LANGUAGE is None:
            self.VOICE_XTTS_LANGUAGE = self.VOICE_STT_LANGUAGE
        if self.VOICE_XTTS_SPEAKER_WAV is None:
            self.VOICE_XTTS_SPEAKER_WAV = self.APP_DIR / "monika_speaker.wav"
        return self


settings = Settings()
settings.APP_DIR.mkdir(parents=True, exist_ok=True)