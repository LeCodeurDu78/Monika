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

from pydantic import SecretStr

from monika.config import (
    PROVIDER_PRESETS,
    Settings,
    SettingsError,
    apply_settings_changes,
    editable_settings,
    pinned_fields,
    restart_required_fields,
    settings_json_path,
    write_env_secret,
    write_env_settings,
)
from monika.i18n import normalize_language
from monika.paths import Paths
from web.server import ActionError

PROBE_TIMEOUT_S = 1.5

# What the page shows. A short, curated list stays readable; everything else stays editable by hand
# in settings.json. Titles, labels and hints are French: the page translates them like the rest of
# the interface.
CATALOG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Général", ("user_name", "persona_text")),
    ("Modèle", ("model_name", "llm_temperature", "llm_timeout_s", "max_steps")),
    (
        "Voix",
        (
            "tts_enabled",
            "tts_text_mode",
            "tts_speed",
            "tts_emotion_tags",
            "tts_pause_style",
            "tts_pause_scale",
            "stt_model",
            "stt_language",
        ),
    ),
    (
        "Mémoire et documents",
        (
            "memory_capture_enabled",
            "memory_consolidate_turns",
            "rag_top_k",
            "rag_min_score",
            "rag_rerank_enabled",
            "rag_ocr_enabled",
        ),
    ),
    ("Personnage", ("character_enabled", "affect_llm_enabled", "journal_enabled")),
    (
        "Automatisations",
        (
            "tasks_enabled",
            "briefing_enabled",
            "briefing_time",
            "briefing_city",
            "nightly_enabled",
            "nightly_time",
        ),
    ),
    ("Avancé", ("log_level", "context_budget_tokens")),
)

# Label and one-line explanation per field (French; translated by the page).
FIELD_META: dict[str, tuple[str, str]] = {
    "user_name": ("Ton prénom", "Le prénom que Monika utilise pour s'adresser à toi."),
    "persona_text": (
        "Consignes de comportement",
        "Ajoutées à son prompt : ton, habitudes, ce qu'elle doit éviter.",
    ),
    "model_name": ("Modèle", "Le modèle servi par ton fournisseur."),
    "llm_temperature": (
        "Température",
        "Plus bas = plus stable, plus haut = plus créatif.",
    ),
    "llm_timeout_s": ("Délai maximal", "Au-delà, l'appel au modèle est abandonné (secondes)."),
    "max_steps": ("Appels d'outils par tour", "Nombre maximal d'outils utilisés dans un tour."),
    "tts_enabled": ("Synthèse vocale", "GPT-SoVITS doit tourner sur la machine."),
    "tts_text_mode": (
        "Parler en mode texte",
        "Fait parler Monika même quand tu écris dans la page.",
    ),
    "tts_speed": ("Vitesse de la voix", "1 = vitesse normale."),
    "tts_emotion_tags": (
        "Émotions dans la voix",
        "Auto n'utilise que les émotions enregistrées dans ta voix.",
    ),
    "tts_pause_style": ("Pauses", "Neutre n'ajoute aucun silence entre les phrases."),
    "tts_pause_scale": ("Longueur des pauses", "Multiplie les pauses calculées."),
    "stt_model": (
        "Reconnaissance vocale",
        "small est un bon compromis ; medium et large sont plus lents.",
    ),
    "stt_language": (
        "Langue parlée",
        "Langue de ton micro ; auto laisse Whisper la deviner.",
    ),
    "memory_capture_enabled": (
        "Mémoriser automatiquement",
        "Retient les faits cités pendant la conversation.",
    ),
    "memory_consolidate_turns": (
        "Consolidation",
        "Réécrit le carnet tous les N tours (minimum 2).",
    ),
    "rag_top_k": ("Passages cités", "Nombre de passages envoyés au modèle."),
    "rag_min_score": (
        "Seuil de pertinence",
        "Sous ce score, un passage est écarté (0 = tout garder).",
    ),
    "rag_rerank_enabled": (
        "Reclassement",
        "Reclasse les passages avant de répondre.",
    ),
    "rag_ocr_enabled": (
        "OCR des PDF scannés",
        "Lit le texte des pages sans couche texte (RapidOCR, CPU).",
    ),
    "character_enabled": (
        "Émotions et relation",
        "Humeur, favorabilité et gestes de l'avatar.",
    ),
    "affect_llm_enabled": (
        "Lire le ton",
        "Petit appel au modèle ; le lexique reste le repli.",
    ),
    "journal_enabled": (
        "Journal de Monika",
        "Elle écrit quelques notes sur toi en arrière-plan.",
    ),
    "tasks_enabled": (
        "Tâches planifiées",
        "Rappels et tâches récurrentes, tant que Monika tourne.",
    ),
    "briefing_enabled": (
        "Briefing du matin",
        "Météo, agenda, mails et tâches une fois par jour.",
    ),
    "briefing_time": ("Heure du briefing", "Heure locale, HH:MM."),
    "briefing_city": ("Ville (météo)", "Vide : pas de météo dans le briefing."),
    "nightly_enabled": (
        "Veille nocturne",
        "Une note de la journée et des idées pour le lendemain.",
    ),
    "nightly_time": ("Heure de la veille", "Heure locale, HH:MM."),
    "log_level": ("Niveau de journal", "DEBUG, INFO, WARNING, ERROR ou CRITICAL."),
    "context_budget_tokens": (
        "Budget de contexte",
        "Au-delà, les anciens messages sont résumés.",
    ),
}

# Fields rendered as a drop-down instead of a free text box.
OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "tts_emotion_tags": (("auto", "Auto"), ("on", "Toujours"), ("off", "Jamais")),
    "tts_pause_style": (("neutre", "Neutre"), ("naturel", "Naturel")),
    "log_level": (
        ("DEBUG", "DEBUG"),
        ("INFO", "INFO"),
        ("WARNING", "WARNING"),
        ("ERROR", "ERROR"),
        ("CRITICAL", "CRITICAL"),
    ),
}


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
    if name in OPTIONS:
        return "select"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if name in ("persona_text", "surface_prompt", "reflection_prompt") or (
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
                    "label": FIELD_META[name][0],
                    "hint": FIELD_META[name][1],
                    "kind": _kind(name, values[name]),
                    "value": values[name],
                    "options": [
                        {"value": value, "label": label}
                        for value, label in OPTIONS.get(name, ())
                    ],
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
            "language": self._s.language,
            "provider": self._s.llm_provider,
            "base_url": self._s.llm_base_url,
            "model": self._s.model_name,
        }

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
                f"{transcriber.model_name} sur {transcriber.device}",
            )
        return _row(
            "Reconnaissance vocale",
            None,
            f"{transcriber.model_name} : modèle non chargé",
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
            return _row("Mémoire", None, "désactivée ou indisponible")
        state = getattr(agent, "memory_ok", None)
        if state is None:
            return _row(
                "Mémoire", None, "prête · aucune lecture depuis le lancement"
            )
        return _row(
            "Mémoire",
            bool(state),
            "dernière lecture : ok"
            if state
            else "dernière lecture : échec (voir la console)",
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
            "settings_provider": self._provider,
            "settings_language": self._language,
        }

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

    async def _provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply OpenRouter/Ollama/custom in one gesture, with the key when one is given."""
        name = str(payload.get("provider") or "").strip().lower()
        if name not in {"custom", "openrouter", "ollama"}:
            raise ActionError("Fournisseur inconnu : custom, openrouter ou ollama.", 400)
        values = {"LLM_PROVIDER": name}
        preset = PROVIDER_PRESETS.get(name)
        if preset is not None:
            values.update(
                {
                    "LLM_BASE_URL": preset["llm_base_url"],
                    "MODEL_NAME": preset["model_name"],
                }
            )
            self._s.llm_base_url = preset["llm_base_url"]
            self._s.model_name = preset["model_name"]
        else:
            base_url = str(payload.get("base_url") or "").strip()
            if not base_url:
                raise ActionError(
                    "URL de base requise pour le fournisseur custom.", 400
                )
            values["LLM_BASE_URL"] = base_url.rstrip("/")
            model = str(payload.get("model") or "").strip()
            if model:
                values["MODEL_NAME"] = model
        key = str(payload.get("key") or "").strip()
        try:
            write_env_settings(values)
            self._s.llm_base_url = values["LLM_BASE_URL"].rstrip("/")
            if "MODEL_NAME" in values:
                self._s.model_name = values["MODEL_NAME"]
            if key:
                write_env_secret("OPENAI_API_KEY", key)
                self._s.openai_api_key = SecretStr(key)
            elif name == "ollama" and not self._s.openai_api_key.get_secret_value():
                write_env_secret("OPENAI_API_KEY", "ollama")
                self._s.openai_api_key = SecretStr("ollama")
        except SettingsError as exc:
            raise ActionError(str(exc), 400) from exc
        self._activity()
        return {
            "restart_required": ["llm_provider", "llm_base_url", "model_name", "openai_api_key"],
            "provider": name,
        }

    async def _language(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Switch the interface and prompts live, writing LANGUAGE to .env so it survives a restart."""
        code = normalize_language(str(payload.get("language") or ""), default="")
        if not code:
            raise ActionError("Langue inconnue : fr ou en.", 400)
        try:
            write_env_settings({"LANGUAGE": code})
        except SettingsError as exc:
            raise ActionError(str(exc), 400) from exc
        self._s.language = code
        self._activity()
        return {"language": code}

    def _activity(self) -> None:
        hub = self._health.hub
        if hub is not None:
            hub.activity("settings")
