"""Settings page and health grid: the only place where the browser changes configuration.

Reads are token-protected too (the page holds the persona and the model names), unlike `/api/status`.
Writes reuse the existing `act()` helper: same token, same `hmac.compare_digest`, same `ActionError`.
Every probe is bounded and independent: a component that is down paints one red light, never blocks the
page, and a component that is simply off paints gray (`ok: None`), not red.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from monika.config import (
    Settings,
    SettingsError,
    apply_settings_changes,
    editable_settings,
    pinned_fields,
    restart_required_fields,
    settings_json_path,
    write_env_secret,
)
from monika.paths import Paths
from security.words import (
    TIERS,
    WordBank,
    WordsError,
    read_words,
    validate_tiers,
    write_words,
)
from web.server import ActionError

PROBE_TIMEOUT_S = 1.5

# What the page shows. A curated list stays readable; the rest stays editable by hand in settings.json.
CATALOG: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Modèle et génération",
        ("model_name", "llm_temperature", "llm_timeout_s", "max_steps"),
    ),
    (
        "Comportement",
        (
            "user_name",
            "persona_text",
            "context_budget_tokens",
            "context_keep_recent_tokens",
            "filler_delay_s",
            "filler_repeat_s",
            "filler_max_per_turn",
        ),
    ),
    (
        "Personnage",
        (
            "character_enabled",
            "affect_llm_enabled",
            "affect_timeout_s",
            "journal_enabled",
            "journal_every_turns",
            "journal_max_chars",
        ),
    ),
    (
        "Rôles",
        (
            "director_enabled",
            "director_llm_model",
            "director_timeout_s",
            "action_llm_enabled",
            "action_llm_model",
        ),
    ),
    (
        "Mémoire",
        ("memory_inject_all_max", "memory_recall_limit", "memory_recall_threshold"),
    ),
    ("Sessions", ("sessions_enabled", "sessions_keep_days")),
    ("Noyau persistant", ("kernel_enabled",)),
    ("Tâches planifiées", ("tasks_enabled", "tasks_max")),
    ("Documents", ("rag_top_k", "rag_min_score", "rag_rerank_enabled")),
    (
        "Voix",
        (
            "tts_enabled",
            "tts_text_mode",
            "tts_speed",
            "tts_emotion_tags",
            "tts_render",
            "tts_pause_style",
            "tts_pause_scale",
            "tts_pause_max_s",
        ),
    ),
    ("Mots sensibles", ("sensitive_words_enabled",)),
    ("Page et journaux", ("web_port", "log_level")),
)


@dataclass
class Health:
    """What the grid probes. Each reference is filled as the component starts (None = disabled)."""

    started_at: float = field(default_factory=time.monotonic)
    llm: Any = None
    agent: Any = None
    speaker: Any = None
    listener: Any = None
    mcp: Any = None
    hub: Any = None


def _row(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _short(exc: BaseException, limit: int = 120) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kind(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if name == "persona_text" or (
        isinstance(value, str) and ("\n" in value or len(value) > 60)
    ):
        return "long"
    return "text"


def _duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


class SettingsApi:
    """Everything the settings page reads and the two writes it may make."""

    def __init__(self, settings: Settings, paths: Paths, health: Health) -> None:
        self._s = settings
        self._paths = paths
        self._health = health

    # ------------------------------------------------------------------------------------ reading
    def read(self) -> dict[str, Any]:
        """Editable values, grouped for the page, plus what the page must not pretend to control."""
        values = editable_settings(self._s)
        pinned = pinned_fields()
        groups = []
        for title, names in CATALOG:
            fields = [
                {
                    "name": name,
                    "kind": _kind(name, values[name]),
                    "value": values[name],
                    "pinned": name in pinned,
                }
                for name in names
                if name in values
            ]
            if fields:
                groups.append({"title": title, "fields": fields})
        return {
            "groups": groups,
            "pinned": sorted(pinned),
            "restart_required": restart_required_fields(),
            "secrets": {
                "OPENAI_API_KEY": bool(self._s.openai_api_key.get_secret_value())
            },
            "settings_file": str(settings_json_path()),
            "words": self._words(),
        }

    def _words(self) -> dict[str, list[str]]:
        """The bank the running agent actually matches (or the file, before the agent exists)."""
        agent = self._health.agent
        bank = getattr(agent, "words", None) if agent is not None else None
        if bank is not None:
            return bank.words
        try:
            return read_words(self._paths.words_file)
        except WordsError:
            return {tier: [] for tier in TIERS}

    # ------------------------------------------------------------------------------------ health
    async def health(self) -> dict[str, Any]:
        """All probes in parallel, each with its own bound; one failure never hides the others."""
        began = time.monotonic()
        rows = await asyncio.gather(
            self._llm_row(),
            self._stt_row(),
            self._tts_row(),
            self._mcp_row(),
            self._memory_row(),
            self._rag_row(),
            self._web_row(),
            return_exceptions=True,
        )
        labelled = [
            "LLM",
            "Reconnaissance vocale",
            "Voix (GPT-SoVITS)",
            "Serveurs MCP",
            "Mémoire rapide",
            "Documents",
            "Page locale",
        ]
        out = []
        for name, row in zip(labelled, rows):
            if isinstance(row, BaseException):
                out.append(_row(name, False, f"{type(row).__name__}: {_short(row)}"))
            else:
                out.append(row)
        return {"rows": out, "took_ms": round((time.monotonic() - began) * 1000)}

    async def _llm_row(self) -> dict[str, Any]:
        llm = self._health.llm
        if llm is None:
            return _row("LLM", None, "désactivé")
        last = llm.last_call
        tail = (
            ""
            if last is None
            else (
                " · dernier appel : ok"
                if last.get("ok")
                else " · dernier appel : échec"
            )
        )
        try:
            detail = await asyncio.wait_for(
                llm.ping(PROBE_TIMEOUT_S), PROBE_TIMEOUT_S + 0.5
            )
        except Exception as exc:  # noqa: BLE001 - a probe never raises
            return _row(
                "LLM", False, f"{self._s.llm_base_url} ne répond pas ({_short(exc)})"
            )
        return _row("LLM", True, f"{self._s.model_name} · {detail}{tail}")

    async def _stt_row(self) -> dict[str, Any]:
        listener = self._health.listener
        if listener is None:
            return _row("Reconnaissance vocale", None, "désactivée (mode texte)")
        transcriber = listener.transcriber
        if transcriber.loaded:
            return _row(
                "Reconnaissance vocale",
                True,
                f"Whisper « {transcriber.model_name} » sur {transcriber.device}",
            )
        return _row(
            "Reconnaissance vocale",
            None,
            f"Whisper « {transcriber.model_name} » : modèle non chargé",
        )

    async def _tts_row(self) -> dict[str, Any]:
        speaker = self._health.speaker
        if speaker is None:
            return _row("Voix (GPT-SoVITS)", None, "désactivée")
        url = urlparse(self._s.tts_url)
        host, port = url.hostname or "127.0.0.1", url.port or 9880
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_S):
                reader, writer = await asyncio.open_connection(host, port)
                writer.close()
                await writer.wait_closed()
                del reader
        except Exception as exc:  # noqa: BLE001
            return _row(
                "Voix (GPT-SoVITS)", False, f"{host}:{port} injoignable ({_short(exc)})"
            )
        engine = getattr(speaker, "engine", None)
        parts = [f"{host}:{port} répond"]
        if engine is not None:
            if engine.loaded:
                name = getattr(engine, "character_name", "") or "sans nom"
                parts.append(f"voix « {name} » à {engine.sample_rate} Hz")
            else:
                parts.append("voix pas encore chargée")
            parts.append(f"{len(engine.available_tags or ())} tag(s)")
        return _row("Voix (GPT-SoVITS)", True, " · ".join(parts))

    async def _mcp_row(self) -> dict[str, Any]:
        manager = self._health.mcp
        if manager is None:
            return _row("Serveurs MCP", None, "aucun serveur configuré")
        statuses = list(getattr(manager, "statuses", []))
        if not statuses:
            return _row("Serveurs MCP", None, "aucun serveur configuré")
        failed = [status for status in statuses if not getattr(status, "ok", False)]
        detail = f"{len(statuses) - len(failed)}/{len(statuses)} serveur(s) prêt(s)"
        if failed:
            detail += " · " + ", ".join(f"{s.name} : {s.detail}" for s in failed[:3])
        return _row("Serveurs MCP", not failed, detail)

    async def _memory_row(self) -> dict[str, Any]:
        agent = self._health.agent
        memory = getattr(agent, "memory", None) if agent is not None else None
        if memory is None:
            return _row("Mémoire rapide", None, "désactivée ou indisponible")
        state = getattr(agent, "memory_ok", None)
        if state is None:
            return _row(
                "Mémoire rapide", None, "prête · aucun rappel depuis le lancement"
            )
        return _row(
            "Mémoire rapide",
            bool(state),
            "dernier rappel : ok"
            if state
            else "dernier rappel : échec (voir la console)",
        )

    async def _rag_row(self) -> dict[str, Any]:
        agent = self._health.agent
        rag = getattr(agent, "rag", None) if agent is not None else None
        if rag is None:
            return _row("Documents", None, "index désactivé ou indisponible")
        counts = rag.counts()
        detail = f"{counts.get('indexed', 0)} fichier(s), {counts.get('chunks', 0)} passage(s)"
        if rag.running:
            detail += " · indexation en cours"
        errors = counts.get("error", 0)
        if errors:
            detail += f" · {errors} erreur(s)"
        return _row("Documents", not errors, detail)

    async def _web_row(self) -> dict[str, Any]:
        hub = self._health.hub
        listeners = getattr(hub, "listeners", 0) if hub is not None else 0
        return _row(
            "Page locale",
            True,
            f"en ligne depuis {_duration(time.monotonic() - self._health.started_at)} · "
            f"{listeners} page(s) connectée(s)",
        )

    # ------------------------------------------------------------------------------------ writing
    def actions(self) -> dict[str, Callable[..., Awaitable[dict[str, Any]]]]:
        return {
            "settings_update": self._update,
            "settings_secret": self._secret,
            "settings_words": self._words_update,
        }

    async def _words_update(self, tiers: dict[str, Any]) -> dict[str, Any]:
        """Validate, write the bank, then swap it into the running agent (hot reload)."""
        try:
            cleaned = validate_tiers(tiers)
            write_words(self._paths.words_file, cleaned)
        except WordsError as exc:
            raise ActionError(str(exc), 400) from exc
        bank = WordBank(cleaned)
        agent = self._health.agent
        if agent is not None:
            agent.words = bank
        self._activity()
        return {"words": bank.counts}

    async def _update(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ActionError(
                "Les réglages doivent arriver sous forme d'objet JSON.", 400
            )
        try:
            restart = apply_settings_changes(
                patch, live=self._s, path=self._paths.settings_file
            )
        except SettingsError as exc:
            raise ActionError(str(exc), 400) from exc
        self._activity()
        return {"restart_required": restart}

    async def _secret(self, name: str, value: str) -> dict[str, Any]:
        try:
            write_env_secret(name, value)
        except SettingsError as exc:
            raise ActionError(str(exc), 400) from exc
        self._activity()
        return {"restart_required": ["openai_api_key"], "secret": True}

    def _activity(self) -> None:
        hub = self._health.hub
        if hub is not None:
            hub.activity("settings")
