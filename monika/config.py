"""Single configuration entry point for Monika (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """All user-tunable settings, read from the environment and the .env file."""

    # Allow a field named model_name (pydantic reserves the "model_" prefix by default)
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # LLM (OpenAI-compatible API)
    llm_base_url: str = "https://api.libertai.io/v1"
    openai_api_key: SecretStr = SecretStr("")
    model_name: str = "deepseek-v4.1-flash"
    llm_temperature: float = Field(0.7, ge=0.0, le=2.0)
    llm_timeout_s: float = Field(120.0, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=10)

    # Storage root for every persistent file
    monika_home: Path = Path("~/.config/monika")

    # MCP servers (default file: <home>/mcp_servers.json)
    mcp_servers_file: Path | None = None
    mcp_start_timeout_s: float = Field(30.0, gt=0)
    mcp_call_timeout_s: float = Field(120.0, gt=0)

    # Agent
    max_steps: int = Field(100, ge=1, le=500)
    loop_warn_after: int = Field(3, ge=2)
    loop_stop_after: int = Field(5, ge=3)

    # Context management (token counts are estimates, see agent/context.py)
    context_window_tokens: int = Field(1_000_000, ge=8_000)
    context_budget_tokens: int = Field(150_000, ge=4_000)
    context_keep_recent_tokens: int = Field(40_000, ge=1_000)
    tool_result_max_chars: int = Field(20_000, ge=1_000)
    tool_results_keep_days: int = Field(14, ge=1)

    # Fast memory (mem0 + local Qdrant): facts and preferences about the user
    fast_memory_enabled: bool = True
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    memory_llm_model: str = ""  # Extraction model; empty means MODEL_NAME
    memory_inject_all_max: int = Field(30, ge=0)  # At most this many facts: inject them all
    memory_recall_limit: int = Field(8, ge=1, le=30)
    memory_recall_threshold: float = Field(0.25, ge=0.0, le=1.0)
    memory_recall_timeout_s: float = Field(5.0, gt=0)
    memory_capture_enabled: bool = True
    memory_capture_min_chars: int = Field(20, ge=1)

    # Document index (RAG): llama-index + local Qdrant, fastembed on CPU
    rag_enabled: bool = True
    rag_roots: str = "~"  # Comma-separated folders to index
    rag_exclude: str = ""  # Extra comma-separated name/path globs to skip
    rag_include_hidden: bool = False
    rag_embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    rag_chunk_chars: int = Field(500, ge=100, le=8000)
    rag_chunk_overlap_chars: int = Field(80, ge=0)
    rag_max_text_kb: int = Field(1024, ge=1)
    rag_max_doc_mb: int = Field(50, ge=1)
    rag_max_chunks_per_file: int = Field(5000, ge=1)
    rag_embed_batch: int = Field(16, ge=1, le=256)
    rag_threads: int = Field(0, ge=0)  # ONNX threads for embeddings; 0 = half of the CPU cores
    rag_candidates: int = Field(24, ge=1, le=200)
    rag_top_k: int = Field(5, ge=1, le=30)
    rag_hybrid_alpha: float = Field(0.5, ge=0.0, le=1.0)  # 1 = meaning only, 0 = keywords only
    rag_rerank_enabled: bool = True
    rag_reranker_model: str = "jinaai/jina-reranker-v2-base-multilingual"

    # Speech recognition: faster-whisper (CPU by default) + Silero voice activity detection
    stt_model: str = "small"
    stt_device: str = "auto"  # auto = cpu, to keep the GPU memory for the voice synthesis
    stt_compute_type: str = "int8"
    stt_language: str = "fr"
    stt_beam_size: int = Field(2, ge=1, le=10)
    stt_cpu_threads: int = Field(0, ge=0)  # 0 = half of the CPU cores
    stt_prompt: str = "Conversation en français avec Monika, une assistante personnelle."
    audio_input_device: str = "@DEFAULT_SOURCE@"  # PulseAudio/PipeWire source name for parec
    vad_speech_threshold: float = Field(0.5, gt=0.0, lt=1.0)
    vad_end_silence_ms: int = Field(700, ge=200, le=3000)  # Silence that ends a sentence
    vad_min_speech_ms: int = Field(250, ge=64)
    vad_pre_roll_ms: int = Field(300, ge=0)
    vad_max_utterance_s: int = Field(30, ge=3)
    voice_resume_delay_ms: int = Field(400, ge=0)  # Deaf period after Monika stops talking

    # Speech synthesis: XTTS v2 (Coqui) cloning the voice found in ~/.config/monika/voice
    tts_enabled: bool = True
    tts_text_mode: bool = True  # Speak in text mode too
    tts_accept_license: bool = False  # XTTS v2 is under the non-commercial CPML: accepting is explicit
    tts_device: str = "auto"  # auto = GPU when available (CPU if it runs out of memory), cpu, cuda
    tts_language: str = "fr"
    tts_speed: float = Field(1.0, ge=0.5, le=2.0)
    tts_stream_chunk: int = Field(20, ge=5, le=100)  # GPT tokens per audio chunk: lower = faster first sound
    tts_max_sentence_chars: int = Field(200, ge=60, le=240)
    audio_output_device: str = ""  # PulseAudio/PipeWire sink for pacat; empty = default output

    # Local web page: 3D avatar and dashboard (reachable from this computer only)
    web_enabled: bool = True
    web_port: int = Field(8765, ge=1024, le=65535)

    # Optional first name used in the system prompt
    user_name: str = ""

    # Waiting phrases spoken while a slow tool runs
    filler_delay_s: float = Field(1.5, ge=0)
    filler_repeat_s: float = Field(8.0, gt=0)
    filler_max_per_turn: int = Field(2, ge=0)

    # Logging and audit trail
    log_level: str = "INFO"
    audit_enabled: bool = True
    audit_max_chars: int = Field(2000, ge=100)

    @field_validator("llm_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        # Avoid double slashes when building endpoint URLs
        return value.strip().rstrip("/")

    @field_validator("monika_home")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("mcp_servers_file")
    @classmethod
    def _expand_mcp_file(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value else None

    @field_validator("stt_device")
    @classmethod
    def _stt_device(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "cpu", "cuda"}:
            raise ValueError("STT_DEVICE doit valoir auto, cpu ou cuda")
        return value

    @field_validator("tts_device")
    @classmethod
    def _tts_device(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "cpu", "cuda"}:
            raise ValueError("TTS_DEVICE doit valoir auto, cpu ou cuda")
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"niveau de log invalide : {value!r}")
        return level

    @model_validator(mode="after")
    def _loop_thresholds(self) -> Settings:
        if self.loop_stop_after <= self.loop_warn_after:
            raise ValueError("LOOP_STOP_AFTER doit être supérieur à LOOP_WARN_AFTER")
        if self.rag_chunk_overlap_chars >= self.rag_chunk_chars // 2:
            raise ValueError("RAG_CHUNK_OVERLAP_CHARS doit rester sous la moitié de RAG_CHUNK_CHARS")
        if not self.context_keep_recent_tokens < self.context_budget_tokens < self.context_window_tokens:
            raise ValueError("il faut CONTEXT_KEEP_RECENT_TOKENS < CONTEXT_BUDGET_TOKENS "
                             "< CONTEXT_WINDOW_TOKENS")
        return self

    def rag_root_paths(self) -> list[Path]:
        """Expanded folders to index."""
        return [Path(p.strip()).expanduser().resolve() for p in self.rag_roots.split(",") if p.strip()]

    def rag_exclude_patterns(self) -> list[str]:
        return [p.strip() for p in self.rag_exclude.split(",") if p.strip()]

    def secret_values(self) -> list[str]:
        """Return every secret value, used to mask them in logs."""
        return [
            v.get_secret_value()
            for _, v in self
            if isinstance(v, SecretStr) and v.get_secret_value()
        ]

    def public_dict(self) -> dict[str, str]:
        """Return settings as strings with secrets hidden."""
        out: dict[str, str] = {}
        for name, value in self:
            if isinstance(value, SecretStr):
                out[name] = "définie" if value.get_secret_value() else "non définie"
            else:
                out[name] = str(value)
        return out


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
