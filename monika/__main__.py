"""Command-line entry point: python -m """

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from monika.__init__ import __version__
from agent.context import ToolOutputStore
from agent.llm import LLMClient
from agent.loop import Agent
from monika.config import ENV_FILE, Settings, get_settings, settings_load_problems
from monika.paths import Paths
from security.audit import AuditLog
from security.masking import Masker, MaskingFilter
from tools.mcp_client import McpConfigError, McpManager, load_mcp_config
from memory.capture import MemoryCapture
from memory.fast import FastMemory, MemoryUnavailable
from rag.service import RagError, RagService
from tools.internal import (register_document_tools, register_memory_tools,
                                   register_result_reader, register_task_tools)
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
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))
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
    console.print(f"Socket du noyau : {paths.kernel_socket}")


async def load_fast_memory(settings: Settings, paths: Paths) -> FastMemory | None:
    """Start the fast memory, or explain clearly why it is unavailable."""
    if not settings.fast_memory_enabled:
        console.print("[dim]Mémoire rapide désactivée (FAST_MEMORY_ENABLED=false).[/dim]")
        return None
    console.print("[dim]Chargement de la mémoire (le premier lancement télécharge le modèle "
                  "d'embeddings, environ 1 Go)…[/dim]")
    try:
        memory = await asyncio.to_thread(FastMemory.create, settings, paths)
    except MemoryUnavailable as exc:
        console.print(f"[red]{exc}[/red]\nDémarrage sans mémoire rapide.")
        return None
    console.print("[green]✓[/green] Mémoire rapide prête.")
    return memory


def load_rag(settings: Settings, paths: Paths, ignore_fingerprint: bool = False) -> RagService | None:
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
    console.print(f"[green]✓[/green] Tâches planifiées : {len(tasks)} ; "
                  f"prochaine {tasks[0].describe().strip()}")
    late = store.due()
    if late:
        console.print(f"[yellow]{len(late)} tâche(s) en retard : déclenchée(s) au démarrage.")
    return store


async def run_rag_command(settings: Settings, paths: Paths, args: argparse.Namespace) -> int:
    """--rag-index / --rag-status / --rag-search, run in the foreground."""
    rag = load_rag(settings, paths, ignore_fingerprint=args.reset)
    if rag is None:
        return 1
    try:
        if args.rag_search:
            return await _rag_search(rag, " ".join(args.rag_search))
        if args.rag_index is not None:
            roots = [Path(p).expanduser().resolve() for p in args.rag_index] or None
            console.print("Indexation de : " + ", ".join(str(r) for r in (roots or settings.rag_root_paths())))
            console.print("[dim]Le premier lancement télécharge le modèle d'embeddings (environ 1 Go). "
                          "Ctrl+C interrompt ; la prochaine fois, seuls les fichiers nouveaux ou modifiés "
                          "seront traités.[/dim]")
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
        console.print("Aucun document indexé : lance d'abord python -m monika --rag-index")
        return 0
    how = "hybride + reranking" if outcome.reranked else "hybride"
    console.print(f"{len(outcome.hits)} passage(s) en {time.perf_counter() - start:.1f} s (recherche {how})")
    if outcome.warning:
        console.print(f"[yellow]{outcome.warning}[/yellow]")
    if outcome.filtered:
        console.print(f"[dim]{outcome.filtered} passage(s) écarté(s) sous le seuil RAG_MIN_SCORE="
                      f"{outcome.min_score:g} (pour tout voir : RAG_MIN_SCORE=-100).[/dim]")
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
        AuditLog(paths.audit_log, masker, settings.audit_enabled, settings.audit_max_chars))
    try:
        servers = load_mcp_config(paths.mcp_config)
    except McpConfigError as exc:
        console.print(f"[red]{exc}[/red]\nDémarrage sans outils.")
        servers = {}
    if not servers:
        console.print("[dim]Aucun outil : pas de serveur MCP configuré "
                      f"({paths.mcp_config}).[/dim]")
        yield registry
        return
    async with McpManager(servers, registry, paths.logs, settings.mcp_start_timeout_s,
                          settings.mcp_call_timeout_s, extra_env=dotenv_for_mcp()) as manager:
        for st in manager.statuses:
            if not st.ok:
                console.print(f"[red]✗[/red] MCP {st.name} : {st.detail}")
        if on_manager is not None:
            on_manager(manager)
        yield registry


async def start_voice(settings: Settings, paths: Paths):
    """Open the microphone and load the speech models; returns a VoiceListener, or None with a message."""
    from voice.audio import ParecSource
    from voice.errors import VoiceError
    from voice.listener import VoiceListener
    from voice.stt import Transcriber
    from voice.vad import SileroStreamingVAD

    transcriber = Transcriber(settings, paths)
    probe = ParecSource(settings.audio_input_device)
    try:  # Fail fast on a missing or wrong microphone, before any model download
        await probe.start()
        await probe.read_frame()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    finally:
        await probe.close()
    console.print(f"[dim]Chargement de la reconnaissance vocale (Whisper « {settings.stt_model} » ; "
                  "le premier lancement télécharge le modèle)…[/dim]")
    try:
        seconds = await transcriber.load()
        listener = VoiceListener(ParecSource(settings.audio_input_device), SileroStreamingVAD(),
                                 transcriber, settings)
        await listener.start()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    except Exception as exc:  # e.g. missing onnxruntime or a broken install
        console.print(f"[red]Reconnaissance vocale indisponible : {type(exc).__name__}: {exc}[/red]")
        return None
    console.print(f"[green]✓[/green] Reconnaissance vocale prête (modèle chargé en {seconds:.1f} s).")
    return listener


async def start_speech(settings: Settings, paths: Paths, voice: bool, on_level=None):
    """Connect the voice (GPT-SoVITS over its own HTTP API); returns a Speaker, or None with a message."""
    import shutil

    from voice.errors import VoiceError
    from voice.gptsovits import GptSovitsEngine
    from voice.playback import PacatSink
    from voice.speaker import Speaker

    if not settings.tts_enabled:
        if voice:
            console.print("[dim]Synthèse vocale désactivée (TTS_ENABLED=false) : réponses à l'écran seulement.[/dim]")
        return None
    if not voice and not settings.tts_text_mode:
        return None
    if shutil.which("pacat") is None:  # Checked first: nothing else is worth doing without a sound output
        console.print("[red]pacat est introuvable[/red] : installe libpulse (sudo pacman -S libpulse). "
                      "[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]")
        return None
    console.print(f"[dim]Connexion au serveur GPT-SoVITS ({settings.tts_url}) et chargement de la voix… "
                  "au tout premier appel, le modèle met une dizaine de secondes.[/dim]")
    engine = GptSovitsEngine(settings, paths)
    try:
        seconds = await engine.load()
    except VoiceError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]")
        engine.close()
        return None
    except Exception as exc:  # e.g. an unexpected api_v2 response
        console.print(f"[red]Synthèse vocale indisponible : {type(exc).__name__}: {exc}[/red]")
        console.print("[yellow]Je continue sans voix : mes réponses s'afficheront à l'écran seulement.[/yellow]")
        engine.close()
        return None
    tags = len(engine.available_tags or ())
    console.print(f"[green]✓[/green] Voix prête : « {engine.character_name} », {engine.sample_rate} Hz, "
                  f"{tags} tag(s) d'émotion, préchauffée en {seconds:.1f} s.")
    for warning in engine.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    tagger = _emotion_tagger(settings, engine)
    block_chars = settings.tts_block_max_chars if settings.tts_render_mode() == "block" else 0
    return Speaker(engine, PacatSink(engine.sample_rate, settings.audio_output_device, on_level=on_level),
                   tagger=tagger, block_chars=block_chars)


def _emotion_tagger(settings: Settings, engine):
    """Text + mood -> text tagged for the emotion references, or None when the voice must get plain text."""
    if settings.tts_emotion_tags == "off":
        return None
    from character.tts_emotion import TtsEmotionMapper

    mapper = TtsEmotionMapper()
    if settings.tts_emotion_tags == "on":  # Trust the voice pack: emit every tag of the emotion
        return lambda text, emotion, intensity: mapper.tagged(text, emotion, intensity)
    available = engine.available_tags or frozenset()  # auto: only what the voice pack really has
    if not available:
        console.print("[yellow]⚠ Ce pack de voix n'a aucun enregistrement de référence : "
                      "je parle d'une voix neutre (TTS_EMOTION_TAGS=on pour forcer).[/yellow]")
    return lambda text, emotion, intensity: mapper.tagged(text, emotion, intensity, available=available)


def load_character(settings: Settings, paths: Paths, llm, hub, masker):
    """Emotions and relationship (optional): a problem here must never prevent Monika from starting."""
    if not settings.character_enabled:
        return None
    try:
        from character.service import create_service, llm_complete

        complete = llm_complete(llm, settings.affect_llm_model) if settings.affect_llm_enabled else None
        journal_on = settings.journal_enabled
        service = create_service(
            paths.character_state, complete, settings.affect_timeout_s,
            journal_path=paths.character_journal if journal_on else None,
            journal_complete=llm_complete(llm, settings.journal_llm_model, 0.7) if journal_on else None,
            journal_every=settings.journal_every_turns, journal_max_chars=settings.journal_max_chars,
            mask=masker.mask_text)
        if hub is not None:
            service.on_change = hub.set_character
            hub.set_character(service.snapshot())
        return service
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Personnage indisponible ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]")
        return None


def load_words(settings: Settings, paths: Paths):
    """The user's own sensitive-word bank (empty at first). A broken file disables the filter, never a turn."""
    try:
        from security.words import WordBank

        bank = WordBank.load(paths.words_file)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Mots sensibles ignorés ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]")
        return None
    if bank.empty:
        console.print(f"[dim]Aucun mot sensible déclaré ({paths.words_file}) : le filtre ne fait rien "
                      "tant que la liste est vide.[/dim]")
    return bank


def load_sessions(settings: Settings, paths: Paths, masker: Masker, voice: bool):
    """Open the conversation log (SQLite + FTS5), or explain why it stays off. Never stops the chat."""
    if not settings.sessions_enabled:
        console.print("[dim]Sessions enregistrées désactivées (SESSIONS_ENABLED=false).[/dim]")
        return None
    from agent.sessions import SessionRecorder, SessionStore, SessionsError

    try:
        store = SessionStore(paths.sessions_db, masker, settings.sessions_keep_days)
    except SessionsError as exc:
        console.print(f"[yellow]{exc} Je continue sans sessions.[/yellow]")
        return None
    try:
        removed = store.purge()
    except Exception as exc:  # noqa: BLE001 - a purge problem must not prevent the chat
        console.print(f"[yellow]Purge des sessions impossible ({type(exc).__name__}: {exc}).[/yellow]")
        removed = 0
    if removed:
        console.print(f"[dim]{removed} session(s) de plus de {settings.sessions_keep_days} jour(s) "
                      "supprimée(s).[/dim]")
    console.print(f"[green]✓[/green] Sessions enregistrées : {paths.sessions_db}")
    return SessionRecorder(store, mode="voix" if voice else "texte")


def load_action_director(settings: Settings, llm, hub):
    """The action role (a short avatar chain after each reply), or None when it is off."""
    if not settings.action_llm_enabled:
        return None
    try:
        from character.action_director import ActionDirector
        from character.service import llm_complete

        return ActionDirector(llm_complete(llm, settings.action_llm_model, 0.7), hub)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Rôle action indisponible ({type(exc).__name__}: {exc}) : je continue sans.[/yellow]")
        return None


async def start_web(settings: Settings, paths: Paths, voice: bool, health=None):
    """Start the local page (avatar and dashboard); returns (hub, dashboard, server) or None with a message."""
    if not settings.web_enabled:
        return None
    from web.dashboard import Dashboard
    from web.hub import Hub
    from web.server import WebError, WebServer
    from web.settings import Health, SettingsApi

    hub = Hub()
    health = health if health is not None else Health()
    health.hub = hub
    dashboard = Dashboard(settings, paths, hub, mode="voix" if voice else "texte")
    server = WebServer(hub, dashboard.snapshot, settings.web_port, actions=dashboard.actions(),
                       settings_api=SettingsApi(settings, paths, health))
    try:
        await server.start()
    except WebError as exc:
        console.print(f"[yellow]{exc} Je continue sans la page web.[/yellow]")
        return None
    console.print(f"[green]✓[/green] Page de Monika (avatar et tableau de bord) : {server.url} "
                  "[dim](visible depuis cet ordinateur seulement)[/dim]")
    return hub, dashboard, server


async def run_chat(settings: Settings, paths: Paths, masker: Masker, voice: bool = False,
                   server=None) -> int:
    """One chat per data folder: the instance lock goes before memory and documents open."""
    from monika.kernel import InstanceLock, KernelError

    lock = InstanceLock(paths.kernel_lock)
    try:
        lock.acquire()
    except KernelError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    try:
        return await _run_chat(settings, paths, masker, voice=voice, server=server)
    finally:
        lock.release()


async def _run_chat(settings: Settings, paths: Paths, masker: Masker, voice: bool = False,
                    server=None) -> int:
    """Start the chat: keyboard (text mode) or microphone (voice mode), with tools, memory and documents.

    With `server` (the kernel), stdin is not the input any more: the socket is, and this function
    keeps everything running until the kernel stops.
    """
    from modes.text import TextSession
    from web.settings import Health

    health = Health()
    listener = None
    if voice:
        listener = await start_voice(settings, paths)
        if listener is None:
            return 1
    console.print(f"[dim]Modèle {settings.model_name} via {settings.llm_base_url}[/dim]")
    store = ToolOutputStore(paths.tool_results, settings.tool_result_max_chars)
    store.purge(settings.tool_results_keep_days)
    sessions = load_sessions(settings, paths, masker, voice)
    tasks = load_tasks(settings, paths, masker)
    memory = await load_fast_memory(settings, paths)
    capture = None
    if memory and settings.memory_capture_enabled:
        capture = MemoryCapture([memory.capture], settings.memory_capture_min_chars)
    rag = load_rag(settings, paths)
    web = await start_web(settings, paths, voice, health)
    hub = web[0] if web else None
    speaker = await start_speech(settings, paths, voice, on_level=hub.on_level if hub else None)
    health.listener = listener
    health.speaker = speaker
    scheduler = None
    try:
        async with open_tools(settings, paths, masker, lambda manager: setattr(health, "mcp", manager)) \
                as registry:
            if memory:
                register_memory_tools(registry, memory)
            if tasks:
                register_task_tools(registry, tasks)
            if rag:
                register_document_tools(registry, rag)
                if rag._store.manifest.counts()["indexed"] == 0:
                    console.print("[dim]Aucun document indexé : /indexer pour commencer.[/dim]")
            if registry.tools():  # Without other tools no result is ever truncated
                register_result_reader(registry, store)
            llm = LLMClient(settings)
            health.llm = llm
            character = load_character(settings, paths, llm, hub, masker)
            action_director = load_action_director(settings, llm, hub)
            words = load_words(settings, paths)
            agent = Agent(settings, llm, registry, store, memory=memory,
                          capture=capture, rag=rag, voice=voice, character=character, words=words,
                          sessions=sessions)
            health.agent = agent
            if web:
                web[1].agent = agent
            session_kwargs = dict(masker=masker, speaker=speaker,
                                  speech_max_chars=settings.tts_max_sentence_chars, hub=hub,
                                  action_director=action_director, tasks=tasks)
            if listener is not None:
                from modes.voice import BANNER, BANNER_SPOKEN, VoiceReader

                reader = VoiceReader(listener, lambda text: (sys.stdout.write(text), sys.stdout.flush()), speaker,
                                     hub=hub)
                session = TextSession(agent, reader, banner=BANNER_SPOKEN if speaker else BANNER,
                                      **session_kwargs)
            elif server is not None:
                async def no_input() -> str:  # The kernel never reads stdin: clients send lines
                    raise EOFError

                session = TextSession(agent, no_input, **session_kwargs)
            else:
                from prompt_toolkit import PromptSession

                prompt = PromptSession()

                async def read_line() -> str:
                    return await prompt.prompt_async("Toi › ")

                session = TextSession(agent, read_line, **session_kwargs)
            if tasks is not None:  # Before the chat starts: a missed reminder fires right away
                from agent.tasks import Scheduler

                scheduler = Scheduler(tasks, server.fire_task if server is not None else session.run_task)
                scheduler.start()
            if server is not None:
                try:
                    await server.serve(session=session, hub=hub, speaker=speaker,
                                       voice_factory=lambda: start_voice(settings, paths))
                finally:
                    if scheduler is not None:
                        await scheduler.stop()
                    await session.close()
            elif listener is not None:
                await session.run()
            else:
                from prompt_toolkit.patch_stdout import patch_stdout

                with patch_stdout():
                    await session.run()
    finally:
        if scheduler is not None:
            await scheduler.stop()
        if web:
            await web[2].stop()
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


async def run_tools_command(settings: Settings, paths: Paths, masker: Masker,
                            call: list[str] | None) -> int:
    """List MCP servers and tools, and optionally call one tool (for checking a setup)."""
    try:
        servers = load_mcp_config(paths.mcp_config)
    except McpConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    if not servers:
        console.print(f"Aucun serveur MCP configuré. Crée {paths.mcp_config} "
                      "(modèle : mcp_servers.example.json à la racine du projet).")
        return 0
    audit = AuditLog(paths.audit_log, masker, settings.audit_enabled, settings.audit_max_chars)
    registry = ToolRegistry(audit)
    async with McpManager(servers, registry, paths.logs, settings.mcp_start_timeout_s,
                          settings.mcp_call_timeout_s, extra_env=dotenv_for_mcp()) as manager:
        table = Table(title="Serveurs MCP")
        for col in ("Serveur", "État", "Détail"):
            table.add_column(col)
        for st in manager.statuses:
            table.add_row(st.name, "[green]connecté[/green]" if st.ok else "[red]indisponible[/red]",
                          st.detail)
        console.print(table)
        tools = Table(title="Outils")
        for col in ("Outil", "Source", "Lent", "Description"):
            tools.add_column(col)
        for t in registry.tools():
            tools.add_row(t.name, t.source, "oui" if t.slow else "", t.description.splitlines()[0][:70])
        console.print(tools)
        if call:
            result = await registry.call(call[0], call[1] if len(call) > 1 else None)
            state = "[red]ERREUR[/red]" if result.is_error else "[green]OK[/green]"
            console.print(f"{state} ({result.duration_ms:.0f} ms) :")
            console.out(result.text, highlight=False)
            return 1 if result.is_error else 0
    return 0


def attach_console(paths: Paths, voice: bool) -> int:
    """Hand this terminal to the kernel (Ctrl+C while waiting for a reply is handled by the client)."""
    from monika.client import attach
    from monika.kernel import KernelError

    try:
        return asyncio.run(attach(paths.kernel_socket, voice=voice))
    except KernelError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except KeyboardInterrupt:
        return 130


def run_kernel_command(settings: Settings, paths: Paths, masker: Masker,
                       args: argparse.Namespace) -> int:
    """--serve (the kernel, in the foreground) and --attach (this terminal as its client)."""
    if args.serve and args.attach:
        console.print("[red]--serve et --attach sont exclusifs.[/red]")
        return 1
    if not settings.kernel_enabled:
        console.print("[red]Le noyau est désactivé[/red] (KERNEL_ENABLED=false). Active-le dans "
                      "/settings ou dans .env puis relance. Sans noyau, `python -m monika` reste le "
                      "mode local d'avant.")
        return 1
    if args.attach:
        return attach_console(paths, args.mode == "voix")
    from monika.kernel import KernelError, KernelServer

    try:
        return asyncio.run(run_chat(settings, paths, masker, voice=False,
                                    server=KernelServer(paths.kernel_socket)))
    except KernelError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except KeyboardInterrupt:  # Ctrl+C on the kernel itself: stop, and release the socket
        console.print("\nNoyau arrêté.")
        return 0


def choose_mode() -> str | None:
    """Ask which mode to start; return None if the user gives up."""
    try:
        answer = console.input("Mode ? [bold]t[/bold]exte / [bold]v[/bold]oix (Entrée = texte) : ")
    except (EOFError, KeyboardInterrupt):
        return None
    return "voix" if answer.strip().lower().startswith("v") else "texte"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monika", description="Monika, assistante IA personnelle locale."
    )
    parser.add_argument("--mode", choices=["texte", "voix"],
                        help="Mode d'interaction (sinon demandé au lancement).")
    parser.add_argument("--config", action="store_true",
                        help="Affiche la configuration chargée (secrets masqués) et quitte.")
    parser.add_argument("--rag-index", nargs="*", metavar="DOSSIER",
                        help="Indexe tes documents (dossiers de RAG_ROOTS, ou ceux donnés) et quitte.")
    parser.add_argument("--reset", action="store_true",
                        help="Avec --rag-index : repart d'un index vide (après un changement de modèle).")
    parser.add_argument("--rag-search", nargs="+", metavar="MOT",
                        help="Cherche dans l'index de documents et quitte.")
    parser.add_argument("--rag-status", action="store_true",
                        help="Affiche l'état de l'index de documents et quitte.")
    parser.add_argument("--tools", action="store_true",
                        help="Liste les serveurs MCP et leurs outils, puis quitte.")
    parser.add_argument("--serve", action="store_true",
                        help="Lance le noyau persistant (mémoire, outils, page, voix) et le laisse tourner.")
    parser.add_argument("--attach", action="store_true",
                        help="Se connecte au noyau en cours : le terminal devient un client.")
    parser.add_argument("--call", nargs="+", metavar=("OUTIL", "JSON"),
                        help="Appelle un outil, ex. --call fichiers__read_file '{\"path\": \"/tmp/a\"}'.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except Exception as exc:  # Invalid .env values: report them in French and stop
        console.print(f"[red]Configuration invalide :[/red] {exc}")
        return 1
    for problem in settings_load_problems():  # A settings.json the page wrote (or a hand edit) was unusable
        console.print(f"[yellow]⚠ {problem}[/yellow]")
    paths = Paths.from_settings(settings)
    paths.ensure()
    masker = setup_logging(settings)

    if args.config:
        show_config(settings, paths)
        return 0
    if args.rag_index is not None or args.rag_search or args.rag_status:
        try:
            return asyncio.run(run_rag_command(settings, paths, args))
        except KeyboardInterrupt:
            return 130
    if args.tools or args.call:
        return asyncio.run(run_tools_command(settings, paths, masker, args.call))
    if args.serve or args.attach:
        return run_kernel_command(settings, paths, masker, args)

    mode = args.mode or choose_mode()
    if mode is None:
        return 0
    if settings.kernel_enabled:  # A kernel exists: the terminal is a client, not a second brain
        from monika.client import kernel_alive

        if kernel_alive(paths.kernel_socket):
            console.print("[dim]Noyau en cours : je m'y rattache.")
            return attach_console(paths, mode == "voix")
        console.print("[dim]Aucun noyau en cours : je démarre en local "
                      "(python -m monika --serve pour le noyau).[/dim]")
    try:
        return asyncio.run(run_chat(settings, paths, masker, voice=(mode == "voix")))
    except KeyboardInterrupt:
        console.print("\nÀ bientôt.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
