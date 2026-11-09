"""Command-line entry point: python -m"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from agent.context import ToolOutputStore
from agent.llm import LLMClient
from agent.loop import Agent
from memory.capture import MemoryCapture
from memory.notes import NoteMemory
from monika.__init__ import __version__
from monika.config import ENV_FILE, Settings, get_settings, settings_load_problems
from monika.paths import Paths
from rag.service import RagError, RagService
from security.audit import AuditLog
from security.masking import Masker, MaskingFilter
from tools.custom_tools import CustomTools, register_custom_tool
from tools.internal import (
    register_document_tools,
    register_memory_tools,
    register_result_reader,
    register_task_tools,
)
from tools.mcp_client import McpConfigError, McpManager, load_mcp_config
from tools.registry import ToolRegistry

console = Console()


def dotenv_for_mcp() -> dict[str, str]:
    """Values of the project's .env, so ${VARIABLE} in mcp_servers.json can use them (e.g. an API key)."""
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}


def setup_logging(settings: Settings) -> Masker:
    """Configure logging with secret masking and return the masker."""
    masker = Masker(settings.secret_values())
    handler = logging.StreamHandler()
    handler.addFilter(MaskingFilter(masker))
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )
    # HTTP libraries log every request at INFO, which clutters the chat
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(
            max(logging.WARNING, logging.getLogger().level)
        )
    return masker


def show_config(settings: Settings, paths: Paths) -> None:
    table = Table(title=f"Configuration de Monika v{__version__}")
    table.add_column("Réglage", style="cyan")
    table.add_column("Valeur")
    for name, value in settings.public_dict().items():
        table.add_row(name.upper(), value)
    console.print(table)
    env_state = "trouvé" if ENV_FILE.exists() else "absent (valeurs par défaut)"
    console.print(f"Fichier .env : {ENV_FILE} — {env_state}")
    console.print(f"Dossier de données : {paths.home}")


def show_phone(settings: Settings) -> int:
    """--telephone : la commande et le lien à ouvrir depuis le téléphone (HTTPS par Tailscale)."""
    from monika.phone import LOCAL_HOSTS, PhoneError, serve_link, tailscale_name

    if not settings.web_enabled:
        console.print(
            "[red]WEB_ENABLED=false[/red] : active la page, sinon il n'y a rien à ouvrir."
        )
        return 1
    token = settings.web_token.get_secret_value()
    if not token:
        console.print(
            "[red]WEB_TOKEN est vide[/red] : sur le tailnet, la page donnerait tes souvenirs, "
            "tes sessions et tes réglages à tout appareil connecté.\n"
            "Génère-le une fois, recopie-le dans .env, puis relance :\n"
            '  [bold]python -c "import secrets; print(secrets.token_urlsafe(32))"[/bold]'
        )
        return 1
    if settings.web_host not in LOCAL_HOSTS:
        console.print(
            f"[yellow]⚠ WEB_HOST={settings.web_host}[/yellow] : `tailscale serve` ne parle qu'à "
            "127.0.0.1. Laisse WEB_HOST=127.0.0.1 (Tailscale termine le HTTPS) et relance."
        )
        return 1
    try:
        name = tailscale_name()
    except PhoneError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    console.print(
        f"[green]✓[/green] Tailscale : {name}, port {settings.web_port}"
    )
    console.print(
        "\n1. Sur cette machine, une fois (Monika lancé) :\n"
        f"   [bold]tailscale serve --bg {settings.web_port}[/bold]\n"
        "2. Sur le téléphone, connecté à ton tailnet, ouvre une fois :\n"
        f"   [bold]{serve_link(name, token)}[/bold]\n"
        "3. Le jeton passe en cookie et l'adresse redevient propre ; le micro de la page"
        " demande HTTPS, c'est justement ce que donne Tailscale.\n"
    )
    console.print(
        "[dim]`tailscale serve status` montre la configuration, `tailscale serve reset` la "
        "coupe. N'utilise pas `tailscale funnel` : ce serait tout l'internet.[/dim]"
    )
    return 0


def show_roles(settings: Settings) -> int:
    """--roles : qui utilise quel modèle, et à quel moment."""
    from agent.roles import roles

    console.print("[bold]Rôles du modèle[/bold] (un réglage vide retombe sur MODEL_NAME)")
    for role in roles(settings):
        state = "[green]actif[/green]" if role.active else "[dim]coupé[/dim]"
        console.print(
            f"\n  [bold]{role.label}[/bold] · {role.model}  "
            f"([dim]{role.chosen_by}[/dim], T={role.temperature:g}) · {state}"
        )
        console.print(f"    {role.when}")
    console.print(
        "\n[dim]La réflexion et l'action ne bloquent jamais une réponse : un modèle moins cher "
        "dans REFLECTION_MODEL se règle sans rien perdre du tour.[/dim]"
    )
    return 0


async def load_memory(settings: Settings, paths: Paths) -> NoteMemory | None:
    """Open the evidence memory (a private JSON file; embeddings load in the background)."""
    if not settings.fast_memory_enabled:
        console.print("[dim]Mémoire désactivée (FAST_MEMORY_ENABLED=false).[/dim]")
        return None
    memory = NoteMemory(paths.memory_notes, settings)
    console.print("[green]✓[/green] Mémoire prête (faits cités, corrections).")
    return memory


def load_rag(
    settings: Settings, paths: Paths, ignore_fingerprint: bool = False
) -> RagService | None:
    """Open the document index (models load lazily), or explain why it is unavailable."""
    if not settings.rag_enabled:
        console.print("[dim]Index de documents désactivé (RAG_ENABLED=false).[/dim]")
        return None
    try:
        return RagService.create(settings, paths, ignore_fingerprint=ignore_fingerprint)
    except RagError as exc:
        console.print(f"[red]{exc}[/red]\nDémarrage sans index de documents.")
        return None


def load_tasks(settings: Settings, paths: Paths, masker: Masker):
    """Open the scheduled tasks (JSON, 0600), or explain why reminders stay off. Never stops the chat."""
    if not settings.tasks_enabled:
        console.print("[dim]Tâches planifiées désactivées (TASKS_ENABLED=false).[/dim]")
        return None
    from agent.tasks import TaskStore

    store = TaskStore(paths.tasks_file, masker, settings.tasks_max)
    tasks = store.all()
    if not tasks:
        console.print("[dim]Aucune tâche planifiée (/rappel pour en créer une).[/dim]")
        return store
    console.print(
        f"[green]✓[/green] Tâches planifiées : {len(tasks)} ; "
        f"prochaine {tasks[0].describe().strip()}"
    )
    late = store.due()
    if late:
        console.print(
            f"[yellow]{len(late)} tâche(s) en retard : déclenchée(s) au démarrage."
        )
    return store


async def run_rag_command(
    settings: Settings, paths: Paths, args: argparse.Namespace
) -> int:
    """--rag-index / --rag-status / --rag-search, run in the foreground."""
    rag = load_rag(settings, paths, ignore_fingerprint=args.reset)
    if rag is None:
        return 1
    try:
        if args.rag_search:
            return await _rag_search(rag, " ".join(args.rag_search))
        if args.rag_index is not None:
            roots = [Path(p).expanduser().resolve() for p in args.rag_index] or None
            console.print(
                "Indexation de : "
                + ", ".join(str(r) for r in (roots or settings.rag_root_paths()))
            )
            console.print(
                "[dim]Le premier lancement télécharge le modèle d'embeddings (environ 1 Go). "
                "Ctrl+C interrompt ; la prochaine fois, seuls les fichiers nouveaux ou modifiés "
                "seront traités.[/dim]"
            )
            rag.start_sync(roots, reset=args.reset)
            last = ""
            while rag.running:
                await asyncio.sleep(1.0)
                p = rag.progress
                line = f"{p.state} : {p.processed}/{p.total} fichiers, {p.chunks} passages — {p.current}"
                if line != last and p.state == "indexation":
                    console.print(f"[dim]{line}[/dim]")
                    last = line
            console.print(rag.progress.summary())
            return 1 if rag.progress.state == "échec" else 0
        console.print(await rag.status_text())
        return 0
    except KeyboardInterrupt:
        await rag.stop_sync()
        console.print("\nIndexation interrompue (reprise possible).")
        return 130
    finally:
        await rag.close()


async def _rag_search(rag: RagService, query: str) -> int:
    start = time.perf_counter()
    try:
        outcome = await rag.search(query)
    except Exception as exc:
        console.print(f"[red]Échec de la recherche :[/red] {type(exc).__name__}: {exc}")
        return 1
    if outcome.index_empty:
        console.print(
            "Aucun document indexé : lance d'abord python -m monika --rag-index"
        )
        return 0
    how = "hybride + reranking" if outcome.reranked else "hybride"
    console.print(
        f"{len(outcome.hits)} passage(s) en {time.perf_counter() - start:.1f} s (recherche {how})"
    )
    if outcome.warning:
        console.print(f"[yellow]{outcome.warning}[/yellow]")
    if outcome.filtered:
        console.print(
            f"[dim]{outcome.filtered} passage(s) écarté(s) sous le seuil RAG_MIN_SCORE="
            f"{outcome.min_score:g} (pour tout voir : RAG_MIN_SCORE=-100).[/dim]"
        )
    for i, hit in enumerate(outcome.hits, start=1):
        console.print(f"\n[bold][{i}] {hit.citation()}[/bold]  (score {hit.score:.2f})")
        console.out(hit.text[:600], highlight=False)
    return 0


@asynccontextmanager
async def open_tools(settings: Settings, paths: Paths, masker: Masker, on_manager=None):
    """Connect the MCP servers (if any) and yield the audited tool registry.

    `on_manager` receives the live McpManager (health grid); nothing else is kept from it.
    """
    registry = ToolRegistry(
        AuditLog(
            paths.audit_log, masker, settings.audit_enabled, settings.audit_max_chars
        )
    )
    # Un outil perso modifié depuis l'accord est désactivé avant tout démarrage
    for notice in CustomTools(paths).enforce():
        console.print(f"[yellow]{notice}[/yellow]")
    try:
        servers = load_mcp_config(paths.mcp_config)
    except McpConfigError as exc:
        console.print(f"[red]{exc}[/red]\nDémarrage sans outils.")
        servers = {}
    if not servers:
        console.print(
            "[dim]Aucun outil : pas de serveur MCP configuré "
            f"({paths.mcp_config}).[/dim]"
        )
        yield registry
        return
    async with McpManager(
        servers,
        registry,
        paths.logs,
        settings.mcp_start_timeout_s,
        settings.mcp_call_timeout_s,
        extra_env=dotenv_for_mcp(),
    ) as manager:
        for st in manager.statuses:
            if not st.ok:
                console.print(f"[red]✗[/red] MCP {st.name} : {st.detail}")
        if on_manager is not None:
            on_manager(manager)
        yield registry


async def start_voice(settings: Settings, paths: Paths):
    """Open the microphone and load the speech models; returns a VoiceListener, or None with a message."""
    from voice.audio import SoundDeviceSource
    from voice.errors import VoiceError
    from voice.listener import VoiceListener
    from voice.stt import Transcriber
    from voice.vad import SileroStreamingVAD

    transcriber = Transcriber(settings, paths)
    probe = SoundDeviceSource(settings.audio_input_device)
    try:  # Fail fast on a missing or wrong microphone, before any model download
        await probe.start()
        await probe.read_frame()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    finally:
        await probe.close()
    console.print(
        f"[dim]Chargement de la reconnaissance vocale (Whisper « {settings.stt_model} » ; "
        "le premier lancement télécharge le modèle)…[/dim]"
    )
    try:
        seconds = await transcriber.load()
        listener = VoiceListener(
            SoundDeviceSource(settings.audio_input_device),
            SileroStreamingVAD(),
            transcriber,
            settings,
        )
        await listener.start()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    except Exception as exc:  # e.g. missing onnxruntime or a broken install
        console.print(
            f"[red]Reconnaissance vocale indisponible : {type(exc).__name__}: {exc}[/red]"
        )
        return None
    console.print(
        f"[green]✓[/green] Reconnaissance vocale prête (modèle chargé en {seconds:.1f} s)."
    )
    return listener


async def start_speech(settings: Settings, paths: Paths, voice: bool, on_level=None):
    """Connect the voice (GPT-SoVITS over its own HTTP API); returns a Speaker, or None with a message."""
    from voice.errors import VoiceError
    from voice.gptsovits import GptSovitsEngine
    from voice.playback import SoundDeviceSink
    from voice.speaker import Speaker

    if not settings.tts_enabled:
        if voice:
            console.print(
                "[dim]Synthèse vocale désactivée (TTS_ENABLED=false) : réponses à l'écran seulement.[/dim]"
            )
        return None
    if not voice and not settings.tts_text_mode:
        return None
    console.print(
        f"[dim]Connexion au serveur GPT-SoVITS ({settings.tts_url}) et chargement de la voix… "
        "au tout premier appel, le modèle met une dizaine de secondes.[/dim]"
    )
    engine = GptSovitsEngine(settings, paths)
    try:
        seconds = await engine.load()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]"
        )
        engine.close()
        return None
    except Exception as exc:  # e.g. an unexpected api_v2 response
        console.print(
            f"[red]Synthèse vocale indisponible : {type(exc).__name__}: {exc}[/red]"
        )
        console.print(
            "[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]"
        )
        engine.close()
        return None
    tags = len(engine.available_tags or ())
    console.print(
        f"[green]✓[/green] Voix prête : « {engine.character_name} », {engine.sample_rate} Hz, "
        f"{tags} tag(s) d'émotion, préchauffée en {seconds:.1f} s."
    )
    for warning in engine.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    sink = SoundDeviceSink(
        engine.sample_rate, settings.audio_output_device, on_level=on_level
    )
    try:  # Fail fast on a missing or wrong output, before the conversation starts
        await sink.open()
        await sink.close()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]"
        )
        engine.close()
        return None
    tagger = _emotion_tagger(settings, engine)
    block_chars = (
        settings.tts_block_max_chars if settings.tts_render_mode() == "block" else 0
    )
    # Read at each gap: the pause settings apply from the page without a restart
    pauses = lambda: (
        settings.tts_pause_style,
        settings.tts_pause_scale,
        settings.tts_pause_max_s,
    )
    return Speaker(
        engine,
        sink,
        tagger=tagger,
        block_chars=block_chars,
        pauses=pauses,
    )


def _emotion_tagger(settings: Settings, engine):
    """Text + mood -> text tagged for the emotion references, or None when the voice must get plain text."""
    if settings.tts_emotion_tags == "off":
        return None
    from character.tts_emotion import TtsEmotionMapper

    mapper = TtsEmotionMapper()
    if (
        settings.tts_emotion_tags == "on"
    ):  # Trust the voice pack: emit every tag of the emotion
        return lambda text, emotion, intensity: mapper.tagged(text, emotion, intensity)
    available = (
        engine.available_tags or frozenset()
    )  # auto: only what the voice pack really has
    if not available:
        console.print(
            "[yellow]⚠ Ce pack de voix n'a aucun enregistrement de référence : "
            "je parle d'une voix neutre (TTS_EMOTION_TAGS=on pour forcer).[/yellow]"
        )
    return lambda text, emotion, intensity: mapper.tagged(
        text, emotion, intensity, available=available
    )


def load_character(settings: Settings, paths: Paths, llm, hub, masker):
    """Emotions and relationship (optional): a problem here must never prevent Monika from starting."""
    if not settings.character_enabled:
        return None
    try:
        from character.service import create_service, llm_complete

        complete = (
            llm_complete(llm, settings.affect_llm_model)
            if settings.affect_llm_enabled
            else None
        )
        journal_on = settings.journal_enabled
        service = create_service(
            paths.character_state,
            complete,
            settings.affect_timeout_s,
            journal_path=paths.character_journal if journal_on else None,
            journal_complete=llm_complete(llm, settings.journal_llm_model, 0.7)
            if journal_on
            else None,
            journal_every=settings.journal_every_turns,
            journal_max_chars=settings.journal_max_chars,
            mask=masker.mask_text,
        )
        if hub is not None:
            service.on_change = hub.set_character
            hub.set_character(service.snapshot())
        return service
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Personnage indisponible ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]"
        )
        return None


def load_words(settings: Settings, paths: Paths):
    """The user's own sensitive-word bank (empty at first). A broken file disables the filter, never a turn."""
    try:
        from security.words import WordBank

        bank = WordBank.load(paths.words_file)
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Mots sensibles ignorés ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]"
        )
        return None
    if bank.empty:
        console.print(
            f"[dim]Aucun mot sensible déclaré ({paths.words_file}) : le filtre ne fait rien "
            "tant que la liste est vide.[/dim]"
        )
    return bank


def load_recipes(settings: Settings, paths: Paths, llm, masker):
    """Recettes apprises (facultatif) : un souci ici ne doit jamais empêcher de démarrer."""
    if not settings.recipes_enabled:
        console.print("[dim]Recettes désactivées (RECIPES_ENABLED=false).[/dim]")
        return None, None
    try:
        from agent.recipes import RecipeKeeper, RecipeStore
        from character.service import llm_complete

        store = RecipeStore(paths.recipes_file, masker.mask_text, settings.recipes_max)
        keeper = RecipeKeeper(
            store,
            llm_complete(llm, settings.recipes_llm_model, 0.4),
            settings.recipes_min_tools,
            mask=masker.mask_text,
        )
        return store, keeper
    except Exception as exc:  # noqa: BLE001 - optional layer
        console.print(
            f"[yellow]Recettes indisponibles ({type(exc).__name__}: {exc}) : "
            "je continue sans.[/yellow]"
        )
        return None, None


def load_sessions(settings: Settings, paths: Paths, masker: Masker, voice: bool):
    """Open the conversation log (SQLite + FTS5), or explain why it stays off. Never stops the chat."""
    if not settings.sessions_enabled:
        console.print(
            "[dim]Sessions enregistrées désactivées (SESSIONS_ENABLED=false).[/dim]"
        )
        return None
    from agent.sessions import SessionRecorder, SessionsError, SessionStore

    try:
        store = SessionStore(paths.sessions_db, masker, settings.sessions_keep_days)
    except SessionsError as exc:
        console.print(f"[yellow]{exc} Je continue sans sessions.[/yellow]")
        return None
    try:
        removed = store.purge()
    except Exception as exc:  # noqa: BLE001 - a purge problem must not prevent the chat
        console.print(
            f"[yellow]Purge des sessions impossible ({type(exc).__name__}: {exc}).[/yellow]"
        )
        removed = 0
    if removed:
        console.print(
            f"[dim]{removed} session(s) de plus de {settings.sessions_keep_days} jour(s) "
            "supprimée(s).[/dim]"
        )
    console.print(f"[green]✓[/green] Sessions enregistrées : {paths.sessions_db}")
    return SessionRecorder(store, mode="voix" if voice else "texte")


def load_nightly(settings: Settings, paths: Paths, masker: Masker, llm, memory, sessions, recipes, character):
    """Veille nocturne (facultative) : note Obsidian, propositions de mémoire, pensées du matin."""
    if not settings.nightly_enabled:
        console.print("[dim]Veille nocturne désactivée (NIGHTLY_ENABLED=false).[/dim]")
        return None, None
    if settings.obsidian_vault is None:
        console.print(
            "[yellow]Veille nocturne ignorée : OBSIDIAN_VAULT n'est pas défini dans .env.[/yellow]"
        )
        return None, None
    if not settings.obsidian_vault.is_dir():
        console.print(
            f"[yellow]Veille nocturne ignorée : le coffre {settings.obsidian_vault} "
            "n'existe pas (OBSIDIAN_VAULT).[/yellow]"
        )
        return None, None
    try:
        from agent.nightly import MorningNotes, NightlyJob

        morning = MorningNotes(paths.morning_notes)
        job = NightlyJob(
            settings,
            paths,
            masker,
            llm=llm,
            memory=memory,
            session_store=sessions.store if sessions is not None else None,
            recipes=recipes,
            character=character,
            morning=morning,
        )
        console.print(
            f"[green]✓[/green] Veille nocturne : {settings.nightly_time} · "
            f"{settings.obsidian_vault}"
        )
        return job, morning
    except Exception as exc:  # noqa: BLE001 - optional layer
        console.print(
            f"[yellow]Veille nocturne indisponible ({type(exc).__name__}: {exc}) : "
            "je continue sans.[/yellow]"
        )
        return None, None


def _stopped_sentence(stopped: list[str]) -> str:
    return "Arrêté : " + ", ".join(stopped) + "." if stopped else "Rien ne tournait."


def load_action_director(settings: Settings, llm, hub):
    """The action role (a short avatar chain after each reply), or None when it is off."""
    if not settings.action_llm_enabled:
        return None
    try:
        from character.action_director import ActionDirector
        from character.service import llm_complete

        return ActionDirector(llm_complete(llm, settings.action_llm_model, 0.7), hub)
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Rôle action indisponible ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]"
        )
        return None


@dataclass
class WebUI:
    """What the page needs, handed from its launcher to the chat that fills it in."""

    hub: Any
    dashboard: Any
    server: Any
    chat: Any
    voice_targets: dict[str, Any] = field(default_factory=dict)


async def start_web(
    settings: Settings, paths: Paths, voice: bool, health=None
) -> WebUI | None:
    """Start the local page (avatar, dashboard, conversation, microphone); None with a message."""
    if not settings.web_enabled:
        return None
    from web.chat import ChatView
    from web.dashboard import Dashboard
    from web.hub import Hub
    from web.server import WebError, WebServer
    from web.settings import Health, SettingsApi
    from web.voice import PageVoice

    hub = Hub()
    chat = ChatView(hub)
    health = health if health is not None else Health()
    health.hub = hub
    dashboard = Dashboard(
        settings, paths, hub, mode="voix" if voice else "texte", chat=chat
    )
    # One PageVoice per open page: the launcher fills the targets once the session exists
    voice_targets: dict[str, Any] = {}
    factory = lambda send_json, send_bytes: PageVoice(
        settings, paths, hub, send_json, send_bytes, voice_targets
    )
    server = WebServer(
        hub,
        dashboard.snapshot,
        settings.web_port,
        actions=dashboard.actions(),
        settings_api=SettingsApi(settings, paths, health),
        chat=chat,
        voice_factory=factory,
        host=settings.web_host,
        token=settings.web_token.get_secret_value(),
    )
    try:
        await server.start()
    except WebError as exc:
        console.print(f"[yellow]{exc} Je continue sans la page web.[/yellow]")
        return None
    if settings.web_token.get_secret_value():
        scope = "jeton demandé"
    elif settings.web_host in ("127.0.0.1", "localhost", "::1"):
        scope = "sans jeton, visible depuis cet ordinateur seulement"
    else:
        scope = "SANS JETON : toute machine qui atteint ce port peut tout lire et tout modifier"
    console.print(
        f"[green]✓[/green] Page de Monika (avatar, tableau de bord et conversation) : "
        f"{server.url} [dim]({scope})[/dim]"
    )
    if not settings.web_token.get_secret_value() and settings.web_host not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        console.print(
            "[yellow]⚠ WEB_HOST n'est pas local : définis WEB_TOKEN, sinon la page est "
            "ouverte à qui atteint ce port.[/yellow]"
        )
    if settings.web_token.get_secret_value():
        console.print(
            "[dim]Depuis le téléphone : python -m monika --telephone (Tailscale, HTTPS).[/dim]"
        )
    return WebUI(hub, dashboard, server, chat, voice_targets)


async def run_chat(
    settings: Settings, paths: Paths, masker: Masker, voice: bool = False
) -> int:
    """One chat per data folder: the instance lock goes before memory and documents open."""
    from monika.lock import InstanceLock, LockError

    lock = InstanceLock(paths.instance_lock)
    try:
        lock.acquire()
    except LockError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    try:
        return await _run_chat(settings, paths, masker, voice=voice)
    finally:
        lock.release()


async def _run_chat(
    settings: Settings, paths: Paths, masker: Masker, voice: bool = False
) -> int:
    """Start Monika with tools, memory and documents: the page, or the microphone in voice mode.

    Nothing is read from the terminal: the page sends its messages through the same session, and
    this process keeps everything running until it is stopped.
    """
    from agent.reflection import BetweenTurns
    from agent.surface import SurfaceReflex
    from modes.text import TextSession
    from web.settings import Health

    health = Health()
    listener = None
    if voice:
        listener = await start_voice(settings, paths)
        if listener is None:
            return 1
    console.print(
        f"[dim]Modèle {settings.model_name} via {settings.llm_base_url}[/dim]"
    )
    store = ToolOutputStore(paths.tool_results, settings.tool_result_max_chars)
    store.purge(settings.tool_results_keep_days)
    sessions = load_sessions(settings, paths, masker, voice)
    tasks = load_tasks(settings, paths, masker)
    memory = await load_memory(settings, paths)
    capture = None
    if memory and settings.memory_capture_enabled:
        capture = MemoryCapture([memory.capture], settings.memory_capture_min_chars)
    rag = load_rag(settings, paths)
    web = await start_web(settings, paths, voice, health)
    if web is None and not voice:
        console.print(
            "[red]La page web est indispensable[/red] : sans conversation au terminal, "
            "c'est la seule façon de me parler. Active WEB_ENABLED (et vérifie WEB_PORT), "
            "puis relance."
        )
        return 1
    hub = web.hub if web else None
    chat = web.chat if web else None
    speaker = await start_speech(
        settings, paths, voice, on_level=hub.on_level if hub else None
    )
    health.listener = listener
    health.speaker = speaker
    scheduler = None
    nightly = None
    briefing = None
    custom = CustomTools(paths)
    try:
        async with open_tools(
            settings, paths, masker, lambda manager: setattr(health, "mcp", manager)
        ) as registry:
            if memory:
                register_memory_tools(registry, memory)
            if tasks:
                register_task_tools(registry, tasks)
            if rag:
                register_document_tools(registry, rag)
                if rag._store.manifest.counts()["indexed"] == 0:
                    console.print(
                        "[dim]Aucun document indexé : /indexer pour commencer.[/dim]"
                    )
            register_custom_tool(registry, custom)
            if registry.tools():  # Without other tools no result is ever truncated
                register_result_reader(registry, store)
            llm = LLMClient(settings)
            health.llm = llm
            if memory is not None:
                memory.bind_llm(llm)  # le directeur extrait les faits ; il lui faut le modèle
            recipes, recipe_keeper = load_recipes(settings, paths, llm, masker)
            character = load_character(settings, paths, llm, hub, masker)
            action_director = load_action_director(settings, llm, hub)
            reflection = BetweenTurns(settings, llm) if settings.reflection_enabled else None
            words = load_words(settings, paths)
            nightly, morning = load_nightly(
                settings, paths, masker, llm, memory, sessions, recipes, character
            )
            agent = Agent(
                settings,
                llm,
                registry,
                store,
                memory=memory,
                capture=capture,
                rag=rag,
                voice=voice,
                character=character,
                words=words,
                sessions=sessions,
                recipes=recipes,
                recipe_keeper=recipe_keeper,
                morning=morning,
                surface=SurfaceReflex(settings, llm),
                reflection=reflection,
            )
            health.agent = agent
            if web:
                web.dashboard.agent = agent
            session_kwargs = {
                "masker": masker,
                "speaker": speaker,
                "speech_max_chars": settings.tts_max_sentence_chars,
                "hub": hub,
                "action_director": action_director,
                "tasks": tasks,
                "custom_tools": custom,
                "recipes": recipes,
                "nightly": nightly,
                "chat": chat,
            }
            if listener is not None:
                from modes.voice import BANNER, BANNER_SPOKEN, VoiceReader

                reader = VoiceReader(
                    listener,
                    lambda text: (sys.stdout.write(text), sys.stdout.flush()),
                    speaker,
                    hub=hub,
                )
                session = TextSession(
                    agent,
                    reader,
                    banner=BANNER_SPOKEN if speaker else BANNER,
                    **session_kwargs,
                )
            else:

                async def no_input() -> str:
                    # Rien n'est lu au terminal : la page envoie ses messages par run_line.
                    raise EOFError

                session = TextSession(agent, no_input, **session_kwargs)
            if reflection is not None:

                async def speak_reflection(intent: str) -> None:
                    await session.run_task(
                        "[Consigne interne de Monika, ne vient pas de l'utilisateur] Prends la "
                        "parole de ta propre initiative, brièvement et naturellement, sur ce "
                        "point préparé par ta réflexion : " + intent,
                        "Réflexion",
                    )

                reflection.bind_speak(speak_reflection)
            if (
                web is not None
            ):  # The page sends its messages through the same session as the terminal
                page_turn: dict[str, asyncio.Task | None] = {"task": None}

                async def submit(text: str) -> bool:
                    task = page_turn["task"]
                    if task is not None and not task.done():
                        return False
                    page_turn["task"] = asyncio.create_task(session.run_line(text))
                    await asyncio.sleep(0)
                    return True

                async def stop() -> str:
                    task = page_turn["task"]
                    running = task is not None and not task.done()
                    if running:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    return _stopped_sentence(await session.stop_all(turn=running))

                web.dashboard.chat_send, web.dashboard.chat_stop = submit, stop

                async def ask(
                    text: str,
                ) -> None:  # The page's microphone: one whole turn, then silence
                    await session.reply(text)

                web.voice_targets.update(speaker=speaker, ask=ask)
            if (
                tasks is not None
            ):  # Before the chat starts: a missed reminder fires right away
                from agent.tasks import Scheduler

                scheduler = Scheduler(tasks, session.run_task)
                scheduler.start()
            if settings.briefing_enabled:  # Après la session : le briefing parle par elle
                from agent.briefing import BriefingJob

                briefing = BriefingJob(
                    settings,
                    paths,
                    masker,
                    session.run_task,
                    tasks=tasks,
                )
                session.set_briefing(briefing)
                briefing.start()
                console.print(
                    f"[green]✓[/green] Briefing du matin : {settings.briefing_time} · "
                    + (
                        f"météo {settings.briefing_city}"
                        if settings.briefing_city
                        else "sans météo (BRIEFING_CITY vide)"
                    )
                )
            else:
                console.print(
                    "[dim]Briefing du matin désactivé (BRIEFING_ENABLED=false).[/dim]"
                )
            if nightly is not None:
                nightly.start()
            if listener is not None:
                await session.run()
            else:  # Sans micro, la page est la seule entrée : on la laisse vivre
                try:
                    await asyncio.Event().wait()
                finally:
                    await session.close()
    finally:
        if scheduler is not None:
            await scheduler.stop()
        if briefing is not None:
            await briefing.stop()
        if nightly is not None:
            await nightly.stop()
        if web:
            await web.server.stop()
        if speaker is not None:
            await speaker.close()
            speaker.engine.close()
        if listener is not None:
            await listener.close()
        if rag:
            await rag.close()
        if memory:
            await memory.close()
        if sessions is not None:
            sessions.close()
    return 0


async def run_tools_command(
    settings: Settings, paths: Paths, masker: Masker, call: list[str] | None
) -> int:
    """List MCP servers and tools, and optionally call one tool (for checking a setup)."""
    try:
        servers = load_mcp_config(paths.mcp_config)
    except McpConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    if not servers:
        console.print(
            f"Aucun serveur MCP configuré. Crée {paths.mcp_config} "
            "(modèle : mcp_servers.example.json à la racine du projet)."
        )
        return 0
    audit = AuditLog(
        paths.audit_log, masker, settings.audit_enabled, settings.audit_max_chars
    )
    registry = ToolRegistry(audit)
    for notice in CustomTools(paths).enforce():
        console.print(f"[yellow]{notice}[/yellow]")
    async with McpManager(
        servers,
        registry,
        paths.logs,
        settings.mcp_start_timeout_s,
        settings.mcp_call_timeout_s,
        extra_env=dotenv_for_mcp(),
    ) as manager:
        table = Table(title="Serveurs MCP")
        for col in ("Serveur", "État", "Détail"):
            table.add_column(col)
        for st in manager.statuses:
            table.add_row(
                st.name,
                "[green]connecté[/green]" if st.ok else "[red]indisponible[/red]",
                st.detail,
            )
        console.print(table)
        tools = Table(title="Outils")
        for col in ("Outil", "Source", "Lent", "Description"):
            tools.add_column(col)
        for t in registry.tools():
            tools.add_row(
                t.name,
                t.source,
                "oui" if t.slow else "",
                t.description.splitlines()[0][:70],
            )
        console.print(tools)
        if call:
            result = await registry.call(call[0], call[1] if len(call) > 1 else None)
            state = "[red]ERREUR[/red]" if result.is_error else "[green]OK[/green]"
            console.print(f"{state} ({result.duration_ms:.0f} ms) :")
            console.out(result.text, highlight=False)
            return 1 if result.is_error else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monika", description="Monika, assistante IA personnelle locale."
    )
    parser.add_argument(
        "--mode",
        choices=["voix"],
        help="Conversation à voix haute (par défaut : la page web seulement).",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Affiche la configuration chargée (secrets masqués) et quitte.",
    )
    parser.add_argument(
        "--rag-index",
        nargs="*",
        metavar="DOSSIER",
        help="Indexe tes documents (dossiers de RAG_ROOTS, ou ceux donnés) et quitte.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Avec --rag-index : repart d'un index vide (après un changement de modèle).",
    )
    parser.add_argument(
        "--rag-search",
        nargs="+",
        metavar="MOT",
        help="Cherche dans l'index de documents et quitte.",
    )
    parser.add_argument(
        "--rag-status",
        action="store_true",
        help="Affiche l'état de l'index de documents et quitte.",
    )
    parser.add_argument(
        "--roles",
        action="store_true",
        help="Affiche les rôles du modèle (surface, deep, réflexion, action), puis quitte.",
    )
    parser.add_argument(
        "--telephone",
        action="store_true",
        help="Affiche la commande `tailscale serve` et le lien HTTPS de la page, puis quitte.",
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        help="Liste les serveurs MCP et leurs outils, puis quitte.",
    )
    parser.add_argument(
        "--call",
        nargs="+",
        metavar=("OUTIL", "JSON"),
        help='Appelle un outil, ex. --call fichiers__read_file \'{"path": "/tmp/a"}\'.',
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except Exception as exc:  # Invalid .env values: report them in French and stop
        console.print(f"[red]Configuration invalide :[/red] {exc}")
        return 1
    for problem in (
        settings_load_problems()
    ):  # A settings.json the page wrote (or a hand edit) was unusable
        console.print(f"[yellow]⚠ {problem}[/yellow]")
    paths = Paths.from_settings(settings)
    paths.ensure()
    masker = setup_logging(settings)

    if args.config:
        show_config(settings, paths)
        return 0
    if args.telephone:
        return show_phone(settings)
    if args.roles:
        return show_roles(settings)
    if args.rag_index is not None or args.rag_search or args.rag_status:
        try:
            return asyncio.run(run_rag_command(settings, paths, args))
        except KeyboardInterrupt:
            return 130
    if args.tools or args.call:
        return asyncio.run(run_tools_command(settings, paths, masker, args.call))
    try:
        return asyncio.run(
            run_chat(settings, paths, masker, voice=(args.mode == "voix"))
        )
    except KeyboardInterrupt:
        console.print("\nÀ bientôt.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
