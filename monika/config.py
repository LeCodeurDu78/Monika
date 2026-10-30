"""Single configuration entry point for Monika (pydantic-settings)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, JsonConfigSettingsSource, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _home_from_environment() -> Path:
    """MONIKA_HOME from the environment or the .env file, without building the whole Settings yet."""
    raw = os.environ.get("MONIKA_HOME", "").strip()
    if not raw and ENV_FILE.exists():
        from dotenv import dotenv_values

        raw = str(dotenv_values(ENV_FILE).get("MONIKA_HOME") or "").strip()
    return Path(raw or "~/.config/monika").expanduser()


def settings_json_path() -> Path:
    """Where the settings page writes: <MONIKA_HOME>/settings.json."""
    return _home_from_environment() / "settings.json"

# GPT-SoVITS text front-ends; French is read by the English one, hence the aliases.
_LANGUAGES = frozenset({"auto", "en", "zh", "ja", "ko", "yue"})
_LANGUAGE_NAMES = {"chinese": "zh", "english": "en", "japanese": "ja", "korean": "ko",
                   "cantonese": "yue", "french": "en", "français": "en", "fr": "en"}


class SettingsError(Exception):
    """A settings change was refused; the message is in French and ready to display."""


class Settings(BaseSettings):
    """All user-tunable settings: environment, .env file, then settings.json (the page writes it)."""

    # Allow a field named model_name (pydantic reserves the "model_" prefix by default)
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        json_file=settings_json_path(),  # Overridden by the .env: secrets and pinned values stay there
        json_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls: type[Settings], init_settings: Any,
                                   env_settings: Any, dotenv_settings: Any,
                                   file_secret_settings: Any) -> tuple[Any, ...]:
        """Order: init > environment > .env > settings.json (the page never overrides the .env)."""
        return (init_settings, env_settings, dotenv_settings,
                JsonConfigSettingsSource(settings_cls), file_secret_settings)

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

    # Roles: two optional model calls, both off by default and both outside the critical path of a turn
    director_enabled: bool = False  # Before the turn: keep only the useful tools and add one short hint
    director_llm_model: str = ""  # Empty means MODEL_NAME
    director_timeout_s: float = Field(1.5, gt=0)
    action_llm_enabled: bool = False  # After the turn: a short avatar action chain
    action_llm_model: str = ""  # Empty means MODEL_NAME

    # Context management (token counts are estimates, see agent/context.py)
    context_window_tokens: int = Field(1_000_000, ge=8_000)
    context_budget_tokens: int = Field(150_000, ge=4_000)
    context_keep_recent_tokens: int = Field(40_000, ge=1_000)
    tool_result_max_chars: int = Field(20_000, ge=1_000)
    tool_results_keep_days: int = Field(14, ge=1)

    # Sessions: the conversation log on disk (SQLite + FTS5), searchable and resumable (`/sessions`)
    sessions_enabled: bool = False
    sessions_keep_days: int = Field(30, ge=1)

    # Persistent kernel: one process owns memory, documents, tools, the page and the voice, and the
    # terminal becomes its client (`--serve`, `--attach`). Off by default: without it, `python -m monika`
    # keeps working exactly as before.
    kernel_enabled: bool = False
    kernel_socket: Path | None = None  # default: <KERNEL home>/kernel.sock

    # Scheduled tasks: reminders and recurring jobs, fired by the running process (`/rappel`).
    # They need a process to fire: the kernel ideally, otherwise the terminal that is open.
    tasks_enabled: bool = False
    tasks_max: int = Field(50, ge=1, le=500)

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

    # Character: emotions and relationship (favorability) that shape Monika's tone and her 3D avatar
    character_enabled: bool = True
    affect_llm_enabled: bool = True  # A small model call reads the tone of each message; the lexicon is the fallback
    affect_llm_model: str = ""  # Empty means MODEL_NAME
    affect_timeout_s: float = Field(2.0, gt=0)
    # Journal: Monika's own notes about you, written in the background and added to her prompt
    journal_enabled: bool = True
    journal_every_turns: int = Field(8, ge=2, le=100)
    journal_llm_model: str = ""  # Empty means MODEL_NAME
    journal_max_chars: int = Field(1800, ge=300, le=8000)  # Size of the journal section in her prompt

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
    # Passages whose reranker score is below this are dropped (only when reranking worked: the hybrid
    # score has another scale). Raise it to be stricter; -100 keeps everything (useful to see scores).
    rag_min_score: float = Field(0.0, ge=-100.0, le=100.0)
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

    # Speech synthesis: GPT-SoVITS through its own HTTP API (api_v2.py), with a voice pack that holds
    # one reference recording per emotion tag (see voice/gptsovits.py)
    tts_enabled: bool = True
    tts_text_mode: bool = True  # Speak in text mode too
    tts_url: str = "http://127.0.0.1:9880"  # api_v2.py address; 127.0.0.1 only, never proxied
    tts_speakers_dir: Path | None = None  # Root of the voice packs; None means MONIKA_HOME/voice
    tts_character_id: str = ""  # Pack to use when the folder holds several; empty = the only one
    tts_prompt_lang: str = "zh"  # Language of the reference transcripts when character.json is silent
    tts_emotion_tags: str = "auto"  # auto = only the tags the voice pack has, on = trust the mapper, off = never
    tts_render: str = "auto"  # auto = one sentence per request, or force block | sentence
    tts_block_max_chars: int = Field(600, ge=60, le=4000)  # One request = one audio; bounds the wait
    tts_timeout_s: float = Field(45.0, gt=0)  # Longest wait for one spoken sentence
    tts_language: str = "en"  # Text front-end: en, zh, ja, ko, yue or auto (French is read as en)
    tts_speed: float = Field(1.0, ge=0.5, le=2.0)
    tts_max_sentence_chars: int = Field(200, ge=60, le=240)
    audio_output_device: str = ""  # PulseAudio/PipeWire sink for pacat; empty = default output

    # Local web page: 3D avatar and dashboard (reachable from this computer only)
    web_enabled: bool = True
    web_port: int = Field(8765, ge=1024, le=65535)

    # Optional first name used in the system prompt
    user_name: str = ""
    # Free-form instructions about Monika's behaviour, added to her system prompt (settings page)
    persona_text: str = Field("", max_length=2000)

    # Waiting phrases spoken while a slow tool runs
    filler_delay_s: float = Field(1.5, ge=0)
    filler_repeat_s: float = Field(8.0, gt=0)
    filler_max_per_turn: int = Field(2, ge=0)

    # Sensitive words: a deterministic three-level filter on the user's own list (no word is shipped)
    sensitive_words_enabled: bool = True

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

    @field_validator("kernel_socket", mode="before")
    @classmethod
    def _expand_socket(cls, value: Any) -> Any:
        # KERNEL_SOCKET= (empty) means the default path, not "." (which pydantic would make of "")
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return Path(value).expanduser() if isinstance(value, str) else value.expanduser()

    @field_validator("persona_text")
    @classmethod
    def _clean_persona(cls, value: str) -> str:
        """One text, never a tag: angle brackets are removed so it cannot look like a system envelope."""
        return value.replace("<", "").replace(">", "").strip()

    @field_validator("mcp_servers_file")
    @classmethod
    def _expand_mcp_file(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value else None

    @field_validator("tts_speakers_dir")
    @classmethod
    def _expand_voice_root(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value else None

    @field_validator("tts_url")
    @classmethod
    def _strip_tts_slash(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("TTS_URL doit commencer par http:// ou https://")
        return value

    @field_validator("stt_device")
    @classmethod
    def _stt_device(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "cpu", "cuda"}:
            raise ValueError("STT_DEVICE doit valoir auto, cpu ou cuda")
        return value

    @field_validator("tts_language", "tts_prompt_lang")
    @classmethod
    def _tts_language(cls, value: str) -> str:
        code = _LANGUAGE_NAMES.get(value.strip().lower(), value.strip().lower())
        if code not in _LANGUAGES:
            raise ValueError("TTS_LANGUAGE et TTS_PROMPT_LANG valent auto, en, zh, ja, ko ou yue "
                             "(GPT-SoVITS n'a pas de frontal français : fr est lu comme en)")
        return code

    @field_validator("tts_emotion_tags")
    @classmethod
    def _tts_emotion_tags(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "on", "off"}:
            raise ValueError("TTS_EMOTION_TAGS doit valoir auto, on ou off")
        return value

    @field_validator("tts_render")
    @classmethod
    def _tts_render(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "sentence", "block"}:
            raise ValueError("TTS_RENDER doit valoir auto, sentence ou block")
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

    def tts_render_mode(self) -> str:
        """`sentence` (fastest first sound, the default) or `block` (one request per few sentences)."""
        if self.tts_render != "auto":
            return self.tts_render
        return "sentence"

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


_problems: list[str] = []


def settings_load_problems() -> list[str]:
    """What went wrong with settings.json at load time (empty when everything was fine)."""
    return list(_problems)


def _settings_file_problem(path: Path) -> str:
    """'' when the file can be loaded, a French reason otherwise. Never raises."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as exc:
        return f"JSON illisible ({exc})"
    if not isinstance(data, dict):
        return "le contenu n'est pas un objet JSON"
    try:
        Settings(**data)  # The same constructor the app uses: out-of-range values are caught here too
    except ValidationError as exc:
        return _first_problem(exc, written=False)
    except Exception as exc:  # noqa: BLE001 - anything unusable is put aside rather than crashing the launch
        return f"{type(exc).__name__}: {exc}"
    return ""


def quarantine_unusable_settings(path: Path | None = None) -> str:
    """Move a settings.json the app cannot load aside; returns the message to show ('' when it is fine)."""
    path = path or settings_json_path()
    if not path.exists():
        return ""
    problem = _settings_file_problem(path)
    if not problem:
        return ""
    backup = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(path, backup)
    except OSError as exc:
        message = f"{path} est inutilisable ({problem}) et n'a pas pu être mis de côté : {exc}"
    else:
        message = (f"{path.name} était inutilisable ({problem}) : mis de côté dans {backup.name}. "
                   "Je repartis des réglages par défaut ; corrige-le et renomme-le si tu y tiens.")
    _problems.append(message)
    return message


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance; a hand-edited file that cannot load is put aside first."""
    quarantine_unusable_settings()
    return Settings()


# --- what the settings page may change ---------------------------------------------------------

# Paths and secrets are edited by hand: writing one from the browser would turn the page into a way of
# making Monika read arbitrary folders. OPENAI_API_KEY has its own one-line path below.
NOT_EDITABLE = frozenset({"monika_home", "rag_roots", "mcp_servers_file", "openai_api_key",
                          "tts_speakers_dir", "kernel_socket"})

# Values a component reads once at startup (models, clients, devices, ports, index settings): saving them
# works, but they only take effect after a restart and the page says so.
RESTART_REQUIRED = frozenset({
    "llm_base_url", "llm_timeout_s", "llm_max_retries",
    "mcp_start_timeout_s", "mcp_call_timeout_s",
    "fast_memory_enabled", "embedding_model", "memory_llm_model", "memory_capture_enabled",
    "memory_capture_min_chars",
    "rag_enabled", "rag_include_hidden", "rag_embedding_model", "rag_chunk_chars",
    "rag_chunk_overlap_chars", "rag_reranker_model", "rag_embed_batch", "rag_threads",
    "rag_exclude",
    "stt_model", "stt_device", "stt_compute_type", "stt_cpu_threads", "stt_language",
    "audio_input_device", "audio_output_device",
    "tts_enabled", "tts_url", "tts_character_id", "tts_prompt_lang", "tts_emotion_tags",
    "tts_render", "tts_block_max_chars", "tts_language", "tts_timeout_s", "tts_text_mode",
    "character_enabled", "affect_llm_enabled", "affect_llm_model", "affect_timeout_s",
    "journal_enabled", "journal_every_turns", "journal_max_chars",
    "action_llm_enabled", "action_llm_model",
    "tool_result_max_chars", "tool_results_keep_days", "audit_enabled", "audit_max_chars",
    "sessions_enabled", "sessions_keep_days",
    "kernel_enabled", "tasks_enabled", "tasks_max",
    "log_level",
    "web_enabled", "web_port",
})

EDITABLE = frozenset(Settings.model_fields) - NOT_EDITABLE
ENV_WRITABLE = frozenset({"OPENAI_API_KEY"})  # The only secret the page may write, one line in .env
_BOUNDS = frozenset({"less_than", "less_than_equal", "greater_than", "greater_than_equal"})


def editable_settings(live: Settings | None = None) -> dict[str, Any]:
    """Current values the page shows and may change, JSON-ready (no secret, no path)."""
    live = live or get_settings()
    return {name: _jsonable(getattr(live, name)) for name in sorted(EDITABLE)}


def pinned_fields() -> set[str]:
    """Fields forced by the environment or the .env: saving them in settings.json has no effect."""
    keys = {k.upper() for k in os.environ}
    if ENV_FILE.exists():
        from dotenv import dotenv_values

        keys |= {str(k).upper() for k in dotenv_values(ENV_FILE)}
    return {name for name in Settings.model_fields if name.upper() in keys}


def restart_required_fields() -> list[str]:
    return sorted(RESTART_REQUIRED & EDITABLE)


def apply_settings_changes(patch: dict[str, Any], live: Settings | None = None,
                           path: Path | None = None) -> list[str]:
    """Validate, write settings.json, then update the live instance. Returns the keys needing a restart.

    All or nothing: a value out of range writes nothing and changes nothing on the running instance.
    """
    if not isinstance(patch, dict) or not patch:
        raise SettingsError("Aucun réglage à enregistrer.")
    refused = sorted(set(patch) - EDITABLE)
    if refused:
        raise SettingsError("Ces réglages ne s'éditent pas depuis la page : " + ", ".join(refused) + ".")
    pinned = sorted(set(patch) & pinned_fields())
    if pinned:
        # The .env wins over settings.json: writing them would look like it worked until the next launch
        raise SettingsError("Figés par l'environnement ou le fichier .env (aucun effet depuis la page) : "
                            + ", ".join(pinned) + ". Modifie-les à la main dans .env.")
    live = live or get_settings()
    merged = {**_read_json(path), **patch}
    try:
        candidate = Settings(**merged)  # init wins: the values as typed are what gets validated
    except ValidationError as exc:
        raise SettingsError(_first_problem(exc)) from exc
    except ValueError as exc:  # settings.json broken by hand while Monika runs (JSONDecodeError)
        raise SettingsError(f"settings.json est illisible ({exc}) : corrige ou supprime-le.") from exc
    values = {name: _jsonable(getattr(candidate, name)) for name in merged}
    before = {name: _jsonable(getattr(live, name)) for name in values}
    _write_json(values, path)  # Disk first: if this fails, the running instance keeps its old values
    for name in values:
        if name in EDITABLE:  # A hand-written key the page must not touch (a path, a secret) stays on disk
            setattr(live, name, getattr(candidate, name))
    changed = {name for name, value in values.items() if name in EDITABLE and before[name] != value}
    return sorted(changed & RESTART_REQUIRED)


def write_env_secret(name: str, value: str) -> None:
    """Replace one line of the project's .env. The value never goes to a log and is never read back."""
    key = str(name).strip().upper()
    if key not in ENV_WRITABLE:
        raise SettingsError("Ce secret ne s'écrit pas depuis la page.")
    value = str(value).strip()
    if not value or "\n" in value or "\r" in value:
        raise SettingsError("La valeur doit tenir sur une seule ligne.")
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    except OSError as exc:
        raise SettingsError(f"Impossible de lire {ENV_FILE} : {exc.strerror or exc}.") from exc
    out: list[str] = []
    replaced = False
    for line in lines:
        name_part = line.split("=", 1)[0].strip().upper()
        if not line.lstrip().startswith("#") and name_part == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    _atomic_write(ENV_FILE, "\n".join(out) + "\n")
    setattr(get_settings(), "openai_api_key", SecretStr(value))


def _read_json(path: Path | None = None) -> dict[str, Any]:
    path = path or settings_json_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{path} est illisible ({exc}) : corrige ou supprime-le.") from exc
    return data if isinstance(data, dict) else {}


def _write_json(values: dict[str, Any], path: Path | None = None) -> None:
    _atomic_write(path or settings_json_path(),
                  json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    """Write a private file in one go: no reader ever sees half of it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise SettingsError(f"Impossible d'écrire {path} : {exc.strerror or exc}.") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _first_problem(exc: ValidationError, written: bool = True) -> str:
    """One French sentence about the first refused value (never the value itself, it may be a secret)."""
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error.get("loc", ())) or "réglage"
    suffix = ", rien n'a été écrit." if written else "."
    if error.get("type") in _BOUNDS:
        return f"{field} : hors bornes{suffix}"
    return f"{field} : {error.get('msg', 'valeur invalide')}{suffix}"
